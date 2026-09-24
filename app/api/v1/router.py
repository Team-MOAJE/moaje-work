from fastapi import APIRouter
from app.api.v1.endpoints.spending import router as spending_router
from app.api.v1.endpoints.fds import router as fds_router
from app.api.v1.endpoints.calendar import router as calendar_router
from app.api.v1.endpoints.onboarding import router as onboarding_router
from app.api.v1.endpoints.simulator import router as simulator_router
from app.api.v1.endpoints.readiness import router as readiness_router
from app.api.v1.endpoints.recap import router as recap_router
from app.api.v1.endpoints.metrics import router as metrics_router

api_router = APIRouter()
api_router.include_router(spending_router)
api_router.include_router(fds_router)
api_router.include_router(calendar_router)
api_router.include_router(onboarding_router)
api_router.include_router(simulator_router)
api_router.include_router(readiness_router)
api_router.include_router(recap_router)
api_router.include_router(metrics_router)
