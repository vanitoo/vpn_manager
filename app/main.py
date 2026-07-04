from __future__ import annotations

import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import shutil
import re
import contextlib
import time
from datetime import datetime
from pathlib import Path
from typing import Any
import tempfile

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, FSInputFile, LabeledPrice, Message, PreCheckoutQuery, BotCommand, BotCommandScopeDefault, BotCommandScopeChat, MenuButtonCommands, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.config import Settings, get_settings
from app.covers import process_cover_image, is_supported_image_filename
from app.db import (
    add_book,
    add_purchase,
    add_external_payment,
    book_files,
    book_to_payload,
    delete_book,
    export_backup_data,
    get_book_by_id,
    get_book_by_slug,
    get_external_payment,
    get_active_external_payment_for_book,
    expire_old_external_payments,
    get_setting,
    get_receipt_contact,
    get_stats,
    init_db,
    list_books,
    list_new_books,
    clear_new_books,
    list_customers,
    list_purchases,
    list_user_purchases,
    sales_by_book,
    seed_books,
    set_setting,
    restore_backup_data,
    get_db_schema_version,
    save_receipt_contact,
    receipt_customer_from_contact,
    update_book,
    update_external_payment_status,
    user_has_purchase,
)
from app.keyboards import after_purchase_menu, admin_backup_menu, admin_book_menu, admin_menu, book_formats_menu, book_menu, bookshelf_menu, main_menu, my_books_menu, payment_methods_menu, external_payment_menu
from app.proxy_manager import ProxyManager
from app.payments import build_provider
from app.payments.base import PaymentProviderError
from app.version import APP_NAME, APP_VERSION, BUILD_DATE, DB_SCHEMA_VERSION, version_line

router = Router()
settings: Settings
proxy_manager: ProxyManager | None = None
provider_runtime_errors: dict[str, str] = {}
provider_runtime_disabled: set[str] = set()

DEFAULT_BOOKS = [
    {
        'slug': 'book-1',
        'cover_path': 'books/book-1/cover.jpg',
        'title': 'Книга 1',
        'description': 'Демо-книга для проверки витрины, оплаты и выдачи файлов.',
        'price_stars': 99,
        'price_rub': 99,
        'file_paths': 'books/book-1/book-1.txt',
        'sort_order': 10,
        'is_new': 1,
    },
    {
        'slug': 'book-2',
        'cover_path': 'books/book-2/cover.jpg',
        'title': 'Книга 2',
        'description': 'Вторая демо-книга для шаблонного развертывания магазина.',
        'price_stars': 149,
        'price_rub': 149,
        'file_paths': 'books/book-2/book-2.txt',
        'sort_order': 20,
    },
    {
        'slug': 'book-3',
        'cover_path': 'books/book-3/cover.jpg',
        'title': 'Книга 3',
        'description': 'Третья демо-книга. Можно удалить или заменить через админку.',
        'price_stars': 199,
        'price_rub': 199,
        'file_paths': 'books/book-3/book-3.txt',
        'sort_order': 30,
    },
    {
        'slug': 'book-4',
        'cover_path': 'books/book-4/cover.jpg',
        'title': 'Книга 4',
        'description': 'Четвертая демо-книга для проверки порядка сортировки.',
        'price_stars': 249,
        'price_rub': 249,
        'file_paths': 'books/book-4/book-4.txt',
        'sort_order': 40,
    },
]

WELCOME = '''
📚 <b>Книжный магазин</b>

Выберите книгу, оплатите через Telegram Stars и сразу получите файлы.
'''

START_TEXT_FILE = Path('content/start/start.md')
START_IMAGE_FILE = Path('content/start/start.jpg')
AUTHOR_TEXT_FILE = Path('content/author/about.md')
AUTHOR_PHOTO_FILE = Path('content/author/photo.jpg')
BANNERS_DIR = Path('content/banners')
BANNER_FILES = {
    'start': BANNERS_DIR / 'start.jpg',
    'bookshelf': BANNERS_DIR / 'bookshelf.jpg',
    'author': BANNERS_DIR / 'author.jpg',
    'support': BANNERS_DIR / 'support.jpg',
}
DEFAULT_AUTHOR_TEXT = '👩 <b>Об авторе</b>\n\nЗдесь будет информация об авторе. Отредактируйте этот текст в админке.'
DEFAULT_SUPPORT_URL = '@kamenevabook_help_bot'

ADMIN_HELP = '''
🔐 <b>Админка книжного магазина v3.2</b>

Можно добавлять, редактировать и удалять книги.
Deep-link для книги:
<code>https://t.me/{bot_username}?start=book_&lt;slug&gt;</code>
'''


class AdminAddBook(StatesGroup):
    title = State()
    slug = State()
    description = State()
    price = State()
    files = State()
    cover = State()


class AdminEditBook(StatesGroup):
    value = State()


class AdminStartSettings(StatesGroup):
    text = State()
    image = State()


class AdminContentSettings(StatesGroup):
    author_text = State()
    author_photo = State()
    support_url = State()


class AdminBannerSettings(StatesGroup):
    image = State()


class AdminBackupRestore(StatesGroup):
    file = State()


class ReceiptFlow(StatesGroup):
    email = State()
    phone = State()


def is_admin(message_or_callback: Message | CallbackQuery) -> bool:
    user = message_or_callback.from_user
    return bool(user and user.id in settings.admin_ids)


def slugify(text: str) -> str:
    text = text.strip().lower()
    mapping = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z',
        'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r',
        'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya', '#': '',
    }
    text = ''.join(mapping.get(ch, ch) for ch in text)
    text = re.sub(r'[^a-z0-9]+', '-', text).strip('-')
    return text or 'book'




def default_start_text() -> str:
    if START_TEXT_FILE.exists():
        return START_TEXT_FILE.read_text(encoding='utf-8').strip() or WELCOME
    return WELCOME


def write_start_text(text: str) -> None:
    START_TEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
    START_TEXT_FILE.write_text(text or '', encoding='utf-8')




def banner_setting_key(name: str) -> str:
    return f'banner_{name}_path'


def default_banner_path(name: str) -> str:
    return BANNER_FILES.get(name, BANNERS_DIR / f'{name}.jpg').as_posix()


async def get_banner_path(name: str) -> Path | None:
    value = await get_setting(settings.db_path, banner_setting_key(name), default_banner_path(name))
    path = Path((value or '').strip())
    return path if path.exists() else None


async def answer_with_optional_banner(message: Message, banner_name: str, text: str, reply_markup=None) -> None:
    banner = await get_banner_path(banner_name)
    if banner:
        await message.answer_photo(FSInputFile(banner), caption=text, reply_markup=reply_markup)
    else:
        await message.answer(text, reply_markup=reply_markup)


async def save_plain_image_from_message(message: Message, destination: Path) -> str | None:
    source_obj = None
    if message.photo:
        source_obj = message.photo[-1]
    elif message.document and is_supported_image_filename(message.document.file_name):
        source_obj = message.document
    else:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    await message.bot.download(source_obj, destination=destination)
    return destination.as_posix()

def price_rub(book: dict[str, Any]) -> int:
    return int(book.get('price_rub') or book.get('price_stars') or 0)


def stars_for_rub(rub: int) -> int:
    rate = max(float(settings.stars_rub_per_star or 1), 0.01)
    return max(1, int(math.ceil(rub / rate)))


def book_price_stars(book: dict[str, Any]) -> int:
    return stars_for_rub(price_rub(book))


def format_price(book: dict[str, Any]) -> str:
    rub = price_rub(book)
    stars = book_price_stars(book)
    return f'{rub} ₽ / {stars} ⭐'



def provider_config_status(provider: str) -> tuple[bool, str]:
    """Return (available, human-readable reason).

    The bot must keep running even if an external payment system is enabled
    but not configured yet. Missing or broken providers are hidden from buyers
    and reported in logs/admin panel.
    """
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
        if not settings.receipt_require_contact and not settings.receipt_fallback_email:
            missing.append('RECEIPT_FALLBACK_EMAIL')
        if missing:
            return False, 'не заполнено: ' + ', '.join(missing)
        return True, 'OK'
    if provider == 'lava':
        missing = []
        if not settings.lava_shop_id:
            missing.append('LAVA_SHOP_ID')
        if not settings.lava_api_key:
            missing.append('LAVA_API_KEY')
        if missing:
            return False, 'не заполнено: ' + ', '.join(missing)
        return False, 'модуль подключен как заготовка, API создания платежа пока не реализован'
    if provider == 'platega':
        missing = []
        if not settings.platega_merchant_id:
            missing.append('PLATEGA_MERCHANT_ID')
        if not settings.platega_api_key:
            missing.append('PLATEGA_API_KEY')
        if missing:
            return False, 'не заполнено: ' + ', '.join(missing)
        return False, 'модуль подключен как заготовка, API создания платежа пока не реализован'
    return False, 'неизвестный провайдер'


def requested_payment_providers() -> list[str]:
    return list(settings.payment_providers or ['stars'])


def enabled_payment_providers() -> list[str]:
    return [p for p in requested_payment_providers() if provider_config_status(p)[0]]


def mark_provider_runtime_error(provider: str, reason: str) -> None:
    provider_runtime_errors[provider] = reason
    provider_runtime_disabled.add(provider)
    logging.error('Payment provider %s disabled at runtime: %s', provider, reason)


def payment_provider_title(provider: str) -> str:
    return {
        'stars': 'Telegram Stars',
        'yookassa': 'ЮKassa',
        'lava': 'Lava',
        'platega': 'Platega',
    }.get(provider, provider)

def book_dir_for_slug(book_slug: str) -> Path:
    safe_slug = slugify(book_slug)
    book_dir = Path('books') / safe_slug
    book_dir.mkdir(parents=True, exist_ok=True)
    return book_dir


def write_book_description(book_slug: str, description: str) -> str:
    book_dir = book_dir_for_slug(book_slug)
    path = book_dir / 'description.md'
    path.write_text(description or '', encoding='utf-8')
    return path.as_posix()


def write_book_meta(book: dict[str, Any]) -> str:
    slug = book.get('slug') or slugify(book.get('title') or 'book')
    book_dir = book_dir_for_slug(slug)
    meta = {
        'id': book.get('id'),
        'slug': slug,
        'title': book.get('title') or '',
        'description_file': 'description.md',
        'price_rub': price_rub(book),
        'price_stars': book_price_stars(book),
        'cover': Path(book.get('cover_path') or '').name if book.get('cover_path') else '',
        'files': [Path(p).name for p in normalize_file_list(book.get('file_paths'))],
        'is_active': int(book.get('is_active', 1)),
        'is_new': int(book.get('is_new', 0)),
        'sort_order': int(book.get('sort_order') or 100),
    }
    path = book_dir / 'meta.json'
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    return path.as_posix()


