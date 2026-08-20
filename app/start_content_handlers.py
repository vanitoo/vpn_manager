from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app import runtime
from app.keyboards import admin_start_content_menu
from app.start_content import get_start_content, update_start_content

router = Router()


class StartContentForm(StatesGroup):
    guest_text = State()
    active_text = State()
    image = State()


async def show(message: Message) -> None:
    cfg = await get_start_content(runtime.settings.db_path)
    text = (
        '👋 <b>Стартовый экран</b>\n\n'
        f"Экран: <b>{'включён' if cfg.enabled else 'выключен'}</b>\n"
        f"Картинка: <b>{'включена' if cfg.image_enabled else 'выключена'}</b>\n"
        f"Картинка загружена: <b>{'да' if cfg.image_file_id else 'нет'}</b>\n\n"
        '<b>Для нового пользователя:</b>\n'
        f'{cfg.guest_text}\n\n'
        '<b>Для активного пользователя:</b>\n'
        f'{cfg.active_text}'
    )
    await message.answer(text[:3900], reply_markup=admin_start_content_menu(cfg.enabled, cfg.image_enabled, bool(cfg.image_file_id)))


@router.callback_query(F.data == 'admin:start')
async def menu(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        return await callback.answer('Нет доступа', show_alert=True)
    await callback.answer()
    await show(callback.message)


@router.callback_query(F.data == 'admin:start:toggle')
async def toggle(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        return await callback.answer('Нет доступа', show_alert=True)
    cfg = await get_start_content(runtime.settings.db_path)
    await update_start_content(runtime.settings.db_path, 'enabled', 0 if cfg.enabled else 1)
    await callback.answer('Изменено')
    await show(callback.message)


@router.callback_query(F.data == 'admin:start:image-toggle')
async def image_toggle(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        return await callback.answer('Нет доступа', show_alert=True)
    cfg = await get_start_content(runtime.settings.db_path)
    await update_start_content(runtime.settings.db_path, 'image_enabled', 0 if cfg.image_enabled else 1)
    await callback.answer('Изменено')
    await show(callback.message)


@router.callback_query(F.data == 'admin:start:guest')
async def edit_guest(callback: CallbackQuery, state: FSMContext) -> None:
    if not runtime.admin(callback):
        return await callback.answer('Нет доступа', show_alert=True)
    await callback.answer()
    await state.set_state(StartContentForm.guest_text)
    await callback.message.answer('Пришлите новый текст /start для пользователя без активного VPN. HTML-разметка поддерживается.')


@router.callback_query(F.data == 'admin:start:active')
async def edit_active(callback: CallbackQuery, state: FSMContext) -> None:
    if not runtime.admin(callback):
        return await callback.answer('Нет доступа', show_alert=True)
    await callback.answer()
    await state.set_state(StartContentForm.active_text)
    await callback.message.answer('Пришлите текст /start для активного пользователя. Используйте <code>{expires}</code> там, где нужна дата окончания.')


@router.callback_query(F.data == 'admin:start:image')
async def edit_image(callback: CallbackQuery, state: FSMContext) -> None:
    if not runtime.admin(callback):
        return await callback.answer('Нет доступа', show_alert=True)
    await callback.answer()
    await state.set_state(StartContentForm.image)
    await callback.message.answer('Пришлите новую стартовую картинку как фото.')


@router.callback_query(F.data == 'admin:start:image-delete')
async def delete_image(callback: CallbackQuery) -> None:
    if not runtime.admin(callback):
        return await callback.answer('Нет доступа', show_alert=True)
    await update_start_content(runtime.settings.db_path, 'image_file_id', '')
    await callback.answer('Картинка удалена')
    await show(callback.message)


@router.message(StartContentForm.guest_text)
async def save_guest(message: Message, state: FSMContext) -> None:
    if not runtime.admin(message):
        return
    text = message.text or message.caption or ''
    if not text.strip():
        return await message.answer('Нужен текст.')
    await update_start_content(runtime.settings.db_path, 'guest_text', text[:3500])
    await state.clear()
    await message.answer('✅ Текст сохранён.')
    await show(message)


@router.message(StartContentForm.active_text)
async def save_active(message: Message, state: FSMContext) -> None:
    if not runtime.admin(message):
        return
    text = message.text or message.caption or ''
    if not text.strip():
        return await message.answer('Нужен текст.')
    await update_start_content(runtime.settings.db_path, 'active_text', text[:3500])
    await state.clear()
    await message.answer('✅ Текст сохранён.')
    await show(message)


@router.message(StartContentForm.image, F.photo)
async def save_image(message: Message, state: FSMContext) -> None:
    if not runtime.admin(message):
        return
    await update_start_content(runtime.settings.db_path, 'image_file_id', message.photo[-1].file_id)
    await update_start_content(runtime.settings.db_path, 'image_enabled', 1)
    await state.clear()
    await message.answer('✅ Стартовая картинка сохранена.')
    await show(message)


@router.message(StartContentForm.image)
async def image_invalid(message: Message) -> None:
    await message.answer('Пришлите изображение именно как фото или /cancel.')
