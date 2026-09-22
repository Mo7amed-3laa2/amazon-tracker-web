"""The JSON API, assembled from one router per resource."""

from fastapi import APIRouter

from api import products, session, settings, sweep

router = APIRouter(prefix="/api")
router.include_router(session.router)
router.include_router(products.router)
router.include_router(settings.router)
router.include_router(sweep.router)
