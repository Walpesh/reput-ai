from fastapi import APIRouter
from reput_ai.api.v1.auth import router as auth_router
from reput_ai.api.v1.branches import router as branches_router
from reput_ai.api.v1.reviews import router as reviews_router
from reput_ai.api.v1.subscriptions import router as subscriptions_router
from reput_ai.api.v1.funnel import router as funnel_router
from reput_ai.api.v1.telegram_webhook import router as telegram_router

api_v1_router = APIRouter()

api_v1_router.include_router(auth_router)
api_v1_router.include_router(branches_router)
api_v1_router.include_router(reviews_router)
api_v1_router.include_router(subscriptions_router)
api_v1_router.include_router(funnel_router)
api_v1_router.include_router(telegram_router)