async def sync_book_folder(book: dict[str, Any]) -> None:
    # В папке книги всегда держим описание и meta.json.
    write_book_description(book.get('slug') or book.get('title') or 'book', book.get('description') or '')
    write_book_meta(book)


async def save_cover_from_message(message: Message, book_slug: str = 'cover') -> str | None:
    """
    Сохраняет обложку в books/<slug>/cover.jpg и cover_small.jpg.

    Админ может отправить картинку как фото или как документ JPG/PNG/WEBP.
    Исходник не кладем в папку книги: на выходе всегда нормализованный JPEG.
    """
    source_obj = None
    source_suffix = '.jpg'

    if message.photo:
        source_obj = message.photo[-1]
    elif message.document and is_supported_image_filename(message.document.file_name):
        source_obj = message.document
        source_suffix = Path(message.document.file_name or 'cover.jpg').suffix.lower() or '.jpg'
    else:
        return None

    book_dir = book_dir_for_slug(book_slug)
    book_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(prefix='cover_upload_', suffix=source_suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        await message.bot.download(source_obj, destination=tmp_path)
        result = process_cover_image(
            tmp_path,
            book_dir,
            cover_width=settings.cover_width,
            cover_height=settings.cover_height,
            cover_quality=settings.cover_quality,
            generate_thumbnail=settings.generate_cover_thumbnail,
            thumb_width=settings.cover_thumb_width,
            thumb_height=settings.cover_thumb_height,
            min_width=settings.cover_min_width,
            min_height=settings.cover_min_height,
        )
        logging.info(
            'Cover processed for %s: original=%sx%s cover=%sx%s thumb=%s',
            book_slug,
            result.original_size[0], result.original_size[1],
            result.cover_size[0], result.cover_size[1],
            f'{result.thumb_size[0]}x{result.thumb_size[1]}' if result.thumb_size else '-',
        )
        return result.cover_path.as_posix()
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()


ALLOWED_BOOK_EXTENSIONS = {'pdf', 'epub', 'fb2', 'docx', 'txt', 'mobi'}


def normalize_file_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.replace('\n', ',').replace(';', ',').split(',') if x.strip()]


def join_file_list(paths: list[str]) -> str:
    seen: list[str] = []
    for path in paths:
        if path and path not in seen:
            seen.append(path)
    return ','.join(seen)


async def save_book_file_from_message(message: Message, book_slug: str) -> str | None:
    """Сохраняет отправленный админом документ в books/<slug>/<slug>.<ext>."""
    if not message.document:
        return None

    original_name = message.document.file_name or ''
    ext = original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else ''
    if ext not in ALLOWED_BOOK_EXTENSIONS:
        await message.answer(
            'Неподдерживаемый формат файла. Можно: '
            f"<code>{', '.join(sorted(ALLOWED_BOOK_EXTENSIONS))}</code>"
        )
        return None

    safe_slug = slugify(book_slug)
    book_dir = book_dir_for_slug(safe_slug)
    file_path = book_dir / f'{safe_slug}.{ext}'
    await message.bot.download(message.document, destination=file_path)
    return file_path.as_posix()

def deep_link_arg(message: Message) -> str | None:
    if not message.text:
        return None
    parts = message.text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else None


def parse_book_arg(arg: str | None) -> str | None:
    if not arg:
        return None
    arg = arg.strip()
    for prefix in ('book_', 'book-', 'book:'):
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return arg




def default_author_text() -> str:
    if AUTHOR_TEXT_FILE.exists():
        return AUTHOR_TEXT_FILE.read_text(encoding='utf-8').strip() or DEFAULT_AUTHOR_TEXT
    return DEFAULT_AUTHOR_TEXT


def write_author_text(text: str) -> None:
    AUTHOR_TEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTHOR_TEXT_FILE.write_text(text, encoding='utf-8')


def _setting_bool(value: str, default: bool = True) -> bool:
    if value == '':
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'y', 'on', 'да'}


def support_to_url(value: str) -> str:
    value = (value or '').strip()
    if not value:
        value = DEFAULT_SUPPORT_URL
    if value.startswith('@'):
        return 'https://t.me/' + value[1:]
    if value.startswith('http://') or value.startswith('https://'):
        return value
    return 'https://t.me/' + value.lstrip('/')


async def get_main_menu_markup():
    author_enabled = _setting_bool(await get_setting(settings.db_path, 'author_enabled', '1'), True)
    support_enabled = _setting_bool(await get_setting(settings.db_path, 'support_enabled', '1'), True)
    support_value = await get_setting(settings.db_path, 'support_url', DEFAULT_SUPPORT_URL)
    new_books = await list_new_books(settings.db_path, active_only=True)
    return main_menu(
        author_enabled=author_enabled,
        support_enabled=support_enabled,
        support_url=support_to_url(support_value),
        has_new_books=bool(new_books),
    )

