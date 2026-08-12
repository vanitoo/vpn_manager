from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db import now_iso

log = logging.getLogger(__name__)

DEFAULT_REMINDERS = [
    {'code': 'before_3d', 'title': 'За 3 дня', 'offset_hours': -72, 'is_enabled': 1, 'message': 'Ваш VPN-доступ истекает через 3 дня. Продлите подписку заранее, чтобы не потерять доступ.'},
    {'code': 'before_1d', 'title': 'За 1 день', 'offset_hours': -24, 'is_enabled': 1, 'message': 'VPN-доступ истекает завтра. Можно продлить прямо сейчас.'},
    {'code': 'before_12h', 'title': 'За 12 часов', 'offset_hours': -12, 'is_enabled': 1, 'message': 'VPN-доступ скоро закончится. Продлите подписку, чтобы всё работало без перерыва.'},
    {'code': 'expired_now', 'title': 'Когда просрочено', 'offset_hours': 0, 'is_enabled': 1, 'message': 'VPN-доступ закончился. Продлите подписку, чтобы снова подключиться.'},
    {'code': 'expired_1d', 'title': '+1 день', 'offset_hours': 24, 'is_enabled': 1, 'message': 'VPN-доступ закончился вчера. Продление всё ещё доступно.'},
    {'code': 'expired_3d', 'title': '+3 дня', 'offset_hours': 72, 'is_enabled': 1, 'message': 'VPN не активен уже 3 дня. Вернуться можно одной оплатой.'},
    {'code': 'expired_7d', 'title': '+7 дней', 'offset_hours': 168, 'is_enabled': 1, 'message': 'VPN-доступ отключён уже неделю. Если нужен доступ, продлите подписку.'},
]


