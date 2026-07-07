from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app import runtime
from app.keyboards import admin_menu, admin_remna_menu
from app.remna_admin import fmt_bytes, remna_nodes, remna_squads, remna_stats, remna_users, squads_text, sync_remna_users_to_sqlite, traffic_limit, traffic_used
from app.remnawave import RemnawaveClient
from app.system_info import get_system_info

router = Router()
log = logging.getLogger(__name__)


def esc(value: str) -> str:
    return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def user_uuid(item: dict) -> str:
    return str(item.get('uuid') or item.get('id') or '')


def user_title(item: dict) -> str:
    return str(item.get('email') or item.get('username') or user_uuid(item)[:8] or 'user')


def remna_user_list_menu(users: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for item in users[:20]:
        uid = user_uuid(item)
        if not uid:
            continue
        title = user_title(item)
        status = '🟢' if str(item.get('status') or '').upper() == 'ACTIVE' else '⚪'
        rows.append([InlineKeyboardButton(text=f'{status} {title[:34]}', callback_data=f'admin:remna:user:{uid}')])
    rows.append([InlineKeyboardButton(text='🔄 Синхронизировать', callback_data='admin:remna:sync')])
    rows.append([InlineKeyboardButton(text='← Remnawave', callback_data='admin:remna')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def remna_user_menu(uid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🚫 Заблокировать', callback_data=f'admin:remna:block:{uid}'), InlineKeyboardButton(text='✅ Активировать', callback_data=f'admin:remna:activate:{uid}')],
        [InlineKeyboardButton(text='🧩 Сменить squad', callback_data=f'admin:remna:change_squad:{uid}')],
        [InlineKeyboardButton(text='💼 Назначить админ-тариф', callback_data=f'admin:remna:assign_plan:{uid}')],
        [InlineKeyboardButton(text='← Пользователи Remna', callback_data='admin:remna:users')],
    ])


async def get_remna_user_by_uuid(uid: str) -> dict | None:
    rows = await remna_users(RemnawaveClient(runtime.settings), limit=1000)
    for item in rows:
        if user_uuid(item) == uid:
            return item
    return None


async def patch_user_status(uid: str, status: str) -> None:
    client = RemnawaveClient(runtime.settings)
    await client._request('PATCH', '/api/users', json_payload={'uuid': uid, 'status': status}, expected_status=(200, 201))


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
    rows = await remna_users(RemnawaveClient(runtime.settings), limit=200)
    st = remna_stats(rows)
    text = (
        '👥 <b>Пользователи Remnawave</b>\n\n'
        f"Всего: <b>{st['total']}</b>\n"
        f"Активных: <b>{st['active']}</b>\n"
        f"Без Telegram ID: <b>{st['total'] - st['with_tg']}</b>\n"
        f"Трафик: <b>{fmt_bytes(st['traffic_used'])}</b> / {fmt_bytes(st['traffic_limit'])}\n\n"
        'Нажмите пользователя для карточки:'
    )
    await callback.message.answer(text[:3900], reply_markup=remna_user_list_menu(rows))


@router.callback_query(F.data.startswith('admin:remna:user:'))
async def admin_user_card(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    uid = callback.data.split(':', 3)[3]
    await callback.answer('Карточка')
    item = await get_remna_user_by_uuid(uid)
    if not item:
        await callback.message.answer('Пользователь не найден в Remnawave.', reply_markup=admin_remna_menu())
        return
    text = (
        f"👤 <b>{esc(user_title(item))}</b>\n\n"
        f"UUID: <code>{esc(user_uuid(item))}</code>\n"
        f"Telegram ID: <code>{esc(str(item.get('telegramId') or item.get('telegram_id') or '-'))}</code>\n"
        f"Статус: <b>{esc(str(item.get('status') or '-'))}</b>\n"
        f"До: <b>{esc(str(item.get('expireAt') or item.get('expiresAt') or '-'))[:19]}</b>\n"
        f"Squads: <b>{esc(squads_text(item))}</b>\n"
        f"Трафик: <b>{fmt_bytes(traffic_used(item))}</b> / {fmt_bytes(traffic_limit(item))}\n"
        f"Подписка:\n<code>{esc(str(item.get('subscriptionUrl') or item.get('subUrl') or '-'))}</code>"
    )
    await callback.message.answer(text[:3900], reply_markup=remna_user_menu(uid))


@router.callback_query(F.data.startswith('admin:remna:block:'))
async def admin_block_user(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    uid = callback.data.split(':', 3)[3]
    await callback.answer('Блокирую')
    try:
        await patch_user_status(uid, 'DISABLED')
        await callback.message.answer('🚫 Пользователь заблокирован.', reply_markup=remna_user_menu(uid))
    except Exception as exc:
        await callback.message.answer(f'Ошибка блокировки: <code>{esc(str(exc))[:1000]}</code>')


@router.callback_query(F.data.startswith('admin:remna:activate:'))
async def admin_activate_user(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    uid = callback.data.split(':', 3)[3]
    await callback.answer('Активирую')
    try:
        await patch_user_status(uid, 'ACTIVE')
        await callback.message.answer('✅ Пользователь активирован.', reply_markup=remna_user_menu(uid))
    except Exception as exc:
        await callback.message.answer(f'Ошибка активации: <code>{esc(str(exc))[:1000]}</code>')


@router.callback_query(F.data.startswith('admin:remna:change_squad:'))
async def admin_change_squad_hint(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer('🧩 Смена squad будет следующим шагом: нужно выбрать squad из списка и отправить PATCH activeInternalSquads. Кнопку-заготовку оставил, чтобы не прятать эту механику в шкаф.')


@router.callback_query(F.data.startswith('admin:remna:assign_plan:'))
async def admin_assign_plan_hint(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer('💼 Назначение админ-тарифа будет следующим шагом: тарифы уже разделены на публичные и служебные, осталось привязать выбранный план к PATCH expireAt/traffic.')


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