async def make_bot(settings: Settings) -> Bot:
    global proxy_manager
    proxy_manager = ProxyManager.from_env_string(
        settings.proxy,
        mode=settings.proxy_mode,
        healthcheck_url=settings.proxy_healthcheck_url,
        healthcheck_timeout=settings.proxy_healthcheck_timeout,
        healthcheck_interval=settings.proxy_healthcheck_interval,
    )

    # До создания Bot/AiohttpSession проверяем прокси не только в failover,
    # но и в sticky/rotate/random. Иначе первый нерабочий прокси может уронить
    # запуск уже на deleteWebhook, не дав менеджеру переключиться на следующий.
    if proxy_manager.has_proxies and proxy_manager.mode.value in {'failover', 'sticky', 'rotate', 'random'}:
        await proxy_manager.check_all()

    session = proxy_manager.get_session() or proxy_manager.get_session_sync()

    if session:
        logging.info('Proxy status: %s', proxy_manager.get_status())
        await proxy_manager.start_healthcheck_loop()
        return Bot(token=settings.bot_token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    logging.info('Proxy disabled or unavailable. Mode: %s', settings.proxy_mode)
    return Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def notify_admins(bot: Bot, text: str) -> None:
    if not settings.admin_notify_purchases:
        logging.info('Admin purchase notifications disabled')
        return
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logging.exception('Failed to notify admin %s', admin_id)


async def show_bookshelf(message: Message) -> None:
    books = await list_books(settings.db_path, active_only=True)
    text = '📚 <b>Книжная полка</b>' if books else '📚 <b>Книжная полка</b>\n\nПока книг нет.'
    await answer_with_optional_banner(message, 'bookshelf', text, bookshelf_menu(books))


async def show_book(message: Message, book: dict[str, Any]) -> None:
    badge = '✨ <b>Новинка</b>\n\n' if int(book.get('is_new') or 0) else ''
    pending_payment = None
    if message.from_user:
        pending_payment = await get_active_external_payment_for_book(
            settings.db_path,
            user_id=message.from_user.id,
            book_id=int(book['id']),
            ttl_minutes=settings.pending_payment_ttl_minutes,
        )

    pending_text = ''
    markup = book_menu(book['id'])
    if pending_payment:
        pending_text = (
            '\n\n💳 <b>Есть ожидающая оплата этой книги.</b>\n'
            'Если вы уже оплатили, нажмите «Проверить оплату». '
            f'Проверка доступна примерно {settings.pending_payment_ttl_minutes} мин.'
        )
        markup = external_payment_menu(
            int(pending_payment['id']),
            pending_payment.get('payment_url') or '',
            int(book['id']),
        )

    text = (
        f"📖 <b>{book['title']}</b>\n\n"
        f"{badge}"
        f"{book.get('description') or 'Описание скоро появится.'}\n\n"
        f"Стоимость: <b>{format_price(book)}</b>"
        f"{pending_text}"
    )
    cover_path = (book.get('cover_path') or '').strip()
    if cover_path and Path(cover_path).exists():
        await message.answer_photo(FSInputFile(cover_path), caption=text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def send_invoice(message: Message, book: dict[str, Any]) -> None:
    prices = [LabeledPrice(label=book['title'], amount=book_price_stars(book))]
    await message.answer_invoice(
        title=book['title'],
        description=book.get('description') or 'Электронная книга',
        payload=book_to_payload(book['id']),
        provider_token='',
        currency='XTR',
        prices=prices,
    )





def receipt_contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text='📱 Поделиться телефоном', request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder='Введите email или нажмите кнопку телефона',
    )


def is_valid_email(value: str) -> bool:
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', (value or '').strip()))


async def get_receipt_customer_for_payment(user_id: int) -> dict[str, str]:
    """Return saved receipt customer for external payments.

    If RECEIPT_REQUIRE_CONTACT=true, we must use the buyer's saved email/phone.
    If false, we use fallback email for tests.
    """
    if settings.receipt_require_contact:
        contact = await get_receipt_contact(settings.db_path, user_id)
        customer = receipt_customer_from_contact(contact)
        if customer:
            logging.info('Receipt contact found for user_id=%s: %s', user_id, 'phone' if 'phone' in customer else 'email')
        else:
            logging.info('Receipt contact not found for user_id=%s', user_id)
        return customer
    return receipt_customer_from_contact(None, settings.receipt_fallback_email)


async def ask_receipt_contact(message: Message, state: FSMContext, *, book_id: int, provider: str) -> None:
    await state.set_state(ReceiptFlow.email)
    await state.update_data(receipt_book_id=book_id, receipt_provider=provider)
    await message.answer(
        '🧾 <b>Данные для электронного чека</b>\n\n'
        'Для оплаты картой нужен email или телефон покупателя.\n'
        'Введите email сообщением или нажмите кнопку «Поделиться телефоном».\n\n'
        'После этого бот создаст ссылку на оплату.',
        reply_markup=receipt_contact_keyboard(),
    )

async def create_external_payment_and_send(message: Message, bot: Bot, book: dict[str, Any], provider_name: str, state: FSMContext | None = None) -> None:
    user = message.from_user
    if not user:
        await message.answer('Не удалось определить пользователя.')
        return

    me = await bot.get_me()
    return_url = f'https://t.me/{me.username}?start=book_{book["slug"]}'
    provider = build_provider(provider_name, settings)
    amount = price_rub(book)

    receipt_customer = None
    if provider_name in {'yookassa', 'lava', 'platega'}:
        receipt_customer = await get_receipt_customer_for_payment(user.id)
        if settings.receipt_require_contact and not receipt_customer:
            if state is None:
                await message.answer('Для оплаты нужен контакт для чека. Откройте покупку заново.')
                return
            await ask_receipt_contact(message, state, book_id=book['id'], provider=provider_name)
            return

    try:
        payment = await provider.create_payment(
            amount_rub=amount,
            description=f'Книга: {book["title"]}',
            return_url=return_url,
            metadata={'user_id': user.id, 'book_id': book['id'], 'book_slug': book['slug']},
            receipt_customer=receipt_customer,
        )
    except PaymentProviderError as e:
        reason = str(e)
        if any(x in reason.lower() for x in ('401', 'unauthorized', 'не настроена', 'credentials', 'shop_id', 'secret_key')):
            mark_provider_runtime_error(provider_name, reason)
        await message.answer(f'Не удалось создать оплату через {payment_provider_title(provider_name)}:\n<code>{e}</code>')
        return
    except Exception as e:
        logging.exception('External payment create failed provider=%s book_id=%s', provider_name, book['id'])
        await message.answer(f'Ошибка создания оплаты через {payment_provider_title(provider_name)}: <code>{e}</code>')
        return

    local_id = await add_external_payment(
        settings.db_path,
        provider=provider_name,
        provider_payment_id=payment.provider_payment_id,
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        book_id=book['id'],
        amount_rub=amount,
        currency='RUB',
        status=payment.status,
        payment_url=payment.payment_url,
        payload=book_to_payload(book['id']),
    )

    await message.answer(
        f'💳 <b>Оплата через {payment_provider_title(provider_name)}</b>\n\n'
        f'Книга: <b>{book["title"]}</b>\n'
        f'Сумма: <b>{amount} ₽</b>\n\n'
        'Нажмите «Перейти к оплате». После оплаты вернитесь в Telegram. Бот попробует проверить оплату автоматически. Если этого не произошло, нажмите «Проверить оплату».',
        reply_markup=external_payment_menu(local_id, payment.payment_url, book['id'])
    )

def format_label_for_file(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        '.pdf': 'PDF',
        '.epub': 'EPUB',
        '.fb2': 'FB2',
        '.mobi': 'MOBI',
        '.docx': 'DOCX',
        '.txt': 'TXT',
    }.get(suffix, suffix.upper().lstrip('.') or 'файл')


def find_book_file_by_ext(book: dict[str, Any], ext: str) -> Path | None:
    ext = ext.lower().lstrip('.')
    for file_path in book_files(book):
        path = Path(file_path)
        if path.exists() and path.suffix.lower().lstrip('.') == ext:
            return path
    return None


async def send_one_book_file(message: Message, book: dict[str, Any], path: Path) -> None:
    await message.answer_document(
        FSInputFile(path),
        caption=f"📚 {book['title']} — {format_label_for_file(path.as_posix())}"
    )
    await message.answer('Готово. Спасибо за покупку ❤️', reply_markup=after_purchase_menu(book['id']))


async def send_book_files(message: Message, book: dict[str, Any], *, force: bool = False, user_id: int | None = None) -> None:
    check_user_id = user_id or (message.from_user.id if message.from_user else 0)
    if not force and not await user_has_purchase(settings.db_path, check_user_id, book['id']):
        await message.answer('Покупка этой книги не найдена. Сначала нажмите «Купить книгу».', reply_markup=book_menu(book['id']))
        return

    files = [p for p in book_files(book) if Path(p).exists()]
    if not files:
        await message.answer(
            'Файлы книги пока не загружены на сервер. Напишите администратору.',
            reply_markup=after_purchase_menu(book['id'])
        )
        return

    if len(files) == 1:
        await send_one_book_file(message, book, Path(files[0]))
        return

    await message.answer(
        f"📚 <b>{book['title']}</b>\n\nВыберите формат книги:",
        reply_markup=book_formats_menu(book['id'], files)
    )


async def check_external_payment_and_deliver(message: Message, bot: Bot, payment: dict[str, Any], book: dict[str, Any]) -> bool:
    """Проверить внешний платеж и выдать книгу. Возвращает True, если оплата подтверждена."""
    user = message.from_user
    if not user:
        await message.answer('Не удалось определить пользователя.')
        return False

    provider_name = payment['provider']
    available, reason = provider_config_status(provider_name)
    if not available:
        await message.answer(
            f'Проверка оплаты через {payment_provider_title(provider_name)} сейчас недоступна.\n'
            f'Причина: <code>{reason}</code>'
        )
        return False

    try:
        provider = build_provider(provider_name, settings)
        status = await provider.get_status(payment['provider_payment_id'])
    except PaymentProviderError as e:
        reason = str(e)
        if any(x in reason.lower() for x in ('401', 'unauthorized', 'не настроена', 'credentials', 'shop_id', 'secret_key')):
            mark_provider_runtime_error(provider_name, reason)
        await message.answer(f'Не удалось проверить платеж через {payment_provider_title(provider_name)}:\n<code>{e}</code>')
        return False
    except Exception as e:
        logging.exception('External payment status failed provider=%s local_id=%s', provider_name, payment.get('id'))
        await message.answer(f'Ошибка проверки оплаты: <code>{e}</code>')
        return False

    await update_external_payment_status(settings.db_path, int(payment['id']), status.status, paid=status.paid)
    if not status.paid:
        await message.answer(
            f'Платеж пока не оплачен. Текущий статус: <code>{status.status}</code>\n\n'
            'Если вы только что оплатили, подождите 10–20 секунд и нажмите «Проверить оплату» еще раз.',
            reply_markup=external_payment_menu(int(payment['id']), payment.get('payment_url') or '', int(book['id']))
        )
        return False

    if not await user_has_purchase(settings.db_path, user.id, int(book['id'])):
        await add_purchase(
            settings.db_path,
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            book_id=int(book['id']),
            payload=payment.get('payload') or book_to_payload(int(book['id'])),
            currency='RUB',
            total_amount=int(payment['amount_rub']),
            telegram_payment_charge_id=f'{provider_name}:{payment["provider_payment_id"]}',
            provider_payment_charge_id=payment['provider_payment_id'],
        )
        logging.getLogger('purchases').info(
            'external_purchase provider=%s user_id=%s username=%s book_id=%s amount_rub=%s payment_id=%s',
            provider_name, user.id, user.username, book['id'], payment['amount_rub'], payment['provider_payment_id']
        )
        await notify_admins(
            bot,
            f"Новая покупка через {payment_provider_title(provider_name)}: {book['title']}\n"
            f"Пользователь: {user.full_name} / @{user.username}\n"
            f"ID: {user.id}\n"
            f"Сумма: {payment['amount_rub']} RUB"
        )

    await message.answer('✅ Оплата подтверждена. Сейчас подготовлю выдачу книги.', reply_markup=after_purchase_menu(int(book['id'])))
    await send_book_files(message, book, force=True)
    return True




async def show_start(message: Message) -> None:
    welcome_text = await get_setting(settings.db_path, 'welcome_text', default_start_text())
    # Старый параметр welcome_image_path сохраняем для совместимости,
    # но новый единый механизм баннеров использует content/banners/start.jpg.
    banner = await get_banner_path('start')
    if not banner:
        legacy = Path((await get_setting(settings.db_path, 'welcome_image_path', START_IMAGE_FILE.as_posix()) or '').strip())
        banner = legacy if legacy.exists() else None
    if banner:
        await message.answer_photo(FSInputFile(banner), caption=welcome_text, reply_markup=await get_main_menu_markup())
    else:
        await message.answer(welcome_text, reply_markup=await get_main_menu_markup())

async def show_admin_list(message: Message) -> None:
    books = await list_books(settings.db_path, active_only=False)
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    rows = []
    for b in books:
        status = '✅' if b['is_active'] else '⛔️'
        rows.append([InlineKeyboardButton(text=f"{status} {b['title']}", callback_data=f"admin:book:{b['id']}")])
    rows.append([InlineKeyboardButton(text='➕ Добавить книгу', callback_data='admin:add')])
    rows.append([InlineKeyboardButton(text='⬅️ Админка', callback_data='admin:home')])
    text = '📚 <b>Управление книгами</b>' if books else '📚 <b>Управление книгами</b>\n\nКниг пока нет.'
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))



async def admin_book_link(bot_username: str, slug: str) -> str:
    return f'https://t.me/{bot_username}?start=book_{slug}'


async def show_admin_book(message: Message, book_id: int, bot: Bot, note: str | None = None) -> None:
    book = await get_book_by_id(settings.db_path, book_id)
    if not book:
        await message.answer('Книга не найдена.', reply_markup=admin_menu())
        return
    me = await bot.get_me()
    status = '✅ В продаже' if book['is_active'] else '⛔️ Скрыта из каталога и продажи'
    link = await admin_book_link(me.username, book['slug'])
    files = '\n'.join(f'  • <code>{p}</code>' for p in book_files(book)) or '  -'
    prefix = f'{note}\n\n' if note else ''
    text = (
        f"{prefix}📖 <b>{book['title']}</b>\n"
        f"ID: <code>{book['id']}</code>\n"
        f"Slug: <code>{book['slug']}</code>\n"
        f"Цена: <b>{format_price(book)}</b>\n"
        f"Статус: <b>{status}</b>\n"
        f"Новинка: <b>{'Да' if int(book.get('is_new') or 0) else 'Нет'}</b>\n"
        f"Порядок: <code>{book['sort_order']}</code>\n\n"
        f"Ссылка для продажи:\n<code>{link}</code>\n\n"
        f"Обложка: <code>{book['cover_path'] or '-'}</code>\n"
        f"Файлы:\n{files}\n\n"
        f"Описание:\n{book.get('description') or '-'}"
    )
    await message.answer(text, reply_markup=admin_book_menu(book_id, book['is_active'], book.get('is_new')))


def backup_file_name() -> str:
    Path('backups').mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    return f'backups/backup_{stamp}.json'


async def create_backup_file() -> str:
    data = await export_backup_data(settings.db_path)
    path = Path(backup_file_name())
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return path.as_posix()


