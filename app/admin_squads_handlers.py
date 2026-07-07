from __future__ import annotations

from collections import defaultdict
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app import runtime
from app.keyboards import admin_menu, admin_squads_menu
from app.remna_admin import fmt_bytes, remna_squads, remna_users, traffic_limit, traffic_used
from app.remnawave import RemnawaveClient

router = Router()
SQUAD_KEYS: dict[str, str] = {}


def esc(value: str) -> str:
    return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def squad_id(item: dict[str, Any]) -> str:
    return str(item.get('uuid') or item.get('id') or item.get('squadUuid') or '')


def squad_name(item: dict[str, Any]) -> str:
    return str(item.get('name') or item.get('title') or squad_id(item)[:8] or 'squad')


def user_squad_ids(user: dict[str, Any]) -> list[str]:
    squads = user.get('activeInternalSquads') or user.get('internalSquads') or user.get('squads') or []
    out = []
    for item in squads:
        if isinstance(item, dict):
            sid = str(item.get('uuid') or item.get('id') or item.get('squadUuid') or '')
        else:
            sid = str(item)
        if sid:
            out.append(sid)
    return out


def stats_by_squad(users: list[dict[str, Any]], squads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    names = {squad_id(s): squad_name(s) for s in squads if squad_id(s)}
    data: dict[str, dict[str, Any]] = defaultdict(lambda: {'name': 'unknown', 'users': 0, 'active': 0, 'traffic_used': 0, 'traffic_limit': 0})
    for user in users:
        ids = user_squad_ids(user) or ['no-squad']
        for sid in ids:
            data[sid]['name'] = names.get(sid, sid[:8] if sid != 'no-squad' else 'Без squad')
            data[sid]['users'] += 1
            if str(user.get('status') or '').upper() == 'ACTIVE':
                data[sid]['active'] += 1
            data[sid]['traffic_used'] += traffic_used(user)
            data[sid]['traffic_limit'] += traffic_limit(user)
    return dict(data)


def squads_list_menu(stats: dict[str, dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    SQUAD_KEYS.clear()
    for idx, (sid, data) in enumerate(sorted(stats.items(), key=lambda x: x[1]['users'], reverse=True)[:40]):
        key = f's{idx}'
        SQUAD_KEYS[key] = sid
        rows.append([InlineKeyboardButton(text=f"{data['name'][:28]} · {data['users']} users", callback_data=f'admin:squad:{key}')])
    rows.append([InlineKeyboardButton(text='← Squads', callback_data='admin:squads')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == 'admin:squads')
async def squads_home(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer()
    await callback.message.answer('🧩 <b>Squads</b>\n\nГруппы доступа Remnawave: статистика, пользователи и нагрузка.', reply_markup=admin_squads_menu())


@router.callback_query(F.data == 'admin:squads:stats')
async def squads_stats(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer('Считаю')
    client = RemnawaveClient(runtime.settings)
    squads = await remna_squads(client)
    users = await remna_users(client, limit=1000)
    stats = stats_by_squad(users, squads)
    total_used = sum(v['traffic_used'] for v in stats.values())
    text = (
        '📊 <b>Статистика Squads</b>\n\n'
        f"Squads: <b>{len(squads)}</b>\n"
        f"Пользователей с распределением: <b>{sum(v['users'] for v in stats.values())}</b>\n"
        f"Трафик: <b>{fmt_bytes(total_used)}</b>\n\n"
    )
    lines = []
    for sid, data in sorted(stats.items(), key=lambda x: x[1]['users'], reverse=True)[:15]:
        lines.append(f"• <b>{esc(data['name'])}</b>: {data['users']} users, active {data['active']}, traffic {fmt_bytes(data['traffic_used'])}")
    await callback.message.answer((text + '\n'.join(lines))[:3900], reply_markup=admin_squads_menu())


@router.callback_query(F.data == 'admin:squads:list')
async def squads_list(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer('Список')
    client = RemnawaveClient(runtime.settings)
    squads = await remna_squads(client)
    users = await remna_users(client, limit=1000)
    stats = stats_by_squad(users, squads)
    await callback.message.answer('📋 <b>Squads</b>\n\nНажмите squad для карточки:', reply_markup=squads_list_menu(stats))


@router.callback_query(F.data.startswith('admin:squad:'))
async def squad_card(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    key = callback.data.split(':')[-1]
    sid = SQUAD_KEYS.get(key)
    if not sid:
        await callback.answer('Обновите список', show_alert=True)
        return
    await callback.answer('Карточка')
    client = RemnawaveClient(runtime.settings)
    users = await remna_users(client, limit=1000)
    squad_users = [u for u in users if sid in user_squad_ids(u) or (sid == 'no-squad' and not user_squad_ids(u))]
    used = sum(traffic_used(u) for u in squad_users)
    limit = sum(traffic_limit(u) for u in squad_users)
    active = sum(1 for u in squad_users if str(u.get('status') or '').upper() == 'ACTIVE')
    lines = []
    for user in squad_users[:20]:
        title = user.get('email') or user.get('username') or user.get('uuid') or '-'
        exp = user.get('expireAt') or user.get('expiresAt') or '-'
        lines.append(f"• {esc(str(title))} · {esc(str(exp))[:10]} · {fmt_bytes(traffic_used(user))}")
    text = (
        f"🧩 <b>{esc(sid)}</b>\n\n"
        f"Пользователей: <b>{len(squad_users)}</b>\n"
        f"Активных: <b>{active}</b>\n"
        f"Трафик: <b>{fmt_bytes(used)}</b> / {fmt_bytes(limit)}\n\n"
        '<b>Пользователи:</b>\n' + ('\n'.join(lines) if lines else 'пусто')
    )
    await callback.message.answer(text[:3900], reply_markup=admin_squads_menu())
