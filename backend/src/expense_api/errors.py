"""One error shape for every failure: {"error": {"code", "message", "details"}}. No traceback reaches a client."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from starlette.exceptions import HTTPException

log = logging.getLogger("expense_api")


class ApiError(Exception):
    """A failure with its HTTP status and a stable code. The message must be safe to show a client."""

    def __init__(self, status: int, code: str, message: str, details: list | dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def error_body(code: str, message: str, details: list | dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError):
        return JSONResponse(error_body(error.code, error.message, error.details), status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        # The offending input is left out: it may be large, or hold data that should not be echoed.
        details = [{"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]} for item in error.errors()]
        return JSONResponse(error_body("invalid_request", "the request does not match its schema", details), status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(error.status_code, "http_error")
        return JSONResponse(error_body(code, str(error.detail)), status_code=error.status_code)

    @app.exception_handler(PyMongoError)
    async def database_error(request: Request, error: PyMongoError):
        log.error("database failure: %s", type(error).__name__)
        return JSONResponse(error_body("database_unavailable", "the database is unavailable"), status_code=503)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception):
        log.exception("unexpected failure: %s", type(error).__name__)
        return JSONResponse(error_body("internal_error", "the server failed to handle the request"), status_code=500)
