"""
Cal.com HTTP client (API v2).

Endpoint assumptions follow public Cal.com docs (March 2026):
- Slots: GET /slots with ``cal-api-version: 2024-09-04``
- Event types: GET /event-types with ``cal-api-version:`` ``CALCOM_EVENT_TYPES_API_VERSION`` (default ``2024-06-14``)
- Bookings: ``cal-api-version: 2026-02-25`` for create, list, reschedule, cancel

If Cal.com returns a different shape, adjust parsing helpers only—keep call sites stable.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

import httpx
from django.conf import settings

from booking.exceptions import ExternalAPIError

logger = logging.getLogger(__name__)


def _client_http_status_for_calcom(resp_status: int) -> int:
    """Map Cal.com HTTP status to our Retell API response (4xx → same, 5xx → 502)."""
    if resp_status >= 500:
        return 502
    if 400 <= resp_status < 500:
        return resp_status
    return 502


def _verbose_integration_logs() -> bool:
    """Full request/response logging for Cal.com HTTP client."""
    return getattr(settings, "CALCOM_VERBOSE_LOGS", False)


class CalComClient:
    """Thin wrapper around Cal.com REST API with retries and defensive JSON parsing."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        bookings_api_version: str | None = None,
        slots_api_version: str | None = None,
        event_types_api_version: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.base_url = (base_url or settings.CALCOM_BASE_URL).rstrip("/") + "/"
        self.api_key = api_key if api_key is not None else settings.CALCOM_API_KEY
        self.bookings_api_version = bookings_api_version or getattr(
            settings, "CALCOM_BOOKINGS_API_VERSION", settings.CALCOM_API_VERSION
        )
        self.slots_api_version = slots_api_version or settings.CALCOM_SLOTS_API_VERSION
        self.event_types_api_version = event_types_api_version or settings.CALCOM_EVENT_TYPES_API_VERSION
        self.timeout = timeout if timeout is not None else settings.CALCOM_REQUEST_TIMEOUT
        self.max_retries = max_retries if max_retries is not None else settings.CALCOM_MAX_RETRIES

    def _request(
        self,
        method: str,
        path: str,
        *,
        version: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        # Avoid urllib.parse.urljoin: a path starting with "/" replaces the host path (e.g. "/v2").
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "cal-api-version": version,
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    ctx_req: dict[str, Any] = {
                        "method": method,
                        "path": path,
                        "attempt": attempt + 1,
                    }
                    if _verbose_integration_logs():
                        ctx_req["url"] = url
                        if params is not None:
                            ctx_req["query_params"] = params
                        if json_body is not None:
                            ctx_req["json_body"] = json_body
                    else:
                        if params and path.rstrip("/").endswith("slots") and "duration" in params:
                            ctx_req["duration"] = params["duration"]
                    logger.info(
                        "calcom_request",
                        extra={"ctx": ctx_req},
                    )
                    if _verbose_integration_logs():
                        try:
                            req_parts: dict[str, Any] = {}
                            if params is not None:
                                req_parts["query_params"] = params
                            if json_body is not None:
                                req_parts["json_body"] = json_body
                            pretty_req = json.dumps(req_parts, indent=2, ensure_ascii=False, default=str)
                        except TypeError:
                            pretty_req = repr({"query_params": params, "json_body": json_body})
                        print(
                            f"[Cal.com] REQUEST {method} {path}\n{pretty_req}",
                            file=sys.stderr,
                            flush=True,
                        )
                    resp = client.request(
                        method,
                        url,
                        headers=headers,
                        json=json_body,
                        params=params,
                    )
                if resp.status_code >= 500 and attempt < self.max_retries:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                parsed = self._parse_response(resp)
                if _verbose_integration_logs():
                    try:
                        pretty = json.dumps(parsed, indent=2, ensure_ascii=False, default=str)
                    except TypeError:
                        pretty = repr(parsed)
                    logger.info(
                        "calcom_http_response",
                        extra={
                            "ctx": {
                                "method": method,
                                "path": path,
                                "status_code": resp.status_code,
                                "response": parsed,
                                "response_json": pretty,
                            }
                        },
                    )
                    print(
                        f"[Cal.com] {method} {path} HTTP {resp.status_code}\n{pretty}",
                        file=sys.stderr,
                        flush=True,
                    )
                return parsed
            except httpx.TimeoutException as e:
                last_exc = e
                if attempt < self.max_retries:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                raise ExternalAPIError("Cal.com request timed out", error_code="calcom_timeout") from e
            except httpx.RequestError as e:
                last_exc = e
                if attempt < self.max_retries:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                raise ExternalAPIError("Cal.com network error", error_code="calcom_network") from e
        assert last_exc is not None
        raise ExternalAPIError("Cal.com request failed", error_code="calcom_error") from last_exc

    def _parse_response(self, resp: httpx.Response) -> Any:
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}
        if 200 <= resp.status_code < 300:
            return data
        msg = _extract_error_message(data)
        ctx: dict[str, Any] = {
            "status_code": resp.status_code,
            "message": msg,
        }
        if _verbose_integration_logs():
            ctx["response_body"] = data
        logger.warning(
            "calcom_error_response",
            extra={"ctx": ctx},
        )
        if _verbose_integration_logs():
            try:
                err_pretty = json.dumps(data, indent=2, ensure_ascii=False, default=str)
            except TypeError:
                err_pretty = repr(data)
            print(
                f"[Cal.com] ERROR HTTP {resp.status_code} {msg}\n{err_pretty}",
                file=sys.stderr,
                flush=True,
            )
        raise ExternalAPIError(
            f"Cal.com API error ({resp.status_code}): {msg}",
            error_code="calcom_http_error",
            status_code=_client_http_status_for_calcom(resp.status_code),
            details={"calcom_status_code": resp.status_code},
        )

    def get_slots(
        self,
        event_type_id: int,
        start: str,
        end: str,
        time_zone: str,
        *,
        duration_minutes: int | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "eventTypeId": event_type_id,
            "start": start,
            "end": end,
            "timeZone": time_zone,
            "format": "range",
        }
        # GET /slots uses query param ``duration`` (minutes). POST /bookings uses body ``lengthInMinutes``.
        if duration_minutes is not None:
            params["duration"] = duration_minutes
        return self._request("GET", "slots", version=self.slots_api_version, params=params)

    def create_booking(self, payload: dict[str, Any]) -> Any:
        return self._request(
            "POST",
            "bookings",
            version=self.bookings_api_version,
            json_body=payload,
        )

    def get_bookings(self, params: dict[str, Any]) -> Any:
        return self._request(
            "GET",
            "bookings",
            version=self.bookings_api_version,
            params=params,
        )

    def get_event_types(self, params: dict[str, Any] | None = None) -> Any:
        """GET /v2/event-types — list event types (see Cal.com API docs for query params)."""
        return self._request(
            "GET",
            "event-types",
            version=self.event_types_api_version,
            params=params,
        )

    def reschedule_booking(self, booking_uid: str, payload: dict[str, Any]) -> Any:
        return self._request(
            "POST",
            f"bookings/{booking_uid}/reschedule",
            version=self.bookings_api_version,
            json_body=payload,
        )

    def cancel_booking(self, booking_uid: str, payload: dict[str, Any]) -> Any:
        return self._request(
            "POST",
            f"bookings/{booking_uid}/cancel",
            version=self.bookings_api_version,
            json_body=payload,
        )


