from __future__ import annotations

import aiosqlite
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.version import DB_SCHEMA_VERSION

CREATE_SQL = '''
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price_stars INTEGER NOT NULL DEFAULT 299,
    price_rub INTEGER NOT NULL DEFAULT 299,
    cover_path TEXT NOT NULL DEFAULT '',
    file_paths TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    is_new INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT,
    full_name TEXT,
    book_id INTEGER,
    payload TEXT NOT NULL,
    currency TEXT NOT NULL,
    total_amount INTEGER NOT NULL,
    telegram_payment_charge_id TEXT,
    provider_payment_charge_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(book_id) REFERENCES books(id)
);

CREATE INDEX IF NOT EXISTS idx_books_slug ON books(slug);
CREATE INDEX IF NOT EXISTS idx_books_active_sort ON books(is_active, sort_order, id);
CREATE INDEX IF NOT EXISTS idx_purchases_user_id ON purchases(user_id);
CREATE INDEX IF NOT EXISTS idx_purchases_book_id ON purchases(book_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_purchases_charge ON purchases(telegram_payment_charge_id);

CREATE TABLE IF NOT EXISTS external_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    provider_payment_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT,
    full_name TEXT,
    book_id INTEGER NOT NULL,
    amount_rub INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'RUB',
    status TEXT NOT NULL DEFAULT 'pending',
    payment_url TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    paid_at TEXT,
    UNIQUE(provider, provider_payment_id)
);

CREATE INDEX IF NOT EXISTS idx_external_payments_user ON external_payments(user_id);
CREATE INDEX IF NOT EXISTS idx_external_payments_book ON external_payments(book_id);
CREATE INDEX IF NOT EXISTS idx_external_payments_status ON external_payments(status);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS receipt_contacts (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    receipt_type TEXT NOT NULL DEFAULT '',
    receipt_email TEXT NOT NULL DEFAULT '',
    receipt_phone TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
'''


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def book_to_payload(book_id: int) -> str:
    return f'book:{book_id}'


def split_paths(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.replace('\n', ',').replace(';', ',').split(',') if x.strip()]


BOOK_FILE_EXTENSIONS = {'.pdf', '.epub', '.fb2', '.mobi', '.docx', '.txt'}


def book_files(book: dict[str, Any]) -> list[str]:
    """Возвращает реальные файлы книги из books/<slug>/.

    Файловая система — главный источник правды.
    Если в папке книги есть pdf/epub/fb2/mobi/docx/txt, бот увидит их
    даже если поле file_paths в старой базе пустое или устарело.
    """
    slug = (book.get('slug') or '').strip()
    if slug:
        book_dir = Path('books') / slug
        if book_dir.exists() and book_dir.is_dir():
            files = [
                p.as_posix()
                for p in sorted(book_dir.iterdir(), key=lambda x: x.name.lower())
                if p.is_file() and p.suffix.lower() in BOOK_FILE_EXTENSIONS
            ]
            if files:
                return files

    # fallback для старых карточек, если файлы лежат вне books/<slug>/
    return [p for p in split_paths(book.get('file_paths')) if Path(p).exists()]


async def _table_columns(db: aiosqlite.Connection, table: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def _table_exists(db: aiosqlite.Connection, table: str) -> bool:
    async with db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)) as cursor:
        return await cursor.fetchone() is not None


async def _safe_add_column(db: aiosqlite.Connection, table: str, column: str, definition: str) -> None:
    columns = await _table_columns(db, table)
    if column not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


