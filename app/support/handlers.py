from __future__ import annotations

import logging
from typing import Any

import aiosqlite
from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import runtime
from app.db import upsert_user
from app.support.diagnostics import admin_user_card, collect_diagnostics, user_diagnostic_text
from app.support.storage import (
    add_support_message,
    create_ticket,
    get_open_ticket,
    get_ticket_by_topic,
    set_ticket_topic,
    support_stats,
    update_ticket_status,
)

router = Router()
log = logging.getLogger(__name__)

CATEGORY_LABELS = {
    'connect': 'Не подключается',
    'payment': 'Проблема с оплатой',
    'link': 'Потерял ссылку',
    'speed': 'Медленно работает',
    'other': 'Другое',
}


class SupportState(StatesGroup):
    waiting_first_message = State()
    chatting = State()


def help_menu(has_ticket: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text='📖 FAQ', callback_data='faq')],
        [InlineKeyboardButton(text='🩺 Диагностика', callback_data='support:diagnostics')],
        [InlineKeyboardButton(text='💬 Написать специалисту', callback_data='support:new')],
    ]
    if has_ticket:
        rows.insert(0, [InlineKeyboardButton(text='🎫 Продолжить обращение', callback_data='support:continue')])
    rows.append([InlineKeyboardButton(text='⌂ Главное', callback_data='home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def categories_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔌 Не подключается', callback_data='support:category:connect')],
        [InlineKeyboardButton(text='💳 Проблема с оплатой', callback_data='support:category:payment')],
        [InlineKeyboardButton(text='🔑 Потерял ссылку', callback_data='support:category:link')],
        [InlineKeyboardButton(text='🐢 Медленно работает', callback_data='support:category:speed')],
        [InlineKeyboardButton(text='💬 Другое', callback_data='support:category:other')],
        [InlineKeyboardButton(text='← Помощь', callback_data='help')],
    ])


def diagnostic_result_menu(category: str = 'other') -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Проблема решена', callback_data='support:solved')],
        [InlineKeyboardButton(text='💬 Написать специалисту', callback_data=f'support:contact:{category}')],
        [InlineKeyboardButton(text='← Помощь', callback_data='help')],
    ])


def active_chat_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Закрыть обращение', callback_data='support:user_close')],
        [InlineKeyboardButton(text='← Помощь', callback_data='help')],
    ])


def _message_type(message: Message) -> str:
    for name in ('text', 'photo', 'video', 'document', 'voice', 'audio', 'animation', 'sticker'):
        if getattr(message, name, None):
            return name
    return 'other'


def _preview(message: Message) -> str:
    return message.text or message.caption or f'[{_message_type(message)}]'


