from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import runtime
from app.products.mtproto.service import (
    access_view,
    configuration_error,
    control_client,
    ensure_enabled,
    entitlement_for,
    reconcile_once,
    rotate,
)
from app.products.mtproto.storage import (
    list_accesses,
    list_plans_mtproto,
    set_plan_enabled,
)

router = Router()


def esc(value: object) -> str:
    return str(value or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='⌂ Главное', callback_data='home')],
    ])


def create_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🚀 Создать личный Telegram Proxy', callback_data='mtproto:create')],
        [InlineKeyboardButton(text='⌂ Главное', callback_data='home')],
    ])


def access_menu(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🚀 Подключить в Telegram', url=url)],
        [InlineKeyboardButton(text='🔄 Сменить код', callback_data='mtproto:rotate:ask')],
        [InlineKeyboardButton(text='⌂ Главное', callback_data='home')],
    ])


async def render_user(message: Message, telegram_id: int) -> None:
    if not runtime.settings.mtproto_enabled:
        await message.answer('🛡 Telegram Proxy сейчас выключен.', reply_markup=back_menu())
        return
    error = configuration_error()
    if error:
        await message.answer(
            '🛡 <b>Telegram Proxy</b>\n\n⚠️ Сервис пока не настроен администратором.',
            reply_markup=back_menu(),
        )
        return
    entitlement = await entitlement_for(runtime.settings.db_path, telegram_id)
    if not entitlement:
        await message.answer(
            '🛡 <b>Telegram Proxy</b>\n\n'
            'Доступен только при активном <b>оплаченном</b> тарифе, '
            'для которого включена опция Telegram Proxy.\n\n'
            'Тестовый и бесплатный доступ не подходят.',
            reply_markup=back_menu(),
        )
        return
    view = await access_view(runtime.settings.db_path, telegram_id)
    if not view:
        admin_note = '\n\n🛠 Для администратора включён служебный override для тестирования.' if entitlement.get('admin_override') else ''
        await message.answer(
            '🛡 <b>Личный Telegram Proxy</b>\n\n'
            'У вас есть право на MTProto. Создайте личный код — он будет действовать, '
            'пока активно право доступа.' + admin_note,
            reply_markup=create_menu(),
        )
        return
    if view.status != 'active':
        try:
            view = await ensure_enabled(runtime.settings.db_path, telegram_id)
        except Exception:
            await message.answer(
                '🛡 <b>Telegram Proxy</b>\n\n⚠️ Не удалось включить доступ. Попробуйте позже.',
                reply_markup=back_menu(),
            )
            return
    entitlement = view.entitlement or entitlement
    admin_note = '\nРежим: <b>admin override</b>' if entitlement.get('admin_override') else ''
    await message.answer(
        '🛡 <b>Ваш личный Telegram Proxy</b>\n\n'
        f'Сервер: <code>{esc(runtime.settings.mtproto_public_host)}</code>\n'
        f'Порт: <code>{runtime.settings.mtproto_public_port}</code>\n'
        f'Код: <code>{esc(view.public_secret)}</code>\n'
        f'Тариф: <b>{esc(entitlement.get("plan_title") or "оплачен")}</b>{admin_note}\n\n'
        'Код индивидуальный. При окончании права доступа он отключится автоматически; '
        'после продления включится снова.',
        reply_markup=access_menu(view.connect_url),
    )


@router.message(Command('mtproto'))
async def mtproto_command(message: Message) -> None:
    if message.from_user:
        await render_user(message, message.from_user.id)


@router.callback_query(F.data == 'mtproto')
async def mtproto_home(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await render_user(callback.message, callback.from_user.id)


@router.callback_query(F.data == 'mtproto:create')
async def mtproto_create(callback: CallbackQuery) -> None:
    await callback.answer('Создаю…')
    try:
        await ensure_enabled(runtime.settings.db_path, callback.from_user.id)
    except PermissionError as exc:
        await callback.message.answer(f'⛔ {esc(exc)}', reply_markup=back_menu())
        return
    except Exception:
        await callback.message.answer('❌ Не удалось создать Telegram Proxy.', reply_markup=back_menu())
        return
    await render_user(callback.message, callback.from_user.id)


@router.callback_query(F.data == 'mtproto:rotate:ask')
async def mtproto_rotate_ask(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        '⚠️ <b>Сменить личный код?</b>\n\nСтарая ссылка перестанет работать.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔄 Да, сменить', callback_data='mtproto:rotate:confirm')],
            [InlineKeyboardButton(text='← Назад', callback_data='mtproto')],
        ]),
    )


