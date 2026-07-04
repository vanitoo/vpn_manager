from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiohttp

from app.config import Settings


@dataclass
class RemnawaveAccess:
    remnawave_user_id: str
    subscription_url: str
    raw: dict


class RemnawaveClient:
    """Thin Remnawave API wrapper.

    Пока это безопасная заготовка: если API URL или токен не заданы, бот выдаёт локальную
    dev-ссылку, а не падает лицом в асфальт, как любят MVP без настроек.
    Реальный endpoint можно уточнить после проверки текущей версии Remnawave API.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.remnawave_base_url and self.settings.remnawave_api_token)

    def _fallback_access(self, telegram_id: int) -> RemnawaveAccess:
        local_id = f"tg-{telegram_id}-{uuid.uuid4().hex[:8]}"
        base = self.settings.remnawave_subscription_base_url or 'https://example.com/sub'
        return RemnawaveAccess(
            remnawave_user_id=local_id,
            subscription_url=f"{base}/{local_id}",
            raw={'mode': 'local_stub'},
        )

    async def create_or_extend_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        duration_days: int,
        traffic_gb: int,
    ) -> RemnawaveAccess:
        expires_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
        if not self.is_configured:
            logging.warning('Remnawave is not configured, returning stub access for telegram_id=%s', telegram_id)
            return self._fallback_access(telegram_id)

        payload = {
            'username': f'tg_{telegram_id}',
            'telegramId': telegram_id,
            'description': username or '',
            'expireAt': expires_at.isoformat(),
            'trafficLimitGb': traffic_gb,
        }
        headers = {
            'Authorization': f'Bearer {self.settings.remnawave_api_token}',
            'Content-Type': 'application/json',
        }

        # Endpoint intentionally centralized here. If Remnawave API path differs in your install,
        # change only this method, not the whole bot.
        url = f"{self.settings.remnawave_base_url}/api/users"
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(url, json=payload, timeout=30) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    raise RuntimeError(f'Remnawave create user error {resp.status}: {data}')

        remnawave_user_id = str(data.get('uuid') or data.get('id') or data.get('userId') or f'tg-{telegram_id}')
        subscription_url = (
            data.get('subscriptionUrl')
            or data.get('subscription_url')
            or data.get('subUrl')
            or f"{self.settings.remnawave_subscription_base_url}/{remnawave_user_id}"
        )
        return RemnawaveAccess(remnawave_user_id=remnawave_user_id, subscription_url=subscription_url, raw=data)