async def init_mailing_tables(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript('''
        CREATE TABLE IF NOT EXISTS reminder_rules (
            code TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            offset_hours INTEGER NOT NULL,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reminder_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            telegram_id INTEGER NOT NULL,
            subscription_id INTEGER,
            scheduled_for TEXT,
            sent_at TEXT,
            status TEXT NOT NULL DEFAULT 'planned',
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_reminder_events_unique ON reminder_events(code, telegram_id, subscription_id);
        ''')
        ts = now_iso()
        for rule in DEFAULT_REMINDERS:
            await db.execute('''
                INSERT OR IGNORE INTO reminder_rules (code,title,offset_hours,is_enabled,message,created_at,updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (rule['code'], rule['title'], rule['offset_hours'], rule['is_enabled'], rule['message'], ts, ts))
        await db.commit()


async def list_reminder_rules(db_path: str) -> list[dict[str, Any]]:
    await init_mailing_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM reminder_rules ORDER BY offset_hours') as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_reminder_rule(db_path: str, code: str) -> dict[str, Any] | None:
    await init_mailing_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM reminder_rules WHERE code=?', (code,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def toggle_reminder_rule(db_path: str, code: str) -> None:
    await init_mailing_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute('UPDATE reminder_rules SET is_enabled=CASE WHEN is_enabled=1 THEN 0 ELSE 1 END, updated_at=? WHERE code=?', (now_iso(), code))
        await db.commit()


async def update_reminder_message(db_path: str, code: str, message: str) -> None:
    await init_mailing_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute('UPDATE reminder_rules SET message=?, updated_at=? WHERE code=?', (message, now_iso(), code))
        await db.commit()


async def reminder_stats(db_path: str) -> dict[str, int]:
    await init_mailing_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        async def count(sql: str) -> int:
            async with db.execute(sql) as cur:
                row = await cur.fetchone()
                return int(row[0] or 0)
        return {
            'rules': await count('SELECT COUNT(*) FROM reminder_rules'),
            'enabled': await count('SELECT COUNT(*) FROM reminder_rules WHERE is_enabled=1'),
            'events': await count('SELECT COUNT(*) FROM reminder_events'),
            'sent': await count("SELECT COUNT(*) FROM reminder_events WHERE status='sent'"),
            'errors': await count("SELECT COUNT(*) FROM reminder_events WHERE status='error'"),
        }


def _parse_datetime(value: str) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


async def _due_reminders(db_path: str, *, now: datetime, lookback_hours: int, limit: int) -> list[dict[str, Any]]:
    await init_mailing_tables(db_path)
    earliest = now - timedelta(hours=max(1, lookback_hours))
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM reminder_rules WHERE is_enabled=1 ORDER BY offset_hours') as cur:
            rules = [dict(row) for row in await cur.fetchall()]
        async with db.execute("SELECT id, telegram_id, expires_at FROM subscriptions WHERE status='active' ORDER BY expires_at") as cur:
            subscriptions = [dict(row) for row in await cur.fetchall()]
        async with db.execute("SELECT code, telegram_id, subscription_id FROM reminder_events WHERE status IN ('sent','error')") as cur:
            existing = {(row['code'], int(row['telegram_id']), int(row['subscription_id'])) for row in await cur.fetchall()}

    due: list[dict[str, Any]] = []
    for subscription in subscriptions:
        expires_at = _parse_datetime(subscription['expires_at'])
        if not expires_at:
            continue
        for rule in rules:
            key = (str(rule['code']), int(subscription['telegram_id']), int(subscription['id']))
            scheduled_for = expires_at + timedelta(hours=int(rule['offset_hours']))
            if int(rule['offset_hours']) < 0 and now >= expires_at:
                continue
            if key not in existing and earliest <= scheduled_for <= now:
                due.append({**rule, **subscription, 'scheduled_for': scheduled_for.isoformat()})
                if len(due) >= limit:
                    return due
    return due


async def _reserve_event(db_path: str, item: dict[str, Any]) -> int | None:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute('''
            INSERT OR IGNORE INTO reminder_events
                (code, telegram_id, subscription_id, scheduled_for, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'planned', ?, ?)
        ''', (item['code'], item['telegram_id'], item['id'], item['scheduled_for'], ts, ts))
        await db.commit()
        if cursor.rowcount:
            return int(cursor.lastrowid)
        async with db.execute('''
            SELECT id FROM reminder_events
            WHERE code=? AND telegram_id=? AND subscription_id=? AND status='planned'
        ''', (item['code'], item['telegram_id'], item['id'])) as existing:
            row = await existing.fetchone()
            return int(row[0]) if row else None


async def _finish_event(db_path: str, event_id: int, *, status: str, error: str = '') -> None:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            'UPDATE reminder_events SET status=?, sent_at=?, error=?, updated_at=? WHERE id=?',
            (status, ts if status == 'sent' else None, error[:1000] or None, ts, event_id),
        )
        await db.commit()


async def send_due_reminders(
    bot: Bot,
    db_path: str,
    *,
    lookback_hours: int = 24,
    batch_size: int = 50,
    now: datetime | None = None,
) -> dict[str, int]:
    """Send due reminders once, reserving each event before Telegram delivery."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    items = await _due_reminders(db_path, now=current, lookback_hours=lookback_hours, limit=max(1, batch_size))
    result = {'due': len(items), 'sent': 0, 'errors': 0}
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Продлить VPN', callback_data='plans')],
    ])
    for item in items:
        event_id = await _reserve_event(db_path, item)
        if event_id is None:
            continue
        try:
            await bot.send_message(int(item['telegram_id']), html.escape(str(item['message'])), reply_markup=keyboard)
        except TelegramRetryAfter as exc:
            await _finish_event(db_path, event_id, status='error', error=f'TelegramRetryAfter: {exc}')
            result['errors'] += 1
            log.warning('Mailing rate limited for %ss', exc.retry_after)
            break
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            await _finish_event(db_path, event_id, status='error', error=f'{type(exc).__name__}: {exc}')
            result['errors'] += 1
        except Exception as exc:
            await _finish_event(db_path, event_id, status='error', error=f'{type(exc).__name__}: {exc}')
            result['errors'] += 1
            log.exception('Cannot send reminder event %s', event_id)
        else:
            await _finish_event(db_path, event_id, status='sent')
            result['sent'] += 1
        await asyncio.sleep(0.05)
    return result


async def mailing_loop(
    bot: Bot,
    db_path: str,
    *,
    interval_seconds: int = 300,
    lookback_hours: int = 24,
    batch_size: int = 50,
) -> None:
    """Continuously deliver reminders; one failed cycle never stops the bot."""

    log.info('Mailing loop started: interval=%ss lookback=%sh batch=%s', interval_seconds, lookback_hours, batch_size)
    while True:
        try:
            result = await send_due_reminders(bot, db_path, lookback_hours=lookback_hours, batch_size=batch_size)
            if result['due'] or result['errors']:
                log.info('Mailing cycle: %s', result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Mailing cycle failed')
        await asyncio.sleep(max(30, interval_seconds))
