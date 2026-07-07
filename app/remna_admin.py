from __future__ import annotations

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
        nested = data.get('data') or data.get('items') or data.get('records')
        if isinstance(nested, list):
            return [x for x in nested if isinstance(x, dict)]
    return []


def fmt_bytes(value: Any) -> str:
    try:
        n = float(value or 0)
    except Exception:
        return '-'
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if n < 1024 or unit == 'TB':
            return f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} TB'


def traffic_used(row: dict[str, Any]) -> int:
    keys = ['usedTrafficBytes', 'usedTraffic', 'trafficUsedBytes', 'trafficUsed', 'usedBytes']
    for key in keys:
        if row.get(key) is not None:
            try:
                return int(row[key])
            except Exception:
                return 0
    stat = row.get('traffic') or row.get('stat') or row.get('stats')
    if isinstance(stat, dict):
        for key in keys:
            if stat.get(key) is not None:
                try:
                    return int(stat[key])
                except Exception:
                    return 0
    return 0


def traffic_limit(row: dict[str, Any]) -> int:
    for key in ['trafficLimitBytes', 'trafficLimit', 'limitBytes']:
        if row.get(key) is not None:
            try:
                return int(row[key])
            except Exception:
                return 0
    return 0


def squads_text(row: dict[str, Any]) -> str:
    squads = row.get('activeInternalSquads') or row.get('internalSquads') or row.get('squads') or []
    if not squads:
        return '-'
    out = []
    for item in squads:
        if isinstance(item, dict):
            out.append(str(item.get('name') or item.get('uuid') or item.get('id') or '?'))
        else:
            out.append(str(item))
    return ', '.join(out[:5])


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


def remna_stats(users: list[dict[str, Any]]) -> dict[str, Any]:
    active = 0
    disabled = 0
    with_tg = 0
    used = 0
    limit = 0
    for row in users:
        status = str(row.get('status') or '').upper()
        if status == 'ACTIVE':
            active += 1
        else:
            disabled += 1
        if _telegram_id(row):
            with_tg += 1
        used += traffic_used(row)
        limit += traffic_limit(row)
    return {'total': len(users), 'active': active, 'disabled': disabled, 'with_tg': with_tg, 'traffic_used': used, 'traffic_limit': limit}


def _telegram_id(row: dict[str, Any]) -> int | None:
    raw = row.get('telegramId') or row.get('telegram_id')
    try:
        return int(raw) if raw not in (None, '') else None
    except Exception:
        return None


def _subscription_url(row: dict[str, Any]) -> str:
    return str(row.get('subscriptionUrl') or row.get('subscription_url') or row.get('subUrl') or '')


async def ensure_admin_plan_columns(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute('PRAGMA table_info(plans)')
        cols = {row[1] for row in await cur.fetchall()}
        if 'is_public' not in cols:
            await db.execute('ALTER TABLE plans ADD COLUMN is_public INTEGER NOT NULL DEFAULT 1')
        await db.execute("UPDATE plans SET is_public=0 WHERE slug='remna-import'")
        await db.commit()


async def list_admin_plans(db_path: str) -> list[dict[str, Any]]:
    await ensure_admin_plan_columns(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM plans WHERE is_public=0 ORDER BY sort_order,id') as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_plan_admin_only(db_path: str, plan_id: int, admin_only: bool) -> None:
    await ensure_admin_plan_columns(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute('UPDATE plans SET is_public=?, updated_at=? WHERE id=?', (0 if admin_only else 1, now_iso(), plan_id))
        await db.commit()


async def sync_remna_users_to_sqlite(db_path: str, users: list[dict[str, Any]]) -> dict[str, int]:
    await ensure_admin_plan_columns(db_path)
    imported = 0
    skipped = 0
    updated = 0
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute('''
            INSERT OR IGNORE INTO plans (slug,title,description,duration_days,traffic_gb,price_rub,is_active,sort_order,created_at,updated_at,is_public)
            VALUES ('remna-import','Remnawave import','Imported from Remnawave',30,0,0,0,999,?,?,0)
        ''', (ts, ts))
        await db.execute("UPDATE plans SET is_public=0 WHERE slug='remna-import'")
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
                await db.execute('UPDATE subscriptions SET status=?, expires_at=?, subscription_url=?, updated_at=? WHERE id=?', ('active' if status == 'active' else status, expire, sub_url, ts, int(existing['id'])))
                updated += 1
            else:
                await db.execute('''
                    INSERT INTO subscriptions (user_id, telegram_id, plan_id, status, starts_at, expires_at, remnawave_user_id, subscription_url, traffic_limit_gb, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ''', (user_id, tg, plan_id, 'active' if status == 'active' else status, ts, expire, rw_uuid, sub_url, ts, ts))
                imported += 1
        await db.commit()
    return {'imported': imported, 'updated': updated, 'skipped': skipped, 'total': len(users)}
