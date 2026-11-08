"""GET /v1/backends and GET /v1/taxonomy: what the installed core and this deployment support. No model is called."""

from typing import Annotated

from fastapi import APIRouter, Depends

from expense_api.config import Settings
from expense_api.dependencies import get_settings
from expense_api.schemas.common import errors
from expense_api.schemas.discovery import Backends, Taxonomy
from expense_api.services import discovery

router = APIRouter(prefix="/v1", tags=["discovery"])
Deployment = Annotated[Settings, Depends(get_settings)]


@router.get("/backends", response_model=Backends, responses=errors(503))
def backends(settings: Deployment):
    """Each classification backend: installed in the core, enabled here, and available for replay or live use; and the policy compiler's recording sets and live extraction."""
    return discovery.backends(settings)


@router.get("/taxonomy", response_model=Taxonomy, responses=errors(503))
def taxonomy(settings: Deployment):
    """The core's field catalog and category taxonomy, exactly as the validator reads them, with their hashes."""
    return discovery.taxonomy(settings)
