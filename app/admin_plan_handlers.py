from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app import runtime
from app.admin_db import add_plan
from app.keyboards import admin_plan_menu
from app.remna_admin import mark_plan_admin_only

router = Router()


class ServicePlanForm(StatesGroup):
    title = State()
    price = State()
    days = State()


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
    data = await state.get_data()
    title = data['title']
    price = int(data['price'])
    slug = re.sub(r'[^a-z0-9]+', '-', f'service-{title}-{days}-{price}'.lower()).strip('-') or f'service-{days}'
    plan_id = await add_plan(runtime.settings.db_path, slug=slug, title=title, description='Служебный тариф. Не показывается пользователям.', duration_days=days, price_rub=price, traffic_gb=0)
    await mark_plan_admin_only(runtime.settings.db_path, plan_id, True)
    await state.clear()
    await message.answer(
        f'✅ <b>Служебный тариф создан</b>\n\n'
        f'ID: <code>{plan_id}</code>\n'
        f'Название: <b>{title}</b>\n'
        f'Цена: <b>{price} ₽</b>\n'
        f'Срок: <b>{days} дн.</b>\n'
        f'Показ в продаже: <b>нет</b>',
        reply_markup=admin_plan_menu(plan_id, True),
    )
