"""The application factory: uvicorn expense_api.main:create_app --factory."""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from expense_validator import ExpenseValidator, ValidatorSettings
from fastapi import FastAPI
from pymongo import MongoClient
from pymongo.errors import PyMongoError

import expense_api
from expense_api.api.v1 import discovery, documents, evaluations, health, rulesets, runs, validation
from expense_api.config import Settings
from expense_api.errors import install_error_handlers
from expense_api.api.v1 import management
from expense_api.repositories import ensure_indexes

log = logging.getLogger("expense_api")


def create_app(
    settings: Settings | None = None,
    *,
    mongo_client: MongoClient | None = None,
    build_validator: Callable[[ValidatorSettings], ExpenseValidator] = ExpenseValidator,
    document_store=None,
) -> FastAPI:
    """Build the app. Tests pass their own settings, client and validator; a deployment passes nothing."""
    settings = settings or Settings()
    client = mongo_client or MongoClient(settings.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=settings.mongo_timeout_ms, tz_aware=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            ensure_indexes(app.state.database)
            app.state.indexes_ready = True
        except PyMongoError as error:  # the app still starts, and /health reports the database
            log.warning("indexes not created at startup, will retry on first use: %s", type(error).__name__)
        yield
        if mongo_client is None:
            client.close()

    app = FastAPI(title="Expense Policy Validator API", version=expense_api.__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.database = client[settings.mongo_database]
    app.state.indexes_ready = False
    app.state.build_validator = build_validator
    app.state.document_store = document_store  # None: built from settings on first use (dependencies.get_document_store)
    install_error_handlers(app)
    app.include_router(management.router)
    app.include_router(health.router)
    app.include_router(rulesets.router)
    app.include_router(documents.router)
    app.include_router(validation.router)
    app.include_router(runs.router)
    app.include_router(discovery.router)
    app.include_router(evaluations.router)
    return app
