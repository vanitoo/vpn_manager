from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app import runtime
from app.support.common import help_menu, message_type
from app.support.settings import group_id
from app.support.storage import add_support_message, get_ticket_by_topic, support_stats, update_ticket_status

router = Router()
log = logging.getLogger(__name__)


@router.message(Command('chatid'), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def show_chat_id(message: Message) -> None:
    await message.reply(f'ID этой группы: <code>{message.chat.id}</code>')


@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.message_thread_id)
async def topic_reply(message: Message) -> None:
    gid = group_id()
    if not gid or message.chat.id != gid or not message.from_user or message.from_user.is_bot:
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
            await message.bot.send_message(
                int(ticket['telegram_id']),
                f"✅ Обращение #{ticket['id']} закрыто специалистом.",
                reply_markup=help_menu(),
            )
        except Exception:
            log.exception('Cannot notify support user')
        await message.reply('✅ Обращение закрыто.')
        try:
            await message.bot.close_forum_topic(message.chat.id, int(message.message_thread_id))
        except Exception:
            log.exception('Cannot close support topic')
        return

    try:
        copied = await message.copy_to(int(ticket['telegram_id']))
        kind = message_type(message)
        await add_support_message(
            runtime.settings.db_path,
            ticket_id=int(ticket['id']),
            direction='support_to_user',
            sender_telegram_id=message.from_user.id,
            user_message_id=copied.message_id,
            support_message_id=message.message_id,
            message_type=kind,
            text_preview=message.text or message.caption or f'[{kind}]',
        )
        await update_ticket_status(runtime.settings.db_path, int(ticket['id']), 'in_progress', message.from_user.id)
    except Exception as exc:
        log.exception('Cannot deliver support answer')
        await message.reply(f'❌ Не удалось доставить ответ: <code>{str(exc)[:700]}</code>')


@router.callback_query(F.data == 'admin:support')
async def admin_support(callback: CallbackQuery) -> None:
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
        f"Группа настроена: <b>{'да' if group_id() else 'нет'}</b>\n"
        'Ответы отправляются обычными сообщениями внутри темы пользователя.'
    )
