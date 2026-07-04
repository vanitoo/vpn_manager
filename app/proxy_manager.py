#!/usr/bin/env python
"""
Proxy Manager v1.2.0

Универсальный менеджер прокси для aiogram/aiohttp.

Режимы:
- off      — прокси отключены, соединение напрямую.
- single   — используется только первый прокси из списка, без переключения.
- sticky   — выбирается один живой прокси и держится до ошибки. После ошибки переключается на следующий живой.
- failover — приоритетный режим: сначала используется proxy #0. Если он упал — переход на proxy #1/#2/... .
             Health-check периодически проверяет упавшие прокси и, когда primary оживает, возвращает его в работу.
- random   — при каждом выборе берется случайный живой прокси.
- rotate   — при каждом выборе берется следующий живой прокси.

Формат списка прокси:
PROXY=http://user:pass@host:3128,http://user:pass@host:3129;socks5://user:pass@host:1080

Также поддерживаются разделители:
- запятая
- точка с запятой
- перенос строки

Поддерживаемые схемы:
- http://
- https://
- socks5://

Для SOCKS5 нужен пакет:
pip install aiohttp-socks

Health-check:
- check_proxy(proxy) проверяет конкретный прокси.
- start_healthcheck_loop() запускает фоновую проверку упавших прокси.
- Для failover режим отличается от sticky именно возвратом на primary после восстановления.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from enum import Enum
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

__version__ = "1.2.0"


class ProxyMode(str, Enum):
    OFF = "off"
    SINGLE = "single"
    FAILOVER = "failover"
    STICKY = "sticky"
    RANDOM = "random"
    ROTATE = "rotate"

    @classmethod
    def normalize(cls, value: str | None) -> "ProxyMode":
        raw = (value or cls.OFF.value).strip().lower()
        aliases = {
            "": cls.OFF,
            "none": cls.OFF,
            "no": cls.OFF,
            "disabled": cls.OFF,
            "disable": cls.OFF,
            "on": cls.FAILOVER,
            "true": cls.FAILOVER,
            "yes": cls.FAILOVER,
            "1": cls.FAILOVER,
            "fallback": cls.FAILOVER,
            "priority": cls.FAILOVER,
        }
        if raw in aliases:
            return aliases[raw]
        try:
            return cls(raw)
        except ValueError:
            log.warning("Неизвестный PROXY_MODE=%r, используется failover", value)
            return cls.FAILOVER


def mask_proxy_url(proxy_url: str) -> str:
    """Маскирует пароль в URL прокси для безопасного логирования."""
    if "://" not in proxy_url:
        return proxy_url
    protocol, rest = proxy_url.split("://", 1)
    if "@" not in rest:
        return proxy_url
    auth, host_port = rest.split("@", 1)
    if ":" in auth:
        user = auth.split(":", 1)[0]
        return f"{protocol}://{user}:***@{host_port}"
    return proxy_url


class ProxyManager:
    """Управление списком прокси с режимами single/sticky/failover/random/rotate."""

    def __init__(
        self,
        proxies: list[str],
        mode: str | ProxyMode = ProxyMode.OFF,
        *,
        healthcheck_url: str = "https://api.telegram.org",
        healthcheck_timeout: float = 8.0,
        healthcheck_interval: float = 60.0,
    ):
        self.proxies = [p.strip() for p in proxies if p and p.strip()]
        self.mode = ProxyMode.normalize(str(mode)) if not isinstance(mode, ProxyMode) else mode
        self.current_index = 0
        self.failed_proxies: set[int] = set()
        self.max_failures = 3
        self.failure_counts: dict[int, int] = {}
        self._pending_proxy: Optional[str] = None
        self.healthcheck_url = healthcheck_url
        self.healthcheck_timeout = float(healthcheck_timeout)
        self.healthcheck_interval = float(healthcheck_interval)
        self._healthcheck_task: Optional[asyncio.Task] = None

        if not self.proxies:
            self.mode = ProxyMode.OFF

        if self.mode == ProxyMode.OFF:
            log.info("ProxyManager v%s: прокси отключены", __version__)
        else:
            log.info(
                "ProxyManager v%s: mode=%s, proxies=%d, healthcheck=%s",
                __version__, self.mode.value, len(self.proxies), self.healthcheck_url,
            )

    @classmethod
    def from_env_string(
        cls,
        proxy_string: str | None,
        mode: str | ProxyMode = ProxyMode.FAILOVER,
        *,
        healthcheck_url: str = "https://api.telegram.org",
        healthcheck_timeout: float = 8.0,
        healthcheck_interval: float = 60.0,
    ) -> "ProxyManager":
        proxy_string = proxy_string or ""
        proxies = [p.strip() for p in re.split(r"[,;\n\r]+", proxy_string) if p.strip()]
        return cls(
            proxies,
            mode=mode,
            healthcheck_url=healthcheck_url,
            healthcheck_timeout=healthcheck_timeout,
            healthcheck_interval=healthcheck_interval,
        )

    @property
    def has_proxies(self) -> bool:
        return bool(self.proxies) and self.mode != ProxyMode.OFF

    @property
    def current_proxy(self) -> Optional[str]:
        if not self.has_proxies:
            return None
        return self.proxies[self.current_index]

    def _active_indexes(self) -> list[int]:
        if not self.has_proxies:
            return []
        if self.mode == ProxyMode.SINGLE:
            return [0] if 0 not in self.failed_proxies else []
        return [i for i in range(len(self.proxies)) if i not in self.failed_proxies]

    def _best_failover_index(self) -> Optional[int]:
        """Для failover выбираем самый приоритетный живой индекс: 0, потом 1, потом 2..."""
        active = self._active_indexes()
        return min(active) if active else None

    def get_proxy(self) -> Optional[str]:
        """Вернуть прокси согласно текущему режиму."""
        active = self._active_indexes()
        if not active:
            if self.has_proxies:
                log.error("Нет доступных прокси: все помечены как нерабочие")
            return None

        if self.mode == ProxyMode.SINGLE:
            self.current_index = 0
        elif self.mode == ProxyMode.FAILOVER:
            best = self._best_failover_index()
            if best is None:
                return None
            if best != self.current_index:
                log.info(
                    "Failover выбирает приоритетный прокси #%d: %s",
                    best, mask_proxy_url(self.proxies[best]),
                )
            self.current_index = best
        elif self.mode == ProxyMode.RANDOM:
            self.current_index = random.choice(active)
        elif self.mode == ProxyMode.ROTATE:
            self.current_index = self._next_active_index(step_from_current=True) or active[0]
        else:
            # sticky: продолжаем использовать текущий, если он жив.
            if self.current_index not in active:
                self.current_index = active[0]

        return self.proxies[self.current_index]

    def _next_active_index(self, step_from_current: bool = False) -> Optional[int]:
        if not self.has_proxies:
            return None
        index = (self.current_index + 1) % len(self.proxies) if step_from_current else self.current_index
        for _ in range(len(self.proxies)):
            if index not in self.failed_proxies:
                return index
            index = (index + 1) % len(self.proxies)
        return None

    def next_proxy(self) -> Optional[str]:
        """Принудительно перейти к следующему живому прокси."""
        idx = self._next_active_index(step_from_current=True)
        if idx is None:
            return None
        self.current_index = idx
        return self.proxies[idx]

    def mark_proxy_failed(self, proxy: Optional[str] = None) -> None:
        """Пометить прокси как нерабочий и при необходимости переключиться."""
        if not self.has_proxies:
            return

        proxy = proxy or self.current_proxy
        if not proxy or proxy not in self.proxies:
            return

        index = self.proxies.index(proxy)
        self.failure_counts[index] = self.failure_counts.get(index, 0) + 1

        if self.mode == ProxyMode.SINGLE:
            log.warning(
                "Прокси %s дал ошибку в режиме single (ошибок: %d/%d), переключение отключено",
                mask_proxy_url(proxy), self.failure_counts[index], self.max_failures,
            )
            if self.failure_counts[index] >= self.max_failures:
                self.failed_proxies.add(index)
            return

        self.failed_proxies.add(index)
        log.warning(
            "Прокси %s помечен как нерабочий (ошибок: %d/%d)",
            mask_proxy_url(proxy), self.failure_counts[index], self.max_failures,
        )

        if self.current_proxy == proxy:
            if self.mode == ProxyMode.FAILOVER:
                idx = self._best_failover_index()
                if idx is not None:
                    self.current_index = idx
                    log.info("Failover переключился на прокси #%d: %s", idx, mask_proxy_url(self.proxies[idx]))
                else:
                    log.error("Нет доступных прокси для failover")
            else:
                new_proxy = self.next_proxy()
                if new_proxy:
                    log.info("Переключение на прокси: %s", mask_proxy_url(new_proxy))
                else:
                    log.error("Нет доступных прокси для переключения")

    def reset_proxy(self, proxy: Optional[str] = None) -> None:
        """Сбросить метки ошибок."""
        if proxy is None:
            self.failed_proxies.clear()
            self.failure_counts.clear()
            if self.mode == ProxyMode.FAILOVER:
                self.current_index = 0
            log.info("Все прокси сброшены в активное состояние")
            return

        if proxy in self.proxies:
            index = self.proxies.index(proxy)
            self.failed_proxies.discard(index)
            self.failure_counts[index] = 0
            log.info("Прокси %s сброшен", mask_proxy_url(proxy))
            if self.mode == ProxyMode.FAILOVER and index < self.current_index:
                self.current_index = index
                log.info("Failover вернулся на более приоритетный прокси #%d: %s", index, mask_proxy_url(proxy))

    async def check_proxy(self, proxy: str) -> bool:
        """Проверить, что через прокси открывается healthcheck_url."""
        protocol = proxy.split("://", 1)[0].lower() if "://" in proxy else ""
        timeout = aiohttp.ClientTimeout(total=self.healthcheck_timeout)

        try:
            if protocol == "socks5":
                try:
                    import aiohttp_socks
                except ImportError:
                    log.error("aiohttp-socks не установлен: pip install aiohttp-socks")
                    return False
                connector = aiohttp_socks.ProxyConnector.from_url(proxy)
                async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                    async with session.get(self.healthcheck_url) as response:
                        return response.status < 500

            if protocol in {"http", "https"}:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(self.healthcheck_url, proxy=proxy) as response:
                        return response.status < 500

            log.warning("Неизвестный протокол прокси для health-check: %s", protocol or "без схемы")
            return False
        except Exception as exc:
            log.warning("Health-check failed for %s: %s", mask_proxy_url(proxy), exc)
            return False

    async def check_all(self) -> dict[int, bool]:
        """Проверить все прокси и обновить их состояние."""
        if not self.has_proxies:
            return {}

        results: dict[int, bool] = {}
        for index, proxy in enumerate(self.proxies):
            ok = await self.check_proxy(proxy)
            results[index] = ok
            if ok:
                if index in self.failed_proxies or self.failure_counts.get(index, 0):
                    self.reset_proxy(proxy)
            else:
                self.failure_counts[index] = self.failure_counts.get(index, 0) + 1
                self.failed_proxies.add(index)

        if self.mode == ProxyMode.FAILOVER:
            best = self._best_failover_index()
            if best is not None and best != self.current_index:
                self.current_index = best
                log.info("Failover выбрал лучший живой прокси #%d: %s", best, mask_proxy_url(self.proxies[best]))
        return results

    async def start_healthcheck_loop(self) -> None:
        """Фоновый health-check. Особенно важен для failover, чтобы возвращаться на primary."""
        if not self.has_proxies or self.healthcheck_interval <= 0:
            return
        if self._healthcheck_task and not self._healthcheck_task.done():
            return

        async def _loop() -> None:
            while True:
                try:
                    await asyncio.sleep(self.healthcheck_interval)
                    await self.check_all()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Ошибка в proxy health-check loop")

        self._healthcheck_task = asyncio.create_task(_loop(), name="proxy-healthcheck")
        log.info("Proxy health-check loop started: interval=%ss, url=%s", self.healthcheck_interval, self.healthcheck_url)

    async def stop_healthcheck_loop(self) -> None:
        if self._healthcheck_task and not self._healthcheck_task.done():
            self._healthcheck_task.cancel()
            try:
                await self._healthcheck_task
            except asyncio.CancelledError:
                pass

    def get_session(self) -> Optional["AiohttpSession"]:
        """Создать сессию Aiogram с прокси согласно режиму."""
        from aiogram.client.session.aiohttp import AiohttpSession

        proxy = self.get_proxy()
        if not proxy:
            return None

        protocol = proxy.split("://", 1)[0].lower() if "://" in proxy else ""

        try:
            if protocol == "socks5":
                try:
                    import aiohttp_socks
                except ImportError:
                    log.error("aiohttp-socks не установлен: pip install aiohttp-socks")
                    self.mark_proxy_failed(proxy)
                    return None

                loop = asyncio.get_event_loop()
                if loop.is_running():
                    connector = aiohttp_socks.ProxyConnector.from_url(proxy)
                    session = AiohttpSession(connector=connector)
                    log.info("Создана сессия с SOCKS5 прокси: %s", mask_proxy_url(proxy))
                    return session

                self._pending_proxy = proxy
                log.info("Event loop не запущен, SOCKS5 сессия будет создана через get_session_sync")
                return None

            if protocol in {"http", "https"}:
                session = AiohttpSession(proxy=proxy)
                log.info("Создана сессия с HTTP/HTTPS прокси: %s", mask_proxy_url(proxy))
                return session

            log.warning("Неизвестный протокол прокси: %s", protocol or "без схемы")
            self.mark_proxy_failed(proxy)
            return None

        except Exception as exc:
            log.error("Ошибка создания сессии с прокси %s: %s", mask_proxy_url(proxy), exc)
            self.mark_proxy_failed(proxy)
            return None

    def get_session_sync(self) -> Optional["AiohttpSession"]:
        """Создать сессию после старта event loop, если SOCKS5 был отложен."""
        from aiogram.client.session.aiohttp import AiohttpSession

        proxy = self._pending_proxy or self.get_proxy()
        if not proxy:
            return None

        protocol = proxy.split("://", 1)[0].lower() if "://" in proxy else ""
        if protocol != "socks5":
            return self.get_session()

        try:
            import aiohttp_socks
            connector = aiohttp_socks.ProxyConnector.from_url(proxy)
            session = AiohttpSession(connector=connector)
            log.info("Создана сессия с SOCKS5 прокси: %s", mask_proxy_url(proxy))
            return session
        except Exception as exc:
            log.error("Ошибка создания SOCKS5 сессии %s: %s", mask_proxy_url(proxy), exc)
            self.mark_proxy_failed(proxy)
            return None

    def get_status(self) -> dict:
        return {
            "version": __version__,
            "mode": self.mode.value,
            "healthcheck_url": self.healthcheck_url,
            "healthcheck_interval": self.healthcheck_interval,
            "total": len(self.proxies),
            "active": len(self._active_indexes()),
            "current_index": self.current_index if self.proxies else None,
            "current_proxy": mask_proxy_url(self.current_proxy) if self.current_proxy else None,
            "proxies": [
                {
                    "index": i,
                    "url": mask_proxy_url(p),
                    "active": i not in self.failed_proxies,
                    "failures": self.failure_counts.get(i, 0),
                    "primary": i == 0,
                }
                for i, p in enumerate(self.proxies)
            ],
        }

    def __repr__(self) -> str:
        return (
            f"ProxyManager(version={__version__}, mode={self.mode.value}, "
            f"proxies={len(self.proxies)}, current={self.current_index}, failed={len(self.failed_proxies)})"
        )
