from __future__ import annotations

import asyncio
import os

from app.products.registry import ProductModule
from app.products.mtproto.handlers import router
from app.products.mtproto.service import reconcile_loop
from app.products.mtproto.storage import init_mtproto_tables


async def initialize(db_path: str) -> None:
    await init_mtproto_tables(db_path)
    if os.getenv('MTPROTO_ENABLED', '').lower() in {'1','true','yes','on'}:
        asyncio.create_task(reconcile_loop(db_path), name='mtproto-reconcile')


module = ProductModule(code='mtproto', routers=(router,), initialize=initialize)
