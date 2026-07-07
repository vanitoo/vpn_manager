from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app import runtime
from app.keyboards import admin_menu, admin_remna_menu
from app.remna_admin import remna_nodes, remna_squads, remna_users, sync_remna_users_to_sqlite
from app.remnawave import RemnawaveClient
from app.system_info import get_system_info

router = Router()
log = logging.getLogger(__name__)


def esc(value: str) -> str:
    return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


@router.callback_query(F.data == 'servers')
async def user_servers(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer('Серверная информация доступна администратору.')


@router.callback_query(F.data == 'admin:remna')
async def admin_remna(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer()
    await callback.message.answer('🌍 <b>Remnawave</b>', reply_markup=admin_remna_menu())


@router.callback_query(F.data == 'admin:remna:nodes')
async def admin_nodes(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer('Запрос серверов')
    rows = await remna_nodes(RemnawaveClient(runtime.settings))
    lines = []
    for item in rows[:25]:
        name = item.get('name') or item.get('address') or item.get('uuid') or item.get('id') or 'node'
        status = item.get('status') or item.get('isConnected') or item.get('isOnline') or '-'
        users = item.get('usersOnline') or item.get('onlineUsers') or item.get('users') or '-'
        lines.append(f'• {esc(str(name))} · status={esc(str(status))} · users={esc(str(users))}')
    text = '🖥 <b>Серверы / Nodes</b>\n\n' + ('\n'.join(lines) if lines else 'Не найдены или endpoint вернул пусто.')
    await callback.message.answer(text[:3900], reply_markup=admin_remna_menu())


@router.callback_query(F.data == 'admin:remna:squads')
async def admin_squads(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer('Запрос squads')
    rows = await remna_squads(RemnawaveClient(runtime.settings))
    lines = []
    for item in rows[:30]:
        name = item.get('name') or item.get('title') or '-'
        uid = item.get('uuid') or item.get('id') or item.get('squadUuid') or '-'
        lines.append(f'• {esc(str(name))}\n  <code>{esc(str(uid))}</code>')
    text = '🧩 <b>Squads</b>\n\n' + ('\n'.join(lines) if lines else 'Не найдены.')
    await callback.message.answer(text[:3900], reply_markup=admin_remna_menu())


@router.callback_query(F.data == 'admin:remna:users')
async def admin_users(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer('Запрос пользователей')
    rows = await remna_users(RemnawaveClient(runtime.settings), limit=50)
    lines = []
    for item in rows[:20]:
        email = item.get('email') or item.get('username') or '-'
        tg = item.get('telegramId') or item.get('telegram_id') or '-'
        exp = item.get('expireAt') or item.get('expiresAt') or '-'
        lines.append(f'• {esc(str(email))} · tg={esc(str(tg))} · до={esc(str(exp))[:10]}')
    text = f'👥 <b>Пользователи Remnawave</b>\nВсего получено: {len(rows)}\n\n' + ('\n'.join(lines) if lines else 'Не найдены.')
    await callback.message.answer(text[:3900], reply_markup=admin_remna_menu())


@router.callback_query(F.data == 'admin:remna:sync')
async def admin_sync(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer('Синхронизация')
    rows = await remna_users(RemnawaveClient(runtime.settings), limit=1000)
    result = await sync_remna_users_to_sqlite(runtime.settings.db_path, rows)
    text = '🔄 <b>Синхронизация Remna → Bot</b>\n\n' + '\n'.join(f'{k}: {v}' for k, v in result.items())
    await callback.message.answer(text, reply_markup=admin_remna_menu())


@router.callback_query(F.data == 'admin:system')
async def admin_system(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer('Система')
    client = RemnawaveClient(runtime.settings)
    diag = await client.diagnostics()
    sysinfo = get_system_info(runtime.settings.db_path, runtime.settings.log_file)
    text = 'ℹ️ <b>Система</b>\n\n<b>Bot</b>\n<code>' + esc(sysinfo) + '</code>\n\n<b>Remnawave</b>\n<code>' + esc(diag) + '</code>'
    await callback.message.answer(text[:3900], reply_markup=admin_menu())
