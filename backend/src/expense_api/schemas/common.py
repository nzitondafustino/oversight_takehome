"""Shared HTTP types."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class RequestBody(BaseModel):
    """A request body: unknown fields are refused, so nothing such as a credential can slip in unread."""

    model_config = ConfigDict(extra="forbid")


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: list[Any] | dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorInfo


def errors(*statuses: int) -> dict[int, dict]:
    """OpenAPI entries for the error responses a route can return."""
    return {status: {"model": ErrorResponse} for status in statuses}
