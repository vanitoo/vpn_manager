from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app import runtime
from app.db import get_active_subscription, upsert_user
from app.support.common import CATEGORIES, category_menu, chat_menu, forward_user_message, help_menu, result_menu, setup_device_menu, setup_devices_menu
from app.support.diagnostics import collect_diagnostics, user_diagnostic_text
from app.support.settings import enabled, group_id
from app.support.storage import create_ticket, get_open_ticket, update_ticket_status
from app.user_vpn_handlers import subscription_page_url

router = Router()
log = logging.getLogger(__name__)

HAPP_DOWNLOADS = {
    'ios': 'https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6788279553',
    'android': 'https://play.google.com/store/apps/details?id=com.happproxy',
    'windows': 'https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe',
    'macos': 'https://github.com/Happ-proxy/happ-desktop/releases/latest',
    'linux': 'https://github.com/Happ-proxy/happ-desktop/releases/latest',
    'androidtv': 'https://play.google.com/store/apps/details?id=com.happproxy',
}

SETUP_TEXTS = {
    'ios': (
        '🍎 <b>iPhone / iPad</b>\n\n'
        '1. Установите Happ из App Store.\n'
        '2. Вернитесь сюда и нажмите «⚡ Подключить VPN».\n'
        '3. На странице нажмите «Открыть в Happ».\n'
        '4. Разрешите добавление VPN-конфигурации.\n'
        '5. Выберите нужную локацию и включите VPN.\n\n'
        'Если Happ недоступен в вашем регионе App Store, на странице подключения также доступен Incy.'
    ),
    'android': (
        '🤖 <b>Android</b>\n\n'
        '1. Установите Happ из Google Play.\n'
        '2. Вернитесь сюда и нажмите «⚡ Подключить VPN».\n'
        '3. На странице нажмите «Открыть в Happ».\n'
        '4. Разрешите создание VPN-подключения.\n'
        '5. Выберите локацию и включите VPN.'
    ),
    'windows': (
        '🪟 <b>Windows</b>\n\n'
        '1. Скачайте и установите Happ.\n'
        '2. Нажмите ниже «⚡ Подключить VPN».\n'
        '3. На открывшейся странице выберите «Открыть в Happ».\n'
        '4. Подписка добавится в приложение.\n'
        '5. Выберите локацию и включите VPN.'
    ),
    'macos': (
        '🍎 <b>macOS</b>\n\n'
        '1. Скачайте и установите Happ.\n'
        '2. Нажмите ниже «⚡ Подключить VPN».\n'
        '3. На странице выберите «Открыть в Happ».\n'
        '4. Разрешите добавление VPN-конфигурации.\n'
        '5. Выберите локацию и включите VPN.'
    ),
    'linux': (
        '🐧 <b>Linux</b>\n\n'
        '1. Откройте страницу загрузки Happ и установите пакет для вашей системы: DEB, RPM или Arch.\n'
        '2. Запустите Happ.\n'
        '3. Нажмите ниже «⚡ Подключить VPN», затем «Открыть в Happ».\n'
        '4. Выберите локацию и включите VPN.'
    ),
    'androidtv': (
        '📺 <b>Android TV</b>\n\n'
        '1. На телевизоре установите Happ из Google Play.\n'
        '2. Запустите Happ на TV — приложение покажет QR-код для добавления подписки.\n'
        '3. Откройте Happ на телефоне, где уже добавлен Warp.\n'
        '4. Используйте функцию добавления устройства / QR и отсканируйте код с телевизора.\n'
        '5. На TV выберите локацию и включите VPN.'
    ),
    'appletv': (
        '📺 <b>Apple TV</b>\n\n'
        '1. Откройте App Store на Apple TV и установите Happ.\n'
        '2. Запустите Happ на телевизоре.\n'
        '3. Откройте Happ на iPhone/iPad, где уже добавлен Warp.\n'
        '4. Добавьте Apple TV через QR-код, который покажет приложение на телевизоре.\n'
        '5. Выберите локацию на TV и включите VPN.'
    ),
}


class SupportState(StatesGroup):
    first_message = State()
    chatting = State()


@router.callback_query(F.data == 'help')
async def home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    ticket = await get_open_ticket(runtime.settings.db_path, callback.from_user.id)
    await callback.answer()
    await callback.message.answer(
        '❓ <b>Помощь</b>\n\nВыберите инструкцию или способ решения проблемы.',
        reply_markup=help_menu(bool(ticket)),
    )


@router.callback_query(F.data == 'setup')
async def setup_home(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        '📲 <b>Как подключить VPN</b>\n\nВыберите устройство, на котором хотите настроить Warp:',
        reply_markup=setup_devices_menu(),
    )


@router.callback_query(F.data.startswith('setup:'))
async def setup_device(callback: CallbackQuery) -> None:
    device = callback.data.split(':', 1)[1]
    text = SETUP_TEXTS.get(device)
    if not text:
        await callback.answer('Инструкция не найдена', show_alert=True)
        return
    sub = await get_active_subscription(runtime.settings.db_path, telegram_id=callback.from_user.id)
    page = subscription_page_url((sub or {}).get('subscription_url') or '') if sub else ''
    await callback.answer()
    if not sub:
        text += '\n\n⚠️ <b>Сейчас у вас нет активной VPN-подписки.</b> Сначала оформите доступ в главном меню.'
    await callback.message.answer(
        text,
        reply_markup=setup_device_menu(download_url=HAPP_DOWNLOADS.get(device, ''), subscription_url=page),
    )


