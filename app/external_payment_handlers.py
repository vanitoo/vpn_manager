from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import runtime
from app.admin_db import has_used_trial
from app.db import add_payment, attach_subscription_to_payment, get_active_pending_payment, get_payment, get_plan_by_id, list_plans, mark_payment_paid, upsert_user
from app.keyboards import after_purchase_menu, external_payment_menu
from app.payments import build_provider
from app.payments.base import PaymentProviderError

router = Router()
log = logging.getLogger(__name__)
SUPPORTED_LOCAL_PROVIDERS = {'stars', 'yookassa', 'cryptomus'}


def payment_methods_keyboard(plan_id: int) -> InlineKeyboardMarkup:
    labels = {'stars': '⭐ Telegram Stars', 'yookassa': '💳 ЮKassa / СБП', 'cryptomus': '₿ Cryptomus'}
    providers = [p for p in runtime.settings.payment_providers if p in SUPPORTED_LOCAL_PROVIDERS]
    rows = [[InlineKeyboardButton(text=labels.get(provider, provider), callback_data=f'pay:{provider}:{plan_id}')] for provider in providers]
    rows.append([InlineKeyboardButton(text='← К тарифу', callback_data=f'plan:{plan_id}')])
    rows.append([InlineKeyboardButton(text='⌂ Главное', callback_data='home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith('buy:'))
async def choose_payment(callback: CallbackQuery) -> None:
    await callback.answer()
    plan_id = int(callback.data.split(':', 1)[1])
    await callback.message.answer('Выберите способ оплаты:', reply_markup=payment_methods_keyboard(plan_id))


@router.callback_query(F.data == 'trial')
async def activate_trial(callback: CallbackQuery) -> None:
    await callback.answer('Создаю тестовый доступ')
    if await has_used_trial(runtime.settings.db_path, callback.from_user.id):
        await callback.message.answer('Тестовый доступ уже использован.')
        return
    plans = await list_plans(runtime.settings.db_path, active_only=True)
    if not plans:
        await callback.message.answer('Нет активного тарифа для тестового доступа.')
        return
    user = await upsert_user(runtime.settings.db_path, telegram_id=callback.from_user.id, username=callback.from_user.username, full_name=callback.from_user.full_name)
    plan = {**plans[0], 'duration_days': runtime.TRIAL_DAYS}
    subscription = await runtime.provision(user, callback.from_user.id, plan, True)
    url = subscription.get('subscription_url') or ''
    await callback.message.answer(
        '🎁 <b>Тестовый доступ на 24 часа активирован.</b>\n\nНажмите кнопку ниже, чтобы открыть VPN-подписку.',
        reply_markup=after_purchase_menu(url),
    )


@router.message(F.successful_payment)
async def stars_paid(message: Message) -> None:
    payload = message.successful_payment.invoice_payload
    if not payload.startswith('vpn:'):
        return
    plan_id = int(payload.split(':', 1)[1])
    plan = await get_plan_by_id(runtime.settings.db_path, plan_id)
    user = await upsert_user(runtime.settings.db_path, telegram_id=message.from_user.id, username=message.from_user.username, full_name=message.from_user.full_name)
    subscription = await runtime.provision(user, message.from_user.id, plan)
    await add_payment(
        runtime.settings.db_path,
        provider='stars',
        provider_payment_id=message.successful_payment.telegram_payment_charge_id,
        user_id=int(user['id']),
        telegram_id=message.from_user.id,
        plan_id=plan_id,
        amount_rub=int(plan['price_rub']),
        currency='XTR',
        status='paid',
        telegram_payment_charge_id=message.successful_payment.telegram_payment_charge_id,
    )
    url = subscription.get('subscription_url') or ''
    await message.answer(
        '✅ <b>Оплата получена. VPN активирован.</b>\n\nНажмите кнопку ниже, чтобы открыть VPN-подписку.',
        reply_markup=after_purchase_menu(url),
    )


@router.callback_query(F.data.startswith('pay:yookassa:') | F.data.startswith('pay:cryptomus:'))
async def create_external_payment(callback: CallbackQuery) -> None:
    await callback.answer('Создаю платёж')
    _, provider_name, plan_id_raw = callback.data.split(':', 2)
    plan_id = int(plan_id_raw)
    plan = await get_plan_by_id(runtime.settings.db_path, plan_id)
    if not plan or not int(plan.get('is_active', 0)):
        await callback.message.answer('Тариф не найден или отключён.')
        return
    provider = build_provider(provider_name, runtime.settings)
    if not provider.is_configured:
        await callback.message.answer(f'Способ оплаты {provider_name} включён, но реквизиты не настроены в .env.')
        return
    user = await upsert_user(runtime.settings.db_path, telegram_id=callback.from_user.id, username=callback.from_user.username, full_name=callback.from_user.full_name)
    pending = await get_active_pending_payment(runtime.settings.db_path, telegram_id=callback.from_user.id, plan_id=plan_id, ttl_minutes=runtime.settings.pending_payment_ttl_minutes)
    if pending and pending.get('provider') == provider_name and pending.get('payment_url'):
        await callback.message.answer('У вас уже есть незавершённый платёж.', reply_markup=external_payment_menu(int(pending['id']), pending['payment_url'], plan_id))
        return
    receipt_customer = {'email': runtime.settings.receipt_fallback_email} if provider_name == 'yookassa' and runtime.settings.receipt_fallback_email else None
    try:
        created = await provider.create_payment(
            amount_rub=int(plan['price_rub']),
            description=f"VPN: {plan['title']} на {plan['duration_days']} дней",
            return_url=runtime.settings.yookassa_return_url if provider_name == 'yookassa' else runtime.settings.cryptomus_return_url,
            metadata={'telegram_id': callback.from_user.id, 'plan_id': plan_id, 'user_id': user['id']},
            receipt_customer=receipt_customer,
        )
    except PaymentProviderError as exc:
        log.exception('Payment create failed: provider=%s', provider_name)
        await callback.message.answer(f'Не удалось создать платёж: <code>{str(exc)[:1000]}</code>')
        return
    payment_id = await add_payment(
        runtime.settings.db_path, provider=provider_name, provider_payment_id=created.provider_payment_id,
        user_id=int(user['id']), telegram_id=callback.from_user.id, plan_id=plan_id,
        amount_rub=int(plan['price_rub']), currency='RUB', status='pending',
        payment_url=created.payment_url, payload=created.raw,
    )
    await callback.message.answer(
        f"💳 <b>{plan['title']}</b>\n\nСумма: <b>{plan['price_rub']} ₽</b>\nПосле оплаты нажмите «Я оплатил».",
        reply_markup=external_payment_menu(payment_id, created.payment_url, plan_id),
    )


@router.callback_query(F.data.startswith('epay:check:'))
async def check_external_payment(callback: CallbackQuery) -> None:
    await callback.answer('Проверяю оплату')
    payment_id = int(callback.data.rsplit(':', 1)[1])
    payment = await get_payment(runtime.settings.db_path, payment_id)
    if not payment or int(payment['telegram_id']) != callback.from_user.id:
        await callback.message.answer('Платёж не найден.')
        return
    if payment.get('subscription_id'):
        await callback.message.answer('✅ Этот платёж уже обработан.', reply_markup=after_purchase_menu())
        return
    provider = build_provider(payment['provider'], runtime.settings)
    try:
        status = await provider.get_status(payment['provider_payment_id'])
    except PaymentProviderError as exc:
        await callback.message.answer(f'Не удалось проверить платёж: <code>{str(exc)[:1000]}</code>')
        return
    if not status.paid:
        await callback.message.answer(
            f"Оплата пока не подтверждена. Статус: <b>{status.status or 'pending'}</b>",
            reply_markup=external_payment_menu(payment_id, payment['payment_url'], int(payment['plan_id'])),
        )
        return
    plan = await get_plan_by_id(runtime.settings.db_path, int(payment['plan_id']))
    user = await upsert_user(runtime.settings.db_path, telegram_id=callback.from_user.id, username=callback.from_user.username, full_name=callback.from_user.full_name)
    try:
        subscription = await runtime.provision(user, callback.from_user.id, plan)
    except Exception as exc:
        log.exception('Paid payment provisioning failed: payment_id=%s', payment_id)
        await callback.message.answer('Оплата подтверждена, но Remnawave не выдал конфигурацию. Платёж сохранён, обратитесь к администратору.\n\n' f'<code>{str(exc)[:800]}</code>')
        return
    await mark_payment_paid(runtime.settings.db_path, payment_id, provider_status='paid')
    await attach_subscription_to_payment(runtime.settings.db_path, payment_id, int(subscription['id']))
    url = subscription.get('subscription_url') or ''
    await callback.message.answer(
        '✅ <b>Оплата подтверждена. VPN активирован.</b>\n\n'
        f"Доступ до: <b>{subscription['expires_at'][:16]}</b>\n\n"
        'Нажмите кнопку ниже, чтобы открыть подписку.',
        reply_markup=after_purchase_menu(url),
    )
    if runtime.settings.admin_notify_purchases:
        for admin_id in runtime.settings.admin_ids:
            try:
                await callback.bot.send_message(admin_id, f"💰 Оплата {payment['provider']}\nПользователь: <code>{callback.from_user.id}</code>\nТариф: <b>{plan['title']}</b>\nСумма: <b>{payment['amount_rub']} ₽</b>")
            except Exception:
                log.exception('Cannot notify admin %s about purchase', admin_id)