async def _local_user(telegram_id: int) -> dict[str, Any]:
    async with aiosqlite.connect(runtime.settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM users WHERE telegram_id=? LIMIT 1', (telegram_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else {'telegram_id': telegram_id}


async def _ensure_topic(message: Message, ticket: dict[str, Any]) -> dict[str, Any]:
    if ticket.get('topic_id'):
        return ticket
    group_id = runtime.settings.support_group_id
    if not group_id:
        raise RuntimeError('SUPPORT_GROUP_ID не настроен')
    user = await _local_user(int(ticket['telegram_id']))
    display = user.get('full_name') or user.get('username') or str(ticket['telegram_id'])
    title = f"#{ticket['id']} {display}"[:128]
    topic = await message.bot.create_forum_topic(chat_id=group_id, name=title)
    topic_id = int(topic.message_thread_id)
    await set_ticket_topic(runtime.settings.db_path, int(ticket['id']), topic_id)
    ticket = {**ticket, 'topic_id': topic_id}
    diagnostics = await collect_diagnostics(int(ticket['telegram_id']))
    await message.bot.send_message(
        chat_id=group_id,
        message_thread_id=topic_id,
        text=admin_user_card(user, ticket, diagnostics),
    )
    return ticket


async def _forward_user_message(message: Message, ticket: dict[str, Any]) -> None:
    ticket = await _ensure_topic(message, ticket)
    copied = await message.copy_to(
        chat_id=runtime.settings.support_group_id,
        message_thread_id=int(ticket['topic_id']),
    )
    await add_support_message(
        runtime.settings.db_path,
        ticket_id=int(ticket['id']),
        direction='user_to_support',
        sender_telegram_id=message.from_user.id if message.from_user else None,
        user_message_id=message.message_id,
        support_message_id=copied.message_id,
        message_type=_message_type(message),
        text_preview=_preview(message),
    )


@router.callback_query(F.data == 'help')
async def support_home(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    ticket = await get_open_ticket(runtime.settings.db_path, callback.from_user.id)
    await callback.message.answer(
        '❓ <b>Помощь</b>\n\nСначала попробуйте FAQ или автоматическую диагностику. Если проблема останется, создайте обращение специалисту.',
        reply_markup=help_menu(bool(ticket)),
    )


@router.callback_query(F.data == 'support:diagnostics')
async def diagnostics(callback: CallbackQuery) -> None:
    await callback.answer('Проверяю')
    data = await collect_diagnostics(callback.from_user.id)
    await callback.message.answer(user_diagnostic_text(data), reply_markup=diagnostic_result_menu())


@router.callback_query(F.data == 'support:new')
async def support_new(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer('Что случилось?', reply_markup=categories_menu())


@router.callback_query(F.data.startswith('support:category:'))
async def support_category(callback: CallbackQuery, state: FSMContext) -> None:
    category = callback.data.rsplit(':', 1)[1]
    await state.update_data(support_category=category)
    await callback.answer('Запускаю диагностику')
    data = await collect_diagnostics(callback.from_user.id)
    await callback.message.answer(
        f"Тема: <b>{CATEGORY_LABELS.get(category, 'Другое')}</b>\n\n{user_diagnostic_text(data)}",
        reply_markup=diagnostic_result_menu(category),
    )


@router.callback_query(F.data == 'support:solved')
async def support_solved(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer('Отлично')
    await callback.message.answer('✅ Хорошо, обращение создавать не будем.', reply_markup=help_menu())


@router.callback_query(F.data.startswith('support:contact:'))
async def support_contact(callback: CallbackQuery, state: FSMContext) -> None:
    category = callback.data.rsplit(':', 1)[1]
    await state.update_data(support_category=category)
    await state.set_state(SupportState.waiting_first_message)
    await callback.answer()
    await callback.message.answer(
        'Опишите проблему одним сообщением. Можно приложить фото, видео, голосовое сообщение или файл.\n\nДля отмены: /cancel'
    )


@router.callback_query(F.data == 'support:continue')
async def support_continue(callback: CallbackQuery, state: FSMContext) -> None:
    ticket = await get_open_ticket(runtime.settings.db_path, callback.from_user.id)
    if not ticket:
        await callback.answer('Открытого обращения нет', show_alert=True)
        return
    await state.set_state(SupportState.chatting)
    await callback.answer()
    await callback.message.answer(
        f"🎫 Обращение <b>#{ticket['id']}</b> открыто. Отправьте сообщение специалисту.",
        reply_markup=active_chat_menu(),
    )


@router.message(SupportState.waiting_first_message)
async def support_first_message(message: Message, state: FSMContext) -> None:
    if not message.from_user or message.chat.type != ChatType.PRIVATE:
        return
    if not runtime.settings.support_group_id:
        await message.answer('Поддержка пока не настроена: администратору нужно указать SUPPORT_GROUP_ID.')
        return
    data = await state.get_data()
    category = str(data.get('support_category') or 'other')
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
            category=category,
        )
    try:
        await _forward_user_message(message, ticket)
    except Exception as exc:
        log.exception('Cannot create support topic or forward user message')
        await message.answer(f'Не удалось передать сообщение специалисту: <code>{str(exc)[:700]}</code>')
        return
    await state.set_state(SupportState.chatting)
    await message.answer(
        f"✅ Обращение <b>#{ticket['id']}</b> создано. Ответ специалиста придёт сюда.",
        reply_markup=active_chat_menu(),
    )


@router.message(SupportState.chatting)
async def support_chat_message(message: Message) -> None:
    if not message.from_user or message.chat.type != ChatType.PRIVATE:
        return
    ticket = await get_open_ticket(runtime.settings.db_path, message.from_user.id)
    if not ticket:
        await message.answer('Открытое обращение не найдено.', reply_markup=help_menu())
        return
    try:
        await _forward_user_message(message, ticket)
        await message.answer('📨 Сообщение передано специалисту.', reply_markup=active_chat_menu())
    except Exception as exc:
        log.exception('Cannot forward follow-up support message')
        await message.answer(f'Не удалось передать сообщение: <code>{str(exc)[:700]}</code>')


@router.callback_query(F.data == 'support:user_close')
async def support_user_close(callback: CallbackQuery, state: FSMContext) -> None:
    ticket = await get_open_ticket(runtime.settings.db_path, callback.from_user.id)
    if ticket:
        await update_ticket_status(runtime.settings.db_path, int(ticket['id']), 'closed')
        if ticket.get('topic_id') and runtime.settings.support_group_id:
            try:
                await callback.bot.send_message(
                    runtime.settings.support_group_id,
                    '✅ Пользователь закрыл обращение.',
                    message_thread_id=int(ticket['topic_id']),
                )
                await callback.bot.close_forum_topic(runtime.settings.support_group_id, int(ticket['topic_id']))
            except Exception:
                log.exception('Cannot close support topic')
    await state.clear()
    await callback.answer('Закрыто')
    await callback.message.answer('✅ Обращение закрыто.', reply_markup=help_menu())


@router.message(F.chat.id == lambda: runtime.settings.support_group_id)
async def support_group_message(message: Message) -> None:
    if not runtime.settings.support_group_id or message.chat.id != runtime.settings.support_group_id:
        return
    if not message.message_thread_id or not message.from_user or message.from_user.is_bot:
        return
    ticket = await get_ticket_by_topic(runtime.settings.db_path, int(message.message_thread_id))
    if not ticket:
        return
    command = (message.text or '').strip().lower()
    if command.startswith('/take'):
        await update_ticket_status(runtime.settings.db_path, int(ticket['id']), 'in_progress', message.from_user.id)
        await message.reply('🔵 Обращение взято в работу.')
        return
    if command.startswith('/wait'):
        await update_ticket_status(runtime.settings.db_path, int(ticket['id']), 'waiting_user', message.from_user.id)
        await message.reply('🟣 Статус: ждём пользователя.')
        return
    if command.startswith('/close'):
        await update_ticket_status(runtime.settings.db_path, int(ticket['id']), 'closed', message.from_user.id)
        try:
            await message.bot.send_message(int(ticket['telegram_id']), f"✅ Обращение #{ticket['id']} закрыто специалистом.", reply_markup=help_menu())
        except Exception:
            log.exception('Cannot notify user about closed ticket')
        await message.reply('✅ Обращение закрыто.')
        try:
            await message.bot.close_forum_topic(message.chat.id, int(message.message_thread_id))
        except Exception:
            log.exception('Cannot close forum topic')
        return
    try:
        copied = await message.copy_to(int(ticket['telegram_id']))
        await add_support_message(
            runtime.settings.db_path,
            ticket_id=int(ticket['id']),
            direction='support_to_user',
            sender_telegram_id=message.from_user.id,
            user_message_id=copied.message_id,
            support_message_id=message.message_id,
            message_type=_message_type(message),
            text_preview=_preview(message),
        )
        await update_ticket_status(runtime.settings.db_path, int(ticket['id']), 'in_progress', message.from_user.id)
    except Exception as exc:
        log.exception('Cannot deliver support reply to user')
        await message.reply(f'❌ Не удалось доставить ответ: <code>{str(exc)[:700]}</code>')


@router.callback_query(F.data == 'admin:support')
async def admin_support_stats(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    stats = await support_stats(runtime.settings.db_path)
    await callback.answer()
    await callback.message.answer(
        '🛟 <b>Поддержка</b>\n\n'
        f"🟡 Новые: <b>{stats['new']}</b>\n"
        f"🔵 В работе: <b>{stats['in_progress']}</b>\n"
        f"🟣 Ждут пользователя: <b>{stats['waiting_user']}</b>\n"
        f"⚫ Закрытые: <b>{stats['closed']}</b>\n\n"
        'Переписка ведётся в Telegram-группе с темами. Каждая тема соответствует одному обращению.'
    )
