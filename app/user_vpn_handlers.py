from __future__ import annotations

from urllib.parse import quote

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app import runtime
from app.admin_db import has_used_trial
from app.db import get_active_subscription, upsert_user
from app.keyboards import main_menu, support_menu

router = Router()

HAPP_REDIRECT_BASE = 'https://legiz-ru.github.io/Orion/redirect-page/?redirect_to='


def esc(value: str) -> str:
    return value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def subscription_page_url(url: str) -> str:
    url = (url or '').strip()
    if not url:
        return ''
    if url.startswith('happ://add/'):
        return url[len('happ://add/'):]
    if url.startswith('http://') or url.startswith('https://'):
        return url
    base = runtime.settings.remnawave_subscription_base_url.rstrip('/') if runtime.settings.remnawave_subscription_base_url else ''
    return f'{base}/{url.lstrip("/")}' if base else url


def happ_url(url: str) -> str:
    page = subscription_page_url(url)
    if not page:
        return ''
    return f'happ://add/{page}'


def happ_redirect_url(url: str) -> str:
    direct = happ_url(url)
    if not direct:
        return ''
    return HAPP_REDIRECT_BASE + quote(direct, safe='')


def connect_menu(subscription_url: str) -> InlineKeyboardMarkup:
    page = subscription_page_url(subscription_url)
    direct = happ_redirect_url(page)
    rows: list[list[InlineKeyboardButton]] = []
    if direct:
        rows.append([InlineKeyboardButton(text='⚡ Подключить в Happ', url=direct)])
    if page:
        rows.append([InlineKeyboardButton(text='📖 Установка / другое устройство', url=page)])
    rows += [
        [InlineKeyboardButton(text='💳 Продлить', callback_data='plans')],
        [InlineKeyboardButton(text='❓ Помощь', callback_data='help')],
        [InlineKeyboardButton(text='⌂ Главное', callback_data='home')],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_user_home(callback: CallbackQuery) -> None:
    await upsert_user(runtime.settings.db_path, telegram_id=callback.from_user.id, username=callback.from_user.username, full_name=callback.from_user.full_name)
    sub = await get_active_subscription(runtime.settings.db_path, telegram_id=callback.from_user.id)
    trial_available = False if sub else not await has_used_trial(runtime.settings.db_path, callback.from_user.id)
    if sub:
        text = f"🛡 <b>VPN</b>\n\n🟢 Доступ активен до <b>{esc(str(sub['expires_at'])[:10])}</b>"
    else:
        text = '🛡 <b>VPN</b>\n\nПодключайтесь за минуту.'
    await callback.message.answer(text, reply_markup=main_menu(active=bool(sub), trial_available=trial_available))


@router.callback_query(F.data == 'home')
async def home(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_user_home(callback)


@router.callback_query(F.data == 'help')
async def support(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer('🆘 <b>Поддержка</b>\n\nПришлите ваш Telegram ID и скрин ошибки.', reply_markup=support_menu())


@router.callback_query(F.data == 'my_vpn')
async def my_vpn(callback: CallbackQuery) -> None:
    await callback.answer()
    telegram_id = callback.from_user.id
    sub = await get_active_subscription(runtime.settings.db_path, telegram_id=telegram_id)
    if not sub:
        trial_available = not await has_used_trial(runtime.settings.db_path, telegram_id)
        await callback.message.answer('🔑 Нет активного доступа.', reply_markup=main_menu(active=False, trial_available=trial_available))
        return

    page = subscription_page_url(sub.get('subscription_url') or '')
    expires = esc(str(sub['expires_at'])[:10])
    text = (
        '🔑 <b>Ваш VPN готов</b>\n\n'
        f'🟢 Активен до <b>{expires}</b>\n\n'
        '<b>Если Happ уже установлен:</b>\n'
        'нажмите «⚡ Подключить в Happ» — подписка добавится в приложение.\n\n'
        '<b>Если приложения ещё нет:</b>\n'
        'откройте «📖 Установка / другое устройство».'
    )
    await callback.message.answer(text, reply_markup=connect_menu(page))
