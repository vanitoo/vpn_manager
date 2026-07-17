from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app import runtime
from app.db import upsert_user
from app.support.common import CATEGORIES, category_menu, chat_menu, forward_user_message, help_menu, result_menu
from app.support.diagnostics import collect_diagnostics, user_diagnostic_text
from app.support.settings import enabled, group_id
from app.support.storage import create_ticket, get_open_ticket, update_ticket_status

router = Router()
log = logging.getLogger(__name__)


class SupportState(StatesGroup):
    first_message = State()
    chatting = State()


@router.callback_query(F.data == 'help')
async def home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    ticket = await get_open_ticket(runtime.settings.db_path, callback.from_user.id)
    await callback.answer()
    await callback.message.answer(
        '❓ <b>Помощь</b>\n\nСначала FAQ или диагностика. Если проблема останется, создайте обращение специалисту.',
        reply_markup=help_menu(bool(ticket)),
    )


@router.callback_query(F.data == 'support:diag')
async def diagnostics(callback: CallbackQuery) -> None:
    await callback.answer('Проверяю')
    data = await collect_diagnostics(callback.from_user.id)
    await callback.message.answer(user_diagnostic_text(data), reply_markup=result_menu())


@router.callback_query(F.data == 'support:new')
async def new_ticket(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer('Что случилось?', reply_markup=category_menu())


@router.callback_query(F.data.startswith('support:cat:'))
async def category(callback: CallbackQuery, state: FSMContext) -> None:
    code = callback.data.rsplit(':', 1)[1]
    await state.update_data(category=code)
    await callback.answer('Запускаю диагностику')
    text = user_diagnostic_text(await collect_diagnostics(callback.from_user.id))
    await callback.message.answer(
        f"Тема: <b>{CATEGORIES.get(code, 'Другое')}</b>\n\n{text}",
        reply_markup=result_menu(code),
    )


@router.callback_query(F.data == 'support:solved')
async def solved(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer('Отлично')
    await callback.message.answer('✅ Обращение создавать не будем.', reply_markup=help_menu())


@router.callback_query(F.data.startswith('support:contact:'))
async def contact(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(category=callback.data.rsplit(':', 1)[1])
    await state.set_state(SupportState.first_message)
    await callback.answer()
    await callback.message.answer(
        'Опишите проблему. Можно приложить фото, видео, голосовое сообщение или файл.\n\nДля отмены: /cancel'
    )


@router.callback_query(F.data == 'support:continue')
async def continue_ticket(callback: CallbackQuery, state: FSMContext) -> None:
    ticket = await get_open_ticket(runtime.settings.db_path, callback.from_user.id)
    if not ticket:
        await callback.answer('Открытого обращения нет', show_alert=True)
        return
    await state.set_state(SupportState.chatting)
    await callback.answer()
    await callback.message.answer(
        f"🎫 Обращение <b>#{ticket['id']}</b> открыто. Отправьте сообщение.",
        reply_markup=chat_menu(),
    )


@router.message(SupportState.first_message)
async def first_message(message: Message, state: FSMContext) -> None:
    if not message.from_user or message.chat.type != ChatType.PRIVATE:
        return
    if not enabled() or not group_id():
        await message.answer('Поддержка пока не настроена. Администратору нужно указать SUPPORT_GROUP_ID.')
        return
    data = await state.get_data()
    user = await upsert_user(
        runtime.settings.db_path,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    ticket = await get_open_ticket(runtime.settings.db_path, message.from_user.id)
    if not ticket:
        ticket = await create_ticket(
            runtime.settings.db_path,
            telegram_id=message.from_user.id,
            user_id=int(user['id']),
            category=str(data.get('category') or 'other'),
        )
    try:
        await forward_user_message(message, ticket)
    except Exception as exc:
        log.exception('Support topic creation failed')
        await message.answer(f'Не удалось создать обращение: <code>{str(exc)[:700]}</code>')
        return
    await state.set_state(SupportState.chatting)
    await message.answer(
        f"✅ Обращение <b>#{ticket['id']}</b> создано. Ответ специалиста придёт сюда.",
        reply_markup=chat_menu(),
    )


@router.message(SupportState.chatting)
async def chat_message(message: Message) -> None:
    if not message.from_user or message.chat.type != ChatType.PRIVATE:
        return
    ticket = await get_open_ticket(runtime.settings.db_path, message.from_user.id)
    if not ticket:
        await message.answer('Открытое обращение не найдено.', reply_markup=help_menu())
        return
    try:
        await forward_user_message(message, ticket)
        await message.answer('📨 Сообщение передано специалисту.', reply_markup=chat_menu())
    except Exception as exc:
        log.exception('Support forwarding failed')
        await message.answer(f'Не удалось передать сообщение: <code>{str(exc)[:700]}</code>')


@router.callback_query(F.data == 'support:close')
async def close_ticket(callback: CallbackQuery, state: FSMContext) -> None:
    ticket = await get_open_ticket(runtime.settings.db_path, callback.from_user.id)
    if ticket:
        await update_ticket_status(runtime.settings.db_path, int(ticket['id']), 'closed')
        if ticket.get('topic_id') and group_id():
            try:
                await callback.bot.send_message(
                    group_id(),
                    '✅ Пользователь закрыл обращение.',
                    message_thread_id=int(ticket['topic_id']),
                )
                await callback.bot.close_forum_topic(group_id(), int(ticket['topic_id']))
            except Exception:
                log.exception('Cannot close support topic')
    await state.clear()
    await callback.answer('Закрыто')
    await callback.message.answer('✅ Обращение закрыто.', reply_markup=help_menu())
