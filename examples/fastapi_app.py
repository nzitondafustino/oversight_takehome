"""Expose ExpenseValidator through a minimal FastAPI example.

Install the api extra and uvicorn. Set EXPENSE_VALIDATOR_CONFIG before serving
examples.fastapi_app:app. Use replay mode and a recording cache for offline runs.

Production callers must authenticate requests and configure the
EXPENSE_VALIDATOR_MODELS allowlist. The synchronous handler uses FastAPI's
thread pool with a shared, stateless validator.
"""

import os
from pathlib import Path

from expense_validator import ConfigurationError, EvidenceError, ExpenseValidator, InvalidInputError, ReplayAnswerMissing


MODEL_CHOICES = ("classification_model", "translation_model")


def validate_request(validator: ExpenseValidator, payload: object, allowed_models: frozenset[str] | None = None) -> tuple[int, dict]:
    """Validate an API payload and return its HTTP response.

    Args:
        validator: Configured expense validator.
        payload: Request body with context completeness, receipts, and model overrides.
        allowed_models: Optional allowlist for model overrides.

    Returns:
        HTTP status and JSON body. Invalid or disallowed overrides return 422.
    """
    if not isinstance(payload, dict) or not {"context_complete", "receipts"} <= set(payload) or not isinstance(payload["receipts"], list):
        return 422, {"error": "the body needs context_complete and a list of receipts"}
    models = {name: payload[name] for name in MODEL_CHOICES if payload.get(name) is not None}
    if allowed_models is not None:
        refused = sorted(name for name, model in models.items() if model not in allowed_models)
        if refused:
            return 422, {"error": f"not an allowed model: {refused}", "allowed": sorted(allowed_models)}
    try:
        batch = validator.validate_batch(payload["receipts"], context_complete=payload["context_complete"], **models)
    except ConfigurationError as error:
        if models:  # raised for this request's choice: the validator itself was checked when it was built
            return 422, {"error": str(error)}
        return 500, {"error": type(error).__name__}
    except InvalidInputError as error:
        return 422, {"error": str(error)}
    except ReplayAnswerMissing as error:
        return 503, {"error": str(error), "missing": len(error.missing)}
    except (ConfigurationError, EvidenceError) as error:
        return 500, {"error": type(error).__name__}
    return 200, batch.model_dump(mode="json")


def create_app(validator: ExpenseValidator, allowed_models: frozenset[str] | None = None):
    from fastapi import Body, FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI(title="Expense Policy Validator")

    @app.post("/v1/validations")
    def validate(payload: dict = Body(...)):
        status, body = validate_request(validator, payload, allowed_models)
        return JSONResponse(body, status_code=status)

    return app


def _from_environment():
    cache = os.environ.get("EXPENSE_VALIDATOR_CACHE")
    validator = ExpenseValidator.from_config(
        Path(os.environ.get("EXPENSE_VALIDATOR_CONFIG", "config.yaml")),
        mode=os.environ.get("EXPENSE_VALIDATOR_MODE", "replay"),
        **({"cache_dir": Path(cache)} if cache else {}),
    )
    allowed = os.environ.get("EXPENSE_VALIDATOR_MODELS")
    return create_app(validator, frozenset(model.strip() for model in allowed.split(",") if model.strip()) if allowed else None)


if os.environ.get("EXPENSE_VALIDATOR_CONFIG"):
    app = _from_environment()
