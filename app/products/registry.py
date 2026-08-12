from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from aiogram import Router


Initializer = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class ProductModule:
    """A self-contained optional product plugged into application startup."""

    code: str
    routers: tuple[Router, ...] = ()
    initialize: Initializer | None = None


def enabled_product_modules(*, socks5_enabled: bool) -> tuple[ProductModule, ...]:
    """Load enabled modules lazily so disabled products cannot break VPN startup."""

    modules: list[ProductModule] = []
    if socks5_enabled:
        from app.products.socks5 import module

        modules.append(module)
    return tuple(modules)

