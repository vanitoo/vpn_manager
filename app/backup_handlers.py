from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from app import runtime

router = Router()
log = logging.getLogger(__name__)


def _create_backup(db_path: str, log_file: str, backup_dir: str) -> Path:
    root = Path(backup_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    work = root / f'vpn_bot_{stamp}'
    work.mkdir(parents=True, exist_ok=True)
    db_copy = work / 'vpn_bot.sqlite3'
    source = sqlite3.connect(db_path)
    target = sqlite3.connect(db_copy)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    log_path = Path(log_file)
    if log_path.exists():
        shutil.copy2(log_path, work / log_path.name)
    zip_path = root / f'vpn_bot_backup_{stamp}.zip'
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for item in work.iterdir():
            archive.write(item, arcname=item.name)
    shutil.rmtree(work, ignore_errors=True)
    return zip_path


async def create_backup() -> Path:
    return await asyncio.to_thread(
        _create_backup,
        runtime.settings.db_path,
        runtime.settings.log_file,
        runtime.settings.backup_dir,
    )


@router.message(Command('backup'))
async def backup_command(message: Message) -> None:
    if not runtime.admin(message):
        return
    if not runtime.settings.backups_enabled:
        await message.answer('Бэкапы выключены: BACKUPS_ENABLED=false')
        return
    status = await message.answer('📦 Создаю бэкап SQLite и логов...')
    try:
        path = await create_backup()
        await message.answer_document(
            FSInputFile(path),
            caption=f'📦 Бэкап создан\n<code>{path.name}</code>',
        )
        await status.delete()
    except Exception as exc:
        log.exception('Backup failed')
        await status.edit_text(f'Ошибка создания бэкапа: <code>{str(exc)[:1000]}</code>')
