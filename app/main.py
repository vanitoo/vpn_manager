from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from app.config import Settings, get_settings
from app.db import (
    add_payment,
    add_subscription,
    attach_subscription_to_payment,
    get_active_pending_payment,
    get_active_subscription,
    get_payment,
    get_plan_by_id,
    get_stats,
    init_db,
    list_plans,
    mark_payment_paid,
    receipt_customer_from_contact,
    seed_plans,
    update_subscription_access,
    upsert_user,
)
from app.keyboards import (
    admin_menu,
    after_purchase_menu,
    external_payment_menu,
    main_menu,
    my_vpn_menu,
    payment_methods_menu,
    plan_menu,
    plans_menu,
)
from app.payments import build_provider
from app.payments.base import PaymentProviderError
from app.proxy_manager import ProxyManager
from app.remnawave import RemnawaveClient
from app.version import APP_NAME, APP_VERSION, DB_SCHEMA_VERSION, version_line

router = Router()
settings: Settings
proxy_manager: ProxyManager | None = None
provider_runtime_errors: dict[str, str] = {}
provider_runtime_disabled: set[str] = set()

DEFAULT_PLANS = [
    {
        'slug': 'month',
        'title': 'VPN на 30 дней',
        'description': 'Доступ к VPN на 30 дней. Подписка выдаётся после подтверждения оплаты.',
        'duration_days': 30,
        'traffic_gb': 0,
        'price_rub': 199,
        'sort_order': 10,
    },
    {
        'slug': 'quarter',
        'title': 'VPN на 90 дней',
        'description': 'Доступ к VPN на 90 дней. Выгоднее, чем платить три раза и каждый раз вспоминать пароль.',
        'duration_days': 90,
        'traffic_gb': 0,
        'price_rub': 499,
        'sort_order': 20,
    },
]

SUPPORT_TEXT = (
    '🆘 <b>Поддержка</b>\n\n'
    'Если подписка не открывается, пришлите в поддержку ваш Telegram ID и скрин ошибки. '
    'Секретные ключи и ссылки на подписку никому не отправляйте. Интернет и без того полон энтузиастов.'
)


def stars_for_rub(value: int) -> int:
    rate = max(float(settings.stars_rub_per_star or 1), 0.01)
    return max(1, int(math.ceil(value / rate)))


def is_admin(event: Message | CallbackQuery) -> bool:
    return bool(event.from_user and event.from_user.id in settings.admin_ids)


def provider_config_status(provider: str) -> tuple[bool, str]:
    if provider in provider_runtime_disabled:
        return False, provider_runtime_errors.get(provider, 'отключена после ошибки')
    if provider == 'stars':
        return True, 'OK'
    if provider == 'yookassa':
        missing = []
        if not settings.yookassa_shop_id:
            missing.append('YOOKASSA_SHOP_ID')
        if not settings.yookassa_secret_key:
            missing.append('YOOKASSA_SECRET_KEY')
        if missing:
            return False, 'не заполнено: ' + ', '.join(missing)
        return True, 'OK'
    return False, 'провайдер пока не подключен'


def enabled_payment_providers() -> list[str]:
    return [p for p in settings.payment_providers if provider_config_status(p)[0]]


def mark_provider_runtime_error(provider: str, reason: str) -> None:
    provider_runtime_errors[provider] = reason
    provider_runtime_disabled.add(provider)
    logging.exception('Payment provider %s disabled: %s', provider, reason)


def payment_title(provider: str) -> str:
    return {'stars': 'Telegram Stars', 'yookassa': 'ЮKassa'}.get(provider, provider)


