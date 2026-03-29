"""Datetime parsing and ISO8601 helpers (timezone-safe)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Union
from zoneinfo import ZoneInfo

from django.utils import timezone as django_tz

DateLike = Union[str, date, datetime]

# IANA does not define ``America/Pacific``; callers often use it. Map to a real zone.
_TZ_ALIASES = {
    "america/pacific": "America/Los_Angeles",
}


def _canonical_iana_timezone(tz_name: str) -> str:
    key = tz_name.strip().lower()
    return _TZ_ALIASES.get(key, tz_name.strip())


def parse_iso_datetime(value: str | datetime) -> datetime:
    """Parse an ISO 8601 datetime string; pass-through if already datetime."""
    if isinstance(value, datetime):
        dt = value
    else:
        s = value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    if django_tz.is_naive(dt):
        dt = django_tz.make_aware(dt, timezone.utc)
    return dt


def parse_date_or_datetime(value: str) -> datetime:
    """Parse date-only (YYYY-MM-DD) as UTC start-of-day, or full ISO datetime."""
    value = value.strip()
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        d = date.fromisoformat(value)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return parse_iso_datetime(value)


def to_utc_iso(dt: datetime) -> str:
    """Return UTC Zulu ISO string."""
    if django_tz.is_naive(dt):
        dt = django_tz.make_aware(dt, timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def normalize_instant_for_calcom(value: str, tz_name: str) -> str:
    """
    Normalize a single instant for Cal.com (slots query bounds, booking ``start``, reschedule).

    - **Date-only** ``YYYY-MM-DD`` — returned unchanged (caller supplies ``timeZone`` separately).
    - **Datetime with** ``Z`` **or an offset** — treated as that absolute instant; output is UTC ``Z``.
    - **Naive datetime** (no ``Z``, no offset) — treated as **wall-clock time in** ``tz_name``,
      then converted to UTC. This matches callers who mean "1:00 PM Pacific" but omit the offset.

    ``tz_name`` must be a valid IANA zone (e.g. ``America/Los_Angeles``).
    """
    value = value.strip()
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        return value
    canon = _canonical_iana_timezone(tz_name)
    try:
        zi = ZoneInfo(canon)
    except Exception as exc:
        raise ValueError(f"Invalid time_zone {tz_name!r}") from exc
    s = value
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        return to_utc_iso(dt)
    return to_utc_iso(dt.replace(tzinfo=zi))


def normalize_booking_status(cal_status: str | None) -> str:
    """Map Cal.com booking status to Retell-facing labels."""
    if not cal_status:
        return "unknown"
    s = cal_status.lower()
    if s in ("accepted",):
        return "confirmed"
    if s in ("cancelled",):
        return "cancelled"
    if s in ("pending",):
        return "pending"
    if s in ("rejected",):
        return "rejected"
    return cal_status
