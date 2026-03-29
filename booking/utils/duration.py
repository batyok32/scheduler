"""Duration rules for handyman scheduling (Cal.com slots + bookings)."""

from __future__ import annotations

from rest_framework import serializers

ALLOWED_DURATION_MINUTES = frozenset({30, 60, 90, 120, 180, 240, 480})


def validate_duration_choice(value: int | None) -> int | None:
    """Reject unknown durations; ``None`` means caller will apply a default later."""
    if value is None:
        return None
    if value not in ALLOWED_DURATION_MINUTES:
        raise serializers.ValidationError(
            f"duration_minutes must be one of {sorted(ALLOWED_DURATION_MINUTES)}."
        )
    return value


def default_duration_minutes_for_service(service_key: str) -> int:
    """
    When the client omits ``duration_minutes``:

    - ``repair_estimate`` → 60
    - ``repair_request`` → 90 (within the 60–120 minute band)
    - legacy / empty ``service_key`` (e.g. ``event_type_id`` only) → 60
    """
    sk = (service_key or "").strip()
    if sk == "repair_estimate":
        return 60
    if sk == "repair_request":
        return 90
    return 60
