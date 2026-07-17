from aiogram import Router

from app.support.operator import router as operator_router
from app.support.user import router as user_router

router = Router()
router.include_router(user_router)
router.include_router(operator_router)