async def migrate_db(db: aiosqlite.Connection) -> None:
    if await _table_exists(db, 'books'):
        for col, definition in {
            'slug': "TEXT NOT NULL DEFAULT ''",
            'title': "TEXT NOT NULL DEFAULT ''",
            'description': "TEXT NOT NULL DEFAULT ''",
            'price_stars': "INTEGER NOT NULL DEFAULT 299",
            'price_rub': "INTEGER NOT NULL DEFAULT 299",
            'cover_path': "TEXT NOT NULL DEFAULT ''",
            'file_paths': "TEXT NOT NULL DEFAULT ''",
            'is_active': "INTEGER NOT NULL DEFAULT 1",
            'is_new': "INTEGER NOT NULL DEFAULT 0",
            'sort_order': "INTEGER NOT NULL DEFAULT 100",
            'created_at': "TEXT NOT NULL DEFAULT ''",
            'updated_at': "TEXT NOT NULL DEFAULT ''",
        }.items():
            await _safe_add_column(db, 'books', col, definition)

    if await _table_exists(db, 'external_payments'):
        for col, definition in {
            'provider': "TEXT NOT NULL DEFAULT ''",
            'provider_payment_id': "TEXT NOT NULL DEFAULT ''",
            'user_id': "INTEGER NOT NULL DEFAULT 0",
            'username': 'TEXT',
            'full_name': 'TEXT',
            'book_id': "INTEGER NOT NULL DEFAULT 0",
            'amount_rub': "INTEGER NOT NULL DEFAULT 0",
            'currency': "TEXT NOT NULL DEFAULT 'RUB'",
            'status': "TEXT NOT NULL DEFAULT 'pending'",
            'payment_url': "TEXT NOT NULL DEFAULT ''",
            'payload': "TEXT NOT NULL DEFAULT ''",
            'created_at': "TEXT NOT NULL DEFAULT ''",
            'updated_at': "TEXT NOT NULL DEFAULT ''",
            'paid_at': 'TEXT',
        }.items():
            await _safe_add_column(db, 'external_payments', col, definition)

    if await _table_exists(db, 'purchases'):
        for col, definition in {
            'username': 'TEXT',
            'full_name': 'TEXT',
            'book_id': 'INTEGER',
            'payload': "TEXT NOT NULL DEFAULT ''",
            'currency': "TEXT NOT NULL DEFAULT 'XTR'",
            'total_amount': "INTEGER NOT NULL DEFAULT 0",
            'telegram_payment_charge_id': 'TEXT',
            'provider_payment_charge_id': 'TEXT',
            'created_at': "TEXT NOT NULL DEFAULT ''",
        }.items():
            await _safe_add_column(db, 'purchases', col, definition)



async def set_db_schema_version(db: aiosqlite.Connection, version: int) -> None:
    await db.execute('''
        INSERT INTO app_meta (key, value, updated_at) VALUES ('db_schema_version', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
    ''', (str(version), now_iso()))


