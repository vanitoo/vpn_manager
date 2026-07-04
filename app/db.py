from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from app.version import DB_SCHEMA_VERSION


CREATE_SQL = '''
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    duration_days INTEGER NOT NULL DEFAULT 30,
    traffic_gb INTEGER NOT NULL DEFAULT 0,
    price_rub INTEGER NOT NULL DEFAULT 299,
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL UNIQUE,
    username TEXT,
    full_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    telegram_id INTEGER NOT NULL,
    plan_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    starts_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    remnawave_user_id TEXT NOT NULL DEFAULT '',
    subscription_url TEXT NOT NULL DEFAULT '',
    traffic_limit_gb INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(plan_id) REFERENCES plans(id)
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    provider_payment_id TEXT NOT NULL DEFAULT '',
    user_id INTEGER NOT NULL,
    telegram_id INTEGER NOT NULL,
    plan_id INTEGER NOT NULL,
    amount_rub INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'RUB',
    status TEXT NOT NULL DEFAULT 'pending',
    payment_url TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '',
    telegram_payment_charge_id TEXT,
    provider_payment_charge_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    paid_at TEXT,
    subscription_id INTEGER,
    UNIQUE(provider, provider_payment_id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(plan_id) REFERENCES plans(id)
);

CREATE TABLE IF NOT EXISTS receipt_contacts (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    receipt_type TEXT NOT NULL DEFAULT '',
    receipt_email TEXT NOT NULL DEFAULT '',
    receipt_phone TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plans_active_sort ON plans(is_active, sort_order, id);
CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_telegram_status ON subscriptions(telegram_id, status, expires_at);
CREATE INDEX IF NOT EXISTS idx_payments_telegram_status ON payments(telegram_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_payments_plan ON payments(plan_id);
'''


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def plan_to_payload(plan_id: int) -> str:
    return f'plan:{plan_id}'


def subscription_is_active(row: dict[str, Any]) -> bool:
    expires = parse_iso(row.get('expires_at'))
    return row.get('status') == 'active' and bool(expires and expires > datetime.now(timezone.utc))


async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute('PRAGMA foreign_keys = ON')
        await db.executescript(CREATE_SQL)
        await set_db_schema_version(db, DB_SCHEMA_VERSION)
        await db.commit()


async def set_db_schema_version(db: aiosqlite.Connection, version: int) -> None:
    await db.execute('''
        INSERT INTO app_meta (key, value, updated_at) VALUES ('db_schema_version', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
    ''', (str(version), now_iso()))


async def get_db_schema_version(db_path: str) -> int:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT value FROM app_meta WHERE key='db_schema_version' LIMIT 1") as cursor:
            row = await cursor.fetchone()
    if not row:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


async def upsert_user(db_path: str, *, telegram_id: int, username: str | None, full_name: str | None) -> dict[str, Any]:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute('''
            INSERT INTO users (telegram_id, username, full_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name,
                updated_at=excluded.updated_at
        ''', (telegram_id, username, full_name, ts, ts))
        await db.commit()
        async with db.execute('SELECT * FROM users WHERE telegram_id=?', (telegram_id,)) as cursor:
            return dict(await cursor.fetchone())


async def seed_plans(db_path: str, plans: list[dict[str, Any]]) -> None:
    async with aiosqlite.connect(db_path) as db:
        for idx, plan in enumerate(plans, start=1):
            ts = now_iso()
            await db.execute('''
                INSERT OR IGNORE INTO plans
                    (slug, title, description, duration_days, traffic_gb, price_rub, is_active, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                plan['slug'], plan['title'], plan.get('description', ''), int(plan.get('duration_days', 30)),
                int(plan.get('traffic_gb', 0)), int(plan.get('price_rub', 299)), int(plan.get('is_active', 1)),
                int(plan.get('sort_order', idx * 10)), ts, ts,
            ))
        await db.commit()


async def list_plans(db_path: str, *, active_only: bool = True) -> list[dict[str, Any]]:
    query = 'SELECT * FROM plans'
    params: list[Any] = []
    if active_only:
        query += ' WHERE is_active=1'
    query += ' ORDER BY sort_order ASC, id ASC'
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def get_plan_by_id(db_path: str, plan_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def add_payment(
    db_path: str,
    *,
    provider: str,
    provider_payment_id: str,
    user_id: int,
    telegram_id: int,
    plan_id: int,
    amount_rub: int,
    currency: str,
    status: str = 'pending',
    payment_url: str = '',
    payload: dict[str, Any] | str = '',
    telegram_payment_charge_id: str | None = None,
    provider_payment_charge_id: str | None = None,
) -> int:
    ts = now_iso()
    payload_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute('''
            INSERT INTO payments
                (provider, provider_payment_id, user_id, telegram_id, plan_id, amount_rub, currency, status, payment_url,
                 payload, telegram_payment_charge_id, provider_payment_charge_id, created_at, updated_at, paid_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            provider, provider_payment_id, user_id, telegram_id, plan_id, amount_rub, currency, status, payment_url,
            payload_text, telegram_payment_charge_id, provider_payment_charge_id, ts, ts, ts if status == 'paid' else None,
        ))
        await db.commit()
        return int(cursor.lastrowid)


