"""DRF serializers for Retell function arguments."""

from __future__ import annotations

import re
from typing import Any

from django.conf import settings as django_settings
from rest_framework import serializers

from booking.service_catalog import (
    get_catalog,
    list_allowed_service_keys,
    resolve_catalog_service_key,
    resolve_duration_minutes,
)
from booking.utils.phone import format_phone_e164_us_preferred, validate_e164_for_calcom
from booking.utils.time import normalize_instant_for_calcom, parse_date_or_datetime


def _validate_iso_date_or_datetime(value: str, field_label: str) -> str:
    s = (value or "").strip()
    if not s:
        raise serializers.ValidationError(f"{field_label} cannot be empty.")
    try:
        parse_date_or_datetime(s)
    except (ValueError, TypeError) as exc:
        raise serializers.ValidationError(
            f"{field_label} must be a valid ISO 8601 date (YYYY-MM-DD) or datetime."
        ) from exc
    return s


def _normalize_phone(value: str) -> str:
    if not value or not str(value).strip():
        return ""
    digits = re.sub(r"\D", "", str(value))
    return digits


def _apply_service_catalog(
    attrs: dict[str, Any],
    *,
    require_service_or_event: bool,
) -> dict[str, Any]:
    """
    Resolve ``service_key`` (preferred) or legacy ``event_type_id``.

    LLM-facing agents should pass ``service_key`` only; ``event_type_id`` is retained for
    backward compatibility. Invalid ``service_key`` values are rejected with an explicit list
    of allowed keys.
    """
    sk = (attrs.get("service_key") or "").strip()
    etid = attrs.get("event_type_id")
    catalog = get_catalog()

    if sk:
        canon = resolve_catalog_service_key(sk)
        if canon is None:
            raise serializers.ValidationError(
                {
                    "service_key": (
                        f"Unknown service_key {sk!r}. Allowed: {list_allowed_service_keys()}. "
                        "Hyphen/underscore variants are accepted if they match one key (e.g. "
                        "repair_estimate vs repair-estimate). "
                        "Do not invent raw Cal.com event_type_id values."
                    )
                }
            )
        entry = catalog[canon]
        attrs["event_type_id"] = int(entry["event_type_id"])
        attrs["service_key"] = canon
        attrs["service_label"] = str(entry.get("label") or "")
        return attrs

    if etid is not None:
        attrs["event_type_id"] = int(etid)
        attrs["service_key"] = ""
        attrs["service_label"] = ""
        return attrs

    if require_service_or_event:
        raise serializers.ValidationError(
            "Provide service_key (preferred) or event_type_id (legacy). "
            "Agents must use configured service_key values — never guess Cal.com event_type_id."
        )
    attrs["service_key"] = ""
    attrs["service_label"] = ""
    return attrs


def _validate_optional_service_key(attrs: dict[str, Any]) -> dict[str, Any]:
    sk = (attrs.get("service_key") or "").strip()
    if not sk:
        attrs["service_key"] = ""
        attrs["service_label"] = ""
        return attrs
    catalog = get_catalog()
    canon = resolve_catalog_service_key(sk)
    if canon is None:
        raise serializers.ValidationError(
            {
                "service_key": (
                    f"Unknown service_key {sk!r}. Allowed: {list_allowed_service_keys()}."
                )
            }
        )
    entry = catalog[canon]
    attrs["service_key"] = canon
    attrs["service_label"] = str(entry.get("label") or "")
    return attrs


def _normalize_calcom_time_fields(attrs: dict[str, Any], field_names: tuple[str, ...]) -> None:
    """
    Convert naive datetimes to UTC using ``time_zone`` (IANA). Explicit ``Z``/offsets unchanged in meaning.

    Uses ``DEFAULT_TIMEZONE`` when ``time_zone`` is missing (e.g. reschedule without field).
    """
    tz = (attrs.get("time_zone") or "").strip() or getattr(
        django_settings, "DEFAULT_TIMEZONE", "America/Los_Angeles"
    )
    errors: dict[str, str] = {}
    for fname in field_names:
        raw = attrs.get(fname)
        if raw is None or not str(raw).strip():
            continue
        try:
            attrs[fname] = normalize_instant_for_calcom(str(raw), tz)
        except ValueError as exc:
            errors[fname] = str(exc)
    if errors:
        raise serializers.ValidationError(errors)