def list_backup_files() -> list[Path]:
    Path('backups').mkdir(parents=True, exist_ok=True)
    return sorted(Path('backups').glob('backup_*.json'), reverse=True)


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot) -> None:
    arg = parse_book_arg(deep_link_arg(message))
    if arg:
        book = await get_book_by_slug(settings.db_path, arg)
        if book and book.get('is_active'):
            pending = None
            if message.from_user:
                pending = await get_active_external_payment_for_book(
                    settings.db_path,
                    user_id=message.from_user.id,
                    book_id=int(book['id']),
                    ttl_minutes=settings.pending_payment_ttl_minutes,
                )
            if pending:
                paid = await check_external_payment_and_deliver(message, bot, pending, book)
                if paid:
                    return
            await show_book(message, book)
            return
        await message.answer('Эта книга пока не найдена или скрыта.', reply_markup=await get_main_menu_markup())
        return
    await show_start(message)



@router.message(Command('id'))
async def cmd_id(message: Message) -> None:
    user = message.from_user
    await message.answer(
        f'Ваш Telegram ID: <code>{user.id}</code>\n'
        f'Username: @{user.username or "-"}\n'
        f'Админ: {"да" if is_admin(message) else "нет"}'
    )

@router.message(Command('admin'))
async def cmd_admin(message: Message, bot: Bot) -> None:
    if not is_admin(message):
        await message.answer('Нет доступа.')
        return
    me = await bot.get_me()
    await message.answer(ADMIN_HELP.format(bot_username=me.username), reply_markup=admin_menu())


@router.message(Command('books'))
async def cmd_books(message: Message) -> None:
    await show_bookshelf(message)


@router.message(Command('get'))
async def cmd_get(message: Message) -> None:
    rows = await list_user_purchases(settings.db_path, message.from_user.id)
    if not rows:
        await message.answer('У вас пока нет покупок.', reply_markup=await get_main_menu_markup())
        return
    await message.answer('Ваши покупки:', reply_markup=my_books_menu(rows))


@router.message(Command('author'))
async def cmd_author(message: Message) -> None:
    enabled = _setting_bool(await get_setting(settings.db_path, 'author_enabled', '1'), True)
    if not enabled:
        await message.answer('Раздел «Об авторе» сейчас отключён.', reply_markup=await get_main_menu_markup())
        return
    text = await get_setting(settings.db_path, 'author_text', default_author_text())
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📚 Книжная полка', callback_data='bookshelf')],
        [InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')],
    ])
    await answer_with_optional_banner(message, 'author', text, kb)


@router.message(Command('support'))
async def cmd_support(message: Message) -> None:
    enabled = _setting_bool(await get_setting(settings.db_path, 'support_enabled', '1'), True)
    if not enabled:
        await message.answer('Поддержка сейчас отключена.', reply_markup=await get_main_menu_markup())
        return
    support_value = await get_setting(settings.db_path, 'support_url', DEFAULT_SUPPORT_URL)
    url = support_to_url(support_value)
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    await answer_with_optional_banner(
        message,
        'support',
        '🆘 <b>Поддержка</b>\n\nЕсли возникла проблема с оплатой или выдачей книги, напишите в поддержку.',
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Открыть поддержку', url=url)],
            [InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')],
        ])
    )


@router.callback_query(F.data == 'noop')
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == 'home')
async def cb_home(callback: CallbackQuery) -> None:
    await show_start(callback.message)
    await callback.answer()


@router.callback_query(F.data == 'new_books')
async def cb_new_books(callback: CallbackQuery) -> None:
    books = await list_new_books(settings.db_path, active_only=True)
    if books:
        await show_book(callback.message, books[0])
    else:
        await callback.message.answer('Сейчас новинок нет.', reply_markup=await get_main_menu_markup())
    await callback.answer()



@router.callback_query(F.data == 'about_author')
async def cb_about_author(callback: CallbackQuery) -> None:
    enabled = _setting_bool(await get_setting(settings.db_path, 'author_enabled', '1'), True)
    if not enabled:
        await callback.message.answer('Раздел «Об авторе» сейчас отключён.', reply_markup=await get_main_menu_markup())
        await callback.answer()
        return
    text = await get_setting(settings.db_path, 'author_text', default_author_text())
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📚 Книжная полка', callback_data='bookshelf')],
        [InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')],
    ])
    await answer_with_optional_banner(callback.message, 'author', text, kb)
    await callback.answer()



@router.callback_query(F.data == 'support')
async def cb_support(callback: CallbackQuery) -> None:
    enabled = _setting_bool(await get_setting(settings.db_path, 'support_enabled', '1'), True)
    if not enabled:
        await callback.message.answer('Поддержка сейчас отключена.', reply_markup=await get_main_menu_markup())
        await callback.answer()
        return
    support_value = await get_setting(settings.db_path, 'support_url', DEFAULT_SUPPORT_URL)
    url = support_to_url(support_value)
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    await answer_with_optional_banner(
        callback.message,
        'support',
        '🆘 <b>Поддержка</b>\n\nЕсли возникла проблема с оплатой или выдачей книги, напишите в поддержку.',
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Открыть поддержку', url=url)],
            [InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')],
        ])
    )
    await callback.answer()

@router.callback_query(F.data == 'bookshelf')
async def cb_bookshelf(callback: CallbackQuery) -> None:
    await show_bookshelf(callback.message)
    await callback.answer()


@router.callback_query(F.data == 'my_books')
async def cb_my_books(callback: CallbackQuery) -> None:
    rows = await list_user_purchases(settings.db_path, callback.from_user.id)
    if not rows:
        await callback.message.answer('У вас пока нет покупок. Откройте книжную полку и выберите книгу.', reply_markup=await get_main_menu_markup())
    else:
        await callback.message.answer('Ваши покупки:', reply_markup=my_books_menu(rows))
    await callback.answer()


@router.callback_query(F.data.startswith('book:'))
async def cb_book(callback: CallbackQuery) -> None:
    book = await get_book_by_id(settings.db_path, int(callback.data.split(':')[1]))
    if not book or not book.get('is_active'):
        await callback.message.answer('Книга не найдена.')
    else:
        await show_book(callback.message, book)
    await callback.answer()


@router.callback_query(F.data.startswith('buy:'))
async def cb_buy(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    book = await get_book_by_id(settings.db_path, int(callback.data.split(':')[1]))
    if not book or not book.get('is_active'):
        await callback.message.answer('Книга не найдена.')
        await callback.answer()
        return

    providers = enabled_payment_providers()
    if not providers:
        await callback.message.answer('Сейчас нет доступных способов оплаты. Администратору нужно проверить раздел «Платежные системы».')
        await callback.answer()
        return
    if len(providers) == 1:
        provider = providers[0]
        if provider == 'stars':
            await send_invoice(callback.message, book)
        else:
            await create_external_payment_and_send(callback.message, bot, book, provider, state)
    else:
        await callback.message.answer(
            f'Выберите способ оплаты для книги <b>{book["title"]}</b>:',
            reply_markup=payment_methods_menu(book['id'], providers)
        )
    await callback.answer()



@router.callback_query(F.data.startswith('pay:'))
async def cb_pay_provider(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    try:
        _, provider, book_id_text = callback.data.split(':', 2)
        book_id = int(book_id_text)
    except Exception:
        await callback.message.answer('Не удалось определить способ оплаты.')
        await callback.answer()
        return
    book = await get_book_by_id(settings.db_path, book_id)
    if not book or not book.get('is_active'):
        await callback.message.answer('Книга не найдена.')
        await callback.answer()
        return
    if provider not in enabled_payment_providers():
        await callback.message.answer('Этот способ оплаты сейчас отключён.')
        await callback.answer()
        return
    if provider == 'stars':
        await send_invoice(callback.message, book)
    else:
        await create_external_payment_and_send(callback.message, bot, book, provider, state)
    await callback.answer()


@router.callback_query(F.data.startswith('epay:check:'))
async def cb_external_payment_check(callback: CallbackQuery, bot: Bot) -> None:
    try:
        payment_id = int(callback.data.split(':', 2)[2])
    except Exception:
        await callback.message.answer('Не удалось определить платеж.')
        await callback.answer()
        return

    payment = await get_external_payment(settings.db_path, payment_id)
    if not payment:
        await callback.message.answer('Платеж не найден. Попробуйте оформить покупку заново.')
        await callback.answer()
        return
    if payment['user_id'] != callback.from_user.id and not is_admin(callback):
        await callback.answer('Это не ваш платеж.', show_alert=True)
        return

    book = await get_book_by_id(settings.db_path, int(payment['book_id']))
    if not book:
        await callback.message.answer('Книга по этому платежу не найдена.')
        await callback.answer()
        return

    provider_name = payment['provider']
    available, reason = provider_config_status(provider_name)
    if not available:
        await callback.message.answer(
            f'Проверка оплаты через {payment_provider_title(provider_name)} сейчас недоступна.\n'
            f'Причина: <code>{reason}</code>'
        )
        await callback.answer()
        return
    try:
        provider = build_provider(provider_name, settings)
        status = await provider.get_status(payment['provider_payment_id'])
    except PaymentProviderError as e:
        reason = str(e)
        if any(x in reason.lower() for x in ('401', 'unauthorized', 'не настроена', 'credentials', 'shop_id', 'secret_key')):
            mark_provider_runtime_error(provider_name, reason)
        await callback.message.answer(f'Не удалось проверить платеж через {payment_provider_title(provider_name)}:\n<code>{e}</code>')
        await callback.answer()
        return
    except Exception as e:
        logging.exception('External payment status failed provider=%s local_id=%s', provider_name, payment_id)
        await callback.message.answer(f'Ошибка проверки оплаты: <code>{e}</code>')
        await callback.answer()
        return

    await update_external_payment_status(settings.db_path, payment_id, status.status, paid=status.paid)
    if not status.paid:
        await callback.message.answer(
            f'Платеж пока не оплачен. Текущий статус: <code>{status.status}</code>\n\n'
            'Если вы только что оплатили, подождите 10–20 секунд и нажмите «Проверить оплату» еще раз.',
            reply_markup=external_payment_menu(payment_id, payment['payment_url'], book['id'])
        )
        await callback.answer()
        return

    # Защита от дублей: если покупка уже есть, просто выдаем файлы.
    if not await user_has_purchase(settings.db_path, callback.from_user.id, book['id']):
        await add_purchase(
            settings.db_path,
            user_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name,
            book_id=book['id'],
            payload=payment.get('payload') or book_to_payload(book['id']),
            currency='RUB',
            total_amount=int(payment['amount_rub']),
            telegram_payment_charge_id=f'{provider_name}:{payment["provider_payment_id"]}',
            provider_payment_charge_id=payment['provider_payment_id'],
        )
        logging.getLogger('purchases').info('external_purchase provider=%s user_id=%s username=%s book_id=%s amount_rub=%s payment_id=%s', provider_name, callback.from_user.id, callback.from_user.username, book['id'], payment['amount_rub'], payment['provider_payment_id'])
        await notify_admins(
            bot,
            f"Новая покупка через {payment_provider_title(provider_name)}: {book['title']}\n"
            f"Пользователь: {callback.from_user.full_name} / @{callback.from_user.username}\n"
            f"ID: {callback.from_user.id}\n"
            f"Сумма: {payment['amount_rub']} RUB"
        )

    await callback.message.answer('✅ Оплата подтверждена. Сейчас подготовлю выдачу книги.', reply_markup=after_purchase_menu(book['id']))
    await send_book_files(callback.message, book, force=True)
    await callback.answer()


@router.callback_query(F.data.startswith('get:'))
async def cb_get(callback: CallbackQuery) -> None:
    book = await get_book_by_id(settings.db_path, int(callback.data.split(':')[1]))
    if not book:
        await callback.message.answer('Книга не найдена.')
    else:
        await send_book_files(callback.message, book, user_id=callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data.startswith('fmt:'))
async def cb_format(callback: CallbackQuery) -> None:
    try:
        _, book_id_text, ext = callback.data.split(':', 2)
        book_id = int(book_id_text)
    except Exception:
        await callback.message.answer('Не удалось определить формат книги.')
        await callback.answer()
        return

    book = await get_book_by_id(settings.db_path, book_id)
    if not book:
        await callback.message.answer('Книга не найдена.')
        await callback.answer()
        return

    if not await user_has_purchase(settings.db_path, callback.from_user.id, book_id):
        await callback.message.answer('Покупка этой книги не найдена. Сначала нажмите «Купить книгу».', reply_markup=book_menu(book_id))
        await callback.answer()
        return

    path = find_book_file_by_ext(book, ext)
    if not path:
        await callback.message.answer('Этот формат пока не загружен или файл удалён.', reply_markup=after_purchase_menu(book_id))
        await callback.answer()
        return

    await send_one_book_file(callback.message, book, path)
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery, bot: Bot) -> None:
    payload = pre_checkout_query.invoice_payload
    if not payload.startswith('book:'):
        await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False, error_message='Неизвестный товар. Попробуйте заново.')
        return
    book = await get_book_by_id(settings.db_path, int(payload.split(':', 1)[1]))
    if not book or not book.get('is_active'):
        await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False, error_message='Книга недоступна.')
        return
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)