async def get_payment(db_path: str, payment_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM payments WHERE id=?', (payment_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_active_pending_payment(db_path: str, *, telegram_id: int, plan_id: int, ttl_minutes: int) -> dict[str, Any] | None:
    threshold = (datetime.now(timezone.utc) - timedelta(minutes=ttl_minutes)).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT * FROM payments
            WHERE telegram_id=? AND plan_id=? AND status='pending' AND created_at>=?
            ORDER BY id DESC LIMIT 1
        ''', (telegram_id, plan_id, threshold)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def mark_payment_paid(db_path: str, payment_id: int, *, provider_status: str = 'paid') -> None:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        await db.execute('''
            UPDATE payments SET status=?, paid_at=COALESCE(paid_at, ?), updated_at=? WHERE id=?
        ''', (provider_status, ts, ts, payment_id))
        await db.commit()


async def attach_subscription_to_payment(db_path: str, payment_id: int, subscription_id: int) -> None:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        await db.execute('UPDATE payments SET subscription_id=?, updated_at=? WHERE id=?', (subscription_id, ts, payment_id))
        await db.commit()


async def add_subscription(
    db_path: str,
    *,
    user_id: int,
    telegram_id: int,
    plan_id: int,
    duration_days: int,
    traffic_limit_gb: int,
    remnawave_user_id: str = '',
    subscription_url: str = '',
) -> int:
    ts = now_iso()
    start = datetime.now(timezone.utc)
    active = await get_active_subscription(db_path, telegram_id=telegram_id)
    active_expires = parse_iso(active.get('expires_at')) if active else None
    if active_expires and active_expires > start:
        start = active_expires
    expires = start + timedelta(days=duration_days)
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute('''
            INSERT INTO subscriptions
                (user_id, telegram_id, plan_id, status, starts_at, expires_at, remnawave_user_id, subscription_url,
                 traffic_limit_gb, created_at, updated_at)
            VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, telegram_id, plan_id, start.isoformat(), expires.isoformat(), remnawave_user_id, subscription_url, traffic_limit_gb, ts, ts))
        await db.commit()
        return int(cursor.lastrowid)


async def update_subscription_access(db_path: str, subscription_id: int, *, remnawave_user_id: str, subscription_url: str) -> None:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        await db.execute('''
            UPDATE subscriptions SET remnawave_user_id=?, subscription_url=?, updated_at=? WHERE id=?
        ''', (remnawave_user_id, subscription_url, ts, subscription_id))
        await db.commit()


async def get_active_subscription(db_path: str, *, telegram_id: int) -> dict[str, Any] | None:
    now = now_iso()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT s.*, p.title AS plan_title, p.duration_days, p.price_rub
            FROM subscriptions s
            JOIN plans p ON p.id=s.plan_id
            WHERE s.telegram_id=? AND s.status='active' AND s.expires_at>?
            ORDER BY s.expires_at DESC LIMIT 1
        ''', (telegram_id, now)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def list_user_subscriptions(db_path: str, *, telegram_id: int) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT s.*, p.title AS plan_title
            FROM subscriptions s
            JOIN plans p ON p.id=s.plan_id
            WHERE s.telegram_id=?
            ORDER BY s.id DESC
        ''', (telegram_id,)) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def get_stats(db_path: str) -> dict[str, int]:
    async with aiosqlite.connect(db_path) as db:
        async def count(sql: str) -> int:
            async with db.execute(sql) as cursor:
                row = await cursor.fetchone()
                return int(row[0] or 0)

        return {
            'plans': await count('SELECT COUNT(*) FROM plans'),
            'users': await count('SELECT COUNT(*) FROM users'),
            'subscriptions': await count('SELECT COUNT(*) FROM subscriptions'),
            'active_subscriptions': await count("SELECT COUNT(*) FROM subscriptions WHERE status='active' AND expires_at>datetime('now')"),
            'payments': await count('SELECT COUNT(*) FROM payments'),
            'paid_payments': await count("SELECT COUNT(*) FROM payments WHERE status IN ('paid','succeeded')"),
        }


async def save_receipt_contact(
    db_path: str,
    *,
    user_id: int,
    username: str | None,
    full_name: str | None,
    receipt_type: str,
    receipt_email: str = '',
    receipt_phone: str = '',
) -> None:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        await db.execute('''
            INSERT INTO receipt_contacts
                (user_id, username, full_name, receipt_type, receipt_email, receipt_phone, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name,
                receipt_type=excluded.receipt_type,
                receipt_email=excluded.receipt_email,
                receipt_phone=excluded.receipt_phone,
                updated_at=excluded.updated_at
        ''', (user_id, username, full_name, receipt_type, receipt_email, receipt_phone, ts, ts))
        await db.commit()


async def get_receipt_contact(db_path: str, user_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM receipt_contacts WHERE user_id=?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


def receipt_customer_from_contact(contact: dict[str, Any] | None, fallback_email: str = '') -> dict[str, str] | None:
    if contact:
        if contact.get('receipt_email'):
            return {'email': contact['receipt_email']}
        if contact.get('receipt_phone'):
            return {'phone': contact['receipt_phone']}
    if fallback_email:
        return {'email': fallback_email}
    return None
