"""
Business logic for each Retell function (delegates to CalComClient).

External agents should supply ``service_key`` (from the configured catalog); raw Cal.com
``event_type_id`` is resolved server-side and should not be invented by the LLM — see
``booking/service_catalog.py``.
"""

from __future__ import annotations

from typing import Any

from booking.exceptions import ExternalAPIError
from booking.service_catalog import (
    calcom_booking_requires_length_in_minutes,
    calcom_reschedule_requires_length_in_minutes,
)
from booking.services.booking_matcher import pick_top_matches
from booking.services.calcom import (
    CalComClient,
    extract_bookings_list,
    extract_data_field,
    flatten_slots_response,
)
from booking.utils.time import normalize_booking_status, parse_date_or_datetime, to_utc_iso

# Cal.com booking-question slugs that may carry the same free-form notes (depends on event-type setup).
_BOOKING_NOTES_FIELD_SLUGS = ("notes", "title", "rescheduleReason")


def _booking_fields_responses_for_notes(notes: str) -> dict[str, str]:
    """Same text under each slug so whichever field exists on the event type is populated."""
    clipped = notes[:500]
    return {slug: clipped for slug in _BOOKING_NOTES_FIELD_SLUGS}


def handle_check_availability(client: CalComClient, args: dict[str, Any]) -> dict[str, Any]:
    etid = args["event_type_id"]
    start = args["start"].strip()
    end = args["end"].strip()
    tz = args["time_zone"].strip()
    # Cal.com accepts date-only or ISO; normalize start/end to UTC-ish strings as passed
    start_s = _format_cal_date_query(start)
    end_s = _format_cal_date_query(end)
    # ``duration_minutes`` already resolved from ``duration_minutes_options`` in serializers.
    duration = int(args["duration_minutes"])
    raw = client.get_slots(etid, start_s, end_s, tz, duration_minutes=duration)
    slots = flatten_slots_response(raw)[:5]
    available = len(slots) > 0
    out: dict[str, Any] = {
        "success": True,
        "available": available,
        "slots": slots,
        "duration_minutes": duration,
    }
    sk = (args.get("service_key") or "").strip()
    if sk:
        out["service_key"] = sk
        lab = (args.get("service_label") or "").strip()
        if lab:
            out["service_label"] = lab
    return out


def _format_cal_date_query(value: str) -> str:
    """Keep date-only as YYYY-MM-DD; otherwise normalize to ISO."""
    v = value.strip()
    if len(v) == 10 and v[4] == "-" and v[7] == "-":
        return v
    return to_utc_iso(parse_date_or_datetime(v))


def handle_book_appointment(client: CalComClient, args: dict[str, Any]) -> dict[str, Any]:
    attendee: dict[str, Any] = {
        "name": args["name"],
        "timeZone": args["time_zone"],
        "email": args["email"],
    }
    phone = (args.get("phone") or "").strip()
    if phone:
        attendee["phoneNumber"] = phone
    # ``duration_minutes`` already resolved from catalog ``duration_minutes_options`` in serializers.
    # ``lengthInMinutes`` is sent only when Cal.com allows multiple lengths (see ``calcom_booking_requires_length_in_minutes``).
    duration = int(args["duration_minutes"])
    sk = (args.get("service_key") or "").strip()
    addr = (args.get("address") or "").strip()
    notes = (args.get("notes") or "").strip()
    payload: dict[str, Any] = {
        "start": to_utc_iso(parse_date_or_datetime(args["start"])),
        "eventTypeId": args["event_type_id"],
        "attendee": attendee,
    }
    if calcom_booking_requires_length_in_minutes(sk if sk else None):
        payload["lengthInMinutes"] = duration
    loc = args.get("location")
    if loc is not None and loc != {}:
        payload["location"] = loc
    elif addr:
        # Cal.com UI shows job-site address when sent as attendee-address location, not only as metadata.
        payload["location"] = {"type": "attendeeAddress", "address": addr[:500]}
    metadata: dict[str, str] = {}
    if addr:
        metadata["address"] = addr[:500]
    if notes:
        metadata["notes"] = notes[:500]
    if metadata:
        payload["metadata"] = metadata
    if notes:
        payload["bookingFieldsResponses"] = _booking_fields_responses_for_notes(notes)
    raw = client.create_booking(payload)
    data = extract_data_field(raw)
    if not isinstance(data, dict):
        raise ExternalAPIError("Unexpected Cal.com create booking response", error_code="calcom_parse_error")
    uid = data.get("uid") or data.get("bookingUid")
    if not uid:
        raise ExternalAPIError(
            "Cal.com create booking response missing booking uid",
            error_code="calcom_parse_error",
        )
    out: dict[str, Any] = {
        "success": True,
        "booking_uid": uid,
        "start": data.get("start"),
        "end": data.get("end"),
        "title": data.get("title"),
        "status": normalize_booking_status(data.get("status")),
        "duration_minutes": duration,
    }
    if sk:
        out["service_key"] = sk
        lab = (args.get("service_label") or "").strip()
        if lab:
            out["service_label"] = lab
    return out


