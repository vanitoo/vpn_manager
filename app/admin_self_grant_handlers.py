from __future__ import annotations

from aiogram import Router
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app import runtime
from app.admin_db import create_access_grant, get_active_access_grant
from app.db import add_subscription, get_plan_by_id, upsert_user
from app.keyboards import admin_user_menu
from app.remna_admin import list_admin_plans
from app.remnawave import RemnawaveClient

router = Router()


def esc(value: object) -> str:
    return str(value or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _self_target(callback: CallbackQuery, prefix: str) -> int | None:
    if not callback.from_user or not callback.data or not callback.data.startswith(prefix):
        return None
    try:
        if prefix.endswith('grant:'):
            target = int(callback.data.split(':', 4)[3])
        else:
            target = int(callback.data.rsplit(':', 1)[1])
    except (TypeError, ValueError, IndexError):
        return None
    if target != callback.from_user.id or not runtime.admin(callback):
        return None
    return target


class AdminSelfPlans(Filter):
    async def __call__(self, callback: CallbackQuery) -> bool:
        return _self_target(callback, 'admin:friend:plans:') is not None


class AdminSelfGrant(Filter):
    async def __call__(self, callback: CallbackQuery) -> bool:
        return _self_target(callback, 'admin:friend:grant:') is not None


@router.callback_query(AdminSelfPlans())
async def admin_self_access_plans(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    if await get_active_access_grant(runtime.settings.db_path, telegram_id):
        await callback.answer('Служебный доступ уже активен', show_alert=True)
        return

    plans = [
        plan for plan in await list_admin_plans(runtime.settings.db_path)
        if int(plan.get('is_active', 0)) and int(plan.get('is_public', 1)) == 0
    ]
    if not plans:
        await callback.answer('Сначала создайте служебный тариф', show_alert=True)
        return

    rows = []
    for plan in plans:
        traffic = 'безлимит' if int(plan.get('traffic_gb') or 0) == 0 else f"{plan['traffic_gb']} ГБ"
        rows.append([InlineKeyboardButton(
            text=f"{plan['title']} · {plan['duration_days']} дн. · {traffic}"[:60],
            callback_data=f"admin:friend:grant:{telegram_id}:{plan['id']}",
        )])
    rows.append([InlineKeyboardButton(text='← Пользователи', callback_data='admin:users')])

    await callback.answer()
    await callback.message.answer(
        '🛠 <b>Служебный тариф для администратора</b>\n\n'
        'Можно выдать его самому себе даже при уже активном VPN-доступе.\n\n'
        '⚠️ В Remnawave существующий пользователь будет продлён на срок выбранного тарифа. '
        'Отдельная оплата не создаётся.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(AdminSelfGrant())
async def admin_self_access_grant(callback: CallbackQuery) -> None:
    _, _, _, telegram_raw, plan_raw = callback.data.split(':', 4)
    telegram_id, plan_id = int(telegram_raw), int(plan_raw)

    if await get_active_access_grant(runtime.settings.db_path, telegram_id):
        await callback.answer('Служебный доступ уже активен', show_alert=True)
        return

    plan = await get_plan_by_id(runtime.settings.db_path, plan_id)
    if not plan or int(plan.get('is_public', 1)) != 0 or not int(plan.get('is_active', 0)):
        await callback.answer('Служебный тариф недоступен', show_alert=True)
        return

    await callback.answer('Выдаю служебный тариф…')
    user = await upsert_user(
        runtime.settings.db_path,
        telegram_id=telegram_id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
    )

    try:
        access = await RemnawaveClient(runtime.settings).create_or_extend_user(
            telegram_id=telegram_id,
            username=user.get('username'),
            duration_days=int(plan['duration_days']),
            traffic_gb=int(plan.get('traffic_gb') or 0),
        )
        subscription_id = await add_subscription(
            runtime.settings.db_path,
            user_id=int(user['id']),
            telegram_id=telegram_id,
            plan_id=plan_id,
            duration_days=int(plan['duration_days']),
            traffic_limit_gb=int(plan.get('traffic_gb') or 0),
            remnawave_user_id=access.remnawave_user_id,
            subscription_url=access.subscription_url,
        )
        await create_access_grant(
            runtime.settings.db_path,
            telegram_id=telegram_id,
            subscription_id=subscription_id,
            plan_id=plan_id,
            granted_by=telegram_id,
        )
    except Exception as exc:
        await callback.message.answer(
            '❌ Не удалось выдать служебный тариф:\n'
            f'<code>{esc(type(exc).__name__ + ": " + str(exc))[:1500]}</code>'
        )
        return

    await callback.message.answer(
        '✅ <b>Служебный тариф выдан администратору</b>\n\n'
        f'Telegram ID: <code>{telegram_id}</code>\n'
        f'Тариф: <b>{esc(plan["title"])}</b>\n'
        f'Срок: <b>{plan["duration_days"]} дней</b>\n'
        'Оплата: <b>не требуется</b>',
        reply_markup=admin_user_menu(telegram_id, True, True, access.remnawave_user_id),
    )
