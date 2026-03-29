"""
HTTP API: Retell custom functions webhook and health check.

Scheduling uses ``service_key`` (``repair_request``, ``repair_estimate`` from ``SERVICE_CATALOG`` /
``SERVICE_CATALOG_JSON``), optional ``duration_minutes`` for slots/bookings, and ``book_appointment``
requires ``address`` (job site). Legacy ``event_type_id`` is still accepted when needed — see
``booking/service_catalog.py``.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from django.conf import settings
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from booking.exceptions import AppError, ExternalAPIError, SignatureVerificationError, ValidationAppError
from booking.models import RetellFunctionCallLog
from booking.serializers import FUNCTION_SERIALIZERS
from booking.services.calcom import CalComClient
from booking.services.function_handlers import FUNCTION_HANDLERS
from booking.services.retell_verify import verify_retell_request
from booking.utils.retell_payload import normalize_retell_function_arguments

logger = logging.getLogger(__name__)


def _retell_debug_stderr(msg: str) -> None:
    """Plain-text Retell lines to stderr when RETELL_LOG_VERBOSE is enabled."""
    if getattr(settings, "RETELL_LOG_VERBOSE", False):
        print(msg, file=sys.stderr, flush=True)


def _retell_verbose_logging() -> bool:
    return getattr(settings, "RETELL_LOG_VERBOSE", False)


def _log_retell_incoming_request(
    *,
    correlation_id: str,
    raw_body: dict[str, Any],
    arguments_normalized: dict[str, Any],
) -> None:
    """Log Retell webhook JSON and normalized tool arguments (structured log + pretty stderr)."""
    if not _retell_verbose_logging():
        return
    ctx = {
        "correlation_id": correlation_id,
        "body": raw_body,
        "arguments_normalized": arguments_normalized,
    }
    try:
        pretty = json.dumps(ctx, indent=2, ensure_ascii=False, default=str)
    except TypeError:
        pretty = repr(ctx)
    logger.info(
        "retell_incoming_request",
        extra={"ctx": ctx},
    )
    print(
        f"[Retell] INCOMING correlation_id={correlation_id}\n{pretty}",
        file=sys.stderr,
        flush=True,
    )


def _log_retell_outgoing_response(
    *,
    correlation_id: str,
    function_name: str,
    http_status: int,
    body: Any,
) -> None:
    """Log the exact JSON body returned to Retell (structured log + pretty stderr)."""
    if not _retell_verbose_logging():
        return
    try:
        pretty = json.dumps(body, indent=2, ensure_ascii=False, default=str)
    except TypeError:
        pretty = repr(body)
    logger.info(
        "retell_outgoing_response",
        extra={
            "ctx": {
                "correlation_id": correlation_id,
                "function_name": function_name,
                "http_status": http_status,
                "response": body,
                "response_json": pretty,
            }
        },
    )
    print(
        f"[Retell] OUTGOING HTTP {http_status} function={function_name} correlation_id={correlation_id}\n{pretty}",
        file=sys.stderr,
        flush=True,
    )


def _drf_detail_to_plain(detail: Any) -> Any:
    """Make DRF error_detail JSON/log safe (ErrorDetail -> str)."""
    try:
        from rest_framework.exceptions import ErrorDetail
    except ImportError:
        ErrorDetail = ()  # type: ignore

    if isinstance(detail, dict):
        return {str(k): _drf_detail_to_plain(v) for k, v in detail.items()}
    if isinstance(detail, list):
        return [_drf_detail_to_plain(x) for x in detail]
    if ErrorDetail and isinstance(detail, ErrorDetail):
        return str(detail)
    return detail


def health_check(_request):
    return HttpResponse("ok", content_type="text/plain")


@method_decorator(csrf_exempt, name="dispatch")
class EventTypesListView(APIView):
    """
    GET /api/event-types/

    Proxies to Cal.com ``GET /v2/event-types`` using server ``CALCOM_API_KEY``.
    Forwards query string parameters (e.g. ``username``, ``orgSlug``) to Cal.com.
    """

    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        correlation_id = getattr(request, "correlation_id", "")
        params = {k: v for k, v in request.query_params.items() if v != ""}
        logger.info(
            "calcom_event_types_request",
            extra={
                "ctx": {
                    "correlation_id": correlation_id,
                    "query_keys": sorted(params.keys()),
                }
            },
        )
        try:
            client = CalComClient()
            raw = client.get_event_types(params=params if params else None)
            return Response(raw, status=status.HTTP_200_OK)
        except ExternalAPIError as e:
            return Response(
                {"success": False, "error": str(e), "error_code": e.error_code},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            logger.exception(
                "event_types_proxy_failed",
                extra={"ctx": {"correlation_id": correlation_id}},
            )
            return Response(
                {"success": False, "error": "Internal server error", "error_code": "internal_error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


@method_decorator(csrf_exempt, name="dispatch")
class RetellFunctionDispatchView(APIView):
    """
    POST /api/retell/functions/

    Expects raw JSON body (signature covers exact bytes). Parses function name from
    ``name`` / ``function_name`` and arguments from ``arguments`` / ``args``.
    """

    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        correlation_id = getattr(request, "correlation_id", "")
        raw_body = request.body.decode("utf-8")
        sig = request.headers.get("x-retell-signature") or request.headers.get("X-Retell-Signature")

        fn_name = ""
        try:
            if not verify_retell_request(raw_body, sig, settings.RETELL_API_KEY):
                logger.warning(
                    "retell_signature_invalid",
                    extra={"ctx": {"correlation_id": correlation_id}},
                )
                raise SignatureVerificationError("Invalid or missing Retell signature")

            try:
                body = json.loads(raw_body) if raw_body.strip() else {}
            except json.JSONDecodeError as e:
                logger.warning(
                    "retell_json_parse_failed",
                    extra={
                        "ctx": {
                            "correlation_id": correlation_id,
                            "error": str(e),
                            "body_preview": raw_body[:500],
                        }
                    },
                )
                _retell_debug_stderr(
                    f"[Retell] JSON parse error: {e!s} correlation_id={correlation_id} body_preview={raw_body[:200]!r}"
                )
                raise ValidationAppError(
                    f"Invalid JSON body: {e}",
                    details={"reason": "json_decode_error", "message": str(e)},
                ) from e

            fn_name, fn_args = _extract_function_call(body)
            fn_args = normalize_retell_function_arguments(fn_args)
            _log_retell_incoming_request(
                correlation_id=correlation_id,
                raw_body=body,
                arguments_normalized=fn_args,
            )
            arg_keys = sorted(fn_args.keys())
            dispatch_ctx: dict[str, Any] = {
                "correlation_id": correlation_id,
                "function_name": fn_name,
                "argument_keys": arg_keys,
            }
            if "duration_minutes" in fn_args:
                dispatch_ctx["duration_minutes"] = fn_args["duration_minutes"]
            logger.debug(
                "retell_function_dispatch",
                extra={"ctx": dispatch_ctx},
            )
            _retell_debug_stderr(
                f"[Retell] dispatch function={fn_name} correlation_id={correlation_id} argument_keys={arg_keys}"
            )

            if fn_name not in FUNCTION_HANDLERS:
                allowed = sorted(FUNCTION_HANDLERS.keys())
                logger.warning(
                    "retell_unknown_function",
                    extra={
                        "ctx": {
                            "correlation_id": correlation_id,
                            "function_name": fn_name,
                            "allowed_functions": allowed,
                        }
                    },
                )
                _retell_debug_stderr(
                    f"[Retell] unknown_function name={fn_name!r} correlation_id={correlation_id} allowed={allowed}"
                )
                raise ValidationAppError(
                    f"Unknown function: {fn_name}",
                    error_code="unknown_function",
                    details={"function_name": fn_name, "allowed_functions": allowed},
                )

            ser_cls = FUNCTION_SERIALIZERS[fn_name]
            serializer = ser_cls(data=fn_args)
            try:
                serializer.is_valid(raise_exception=True)
            except DRFValidationError as e:
                plain_detail = _drf_detail_to_plain(e.detail)
                err_msg = _format_drf_errors(e.detail)
                logger.warning(
                    "retell_validation_failed",
                    extra={
                        "ctx": {
                            "correlation_id": correlation_id,
                            "function_name": fn_name,
                            "argument_keys": arg_keys,
                            "errors": err_msg,
                            "validation_detail": plain_detail,
                        }
                    },
                )
                _retell_debug_stderr(
                    f"[Retell] VALIDATION FAILED function={fn_name} correlation_id={correlation_id}\n"
                    f"  argument_keys={arg_keys}\n"
                    f"  errors={err_msg}\n"
                    f"  detail={plain_detail!r}"
                )
                raise ValidationAppError(
                    err_msg,
                    error_code="validation_error",
                    details=plain_detail if isinstance(plain_detail, (dict, list)) else {"detail": plain_detail},
                ) from e

            validated = serializer.validated_data
            client = CalComClient()
            handler = FUNCTION_HANDLERS[fn_name]
            result = handler(client, validated)

            _persist_audit(
                function_name=fn_name,
                correlation_id=correlation_id,
                payload={"name": fn_name, "arguments": validated},
                response=result,
                success=True,
                error_message="",
            )
            _log_retell_outgoing_response(
                correlation_id=correlation_id,
                function_name=fn_name,
                http_status=status.HTTP_200_OK,
                body=result,
            )
            return Response(result, status=status.HTTP_200_OK)

        except AppError as e:
            payload = {
                "success": False,
                "error": str(e),
                "error_code": getattr(e, "error_code", "app_error"),
            }
            details = getattr(e, "details", None)
            if details is not None:
                payload["details"] = details
            logger.warning(
                "retell_app_error",
                extra={
                    "ctx": {
                        "correlation_id": correlation_id,
                        "function_name": fn_name or "unknown",
                        "error_code": payload["error_code"],
                        "error": str(e),
                        "has_details": details is not None,
                    }
                },
            )
            _retell_debug_stderr(
                f"[Retell] APP ERROR correlation_id={correlation_id} function={fn_name or 'unknown'} "
                f"code={payload['error_code']} message={e!s}"
            )
            _log_retell_outgoing_response(
                correlation_id=correlation_id,
                function_name=fn_name or "unknown",
                http_status=int(getattr(e, "status_code", 400)),
                body=payload,
            )
            _persist_audit(
                function_name=fn_name or "unknown",
                correlation_id=correlation_id,
                payload=_safe_parse_body(raw_body),
                response=payload,
                success=False,
                error_message=str(e),
            )
            return Response(payload, status=getattr(e, "status_code", 400))
        except Exception as e:
            logger.exception(
                "retell_unhandled_exception",
                extra={"ctx": {"correlation_id": correlation_id, "function_name": fn_name}},
            )
            payload = {
                "success": False,
                "error": "Internal server error",
                "error_code": "internal_error",
            }
            _log_retell_outgoing_response(
                correlation_id=correlation_id,
                function_name=fn_name or "unknown",
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                body=payload,
            )
            _persist_audit(
                function_name=fn_name or "unknown",
                correlation_id=correlation_id,
                payload=_safe_parse_body(raw_body),
                response=payload,
                success=False,
                error_message=str(e),
            )
            return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _format_drf_errors(detail: Any) -> str:
    if isinstance(detail, list):
        return "; ".join(_format_drf_errors(x) for x in detail)
    if isinstance(detail, dict):
        parts = []
        for k, v in detail.items():
            parts.append(f"{k}: {_format_drf_errors(v)}")
        return "; ".join(parts)
    return str(detail)


def _extract_function_call(body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    name = body.get("name") or body.get("function_name")
    args = body.get("arguments") or body.get("args")
    if not name or not isinstance(name, str):
        raise ValidationAppError(
            "Missing function name (name / function_name).",
            details={"reason": "missing_function_name", "got_type": type(name).__name__},
        )
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValidationAppError(
            "Arguments must be a JSON object (arguments / args).",
            details={"reason": "arguments_not_object", "got_type": type(args).__name__},
        )
    return name.strip(), args


def _persist_audit(
    *,
    function_name: str,
    correlation_id: str,
    payload: dict[str, Any],
    response: dict[str, Any] | None,
    success: bool,
    error_message: str,
) -> None:
    try:
        RetellFunctionCallLog.objects.create(
            function_name=function_name,
            request_id=correlation_id,
            payload_json=payload,
            response_json=response or {},
            success=success,
            error_message=error_message[:2000],
        )
    except Exception:
        logger.exception("audit_log_write_failed")


def _safe_parse_body(raw_body: str) -> dict[str, Any]:
    try:
        return json.loads(raw_body) if raw_body.strip() else {}
    except json.JSONDecodeError:
        return {"raw": "unparseable"}