def handle_find_booking(client: CalComClient, args: dict[str, Any]) -> dict[str, Any]:
    candidates = _collect_bookings(client, args)
    matches_raw = pick_top_matches(
        candidates,
        email=(args.get("email") or "").strip() or None,
        phone=(args.get("phone") or "").strip() or None,
        name=(args.get("name") or "").strip() or None,
        limit=3,
    )
    matches = [_booking_match_dict(b) for b in matches_raw]
    out: dict[str, Any] = {
        "success": True,
        "found": len(matches) > 0,
        "matches": matches,
    }
    sk = (args.get("service_key") or "").strip()
    if sk:
        out["service_key"] = sk
        lab = (args.get("service_label") or "").strip()
        if lab:
            out["service_label"] = lab
    return out


def _collect_bookings(client: CalComClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch booking rows from Cal.com with pagination (bounded)."""
    params: dict[str, Any] = {"take": 100, "sortStart": "desc"}
    eid = args.get("event_type_id")
    if eid is not None:
        params["eventTypeId"] = str(int(eid))
    after = (args.get("after_start") or "").strip()
    before = (args.get("before_end") or "").strip()
    if after:
        params["afterStart"] = after
    if before:
        params["beforeEnd"] = before
    email = (args.get("email") or "").strip()
    if email:
        params["attendeeEmail"] = email

    out: list[dict[str, Any]] = []
    max_pages = 10
    for page in range(max_pages):
        params["skip"] = page * 100
        raw = client.get_bookings(params)
        page_rows = extract_bookings_list(raw)
        if page_rows is None:
            break
        out.extend(page_rows)
        if len(page_rows) < 100:
            break
    return out


def _booking_match_dict(b: dict[str, Any]) -> dict[str, Any]:
    return {
        "booking_uid": b.get("uid"),
        "start": b.get("start"),
        "end": b.get("end"),
        "title": b.get("title"),
        "status": normalize_booking_status(b.get("status")),
    }


def handle_reschedule_booking(client: CalComClient, args: dict[str, Any]) -> dict[str, Any]:
    uid = args["booking_uid"].strip()
    payload: dict[str, Any] = {
        "start": to_utc_iso(parse_date_or_datetime(args["new_start"])),
    }
    # Reschedule: omit ``lengthInMinutes`` unless catalog says the event type allows it (and
    # never send it when ``service_key`` is missing — fixed-length types reject it).
    rd = args.get("duration_minutes")
    sk = (args.get("service_key") or "").strip()
    if rd is not None and calcom_reschedule_requires_length_in_minutes(sk):
        payload["lengthInMinutes"] = int(rd)
    reason = (args.get("reason") or "").strip()
    if reason:
        payload["reschedulingReason"] = reason
    email = (args.get("email") or "").strip()
    if email:
        payload["rescheduledBy"] = email
    raw = client.reschedule_booking(uid, payload)
    data = extract_data_field(raw)
    if not isinstance(data, dict):
        raise ExternalAPIError("Unexpected Cal.com reschedule response", error_code="calcom_parse_error")
    out_uid = data.get("uid") or data.get("rescheduledToUid") or data.get("bookingUid") or uid
    out: dict[str, Any] = {
        "success": True,
        "booking_uid": out_uid,
        "new_start": data.get("start"),
        "new_end": data.get("end"),
        "status": "rescheduled",
    }
    if rd is not None:
        out["duration_minutes"] = int(rd)
    if sk:
        out["service_key"] = sk
        lab = (args.get("service_label") or "").strip()
        if lab:
            out["service_label"] = lab
    return out


def handle_cancel_booking(client: CalComClient, args: dict[str, Any]) -> dict[str, Any]:
    uid = args["booking_uid"].strip()
    payload: dict[str, Any] = {}
    reason = (args.get("reason") or "").strip()
    if reason:
        payload["cancellationReason"] = reason
    client.cancel_booking(uid, payload)
    out: dict[str, Any] = {"success": True, "booking_uid": uid, "status": "cancelled"}
    sk = (args.get("service_key") or "").strip()
    if sk:
        out["service_key"] = sk
        lab = (args.get("service_label") or "").strip()
        if lab:
            out["service_label"] = lab
    return out


FUNCTION_HANDLERS = {
    "check_availability": handle_check_availability,
    "book_appointment": handle_book_appointment,
    "find_booking": handle_find_booking,
    "reschedule_booking": handle_reschedule_booking,
    "cancel_booking": handle_cancel_booking,
}