def _extract_error_message(data: Any) -> str:
    if isinstance(data, dict):
        err = data.get("error") or data.get("message")
        if isinstance(err, dict):
            return str(err.get("message") or err)
        if err:
            return str(err)
        return str(data)[:500]
    return str(data)[:500]


def extract_data_field(response: Any) -> Any:
    """Return the `data` payload from a typical Cal.com envelope."""
    if isinstance(response, dict) and "data" in response:
        return response["data"]
    return response


def extract_bookings_list(response: Any) -> list[dict[str, Any]] | None:
    """
    Normalize list-bookings responses: ``data`` as array, or ``{ data: { bookings: [...] } }``.
    Returns None if the shape is not recognized (caller should treat as empty/error).
    """
    data = extract_data_field(response)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        inner = data.get("bookings")
        if isinstance(inner, list):
            return [x for x in inner if isinstance(x, dict)]
    return None


def flatten_slots_response(response: Any) -> list[dict[str, str]]:
    """
    Flatten slots payload to a list of {start, end} dicts.

    Handles:
    - ``data`` as dict of date -> list of slots
    - ``data`` as list
    - range format objects or time-only strings
    """
    data = extract_data_field(response)
    out: list[dict[str, str]] = []
    if data is None:
        return out

    def add_slot(slot: Any) -> None:
        if isinstance(slot, dict):
            s = slot.get("start")
            e = slot.get("end")
            if s and e:
                out.append({"start": str(s), "end": str(e)})
            elif s:
                out.append({"start": str(s), "end": str(s)})
        elif isinstance(slot, str):
            out.append({"start": slot, "end": slot})

    if isinstance(data, dict):
        for _date_key, slots in data.items():
            # Ignore non-slot keys if present (defensive).
            if not isinstance(slots, list):
                continue
            for slot in slots:
                add_slot(slot)
    elif isinstance(data, list):
        for slot in data:
            add_slot(slot)
    return out
