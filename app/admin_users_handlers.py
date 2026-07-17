from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import runtime
from app.admin_db import find_user
from app.keyboards import admin_menu, admin_user_menu
from app.remna_admin import fmt_bytes, remna_users, squads_text, traffic_limit, traffic_used
from app.remnawave import RemnawaveClient

router = Router()
PAGE_SIZE = 8
USER_CACHE: dict[str, dict[str, Any]] = {}


class UserSearchForm(StatesGroup):
    query = State()


def esc(value: Any) -> str:
    return str(value or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def remna_tg(row: dict[str, Any]) -> int | None:
    raw = row.get('telegramId') or row.get('telegram_id')
    try:
        if raw not in (None, ''):
            return int(raw)
    except Exception:
        pass
    for value in (row.get('email'), row.get('username')):
        match = re.search(r'tg(\d+)@', str(value or ''), flags=re.I)
        if match:
            return int(match.group(1))
    return None


def remna_uuid(row: dict[str, Any]) -> str:
    return str(row.get('uuid') or row.get('id') or '')


def remna_expire(row: dict[str, Any]) -> str:
    return str(row.get('expireAt') or row.get('expiresAt') or row.get('expire_at') or '')


def remna_name(row: dict[str, Any]) -> str:
    return str(row.get('username') or row.get('email') or remna_uuid(row) or 'Remnawave user')


async def local_users() -> list[dict[str, Any]]:
    query = '''
        SELECT u.*,
               (SELECT s.id FROM subscriptions s WHERE s.telegram_id=u.telegram_id ORDER BY s.id DESC LIMIT 1) AS subscription_id,
               (SELECT s.status FROM subscriptions s WHERE s.telegram_id=u.telegram_id ORDER BY s.id DESC LIMIT 1) AS subscription_status,
               (SELECT s.expires_at FROM subscriptions s WHERE s.telegram_id=u.telegram_id ORDER BY s.id DESC LIMIT 1) AS expires_at,
               (SELECT s.subscription_url FROM subscriptions s WHERE s.telegram_id=u.telegram_id ORDER BY s.id DESC LIMIT 1) AS subscription_url,
               (SELECT s.remnawave_user_id FROM subscriptions s WHERE s.telegram_id=u.telegram_id ORDER BY s.id DESC LIMIT 1) AS remnawave_user_id,
               (SELECT p.title FROM subscriptions s JOIN plans p ON p.id=s.plan_id WHERE s.telegram_id=u.telegram_id ORDER BY s.id DESC LIMIT 1) AS plan_title,
               (SELECT COUNT(*) FROM payments p WHERE p.telegram_id=u.telegram_id AND p.status IN ('paid','succeeded')) AS paid_count,
               (SELECT COALESCE(SUM(amount_rub),0) FROM payments p WHERE p.telegram_id=u.telegram_id AND p.status IN ('paid','succeeded')) AS paid_total
        FROM users u
        ORDER BY u.updated_at DESC
    '''
    async with aiosqlite.connect(runtime.settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def merged_users() -> list[dict[str, Any]]:
    locals_ = await local_users()
    try:
        remotes = await remna_users(RemnawaveClient(runtime.settings), limit=3000)
    except Exception:
        remotes = []

    by_tg = {remna_tg(row): row for row in remotes if remna_tg(row)}
    by_uuid = {remna_uuid(row): row for row in remotes if remna_uuid(row)}
    used_remote: set[str] = set()
    merged: list[dict[str, Any]] = []

    for local in locals_:
        tg = int(local['telegram_id'])
        remote = by_tg.get(tg)
        if not remote and local.get('remnawave_user_id'):
            remote = by_uuid.get(str(local['remnawave_user_id']))
        if remote:
            used_remote.add(remna_uuid(remote))
        merged.append({'local': local, 'remote': remote, 'telegram_id': tg, 'source': 'linked' if remote else 'bot'})

    for remote in remotes:
        uuid = remna_uuid(remote)
        if uuid and uuid in used_remote:
            continue
        tg = remna_tg(remote)
        if tg and any(item['telegram_id'] == tg for item in merged):
            continue
        merged.append({'local': None, 'remote': remote, 'telegram_id': tg, 'source': 'remna'})
    return merged


def effective_expire(item: dict[str, Any]) -> datetime | None:
    local = item.get('local') or {}
    remote = item.get('remote') or {}
    return parse_dt(local.get('expires_at')) or parse_dt(remna_expire(remote))


def effective_status(item: dict[str, Any]) -> str:
    local = item.get('local') or {}
    remote = item.get('remote') or {}
    local_status = str(local.get('subscription_status') or '').lower()
    remote_status = str(remote.get('status') or '').lower()
    if local_status == 'blocked' or remote_status in {'disabled', 'blocked', 'limited'}:
        return 'blocked'
    exp = effective_expire(item)
    if exp and exp <= datetime.now(timezone.utc):
        return 'expired'
    if local_status == 'active' or remote_status == 'active':
        return 'active'
    return 'none'


def filter_items(items: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    if mode == 'all':
        return items
    if mode == 'active':
        return [x for x in items if effective_status(x) == 'active']
    if mode == 'soon':
        return [x for x in items if (effective_expire(x) and now < effective_expire(x) <= now + timedelta(days=7))]
    if mode == 'expired':
        return [x for x in items if effective_status(x) == 'expired']
    if mode == 'blocked':
        return [x for x in items if effective_status(x) == 'blocked']
    if mode == 'bot':
        return [x for x in items if x['source'] == 'bot']
    if mode == 'remna':
        return [x for x in items if x['source'] == 'remna']
    if mode == 'linked':
        return [x for x in items if x['source'] == 'linked']
    if mode == 'unlinked':
        return [x for x in items if x['source'] != 'linked']
    if mode == 'recent':
        return sorted(items, key=lambda x: str((x.get('local') or {}).get('updated_at') or remna_expire(x.get('remote') or {})), reverse=True)[:50]
    return items


def item_title(item: dict[str, Any]) -> str:
    local = item.get('local') or {}
    remote = item.get('remote') or {}
    return str(local.get('full_name') or (f"@{local['username']}" if local.get('username') else '') or remna_name(remote) or item.get('telegram_id') or 'Пользователь')


def item_badge(item: dict[str, Any]) -> str:
    status = effective_status(item)
    return {'active': '🟢', 'expired': '🔴', 'blocked': '🚫', 'none': '⚪'}.get(status, '⚪')


def source_badge(item: dict[str, Any]) -> str:
    return {'linked': '🔗', 'bot': '🤖', 'remna': '🌍'}.get(item['source'], '•')


def cache_item(item: dict[str, Any], index: int) -> str:
    local = item.get('local') or {}
    remote = item.get('remote') or {}
    raw = str(local.get('telegram_id') or remna_uuid(remote) or index)
    key = ''.join(ch for ch in raw if ch.isalnum())[-20:] or str(index)
    USER_CACHE[key] = item
    return key


def filters_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📋 Все', callback_data='admin:ul:all:0'), InlineKeyboardButton(text='🔎 Поиск', callback_data='admin:usersearch')],
        [InlineKeyboardButton(text='🟢 Активные', callback_data='admin:ul:active:0'), InlineKeyboardButton(text='🟡 Истекают ≤7д', callback_data='admin:ul:soon:0')],
        [InlineKeyboardButton(text='🔴 Просроченные', callback_data='admin:ul:expired:0'), InlineKeyboardButton(text='🚫 Заблокированные', callback_data='admin:ul:blocked:0')],
        [InlineKeyboardButton(text='🤖 Только Bot', callback_data='admin:ul:bot:0'), InlineKeyboardButton(text='🌍 Только Remna', callback_data='admin:ul:remna:0')],
        [InlineKeyboardButton(text='🔗 Связанные', callback_data='admin:ul:linked:0'), InlineKeyboardButton(text='⚠️ Несвязанные', callback_data='admin:ul:unlinked:0')],
        [InlineKeyboardButton(text='🕘 Последние', callback_data='admin:ul:recent:0')],
        [InlineKeyboardButton(text='← Админка', callback_data='admin:home')],
    ])


def list_keyboard(items: list[dict[str, Any]], mode: str, page: int, total: int) -> InlineKeyboardMarkup:
    USER_CACHE.clear()
    rows = []
    for idx, item in enumerate(items):
        key = cache_item(item, idx)
        title = item_title(item)[:30]
        rows.append([InlineKeyboardButton(text=f'{item_badge(item)} {source_badge(item)} {title}', callback_data=f'admin:uc:{key}')])
    pages = max(1, math.ceil(total / PAGE_SIZE))
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text='←', callback_data=f'admin:ul:{mode}:{page-1}'))
    nav.append(InlineKeyboardButton(text=f'{page+1}/{pages}', callback_data='admin:users'))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text='→', callback_data=f'admin:ul:{mode}:{page+1}'))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text='⚙️ Фильтры', callback_data='admin:users')])
    rows.append([InlineKeyboardButton(text='← Админка', callback_data='admin:home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_page(callback: CallbackQuery, mode: str, page: int) -> None:
    items = filter_items(await merged_users(), mode)
    total = len(items)
    page = max(0, min(page, max(0, math.ceil(total / PAGE_SIZE) - 1)))
    chunk = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    lines = []
    for item in chunk:
        exp = effective_expire(item)
        exp_text = exp.strftime('%d.%m.%Y') if exp else 'без подписки'
        lines.append(f"{item_badge(item)} {source_badge(item)} <b>{esc(item_title(item))}</b> · {exp_text}")
    text = f"👥 <b>Пользователи</b>\nФильтр: <b>{esc(mode)}</b> · найдено: <b>{total}</b>\n\n" + ('\n'.join(lines) if lines else 'Ничего не найдено.')
    await callback.message.answer(text[:3900], reply_markup=list_keyboard(chunk, mode, page, total))


@router.callback_query(F.data == 'admin:users')
async def users_home(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer()
    items = await merged_users()
    linked = sum(1 for x in items if x['source'] == 'linked')
    bot_only = sum(1 for x in items if x['source'] == 'bot')
    remna_only = sum(1 for x in items if x['source'] == 'remna')
    text = (
        '👥 <b>Пользователи</b>\n\n'
        f'Всего объединённых записей: <b>{len(items)}</b>\n'
        f'🔗 Связанные: <b>{linked}</b>\n'
        f'🤖 Только Bot: <b>{bot_only}</b>\n'
        f'🌍 Только Remnawave: <b>{remna_only}</b>\n\n'
        'Выберите список или фильтр.'
    )
    await callback.message.answer(text, reply_markup=filters_keyboard())


@router.callback_query(F.data.startswith('admin:ul:'))
async def users_list(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    _, _, mode, page_raw = callback.data.split(':', 3)
    await callback.answer('Загружаю')
    await show_page(callback, mode, int(page_raw))


@router.callback_query(F.data == 'admin:usersearch')
async def users_search_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await state.set_state(UserSearchForm.query)
    await callback.answer()
    await callback.message.answer('🔎 Пришлите Telegram ID, @username, email, Remnawave UUID или часть имени.\n\n/cancel — отменить.')


@router.message(UserSearchForm.query)
async def users_search(message: Message, state: FSMContext) -> None:
    if not runtime.admin(message):
        return
    query = (message.text or '').strip().lstrip('@').lower()
    await state.clear()
    items = await merged_users()
    found = []
    for item in items:
        local = item.get('local') or {}
        remote = item.get('remote') or {}
        haystack = ' '.join([
            str(local.get('telegram_id') or ''), str(local.get('username') or ''), str(local.get('full_name') or ''),
            str(remote.get('email') or ''), str(remote.get('username') or ''), remna_uuid(remote),
        ]).lower()
        if query and query in haystack:
            found.append(item)
    if not found:
        await message.answer('Ничего не найдено.', reply_markup=filters_keyboard())
        return
    rows = []
    USER_CACHE.clear()
    for idx, item in enumerate(found[:20]):
        key = cache_item(item, idx)
        rows.append([InlineKeyboardButton(text=f'{item_badge(item)} {source_badge(item)} {item_title(item)[:32]}', callback_data=f'admin:uc:{key}')])
    rows.append([InlineKeyboardButton(text='← Пользователи', callback_data='admin:users')])
    await message.answer(f'🔎 Найдено: <b>{len(found)}</b>', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith('admin:uc:'))
async def user_card(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    key = callback.data.rsplit(':', 1)[1]
    item = USER_CACHE.get(key)
    if not item:
        await callback.answer('Список устарел, откройте его заново', show_alert=True)
        return
    await callback.answer()
    local = item.get('local') or {}
    remote = item.get('remote') or {}
    exp = effective_expire(item)
    status = effective_status(item)
    text = (
        f"👤 <b>{esc(item_title(item))}</b>\n\n"
        f"Статус: <b>{esc(status)}</b>\n"
        f"Источник: <b>{esc(item['source'])}</b>\n"
        f"До: <b>{exp.strftime('%d.%m.%Y %H:%M') if exp else 'нет'}</b>\n\n"
        '<b>Telegram / Bot</b>\n'
        f"ID: <code>{esc(local.get('telegram_id') or item.get('telegram_id') or '-')}</code>\n"
        f"Username: {('@' + esc(local.get('username'))) if local.get('username') else '-'}\n"
        f"Тариф: {esc(local.get('plan_title') or '-')}\n"
        f"Оплат: {esc(local.get('paid_count') or 0)} · {esc(local.get('paid_total') or 0)} ₽\n\n"
        '<b>Remnawave</b>\n'
        f"UUID: <code>{esc(remna_uuid(remote) or local.get('remnawave_user_id') or '-')}</code>\n"
        f"Status: {esc(remote.get('status') or '-')}\n"
        f"Squad: {esc(squads_text(remote))}\n"
        f"Трафик: {fmt_bytes(traffic_used(remote))} / {fmt_bytes(traffic_limit(remote))}"
    )
    tg = local.get('telegram_id') or item.get('telegram_id')
    if tg:
        markup = admin_user_menu(int(tg), status == 'active')
    else:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔄 Синхронизировать Remnawave → Bot', callback_data='admin:remna:sync')],
            [InlineKeyboardButton(text='← Пользователи', callback_data='admin:users')],
        ])
    await callback.message.answer(text[:3900], reply_markup=markup)
