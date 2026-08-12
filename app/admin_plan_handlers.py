from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import runtime
from app.admin_db import add_plan, delete_unused_plan, plan_usage_counts, update_plan
from app.db import get_plan_by_id
from app.keyboards import admin_plan_menu, admin_plans_menu
from app.db import list_plans
from app.remna_admin import mark_plan_admin_only

router = Router()


def plan_card_text(plan: dict) -> str:
    traffic = 'безлимит' if int(plan.get('traffic_gb') or 0) == 0 else f"{plan['traffic_gb']} ГБ"
    return (
        f"💰 <b>{plan['title']}</b>\n\n"
        f"Статус: <b>{'включён' if plan['is_active'] else 'выключен'}</b>\n"
        f"Тип: <b>{'публичный' if int(plan.get('is_public', 1)) else 'служебный'}</b>\n"
        f"Цена: <b>{plan['price_rub']} ₽</b>\n"
        f"Срок: <b>{plan['duration_days']} дней</b>\n"
        f"Трафик: <b>{traffic}</b>\n\n"
        f"{plan.get('description') or ''}"
    )


@router.callback_query(F.data.startswith('admin:plantoggle:'))
async def toggle_plan(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    plan_id = int(callback.data.rsplit(':', 1)[1])
    plan = await get_plan_by_id(runtime.settings.db_path, plan_id)
    if not plan:
        await callback.answer('Тариф не найден', show_alert=True)
        return
    await update_plan(runtime.settings.db_path, plan_id, 'is_active', 0 if plan['is_active'] else 1)
    plan = await get_plan_by_id(runtime.settings.db_path, plan_id)
    await callback.answer('Тариф выключен' if not plan['is_active'] else 'Тариф включён')
    await callback.message.answer(plan_card_text(plan), reply_markup=admin_plan_menu(plan_id, bool(plan['is_active'])))


@router.callback_query(F.data.startswith('admin:plandelete:ask:'))
async def ask_delete_plan(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    plan_id = int(callback.data.rsplit(':', 1)[1])
    plan = await get_plan_by_id(runtime.settings.db_path, plan_id)
    if not plan:
        await callback.answer('Тариф не найден', show_alert=True)
        return
    usage = await plan_usage_counts(runtime.settings.db_path, plan_id)
    if any(usage.values()):
        await callback.answer('Используемый тариф удалить нельзя', show_alert=True)
        await callback.message.answer(
            f"Тариф <b>{plan['title']}</b> используется:\n"
            f"Подписки: <b>{usage['subscriptions']}</b>\n"
            f"Платежи: <b>{usage['payments']}</b>\n"
            f"Бесплатные выдачи: <b>{usage['grants']}</b>\n\n"
            "Его можно выключить, чтобы скрыть из продажи."
        )
        return
    await callback.answer()
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🗑 Да, удалить', callback_data=f'admin:plandelete:confirm:{plan_id}')],
        [InlineKeyboardButton(text='← Отмена', callback_data=f'admin:plan:{plan_id}')],
    ])
    await callback.message.answer(f"⚠️ Удалить тариф <b>{plan['title']}</b>?\n\nЭто действие нельзя отменить.", reply_markup=markup)


@router.callback_query(F.data.startswith('admin:plandelete:confirm:'))
async def confirm_delete_plan(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    plan_id = int(callback.data.rsplit(':', 1)[1])
    deleted = await delete_unused_plan(runtime.settings.db_path, plan_id)
    if not deleted:
        await callback.answer('Тариф уже используется или удалён', show_alert=True)
        return
    await callback.answer('Тариф удалён')
    await callback.message.answer('🗑 Тариф удалён.', reply_markup=admin_plans_menu(await list_plans(runtime.settings.db_path, active_only=False)))


class ServicePlanForm(StatesGroup):
    title = State()
    price = State()
    days = State()
    traffic = State()


@router.callback_query(F.data == 'admin:planadd_service')
async def start_service_plan(callback: CallbackQuery, state: FSMContext) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer()
    await state.set_state(ServicePlanForm.title)
    await callback.message.answer('🔒 Название служебного тарифа?\n\nОн не будет показываться пользователям в покупке.')


@router.message(ServicePlanForm.title)
async def service_plan_title(message: Message, state: FSMContext) -> None:
    if not runtime.admin(message):
        return
    await state.update_data(title=message.text or 'Служебный тариф')
    await state.set_state(ServicePlanForm.price)
    await message.answer('Цена в рублях? Можно 0, если тариф только для ручной выдачи.')


@router.message(ServicePlanForm.price)
async def service_plan_price(message: Message, state: FSMContext) -> None:
    if not runtime.admin(message):
        return
    try:
        price = int(message.text or '0')
    except ValueError:
        await message.answer('Нужна цифра. Да, опять эти скучные числа.')
        return
    await state.update_data(price=price)
    await state.set_state(ServicePlanForm.days)
    await message.answer('Срок в днях?')


@router.message(ServicePlanForm.days)
async def service_plan_days(message: Message, state: FSMContext) -> None:
    if not runtime.admin(message):
        return
    try:
        days = int(message.text or '0')
    except ValueError:
        await message.answer('Нужна цифра.')
        return
    await state.update_data(days=days)
    await state.set_state(ServicePlanForm.traffic)
    await message.answer('Трафик в ГБ?\n\n0 = безлимит.')


@router.message(ServicePlanForm.traffic)
async def service_plan_traffic(message: Message, state: FSMContext) -> None:
    if not runtime.admin(message):
        return
    try:
        traffic_gb = int(message.text or '0')
    except ValueError:
        await message.answer('Нужна цифра. 0 = безлимит.')
        return
    data = await state.get_data()
    title = data['title']
    price = int(data['price'])
    days = int(data['days'])
    slug = re.sub(r'[^a-z0-9]+', '-', f'service-{title}-{days}-{price}'.lower()).strip('-') or f'service-{days}'
    plan_id = await add_plan(runtime.settings.db_path, slug=slug, title=title, description='Служебный тариф. Не показывается пользователям.', duration_days=days, price_rub=price, traffic_gb=traffic_gb)
    await mark_plan_admin_only(runtime.settings.db_path, plan_id, True)
    await state.clear()
    traffic_text = 'безлимит' if traffic_gb == 0 else f'{traffic_gb} ГБ'
    await message.answer(
        f'✅ <b>Служебный тариф создан</b>\n\n'
        f'ID: <code>{plan_id}</code>\n'
        f'Название: <b>{title}</b>\n'
        f'Цена: <b>{price} ₽</b>\n'
        f'Срок: <b>{days} дн.</b>\n'
        f'Трафик: <b>{traffic_text}</b>\n'
        f'Показ в продаже: <b>нет</b>',
        reply_markup=admin_plan_menu(plan_id, True),
    )
