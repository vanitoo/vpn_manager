from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app import runtime
from app.products.mtproto.handlers import render_admin

router = Router()


@router.message(Command('mtproto_admin'))
async def mtproto_admin_command(message: Message) -> None:
    if not runtime.admin(message):
        return
    await render_admin(message)
