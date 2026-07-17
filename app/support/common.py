from __future__ import annotations

from typing import Any

import aiosqlite
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import runtime
from app.support.diagnostics import admin_user_card, collect_diagnostics
from app.support.settings import enabled, group_id
from app.support.storage import add_support_message, set_ticket_topic

CATEGORIES = {
    'connect': 'Не подключается',
    'payment': 'Проблема с оплатой',
    'link': 'Потерял ссылку',
    'speed': 'Медленно работает',
    'other': 'Другое',
}


def help_menu(has_ticket: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text='📖 FAQ', callback_data='faq')],
        [InlineKeyboardButton(text='🩺 Диагностика', callback_data='support:diag')],
        [InlineKeyboardButton(text='💬 Написать специалисту', callback_data='support:new')],
    ]
    if has_ticket:
        rows.insert(0, [InlineKeyboardButton(text='🎫 Продолжить обращение', callback_data='support:continue')])
    rows.append([InlineKeyboardButton(text='⌂ Главное', callback_data='home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def category_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔌 Не подключается', callback_data='support:cat:connect')],
        [InlineKeyboardButton(text='💳 Проблема с оплатой', callback_data='support:cat:payment')],
        [InlineKeyboardButton(text='🔑 Потерял ссылку', callback_data='support:cat:link')],
        [InlineKeyboardButton(text='🐢 Медленно работает', callback_data='support:cat:speed')],
        [InlineKeyboardButton(text='💬 Другое', callback_data='support:cat:other')],
        [InlineKeyboardButton(text='← Помощь', callback_data='help')],
    ])


def result_menu(category: str = 'other') -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Проблема решена', callback_data='support:solved')],
        [InlineKeyboardButton(text='💬 Написать специалисту', callback_data=f'support:contact:{category}')],
        [InlineKeyboardButton(text='← Помощь', callback_data='help')],
    ])


def chat_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Закрыть обращение', callback_data='support:close')],
        [InlineKeyboardButton(text='← Помощь', callback_data='help')],
    ])


def message_type(message: Message) -> str:
    for name in ('text', 'photo', 'video', 'document', 'voice', 'audio', 'animation', 'sticker'):
        if getattr(message, name, None):
            return name
    return 'other'


async def local_user(telegram_id: int) -> dict[str, Any]:
    async with aiosqlite.connect(runtime.settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM users WHERE telegram_id=? LIMIT 1', (telegram_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else {'telegram_id': telegram_id}


async def ensure_topic(message: Message, ticket: dict[str, Any]) -> dict[str, Any]:
    if ticket.get('topic_id'):
        return ticket
    gid = group_id()
    if not enabled() or not gid:
        raise RuntimeError('SUPPORT_GROUP_ID не настроен')
    user = await local_user(int(ticket['telegram_id']))
    display = user.get('full_name') or user.get('username') or str(ticket['telegram_id'])
    topic = await message.bot.create_forum_topic(gid, name=f"#{ticket['id']} {display}"[:128])
    topic_id = int(topic.message_thread_id)
    await set_ticket_topic(runtime.settings.db_path, int(ticket['id']), topic_id)
    ticket = {**ticket, 'topic_id': topic_id}
    diagnostics = await collect_diagnostics(int(ticket['telegram_id']))
    await message.bot.send_message(gid, admin_user_card(user, ticket, diagnostics), message_thread_id=topic_id)
    return ticket


async def forward_user_message(message: Message, ticket: dict[str, Any]) -> None:
    ticket = await ensure_topic(message, ticket)
    copied = await message.copy_to(group_id(), message_thread_id=int(ticket['topic_id']))
    kind = message_type(message)
    await add_support_message(
        runtime.settings.db_path,
        ticket_id=int(ticket['id']),
        direction='user_to_support',
        sender_telegram_id=message.from_user.id if message.from_user else None,
        user_message_id=message.message_id,
        support_message_id=copied.message_id,
        message_type=kind,
        text_preview=message.text or message.caption or f'[{kind}]',
    )
