from __future__ import annotations

from dataclasses import dataclass

import aiosqlite


DEFAULT_GUEST_TEXT = '🛡 <b>VPN</b>\n\nПодключайтесь за минуту.'
DEFAULT_ACTIVE_TEXT = '🛡 <b>VPN</b>\n\n🟢 Доступ активен до <b>{expires}</b>'


@dataclass
class StartContent:
    enabled: bool = True
    image_enabled: bool = True
    guest_text: str = DEFAULT_GUEST_TEXT
    active_text: str = DEFAULT_ACTIVE_TEXT
    image_file_id: str = ''


async def init_start_content(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            '''CREATE TABLE IF NOT EXISTS start_content (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 1,
                image_enabled INTEGER NOT NULL DEFAULT 1,
                guest_text TEXT NOT NULL,
                active_text TEXT NOT NULL,
                image_file_id TEXT NOT NULL DEFAULT ''
            )'''
        )
        await db.execute(
            'INSERT OR IGNORE INTO start_content(id, guest_text, active_text) VALUES(1, ?, ?)',
            (DEFAULT_GUEST_TEXT, DEFAULT_ACTIVE_TEXT),
        )
        await db.commit()


async def get_start_content(db_path: str) -> StartContent:
    await init_start_content(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM start_content WHERE id=1') as cur:
            row = await cur.fetchone()
    if not row:
        return StartContent()
    return StartContent(
        enabled=bool(row['enabled']),
        image_enabled=bool(row['image_enabled']),
        guest_text=str(row['guest_text']),
        active_text=str(row['active_text']),
        image_file_id=str(row['image_file_id'] or ''),
    )


async def update_start_content(db_path: str, field: str, value: object) -> None:
    allowed = {'enabled', 'image_enabled', 'guest_text', 'active_text', 'image_file_id'}
    if field not in allowed:
        raise ValueError(f'Unsupported start content field: {field}')
    async with aiosqlite.connect(db_path) as db:
        await db.execute(f'UPDATE start_content SET {field}=? WHERE id=1', (value,))
        await db.commit()
