from __future__ import annotations

import uuid
from uuid import UUID

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from starlette.exceptions import HTTPException as StarletteHTTPException

from packages.domain.errors import ErrorBody, ErrorCode, ErrorEnvelope, FieldError
from packages.platform.errors import DEFAULT_MESSAGE, STATUS_FOR_CODE, AppError


def error_response(
    code: ErrorCode,
    reference_id: str,
    *,
    message: str | None = None,
    fields: list[FieldError] | None = None,
    related_case_id: UUID | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message or DEFAULT_MESSAGE[code],
            fields=fields or [],
            reference_id=reference_id,
            related_case_id=related_case_id,
        )
    )
    return JSONResponse(
        envelope.model_dump(mode="json"),
        status_code=STATUS_FOR_CODE[code],
        headers={"X-Request-ID": reference_id},
    )


def _reference_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", None) or uuid.uuid4())


_CODE_FOR_STATUS = {
    401: ErrorCode.auth_required,
    403: ErrorCode.auth_forbidden,
    404: ErrorCode.not_found,
    409: ErrorCode.conflict,
    503: ErrorCode.dependency_unavailable,
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PyMongoError)
    async def handle_database(request: Request, exc: PyMongoError) -> JSONResponse:
        structlog.get_logger().error(
            "dependency.unavailable", dependency="mongo", error=type(exc).__name__
        )
        return error_response(ErrorCode.dependency_unavailable, _reference_id(request))

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(
            exc.code,
            _reference_id(request),
            message=exc.message,
            fields=exc.fields,
            related_case_id=exc.related_case_id,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Only the location, message and type are returned. The submitted value is never echoed.
        fields = [
            FieldError(
                field=".".join(str(part) for part in error["loc"]),
                message=str(error["msg"]),
                type=str(error["type"]),
            )
            for error in exc.errors()
        ]
        return error_response(ErrorCode.validation_failed, _reference_id(request), fields=fields)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _CODE_FOR_STATUS.get(exc.status_code)
        if code is None:
            code = (
                ErrorCode.internal_error if exc.status_code >= 500 else ErrorCode.validation_failed
            )
        return error_response(code, _reference_id(request))
