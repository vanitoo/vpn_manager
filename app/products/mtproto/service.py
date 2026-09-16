from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from dataclasses import dataclass
from urllib.parse import urlencode

from app import runtime
from app.products.mtproto.client import MTProtoControlClient
from app.products.mtproto.storage import (
    ensure_access_row,
    get_access,
    get_paid_entitlement,
    list_accesses,
    list_paid_entitled_telegram_ids,
    rotate_generation,
    set_access_state,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MTProtoAccessView:
    telegram_id: int
    status: str
    generation: int
    secret: str
    public_secret: str
    connect_url: str
    entitlement: dict | None
    last_error: str = ''


def control_client() -> MTProtoControlClient:
    return MTProtoControlClient(
        base_url=runtime.settings.mtproto_control_url,
        token=runtime.settings.mtproto_control_token,
        dry_run=runtime.settings.mtproto_dry_run,
    )


def configuration_error() -> str:
    settings = runtime.settings
    if not settings.mtproto_enabled:
        return 'MTProto feature is disabled'
    if not settings.mtproto_secret_key:
        return 'MTPROTO_SECRET_KEY is empty'
    if not settings.mtproto_public_host:
        return 'MTPROTO_PUBLIC_HOST is empty'
    if settings.mtproto_public_port <= 0:
        return 'MTPROTO_PUBLIC_PORT is invalid'
    if not settings.mtproto_dry_run and not settings.mtproto_control_url:
        return 'MTPROTO_CONTROL_URL is empty'
    return ''


def _admin_entitlement(telegram_id: int) -> dict | None:
    admin_ids = {int(value) for value in (getattr(runtime.settings, 'admin_ids', ()) or ())}
    if int(telegram_id) not in admin_ids:
        return None
    return {
        'telegram_id': int(telegram_id),
        'plan_title': 'Администратор',
        'plan_slug': 'admin-override',
        'mtproto_enabled': 1,
        'entitlement_source': 'admin',
        'admin_override': 1,
        'status': 'active',
    }


async def entitlement_for(db_path: str, telegram_id: int) -> dict | None:
    """Return entitlement from payment, friend grant, or explicit admin override."""
    subscription_entitlement = await get_paid_entitlement(db_path, telegram_id)
    if subscription_entitlement:
        return subscription_entitlement
    return _admin_entitlement(telegram_id)


async def entitled_telegram_ids(db_path: str) -> list[int]:
    ids = set(await list_paid_entitled_telegram_ids(db_path))
    ids.update(int(value) for value in (getattr(runtime.settings, 'admin_ids', ()) or ()))
    return sorted(ids)


def secret_for(telegram_id: int, generation: int) -> str:
    key = runtime.settings.mtproto_secret_key.encode('utf-8')
    msg = f'mtproto:{int(telegram_id)}:{int(generation)}'.encode('utf-8')
    return hmac.new(key, msg, hashlib.sha256).digest()[:16].hex()


def public_secret(base_secret: str) -> str:
    return f'dd{base_secret}' if runtime.settings.mtproto_random_padding else base_secret


def connect_url(secret: str) -> str:
    query = urlencode({
        'server': runtime.settings.mtproto_public_host,
        'port': runtime.settings.mtproto_public_port,
        'secret': secret,
    })
    return f'https://t.me/proxy?{query}'


async def access_view(db_path: str, telegram_id: int) -> MTProtoAccessView | None:
    row = await get_access(db_path, telegram_id)
    if not row:
        return None
    generation = int(row.get('generation') or 1)
    base_secret = secret_for(telegram_id, generation)
    shown_secret = public_secret(base_secret)
    return MTProtoAccessView(
        telegram_id=telegram_id,
        status=str(row.get('status') or 'disabled'),
        generation=generation,
        secret=base_secret,
        public_secret=shown_secret,
        connect_url=connect_url(shown_secret),
        entitlement=await entitlement_for(db_path, telegram_id),
        last_error=str(row.get('last_error') or ''),
    )


async def ensure_enabled(db_path: str, telegram_id: int) -> MTProtoAccessView:
    error = configuration_error()
    if error:
        raise RuntimeError(error)
    entitlement = await entitlement_for(db_path, telegram_id)
    if not entitlement:
        raise PermissionError('MTProto доступен только при активном тарифе с включённой опцией Telegram Proxy')

    row = await ensure_access_row(db_path, telegram_id)
    generation = int(row.get('generation') or 1)
    secret = secret_for(telegram_id, generation)
    try:
        await control_client().apply(telegram_id=telegram_id, secret=secret, enabled=True)
        await set_access_state(db_path, telegram_id, 'active')
    except Exception as exc:
        await set_access_state(db_path, telegram_id, 'error', error=f'{type(exc).__name__}: {exc}')
        raise
    view = await access_view(db_path, telegram_id)
    if not view:
        raise RuntimeError('MTProto access disappeared after provisioning')
    return view


async def disable(db_path: str, telegram_id: int) -> None:
    row = await get_access(db_path, telegram_id)
    if not row:
        return
    generation = int(row.get('generation') or 1)
    secret = secret_for(telegram_id, generation)
    try:
        await control_client().apply(telegram_id=telegram_id, secret=secret, enabled=False)
        await set_access_state(db_path, telegram_id, 'disabled')
    except Exception as exc:
        await set_access_state(db_path, telegram_id, 'error', error=f'{type(exc).__name__}: {exc}')
        raise


async def rotate(db_path: str, telegram_id: int) -> MTProtoAccessView:
    error = configuration_error()
    if error:
        raise RuntimeError(error)
    entitlement = await entitlement_for(db_path, telegram_id)
    if not entitlement:
        raise PermissionError('Нет активного права на MTProto')

    old = await ensure_access_row(db_path, telegram_id)
    old_generation = int(old.get('generation') or 1)
    old_secret = secret_for(telegram_id, old_generation)
    row = await rotate_generation(db_path, telegram_id)
    new_generation = int(row.get('generation') or 1)
    new_secret = secret_for(telegram_id, new_generation)
    client = control_client()
    try:
        # The controller contract is idempotent by telegram_id: enabling with a new
        # secret must replace the old credential atomically on the proxy side.
        await client.apply(telegram_id=telegram_id, secret=new_secret, enabled=True)
        await set_access_state(db_path, telegram_id, 'active')
    except Exception as exc:
        # Restore the previous generation locally and best-effort re-enable it.
        import aiosqlite
        from app.products.mtproto.storage import now_iso

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE mtproto_access SET generation=?, status='error', updated_at=?, last_error=? WHERE telegram_id=?",
                (old_generation, now_iso(), f'{type(exc).__name__}: {exc}'[:1000], telegram_id),
            )
            await db.commit()
        try:
            await client.apply(telegram_id=telegram_id, secret=old_secret, enabled=True)
        except Exception:
            pass
        raise
    view = await access_view(db_path, telegram_id)
    if not view:
        raise RuntimeError('MTProto access disappeared after rotation')
    return view


