"""Unit tests (mock external HTTP and Retell signature verification)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from booking.models import RetellFunctionCallLog

# Isolated catalog: handyman services only (matches default settings shape).
_TEST_SERVICE_CATALOG = {
    "repair_request": {"event_type_id": 1, "label": "Repair request", "description": ""},
    "repair_estimate": {"event_type_id": 123, "label": "Repair estimate", "description": ""},
}


@override_settings(SERVICE_CATALOG=_TEST_SERVICE_CATALOG)
class ServiceCatalogUnitTests(TestCase):
    def test_resolve_event_type_id_for_key(self) -> None:
        from booking.service_catalog import resolve_event_type_id_for_key

        self.assertEqual(resolve_event_type_id_for_key("repair_request"), 1)
        self.assertEqual(resolve_event_type_id_for_key("repair_estimate"), 123)
        self.assertIsNone(resolve_event_type_id_for_key("unknown"))

    def test_apply_service_or_legacy_prefers_service_key(self) -> None:
        from booking.service_catalog import apply_service_or_legacy_event_type

        et, sk, _lab = apply_service_or_legacy_event_type(
            service_key="repair_estimate",
            event_type_id=999,
        )
        self.assertEqual(et, 123)
        self.assertEqual(sk, "repair_estimate")

    def test_apply_legacy_event_type_only(self) -> None:
        from booking.service_catalog import apply_service_or_legacy_event_type

        et, sk, lab = apply_service_or_legacy_event_type(service_key="", event_type_id=42)
        self.assertEqual(et, 42)
        self.assertIsNone(sk)
        self.assertIsNone(lab)

    @override_settings(
        SERVICE_CATALOG={"repair-estimate": {"event_type_id": 99, "label": "Repair", "description": ""}}
    )
    def test_underscore_input_matches_hyphen_catalog_key(self) -> None:
        from booking.service_catalog import resolve_catalog_service_key

        self.assertEqual(resolve_catalog_service_key("repair_estimate"), "repair-estimate")


@override_settings(SERVICE_CATALOG=_TEST_SERVICE_CATALOG)
class RetellPayloadNormalizeTests(TestCase):
    def test_camel_case_maps_to_snake_case(self) -> None:
        from booking.utils.retell_payload import normalize_retell_function_arguments

        raw = {
            "serviceKey": "repair_estimate",
            "eventTypeId": "42",
            "timeZone": "America/Los_Angeles",
            "start": "2026-03-30",
            "end": "2026-04-02",
        }
        out = normalize_retell_function_arguments(raw)
        self.assertEqual(out["service_key"], "repair_estimate")
        raw2 = {"durationMinutes": 60, "serviceKey": "repair_request"}
        out2 = normalize_retell_function_arguments(raw2)
        self.assertEqual(out2["duration_minutes"], 60)
        self.assertEqual(out2["service_key"], "repair_request")
        raw3 = {"lengthInMinutes": 90, "serviceKey": "repair_request"}
        out3 = normalize_retell_function_arguments(raw3)
        self.assertEqual(out3["duration_minutes"], 90)
        self.assertEqual(out["event_type_id"], 42)
        self.assertEqual(out["time_zone"], "America/Los_Angeles")

    def test_snake_case_unchanged(self) -> None:
        from booking.utils.retell_payload import normalize_retell_function_arguments

        raw = {"service_key": "repair_request", "start": "2026-03-30", "end": "2026-04-02", "time_zone": "UTC"}
        self.assertEqual(normalize_retell_function_arguments(raw), raw)


class DurationUtilTests(TestCase):
    def test_defaults(self) -> None:
        from booking.utils.duration import default_duration_minutes_for_service

        self.assertEqual(default_duration_minutes_for_service("repair_estimate"), 60)
        self.assertEqual(default_duration_minutes_for_service("repair_request"), 90)
        self.assertEqual(default_duration_minutes_for_service(""), 60)


class TimeNormalizeTests(TestCase):
    def test_date_only_unchanged(self) -> None:
        from booking.utils.time import normalize_instant_for_calcom

        self.assertEqual(normalize_instant_for_calcom("2026-03-30", "America/Los_Angeles"), "2026-03-30")

    def test_z_is_utc(self) -> None:
        from booking.utils.time import normalize_instant_for_calcom

        self.assertEqual(
            normalize_instant_for_calcom("2026-03-30T13:00:00Z", "America/Los_Angeles"),
            "2026-03-30T13:00:00Z",
        )

    def test_naive_is_wall_time_in_zone(self) -> None:
        from booking.utils.time import normalize_instant_for_calcom

        # 1 PM Pacific (PDT, -7) -> 20:00 UTC
        self.assertEqual(
            normalize_instant_for_calcom("2026-03-30T13:00:00", "America/Los_Angeles"),
            "2026-03-30T20:00:00Z",
        )


@override_settings(
    SERVICE_CATALOG={
        "svc_one": {
            "event_type_id": 1,
            "label": "One",
            "description": "",
            "duration_minutes_options": [45],
        },
        "svc_multi": {
            "event_type_id": 2,
            "label": "Multi",
            "description": "",
            "duration_minutes_options": [30, 60, 90],
        },
    }
)
class CatalogDurationResolveTests(TestCase):
    def test_single_option_overrides_user_choice(self) -> None:
        from booking.service_catalog import resolve_duration_minutes

        self.assertEqual(resolve_duration_minutes("svc_one", 120), 45)
        self.assertEqual(resolve_duration_minutes("svc_one", None), 45)

    def test_multi_uses_match_or_first(self) -> None:
        from booking.service_catalog import resolve_duration_minutes

        self.assertEqual(resolve_duration_minutes("svc_multi", 60), 60)
        self.assertEqual(resolve_duration_minutes("svc_multi", 25), 30)
        self.assertEqual(resolve_duration_minutes("svc_multi", None), 30)

    def test_calcom_booking_requires_length_in_minutes(self) -> None:
        from booking.service_catalog import calcom_booking_requires_length_in_minutes

        self.assertTrue(calcom_booking_requires_length_in_minutes(None))
        self.assertTrue(calcom_booking_requires_length_in_minutes("svc_multi"))
        self.assertFalse(calcom_booking_requires_length_in_minutes("svc_one"))

    def test_calcom_reschedule_requires_length_in_minutes(self) -> None:
        from booking.service_catalog import calcom_reschedule_requires_length_in_minutes

        self.assertFalse(calcom_reschedule_requires_length_in_minutes(""))
        self.assertFalse(calcom_reschedule_requires_length_in_minutes("svc_one"))
        self.assertTrue(calcom_reschedule_requires_length_in_minutes("svc_multi"))


@override_settings(
    SERVICE_CATALOG={
        "repair_estimate": {
            "event_type_id": 999,
            "label": "Estimate",
            "description": "",
            "duration_minutes_options": [60],
        },
        "repair_request": _TEST_SERVICE_CATALOG["repair_request"],
    },
    DEFAULT_TIMEZONE="America/Los_Angeles",
)
class BookAppointmentOmitLengthInMinutesTests(TestCase):
    """Cal.com rejects lengthInMinutes for fixed-length event types; catalog single option omits it."""

    def setUp(self) -> None:
        self.client = APIClient()
        self.url = reverse("retell_functions")
        self.verify = patch("booking.views.verify_retell_request", return_value=True)
        self.verify.start()
        self.addCleanup(self.verify.stop)

    @patch("booking.views.CalComClient")
    def test_create_booking_omits_length_for_single_duration_catalog(self, mock_cls: MagicMock) -> None:
        inst = mock_cls.return_value
        inst.create_booking.return_value = {
            "data": {
                "uid": "bk_fixed",
                "start": "2026-03-30T18:00:00Z",
                "end": "2026-03-30T19:00:00Z",
                "title": "Estimate",
                "status": "accepted",
            }
        }
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "book_appointment",
                    "arguments": {
                        "service_key": "repair_estimate",
                        "start": "2026-03-30T18:00:00Z",
                        "name": "Jane",
                        "email": "jane@example.com",
                        "address": "1 Site Rd",
                        "time_zone": "America/Los_Angeles",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        call = inst.create_booking.call_args[0][0]
        self.assertNotIn("lengthInMinutes", call)
        self.assertEqual(call["eventTypeId"], 999)

    @patch("booking.views.CalComClient")
    def test_reschedule_omits_length_for_single_duration_catalog(self, mock_cls: MagicMock) -> None:
        inst = mock_cls.return_value
        inst.reschedule_booking.return_value = {
            "data": {
                "uid": "bk_fixed",
                "start": "2026-03-31T18:00:00Z",
                "end": "2026-03-31T19:00:00Z",
            }
        }
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "reschedule_booking",
                    "arguments": {
                        "booking_uid": "uid1",
                        "new_start": "2026-03-31T18:00:00Z",
                        "service_key": "repair_estimate",
                        "duration_minutes": 60,
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = inst.reschedule_booking.call_args[0][1]
        self.assertNotIn("lengthInMinutes", body)


class PhoneUtilTests(TestCase):
    def test_formats_ten_digit_us(self) -> None:
        from booking.utils.phone import format_phone_e164_us_preferred

        self.assertEqual(format_phone_e164_us_preferred("2065551212"), "+12065551212")
        self.assertEqual(format_phone_e164_us_preferred("(206) 555-1212"), "+12065551212")

    def test_preserves_plus_prefix(self) -> None:
        from booking.utils.phone import format_phone_e164_us_preferred

        self.assertEqual(format_phone_e164_us_preferred("+44 20 7946 0958"), "+442079460958")

    def test_empty_and_none(self) -> None:
        from booking.utils.phone import format_phone_e164_us_preferred

        self.assertEqual(format_phone_e164_us_preferred(None), "")
        self.assertEqual(format_phone_e164_us_preferred(""), "")

    def test_validate_rejects_invalid_us_area_code(self) -> None:
        from booking.utils.phone import format_phone_e164_us_preferred, validate_e164_for_calcom

        bad = format_phone_e164_us_preferred("5567375391")
        self.assertEqual(bad, "+15567375391")
        with self.assertRaises(ValueError):
            validate_e164_for_calcom(bad)


class HealthEndpointTests(TestCase):
    def test_health_returns_ok(self) -> None:
        resp = self.client.get(reverse("health"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.decode(), "ok")
        self.assertIn("text/plain", resp["Content-Type"])


class EventTypesEndpointTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    @patch("booking.views.CalComClient")
    def test_get_event_types_returns_calcom_payload(self, mock_cls: MagicMock) -> None:
        mock_cls.return_value.get_event_types.return_value = {
            "status": "success",
            "data": [{"id": 1, "title": "Intro call"}],
        }
        url = reverse("event_types")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")
        mock_cls.return_value.get_event_types.assert_called_once_with(params=None)

    @patch("booking.views.CalComClient")
    def test_get_event_types_forwards_query_params(self, mock_cls: MagicMock) -> None:
        mock_cls.return_value.get_event_types.return_value = {"data": []}
        url = reverse("event_types")
        self.client.get(url, {"username": "alice", "orgSlug": "acme"})
        mock_cls.return_value.get_event_types.assert_called_once_with(
            params={"username": "alice", "orgSlug": "acme"}
        )

    @patch("booking.views.CalComClient")
    def test_get_event_types_calcom_error_502(self, mock_cls: MagicMock) -> None:
        from booking.exceptions import ExternalAPIError

        mock_cls.return_value.get_event_types.side_effect = ExternalAPIError("upstream", error_code="calcom_http_error")
        resp = self.client.get(reverse("event_types"))
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json().get("error_code"), "calcom_http_error")


@override_settings(SERVICE_CATALOG=_TEST_SERVICE_CATALOG)
class RetellSignatureTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    @patch("booking.views.verify_retell_request", return_value=False)
    def test_invalid_signature_rejected(self, _mock_verify: MagicMock) -> None:
        url = reverse("retell_functions")
        resp = self.client.post(
            url,
            data=json.dumps({"name": "check_availability", "arguments": {}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    @patch("booking.views.verify_retell_request", return_value=True)
    def test_valid_signature_dispatches(self, _mock_verify: MagicMock) -> None:
        url = reverse("retell_functions")
        with patch("booking.views.CalComClient") as mock_cls:
            instance = mock_cls.return_value
            instance.get_slots.return_value = {
                "status": "success",
                "data": {
                    "2026-03-30": [
                        {
                            "start": "2026-03-30T18:00:00Z",
                            "end": "2026-03-30T18:30:00Z",
                        }
                    ]
                },
            }
            resp = self.client.post(
                url,
                data=json.dumps(
                    {
                        "name": "check_availability",
                        "arguments": {
                            "service_key": "repair_estimate",
                            "start": "2026-03-30",
                            "end": "2026-04-02",
                            "time_zone": "America/Los_Angeles",
                        },
                    }
                ),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("success"))
        self.assertTrue(body.get("available"))
        self.assertEqual(body.get("duration_minutes"), 60)
        self.assertEqual(len(body.get("slots", [])), 1)
        self.assertEqual(body.get("service_key"), "repair_estimate")
        instance.get_slots.assert_called_once()
        self.assertEqual(instance.get_slots.call_args[0][0], 123)
        self.assertEqual(instance.get_slots.call_args.kwargs.get("duration_minutes"), 60)

    @patch("booking.views.verify_retell_request", return_value=True)
    def test_legacy_event_type_id_still_works(self, _mock_verify: MagicMock) -> None:
        with patch("booking.views.CalComClient") as mock_cls:
            mock_cls.return_value.get_slots.return_value = {"data": {}}
            self.client.post(
                reverse("retell_functions"),
                data=json.dumps(
                    {
                        "name": "check_availability",
                        "arguments": {
                            "event_type_id": 123,
                            "start": "2026-03-30",
                            "end": "2026-04-02",
                            "time_zone": "UTC",
                        },
                    }
                ),
                content_type="application/json",
            )
            self.assertEqual(mock_cls.return_value.get_slots.call_args[0][0], 123)
            self.assertEqual(mock_cls.return_value.get_slots.call_args.kwargs.get("duration_minutes"), 60)


@override_settings(SERVICE_CATALOG=_TEST_SERVICE_CATALOG)
class ValidationTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    @patch("booking.views.verify_retell_request", return_value=True)
    def test_unknown_function(self, _mock_verify: MagicMock) -> None:
        url = reverse("retell_functions")
        resp = self.client.post(
            url,
            data=json.dumps({"function_name": "nope", "args": {}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get("error_code"), "unknown_function")

    @patch("booking.views.verify_retell_request", return_value=True)
    def test_check_availability_validation(self, _mock_verify: MagicMock) -> None:
        url = reverse("retell_functions")
        resp = self.client.post(
            url,
            data=json.dumps({"name": "check_availability", "arguments": {"event_type_id": "x"}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    @patch("booking.views.verify_retell_request", return_value=True)
    def test_check_availability_invalid_duration_rejected(self, _mock_verify: MagicMock) -> None:
        url = reverse("retell_functions")
        resp = self.client.post(
            url,
            data=json.dumps(
                {
                    "name": "check_availability",
                    "arguments": {
                        "service_key": "repair_request",
                        "start": "2026-03-30",
                        "end": "2026-04-02",
                        "time_zone": "UTC",
                        "duration_minutes": 25,
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get("error_code"), "validation_error")

    @patch("booking.views.verify_retell_request", return_value=True)
    def test_unknown_service_key_rejected(self, _mock_verify: MagicMock) -> None:
        url = reverse("retell_functions")
        resp = self.client.post(
            url,
            data=json.dumps(
                {
                    "name": "check_availability",
                    "arguments": {
                        "service_key": "not_in_catalog",
                        "start": "2026-03-30",
                        "end": "2026-04-02",
                        "time_zone": "UTC",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get("error_code"), "validation_error")

    @patch("booking.views.verify_retell_request", return_value=True)
    def test_book_appointment_invalid_duration_rejected(self, _mock_verify: MagicMock) -> None:
        url = reverse("retell_functions")
        resp = self.client.post(
            url,
            data=json.dumps(
                {
                    "name": "book_appointment",
                    "arguments": {
                        "service_key": "repair_request",
                        "start": "2026-03-30T18:00:00Z",
                        "name": "A",
                        "email": "a@b.co",
                        "address": "123 Main St",
                        "time_zone": "UTC",
                        "duration_minutes": 45,
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get("error_code"), "validation_error")

    @patch("booking.views.verify_retell_request", return_value=True)
    def test_invalid_iso_datetime_returns_400(self, _mock_verify: MagicMock) -> None:
        url = reverse("retell_functions")
        resp = self.client.post(
            url,
            data=json.dumps(
                {
                    "name": "book_appointment",
                    "arguments": {
                        "service_key": "repair_request",
                        "start": "not-a-date",
                        "name": "A",
                        "email": "a@b.co",
                        "address": "123 Main St",
                        "time_zone": "UTC",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get("error_code"), "validation_error")

    @patch("booking.views.verify_retell_request", return_value=True)
    def test_invalid_json_body(self, _mock_verify: MagicMock) -> None:
        url = reverse("retell_functions")
        resp = self.client.post(url, data="{not json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get("error_code"), "validation_error")

    @patch("booking.views.verify_retell_request", return_value=True)
    def test_book_appointment_requires_address(self, _mock_verify: MagicMock) -> None:
        url = reverse("retell_functions")
        resp = self.client.post(
            url,
            data=json.dumps(
                {
                    "name": "book_appointment",
                    "arguments": {
                        "service_key": "repair_request",
                        "start": "2026-03-30T18:00:00Z",
                        "name": "A",
                        "email": "a@b.co",
                        "time_zone": "UTC",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get("error_code"), "validation_error")

    @patch("booking.views.verify_retell_request", return_value=True)
    def test_missing_function_name(self, _mock_verify: MagicMock) -> None:
        url = reverse("retell_functions")
        resp = self.client.post(
            url,
            data=json.dumps({"arguments": {}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)


@override_settings(
    SERVICE_CATALOG=_TEST_SERVICE_CATALOG,
    DEFAULT_TIMEZONE="America/Los_Angeles",
)
class FunctionDispatchTests(TestCase):
    """End-to-end dispatch with Cal.com mocked."""

    def setUp(self) -> None:
        self.client = APIClient()
        self.url = reverse("retell_functions")
        self.verify = patch("booking.views.verify_retell_request", return_value=True)
        self.verify.start()
        self.addCleanup(self.verify.stop)

    def _mock_client(self, mock_cls: MagicMock) -> MagicMock:
        instance = mock_cls.return_value
        return instance

    @patch("booking.views.CalComClient")
    def test_book_appointment_returns_booking_uid(self, mock_cls: MagicMock) -> None:
        inst = self._mock_client(mock_cls)
        inst.create_booking.return_value = {
            "data": {
                "uid": "bk_123",
                "start": "2026-03-30T18:00:00Z",
                "end": "2026-03-30T18:30:00Z",
                "title": "Consultation",
                "status": "accepted",
            }
        }
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "book_appointment",
                    "arguments": {
                        "service_key": "repair_request",
                        "start": "2026-03-30T18:00:00Z",
                        "name": "Jane",
                        "email": "jane@example.com",
                        "address": "456 Oak Ave, Seattle",
                        "time_zone": "America/Los_Angeles",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("success"))
        self.assertEqual(body.get("booking_uid"), "bk_123")
        self.assertEqual(body.get("status"), "confirmed")
        self.assertEqual(body.get("service_key"), "repair_request")
        self.assertEqual(body.get("duration_minutes"), 90)
        call = inst.create_booking.call_args[0][0]
        self.assertEqual(call["eventTypeId"], 1)
        self.assertEqual(call["lengthInMinutes"], 90)
        self.assertEqual(call["metadata"]["address"], "456 Oak Ave, Seattle")
        self.assertEqual(
            call["location"],
            {"type": "attendeeAddress", "address": "456 Oak Ave, Seattle"},
        )
        self.assertNotIn("bookingFieldsResponses", call)

    @patch("booking.views.CalComClient")
    def test_book_appointment_accepts_null_notes_and_phone(self, mock_cls: MagicMock) -> None:
        """Retell may send JSON null for optional fields; treat as empty."""
        inst = self._mock_client(mock_cls)
        inst.create_booking.return_value = {
            "data": {
                "uid": "bk_null",
                "start": "2026-03-30T18:00:00Z",
                "end": "2026-03-30T18:30:00Z",
                "title": "Consultation",
                "status": "accepted",
            }
        }
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "book_appointment",
                    "arguments": {
                        "service_key": "repair_request",
                        "start": "2026-03-30T18:00:00Z",
                        "name": "Jane",
                        "email": "jane@example.com",
                        "time_zone": "America/Los_Angeles",
                        "address": "1 Job Site Rd",
                        "notes": None,
                        "phone": None,
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json().get("booking_uid"), "bk_null")
        call = inst.create_booking.call_args[0][0]
        self.assertEqual(call["metadata"], {"address": "1 Job Site Rd"})
        self.assertEqual(
            call["location"],
            {"type": "attendeeAddress", "address": "1 Job Site Rd"},
        )
        self.assertNotIn("bookingFieldsResponses", call)
        self.assertNotIn("phoneNumber", call["attendee"])

    @patch("booking.views.CalComClient")
    def test_book_appointment_sends_notes_in_booking_fields_and_attendee_address_location(
        self, mock_cls: MagicMock
    ) -> None:
        """Notes map to bookingFieldsResponses; address uses Cal.com attendeeAddress location (UI visibility)."""
        inst = self._mock_client(mock_cls)
        inst.create_booking.return_value = {
            "data": {
                "uid": "bk_notes",
                "start": "2026-03-30T19:00:00.000Z",
                "end": "2026-03-30T20:00:00.000Z",
                "title": "Repair",
                "status": "accepted",
            }
        }
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "book_appointment",
                    "arguments": {
                        "service_key": "repair_request",
                        "start": "2026-03-30T19:00:00Z",
                        "name": "James Jones",
                        "email": "j@example.com",
                        "address": "14100 Linden Avenue East, Unit 225, Seattle, Washington",
                        "time_zone": "America/Los_Angeles",
                        "notes": "Oven not working",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        call = inst.create_booking.call_args[0][0]
        self.assertEqual(
            call["location"],
            {"type": "attendeeAddress", "address": "14100 Linden Avenue East, Unit 225, Seattle, Washington"},
        )
        self.assertEqual(
            call["metadata"],
            {
                "address": "14100 Linden Avenue East, Unit 225, Seattle, Washington",
                "notes": "Oven not working",
            },
        )
        self.assertEqual(
            call["bookingFieldsResponses"],
            {
                "notes": "Oven not working",
                "title": "Oven not working",
                "rescheduleReason": "Oven not working",
            },
        )

    @patch("booking.views.CalComClient")
    def test_book_appointment_sends_length_location_and_formats_phone(self, mock_cls: MagicMock) -> None:
        inst = self._mock_client(mock_cls)
        inst.create_booking.return_value = {
            "data": {
                "uid": "bk_loc",
                "start": "2026-03-30T18:00:00Z",
                "end": "2026-03-30T19:00:00Z",
                "title": "Repair estimate",
                "status": "accepted",
            }
        }
        loc = {"type": "integration", "integration": "cal-video"}
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "book_appointment",
                    "arguments": {
                        "service_key": "repair_estimate",
                        "start": "2026-03-30T18:00:00Z",
                        "name": "Jane",
                        "email": "jane@example.com",
                        "time_zone": "America/Los_Angeles",
                        "phone": "2065551212",
                        "address": "999 Pine St",
                        "duration_minutes": 60,
                        "location": loc,
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        call = inst.create_booking.call_args[0][0]
        self.assertEqual(call["lengthInMinutes"], 60)
        self.assertEqual(call["location"], loc)
        self.assertEqual(call["attendee"]["phoneNumber"], "+12065551212")
        self.assertEqual(call["metadata"]["address"], "999 Pine St")

    @patch("booking.views.CalComClient")
    def test_find_booking_uses_option_b_shape(self, mock_cls: MagicMock) -> None:
        inst = self._mock_client(mock_cls)
        inst.get_bookings.return_value = {
            "data": [
                {
                    "uid": "fb1",
                    "start": "2026-03-30T18:00:00Z",
                    "end": "2026-03-30T18:30:00Z",
                    "title": "Call",
                    "status": "accepted",
                    "attendees": [{"email": "find@example.com", "name": "F"}],
                }
            ]
        }
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "function_name": "find_booking",
                    "args": {
                        "email": "find@example.com",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("success"))
        self.assertTrue(body.get("found"))
        self.assertEqual(len(body.get("matches", [])), 1)
        self.assertEqual(body["matches"][0]["booking_uid"], "fb1")

    @patch("booking.views.CalComClient")
    def test_find_booking_accepts_json_null_optionals(self, mock_cls: MagicMock) -> None:
        """Retell often sends JSON null for omitted fields; treat like empty."""
        inst = self._mock_client(mock_cls)
        inst.get_bookings.return_value = {"data": []}
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "find_booking",
                    "arguments": {
                        "phone": "2065551212",
                        "email": None,
                        "after_start": None,
                        "before_end": None,
                        "name": None,
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

    @patch("booking.views.CalComClient")
    def test_reschedule_booking(self, mock_cls: MagicMock) -> None:
        inst = self._mock_client(mock_cls)
        inst.reschedule_booking.return_value = {
            "data": {
                "uid": "new_uid",
                "start": "2026-04-01T19:00:00Z",
                "end": "2026-04-01T19:30:00Z",
            }
        }
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "reschedule_booking",
                    "arguments": {
                        "booking_uid": "old_uid",
                        "new_start": "2026-04-01T19:00:00Z",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body.get("booking_uid"), "new_uid")
        self.assertEqual(body.get("status"), "rescheduled")

    @patch("booking.views.CalComClient")
    def test_reschedule_booking_accepts_null_email_and_name(self, mock_cls: MagicMock) -> None:
        """Retell often sends explicit JSON null for optional fields."""
        inst = self._mock_client(mock_cls)
        inst.reschedule_booking.return_value = {
            "data": {
                "uid": "new_uid",
                "start": "2026-04-01T19:00:00Z",
                "end": "2026-04-01T19:30:00Z",
            }
        }
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "reschedule_booking",
                    "arguments": {
                        "booking_uid": "old_uid",
                        "new_start": "2026-04-01T19:00:00Z",
                        "email": None,
                        "name": None,
                        "reason": "Customer asked",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        payload = inst.reschedule_booking.call_args[0][1]
        self.assertNotIn("rescheduledBy", payload)

    @patch("booking.views.CalComClient")
    def test_reschedule_returns_400_when_calcom_rejects_slot(self, mock_cls: MagicMock) -> None:
        """Cal.com 4xx (e.g. slot unavailable) maps to HTTP 400, not 502 Bad Gateway."""
        from booking.exceptions import ExternalAPIError

        inst = self._mock_client(mock_cls)
        inst.reschedule_booking.side_effect = ExternalAPIError(
            "Cal.com API error (400): User either already has booking at this time or is not available",
            error_code="calcom_http_error",
            status_code=400,
            details={"calcom_status_code": 400},
        )
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "reschedule_booking",
                    "arguments": {
                        "booking_uid": "uid",
                        "new_start": "2026-03-30T22:00:00Z",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body.get("error_code"), "calcom_http_error")
        self.assertEqual(body.get("details", {}).get("calcom_status_code"), 400)

    @patch("booking.views.CalComClient")
    def test_service_key_overrides_legacy_event_type_id(self, mock_cls: MagicMock) -> None:
        """When both are sent, catalog resolution for service_key wins."""
        inst = self._mock_client(mock_cls)
        inst.get_slots.return_value = {"data": {}}
        self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "check_availability",
                    "arguments": {
                        "service_key": "repair_estimate",
                        "event_type_id": 999,
                        "start": "2026-03-30",
                        "end": "2026-04-02",
                        "time_zone": "UTC",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(inst.get_slots.call_args[0][0], 123)
        self.assertEqual(inst.get_slots.call_args.kwargs.get("duration_minutes"), 60)

    @patch("booking.views.CalComClient")
    def test_check_availability_passes_explicit_duration_to_slots(self, mock_cls: MagicMock) -> None:
        inst = self._mock_client(mock_cls)
        inst.get_slots.return_value = {"data": {}}
        self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "check_availability",
                    "arguments": {
                        "service_key": "repair_request",
                        "start": "2026-03-30",
                        "end": "2026-04-02",
                        "time_zone": "UTC",
                        "duration_minutes": 120,
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(inst.get_slots.call_args.kwargs.get("duration_minutes"), 120)

    @patch("booking.views.CalComClient")
    def test_reschedule_booking_optional_duration(self, mock_cls: MagicMock) -> None:
        inst = self._mock_client(mock_cls)
        inst.reschedule_booking.return_value = {
            "data": {
                "uid": "new_uid",
                "start": "2026-04-01T19:00:00Z",
                "end": "2026-04-01T20:00:00Z",
            }
        }
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "reschedule_booking",
                    "arguments": {
                        "booking_uid": "old_uid",
                        "new_start": "2026-04-01T19:00:00Z",
                        "duration_minutes": 180,
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("duration_minutes"), 180)
        body = inst.reschedule_booking.call_args[0][1]
        # No service_key: omit lengthInMinutes (Cal.com fixed-length event types reject it on reschedule).
        self.assertNotIn("lengthInMinutes", body)

    @patch("booking.views.CalComClient")
    def test_reschedule_booking_sends_length_when_service_key_allows(self, mock_cls: MagicMock) -> None:
        inst = mock_cls.return_value
        inst.reschedule_booking.return_value = {
            "data": {
                "uid": "new_uid",
                "start": "2026-04-01T19:00:00Z",
                "end": "2026-04-01T20:00:00Z",
            }
        }
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "reschedule_booking",
                    "arguments": {
                        "booking_uid": "old_uid",
                        "new_start": "2026-04-01T19:00:00Z",
                        "duration_minutes": 180,
                        "service_key": "repair_request",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        body = inst.reschedule_booking.call_args[0][1]
        self.assertEqual(body["lengthInMinutes"], 180)

    @patch("booking.views.CalComClient")
    def test_cancel_booking(self, mock_cls: MagicMock) -> None:
        inst = self._mock_client(mock_cls)
        inst.cancel_booking.return_value = {"status": "success", "data": {}}
        resp = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "name": "cancel_booking",
                    "arguments": {"booking_uid": "to_cancel", "reason": "User asked"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("success"))
        self.assertEqual(body.get("status"), "cancelled")
        inst.cancel_booking.assert_called_once()


@override_settings(SERVICE_CATALOG=_TEST_SERVICE_CATALOG)
class AuditLogTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    @patch("booking.views.verify_retell_request", return_value=True)
    @patch("booking.views.CalComClient")
    def test_success_persists_retell_function_call_log(self, mock_cls: MagicMock, _v: MagicMock) -> None:
        mock_cls.return_value.get_slots.return_value = {"data": {}}
        before = RetellFunctionCallLog.objects.count()
        self.client.post(
            reverse("retell_functions"),
            data=json.dumps(
                {
                    "name": "check_availability",
                    "arguments": {
                        "service_key": "repair_request",
                        "start": "2026-03-30",
                        "end": "2026-04-02",
                        "time_zone": "UTC",
                    },
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(RetellFunctionCallLog.objects.count(), before + 1)
        log = RetellFunctionCallLog.objects.order_by("-id").first()
        assert log is not None
        self.assertEqual(log.function_name, "check_availability")
        self.assertTrue(log.success)
        self.assertEqual(log.payload_json.get("name"), "check_availability")


class CorrelationIdTests(TestCase):
    def test_response_includes_x_request_id(self) -> None:
        resp = self.client.get(reverse("health"))
        self.assertIn("X-Request-ID", resp)
        self.assertEqual(len(resp["X-Request-ID"]), 36)  # UUID


class BookingMatcherTests(TestCase):
    def test_prefers_email_and_phone_when_both_required(self) -> None:
        from booking.services.booking_matcher import pick_top_matches

        bookings = [
            {
                "uid": "a",
                "start": "2026-03-30T18:00:00Z",
                "end": "2026-03-30T18:30:00Z",
                "title": "T",
                "status": "accepted",
                "attendees": [{"email": "j@example.com", "phoneNumber": "+12065551212", "name": "John"}],
            },
            {
                "uid": "b",
                "start": "2026-03-31T18:00:00Z",
                "end": "2026-03-31T18:30:00Z",
                "title": "T2",
                "status": "accepted",
                "attendees": [{"email": "other@example.com", "name": "John"}],
            },
        ]
        matches = pick_top_matches(
            bookings,
            email="j@example.com",
            phone="+1 (206) 555-1212",
            name="John",
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["uid"], "a")

    def test_us_phone_without_country_code_matches_e164_attendee(self) -> None:
        """Request like 206-550-1543 must match Cal.com +12065501543 (same NANP number)."""
        from booking.services.booking_matcher import pick_top_matches

        bookings = [
            {
                "uid": "ben",
                "start": "2026-04-06T16:00:00.000Z",
                "end": "2026-04-06T17:00:00.000Z",
                "status": "accepted",
                "attendees": [
                    {
                        "name": "Ben Jones",
                        "email": "cookgoc@gmail.com",
                        "phoneNumber": "+12065501543",
                    }
                ],
            },
        ]
        matches = pick_top_matches(
            bookings,
            email=None,
            phone="206-550-1543",
            name="Ben Jones",
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["uid"], "ben")

    def test_rejects_name_only_query(self) -> None:
        from booking.services.booking_matcher import pick_top_matches

        bookings = [
            {
                "uid": "a",
                "attendees": [{"email": "j@example.com", "name": "John"}],
            }
        ]
        matches = pick_top_matches(bookings, email=None, phone=None, name="John")
        self.assertEqual(matches, [])


class CalComParsingTests(TestCase):
    def test_extract_bookings_list_accepts_array_envelope(self) -> None:
        from booking.services.calcom import extract_bookings_list

        rows = extract_bookings_list({"status": "success", "data": [{"uid": "a"}]})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uid"], "a")

    def test_extract_bookings_list_accepts_nested_bookings(self) -> None:
        from booking.services.calcom import extract_bookings_list

        rows = extract_bookings_list({"data": {"bookings": [{"uid": "b"}]}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uid"], "b")


@override_settings(SERVICE_CATALOG=_TEST_SERVICE_CATALOG)
class CalComErrorTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    @patch("booking.views.verify_retell_request", return_value=True)
    def test_calcom_error_propagates(self, _mock_verify: MagicMock) -> None:
        from booking.exceptions import ExternalAPIError

        url = reverse("retell_functions")
        with patch("booking.views.CalComClient") as mock_cls:
            instance = mock_cls.return_value
            instance.get_slots.side_effect = ExternalAPIError("fail", error_code="calcom_http_error")
            resp = self.client.post(
                url,
                data=json.dumps(
                    {
                        "name": "check_availability",
                        "arguments": {
                            "service_key": "repair_request",
                            "start": "2026-03-30",
                            "end": "2026-04-02",
                            "time_zone": "UTC",
                        },
                    }
                ),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 502)
