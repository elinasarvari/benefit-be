from __future__ import annotations

from uuid import UUID

from packages.domain.errors import ErrorCode, FieldError

STATUS_FOR_CODE: dict[ErrorCode, int] = {
    ErrorCode.auth_required: 401,
    ErrorCode.auth_session_expired: 401,
    ErrorCode.auth_invalid_credentials: 401,
    ErrorCode.auth_forbidden: 403,
    ErrorCode.not_found: 404,
    ErrorCode.conflict: 409,
    ErrorCode.validation_failed: 422,
    ErrorCode.internal_error: 500,
    ErrorCode.dependency_unavailable: 503,
    ErrorCode.not_implemented: 501,
}

DEFAULT_MESSAGE: dict[ErrorCode, str] = {
    ErrorCode.auth_required: "Authentication is required.",
    ErrorCode.auth_session_expired: "Your session has expired. Please sign in again.",
    ErrorCode.auth_invalid_credentials: "Invalid username or password.",
    ErrorCode.auth_forbidden: "You are not allowed to do this.",
    ErrorCode.not_found: "Not found.",
    ErrorCode.conflict: "The request conflicts with the current state.",
    ErrorCode.validation_failed: "Some fields are invalid.",
    ErrorCode.internal_error: "Something went wrong.",
    ErrorCode.dependency_unavailable: "A required service is unavailable.",
    ErrorCode.not_implemented: "This feature is not available yet.",
}


class AppError(Exception):
    """An error the client may see. The message is fixed per code and never carries internals."""

    code: ErrorCode = ErrorCode.internal_error
    # A status other than the code's own, for refusals HTTP names more exactly (413, 415).
    http_status: int | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        fields: list[FieldError] | None = None,
        related_case_id: UUID | None = None,
    ):
        self.message = message or DEFAULT_MESSAGE[self.code]
        self.fields = fields or []
        self.related_case_id = (
            related_case_id  # only the two household refusals set it (ADR-001-09)
        )
        super().__init__(self.message)

    @property
    def status(self) -> int:
        return STATUS_FOR_CODE[self.code]

    @staticmethod
    def for_code(code: ErrorCode) -> AppError:
        error = AppError()
        error.code = code
        error.message = DEFAULT_MESSAGE[code]
        return error


class AuthRequired(AppError):
    code = ErrorCode.auth_required


class SessionExpired(AppError):
    code = ErrorCode.auth_session_expired


class InvalidCredentials(AppError):
    code = ErrorCode.auth_invalid_credentials


class Forbidden(AppError):
    code = ErrorCode.auth_forbidden


class NotFound(AppError):
    code = ErrorCode.not_found


class Conflict(AppError):
    code = ErrorCode.conflict


class InvalidInput(AppError):
    """A well-formed request whose value is not acceptable (for example a bad cursor)."""

    code = ErrorCode.validation_failed


class DependencyUnavailable(AppError):
    code = ErrorCode.dependency_unavailable


class CredentialRejected(AppError):
    """A system credential failed verification (wrong case, expired, wrong audience or type)."""

    code = ErrorCode.auth_forbidden


class NotBuiltYet(AppError):
    """A contract endpoint whose feature has not landed (MVP contracts PR). Each feature replaces
    its stubs; nothing should raise this once the MVP is built."""

    code = ErrorCode.not_implemented
