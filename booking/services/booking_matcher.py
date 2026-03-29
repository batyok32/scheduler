"""
Conservative booking matching for ``find_booking``.

**Why name-only matching is unsafe:** many attendees share common names, and typos
create false positives. Strong identifiers (email, E.164 phone) drastically reduce
collisions. Name is only used as a weak tie-breaker when email/phone already match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class ScoredBooking:
    """Do not use ``order=True``: ``booking`` dicts are not orderable in Python 3."""

    score: int
    booking: dict[str, Any]


def normalize_phone(phone: str | None) -> str:
    """
    Digits only, with US/Canada NANP normalized to **10 digits** (drop leading country ``1``).

    So ``2065551212``, ``+12065551212``, and ``1-206-555-1212`` all compare equal.
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits[0] == "1":
        return digits[1:]
    return digits


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def names_loosely_match(name_a: str | None, name_b: str | None) -> bool:
    if not name_a or not name_b:
        return False
    a = " ".join(name_a.lower().split())
    b = " ".join(name_b.lower().split())
    return a == b


def extract_attendee(booking: dict[str, Any]) -> dict[str, Any]:
    attendees = booking.get("attendees")
    if isinstance(attendees, list) and attendees:
        first = attendees[0]
        if isinstance(first, dict):
            return first
    return {}


def score_candidate(
    booking: dict[str, Any],
    *,
    email: str | None,
    phone: str | None,
    name: str | None,
) -> int | None:
    """
    Return a score for ordering, or None if this booking should be discarded.

    When **both** email and phone are supplied, **both** must match (reduces false positives).

    Scoring (higher is better):
    - Exact email match: +100
    - Exact phone match (digits): +80
    - Loose name match: +5 (tie-breaker only)

    If neither email nor phone is in the query, we refuse to match — name alone is unsafe.
    """
    want_email = normalize_email(email)
    want_phone = normalize_phone(phone)
    want_name = (name or "").strip()

    att = extract_attendee(booking)
    cand_email = normalize_email(att.get("email"))
    cand_phone = normalize_phone(att.get("phoneNumber") or att.get("phone"))
    cand_name = att.get("name") or booking.get("title")

    if not want_email and not want_phone:
        return None

    em_ok = bool(want_email and cand_email and want_email == cand_email)
    ph_ok = bool(want_phone and cand_phone and want_phone == cand_phone)

    if want_email and want_phone:
        if not (em_ok and ph_ok):
            return None
        score = 180
    elif want_email:
        if not em_ok:
            return None
        score = 100
    else:
        # phone only
        if not ph_ok:
            return None
        score = 80

    if want_name and names_loosely_match(want_name, str(cand_name or "")):
        score += 5

    return score


def pick_top_matches(
    bookings: list[dict[str, Any]],
    *,
    email: str | None,
    phone: str | None,
    name: str | None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    scored: list[ScoredBooking] = []
    for b in bookings:
        if not isinstance(b, dict):
            continue
        s = score_candidate(b, email=email, phone=phone, name=name)
        if s is None:
            continue
        scored.append(ScoredBooking(score=s, booking=b))
    scored.sort(key=lambda x: (-x.score, str(x.booking.get("start", ""))))
    return [x.booking for x in scored[:limit]]
