from __future__ import annotations

from typing import Any

import aiosqlite

from app.db import now_iso

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
