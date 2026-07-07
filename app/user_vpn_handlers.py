from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app import runtime
from app.admin_db import has_used_trial
from app.db import get_active_subscription, upsert_user
from app.keyboards import main_menu, my_vpn_menu, support_menu

router = Router()


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
    return page if page.startswith('happ://add/') else f'happ://add/{page}'


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
    happ = happ_url(page)
    text = (
        f"🔑 <b>Ваш VPN</b>\n\n"
        f"Доступ активен до: <b>{esc(str(sub['expires_at'])[:16])}</b>\n\n"
        f"<b>Happ:</b>\n<code>{esc(happ)}</code>\n\n"
        f"<b>Страница подписки:</b>\n<code>{esc(page)}</code>"
    )
    await callback.message.answer(text, reply_markup=my_vpn_menu(subscription_url=page, happ_url=happ))
