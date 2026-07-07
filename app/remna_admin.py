from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.db import now_iso
from app.remnawave import RemnawaveClient


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and 'response' in payload:
        return payload['response']
    return payload


def _extract_list(payload: Any, keys: list[str]) -> list[dict[str, Any]]:
    data = _unwrap(payload)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        nested = data.get('data') or data.get('items')
        if isinstance(nested, list):
            return [x for x in nested if isinstance(x, dict)]
    return []


async def remna_nodes(client: RemnawaveClient) -> list[dict[str, Any]]:
    for path in ['/api/nodes', '/api/nodes?page=0&size=100']:
        try:
            _, data = await client._request('GET', path, expected_status=(200, 404))
            rows = _extract_list(data, ['nodes', 'data', 'items'])
            if rows:
                return rows
        except Exception:
            continue
    return []


async def remna_squads(client: RemnawaveClient) -> list[dict[str, Any]]:
    return await client.list_internal_squads()


async def remna_users(client: RemnawaveClient, *, limit: int = 500) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    page = 0
    size = min(max(limit, 1), 1000)
    while len(result) < limit:
        path = f'/api/users?page={page}&size={size}'
        _, data = await client._request('GET', path, expected_status=(200, 404))
        rows = _extract_list(data, ['users', 'data', 'items'])
        if not rows:
            break
        result.extend(rows)
        if len(rows) < size:
            break
        page += 1
    return result[:limit]


def _telegram_id(row: dict[str, Any]) -> int | None:
    raw = row.get('telegramId') or row.get('telegram_id')
    try:
        return int(raw) if raw not in (None, '') else None
    except Exception:
        return None


def _subscription_url(row: dict[str, Any]) -> str:
    return str(row.get('subscriptionUrl') or row.get('subscription_url') or row.get('subUrl') or '')


async def sync_remna_users_to_sqlite(db_path: str, users: list[dict[str, Any]]) -> dict[str, int]:
    imported = 0
    skipped = 0
    updated = 0
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute('''
            INSERT OR IGNORE INTO plans (slug,title,description,duration_days,traffic_gb,price_rub,is_active,sort_order,created_at,updated_at)
            VALUES ('remna-import','Remnawave import','Imported from Remnawave',30,0,0,0,999,?,?)
        ''', (ts, ts))
        cur = await db.execute("SELECT id FROM plans WHERE slug='remna-import'")
        plan_id = int((await cur.fetchone())['id'])
        for row in users:
            tg = _telegram_id(row)
            if not tg:
                skipped += 1
                continue
            username = row.get('username') or row.get('email') or ''
            full_name = row.get('email') or row.get('username') or f'Remnawave {tg}'
            await db.execute('''
                INSERT INTO users (telegram_id, username, full_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name, updated_at=excluded.updated_at
            ''', (tg, username, full_name, ts, ts))
            cur = await db.execute('SELECT id FROM users WHERE telegram_id=?', (tg,))
            user_id = int((await cur.fetchone())['id'])
            expire = row.get('expireAt') or row.get('expiresAt') or row.get('expire_at')
            if not expire:
                skipped += 1
                continue
            status = str(row.get('status') or 'ACTIVE').lower()
            sub_url = _subscription_url(row)
            rw_uuid = str(row.get('uuid') or row.get('id') or '')
            cur = await db.execute('SELECT id FROM subscriptions WHERE telegram_id=? AND remnawave_user_id=? ORDER BY id DESC LIMIT 1', (tg, rw_uuid))
            existing = await cur.fetchone()
            if existing:
                await db.execute('''
                    UPDATE subscriptions SET status=?, expires_at=?, subscription_url=?, updated_at=? WHERE id=?
                ''', ('active' if status == 'active' else status, expire, sub_url, ts, int(existing['id'])))
                updated += 1
            else:
                await db.execute('''
                    INSERT INTO subscriptions (user_id, telegram_id, plan_id, status, starts_at, expires_at, remnawave_user_id, subscription_url, traffic_limit_gb, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ''', (user_id, tg, plan_id, 'active' if status == 'active' else status, ts, expire, rw_uuid, sub_url, ts, ts))
                imported += 1
        await db.commit()
    return {'imported': imported, 'updated': updated, 'skipped': skipped, 'total': len(users)}