async def reconcile_once(db_path: str) -> dict[str, int]:
    result = {'enabled': 0, 'disabled': 0, 'created': 0, 'errors': 0}
    if configuration_error():
        return result

    if runtime.settings.mtproto_auto_create:
        existing_ids = {int(row['telegram_id']) for row in await list_accesses(db_path)}
        for telegram_id in await entitled_telegram_ids(db_path):
            if telegram_id not in existing_ids:
                await ensure_access_row(db_path, telegram_id)
                result['created'] += 1

    for row in await list_accesses(db_path):
        telegram_id = int(row['telegram_id'])
        entitlement = await entitlement_for(db_path, telegram_id)
        status = str(row.get('status') or 'disabled')
        try:
            if entitlement:
                if status != 'active':
                    await ensure_enabled(db_path, telegram_id)
                    result['enabled'] += 1
            elif status != 'disabled':
                await disable(db_path, telegram_id)
                result['disabled'] += 1
        except Exception:
            result['errors'] += 1
            log.exception('MTProto reconcile failed for telegram_id=%s', telegram_id)
    return result


async def reconcile_loop(db_path: str) -> None:
    interval = max(30, int(runtime.settings.mtproto_reconcile_interval_seconds))
    log.info('MTProto reconcile loop started: interval=%ss auto_create=%s', interval, runtime.settings.mtproto_auto_create)
    while True:
        try:
            result = await reconcile_once(db_path)
            if any(result.values()):
                log.info('MTProto reconcile: %s', result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('MTProto reconcile iteration failed')
        await asyncio.sleep(interval)
