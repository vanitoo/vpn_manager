from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

router = Router()


@router.message(Command('cancel'))
async def cancel_command(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    if current:
        await message.answer('❌ Действие отменено. Состояние очищено.')
    else:
        await message.answer('Активного действия нет.')


@router.callback_query(F.data == 'fsm:cancel')
async def cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer('Отменено')
    await callback.message.answer('❌ Действие отменено.')
