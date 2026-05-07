from fastapi import APIRouter
from app.api.v1.endpoints.spending import router as spending_router

api_router = APIRouter()
api_router.include_router(spending_router)
