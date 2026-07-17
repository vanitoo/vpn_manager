from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app import runtime
from app.db import get_active_subscription
from app.remnawave import RemnawaveClient


def esc(value: Any) -> str:
    return str(value or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


async def _payment_summary(telegram_id: int) -> dict[str, Any]:
    async with aiosqlite.connect(runtime.settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT COUNT(*) AS paid_count,
                   COALESCE(SUM(amount_rub), 0) AS paid_total,
                   MAX(paid_at) AS last_paid_at
            FROM payments
            WHERE telegram_id=? AND status IN ('paid','succeeded')
        ''', (telegram_id,)) as cur:
            row = await cur.fetchone()
        async with db.execute('''
            SELECT provider, amount_rub, paid_at, created_at
            FROM payments
            WHERE telegram_id=? AND status IN ('paid','succeeded')
            ORDER BY COALESCE(paid_at, created_at) DESC LIMIT 1
        ''', (telegram_id,)) as cur:
            last = await cur.fetchone()
    return {
        'paid_count': int(row['paid_count'] or 0),
        'paid_total': int(row['paid_total'] or 0),
        'last_paid_at': row['last_paid_at'],
        'last_payment': dict(last) if last else None,
    }


async def collect_diagnostics(telegram_id: int) -> dict[str, Any]:
    sub = await get_active_subscription(runtime.settings.db_path, telegram_id=telegram_id)
    payments = await _payment_summary(telegram_id)
    remna: dict[str, Any] | None = None
    remna_error = ''
    client = RemnawaveClient(runtime.settings)
    if client.is_configured:
        try:
            remna = await client._get_user_by_email(client._email_for_user(telegram_id))
        except Exception as exc:
            remna_error = f'{type(exc).__name__}: {exc}'
    return {
        'subscription': sub,
        'payments': payments,
        'remnawave': remna,
        'remnawave_error': remna_error,
        'checked_at': datetime.now(timezone.utc).isoformat(),
    }


def user_diagnostic_text(data: dict[str, Any]) -> str:
    sub = data.get('subscription')
    remna = data.get('remnawave') or {}
    lines = ['🩺 <b>Диагностика Warp</b>', '']
    if sub:
        lines.extend([
            '✅ Подписка найдена',
            f"Статус: <b>{esc(sub.get('status'))}</b>",
            f"Активна до: <b>{esc(str(sub.get('expires_at') or '')[:16])}</b>",
            f"Ссылка подписки: <b>{'есть' if sub.get('subscription_url') else 'не найдена'}</b>",
        ])
    else:
        lines.extend(['❌ Активная подписка не найдена', 'Откройте тарифы, если доступ ещё не приобретён или уже истёк.'])
    if remna:
        lines.extend([
            '',
            f"Remnawave: <b>{esc(remna.get('status') or 'найден')}</b>",
            f"Дата окончания панели: <b>{esc(str(remna.get('expireAt') or '-')[:16])}</b>",
        ])
    elif data.get('remnawave_error'):
        lines.extend(['', '⚠️ Панель временно не ответила. Это будет показано специалисту.'])
    lines.extend(['', 'Если проблема осталась, передайте сообщение специалисту. Результат диагностики будет приложен автоматически.'])
    return '\n'.join(lines)


def admin_user_card(user: dict[str, Any], ticket: dict[str, Any], data: dict[str, Any]) -> str:
    sub = data.get('subscription') or {}
    remna = data.get('remnawave') or {}
    payments = data.get('payments') or {}
    last_payment = payments.get('last_payment') or {}
    username = f"@{user.get('username')}" if user.get('username') else '-'
    status = remna.get('status') or sub.get('status') or 'нет доступа'
    squad_value = remna.get('activeInternalSquads') or remna.get('internalSquads') or '-'
    return (
        f"🎫 <b>Обращение #{ticket['id']}</b>\n\n"
        f"<b>Пользователь</b>\n"
        f"Имя: {esc(user.get('full_name') or 'Пользователь')}\n"
        f"Username: {esc(username)}\n"
        f"Telegram ID: <code>{ticket['telegram_id']}</code>\n"
        f"Категория: <b>{esc(ticket.get('category'))}</b>\n\n"
        f"<b>VPN</b>\n"
        f"Статус: <b>{esc(status)}</b>\n"
        f"Тариф: <b>{esc(sub.get('plan_title') or '-')}</b>\n"
        f"Истекает: <b>{esc(str(sub.get('expires_at') or remna.get('expireAt') or '-')[:16])}</b>\n"
        f"Ссылка: <b>{'есть' if sub.get('subscription_url') or remna.get('subscriptionUrl') else 'нет'}</b>\n"
        f"Squad: <code>{esc(str(squad_value)[:300])}</code>\n"
        f"Remnawave UUID: <code>{esc(remna.get('uuid') or sub.get('remnawave_user_id') or '-')}</code>\n\n"
        f"<b>Оплаты</b>\n"
        f"Количество: <b>{payments.get('paid_count', 0)}</b>\n"
        f"Сумма: <b>{payments.get('paid_total', 0)} ₽</b>\n"
        f"Последняя: <b>{esc(last_payment.get('provider') or '-')} · {last_payment.get('amount_rub', 0)} ₽</b>\n\n"
        f"<b>Управление темой</b>\n"
        f"<code>/take</code> — взять в работу\n"
        f"<code>/wait</code> — ждать пользователя\n"
        f"<code>/close</code> — закрыть обращение"
    )
