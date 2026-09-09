from __future__ import annotations

from typing import Any

import aiosqlite


async def _ensure_balance_column(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute('PRAGMA table_info(users)') as cursor:
            columns = {str(row[1]) for row in await cursor.fetchall()}
        if 'balance_rub' not in columns:
            await db.execute('ALTER TABLE users ADD COLUMN balance_rub INTEGER NOT NULL DEFAULT 0')
            await db.commit()


async def get_user_account(db_path: str, *, telegram_id: int) -> dict[str, Any] | None:
    await _ensure_balance_column(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT telegram_id, username, full_name, balance_rub FROM users WHERE telegram_id=? LIMIT 1',
            (telegram_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def list_user_payments(db_path: str, *, telegram_id: int, limit: int = 10) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 20))
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            '''
            SELECT pay.id, pay.provider, pay.amount_rub, pay.currency, pay.status,
                   pay.created_at, pay.paid_at, p.title AS plan_title
            FROM payments pay
            LEFT JOIN plans p ON p.id = pay.plan_id
            WHERE pay.telegram_id=?
            ORDER BY pay.id DESC
            LIMIT ?
            ''',
            (telegram_id, safe_limit),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]
