from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from app import runtime
from app.keyboards import main_menu
from app.start_content import DEFAULT_ACTIVE_TEXT, DEFAULT_GUEST_TEXT, get_start_content

router = Router()


async def render_start(message: Message, telegram_id: int) -> None:
    await runtime.upsert_user(
        runtime.settings.db_path,
        telegram_id=telegram_id,
        username=message.chat.username if message.chat.type == 'private' else None,
        full_name=message.chat.full_name,
    )
    subscription = await runtime.get_active_subscription(runtime.settings.db_path, telegram_id=telegram_id)
    trial_available = not await runtime.has_used_trial(runtime.settings.db_path, telegram_id)
    cfg = await get_start_content(runtime.settings.db_path)

    if cfg.enabled:
        template = cfg.active_text if subscription else cfg.guest_text
    else:
        template = DEFAULT_ACTIVE_TEXT if subscription else DEFAULT_GUEST_TEXT
    expires = str(subscription.get('expires_at') or '')[:10] if subscription else ''
    try:
        text = template.replace('{expires}', expires)
    except Exception:
        text = template
    markup = main_menu(active=bool(subscription), trial_available=trial_available)

    if cfg.enabled and cfg.image_enabled and cfg.image_file_id:
        await message.answer_photo(photo=cfg.image_file_id, caption=text[:1024], reply_markup=markup)
    else:
        await message.answer(text[:4000], reply_markup=markup)


@router.message(CommandStart())
async def start(message: Message) -> None:
    if not message.from_user:
        return
    await render_start(message, message.from_user.id)


@router.callback_query(F.data == 'home')
async def home(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        return
    await callback.answer()
    await render_start(callback.message, callback.from_user.id)
