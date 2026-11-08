"""GET /health: the application answers even when MongoDB does not."""

from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pymongo.database import Database
from pymongo.errors import PyMongoError

import expense_api
from expense_api.dependencies import get_raw_database

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    application: Literal["running"]
    database: Literal["ok", "unavailable"]
    version: str


@router.get("/health", response_model=Health, responses={503: {"model": Health, "description": "Running, but MongoDB is unavailable"}})
def health(database: Database = Depends(get_raw_database)):
    try:
        database.command("ping")
    except PyMongoError:
        body = Health(status="degraded", application="running", database="unavailable", version=expense_api.__version__)
        return JSONResponse(body.model_dump(), status_code=503)
    return Health(status="ok", application="running", database="ok", version=expense_api.__version__)
