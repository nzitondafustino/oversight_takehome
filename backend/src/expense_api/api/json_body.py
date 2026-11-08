"""Read request bodies as the core reads JSONL: exact decimals, no NaN or Infinity, no repeated keys.

Standard JSON decoding turns 12.10 into a float before any model sees it, so money and thresholds would lose
their exact value at the HTTP boundary. Routes built with DecimalJSONRoute decode with Decimal instead.
"""

import json
from collections.abc import Callable
from decimal import Decimal

from fastapi import Request, Response
from fastapi.routing import APIRoute


def parse_json(body: bytes) -> object:
    """Decode JSON with exact decimals. Refused constructs raise JSONDecodeError, which FastAPI reports as 422."""
    text = body.decode("utf-8")
    return json.loads(text, parse_float=Decimal, parse_constant=_refuse_constant, object_pairs_hook=_refuse_repeats)


def as_standard_json(value: object) -> object:
    """The value as standard JSON decoding gives it, for core records that are hashed as plain JSON, such as approvals."""
    if isinstance(value, Decimal):
        return float(value)  # the correctly rounded float, exactly as json.loads reads the same literal
    if isinstance(value, dict):
        return {key: as_standard_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [as_standard_json(item) for item in value]
    return value


def _refuse_constant(name: str):
    raise json.JSONDecodeError(f"{name} is not a JSON number", name, 0)


def _refuse_repeats(pairs: list[tuple[str, object]]) -> dict[str, object]:
    names = [name for name, _ in pairs]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise json.JSONDecodeError(f"repeated keys: {repeated}", "", 0)
    return dict(pairs)


class _DecimalJSONRequest(Request):
    async def json(self) -> object:
        if not hasattr(self, "_json"):
            try:
                self._json = parse_json(await self.body())
            except UnicodeDecodeError as error:
                raise json.JSONDecodeError("the body is not UTF-8", "", error.start) from None
        return self._json


class DecimalJSONRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def route(request: Request) -> Response:
            return await handler(_DecimalJSONRequest(request.scope, request.receive))

        return route