@router.message(ReceiptFlow.email)
async def receipt_contact_input(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    book_id = int(data.get('receipt_book_id') or 0)
    provider = str(data.get('receipt_provider') or '')
    if not book_id or not provider:
        await state.clear()
        await message.answer('Не удалось продолжить оплату. Откройте книгу и нажмите «Купить» еще раз.', reply_markup=ReplyKeyboardRemove())
        return

    receipt_email = ''
    receipt_phone = ''
    receipt_type = ''
    if message.contact and message.contact.phone_number:
        receipt_phone = message.contact.phone_number.strip()
        receipt_type = 'phone'
    else:
        value = (message.text or '').strip()
        if not is_valid_email(value):
            await message.answer('Похоже, это не email. Введите email вроде <code>name@example.com</code> или нажмите «Поделиться телефоном».')
            return
        receipt_email = value
        receipt_type = 'email'

    # В продакшн-режиме с обязательным чеком контакт нужно сохранять всегда,
    # иначе следующая покупка снова будет спрашивать email/телефон.
    # RECEIPT_SAVE_CONTACT=false имеет смысл только для экспериментов,
    # но не должен ломать сценарий REQUIRE_CONTACT.
    should_save_receipt_contact = settings.receipt_save_contact or settings.receipt_require_contact
    if should_save_receipt_contact:
        await save_receipt_contact(
            settings.db_path,
            user_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            receipt_type=receipt_type,
            receipt_email=receipt_email,
            receipt_phone=receipt_phone,
        )
        logging.info(
            'Receipt contact saved for user_id=%s type=%s',
            message.from_user.id,
            receipt_type,
        )
    else:
        logging.info('Receipt contact was not saved because RECEIPT_SAVE_CONTACT=false')
    await state.clear()
    book = await get_book_by_id(settings.db_path, book_id)
    if not book:
        await message.answer('Книга не найдена.', reply_markup=ReplyKeyboardRemove())
        return
    await message.answer('Контакт для чека сохранен. Создаю ссылку на оплату...', reply_markup=ReplyKeyboardRemove())
    await create_external_payment_and_send(message, bot, book, provider, state=None)

@router.message(F.successful_payment)
async def successful_payment(message: Message, bot: Bot) -> None:
    payment = message.successful_payment
    book_id = int(payment.invoice_payload.split(':', 1)[1]) if payment.invoice_payload.startswith('book:') else None
    book = await get_book_by_id(settings.db_path, book_id) if book_id else None
    user = message.from_user

    await add_purchase(
        settings.db_path,
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        book_id=book_id,
        payload=payment.invoice_payload,
        currency=payment.currency,
        total_amount=payment.total_amount,
        telegram_payment_charge_id=payment.telegram_payment_charge_id,
        provider_payment_charge_id=payment.provider_payment_charge_id,
    )

    logging.getLogger('purchases').info('purchase user_id=%s username=%s book_id=%s amount=%s currency=%s charge_id=%s', user.id, user.username, book_id, payment.total_amount, payment.currency, payment.telegram_payment_charge_id)
    await message.answer('✅ Оплата прошла. Сейчас подготовлю выдачу книги.', reply_markup=after_purchase_menu(book_id or 0))
    if book:
        await send_book_files(message, book, force=True)
        await notify_admins(bot, f"Новая покупка: {book['title']}\nПользователь: {user.full_name} / @{user.username}\nID: {user.id}\nСумма: {payment.total_amount} {payment.currency}")


# ---------------- Admin callbacks ----------------

@router.callback_query(F.data == 'admin:list')
async def cb_admin_list(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await show_admin_list(callback.message)
    await callback.answer()


@router.callback_query(F.data == 'admin:add')
async def cb_admin_add(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminAddBook.title)
    await callback.message.answer('Введите название книги:')
    await callback.answer()


@router.message(AdminAddBook.title)
async def admin_add_title(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    title = message.text.strip()
    await state.update_data(title=title, slug=slugify(title))
    await state.set_state(AdminAddBook.slug)
    await message.answer(f'Введите slug для ссылки или оставьте предложенный:\n<code>{slugify(title)}</code>')


@router.message(AdminAddBook.slug)
async def admin_add_slug(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    text = (message.text or '').strip()
    data = await state.get_data()
    await state.update_data(slug=slugify(text or data['slug']))
    await state.set_state(AdminAddBook.description)
    await message.answer('Введите описание книги:')


@router.message(AdminAddBook.description)
async def admin_add_description(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    await state.update_data(description=(message.text or '').strip())
    await state.set_state(AdminAddBook.price)
    await message.answer('Введите цену в рублях, например 299:')


@router.message(AdminAddBook.price)
async def admin_add_price(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    try:
        price = int((message.text or '').strip())
    except ValueError:
        await message.answer('Нужно число. Например: 299')
        return
    await state.update_data(price_rub=price, price_stars=stars_for_rub(price), file_paths='')
    await state.set_state(AdminAddBook.files)
    await message.answer(
        'Теперь отправьте файлы книги прямо сюда: PDF / EPUB / FB2 / DOCX.\n'
        'Можно отправить несколько файлов подряд. Я сохраню их автоматически в папку '
        '<code>books/slug_книги/</code>.\n\n'
        'Когда закончите — напишите <code>готово</code>.\n'
        'Если хотите указать старые пути вручную — отправьте их через запятую.'
    )


@router.message(AdminAddBook.files)
async def admin_add_files(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    data = await state.get_data()
    slug = data.get('slug') or slugify(data.get('title') or 'book')
    current_paths = normalize_file_list(data.get('file_paths'))

    if message.document:
        saved_path = await save_book_file_from_message(message, slug)
        if saved_path:
            current_paths.append(saved_path)
            await state.update_data(file_paths=join_file_list(current_paths))
            await message.answer(
                f'Файл сохранён: <code>{saved_path}</code>\n'
                'Можно отправить ещё файл или написать <code>готово</code>.'
            )
        return

    text = (message.text or '').strip()
    if text.lower() in {'готово', 'done', 'ok', 'ок', 'далее', '-'}:
        await state.update_data(file_paths=join_file_list(current_paths))
        await state.set_state(AdminAddBook.cover)
        await message.answer('Отправьте фото обложки, введите путь к файлу обложки или отправьте <code>-</code>, если пока без обложки:')
        return

    if text:
        manual_paths = normalize_file_list(text)
        current_paths.extend(manual_paths)
        await state.update_data(file_paths=join_file_list(current_paths))
        await state.set_state(AdminAddBook.cover)
        await message.answer(
            'Пути к файлам сохранены.\n'
            'Теперь отправьте обложку фото/документом JPG, PNG или WEBP. Бот сам приведёт её к нужному размеру.\n'
            'Можно ввести путь к файлу или отправить <code>-</code>, если пока без обложки:'
        )
        return

    await message.answer('Отправьте файл книги или напишите <code>готово</code>.')


@router.message(AdminAddBook.cover)
async def admin_add_cover(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message):
        return
    data = await state.get_data()
    saved_cover = await save_cover_from_message(message, data.get('slug') or data.get('title') or 'cover')
    cover_text = (message.text or '').strip()
    if saved_cover:
        data['cover_path'] = saved_cover
    else:
        data['cover_path'] = '' if cover_text == '-' else cover_text
    data['is_active'] = 1
    data['sort_order'] = 100
    try:
        book_id = await add_book(settings.db_path, data)
    except Exception as e:
        await message.answer(f'Ошибка добавления: <code>{e}</code>')
        await state.clear()
        return
    await state.clear()
    book = await get_book_by_id(settings.db_path, book_id)
    if book:
        await sync_book_folder(book)
    await show_admin_book(message, book_id, bot, note=f'Книга добавлена. ID: {book_id}')


@router.callback_query(F.data.startswith('admin:book:'))
async def cb_admin_book(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    book_id = int(callback.data.split(':')[2])
    await show_admin_book(callback.message, book_id, bot)
    await callback.answer()


@router.callback_query(F.data.startswith('admin:edit:'))
async def cb_admin_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    _, _, field, book_id = callback.data.split(':')
    await state.set_state(AdminEditBook.value)
    await state.update_data(book_id=int(book_id), field=field)
    if field == 'file_paths':
        await callback.message.answer(
            'Отправьте файл книги PDF / EPUB / FB2 / DOCX — я сохраню его в папку книги и добавлю к карточке.\n'
            'Можно отправлять несколько файлов подряд. Когда закончите — напишите <code>готово</code>.\n\n'
            'Или отправьте новый список путей через запятую, чтобы заменить текущий список.'
        )
    elif field == 'cover_path':
        await callback.message.answer('Отправьте новое фото обложки или путь к файлу:')
    else:
        await callback.message.answer('Введите новое значение для <code>sort_order</code>. Меньшее число = выше в каталоге.' if field == 'sort_order' else f'Введите новое значение для <code>{field}</code>.')
    await callback.answer()


@router.message(AdminEditBook.value)
async def admin_edit_value(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message):
        return
    data = await state.get_data()
    field = data['field']
    book_id = int(data['book_id'])
    book = await get_book_by_id(settings.db_path, book_id)
    if not book:
        await state.clear()
        await message.answer('Книга не найдена.', reply_markup=admin_menu())
        return

    if field == 'file_paths':
        text = (message.text or '').strip()
        current_paths = normalize_file_list(book.get('file_paths'))

        if message.document:
            saved_path = await save_book_file_from_message(message, book.get('slug') or f'book-{book_id}')
            if saved_path:
                current_paths.append(saved_path)
                await update_book(settings.db_path, book_id, {'file_paths': join_file_list(current_paths)})
                updated = await get_book_by_id(settings.db_path, book_id)
                if updated:
                    await sync_book_folder(updated)
                await message.answer(
                    f'Файл добавлен: <code>{saved_path}</code>\n'
                    'Можно отправить ещё файл или написать <code>готово</code>.'
                )
            return

        if text.lower() in {'готово', 'done', 'ok', 'ок'}:
            await state.clear()
            await show_admin_book(message, book_id, bot, note='Файлы сохранены.')
            return

        if text:
            await update_book(settings.db_path, book_id, {'file_paths': join_file_list(normalize_file_list(text))})
            updated = await get_book_by_id(settings.db_path, book_id)
            if updated:
                await sync_book_folder(updated)
            await state.clear()
            await show_admin_book(message, book_id, bot, note='Список файлов заменён.')
            return

        await message.answer('Отправьте файл книги, список путей или <code>готово</code>.')
        return

    value: Any = (message.text or '').strip()
    if field == 'cover_path' and (message.photo or (message.document and is_supported_image_filename(message.document.file_name))):
        value = await save_cover_from_message(message, book.get('slug') or f'book-{book_id}') or ''
    if field in {'price_stars', 'price_rub', 'sort_order', 'is_active'}:
        try:
            value = int(value)
        except ValueError:
            await message.answer('Нужно число.')
            return
    if field == 'slug':
        value = slugify(value)
    update_data = {field: value}
    if field == 'price_rub':
        update_data['price_stars'] = stars_for_rub(int(value))
    await update_book(settings.db_path, book_id, update_data)
    await state.clear()
    book = await get_book_by_id(settings.db_path, book_id)
    if book:
        await sync_book_folder(book)
    await show_admin_book(message, book_id, bot, note='Сохранено.')


@router.callback_query(F.data.startswith('admin:new:'))
async def cb_admin_new(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    book_id = int(callback.data.split(':')[2])
    book = await get_book_by_id(settings.db_path, book_id)
    if book:
        new_status = 0 if int(book.get('is_new') or 0) else 1
        if new_status:
            # Новинка может быть только одна: перед установкой новой снимаем флаг со всех книг.
            await clear_new_books(settings.db_path)
        await update_book(settings.db_path, book_id, {'is_new': new_status})
        updated = await get_book_by_id(settings.db_path, book_id)
        if updated:
            await sync_book_folder(updated)
        note = 'Книга стала единственной новинкой. С других книг флаг снят.' if new_status else 'Книга убрана из новинок.'
        await show_admin_book(callback.message, book_id, bot, note=note)
    await callback.answer()


@router.callback_query(F.data.startswith('admin:toggle:'))
async def cb_admin_toggle(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    book_id = int(callback.data.split(':')[2])
    book = await get_book_by_id(settings.db_path, book_id)
    if book:
        new_status = 0 if book['is_active'] else 1
        await update_book(settings.db_path, book_id, {'is_active': new_status})
        updated = await get_book_by_id(settings.db_path, book_id)
        if updated:
            await sync_book_folder(updated)
        note = 'Книга выключена: скрыта из каталога и недоступна для покупки.' if new_status == 0 else 'Книга включена: отображается в каталоге и доступна для покупки.'
        await show_admin_book(callback.message, book_id, bot, note=note)
    await callback.answer()


@router.callback_query(F.data.startswith('admin:preview:'))
async def cb_admin_preview(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    book_id = int(callback.data.split(':')[2])
    book = await get_book_by_id(settings.db_path, book_id)
    if book:
        await show_book(callback.message, book)
    else:
        await callback.message.answer('Книга не найдена.')
    await callback.answer()


@router.callback_query(F.data.startswith('admin:gift:self:'))
async def cb_admin_gift_self(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    book_id = int(callback.data.split(':')[3])
    book = await get_book_by_id(settings.db_path, book_id)
    if not book:
        await callback.message.answer('Книга не найдена.')
        await callback.answer()
        return
    user = callback.from_user
    await add_purchase(
        settings.db_path,
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        book_id=book_id,
        payload=book_to_payload(book_id),
        currency='XTR',
        total_amount=0,
        telegram_payment_charge_id=f'admin_gift_{user.id}_{book_id}_{int(datetime.now().timestamp())}',
        provider_payment_charge_id='admin_gift',
    )
    await callback.message.answer(f'Книга выдана тебе как тестовая покупка: {book["title"]}')
    await send_book_files(callback.message, book, force=True, user_id=user.id)
    await callback.answer()


@router.callback_query(F.data.startswith('admin:delete:'))
async def cb_admin_delete(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    book_id = int(callback.data.split(':')[2])
    await delete_book(settings.db_path, book_id)
    await callback.message.answer('Книга удалена.', reply_markup=admin_menu())
    await callback.answer()


@router.callback_query(F.data == 'admin:stats')
async def cb_admin_stats(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    st = await get_stats(settings.db_path)
    text = (
        '📊 <b>Статистика</b>\n\n'
        f"Книг всего: <b>{st['books_total']}</b>\n"
        f"Активных книг: <b>{st['books_active']}</b>\n"
        f"Покупок: <b>{st['purchases_total']}</b>\n"
        f"Покупателей: <b>{st['users_total']}</b>\n"
        f"Выручка: <b>{st['stars_total']} XTR</b>"
    )
    await callback.message.answer(text, reply_markup=admin_menu())
    await callback.answer()


@router.callback_query(F.data == 'admin:sales')
async def cb_admin_sales(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    rows = await list_purchases(settings.db_path, limit=30)
    if not rows:
        await callback.message.answer('Продаж пока нет.', reply_markup=admin_menu())
    else:
        lines = ['🧾 <b>Последние продажи</b>\n']
        for r in rows:
            user = r.get('full_name') or '-'
            username = f"@{r['username']}" if r.get('username') else '-'
            title = r.get('book_title') or f"Книга #{r.get('book_id')}"
            lines.append(f"• <b>{title}</b> — {r['total_amount']} {r['currency']}\n  {user} / {username} / <code>{r['user_id']}</code>")
        await callback.message.answer('\n'.join(lines), reply_markup=admin_menu())
    await callback.answer()


@router.callback_query(F.data == 'admin:users')
async def cb_admin_users(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    rows = await list_customers(settings.db_path, limit=50)
    if not rows:
        await callback.message.answer('Покупателей пока нет.', reply_markup=admin_menu())
    else:
        lines = ['👥 <b>Покупатели</b>\n']
        for r in rows:
            username = f"@{r['username']}" if r.get('username') else '-'
            name = r.get('full_name') or '-'
            lines.append(f"• {name} / {username} / <code>{r['user_id']}</code>\n  Покупок: {r['purchases_count']}, сумма: {r['total_amount']} XTR")
        await callback.message.answer('\n'.join(lines), reply_markup=admin_menu())
    await callback.answer()


@router.callback_query(F.data == 'admin:booksales')
async def cb_admin_booksales(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    rows = await sales_by_book(settings.db_path)
    lines = ['🏆 <b>Продажи по книгам</b>\n']
    for r in rows:
        lines.append(f"• <b>{r['title']}</b> — {r['purchases_count']} шт., {r['total_amount']} XTR")
    await callback.message.answer('\n'.join(lines), reply_markup=admin_menu())
    await callback.answer()






@router.callback_query(F.data == 'admin:payments')
async def cb_admin_payments(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    lines = ['💳 <b>Платежные системы</b>\n']
    lines.append(f'Запрошено в PAYMENT_PROVIDERS: <code>{", ".join(requested_payment_providers())}</code>')
    lines.append(f'Доступно покупателю: <code>{", ".join(enabled_payment_providers()) or "нет"}</code>\n')
    for provider in ['stars', 'yookassa', 'lava', 'platega']:
        requested = provider in requested_payment_providers()
        available, reason = provider_config_status(provider)
        if available:
            status = '✅ OK'
        elif requested:
            status = '⚠️ Недоступна'
        else:
            status = '⏸ Выключена'
            reason = 'не указана в PAYMENT_PROVIDERS'
        lines.append(f'<b>{payment_provider_title(provider)}</b>')
        lines.append(f'Статус: {status}')
        lines.append(f'Причина: <code>{reason}</code>')
        if provider == 'yookassa':
            lines.append(f'SHOP_ID: <code>{settings.yookassa_shop_id or "-"}</code>')
            lines.append(f'SECRET_KEY: <code>{"заполнен" if settings.yookassa_secret_key else "-"}</code>')
        lines.append('')
    await callback.message.answer('\n'.join(lines), reply_markup=admin_menu())
    await callback.answer()



@router.callback_query(F.data == 'admin:system')
async def cb_admin_system(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    import platform
    me = await bot.get_me()
    stats = await get_stats(settings.db_path)
    books = await list_books(settings.db_path, active_only=False)
    db_schema = await get_db_schema_version(settings.db_path)
    proxy_status = proxy_manager.get_status() if proxy_manager else {}
    lines = [
        f'ℹ️ <b>{APP_NAME}</b>',
        '',
        f'Версия: <code>{APP_VERSION}</code>',
        f'Build: <code>{BUILD_DATE}</code>',
        f'Бот: <code>@{me.username}</code>',
        '',
        f'Схема БД: <code>{db_schema}</code> / ожидается <code>{DB_SCHEMA_VERSION}</code>',
        f'Файл БД: <code>{settings.db_path}</code>',
        '',
        f'Книг: <code>{len(books)}</code>',
        f'Пользователей: <code>{stats.get("users_total")}</code>',
        f'Покупок: <code>{stats.get("purchases_total")}</code>',
        f'Админов: <code>{len(settings.admin_ids)}</code>',
        '',
        f'Python: <code>{platform.python_version()}</code>',
        f'Прокси: <code>{settings.proxy_mode}</code>',
        f'Активный прокси: <code>{proxy_status.get("current_proxy") if isinstance(proxy_status, dict) else "-"}</code>',
        '',
        f'Платежки: <code>{", ".join(enabled_payment_providers()) or "нет"}</code>',
        f'Чеки: <code>{"запрашивать контакт" if settings.receipt_require_contact else "fallback email"}</code>',
        f'Лог: <code>{settings.log_file}</code>',
    ]
    await callback.message.answer('\n'.join(lines), reply_markup=admin_menu())
    await callback.answer()


@router.callback_query(F.data == 'admin:backup')
async def cb_admin_backup(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await callback.message.answer(
        '📦 <b>Бэкап / восстановление</b>\n\n'
        'Бэкап сохраняет пользователей и покупки в JSON. Книги и файлы остаются в папке <code>books/</code>.',
        reply_markup=admin_backup_menu()
    )
    await callback.answer()


@router.callback_query(F.data == 'admin:backup:create')
async def cb_admin_backup_create(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    path = await create_backup_file()
    await callback.message.answer_document(FSInputFile(path), caption=f'Бэкап создан: <code>{path}</code>', reply_markup=admin_backup_menu())
    await callback.answer()


@router.callback_query(F.data == 'admin:backup:list')
async def cb_admin_backup_list(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    files = list_backup_files()
    if not files:
        await callback.message.answer('Бэкапов пока нет.', reply_markup=admin_backup_menu())
        await callback.answer()
        return
    lines = ['📋 <b>Бэкапы на сервере</b>\n']
    for idx, path in enumerate(files[:20], start=1):
        size_kb = path.stat().st_size / 1024
        lines.append(f'{idx}. <code>{path.as_posix()}</code> — {size_kb:.1f} КБ')
    await callback.message.answer('\n'.join(lines), reply_markup=admin_backup_menu())
    for path in files[:5]:
        await callback.message.answer_document(FSInputFile(path), caption=path.name)
    await callback.answer()


@router.callback_query(F.data == 'admin:backup:restore')
async def cb_admin_backup_restore(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await state.set_state(AdminBackupRestore.file)
    await callback.message.answer(
        'Отправьте JSON-файл бэкапа документом.\n\n'
        'Восстановятся покупки и покупатели. Файлы книг из папки <code>books/</code> не трогаются.'
    )
    await callback.answer()


@router.message(AdminBackupRestore.file)
async def admin_backup_restore_file(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    if not message.document:
        await message.answer('Нужно отправить JSON-файл документом.')
        return
    Path('backups/restore_uploads').mkdir(parents=True, exist_ok=True)
    file_name = message.document.file_name or 'restore.json'
    if not file_name.lower().endswith('.json'):
        await message.answer('Это не JSON-файл.')
        return
    dst = Path('backups/restore_uploads') / file_name
    await message.bot.download(message.document, destination=dst)
    try:
        data = json.loads(dst.read_text(encoding='utf-8'))
    except Exception as e:
        await message.answer(f'Не смог прочитать JSON: <code>{e}</code>')
        return
    result = await restore_backup_data(settings.db_path, data)
    await state.clear()
    await message.answer(
        'Восстановление завершено.\n'
        f'Добавлено покупок: <b>{result["inserted"]}</b>\n'
        f'Пропущено дублей/ошибок: <b>{result["skipped"]}</b>',
        reply_markup=admin_backup_menu()
    )


@router.callback_query(F.data == 'admin:start_settings')
async def cb_admin_start_settings(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    current_text = await get_setting(settings.db_path, 'welcome_text', default_start_text())
    current_image = await get_setting(settings.db_path, 'banner_start_path', default_banner_path('start'))
    text = (
        '⚙️ <b>Старт бота</b>\n\n'
        f'Картинка: <code>{current_image or "-"}</code>\n\n'
        'Текущий текст:\n'
        f'{current_text}'
    )
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✏️ Изменить текст', callback_data='admin:start:text')],
        [InlineKeyboardButton(text='🖼 Изменить картинку', callback_data='admin:start:image')],
        [InlineKeyboardButton(text='⬅️ Админка', callback_data='admin:home')],
    ])
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == 'admin:home')
async def cb_admin_home(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    me = await bot.get_me()
    await callback.message.answer(ADMIN_HELP.format(bot_username=me.username), reply_markup=admin_menu())
    await callback.answer()


@router.callback_query(F.data == 'admin:start:text')
async def cb_admin_start_text(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await state.set_state(AdminStartSettings.text)
    await callback.message.answer('Отправьте новый текст стартового сообщения. Можно использовать HTML: <b>, <i>, <code>.', reply_markup=admin_menu())
    await callback.answer()


@router.message(AdminStartSettings.text)
async def admin_start_text(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    new_text = message.html_text or message.text or ''
    write_start_text(new_text)
    await set_setting(settings.db_path, 'welcome_text', new_text)
    await state.clear()
    await message.answer('Стартовый текст сохранён.', reply_markup=admin_menu())


@router.callback_query(F.data == 'admin:start:image')
async def cb_admin_start_image(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await state.set_state(AdminStartSettings.image)
    await callback.message.answer('Отправьте фото для стартового сообщения или путь к файлу, например: <code>content/start/start.jpg</code>', reply_markup=admin_menu())
    await callback.answer()


@router.message(AdminStartSettings.image)
async def admin_start_image(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    saved_path = None
    if message.photo:
        START_IMAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        saved_path = START_IMAGE_FILE.as_posix()
        await message.bot.download(message.photo[-1], destination=saved_path)
    else:
        saved_path = (message.text or '').strip()
    if not saved_path:
        await message.answer('Не получил ни фото, ни путь к файлу.')
        return
    await set_setting(settings.db_path, 'banner_start_path', saved_path)
    await set_setting(settings.db_path, 'welcome_image_path', saved_path)
    await state.clear()
    await message.answer(f'Стартовая картинка сохранена: <code>{saved_path}</code>', reply_markup=admin_menu())



@router.callback_query(F.data == 'admin:content_settings')
async def cb_admin_content_settings(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    author_enabled = _setting_bool(await get_setting(settings.db_path, 'author_enabled', '1'), True)
    support_enabled = _setting_bool(await get_setting(settings.db_path, 'support_enabled', '1'), True)
    support_url = await get_setting(settings.db_path, 'support_url', DEFAULT_SUPPORT_URL)
    author_photo = await get_setting(settings.db_path, 'author_photo_path', AUTHOR_PHOTO_FILE.as_posix())
    text = (
        '👩 <b>Автор и поддержка</b>\n\n'
        f'Об авторе: <b>{"включено" if author_enabled else "выключено"}</b>\n'
        f'Фото автора: <code>{author_photo or "-"}</code>\n\n'
        f'Поддержка: <b>{"включена" if support_enabled else "выключена"}</b>\n'
        f'Аккаунт/ссылка: <code>{support_url or "-"}</code>'
    )
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='👩 Вкл/выкл «Об авторе»', callback_data='admin:author:toggle')],
        [InlineKeyboardButton(text='📝 Текст автора', callback_data='admin:author:text'), InlineKeyboardButton(text='🖼 Фото автора', callback_data='admin:author:photo')],
        [InlineKeyboardButton(text='🆘 Вкл/выкл поддержку', callback_data='admin:support:toggle')],
        [InlineKeyboardButton(text='🔗 Аккаунт поддержки', callback_data='admin:support:url')],
        [InlineKeyboardButton(text='⬅️ Админка', callback_data='admin:home')],
    ])
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == 'admin:author:toggle')
async def cb_admin_author_toggle(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    current = _setting_bool(await get_setting(settings.db_path, 'author_enabled', '1'), True)
    new_value = '0' if current else '1'
    await set_setting(settings.db_path, 'author_enabled', new_value)
    await callback.message.answer('Раздел «Об авторе» выключен.' if current else 'Раздел «Об авторе» включен.')
    await cb_admin_content_settings(callback)


@router.callback_query(F.data == 'admin:support:toggle')
async def cb_admin_support_toggle(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    current = _setting_bool(await get_setting(settings.db_path, 'support_enabled', '1'), True)
    new_value = '0' if current else '1'
    await set_setting(settings.db_path, 'support_enabled', new_value)
    await callback.message.answer('Кнопка поддержки выключена.' if current else 'Кнопка поддержки включена.')
    await cb_admin_content_settings(callback)


@router.callback_query(F.data == 'admin:author:text')
async def cb_admin_author_text(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await state.set_state(AdminContentSettings.author_text)
    await callback.message.answer('Отправьте новый текст для раздела «Об авторе». Можно использовать HTML: <b>, <i>, <code>.')
    await callback.answer()


@router.message(AdminContentSettings.author_text)
async def admin_author_text(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    new_text = message.html_text or message.text or ''
    write_author_text(new_text)
    await set_setting(settings.db_path, 'author_text', new_text)
    await state.clear()
    await message.answer('Текст «Об авторе» сохранён.', reply_markup=admin_menu())


@router.callback_query(F.data == 'admin:author:photo')
async def cb_admin_author_photo(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await state.set_state(AdminContentSettings.author_photo)
    await callback.message.answer('Отправьте фото автора или путь к файлу, например: <code>content/author/photo.jpg</code>')
    await callback.answer()


@router.message(AdminContentSettings.author_photo)
async def admin_author_photo(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    saved_path = None
    if message.photo:
        AUTHOR_PHOTO_FILE.parent.mkdir(parents=True, exist_ok=True)
        saved_path = AUTHOR_PHOTO_FILE.as_posix()
        await message.bot.download(message.photo[-1], destination=saved_path)
    else:
        saved_path = (message.text or '').strip()
    if not saved_path:
        await message.answer('Не получил ни фото, ни путь к файлу.')
        return
    await set_setting(settings.db_path, 'author_photo_path', saved_path)
    await state.clear()
    await message.answer(f'Фото автора сохранено: <code>{saved_path}</code>', reply_markup=admin_menu())


@router.callback_query(F.data == 'admin:support:url')
async def cb_admin_support_url(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    await state.set_state(AdminContentSettings.support_url)
    await callback.message.answer('Отправьте аккаунт или ссылку поддержки. Например: <code>@kamenevabook_help_bot</code>')
    await callback.answer()


@router.message(AdminContentSettings.support_url)
async def admin_support_url(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    value = (message.text or '').strip() or DEFAULT_SUPPORT_URL
    await set_setting(settings.db_path, 'support_url', value)
    await state.clear()
    await message.answer(f'Поддержка сохранена: <code>{value}</code>', reply_markup=admin_menu())



@router.callback_query(F.data == 'admin:banners')
async def cb_admin_banners(callback: CallbackQuery) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    names = {
        'start': 'Главное меню',
        'bookshelf': 'Книжная полка',
        'author': 'Об авторе',
        'support': 'Поддержка',
    }
    lines = ['🖼 <b>Баннеры экранов</b>\n']
    for key, title in names.items():
        path = await get_setting(settings.db_path, banner_setting_key(key), default_banner_path(key))
        exists = 'есть' if Path(path).exists() else 'нет'
        lines.append(f'{title}: <code>{path}</code> — {exists}')
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🏠 Главное меню', callback_data='admin:banner:start')],
        [InlineKeyboardButton(text='📚 Книжная полка', callback_data='admin:banner:bookshelf')],
        [InlineKeyboardButton(text='👩 Об авторе', callback_data='admin:banner:author')],
        [InlineKeyboardButton(text='🆘 Поддержка', callback_data='admin:banner:support')],
        [InlineKeyboardButton(text='⬅️ Админка', callback_data='admin:home')],
    ])
    await callback.message.answer('\n'.join(lines), reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith('admin:banner:'))
async def cb_admin_banner_select(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback):
        await callback.answer('Нет доступа', show_alert=True)
        return
    banner_name = callback.data.split(':', 2)[2]
    allowed = {'start', 'bookshelf', 'author', 'support'}
    if banner_name not in allowed:
        await callback.answer('Неизвестный баннер', show_alert=True)
        return
    await state.set_state(AdminBannerSettings.image)
    await state.update_data(banner_name=banner_name)
    await callback.message.answer(
        f'Отправьте картинку для баннера <b>{banner_name}</b>.\n'
        f'Она будет сохранена в <code>{default_banner_path(banner_name)}</code>.\n\n'
        'Можно отправить фото или файл JPG/PNG/WEBP.'
    )
    await callback.answer()


@router.message(AdminBannerSettings.image)
async def admin_banner_image(message: Message, state: FSMContext) -> None:
    if not is_admin(message):
        return
    data = await state.get_data()
    banner_name = data.get('banner_name') or 'start'
    destination = Path(default_banner_path(banner_name))
    saved_path = await save_plain_image_from_message(message, destination)
    if not saved_path:
        typed_path = (message.text or '').strip()
        if typed_path:
            saved_path = typed_path
        else:
            await message.answer('Не получил картинку. Отправьте фото или файл JPG/PNG/WEBP.')
            return
    await set_setting(settings.db_path, banner_setting_key(banner_name), saved_path)
    if banner_name == 'start':
        await set_setting(settings.db_path, 'welcome_image_path', saved_path)
    await state.clear()
    await message.answer(f'Баннер сохранён: <code>{saved_path}</code>', reply_markup=admin_menu())

@router.message()
async def unhandled_message(message: Message) -> None:
    logging.info('Unhandled message from %s: %r', message.from_user.id if message.from_user else None, message.text)
    await message.answer('Не понял команду. Нажмите /start. Для админки — /admin.')


@router.callback_query()
async def unhandled_callback(callback: CallbackQuery) -> None:
    logging.info('Unhandled callback from %s: %r', callback.from_user.id if callback.from_user else None, callback.data)
    await callback.answer('Эта кнопка устарела. Нажмите /start.', show_alert=True)




def setup_logging(cfg: Settings) -> None:
    Path(cfg.log_file).parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, cfg.log_level, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(cfg.log_file, maxBytes=cfg.log_max_bytes, backupCount=cfg.log_backup_count, encoding='utf-8')
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    purchases_log = logging.getLogger('purchases')
    purchases_log.setLevel(logging.INFO)
    purchases_handler = RotatingFileHandler('logs/purchases.log', maxBytes=cfg.log_max_bytes, backupCount=cfg.log_backup_count, encoding='utf-8')
    purchases_handler.setFormatter(formatter)
    purchases_log.addHandler(purchases_handler)
    purchases_log.propagate = True




def _commands_equal(current: list[BotCommand], desired: list[BotCommand]) -> bool:
    return [(c.command, c.description) for c in current] == [(c.command, c.description) for c in desired]


async def _set_commands_if_changed(bot: Bot, commands: list[BotCommand], scope, label: str) -> None:
    try:
        current = await bot.get_my_commands(scope=scope)
        if _commands_equal(current, commands):
            logging.info('Telegram commands unchanged: %s', label)
            return
        await bot.set_my_commands(commands, scope=scope)
        logging.info('Telegram commands updated: %s', label)
    except Exception as e:
        logging.warning('Cannot setup Telegram commands for %s: %s', label, e)


async def setup_bot_commands_and_menu(bot: Bot) -> None:
    """Register a minimal Telegram command menu.

    Public users see only /start. Admins additionally see /admin in their
    personal command window. The setup is idempotent: if commands are already
    correct, the bot does not rewrite them on every startup.
    """
    if not settings.auto_setup_bot_menu:
        logging.info('Auto setup of Telegram commands/menu is disabled.')
        return

    public_commands = [
        BotCommand(command='start', description='✳️ Главное меню'),
    ]
    admin_commands = [
        BotCommand(command='start', description='✳️ Главное меню'),
        BotCommand(command='admin', description='⚙️ Админ-панель'),
    ]

    await _set_commands_if_changed(bot, public_commands, BotCommandScopeDefault(), 'default')

    for admin_id in settings.admin_ids:
        await _set_commands_if_changed(
            bot,
            admin_commands,
            BotCommandScopeChat(chat_id=admin_id),
            f'admin chat {admin_id}',
        )

    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logging.info('Telegram menu button is set to Commands.')
    except Exception as e:
        logging.warning('Cannot set Telegram menu button automatically: %s', e)


async def log_startup_summary(bot: Bot, started_at: float) -> None:
    me = await bot.get_me()
    stats = await get_stats(settings.db_path)
    books = await list_books(settings.db_path, active_only=False)
    proxy_status = proxy_manager.get_status() if proxy_manager else {}
    logging.info('=' * 54)
    logging.info('%s', version_line())
    logging.info('Build date: %s', BUILD_DATE)
    logging.info('Bot: @%s', me.username)
    logging.info('DB schema: %s / expected %s', await get_db_schema_version(settings.db_path), DB_SCHEMA_VERSION)
    logging.info('DB path: %s', settings.db_path)
    logging.info('Proxy mode: %s', settings.proxy_mode)
    logging.info('Proxy active: %s', proxy_status.get('current_proxy') if isinstance(proxy_status, dict) else '-')
    logging.info('Books loaded: %s', len(books))
    logging.info('Users: %s', stats.get('users_total'))
    logging.info('Purchases: %s', stats.get('purchases_total'))
    logging.info('Admins: %s', len(settings.admin_ids))
    logging.info('Stars rate: 1 ⭐ = %s ₽', settings.stars_rub_per_star)
    logging.info('Payment providers requested: %s', ', '.join(requested_payment_providers()))
    logging.info('Payment providers enabled: %s', ', '.join(enabled_payment_providers()) or 'none')
    logging.info('Receipt mode: %s', 'require_contact' if settings.receipt_require_contact else 'fallback_email')
    logging.info('Receipt fallback email: %s', settings.receipt_fallback_email or '-')
    logging.info('Receipt save contact: %s', settings.receipt_save_contact)
    logging.info('Cover target: %sx%s jpg quality=%s', settings.cover_width, settings.cover_height, settings.cover_quality)
    logging.info('Cover thumbnail: %s %sx%s', settings.generate_cover_thumbnail, settings.cover_thumb_width, settings.cover_thumb_height)
    for provider in requested_payment_providers():
        available, reason = provider_config_status(provider)
        logging.info('Payment provider %s: %s (%s)', provider, 'OK' if available else 'DISABLED', reason)
    logging.info('Log file: %s', settings.log_file)
    logging.info('Startup completed in %.2f sec', time.perf_counter() - started_at)
    logging.info('=' * 54)


async def main() -> None:
    global settings
    started_at = time.perf_counter()
    settings = get_settings()
    setup_logging(settings)
    await init_db(settings.db_path)
    if settings.seed_books_on_start:
        await seed_books(settings.db_path, DEFAULT_BOOKS)
    for book in await list_books(settings.db_path, active_only=False):
        await sync_book_folder(book)

    bot = await make_bot(settings)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    if settings.delete_webhook_on_start:
        logging.info('Deleting webhook before polling. drop_pending_updates=%s', settings.drop_pending_updates)
        await bot.delete_webhook(drop_pending_updates=settings.drop_pending_updates)

    await setup_bot_commands_and_menu(bot)
    await log_startup_summary(bot, started_at)
    try:
        await dp.start_polling(bot)
    except asyncio.CancelledError:
        logging.info('Polling cancelled. Shutdown requested.')
        raise
    finally:
        logging.info('Saving state...')
        logging.info('Stopping background tasks and closing Telegram session...')
        if proxy_manager:
            with contextlib.suppress(Exception):
                await proxy_manager.stop_healthcheck_loop()
        with contextlib.suppress(Exception):
            await bot.session.close()
        logging.info('Shutdown complete.')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info('Bot stopped by user (Ctrl+C).')
