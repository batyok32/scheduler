"""Domain exceptions for the booking / Retell integration layer."""


class AppError(Exception):
    """Base class for application errors that map to HTTP responses."""

    error_code: str = "app_error"
    status_code: int = 400

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        if error_code:
            self.error_code = error_code


class SignatureVerificationError(AppError):
    error_code = "signature_verification_failed"
    status_code = 401


class ValidationAppError(AppError):
    error_code = "validation_error"
    status_code = 400

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        details: dict | list | None = None,
    ) -> None:
        super().__init__(message, error_code=error_code)
        self.details = details


class ExternalAPIError(AppError):
    error_code = "external_api_error"
    status_code = 502

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        status_code: int | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, error_code=error_code)
        if status_code is not None:
            self.status_code = status_code
        self.details = details


class BookingNotFoundError(AppError):
    error_code = "booking_not_found"
    status_code = 404


class AmbiguousBookingMatchError(AppError):
    error_code = "ambiguous_booking_match"
    status_code = 409
