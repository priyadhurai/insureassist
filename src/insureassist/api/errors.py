"""One error format for every failure, and nothing internal in it:

    {"error": {"code": "not_found", "message": "...", "request_id": "...", "details": {...}}}

Stack traces, SQL, provider messages and secrets go only to the server log.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("insureassist.api")


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.details = details or {}
        self.headers = headers or {}


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": getattr(request.state, "request_id", None),
    }
    if details:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        return error_response(
            request, error.status, error.code, error.message, error.details, error.headers
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        # Field locations and messages only: never echo the submitted values back.
        problems = [
            {"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg", "")}
            for e in error.errors()
        ]
        return error_response(
            request, 422, "validation_error", "The request is invalid.", {"errors": problems}
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed", 401: "authentication_required"}
        return error_response(
            request,
            error.status_code,
            codes.get(error.status_code, "bad_request"),
            str(error.detail) if error.status_code != 404 else "Not found.",
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        log.exception(
            "unexpected error", extra={"request_id": getattr(request.state, "request_id", None)}
        )
        return error_response(request, 500, "internal_error", "Something went wrong.")