async def get_db_schema_version(db_path: str) -> int:
    async with aiosqlite.connect(db_path) as db:
        if not await _table_exists(db, 'app_meta'):
            return 0
        async with db.execute("SELECT value FROM app_meta WHERE key = 'db_schema_version' LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if not row:
                return 0
            try:
                return int(row[0])
            except (TypeError, ValueError):
                return 0


async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute('PRAGMA foreign_keys = OFF')
        await migrate_db(db)
        await db.executescript(CREATE_SQL)
        await set_db_schema_version(db, DB_SCHEMA_VERSION)
        await db.commit()


async def seed_books(db_path: str, books: list[dict[str, Any]]) -> None:
    async with aiosqlite.connect(db_path) as db:
        for idx, book in enumerate(books, start=1):
            ts = now_iso()
            await db.execute('''
                INSERT OR IGNORE INTO books (slug, title, description, price_stars, price_rub, cover_path, file_paths,
                    is_active, is_new, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (book['slug'], book['title'], book.get('description', ''), int(book.get('price_stars', 299)),
                  int(book.get('price_rub', book.get('price_stars', 299))), book.get('cover_path', ''), book.get('file_paths', ''),
                  int(book.get('is_active', 1)), int(book.get('is_new', 0)), int(book.get('sort_order', idx * 10)), ts, ts))
            # Если книга уже была в базе, но у неё пустые поля, аккуратно дополняем демо-значениями.
            await db.execute('''
                UPDATE books
                SET cover_path = CASE WHEN COALESCE(cover_path, '') = '' THEN ? ELSE cover_path END,
                    file_paths = CASE WHEN COALESCE(file_paths, '') = '' THEN ? ELSE file_paths END,
                    updated_at = ?
                WHERE slug = ?
            ''', (book.get('cover_path', ''), book.get('file_paths', ''), ts, book['slug']))
        await db.commit()


async def list_books(db_path: str, *, active_only: bool = True) -> list[dict[str, Any]]:
    query = 'SELECT * FROM books'
    params: list[Any] = []
    if active_only:
        query += ' WHERE is_active = 1'
    query += ' ORDER BY sort_order ASC, id ASC'
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]




async def list_new_books(db_path: str, *, active_only: bool = True) -> list[dict[str, Any]]:
    query = 'SELECT * FROM books WHERE is_new = 1'
    params: list[Any] = []
    if active_only:
        query += ' AND is_active = 1'
    query += ' ORDER BY sort_order ASC, id ASC'
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]



async def clear_new_books(db_path: str) -> None:
    """Снять флаг новинки со всех книг.

    Используется перед установкой новой книги как единственной новинки.
    """
    async with aiosqlite.connect(db_path) as db:
        await db.execute('UPDATE books SET is_new = 0, updated_at = ?', (now_iso(),))
        await db.commit()

async def get_book_by_id(db_path: str, book_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM books WHERE id = ?', (book_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_book_by_slug(db_path: str, slug: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM books WHERE slug = ?', (slug,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def add_book(db_path: str, data: dict[str, Any]) -> int:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute('''
            INSERT INTO books (slug, title, description, price_stars, price_rub, cover_path, file_paths, is_active, is_new, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (data['slug'], data['title'], data.get('description', ''), int(data.get('price_stars', data.get('price_rub', 299))),
              int(data.get('price_rub', data.get('price_stars', 299))), data.get('cover_path', ''), data.get('file_paths', ''),
              int(data.get('is_active', 1)), int(data.get('is_new', 0)), int(data.get('sort_order', 100)), ts, ts))
        await db.commit()
        return int(cursor.lastrowid)


async def update_book(db_path: str, book_id: int, data: dict[str, Any]) -> None:
    allowed = {'slug', 'title', 'description', 'price_stars', 'price_rub', 'cover_path', 'file_paths', 'is_active', 'is_new', 'sort_order'}
    fields = [key for key in data if key in allowed]
    if not fields:
        return
    sql = ', '.join(f'{key} = ?' for key in fields) + ', updated_at = ?'
    values = [data[key] for key in fields] + [now_iso(), book_id]
    async with aiosqlite.connect(db_path) as db:
        await db.execute(f'UPDATE books SET {sql} WHERE id = ?', values)
        await db.commit()


async def delete_book(db_path: str, book_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute('DELETE FROM books WHERE id = ?', (book_id,))
        await db.commit()


async def add_purchase(db_path: str, *, user_id: int, username: str | None, full_name: str | None, book_id: int | None,
                       payload: str, currency: str, total_amount: int,
                       telegram_payment_charge_id: str | None, provider_payment_charge_id: str | None) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute('''
            INSERT OR REPLACE INTO purchases (user_id, username, full_name, book_id, payload, currency, total_amount,
                telegram_payment_charge_id, provider_payment_charge_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, username, full_name, book_id, payload, currency, total_amount,
              telegram_payment_charge_id, provider_payment_charge_id, now_iso()))
        await db.commit()


async def user_has_purchase(db_path: str, user_id: int, book_id: int) -> bool:
    payload = book_to_payload(book_id)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute('''
            SELECT 1 FROM purchases
            WHERE user_id = ? AND (book_id = ? OR payload = ?)
            LIMIT 1
        ''', (user_id, book_id, payload)) as cursor:
            return await cursor.fetchone() is not None


async def list_user_purchases(db_path: str, user_id: int) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT p.*, b.title, b.slug, b.description, b.cover_path, b.file_paths, b.price_stars, b.is_active
            FROM purchases p
            LEFT JOIN books b ON b.id = p.book_id
            WHERE p.user_id = ?
            ORDER BY p.created_at DESC, p.id DESC
        ''', (user_id,)) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def list_purchases(db_path: str, limit: int = 20) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT p.*, b.title AS book_title, b.slug AS book_slug
            FROM purchases p
            LEFT JOIN books b ON b.id = p.book_id
            ORDER BY p.created_at DESC, p.id DESC
            LIMIT ?
        ''', (limit,)) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def list_customers(db_path: str, limit: int = 50) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT user_id, COALESCE(username, '') AS username, COALESCE(full_name, '') AS full_name,
                   COUNT(*) AS purchases_count, SUM(total_amount) AS total_amount, MAX(created_at) AS last_purchase_at
            FROM purchases
            GROUP BY user_id
            ORDER BY last_purchase_at DESC
            LIMIT ?
        ''', (limit,)) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def sales_by_book(db_path: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT
                b.id,
                b.title,
                b.slug,
                COUNT(p.id) AS purchases_count,
                COALESCE(SUM(CASE WHEN p.currency = 'RUB' THEN p.total_amount ELSE 0 END), 0) AS rub_amount,
                COALESCE(SUM(CASE WHEN p.currency = 'XTR' THEN p.total_amount ELSE 0 END), 0) AS stars_amount,
                COALESCE(SUM(p.total_amount), 0) AS total_amount
            FROM books b
            LEFT JOIN purchases p ON p.book_id = b.id
            GROUP BY b.id
            ORDER BY purchases_count DESC, rub_amount DESC, stars_amount DESC, b.sort_order ASC, b.id ASC
        ''') as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def get_stats(db_path: str) -> dict[str, Any]:
    async with aiosqlite.connect(db_path) as db:
        async def one(sql: str) -> int:
            async with db.execute(sql) as cursor:
                row = await cursor.fetchone()
                return int(row[0] or 0)
        return {
            'books_total': await one('SELECT COUNT(*) FROM books'),
            'books_active': await one('SELECT COUNT(*) FROM books WHERE is_active = 1'),
            'purchases_total': await one('SELECT COUNT(*) FROM purchases'),
            'users_total': await one('SELECT COUNT(DISTINCT user_id) FROM purchases'),
            'stars_total': await one('SELECT COALESCE(SUM(total_amount), 0) FROM purchases'),
        }



async def add_external_payment(db_path: str, *, provider: str, provider_payment_id: str, user_id: int,
                               username: str | None, full_name: str | None, book_id: int,
                               amount_rub: int, currency: str, status: str, payment_url: str,
                               payload: str = '') -> int:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute('''
            INSERT INTO external_payments (provider, provider_payment_id, user_id, username, full_name, book_id,
                amount_rub, currency, status, payment_url, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, provider_payment_id) DO UPDATE SET
                user_id = excluded.user_id,
                username = excluded.username,
                full_name = excluded.full_name,
                book_id = excluded.book_id,
                amount_rub = excluded.amount_rub,
                currency = excluded.currency,
                status = excluded.status,
                payment_url = excluded.payment_url,
                payload = excluded.payload,
                updated_at = excluded.updated_at
        ''', (provider, provider_payment_id, user_id, username, full_name, book_id,
              amount_rub, currency, status, payment_url, payload, ts, ts))
        await db.commit()
        return int(cursor.lastrowid or 0)


async def get_external_payment(db_path: str, payment_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM external_payments WHERE id = ?', (payment_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_external_payment_by_provider_id(db_path: str, provider: str, provider_payment_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM external_payments WHERE provider = ? AND provider_payment_id = ?', (provider, provider_payment_id)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_external_payment_status(db_path: str, payment_id: int, status: str, *, paid: bool = False) -> None:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        await db.execute('''
            UPDATE external_payments
            SET status = ?, updated_at = ?, paid_at = CASE WHEN ? THEN COALESCE(paid_at, ?) ELSE paid_at END
            WHERE id = ?
        ''', (status, ts, int(paid), ts, payment_id))
        await db.commit()




async def get_receipt_contact(db_path: str, user_id: int) -> dict[str, Any] | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM receipt_contacts WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None


async def save_receipt_contact(
    db_path: str,
    *,
    user_id: int,
    username: str | None,
    full_name: str | None,
    receipt_type: str,
    receipt_email: str = '',
    receipt_phone: str = '',
) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO receipt_contacts (user_id, username, full_name, receipt_type, receipt_email, receipt_phone, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                receipt_type = excluded.receipt_type,
                receipt_email = excluded.receipt_email,
                receipt_phone = excluded.receipt_phone,
                updated_at = excluded.updated_at
            """,
            (user_id, username, full_name, receipt_type, receipt_email, receipt_phone, now_iso(), now_iso()),
        )
        await db.commit()


def receipt_customer_from_contact(contact: dict[str, Any] | None, fallback_email: str = '') -> dict[str, str]:
    if contact:
        if contact.get('receipt_type') == 'phone' and contact.get('receipt_phone'):
            return {'phone': str(contact['receipt_phone'])}
        if contact.get('receipt_email'):
            return {'email': str(contact['receipt_email'])}
    if fallback_email:
        return {'email': fallback_email}
    return {}

async def get_setting(db_path: str, key: str, default: str = '') -> str:
    async with aiosqlite.connect(db_path) as db:
        async with db.execute('SELECT value FROM app_settings WHERE key = ?', (key,)) as cursor:
            row = await cursor.fetchone()
            return str(row[0]) if row else default


async def set_setting(db_path: str, key: str, value: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute('''
            INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        ''', (key, value, now_iso()))
        await db.commit()


async def export_backup_data(db_path: str) -> dict[str, Any]:
    """Экспортирует пользователей и покупки в переносимый JSON-совместимый словарь."""
    users = await list_customers(db_path, limit=100000)
    purchases = await list_purchases(db_path, limit=100000)
    return {
        'version': '2.0',
        'created_at': now_iso(),
        'users': users,
        'purchases': purchases,
    }


async def restore_backup_data(db_path: str, data: dict[str, Any]) -> dict[str, int]:
    """Восстанавливает покупки из JSON-бэкапа. Книги сопоставляются по book_slug."""
    inserted = 0
    skipped = 0
    purchases = data.get('purchases') or []
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        for p in purchases:
            user_id = int(p.get('user_id') or p.get('telegram_id') or 0)
            if not user_id:
                skipped += 1
                continue
            book_id = p.get('book_id')
            book_slug = p.get('book_slug') or p.get('slug')
            if book_slug:
                async with db.execute('SELECT id FROM books WHERE slug = ? LIMIT 1', (book_slug,)) as cur:
                    row = await cur.fetchone()
                    if row:
                        book_id = int(row['id'])
            book_id = int(book_id) if book_id else None
            created_at = p.get('created_at') or now_iso()
            total_amount = int(p.get('total_amount') or p.get('amount') or 0)
            currency = p.get('currency') or 'XTR'
            payload = p.get('payload') or (book_to_payload(book_id) if book_id else '')
            telegram_charge = p.get('telegram_payment_charge_id')
            provider_charge = p.get('provider_payment_charge_id')
            if telegram_charge:
                async with db.execute('SELECT 1 FROM purchases WHERE telegram_payment_charge_id = ? LIMIT 1', (telegram_charge,)) as cur:
                    if await cur.fetchone():
                        skipped += 1
                        continue
            else:
                async with db.execute("""
                    SELECT 1 FROM purchases
                    WHERE user_id = ? AND COALESCE(book_id, 0) = COALESCE(?, 0)
                      AND total_amount = ? AND created_at = ?
                    LIMIT 1
                """, (user_id, book_id, total_amount, created_at)) as cur:
                    if await cur.fetchone():
                        skipped += 1
                        continue
            await db.execute("""
                INSERT INTO purchases (user_id, username, full_name, book_id, payload, currency, total_amount,
                    telegram_payment_charge_id, provider_payment_charge_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, p.get('username'), p.get('full_name'), book_id, payload, currency, total_amount,
                  telegram_charge, provider_charge, created_at))
            inserted += 1
        await db.commit()
    return {'inserted': inserted, 'skipped': skipped}


async def get_active_external_payment_for_book(
    db_path: str,
    *,
    user_id: int,
    book_id: int,
    ttl_minutes: int = 60,
) -> dict[str, Any] | None:
    """Найти свежий ожидающий внешний платеж пользователя по книге."""
    cutoff = datetime.now(timezone.utc).timestamp() - max(ttl_minutes, 1) * 60
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM external_payments
            WHERE user_id = ?
              AND book_id = ?
              AND status IN ('pending', 'waiting_for_capture')
            ORDER BY id DESC
            LIMIT 10
            """,
            (user_id, book_id),
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            item = dict(row)
            created_at = item.get('created_at') or ''
            try:
                dt = datetime.fromisoformat(created_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt.timestamp() >= cutoff:
                    return item
            except Exception:
                return item
    return None


async def expire_old_external_payments(db_path: str, *, ttl_minutes: int = 60) -> int:
    cutoff_dt = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() - max(ttl_minutes, 1) * 60,
        tz=timezone.utc,
    ).isoformat()
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            UPDATE external_payments
            SET status = 'expired', updated_at = ?
            WHERE status IN ('pending', 'waiting_for_capture')
              AND created_at < ?
            """,
            (now_iso(), cutoff_dt),
        )
        await db.commit()
        return int(cursor.rowcount or 0)
