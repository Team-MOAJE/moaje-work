from fastapi import APIRouter
from app.api.v1.endpoints.spending import router as spending_router
from app.api.v1.endpoints.fds import router as fds_router

api_router = APIRouter()
api_router.include_router(spending_router)
api_router.include_router(fds_router)
