from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, CallbackQuery, TelegramObject

from app import runtime
from app.admin_plan_handlers import router as admin_plan_router
from app.admin_remna_handlers import router as admin_remna_router
from app.config import get_settings
from app.proxy_manager import ProxyManager
from app.user_vpn_handlers import router as user_vpn_router

log = logging.getLogger(__name__)
proxy_manager: ProxyManager | None = None


class DeleteOldMenuMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        if isinstance(event, CallbackQuery) and event.message:
            try:
                await event.message.delete()
            except Exception as exc:
                logging.getLogger(__name__).debug('Cannot delete previous menu: %s', exc)
        return await handler(event, data)


def setup_logging() -> None:
    Path(runtime.settings.log_file).parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, runtime.settings.log_level, logging.INFO))
    root.handlers.clear()
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    file_handler = RotatingFileHandler(runtime.settings.log_file, maxBytes=runtime.settings.log_max_bytes, backupCount=runtime.settings.log_backup_count, encoding='utf-8')
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    logging.getLogger('aiogram').setLevel(logging.INFO)
    logging.getLogger('aiohttp').setLevel(logging.INFO)


async def setup_commands(bot: Bot) -> None:
    public = [BotCommand(command='start', description='Главное меню')]
    await bot.set_my_commands(public, scope=BotCommandScopeDefault())
    for admin_id in runtime.settings.admin_ids:
        await bot.set_my_commands(public + [BotCommand(command='admin', description='Админка')], scope=BotCommandScopeChat(chat_id=admin_id))
    log.info('Minimal Telegram commands registered. Admin IDs: %s', runtime.settings.admin_ids)


async def make_bot() -> Bot:
    global proxy_manager
    proxy_manager = ProxyManager.from_env_string(runtime.settings.proxy, mode=runtime.settings.proxy_mode, healthcheck_url=runtime.settings.proxy_healthcheck_url, healthcheck_timeout=runtime.settings.proxy_healthcheck_timeout, healthcheck_interval=runtime.settings.proxy_healthcheck_interval)
    log.info('Proxy mode=%s has_proxies=%s', runtime.settings.proxy_mode, proxy_manager.has_proxies)
    if proxy_manager.has_proxies:
        await proxy_manager.check_all()
    session = proxy_manager.get_session() or proxy_manager.get_session_sync()
    if session:
        await proxy_manager.start_healthcheck_loop()
        log.info('Telegram API session: proxy')
        return Bot(runtime.settings.bot_token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    log.info('Telegram API session: direct')
    return Bot(runtime.settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def close_proxy_manager() -> None:
    if not proxy_manager:
        return
    close = getattr(proxy_manager, 'close', None)
    if callable(close):
        result = close()
        if hasattr(result, '__await__'):
            await result


async def main() -> None:
    runtime.settings = get_settings()
    setup_logging()
    log.info('Starting VPN bot')
    log.info('DB=%s LOG=%s', runtime.settings.db_path, runtime.settings.log_file)
    log.info('Remnawave base=%s token_set=%s squad_set=%s', runtime.settings.remnawave_base_url, bool(runtime.settings.remnawave_api_token), bool(runtime.settings.remnawave_internal_squad_uuid))

    await runtime.init_db(runtime.settings.db_path)
    await runtime.init_admin_tables(runtime.settings.db_path)
    await runtime.seed_plans(runtime.settings.db_path, runtime.DEFAULT_PLANS)

    bot = await make_bot()
    await setup_commands(bot)

    dp = Dispatcher(storage=MemoryStorage())
    dp.callback_query.outer_middleware(DeleteOldMenuMiddleware())
    dp.include_router(user_vpn_router)
    dp.include_router(admin_plan_router)
    dp.include_router(admin_remna_router)
    dp.include_router(runtime.router)

    try:
        if runtime.settings.delete_webhook_on_start:
            log.info('Deleting webhook. drop_pending=%s', runtime.settings.drop_pending_updates)
            await bot.delete_webhook(drop_pending_updates=runtime.settings.drop_pending_updates)
        me = await bot.get_me()
        log.info('Bot started: @%s id=%s', me.username, me.id)
        await dp.start_polling(bot)
    finally:
        log.info('Stopping VPN bot')
        await close_proxy_manager()
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
