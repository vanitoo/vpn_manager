from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_admin_tables(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript('''
        CREATE TABLE IF NOT EXISTS trials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL UNIQUE,
            subscription_id INTEGER,
            granted_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_trials_telegram_id ON trials(telegram_id);
        ''')
        await db.commit()


async def has_used_trial(db_path: str, telegram_id: int) -> bool:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute('SELECT 1 FROM trials WHERE telegram_id=? LIMIT 1', (telegram_id,)) as cur:
            return await cur.fetchone() is not None


async def mark_trial_used(db_path: str, telegram_id: int, subscription_id: int | None) -> None:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            'INSERT OR IGNORE INTO trials (telegram_id, subscription_id, granted_at, created_at) VALUES (?, ?, ?, ?)',
            (telegram_id, subscription_id, ts, ts),
        )
        await db.commit()


async def list_users_page(db_path: str, *, mode: str = 'recent', limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
    where = ''
    params: list[Any] = []
    if mode == 'active':
        where = "WHERE EXISTS (SELECT 1 FROM subscriptions s WHERE s.telegram_id=u.telegram_id AND s.status='active' AND s.expires_at>?)"
        params.append(now_iso())
    elif mode == 'expired':
        where = "WHERE EXISTS (SELECT 1 FROM subscriptions s WHERE s.telegram_id=u.telegram_id) AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.telegram_id=u.telegram_id AND s.status='active' AND s.expires_at>?)"
        params.append(now_iso())
    query = f'''
        SELECT u.*, 
               (SELECT MAX(expires_at) FROM subscriptions s WHERE s.telegram_id=u.telegram_id AND s.status='active') AS expires_at,
               (SELECT subscription_url FROM subscriptions s WHERE s.telegram_id=u.telegram_id ORDER BY id DESC LIMIT 1) AS subscription_url,
               (SELECT remnawave_user_id FROM subscriptions s WHERE s.telegram_id=u.telegram_id ORDER BY id DESC LIMIT 1) AS remnawave_user_id,
               (SELECT COUNT(*) FROM payments p WHERE p.telegram_id=u.telegram_id AND p.status IN ('paid','succeeded')) AS paid_count
        FROM users u
        {where}
        ORDER BY u.updated_at DESC
        LIMIT ? OFFSET ?
    '''
    params.extend([limit, offset])
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def find_user(db_path: str, query: str) -> dict[str, Any] | None:
    q = query.strip().lstrip('@')
    if not q:
        return None
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        sql = '''
            SELECT u.*, 
                   (SELECT MAX(expires_at) FROM subscriptions s WHERE s.telegram_id=u.telegram_id AND s.status='active') AS expires_at,
                   (SELECT subscription_url FROM subscriptions s WHERE s.telegram_id=u.telegram_id ORDER BY id DESC LIMIT 1) AS subscription_url,
                   (SELECT remnawave_user_id FROM subscriptions s WHERE s.telegram_id=u.telegram_id ORDER BY id DESC LIMIT 1) AS remnawave_user_id,
                   (SELECT COUNT(*) FROM payments p WHERE p.telegram_id=u.telegram_id AND p.status IN ('paid','succeeded')) AS paid_count
            FROM users u
            WHERE CAST(u.telegram_id AS TEXT)=? OR lower(COALESCE(u.username,''))=lower(?)
            LIMIT 1
        '''
        async with db.execute(sql, (q, q)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def set_subscription_status(db_path: str, telegram_id: int, status: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute('UPDATE subscriptions SET status=?, updated_at=? WHERE telegram_id=? AND status="active"', (status, now_iso(), telegram_id))
        await db.commit()


async def update_plan(db_path: str, plan_id: int, field: str, value: Any) -> None:
    allowed = {'title', 'description', 'duration_days', 'traffic_gb', 'price_rub', 'is_active', 'sort_order'}
    if field not in allowed:
        raise ValueError('Unsupported plan field')
    async with aiosqlite.connect(db_path) as db:
        await db.execute(f'UPDATE plans SET {field}=?, updated_at=? WHERE id=?', (value, now_iso(), plan_id))
        await db.commit()


async def add_plan(db_path: str, *, slug: str, title: str, description: str, duration_days: int, price_rub: int, traffic_gb: int = 0) -> int:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute('''
            INSERT INTO plans (slug, title, description, duration_days, traffic_gb, price_rub, is_active, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, 100, ?, ?)
        ''', (slug, title, description, duration_days, traffic_gb, price_rub, ts, ts))
        await db.commit()
        return int(cur.lastrowid)
