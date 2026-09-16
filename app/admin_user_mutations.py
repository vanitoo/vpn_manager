from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from aiogram import F, Router
from aiogram.types import CallbackQuery

from app import runtime
from app.admin_db import (
    get_active_access_grant,
    has_other_active_subscription,
    revoke_access_grant,
)
from app.admin_remna_handlers import PENDING_ACTIONS, remna_user_menu
from app.keyboards import admin_user_menu
from app.remna_admin import remna_users
from app.remnawave import RemnawaveClient

router = Router()


def esc(value: Any) -> str:
    return str(value or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def remote_ref(row: dict[str, Any]) -> str:
    return str(row.get('id') or row.get('uuid') or '')


def remote_tg(row: dict[str, Any]) -> int | None:
    raw = row.get('telegramId') or row.get('telegram_id')
    try:
        if raw not in (None, ''):
            return int(raw)
    except (TypeError, ValueError):
        pass
    for value in (row.get('email'), row.get('username')):
        match = re.search(r'tg(\d+)@', str(value or ''), flags=re.I)
        if match:
            return int(match.group(1))
    return None


def remote_expire(row: dict[str, Any]) -> float:
    value = row.get('expireAt') or row.get('expiresAt') or row.get('expire_at')
    if not value:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def remote_rank(row: dict[str, Any]) -> tuple[float, int]:
    try:
        numeric_id = int(row.get('id') or 0)
    except (TypeError, ValueError):
        numeric_id = 0
    return remote_expire(row), numeric_id


def squad_ids(row: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    values = row.get('activeInternalSquads') or row.get('internalSquads') or []
    if isinstance(values, dict):
        values = values.get('squads') or values.get('items') or []
    if not isinstance(values, list):
        values = [values]
    for item in values:
        if isinstance(item, dict):
            value = item.get('uuid') or item.get('id') or item.get('squadUuid')
        else:
            value = item
        if value not in (None, ''):
            result.add(str(value))
    return result


async def latest_local_subscription(telegram_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(runtime.settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            '''
            SELECT id, telegram_id, status, expires_at, remnawave_user_id
            FROM subscriptions
            WHERE telegram_id=?
            ORDER BY id DESC
            LIMIT 1
            ''',
            (telegram_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def set_local_blocked(telegram_id: int) -> int:
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(runtime.settings.db_path) as db:
        cur = await db.execute(
            "UPDATE subscriptions SET status='blocked', updated_at=? WHERE telegram_id=? AND status='active'",
            (ts, telegram_id),
        )
        await db.commit()
        return int(cur.rowcount or 0)


async def set_local_active(telegram_id: int) -> int:
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(runtime.settings.db_path) as db:
        cur = await db.execute(
            '''
            UPDATE subscriptions
            SET status='active', updated_at=?
            WHERE id=(
                SELECT id FROM subscriptions
                WHERE telegram_id=? AND status='blocked'
                ORDER BY id DESC LIMIT 1
            )
            ''',
            (ts, telegram_id),
        )
        await db.commit()
        return int(cur.rowcount or 0)


async def all_remote_users(client: RemnawaveClient) -> list[dict[str, Any]]:
    return await remna_users(client, limit=3000)


async def remote_by_ref(client: RemnawaveClient, user_ref: str) -> dict[str, Any] | None:
    for row in await all_remote_users(client):
        if remote_ref(row) == str(user_ref):
            return row
    return None


async def remote_action(client: RemnawaveClient, user_ref: str, action: str) -> None:
    if action not in {'disable', 'enable'}:
        raise ValueError(f'Unsupported Remnawave user action: {action}')
    major = await client.api_major()
    if major >= 3:
        try:
            path_ref = str(int(user_ref))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f'Remnawave v3 requires numeric user id, got {user_ref!r}') from exc
    else:
        path_ref = str(user_ref)
    await client._request(  # noqa: SLF001 - centralized compatibility client request wrapper
        'POST',
        f'/api/users/{path_ref}/actions/{action}',
        expected_status=(200, 201, 204),
    )


async def verify_remote_status(
    client: RemnawaveClient,
    user_ref: str,
    expected: str,
    *,
    attempts: int = 4,
) -> dict[str, Any]:
    expected = expected.upper()
    last: dict[str, Any] | None = None
    for attempt in range(attempts):
        last = await remote_by_ref(client, user_ref)
        if last and str(last.get('status') or '').upper() == expected:
            return last
        if attempt + 1 < attempts:
            await asyncio.sleep(0.35)
    actual = str((last or {}).get('status') or 'NOT_FOUND')
    raise RuntimeError(f'Remnawave verification failed for user {user_ref}: expected {expected}, got {actual}')


async def exact_remote_refs_for_telegram(
    client: RemnawaveClient,
    telegram_id: int,
    preferred_ref: str = '',
) -> tuple[list[str], list[dict[str, Any]]]:
    rows = await all_remote_users(client)
    exact = [row for row in rows if remote_tg(row) == telegram_id]
    refs = [remote_ref(row) for row in exact if remote_ref(row)]
    if preferred_ref and preferred_ref not in refs:
        if any(remote_ref(row) == preferred_ref for row in rows):
            refs.append(preferred_ref)
    return list(dict.fromkeys(refs)), exact


async def choose_primary_ref(
    client: RemnawaveClient,
    telegram_id: int,
    preferred_ref: str = '',
) -> str:
    rows = await all_remote_users(client)
    if preferred_ref and any(remote_ref(row) == preferred_ref for row in rows):
        return preferred_ref
    exact = [row for row in rows if remote_tg(row) == telegram_id and remote_ref(row)]
    if not exact:
        return ''
    return remote_ref(max(exact, key=remote_rank))


@router.callback_query(F.data.startswith('admin:block:'))
async def block_linked_user(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    telegram_id = int(callback.data.rsplit(':', 1)[1])
    await callback.answer('Блокирую в Remnawave…')
    local = await latest_local_subscription(telegram_id)
    preferred_ref = str((local or {}).get('remnawave_user_id') or '')
    client = RemnawaveClient(runtime.settings)
    try:
        refs, _ = await exact_remote_refs_for_telegram(client, telegram_id, preferred_ref)
        if not refs and preferred_ref:
            raise RuntimeError(f'Сохранённый Remnawave ID {preferred_ref} не найден в панели')
        for ref in refs:
            await remote_action(client, ref, 'disable')
            await verify_remote_status(client, ref, 'DISABLED')
        changed = await set_local_blocked(telegram_id)
    except Exception as exc:
        await callback.message.answer(
            '❌ <b>Блокировка не выполнена</b>\n\n'
            'Локальный статус не изменён, потому что Remnawave не подтвердил операцию.\n\n'
            f'<code>{esc(type(exc).__name__ + ": " + str(exc))[:1400]}</code>'
        )
        return
    suffix = f'\nRemnawave записей отключено: <b>{len(refs)}</b>' if refs else '\nПользователь не связан с Remnawave — изменён только Bot.'
    await callback.message.answer(
        f'🚫 <b>Пользователь заблокирован</b>\n\nTelegram ID: <code>{telegram_id}</code>'
        f'{suffix}\nЛокальных подписок заблокировано: <b>{changed}</b>',
        reply_markup=admin_user_menu(telegram_id, False),
    )


@router.callback_query(F.data.startswith('admin:activate:'))
async def activate_linked_user(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    telegram_id = int(callback.data.rsplit(':', 1)[1])
    await callback.answer('Активирую в Remnawave…')
    local = await latest_local_subscription(telegram_id)
    preferred_ref = str((local or {}).get('remnawave_user_id') or '')
    client = RemnawaveClient(runtime.settings)
    try:
        ref = await choose_primary_ref(client, telegram_id, preferred_ref)
        if preferred_ref and not ref:
            raise RuntimeError(f'Сохранённый Remnawave ID {preferred_ref} не найден в панели')
        if ref:
            await remote_action(client, ref, 'enable')
            await verify_remote_status(client, ref, 'ACTIVE')
        changed = await set_local_active(telegram_id)
    except Exception as exc:
        await callback.message.answer(
            '❌ <b>Активация не выполнена</b>\n\n'
            'Локальный статус не изменён, потому что Remnawave не подтвердил операцию.\n\n'
            f'<code>{esc(type(exc).__name__ + ": " + str(exc))[:1400]}</code>'
        )
        return
    suffix = f'\nRemnawave ID: <code>{esc(ref)}</code>' if ref else '\nПользователь не связан с Remnawave — изменён только Bot.'
    await callback.message.answer(
        f'✅ <b>Пользователь активирован</b>\n\nTelegram ID: <code>{telegram_id}</code>'
        f'{suffix}\nЛокальных подписок активировано: <b>{changed}</b>\n\n'
        'Срок подписки этой кнопкой не продлевается.',
        reply_markup=admin_user_menu(telegram_id, True),
    )


@router.callback_query(F.data.startswith('admin:remna:block:'))
async def block_direct_remna_user(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    user_ref = callback.data.split(':', 3)[3]
    await callback.answer('Блокирую в Remnawave…')
    client = RemnawaveClient(runtime.settings)
    try:
        await remote_action(client, user_ref, 'disable')
        row = await verify_remote_status(client, user_ref, 'DISABLED')
        tg = remote_tg(row)
        if tg:
            await set_local_blocked(tg)
    except Exception as exc:
        await callback.message.answer(f'❌ Ошибка блокировки: <code>{esc(type(exc).__name__ + ": " + str(exc))[:1400]}</code>')
        return
    await callback.message.answer('🚫 Пользователь заблокирован в Remnawave и синхронизирован с Bot.', reply_markup=remna_user_menu(user_ref))


@router.callback_query(F.data.startswith('admin:remna:activate:'))
async def activate_direct_remna_user(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    user_ref = callback.data.split(':', 3)[3]
    await callback.answer('Активирую в Remnawave…')
    client = RemnawaveClient(runtime.settings)
    try:
        await remote_action(client, user_ref, 'enable')
        row = await verify_remote_status(client, user_ref, 'ACTIVE')
        tg = remote_tg(row)
        if tg:
            await set_local_active(tg)
    except Exception as exc:
        await callback.message.answer(f'❌ Ошибка активации: <code>{esc(type(exc).__name__ + ": " + str(exc))[:1400]}</code>')
        return
    await callback.message.answer('✅ Пользователь активирован в Remnawave и синхронизирован с Bot.', reply_markup=remna_user_menu(user_ref))


@router.callback_query(F.data.startswith('admin:remna:set_squad:'))
async def set_direct_remna_squad(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    key = callback.data.split(':', 3)[3]
    user_ref, squad_id = PENDING_ACTIONS.pop(key, ('', ''))
    if not user_ref or not squad_id:
        await callback.answer('Действие устарело', show_alert=True)
        return
    await callback.answer('Меняю squad в Remnawave…')
    client = RemnawaveClient(runtime.settings)
    try:
        await client.patch_user(user_ref, {'activeInternalSquads': [squad_id]})
        row = await remote_by_ref(client, user_ref)
        if not row or squad_id not in squad_ids(row):
            raise RuntimeError('Remnawave не подтвердил новый Internal Squad после PATCH')
    except Exception as exc:
        await callback.message.answer(f'❌ Ошибка смены squad: <code>{esc(type(exc).__name__ + ": " + str(exc))[:1400]}</code>')
        return
    await callback.message.answer(
        f'✅ Squad изменён и проверен в Remnawave.\n<code>{esc(squad_id)}</code>\n\n'
        'Статус пользователя при смене squad теперь не меняется.',
        reply_markup=remna_user_menu(user_ref),
    )


@router.callback_query(F.data.startswith('admin:friend:revoke:'))
async def revoke_friend_access_safely(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    telegram_id = int(callback.data.rsplit(':', 1)[1])
    grant = await get_active_access_grant(runtime.settings.db_path, telegram_id)
    if not grant:
        await callback.answer('Активная бесплатная выдача не найдена', show_alert=True)
        return
    user_ref = str(grant.get('remnawave_user_id') or '')
    has_other_access = await has_other_active_subscription(
        runtime.settings.db_path,
        telegram_id,
        int(grant['subscription_id']),
    )
    await callback.answer('Отключаю доступ…')
    if user_ref and not has_other_access:
        client = RemnawaveClient(runtime.settings)
        try:
            await remote_action(client, user_ref, 'disable')
            await verify_remote_status(client, user_ref, 'DISABLED')
        except Exception as exc:
            await callback.message.answer(
                '❌ <b>Доступ не отключён</b>\n\n'
                'Remnawave не подтвердил блокировку, поэтому локальная выдача сохранена.\n\n'
                f'<code>{esc(type(exc).__name__ + ": " + str(exc))[:1400]}</code>'
            )
            return
    revoked = await revoke_access_grant(runtime.settings.db_path, telegram_id)
    if not revoked:
        await callback.message.answer('⚠️ Remnawave обновлён, но локальная выдача уже отсутствовала.')
        return
    await callback.message.answer(
        f'⛔ Бесплатный доступ пользователя <code>{telegram_id}</code> отключён'
        + (' в Remnawave и Bot.' if user_ref and not has_other_access else ' в Bot; другая активная подписка Remnawave сохранена.'),
        reply_markup=admin_user_menu(telegram_id, False, False),
    )
