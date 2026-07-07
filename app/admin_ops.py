from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app import runtime
from app.keyboards import admin_logs_menu, admin_menu, admin_nodes_menu, node_manage_menu
from app.remna_admin import fmt_bytes, remna_nodes, remna_stats, remna_users
from app.remnawave import RemnawaveClient
from app.system_info import get_system_info

router = Router()
NODE_KEYS: dict[str, dict[str, Any]] = {}


def esc(value: str) -> str:
    return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def node_key(item: dict[str, Any]) -> str:
    key = str(item.get('uuid') or item.get('id') or item.get('name') or item.get('address') or len(NODE_KEYS))[:32]
    safe = ''.join(ch for ch in key if ch.isalnum())[:20] or str(len(NODE_KEYS))
    NODE_KEYS[safe] = item
    return safe


def node_name(item: dict[str, Any]) -> str:
    return str(item.get('name') or item.get('address') or item.get('host') or item.get('uuid') or item.get('id') or 'node')


def node_status(item: dict[str, Any]) -> str:
    value = item.get('status') or item.get('isConnected') or item.get('isOnline') or '-'
    return str(value)


def node_health_dot(item: dict[str, Any]) -> str:
    s = node_status(item).lower()
    return '🟢' if s in {'true', 'online', 'active', 'connected', 'running'} else '🟡'


def node_address(item: dict[str, Any]) -> str:
    return str(item.get('address') or item.get('host') or item.get('ip') or '-')


def node_metrics(item: dict[str, Any]) -> str:
    keys = {
        'CPU': ['cpu', 'cpuLoad', 'cpuUsage', 'cpuPercent'],
        'RAM': ['ram', 'memory', 'memoryUsage', 'mem'],
        'Users': ['usersOnline', 'onlineUsers', 'users', 'usersCount'],
        'Traffic': ['traffic', 'trafficUsed', 'usedTrafficBytes'],
        'Version': ['version', 'xrayVersion', 'remnanodeVersion'],
        'Uptime': ['uptime', 'upTime'],
    }
    lines = []
    for label, names in keys.items():
        val = '-'
        for name in names:
            if item.get(name) not in (None, ''):
                val = item[name]
                break
        lines.append(f'{label}: {esc(str(val))}')
    return '\n'.join(lines)


async def local_dashboard() -> dict[str, int]:
    async with aiosqlite.connect(runtime.settings.db_path) as db:
        async def count(sql: str) -> int:
            try:
                async with db.execute(sql) as cur:
                    row = await cur.fetchone()
                    return int(row[0] or 0)
            except Exception:
                return 0
        now = datetime.now(timezone.utc).isoformat()
        return {
            'users': await count('SELECT COUNT(*) FROM users'),
            'active': await count("SELECT COUNT(*) FROM subscriptions WHERE status='active' AND expires_at>datetime('now')"),
            'blocked': await count("SELECT COUNT(*) FROM subscriptions WHERE status='blocked'"),
            'payments': await count("SELECT COUNT(*) FROM payments WHERE status IN ('paid','succeeded')"),
            'trials': await count('SELECT COUNT(*) FROM trials'),
            'plans_public': await count("SELECT COUNT(*) FROM plans WHERE COALESCE(is_public,1)=1 AND slug!='remna-import'"),
            'plans_service': await count("SELECT COUNT(*) FROM plans WHERE COALESCE(is_public,1)=0 AND slug!='remna-import'"),
        }