@router.callback_query(F.data == 'support:diag')
async def diagnostics(callback: CallbackQuery) -> None:
    await callback.answer('Проверяю')
    data = await collect_diagnostics(callback.from_user.id)
    await callback.message.answer(user_diagnostic_text(data), reply_markup=result_menu())


@router.callback_query(F.data == 'support:new')
async def new_ticket(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer('Что случилось?', reply_markup=category_menu())


@router.callback_query(F.data.startswith('support:cat:'))
async def category(callback: CallbackQuery, state: FSMContext) -> None:
    code = callback.data.rsplit(':', 1)[1]
    await state.update_data(category=code)
    await callback.answer('Запускаю диагностику')
    text = user_diagnostic_text(await collect_diagnostics(callback.from_user.id))
    await callback.message.answer(
        f"Тема: <b>{CATEGORIES.get(code, 'Другое')}</b>\n\n{text}",
        reply_markup=result_menu(code),
    )


@router.callback_query(F.data == 'support:solved')
async def solved(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer('Отлично')
    await callback.message.answer('✅ Обращение создавать не будем.', reply_markup=help_menu())


@router.callback_query(F.data.startswith('support:contact:'))
async def contact(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(category=callback.data.rsplit(':', 1)[1])
    await state.set_state(SupportState.first_message)
    await callback.answer()
    await callback.message.answer(
        'Опишите проблему. Можно приложить фото, видео, голосовое сообщение или файл.\n\nДля отмены: /cancel'
    )


@router.callback_query(F.data == 'support:continue')
async def continue_ticket(callback: CallbackQuery, state: FSMContext) -> None:
    ticket = await get_open_ticket(runtime.settings.db_path, callback.from_user.id)
    if not ticket:
        await callback.answer('Открытого обращения нет', show_alert=True)
        return
    await state.set_state(SupportState.chatting)
    await callback.answer()
    await callback.message.answer(
        f"🎫 Обращение <b>#{ticket['id']}</b> открыто. Отправьте сообщение.",
        reply_markup=chat_menu(),
    )


@router.message(SupportState.first_message)
async def first_message(message: Message, state: FSMContext) -> None:
    if not message.from_user or message.chat.type != ChatType.PRIVATE:
        return
    if not enabled() or not group_id():
        await message.answer('Поддержка пока не настроена. Администратору нужно указать SUPPORT_GROUP_ID.')
        return
    data = await state.get_data()
    user = await upsert_user(
        runtime.settings.db_path,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    ticket = await get_open_ticket(runtime.settings.db_path, message.from_user.id)
    if not ticket:
        ticket = await create_ticket(
            runtime.settings.db_path,
            telegram_id=message.from_user.id,
            user_id=int(user['id']),
            category=str(data.get('category') or 'other'),
        )
    try:
        await forward_user_message(message, ticket)
    except Exception as exc:
        log.exception('Support topic creation failed')
        await message.answer(f'Не удалось создать обращение: <code>{str(exc)[:700]}</code>')
        return
    await state.set_state(SupportState.chatting)
    await message.answer(
        f"✅ Обращение <b>#{ticket['id']}</b> создано. Ответ специалиста придёт сюда.",
        reply_markup=chat_menu(),
    )


@router.message(SupportState.chatting)
async def chat_message(message: Message) -> None:
    if not message.from_user or message.chat.type != ChatType.PRIVATE:
        return
    ticket = await get_open_ticket(runtime.settings.db_path, message.from_user.id)
    if not ticket:
        await message.answer('Открытое обращение не найдено.', reply_markup=help_menu())
        return
    try:
        await forward_user_message(message, ticket)
        await message.answer('📨 Сообщение передано специалисту.', reply_markup=chat_menu())
    except Exception as exc:
        log.exception('Support forwarding failed')
        await message.answer(f'Не удалось передать сообщение: <code>{str(exc)[:700]}</code>')


@router.callback_query(F.data == 'support:close')
async def close_ticket(callback: CallbackQuery, state: FSMContext) -> None:
    ticket = await get_open_ticket(runtime.settings.db_path, callback.from_user.id)
    if ticket:
        await update_ticket_status(runtime.settings.db_path, int(ticket['id']), 'closed')
        if ticket.get('topic_id') and group_id():
            try:
                await callback.bot.send_message(
                    group_id(),
                    '✅ Пользователь закрыл обращение.',
                    message_thread_id=int(ticket['topic_id']),
                )
                await callback.bot.close_forum_topic(group_id(), int(ticket['topic_id']))
            except Exception:
                log.exception('Cannot close support topic')
    await state.clear()
    await callback.answer('Закрыто')
    await callback.message.answer('✅ Обращение закрыто.', reply_markup=help_menu())
