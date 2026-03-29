"""
Normalize Retell / LLM tool payloads before DRF validation.

Retell custom functions often send **camelCase** parameter names (matching JSON / OpenAPI
conventions) while our serializers use **snake_case**. We merge camelCase into snake_case
when the snake_case key is absent so both styles work.

Cal.com **create/reschedule booking** bodies use ``lengthInMinutes`` for duration; we store
that internally as ``duration_minutes`` and pass it through as ``lengthInMinutes`` to the API.

Also coerces ``eventTypeId`` / ``event_type_id`` string digits to int.
"""

from __future__ import annotations

from typing import Any

# Common Retell / JS-style keys -> Django / Python serializer fields
_CAMEL_TO_SNAKE: dict[str, str] = {
    "serviceKey": "service_key",
    "eventTypeId": "event_type_id",
    "timeZone": "time_zone",
    "afterStart": "after_start",
    "beforeEnd": "before_end",
    "newStart": "new_start",
    "bookingUid": "booking_uid",
    # Cal.com booking API: duration is ``lengthInMinutes`` in JSON (preferred for agents).
    "lengthInMinutes": "duration_minutes",
    "durationMinutes": "duration_minutes",
}


def normalize_retell_function_arguments(args: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy with camelCase aliases copied to snake_case keys."""
    if not isinstance(args, dict):
        return args
    out = dict(args)
    for camel, snake in _CAMEL_TO_SNAKE.items():
        if camel in out and snake not in out:
            out[snake] = out[camel]

    et = out.get("event_type_id")
    if isinstance(et, str) and et.strip().isdigit():
        out["event_type_id"] = int(et.strip())

    dm = out.get("duration_minutes")
    if isinstance(dm, str) and dm.strip().isdigit():
        out["duration_minutes"] = int(dm.strip())

    return out
