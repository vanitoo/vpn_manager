from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_support_tables(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript('''
        CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            user_id INTEGER,
            category TEXT NOT NULL DEFAULT 'other',
            status TEXT NOT NULL DEFAULT 'new',
            priority TEXT NOT NULL DEFAULT 'normal',
            topic_id INTEGER,
            assigned_admin_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            closed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_support_tickets_user_status
            ON support_tickets(telegram_id, status, updated_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_support_tickets_topic
            ON support_tickets(topic_id) WHERE topic_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS support_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            sender_telegram_id INTEGER,
            user_message_id INTEGER,
            support_message_id INTEGER,
            message_type TEXT NOT NULL DEFAULT 'text',
            text_preview TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(ticket_id) REFERENCES support_tickets(id)
        );
        CREATE INDEX IF NOT EXISTS idx_support_messages_ticket
            ON support_messages(ticket_id, created_at);
        ''')
        await db.commit()


async def get_open_ticket(db_path: str, telegram_id: int) -> dict[str, Any] | None:
    await init_support_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT * FROM support_tickets
            WHERE telegram_id=? AND status IN ('new','in_progress','waiting_user')
            ORDER BY id DESC LIMIT 1
        ''', (telegram_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def create_ticket(db_path: str, *, telegram_id: int, user_id: int | None, category: str) -> dict[str, Any]:
    await init_support_tables(db_path)
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute('''
            INSERT INTO support_tickets
                (telegram_id, user_id, category, status, priority, created_at, updated_at)
            VALUES (?, ?, ?, 'new', 'normal', ?, ?)
        ''', (telegram_id, user_id, category, ts, ts))
        await db.commit()
        ticket_id = int(cur.lastrowid)
        async with db.execute('SELECT * FROM support_tickets WHERE id=?', (ticket_id,)) as cur2:
            return dict(await cur2.fetchone())


async def set_ticket_topic(db_path: str, ticket_id: int, topic_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute('UPDATE support_tickets SET topic_id=?, updated_at=? WHERE id=?', (topic_id, now_iso(), ticket_id))
        await db.commit()


async def get_ticket_by_topic(db_path: str, topic_id: int) -> dict[str, Any] | None:
    await init_support_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM support_tickets WHERE topic_id=? LIMIT 1', (topic_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_ticket_status(db_path: str, ticket_id: int, status: str, assigned_admin_id: int | None = None) -> None:
    ts = now_iso()
    closed_at = ts if status == 'closed' else None
    async with aiosqlite.connect(db_path) as db:
        await db.execute('''
            UPDATE support_tickets
            SET status=?, assigned_admin_id=COALESCE(?, assigned_admin_id), updated_at=?, closed_at=?
            WHERE id=?
        ''', (status, assigned_admin_id, ts, closed_at, ticket_id))
        await db.commit()


async def add_support_message(
    db_path: str,
    *,
    ticket_id: int,
    direction: str,
    sender_telegram_id: int | None,
    user_message_id: int | None,
    support_message_id: int | None,
    message_type: str,
    text_preview: str,
) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute('''
            INSERT INTO support_messages
                (ticket_id, direction, sender_telegram_id, user_message_id, support_message_id,
                 message_type, text_preview, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            ticket_id, direction, sender_telegram_id, user_message_id, support_message_id,
            message_type, text_preview[:500], now_iso(),
        ))
        await db.execute('UPDATE support_tickets SET updated_at=? WHERE id=?', (now_iso(), ticket_id))
        await db.commit()


async def support_stats(db_path: str) -> dict[str, int]:
    await init_support_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        result: dict[str, int] = {}
        for status in ('new', 'in_progress', 'waiting_user', 'closed'):
            async with db.execute('SELECT COUNT(*) FROM support_tickets WHERE status=?', (status,)) as cur:
                row = await cur.fetchone()
                result[status] = int(row[0] or 0)
        return result