async def make_bot(app_settings: Settings) -> Bot:
    global proxy_manager
    proxy_manager = ProxyManager.from_env_string(
        app_settings.proxy,
        mode=app_settings.proxy_mode,
        healthcheck_url=app_settings.proxy_healthcheck_url,
        healthcheck_timeout=app_settings.proxy_healthcheck_timeout,
        healthcheck_interval=app_settings.proxy_healthcheck_interval,
    )
    if proxy_manager.has_proxies and proxy_manager.mode.value in {'failover', 'sticky', 'rotate', 'random'}:
        await proxy_manager.check_all()
    session = proxy_manager.get_session() or proxy_manager.get_session_sync()
    if session:
        await proxy_manager.start_healthcheck_loop()
        return Bot(token=app_settings.bot_token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return Bot(token=app_settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def ensure_user(message_or_callback: Message | CallbackQuery) -> dict:
    user = message_or_callback.from_user
    if not user:
        raise RuntimeError('Telegram user is missing')
    return await upsert_user(
        settings.db_path,
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )


async def notify_admins(bot: Bot, text: str) -> None:
    if not settings.admin_notify_purchases:
        return
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logging.exception('Failed to notify admin %s', admin_id)


async def show_home(message: Message) -> None:
    await ensure_user(message)
    await message.answer(
        '🛡 <b>VPN-доступ</b>\n\n'
        'Выберите тариф, оплатите и получите персональную ссылку подписки. '
        'Она открывается в клиенте, который поддерживает вашу конфигурацию Remnawave.',
        reply_markup=main_menu(),
    )


async def show_plans(message: Message) -> None:
    plans = await list_plans(settings.db_path, active_only=True)
    await message.answer('🛡 <b>Тарифы VPN</b>\n\nВыберите срок доступа:', reply_markup=plans_menu(plans))


async def show_plan(message: Message, plan_id: int) -> None:
    plan = await get_plan_by_id(settings.db_path, plan_id)
    if not plan or not int(plan.get('is_active', 0)):
        await message.answer('Этот тариф сейчас недоступен.')
        return
    pending = await get_active_pending_payment(
        settings.db_path,
        telegram_id=message.from_user.id,
        plan_id=plan_id,
        ttl_minutes=settings.pending_payment_ttl_minutes,
    ) if message.from_user else None
    traffic = 'Без лимита трафика' if not int(plan.get('traffic_gb') or 0) else f"Лимит: {plan['traffic_gb']} ГБ"
    text = (
        f"🛡 <b>{plan['title']}</b>\n\n"
        f"{plan.get('description') or ''}\n\n"
        f"📅 Срок: <b>{plan['duration_days']} дней</b>\n"
        f"📶 {traffic}\n"
        f"💰 Цена: <b>{plan['price_rub']} ₽</b> / {stars_for_rub(int(plan['price_rub']))} ⭐"
    )
    await message.answer(text, reply_markup=plan_menu(plan_id, has_pending=bool(pending)))


async def show_my_vpn(message: Message) -> None:
    await ensure_user(message)
    subscription = await get_active_subscription(settings.db_path, telegram_id=message.from_user.id)
    if not subscription:
        await message.answer(
            '🔑 <b>Активного VPN-доступа нет.</b>\n\nВыберите тариф, оплатите его, и бот выдаст ссылку подписки.',
            reply_markup=main_menu(),
        )
        return
    try:
        expires = datetime.fromisoformat(subscription['expires_at']).astimezone().strftime('%d.%m.%Y %H:%M')
    except Exception:
        expires = subscription['expires_at']
    url = subscription.get('subscription_url') or ''
    text = (
        '🔑 <b>Мой VPN</b>\n\n'
        f"Тариф: <b>{subscription['plan_title']}</b>\n"
        f"Действует до: <b>{expires}</b>\n\n"
    )
    if url:
        text += f"<code>{url}</code>\n\nСохраните ссылку и откройте её в поддерживаемом VPN-клиенте."
    else:
        text += 'Подписка создана, но ссылка от Remnawave пока не получена. Проверьте настройки интеграции.'
    await message.answer(text, reply_markup=my_vpn_menu(subscription_url=url))


async def grant_access(*, bot: Bot, payment_id: int) -> tuple[dict, dict]:
    payment = await get_payment(settings.db_path, payment_id)
    if not payment:
        raise RuntimeError('Payment not found')
    plan = await get_plan_by_id(settings.db_path, int(payment['plan_id']))
    if not plan:
        raise RuntimeError('Plan not found')

    if payment.get('subscription_id'):
        subscription = await get_active_subscription(settings.db_path, telegram_id=int(payment['telegram_id']))
        if subscription:
            return payment, subscription

    client = RemnawaveClient(settings)
    access = await client.create_or_extend_user(
        telegram_id=int(payment['telegram_id']),
        username='',
        duration_days=int(plan['duration_days']),
        traffic_gb=int(plan.get('traffic_gb') or settings.remnawave_default_traffic_gb),
    )
    subscription_id = await add_subscription(
        settings.db_path,
        user_id=int(payment['user_id']),
        telegram_id=int(payment['telegram_id']),
        plan_id=int(plan['id']),
        duration_days=int(plan['duration_days']),
        traffic_limit_gb=int(plan.get('traffic_gb') or settings.remnawave_default_traffic_gb),
        remnawave_user_id=access.remnawave_user_id,
        subscription_url=access.subscription_url,
    )
    await update_subscription_access(
        settings.db_path,
        subscription_id,
        remnawave_user_id=access.remnawave_user_id,
        subscription_url=access.subscription_url,
    )
    await attach_subscription_to_payment(settings.db_path, payment_id, subscription_id)
    subscription = await get_active_subscription(settings.db_path, telegram_id=int(payment['telegram_id']))
    await notify_admins(bot, f"✅ VPN-оплата: user={payment['telegram_id']}, тариф={plan['title']}, сумма={payment['amount_rub']} ₽")
    return payment, subscription or {}


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await show_home(message)


@router.message(Command('plans'))
async def cmd_plans(message: Message) -> None:
    await ensure_user(message)
    await show_plans(message)


@router.message(Command('vpn'))
async def cmd_vpn(message: Message) -> None:
    await show_my_vpn(message)


@router.message(Command('support'))
async def cmd_support(message: Message) -> None:
    await message.answer(SUPPORT_TEXT, reply_markup=main_menu())


@router.message(Command('id'))
async def cmd_id(message: Message) -> None:
    await message.answer(f'Ваш Telegram ID: <code>{message.from_user.id}</code>')


@router.message(Command('admin'))
async def cmd_admin(message: Message) -> None:
    if not is_admin(message):
        return
    await message.answer('⚙️ <b>Админка VPN-бота</b>', reply_markup=admin_menu())


@router.callback_query(F.data == 'home')
async def cb_home(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_home(callback.message)


@router.callback_query(F.data == 'plans')
async def cb_plans(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_plans(callback.message)


@router.callback_query(F.data == 'my_vpn')
async def cb_my_vpn(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_my_vpn(callback.message)


@router.callback_query(F.data == 'help')
async def cb_help(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(SUPPORT_TEXT, reply_markup=main_menu())


@router.callback_query(F.data.startswith('plan:'))
async def cb_plan(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_plan(callback.message, int(callback.data.split(':')[1]))


@router.callback_query(F.data.startswith('buy:'))
async def cb_buy(callback: CallbackQuery) -> None:
    await callback.answer()
    plan_id = int(callback.data.split(':')[1])
    plan = await get_plan_by_id(settings.db_path, plan_id)
    if not plan:
        await callback.message.answer('Тариф не найден.')
        return
    providers = enabled_payment_providers()
    if not providers:
        await callback.message.answer('Оплата временно недоступна. Проверьте настройки платежных провайдеров.')
        return
    await callback.message.answer('Выберите способ оплаты:', reply_markup=payment_methods_menu(plan_id, providers))


@router.callback_query(F.data.startswith('pay:stars:'))
async def cb_pay_stars(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    plan_id = int(callback.data.split(':')[2])
    plan = await get_plan_by_id(settings.db_path, plan_id)
    if not plan:
        await callback.message.answer('Тариф не найден.')
        return
    user = await ensure_user(callback)
    payload = f'vpn_plan:{plan_id}:{callback.from_user.id}'
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=plan['title'],
        description=f"VPN-доступ на {plan['duration_days']} дней",
        payload=payload,
        provider_token='',
        currency='XTR',
        prices=[LabeledPrice(label=plan['title'], amount=stars_for_rub(int(plan['price_rub'])))],
    )


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    if not query.invoice_payload.startswith('vpn_plan:'):
        await query.answer(ok=False, error_message='Неизвестный товар.')
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, bot: Bot) -> None:
    payment_info = message.successful_payment
    try:
        _, plan_id_text, _ = payment_info.invoice_payload.split(':', 2)
        plan_id = int(plan_id_text)
    except Exception:
        logging.exception('Invalid Stars payload: %s', payment_info.invoice_payload)
        await message.answer('Не удалось определить тариф. Обратитесь в поддержку.')
        return
    user = await ensure_user(message)
    payment_id = await add_payment(
        settings.db_path,
        provider='stars',
        provider_payment_id=payment_info.telegram_payment_charge_id,
        user_id=int(user['id']),
        telegram_id=message.from_user.id,
        plan_id=plan_id,
        amount_rub=int((await get_plan_by_id(settings.db_path, plan_id))['price_rub']),
        currency='XTR',
        status='paid',
        payload=payment_info.invoice_payload,
        telegram_payment_charge_id=payment_info.telegram_payment_charge_id,
        provider_payment_charge_id=payment_info.provider_payment_charge_id,
    )
    try:
        _, subscription = await grant_access(bot=bot, payment_id=payment_id)
        url = subscription.get('subscription_url') or ''
        await message.answer(
            '✅ <b>Оплата получена. VPN-доступ создан.</b>\n\n'
            + (f'<code>{url}</code>' if url else 'Ссылка будет доступна в разделе «Мой VPN».') ,
            reply_markup=after_purchase_menu(),
        )
    except Exception:
        logging.exception('Access grant failed after Stars payment payment_id=%s', payment_id)
        await message.answer('Оплата подтверждена, но выдача доступа требует проверки. Администратор уже получил уведомление.')


@router.callback_query(F.data.startswith('pay:yookassa:'))
async def cb_pay_yookassa(callback: CallbackQuery) -> None:
    await callback.answer()
    plan_id = int(callback.data.split(':')[2])
    plan = await get_plan_by_id(settings.db_path, plan_id)
    if not plan:
        await callback.message.answer('Тариф не найден.')
        return
    ok, reason = provider_config_status('yookassa')
    if not ok:
        await callback.message.answer(f'ЮKassa недоступна: {reason}')
        return
    user = await ensure_user(callback)
    provider = build_provider('yookassa', settings)
    try:
        created = await provider.create_payment(
            amount_rub=int(plan['price_rub']),
            description=f"VPN-доступ: {plan['title']}",
            return_url=settings.yookassa_return_url or 'https://t.me',
            metadata={'telegram_id': callback.from_user.id, 'plan_id': plan_id},
            receipt_customer=receipt_customer_from_contact(None, settings.receipt_fallback_email),
        )
        payment_id = await add_payment(
            settings.db_path,
            provider='yookassa',
            provider_payment_id=created.provider_payment_id,
            user_id=int(user['id']),
            telegram_id=callback.from_user.id,
            plan_id=plan_id,
            amount_rub=int(plan['price_rub']),
            currency='RUB',
            status='pending',
            payment_url=created.payment_url,
            payload={'plan_id': plan_id},
        )
        await callback.message.answer(
            '💳 Перейдите по ссылке, оплатите, затем вернитесь и нажмите «Я оплатил / проверить».',
            reply_markup=external_payment_menu(payment_id, created.payment_url, plan_id),
        )
    except Exception as exc:
        mark_provider_runtime_error('yookassa', str(exc))
        await callback.message.answer('Не удалось создать платёж ЮKassa. Проверьте настройки и логи.')


@router.callback_query(F.data.startswith('pending:'))
async def cb_pending(callback: CallbackQuery) -> None:
    await callback.answer()
    plan_id = int(callback.data.split(':')[1])
    pending = await get_active_pending_payment(
        settings.db_path,
        telegram_id=callback.from_user.id,
        plan_id=plan_id,
        ttl_minutes=settings.pending_payment_ttl_minutes,
    )
    if not pending:
        await callback.message.answer('Ожидающий платёж не найден или его срок истёк.')
        return
    await check_external_payment(callback, int(pending['id']))


@router.callback_query(F.data.startswith('epay:check:'))
async def cb_external_check(callback: CallbackQuery) -> None:
    await callback.answer()
    await check_external_payment(callback, int(callback.data.split(':')[2]))


async def check_external_payment(callback: CallbackQuery, payment_id: int) -> None:
    payment = await get_payment(settings.db_path, payment_id)
    if not payment or int(payment['telegram_id']) != callback.from_user.id:
        await callback.message.answer('Платёж не найден.')
        return
    if payment.get('subscription_id'):
        await show_my_vpn(callback.message)
        return
    if payment['provider'] != 'yookassa':
        await callback.message.answer('Проверка для этого способа оплаты пока не поддерживается.')
        return
    try:
        provider = build_provider('yookassa', settings)
        status = await provider.get_status(payment['provider_payment_id'])
        if not status.paid:
            await callback.message.answer('Оплата пока не подтверждена. Иногда банкам требуется несколько минут, потому что время у них течёт с характером.')
            return
        await mark_payment_paid(settings.db_path, payment_id, provider_status='paid')
        _, subscription = await grant_access(bot=callback.bot, payment_id=payment_id)
        url = subscription.get('subscription_url') or ''
        await callback.message.answer(
            '✅ <b>Оплата подтверждена. VPN-доступ создан.</b>\n\n'
            + (f'<code>{url}</code>' if url else 'Откройте раздел «Мой VPN».') ,
            reply_markup=after_purchase_menu(),
        )
    except PaymentProviderError as exc:
        await callback.message.answer(f'Ошибка проверки оплаты: {exc}')
    except Exception:
        logging.exception('External payment check failed payment_id=%s', payment_id)
        await callback.message.answer('Ошибка проверки оплаты. Подробности записаны в лог.')


@router.callback_query(F.data.startswith('admin:'))
async def cb_admin(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Недостаточно прав', show_alert=True)
        return
    await callback.answer()
    action = callback.data.split(':', 1)[1]
    if action == 'stats':
        stats = await get_stats(settings.db_path)
        await callback.message.answer(
            '📊 <b>Статистика</b>\n\n'
            f"Тарифов: {stats['plans']}\n"
            f"Пользователей: {stats['users']}\n"
            f"Подписок всего: {stats['subscriptions']}\n"
            f"Активных подписок: {stats['active_subscriptions']}\n"
            f"Платежей: {stats['payments']}\n"
            f"Оплаченных: {stats['paid_payments']}",
            reply_markup=admin_menu(),
        )
    elif action == 'plans':
        plans = await list_plans(settings.db_path, active_only=False)
        text = '🛡 <b>Тарифы</b>\n\n' + '\n'.join(
            f"#{p['id']} {p['title']} · {p['duration_days']} дн. · {p['price_rub']} ₽ · {'вкл' if p['is_active'] else 'выкл'}"
            for p in plans
        )
        await callback.message.answer(text or 'Тарифов нет.', reply_markup=admin_menu())
    elif action == 'payments':
        lines = []
        for provider in settings.payment_providers:
            ok, reason = provider_config_status(provider)
            lines.append(f"{'✅' if ok else '⛔'} {payment_title(provider)}: {reason}")
        await callback.message.answer('💳 <b>Платежные системы</b>\n\n' + '\n'.join(lines), reply_markup=admin_menu())
    elif action == 'system':
        client = RemnawaveClient(settings)
        await callback.message.answer(
            'ℹ️ <b>О системе</b>\n\n'
            f"{version_line()}\n"
            f"SQLite schema: {DB_SCHEMA_VERSION}\n"
            f"Remnawave API: {'настроен' if client.is_configured else 'не настроен, работает заглушка'}\n"
            f"База: <code>{settings.db_path}</code>",
            reply_markup=admin_menu(),
        )
    else:
        await callback.message.answer('⚙️ <b>Админка VPN-бота</b>', reply_markup=admin_menu())


async def setup_commands(bot: Bot) -> None:
    if not settings.auto_setup_bot_menu:
        return
    await bot.set_my_commands([
        BotCommand(command='start', description='Главное меню'),
        BotCommand(command='plans', description='Тарифы VPN'),
        BotCommand(command='vpn', description='Мой VPN'),
        BotCommand(command='support', description='Поддержка'),
        BotCommand(command='id', description='Мой Telegram ID'),
    ], scope=BotCommandScopeDefault())
    for admin_id in settings.admin_ids:
        await bot.set_my_commands([
            BotCommand(command='start', description='Главное меню'),
            BotCommand(command='plans', description='Тарифы VPN'),
            BotCommand(command='vpn', description='Мой VPN'),
            BotCommand(command='support', description='Поддержка'),
            BotCommand(command='id', description='Мой Telegram ID'),
            BotCommand(command='admin', description='Админка'),
        ], scope=BotCommandScopeChat(chat_id=admin_id))


def configure_logging() -> None:
    Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(settings.log_file, maxBytes=settings.log_max_bytes, backupCount=settings.log_backup_count, encoding='utf-8'),
        ],
    )


async def main() -> None:
    global settings
    settings = get_settings()
    configure_logging()
    await init_db(settings.db_path)
    if settings.seed_plans_on_start:
        await seed_plans(settings.db_path, DEFAULT_PLANS)
    bot = await make_bot(settings)
    await setup_commands(bot)
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await bot.delete_webhook(drop_pending_updates=settings.drop_pending_updates) if settings.delete_webhook_on_start else None
        await dp.start_polling(bot)
    finally:
        if proxy_manager:
            await proxy_manager.close()
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
