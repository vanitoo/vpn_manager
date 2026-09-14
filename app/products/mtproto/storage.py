from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_mtproto_tables(db_path: str) -> None:
    """Create MTProto-owned schema without changing the core VPN schema."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute('PRAGMA table_info(plans)')
        columns = {row[1] for row in await cur.fetchall()}
        if 'mtproto_enabled' not in columns:
            await db.execute('ALTER TABLE plans ADD COLUMN mtproto_enabled INTEGER NOT NULL DEFAULT 0')

        await db.executescript('''
        CREATE TABLE IF NOT EXISTS mtproto_access (
            telegram_id INTEGER PRIMARY KEY,
            generation INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'disabled',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            enabled_at TEXT,
            disabled_at TEXT,
            last_error TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_mtproto_access_status
            ON mtproto_access(status, updated_at);
        ''')
        await db.commit()


async def get_access(db_path: str, telegram_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM mtproto_access WHERE telegram_id=?', (telegram_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def ensure_access_row(db_path: str, telegram_id: int) -> dict[str, Any]:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        await db.execute('''
            INSERT OR IGNORE INTO mtproto_access
                (telegram_id, generation, status, created_at, updated_at, last_error)
            VALUES (?, 1, 'disabled', ?, ?, '')
        ''', (telegram_id, ts, ts))
        await db.commit()
    row = await get_access(db_path, telegram_id)
    if not row:
        raise RuntimeError('Cannot create MTProto access row')
    return row


async def rotate_generation(db_path: str, telegram_id: int) -> dict[str, Any]:
    await ensure_access_row(db_path, telegram_id)
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        await db.execute('''
            UPDATE mtproto_access
            SET generation=generation+1, status='provisioning', updated_at=?, last_error=''
            WHERE telegram_id=?
        ''', (ts, telegram_id))
        await db.commit()
    row = await get_access(db_path, telegram_id)
    if not row:
        raise RuntimeError('Cannot rotate MTProto generation')
    return row


async def set_access_state(
    db_path: str,
    telegram_id: int,
    status: str,
    *,
    error: str = '',
) -> None:
    ts = now_iso()
    enabled_at = ts if status == 'active' else None
    disabled_at = ts if status == 'disabled' else None
    async with aiosqlite.connect(db_path) as db:
        await db.execute('''
            UPDATE mtproto_access
            SET status=?, updated_at=?,
                enabled_at=COALESCE(?, enabled_at),
                disabled_at=COALESCE(?, disabled_at),
                last_error=?
            WHERE telegram_id=?
        ''', (status, ts, enabled_at, disabled_at, error[:1000], telegram_id))
        await db.commit()


async def list_accesses(db_path: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM mtproto_access ORDER BY updated_at DESC') as cur:
            return [dict(row) for row in await cur.fetchall()]


async def set_plan_enabled(db_path: str, plan_id: int, enabled: bool) -> None:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            'UPDATE plans SET mtproto_enabled=?, updated_at=? WHERE id=?',
            (1 if enabled else 0, ts, plan_id),
        )
        await db.commit()


async def list_plans_mtproto(db_path: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT id, slug, title, price_rub, is_active,
                   COALESCE(mtproto_enabled, 0) AS mtproto_enabled
            FROM plans
            WHERE slug <> 'remna-import'
            ORDER BY sort_order, id
        ''') as cur:
            return [dict(row) for row in await cur.fetchall()]


_PAID_ENTITLEMENT_SQL = '''
    SELECT s.*, p.title AS plan_title, p.slug AS plan_slug,
           COALESCE(p.mtproto_enabled, 0) AS mtproto_enabled
    FROM subscriptions s
    JOIN plans p ON p.id=s.plan_id
    WHERE s.telegram_id=?
      AND s.status='active'
      AND s.expires_at>?
      AND COALESCE(p.mtproto_enabled, 0)=1
      AND EXISTS (
          SELECT 1
          FROM payments pay
          WHERE pay.telegram_id=s.telegram_id
            AND pay.status IN ('paid','succeeded')
            AND (
                pay.subscription_id=s.id
                OR (
                    pay.subscription_id IS NULL
                    AND pay.plan_id=s.plan_id
                    AND pay.created_at>=s.created_at
                )
            )
      )
    ORDER BY s.expires_at DESC, s.id DESC
    LIMIT 1
'''


async def get_paid_entitlement(db_path: str, telegram_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(_PAID_ENTITLEMENT_SQL, (telegram_id, now_iso())) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def list_paid_entitled_telegram_ids(db_path: str) -> list[int]:
    now = now_iso()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute('''
            SELECT DISTINCT s.telegram_id
            FROM subscriptions s
            JOIN plans p ON p.id=s.plan_id
            WHERE s.status='active'
              AND s.expires_at>?
              AND COALESCE(p.mtproto_enabled, 0)=1
              AND EXISTS (
                  SELECT 1 FROM payments pay
                  WHERE pay.telegram_id=s.telegram_id
                    AND pay.status IN ('paid','succeeded')
                    AND (
                        pay.subscription_id=s.id
                        OR (
                            pay.subscription_id IS NULL
                            AND pay.plan_id=s.plan_id
                            AND pay.created_at>=s.created_at
                        )
                    )
              )
            ORDER BY s.telegram_id
        ''', (now,)) as cur:
            return [int(row[0]) for row in await cur.fetchall()]
