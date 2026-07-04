from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu(*, author_enabled: bool = True, support_enabled: bool = True, support_url: str = '', has_new_books: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if author_enabled:
        rows.append([InlineKeyboardButton(text='👩 Об авторе', callback_data='about_author')])
    if has_new_books:
        rows.append([InlineKeyboardButton(text='✨ Новинка', callback_data='new_books')])
    rows.append([InlineKeyboardButton(text='📚 Книжная полка', callback_data='bookshelf')])
    rows.append([InlineKeyboardButton(text='🛒 Мои покупки', callback_data='my_books')])
    if support_enabled and support_url:
        rows.append([InlineKeyboardButton(text='🆘 Поддержка', callback_data='support')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def book_menu(book_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🛒 Купить книгу', callback_data=f'buy:{book_id}')],
        [InlineKeyboardButton(text='📥 Получить покупку', callback_data=f'get:{book_id}')],
        [InlineKeyboardButton(text='⬅️ К книжной полке', callback_data='bookshelf')],
        [InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')],
    ])


def bookshelf_menu(books: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for b in books:
        rows.append([InlineKeyboardButton(text=b['title'], callback_data=f"book:{b['id']}")])
    if not rows:
        rows = [[InlineKeyboardButton(text='Пока книг нет', callback_data='noop')]]
    rows.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def my_books_menu(rows_data: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for item in rows_data:
        title = item.get('title') or item.get('book_title') or f"Книга #{item.get('book_id')}"
        rows.append([InlineKeyboardButton(text=f'Получить: {title}', callback_data=f"get:{item['book_id']}")])
    rows.append([InlineKeyboardButton(text='⬅️ К книжной полке', callback_data='bookshelf')])
    rows.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def after_purchase_menu(book_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📥 Получить файлы еще раз', callback_data=f'get:{book_id}')],
        [InlineKeyboardButton(text='⬅️ К книжной полке', callback_data='bookshelf')],
        [InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')],
    ])



def book_formats_menu(book_id: int, files: list[str]) -> InlineKeyboardMarkup:
    labels = {
        '.pdf': '📕 PDF',
        '.epub': '📗 EPUB',
        '.fb2': '📘 FB2',
        '.mobi': '📙 MOBI',
        '.docx': '📄 DOCX',
        '.txt': '📄 TXT',
    }
    rows = []
    for path in files:
        suffix = '.' + path.rsplit('.', 1)[-1].lower() if '.' in path else ''
        text = labels.get(suffix, suffix.upper().lstrip('.') or 'Файл')
        rows.append([InlineKeyboardButton(text=text, callback_data=f'fmt:{book_id}:{suffix.lstrip(".")}')])
    rows.append([InlineKeyboardButton(text='⬅️ К моим покупкам', callback_data='my_books')])
    rows.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📚 Книги', callback_data='admin:list'), InlineKeyboardButton(text='➕ Добавить', callback_data='admin:add')],
        [InlineKeyboardButton(text='📊 Статистика', callback_data='admin:stats')],
        [InlineKeyboardButton(text='🧾 Продажи', callback_data='admin:sales'), InlineKeyboardButton(text='👥 Пользователи', callback_data='admin:users')],
        [InlineKeyboardButton(text='🏆 Продажи по книгам', callback_data='admin:booksales')],
        [InlineKeyboardButton(text='💳 Платежные системы', callback_data='admin:payments')],
        [InlineKeyboardButton(text='📦 Бэкап / восстановление', callback_data='admin:backup')],
        [InlineKeyboardButton(text='⚙️ Старт бота', callback_data='admin:start_settings'), InlineKeyboardButton(text='🖼 Баннеры', callback_data='admin:banners')],
        [InlineKeyboardButton(text='👩 Автор / 🆘 Поддержка', callback_data='admin:content_settings')],
        [InlineKeyboardButton(text='ℹ️ О системе', callback_data='admin:system')],
        [InlineKeyboardButton(text='🏠 В главное меню бота', callback_data='home')],
    ])


def admin_book_menu(book_id: int, is_active: int | bool = True, is_new: int | bool = False) -> InlineKeyboardMarkup:
    toggle_text = '⛔️ Выключить продажу' if is_active else '✅ Включить продажу'
    new_text = '✨ Убрать из новинок' if is_new else '✨ Сделать новинкой'
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✏️ Название', callback_data=f'admin:edit:title:{book_id}'), InlineKeyboardButton(text='📝 Описание', callback_data=f'admin:edit:description:{book_id}')],
        [InlineKeyboardButton(text='💰 Цена ₽', callback_data=f'admin:edit:price_rub:{book_id}'), InlineKeyboardButton(text='🔗 Slug', callback_data=f'admin:edit:slug:{book_id}')],
        [InlineKeyboardButton(text='🖼 Обложка', callback_data=f'admin:edit:cover_path:{book_id}'), InlineKeyboardButton(text='📎 Файлы', callback_data=f'admin:edit:file_paths:{book_id}')],
        [InlineKeyboardButton(text='🔢 Порядок', callback_data=f'admin:edit:sort_order:{book_id}'), InlineKeyboardButton(text='👁 Просмотр', callback_data=f'admin:preview:{book_id}')],
        [InlineKeyboardButton(text=new_text, callback_data=f'admin:new:{book_id}')],
        [InlineKeyboardButton(text=toggle_text, callback_data=f'admin:toggle:{book_id}')],
        [InlineKeyboardButton(text='🎁 Выдать себе', callback_data=f'admin:gift:self:{book_id}')],
        [InlineKeyboardButton(text='🗑 Удалить', callback_data=f'admin:delete:{book_id}')],
        [InlineKeyboardButton(text='⬅️ Назад', callback_data='admin:list')],
    ])


def admin_backup_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📦 Создать бэкап', callback_data='admin:backup:create')],
        [InlineKeyboardButton(text='📋 Список бэкапов', callback_data='admin:backup:list')],
        [InlineKeyboardButton(text='♻️ Восстановить из файла', callback_data='admin:backup:restore')],
        [InlineKeyboardButton(text='⬅️ Админка', callback_data='admin:home')],
    ])


def payment_methods_menu(book_id: int, providers: list[str]) -> InlineKeyboardMarkup:
    labels = {
        'stars': '⭐ Telegram Stars',
        'yookassa': '💳 ЮKassa: карта / СБП',
        'lava': '💳 Lava',
        'platega': '💳 Platega',
    }
    rows = [[InlineKeyboardButton(text=labels.get(p, p), callback_data=f'pay:{p}:{book_id}')] for p in providers]
    rows.append([InlineKeyboardButton(text='⬅️ К книге', callback_data=f'book:{book_id}')])
    rows.append([InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def external_payment_menu(payment_id: int, payment_url: str, book_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💳 Перейти к оплате', url=payment_url)],
        [InlineKeyboardButton(text='✅ Проверить оплату', callback_data=f'epay:check:{payment_id}')],
        [InlineKeyboardButton(text='📖 Вернуться к книге', callback_data=f'book:{book_id}')],
        [InlineKeyboardButton(text='🏠 Главное меню', callback_data='home')],
    ])