@router.callback_query(F.data == 'mtproto:rotate:confirm')
async def mtproto_rotate_confirm(callback: CallbackQuery) -> None:
    await callback.answer('Меняю код…')
    try:
        await rotate(runtime.settings.db_path, callback.from_user.id)
    except Exception:
        await callback.message.answer('❌ Не удалось сменить код.', reply_markup=back_menu())
        return
    await render_user(callback.message, callback.from_user.id)


def admin_menu(plans: list[dict], *, controller_ok: bool, accesses: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text='👤 Мой MTProto', callback_data='mtproto')],
    ]
    for plan in plans:
        enabled = bool(int(plan.get('mtproto_enabled') or 0))
        rows.append([InlineKeyboardButton(
            text=f"{'✅' if enabled else '⚪'} {plan['title']} · MTProto {'ON' if enabled else 'OFF'}"[:60],
            callback_data=f"admin:mtproto:plan:{plan['id']}",
        )])
    rows += [
        [InlineKeyboardButton(text='🔄 Reconcile сейчас', callback_data='admin:mtproto:reconcile')],
        [InlineKeyboardButton(text='← Админка', callback_data='admin:home')],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_admin(message: Message) -> None:
    plans = await list_plans_mtproto(runtime.settings.db_path)
    accesses = await list_accesses(runtime.settings.db_path)
    health = False
    health_text = 'не настроен'
    try:
        data = await control_client().health()
        health = bool(data.get('ok', True))
        health_text = 'dry-run' if data.get('mode') == 'dry-run' else ('online' if health else 'ошибка')
    except Exception:
        health_text = 'offline'
    active = sum(1 for row in accesses if row.get('status') == 'active')
    disabled = sum(1 for row in accesses if row.get('status') == 'disabled')
    errors = sum(1 for row in accesses if row.get('status') == 'error')
    cfg_error = configuration_error()
    await message.answer(
        '🛡 <b>MTProto</b>\n\n'
        f"Feature: <b>{'ON' if runtime.settings.mtproto_enabled else 'OFF'}</b>\n"
        f'Controller: <b>{esc(health_text)}</b>\n'
        f'Host: <code>{esc(runtime.settings.mtproto_public_host or "-")}</code>:{runtime.settings.mtproto_public_port}\n'
        f'Auto-create: <b>{"ON" if runtime.settings.mtproto_auto_create else "OFF"}</b>\n'
        'Admin access: <b>always allowed for testing</b>\n\n'
        f'Активных кодов: <b>{active}</b>\n'
        f'Отключённых: <b>{disabled}</b>\n'
        f'Ошибок: <b>{errors}</b>'
        + (f'\n\n⚠️ <code>{esc(cfg_error)}</code>' if cfg_error else ''),
        reply_markup=admin_menu(plans, controller_ok=health, accesses=accesses),
    )


@router.callback_query(F.data == 'admin:mtproto')
async def mtproto_admin(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer()
    await render_admin(callback.message)


@router.callback_query(F.data.startswith('admin:mtproto:plan:'))
async def mtproto_plan_toggle(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    plan_id = int(callback.data.rsplit(':', 1)[1])
    plans = await list_plans_mtproto(runtime.settings.db_path)
    plan = next((row for row in plans if int(row['id']) == plan_id), None)
    if not plan:
        await callback.answer('Тариф не найден', show_alert=True)
        return
    enabled = not bool(int(plan.get('mtproto_enabled') or 0))
    await set_plan_enabled(runtime.settings.db_path, plan_id, enabled)
    await callback.answer('MTProto включён для тарифа' if enabled else 'MTProto выключен для тарифа')
    await render_admin(callback.message)


@router.callback_query(F.data == 'admin:mtproto:reconcile')
async def mtproto_admin_reconcile(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.answer('Проверяю…')
    result = await reconcile_once(runtime.settings.db_path)
    await callback.message.answer(
        '🔄 <b>MTProto reconcile</b>\n\n'
        f"Создано: {result['created']}\n"
        f"Включено: {result['enabled']}\n"
        f"Отключено: {result['disabled']}\n"
        f"Ошибок: {result['errors']}",
    )
