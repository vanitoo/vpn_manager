from __future__ import annotations

from typing import Any

import aiosqlite

from app.db import now_iso

DEFAULT_FAQ = [
    ('Как подключить VPN?', 'Откройте раздел «Мой VPN» и нажмите кнопку «Открыть VPN-подписку». На странице подписки выберите приложение для вашего устройства и следуйте инструкции.', 10),
    ('На каких устройствах работает?', 'VPN можно использовать на Android, iPhone/iPad, Windows, macOS и Linux. Конкретный список приложений показывается на странице подписки.', 20),
    ('Как продлить доступ?', 'Откройте главное меню, нажмите «Продлить доступ», выберите тариф и оплатите его. Новые дни добавятся после текущей даты окончания.', 30),
    ('Что делать, если VPN не подключается?', 'Обновите подписку в приложении, переключите сервер и перезапустите приложение. Если не помогло, отправьте в поддержку Telegram ID, устройство и скрин ошибки.', 40),
    ('Можно ли использовать на нескольких устройствах?', 'Это зависит от настроенного лимита устройств. Текущий лимит можно уточнить у поддержки.', 50),
    ('Где взять ссылку повторно?', 'Откройте главное меню и нажмите «Подключить VPN» или «Мой VPN». Кнопка перехода к подписке будет доступна всё время, пока подписка активна.', 60),
]


async def init_faq_tables(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS faq_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 100,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        async with db.execute('SELECT COUNT(*) FROM faq_items') as cur:
            count = int((await cur.fetchone())[0] or 0)
        if count == 0:
            ts = now_iso()
            for question, answer, sort_order in DEFAULT_FAQ:
                await db.execute(
                    'INSERT INTO faq_items (question,answer,is_active,sort_order,created_at,updated_at) VALUES (?,?,1,?,?,?)',
                    (question, answer, sort_order, ts, ts),
                )
        await db.commit()


async def list_faq(db_path: str, *, active_only: bool = True) -> list[dict[str, Any]]:
    await init_faq_tables(db_path)
    query = 'SELECT * FROM faq_items'
    if active_only:
        query += ' WHERE is_active=1'
    query += ' ORDER BY sort_order,id'
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_faq(db_path: str, item_id: int) -> dict[str, Any] | None:
    await init_faq_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM faq_items WHERE id=?', (item_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def add_faq(db_path: str, question: str, answer: str) -> int:
    ts = now_iso()
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            'INSERT INTO faq_items (question,answer,is_active,sort_order,created_at,updated_at) VALUES (?,?,1,100,?,?)',
            (question, answer, ts, ts),
        )
        await db.commit()
        return int(cur.lastrowid)


async def update_faq(db_path: str, item_id: int, field: str, value: str) -> None:
    if field not in {'question', 'answer'}:
        raise ValueError('Unsupported FAQ field')
    async with aiosqlite.connect(db_path) as db:
        await db.execute(f'UPDATE faq_items SET {field}=?,updated_at=? WHERE id=?', (value, now_iso(), item_id))
        await db.commit()


async def toggle_faq(db_path: str, item_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute('UPDATE faq_items SET is_active=CASE WHEN is_active=1 THEN 0 ELSE 1 END,updated_at=? WHERE id=?', (now_iso(), item_id))
        await db.commit()


async def delete_faq(db_path: str, item_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute('DELETE FROM faq_items WHERE id=?', (item_id,))
        await db.commit()