class CheckAvailabilitySerializer(serializers.Serializer):
    service_key = serializers.CharField(required=False, allow_blank=True, default="")
    event_type_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    start = serializers.CharField()
    end = serializers.CharField()
    time_zone = serializers.CharField()
    duration_minutes = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate_start(self, value: str) -> str:
        return _validate_iso_date_or_datetime(value, "start")

    def validate_end(self, value: str) -> str:
        return _validate_iso_date_or_datetime(value, "end")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = _apply_service_catalog(attrs, require_service_or_event=True)
        attrs["duration_minutes"] = resolve_duration_minutes(
            attrs.get("service_key"),
            attrs.get("duration_minutes"),
            treat_missing_request=True,
        )
        _normalize_calcom_time_fields(attrs, ("start", "end"))
        return attrs


class BookAppointmentSerializer(serializers.Serializer):
    service_key = serializers.CharField(required=False, allow_blank=True, default="")
    event_type_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    start = serializers.CharField()
    name = serializers.CharField(max_length=500)
    email = serializers.EmailField()
    address = serializers.CharField(max_length=500)
    # Retell / JSON often sends null for omitted optionals — allow_null so we don't 400.
    phone = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    time_zone = serializers.CharField()
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    duration_minutes = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    location = serializers.JSONField(required=False, allow_null=True, default=None)

    def validate_phone(self, value: str | None) -> str:
        out = format_phone_e164_us_preferred(value)
        if out:
            try:
                validate_e164_for_calcom(out)
            except ValueError as exc:
                raise serializers.ValidationError(str(exc)) from exc
        return out

    def validate_address(self, value: str) -> str:
        s = (value or "").strip()
        if not s:
            raise serializers.ValidationError("address is required (service location / job site).")
        return s

    def validate_notes(self, value: str | None) -> str:
        return value.strip() if value else ""

    def validate_start(self, value: str) -> str:
        return _validate_iso_date_or_datetime(value, "start")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = _apply_service_catalog(attrs, require_service_or_event=True)
        attrs["duration_minutes"] = resolve_duration_minutes(
            attrs.get("service_key"),
            attrs.get("duration_minutes"),
            treat_missing_request=True,
        )
        _normalize_calcom_time_fields(attrs, ("start",))
        return attrs


class FindBookingSerializer(serializers.Serializer):
    service_key = serializers.CharField(required=False, allow_blank=True, default="")
    event_type_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    name = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True, default="")
    phone = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    after_start = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    before_end = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")

    def validate_after_start(self, value: str | None) -> str:
        v = (value or "").strip()
        if not v:
            return ""
        return _validate_iso_date_or_datetime(v, "after_start")

    def validate_before_end(self, value: str | None) -> str:
        v = (value or "").strip()
        if not v:
            return ""
        return _validate_iso_date_or_datetime(v, "before_end")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = _apply_service_catalog(attrs, require_service_or_event=False)
        email = (attrs.get("email") or "").strip()
        phone = _normalize_phone(attrs.get("phone") or "")
        if not email and not phone:
            raise serializers.ValidationError(
                "Provide at least one of: email, phone (name alone is not sufficient)."
            )
        return attrs


class RescheduleBookingSerializer(serializers.Serializer):
    booking_uid = serializers.CharField()
    service_key = serializers.CharField(required=False, allow_blank=True, default="")
    new_start = serializers.CharField()
    name = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True, default="")
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    duration_minutes = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate_new_start(self, value: str) -> str:
        return _validate_iso_date_or_datetime(value, "new_start")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = _validate_optional_service_key(attrs)
        sk = (attrs.get("service_key") or "").strip()
        dm = attrs.get("duration_minutes")
        attrs["duration_minutes"] = resolve_duration_minutes(
            sk if sk else None,
            dm,
            treat_missing_request=(dm is not None),
        )
        _normalize_calcom_time_fields(attrs, ("new_start",))
        return attrs


class CancelBookingSerializer(serializers.Serializer):
    booking_uid = serializers.CharField()
    service_key = serializers.CharField(required=False, allow_blank=True, default="")
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return _validate_optional_service_key(attrs)


FUNCTION_SERIALIZERS = {
    "check_availability": CheckAvailabilitySerializer,
    "book_appointment": BookAppointmentSerializer,
    "find_booking": FindBookingSerializer,
    "reschedule_booking": RescheduleBookingSerializer,
    "cancel_booking": CancelBookingSerializer,
}
