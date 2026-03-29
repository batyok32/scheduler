"""Phone normalization for Cal.com (E.164, US +1 when country code omitted)."""

from __future__ import annotations

import re

import phonenumbers
from phonenumbers import NumberParseException


def format_phone_e164_us_preferred(value: str | None) -> str:
    """
    Normalize phone for ``attendee.phoneNumber``.

    - Empty / null → ``""`` (caller omits field).
    - Already starts with ``+`` → keep as E.164 (digits after ``+`` only).
    - Exactly 10 US digits → ``+1`` + digits.
    - 11 digits starting with ``1`` (US with leading country digit) → ``+`` + digits.
    - Any other digit run → ``+`` + digits (international without ``+``).
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if s.startswith("+"):
        rest = re.sub(r"\D", "", s[1:])
        return f"+{rest}" if rest else ""
    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+{digits}"
    return f"+{digits}"


def validate_e164_for_calcom(formatted: str) -> None:
    """
    Cal.com rejects booking creation when ``attendee.phoneNumber`` fails their validation
    (``invalid_number``). We mirror libphonenumber validity so callers get a 400 with a clear
    message instead of a 502 from Cal.com.

    Raises ``ValueError`` with a short user-facing message when invalid.
    """
    if not formatted:
        return
    try:
        n = phonenumbers.parse(formatted, None)
    except NumberParseException as exc:
        raise ValueError(
            "Phone number could not be parsed. Use a valid international number with country code, "
            "or omit phone if optional."
        ) from exc
    if not phonenumbers.is_valid_number(n):
        raise ValueError(
            "Phone number is not valid (Cal.com rejected similar numbers). "
            "Check the digits—e.g. US area codes must be real—or omit phone if the event allows it."
        )