@router.callback_query(F.data == 'admin:dashboard')
async def admin_dashboard(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer('Дашборд')
    local = await local_dashboard()
    nodes = await remna_nodes(RemnawaveClient(runtime.settings))
    remna = await remna_users(RemnawaveClient(runtime.settings), limit=1000)
    st = remna_stats(remna)
    nodes_online = sum(1 for node in nodes if node_health_dot(node) == '🟢')
    text = (
        '📊 <b>Дашборд</b>\n\n'
        '<b>Bot / SQLite</b>\n'
        f"👥 Пользователей: <b>{local['users']}</b>\n"
        f"🟢 Активных подписок: <b>{local['active']}</b>\n"
        f"🚫 Заблокировано: <b>{local['blocked']}</b>\n"
        f"🎁 Тестов выдано: <b>{local['trials']}</b>\n"
        f"💳 Оплат: <b>{local['payments']}</b>\n"
        f"🛒 Публичных тарифов: <b>{local['plans_public']}</b>\n"
        f"🔒 Служебных тарифов: <b>{local['plans_service']}</b>\n\n"
        '<b>Remnawave</b>\n'
        f"👥 Пользователей: <b>{st['total']}</b>\n"
        f"🟢 Active: <b>{st['active']}</b>\n"
        f"🧷 С Telegram ID: <b>{st['with_tg']}</b>\n"
        f"📡 Трафик: <b>{fmt_bytes(st['traffic_used'])}</b> / {fmt_bytes(st['traffic_limit'])}\n"
        f"🖥 Ноды: <b>{len(nodes)}</b>, online: <b>{nodes_online}</b>"
    )
    await callback.message.answer(text[:3900], reply_markup=admin_menu())


@router.callback_query(F.data == 'admin:logs')
async def admin_logs(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer()
    await callback.message.answer('📜 <b>Логи</b>', reply_markup=admin_logs_menu())


def read_tail(path: str, lines: int = 50, errors_only: bool = False) -> str:
    p = Path(path)
    if not p.exists():
        return 'log file not found'
    data = p.read_text(encoding='utf-8', errors='replace').splitlines()
    if errors_only:
        data = [x for x in data if 'ERROR' in x or 'WARNING' in x or 'Traceback' in x]
    return '\n'.join(data[-lines:]) or 'empty'


@router.callback_query(F.data == 'admin:logs:tail')
async def admin_logs_tail(callback: CallbackQuery) -> None:
    await callback.answer('Читаю')
    text = read_tail(runtime.settings.log_file, 50)
    await callback.message.answer('📄 <b>Последние строки</b>\n\n<code>' + esc(text[-3500:]) + '</code>', reply_markup=admin_logs_menu())


@router.callback_query(F.data == 'admin:logs:errors')
async def admin_logs_errors(callback: CallbackQuery) -> None:
    await callback.answer('Ищу ошибки')
    text = read_tail(runtime.settings.log_file, 80, errors_only=True)
    await callback.message.answer('⚠️ <b>Ошибки / warnings</b>\n\n<code>' + esc(text[-3500:]) + '</code>', reply_markup=admin_logs_menu())


@router.callback_query(F.data == 'admin:logs:size')
async def admin_logs_size(callback: CallbackQuery) -> None:
    await callback.answer('Размеры')
    root = Path(runtime.settings.log_file).parent
    items = []
    if root.exists():
        for item in sorted(root.glob('*')):
            if item.is_file():
                items.append(f'{item.name}: {fmt_bytes(item.stat().st_size)}')
    await callback.message.answer('🧹 <b>Файлы логов</b>\n\n<code>' + esc('\n'.join(items) or 'empty') + '</code>', reply_markup=admin_logs_menu())


@router.callback_query(F.data == 'admin:nodes')
async def admin_nodes_home(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer()
    await callback.message.answer('🖥 <b>Ноды</b>\n\nУправление нодами пока в безопасном режиме: мониторинг и заготовки операций.', reply_markup=admin_nodes_menu())


@router.callback_query(F.data == 'admin:nodes:list')
async def admin_nodes_list(callback: CallbackQuery) -> None:
    await callback.answer('Запрос нод')
    nodes = await remna_nodes(RemnawaveClient(runtime.settings))
    if not nodes:
        await callback.message.answer('🖥 Ноды не найдены.', reply_markup=admin_nodes_menu())
        return
    rows = []
    lines = []
    for node in nodes[:15]:
        key = node_key(node)
        lines.append(f"{node_health_dot(node)} <b>{esc(node_name(node))}</b> · {esc(node_address(node))} · {esc(node_status(node))}")
        rows.append([InlineKeyboardButton(text=f'{node_health_dot(node)} {node_name(node)[:36]}', callback_data=f'admin:node:card:{key}')])
    rows.append([InlineKeyboardButton(text='← Ноды', callback_data='admin:nodes')])
    await callback.message.answer('🖥 <b>Ноды</b>\n\n' + '\n'.join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith('admin:node:card:'))
async def admin_node_card(callback: CallbackQuery) -> None:
    key = callback.data.split(':')[-1]
    node = NODE_KEYS.get(key)
    if not node:
        await callback.answer('Нода устарела, обновите список', show_alert=True)
        return
    await callback.answer('Нода')
    text = f"🖥 <b>{esc(node_name(node))}</b>\n\nАдрес: <code>{esc(node_address(node))}</code>\nСтатус: <b>{esc(node_status(node))}</b>\n\n<code>{node_metrics(node)}</code>"
    await callback.message.answer(text[:3900], reply_markup=node_manage_menu(key))


@router.callback_query(F.data == 'admin:nodes:health')
async def admin_nodes_health(callback: CallbackQuery) -> None:
    await callback.answer('Проверяю')
    client = RemnawaveClient(runtime.settings)
    diag = await client.diagnostics()
    nodes = await remna_nodes(client)
    lines = [f'{node_health_dot(node)} {node_name(node)} · {node_status(node)}' for node in nodes[:20]]
    text = '🩺 <b>Проверка доступности</b>\n\n<b>API</b>\n<code>' + esc(diag)[:1500] + '</code>\n\n<b>Nodes</b>\n' + esc('\n'.join(lines) or 'empty')
    await callback.message.answer(text[:3900], reply_markup=admin_nodes_menu())


@router.callback_query(F.data == 'admin:nodes:roadmap')
async def admin_nodes_roadmap(callback: CallbackQuery) -> None:
    await callback.answer()
    text = (
        '🧭 <b>Идея управления нодами</b>\n\n'
        '1. <b>Создать ноду</b>\n'
        '   Бот генерирует команду установки remnanode под конкретный server key.\n\n'
        '2. <b>Подключить ноду</b>\n'
        '   Админ вставляет IP/SSH, бот проверяет порт, ключ, связь с панелью.\n\n'
        '3. <b>Мониторинг</b>\n'
        '   Online/offline, users, CPU/RAM, трафик, версия Xray/remnanode.\n\n'
        '4. <b>Reboot / Update</b>\n'
        '   Через SSH-команды или Remnawave API, после подтверждения.\n\n'
        '5. <b>Авто-алерты</b>\n'
        '   Нода offline, высокий CPU/RAM, нет трафика, ошибка API.'
    )
    await callback.message.answer(text, reply_markup=admin_nodes_menu())


@router.callback_query(F.data.startswith('admin:node:'))
async def admin_node_safe_action(callback: CallbackQuery) -> None:
    await callback.answer()
    parts = callback.data.split(':')
    action = parts[2] if len(parts) > 2 else '-'
    await callback.message.answer(
        f'⚠️ Действие <b>{esc(action)}</b> пока в безопасном режиме.\n\n'
        'Для reboot/update/connect нужно добавить SSH-профиль ноды и подтверждение перед выполнением. Иначе получится кнопка «сломать прод», а такие кнопки человечество уже изобретало слишком много раз.',
        reply_markup=admin_nodes_menu(),
    )


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
