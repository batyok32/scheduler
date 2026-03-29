"""
Central **service catalog**: maps stable ``service_key`` strings to Cal.com ``event_type_id``
and human-readable metadata.

**Durations (``duration_minutes_options``)** — configured per service in ``SERVICE_CATALOG_JSON``
(a JSON array of minute lengths, e.g. ``[60]`` or ``[30, 60, 90]``). This is the server-side
list for that offering (not fetched live from Cal.com). It drives both:

- **Check availability** → resolved minutes are sent to Cal.com ``GET /slots`` as the ``duration`` query param.
- **Create booking** → resolved minutes become ``lengthInMinutes`` when Cal.com allows multiple lengths; a **single** catalog option omits the field (fixed-length types reject it) — see ``calcom_booking_requires_length_in_minutes``.
- **Reschedule** → same omission rules when ``service_key`` is set; if ``service_key`` is omitted, ``lengthInMinutes`` is never sent (time-only reschedule; Cal.com rejects the field for fixed-length types) — see ``calcom_reschedule_requires_length_in_minutes``.

**Resolution rules** (same for slots and bookings):

1. If ``duration_minutes_options`` has **one** value → always use it (ignore whatever the user sent).
2. If it has **several** values → if the user’s ``duration_minutes`` is in the list, use it; otherwise use the **first** entry in the list (and if the user omitted duration, use the first entry).
3. If the catalog entry has **no** ``duration_minutes_options`` → legacy rules: global allowed set + per-service defaults.

**Important for LLM / agent prompts:** Agents must **only** use ``service_key`` values that
come from this catalog (or from the ``GET /api/event-types/`` discovery flow your app
documents). They must **never** invent, guess, or transcribe raw numeric Cal.com
``event_type_id`` values — those IDs are internal to Cal.com and are configured here by
operators, not by callers.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from rest_framework import serializers as drf_serializers

from booking.utils.duration import ALLOWED_DURATION_MINUTES, default_duration_minutes_for_service


def get_catalog() -> dict[str, dict[str, Any]]:
    """Return the merged service catalog (read-only view)."""
    return getattr(settings, "SERVICE_CATALOG", {})


def resolve_catalog_service_key(raw: str) -> str | None:
    """
    Map a user- or LLM-supplied key to a key present in the catalog.

    Retell prompts and JSON often disagree on **hyphens vs underscores** (e.g.
    ``repair_estimate`` vs ``repair-estimate``). We accept either if it matches
    exactly, or after a single style swap.
    """
    cat = get_catalog()
    sk = (raw or "").strip()
    if not sk:
        return None
    if sk in cat:
        return sk
    alt_hyphen = sk.replace("_", "-")
    if alt_hyphen in cat:
        return alt_hyphen
    alt_under = sk.replace("-", "_")
    if alt_under in cat:
        return alt_under
    return None


def list_allowed_service_keys() -> list[str]:
    """Sorted list of valid ``service_key`` values."""
    return sorted(get_catalog().keys())


def is_allowed_service_key(service_key: str) -> bool:
    sk = (service_key or "").strip()
    return resolve_catalog_service_key(sk) is not None


def resolve_event_type_id_for_key(service_key: str) -> int | None:
    """Return Cal.com ``event_type_id`` for ``service_key``, or None if unknown."""
    cat = get_catalog()
    canon = resolve_catalog_service_key(service_key)
    if not canon:
        return None
    entry = cat[canon]
    return int(entry["event_type_id"])


def get_service_metadata(service_key: str) -> dict[str, Any]:
    """Return catalog entry for a key (empty dict if missing)."""
    cat = get_catalog()
    canon = resolve_catalog_service_key(service_key)
    if not canon:
        return {}
    return dict(cat.get(canon, {}))


def _normalize_duration_minutes_options(raw: Any) -> list[int] | None:
    """
    Parse ``duration_minutes_options`` from a catalog entry.

    Returns ``None`` if unset or invalid/empty (caller falls back to global duration rules).
    Order is preserved; duplicates are dropped (first wins).
    """
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) == 0:
        return None
    out: list[int] = []
    seen: set[int] = set()
    for x in raw:
        if isinstance(x, bool) or not isinstance(x, int):
            continue
        if x < 1:
            continue
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out if out else None


def resolve_duration_minutes(
    service_key: str | None,
    requested: int | None,
    *,
    treat_missing_request: bool = True,
) -> int | None:
    """
    Resolve minutes from the catalog’s ``duration_minutes_options`` list (see module docstring).

    - If ``treat_missing_request`` is False and ``requested`` is ``None`` (reschedule without
      changing duration), returns ``None``.
    - **One** option → always that minute value.
    - **Multiple** options → ``requested`` if it is in the list, else the **first** list entry
      (also used when ``requested`` is missing and ``treat_missing_request`` is True).
    - **No** catalog list → ``ALLOWED_DURATION_MINUTES`` + ``default_duration_minutes_for_service``.
    """
    sk = (service_key or "").strip()
    if not treat_missing_request and requested is None:
        return None

    opts: list[int] | None = None
    if sk:
        opts = _normalize_duration_minutes_options(get_service_metadata(sk).get("duration_minutes_options"))

    if opts:
        if len(opts) == 1:
            return opts[0]
        if requested is not None and requested in opts:
            return requested
        return opts[0]

    # Legacy: no per-service list
    if requested is None:
        return default_duration_minutes_for_service(sk)
    if requested not in ALLOWED_DURATION_MINUTES:
        raise drf_serializers.ValidationError(
            f"duration_minutes must be one of {sorted(ALLOWED_DURATION_MINUTES)}."
        )
    return requested


def calcom_booking_requires_length_in_minutes(service_key: str | None) -> bool:
    """
    Cal.com returns 400 if ``lengthInMinutes`` is sent for an event type that only has one
    fixed length ("does not have multiple possible lengths").

    When the catalog lists exactly one ``duration_minutes_options`` value, omit the field on
    create/reschedule and let Cal.com use the event type's default length.

    Legacy bookings (no ``service_key``) still send ``lengthInMinutes`` for compatibility.
    Optional ``omit_length_in_minutes: true`` on the catalog entry forces omission when you
    cannot model durations in the catalog but the event type is fixed-length in Cal.com.
    """
    sk = (service_key or "").strip()
    if not sk:
        return True
    meta = get_service_metadata(sk)
    if meta.get("omit_length_in_minutes") is True:
        return False
    opts = _normalize_duration_minutes_options(meta.get("duration_minutes_options"))
    if opts is not None and len(opts) == 1:
        return False
    return True


def calcom_reschedule_requires_length_in_minutes(service_key: str | None) -> bool:
    """
    Whether to include ``lengthInMinutes`` on ``POST .../bookings/{uid}/reschedule``.

    Without ``service_key``, omit the field: Cal.com rejects ``lengthInMinutes`` on reschedule
    for fixed-length event types (same as create), and time-only reschedules are typical.

    With ``service_key``, follow the same rules as ``calcom_booking_requires_length_in_minutes``
    (omit for single ``duration_minutes_options`` / ``omit_length_in_minutes``).
    """
    sk = (service_key or "").strip()
    if not sk:
        return False
    return calcom_booking_requires_length_in_minutes(sk)


def apply_service_or_legacy_event_type(
    *,
    service_key: str | None,
    event_type_id: int | None,
    prefer_service_key: bool = True,
) -> tuple[int | None, str | None, str | None]:
    """
    Resolve which Cal.com ``event_type_id`` to use.

    - If ``service_key`` is non-empty: it must exist in the catalog; returns that ID.
      If ``event_type_id`` is also provided and differs, **service_key wins** when
      ``prefer_service_key`` is True (default).
    - If only ``event_type_id`` is provided (legacy): it is returned as-is (not checked
      against the catalog, for backward compatibility).

    Returns ``(resolved_event_type_id, normalized_service_key_or_None, label_or_None)``.
    """
    sk = (service_key or "").strip() if service_key else ""
    cat = get_catalog()

    if sk:
        canon = resolve_catalog_service_key(sk)
        if not canon:
            return None, None, None
        entry = cat[canon]
        etid = int(entry["event_type_id"])
        label = str(entry.get("label") or "")
        return etid, canon, label or None

    if event_type_id is not None:
        return int(event_type_id), None, None

    return None, None, None
