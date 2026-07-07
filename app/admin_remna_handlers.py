from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app import runtime
from app.keyboards import admin_menu, admin_remna_menu
from app.remna_admin import fmt_bytes, list_admin_plans, remna_nodes, remna_squads, remna_stats, remna_users, squads_text, sync_remna_users_to_sqlite, traffic_limit, traffic_used
from app.remnawave import RemnawaveClient
from app.system_info import get_system_info

router = Router()
log = logging.getLogger(__name__)
PENDING_ACTIONS: dict[str, tuple[str, str]] = {}


def esc(value: str) -> str:
    return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def short_key(value1: str, value2: str) -> str:
    key = uuid.uuid4().hex[:10]
    PENDING_ACTIONS[key] = (value1, value2)
    return key


def user_uuid(item: dict) -> str:
    return str(item.get('uuid') or item.get('id') or '')


def user_title(item: dict) -> str:
    return str(item.get('email') or item.get('username') or user_uuid(item)[:8] or 'user')


def parse_expire(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def node_value(item: dict, *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, ''):
            return str(value)
    return '-'


def node_line(item: dict) -> str:
    name = node_value(item, 'name', 'address', 'uuid', 'id')
    address = node_value(item, 'address', 'host', 'ip')
    status = node_value(item, 'status', 'isConnected', 'isOnline')
    users = node_value(item, 'usersOnline', 'onlineUsers', 'users', 'usersCount')
    cpu = node_value(item, 'cpu', 'cpuLoad', 'cpuUsage', 'cpuPercent')
    ram = node_value(item, 'ram', 'memory', 'memoryUsage', 'mem')
    traffic = node_value(item, 'traffic', 'trafficUsed', 'usedTrafficBytes')
    dot = '🟢' if str(status).lower() in {'true', 'online', 'active', 'connected', 'running'} else '🟡'
    return f'{dot} <b>{esc(name)}</b>\n  {esc(address)}\n  status={esc(status)} · users={esc(users)} · cpu={esc(cpu)} · ram={esc(ram)} · traffic={esc(traffic)}'


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


def squad_select_menu(uid: str, squads: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for squad in squads[:25]:
        squad_id = str(squad.get('uuid') or squad.get('id') or squad.get('squadUuid') or '')
        if not squad_id:
            continue
        name = str(squad.get('name') or squad.get('title') or squad_id[:8])
        key = short_key(uid, squad_id)
        rows.append([InlineKeyboardButton(text=name[:40], callback_data=f'admin:remna:set_squad:{key}')])
    rows.append([InlineKeyboardButton(text='← Карточка', callback_data=f'admin:remna:user:{uid}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plan_select_menu(uid: str, plans: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for plan in plans[:25]:
        key = short_key(uid, str(plan['id']))
        rows.append([InlineKeyboardButton(text=f"{plan['title']} · {plan['duration_days']} дн. · {plan['price_rub']} ₽", callback_data=f'admin:remna:set_plan:{key}')])
    rows.append([InlineKeyboardButton(text='← Карточка', callback_data=f'admin:remna:user:{uid}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def get_remna_user_by_uuid(uid: str) -> dict | None:
    rows = await remna_users(RemnawaveClient(runtime.settings), limit=1000)
    for item in rows:
        if user_uuid(item) == uid:
            return item
    return None


async def patch_user_status(uid: str, status: str) -> None:
    await RemnawaveClient(runtime.settings)._request('PATCH', '/api/users', json_payload={'uuid': uid, 'status': status}, expected_status=(200, 201))


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
    text = '🖥 <b>Серверы / Nodes</b>\n\n'
    text += '\n\n'.join(node_line(item) for item in rows[:15]) if rows else 'Не найдены или endpoint вернул пусто.'
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
    text = ('👥 <b>Пользователи Remnawave</b>\n\n'
            f"Всего: <b>{st['total']}</b>\n"
            f"Активных: <b>{st['active']}</b>\n"
            f"Без Telegram ID: <b>{st['total'] - st['with_tg']}</b>\n"
            f"Трафик: <b>{fmt_bytes(st['traffic_used'])}</b> / {fmt_bytes(st['traffic_limit'])}\n\n"
            'Нажмите пользователя для карточки:')
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
    text = (f"👤 <b>{esc(user_title(item))}</b>\n\n"
            f"UUID: <code>{esc(user_uuid(item))}</code>\n"
            f"Telegram ID: <code>{esc(str(item.get('telegramId') or item.get('telegram_id') or '-'))}</code>\n"
            f"Статус: <b>{esc(str(item.get('status') or '-'))}</b>\n"
            f"До: <b>{esc(str(item.get('expireAt') or item.get('expiresAt') or '-'))[:19]}</b>\n"
            f"Squads: <b>{esc(squads_text(item))}</b>\n"
            f"Трафик: <b>{fmt_bytes(traffic_used(item))}</b> / {fmt_bytes(traffic_limit(item))}\n"
            f"Подписка:\n<code>{esc(str(item.get('subscriptionUrl') or item.get('subUrl') or '-'))}</code>")
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
async def admin_change_squad(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    uid = callback.data.split(':', 3)[3]
    await callback.answer('Выбор squad')
    squads = await remna_squads(RemnawaveClient(runtime.settings))
    await callback.message.answer('🧩 <b>Выберите squad</b>', reply_markup=squad_select_menu(uid, squads))


@router.callback_query(F.data.startswith('admin:remna:set_squad:'))
async def admin_set_squad(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    key = callback.data.split(':', 3)[3]
    uid, squad_id = PENDING_ACTIONS.pop(key, ('', ''))
    if not uid or not squad_id:
        await callback.answer('Действие устарело', show_alert=True)
        return
    await callback.answer('Меняю squad')
    try:
        await RemnawaveClient(runtime.settings)._request('PATCH', '/api/users', json_payload={'uuid': uid, 'activeInternalSquads': [squad_id], 'status': 'ACTIVE'}, expected_status=(200, 201))
        await callback.message.answer(f'✅ Squad изменён.\n<code>{esc(squad_id)}</code>', reply_markup=remna_user_menu(uid))
    except Exception as exc:
        await callback.message.answer(f'Ошибка смены squad: <code>{esc(str(exc))[:1000]}</code>')


@router.callback_query(F.data.startswith('admin:remna:assign_plan:'))
async def admin_assign_plan(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    uid = callback.data.split(':', 3)[3]
    plans = await list_admin_plans(runtime.settings.db_path)
    plans = [p for p in plans if p.get('slug') != 'remna-import']
    if not plans:
        await callback.message.answer('Служебных тарифов нет. Создайте: /admin → Тарифы → Новый служебный.')
        return
    await callback.answer('Выбор тарифа')
    await callback.message.answer('💼 <b>Выберите служебный тариф</b>', reply_markup=plan_select_menu(uid, plans))


@router.callback_query(F.data.startswith('admin:remna:set_plan:'))
async def admin_set_plan(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    key = callback.data.split(':', 3)[3]
    uid, plan_id_raw = PENDING_ACTIONS.pop(key, ('', ''))
    if not uid or not plan_id_raw:
        await callback.answer('Действие устарело', show_alert=True)
        return
    plans = await list_admin_plans(runtime.settings.db_path)
    plan = next((p for p in plans if str(p['id']) == plan_id_raw), None)
    user = await get_remna_user_by_uuid(uid)
    if not plan or not user:
        await callback.message.answer('План или пользователь не найден.')
        return
    current_expire = parse_expire(str(user.get('expireAt') or user.get('expiresAt') or ''))
    start = current_expire if current_expire > datetime.now(timezone.utc) else datetime.now(timezone.utc)
    expire = start + timedelta(days=int(plan['duration_days']))
    payload = {'uuid': uid, 'status': 'ACTIVE', 'expireAt': to_iso(expire)}
    if int(plan.get('traffic_gb') or 0) > 0:
        payload['trafficLimitBytes'] = int(plan['traffic_gb']) * 1024 * 1024 * 1024
    await callback.answer('Назначаю тариф')
    try:
        await RemnawaveClient(runtime.settings)._request('PATCH', '/api/users', json_payload=payload, expected_status=(200, 201))
        await callback.message.answer(f"✅ Назначен служебный тариф <b>{esc(plan['title'])}</b>\nДо: <b>{to_iso(expire)[:19]}</b>", reply_markup=remna_user_menu(uid))
    except Exception as exc:
        await callback.message.answer(f'Ошибка назначения тарифа: <code>{esc(str(exc))[:1000]}</code>')


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
