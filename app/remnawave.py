from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import aiohttp

from app.config import Settings


@dataclass
class RemnawaveAccess:
    remnawave_user_id: str
    subscription_url: str
    raw: dict[str, Any]


class RemnawaveClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(
            self.settings.remnawave_base_url
            and self.settings.remnawave_api_token
            and getattr(self.settings, 'remnawave_internal_squad_uuid', '')
        )

    def _headers(self) -> dict[str, str]:
        return {
            'Authorization': f'Bearer {self.settings.remnawave_api_token}',
            'Content-Type': 'application/json',
        }

    def _fallback_access(self, telegram_id: int) -> RemnawaveAccess:
        local_id = f"tg-{telegram_id}-{uuid.uuid4().hex[:8]}"
        base = self.settings.remnawave_subscription_base_url or 'https://example.com/sub'
        return RemnawaveAccess(
            remnawave_user_id=local_id,
            subscription_url=f"{base}/{local_id}",
            raw={'mode': 'local_stub'},
        )

    @staticmethod
    def _to_iso(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

    @staticmethod
    def _safe_username(value: str | None, telegram_id: int) -> str:
        base = (value or f'tg_{telegram_id}').strip().lower()
        base = re.sub(r'[^a-z0-9_-]', '_', base).strip('_-')
        if not base or not re.match(r'^[a-z0-9]', base):
            base = f'tg_{telegram_id}'
        return base[:32]

    @staticmethod
    def _email_for_user(telegram_id: int) -> str:
        return f'tg{telegram_id}@bot.local'

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
        expected_status: tuple[int, ...] = (200,),
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self.settings.remnawave_base_url}{path}"
        async with aiohttp.ClientSession(headers=self._headers()) as session:
            async with session.request(method, url, json=json_payload, timeout=30) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {'raw': await resp.text()}
                if resp.status not in expected_status:
                    raise RuntimeError(f'Remnawave API {method} {path} error {resp.status}: {data}')
                return resp.status, data if isinstance(data, dict) else {'response': data}

    @staticmethod
    def _unwrap(data: dict[str, Any]) -> dict[str, Any]:
        response = data.get('response')
        return response if isinstance(response, dict) else data

    async def _get_user_by_email(self, email: str) -> dict[str, Any] | None:
        status, data = await self._request('GET', f'/api/users/by-email/{quote(email)}', expected_status=(200, 404))
        if status == 404:
            return None
        user = self._unwrap(data)
        return user if isinstance(user, dict) else None

    def _subscription_url(self, user: dict[str, Any]) -> str:
        direct = user.get('subscriptionUrl') or user.get('subscription_url') or user.get('subUrl')
        if direct:
            return str(direct)
        short_uuid = user.get('shortUuid') or user.get('short_uuid')
        if short_uuid and self.settings.remnawave_subscription_base_url:
            return f"{self.settings.remnawave_subscription_base_url}/{short_uuid}"
        user_uuid = user.get('uuid') or user.get('id')
        if user_uuid and self.settings.remnawave_subscription_base_url:
            return f"{self.settings.remnawave_subscription_base_url}/{user_uuid}"
        return ''

    async def create_or_extend_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        duration_days: int,
        traffic_gb: int,
    ) -> RemnawaveAccess:
        if not self.is_configured:
            logging.warning('Remnawave is not fully configured, returning stub access for telegram_id=%s', telegram_id)
            return self._fallback_access(telegram_id)

        email = self._email_for_user(telegram_id)
        current = await self._get_user_by_email(email)
        requested_expire = datetime.now(timezone.utc) + timedelta(days=duration_days)
        expire_at = requested_expire

        if current and current.get('expireAt'):
            try:
                current_expire = datetime.fromisoformat(str(current['expireAt']).replace('Z', '+00:00'))
                if current_expire > datetime.now(timezone.utc):
                    expire_at = current_expire + timedelta(days=duration_days)
            except ValueError:
                pass

        traffic_limit_bytes = int(traffic_gb) * 1024 * 1024 * 1024 if int(traffic_gb or 0) > 0 else None
        payload: dict[str, Any] = {
            'status': 'ACTIVE',
            'expireAt': self._to_iso(expire_at),
            'activeInternalSquads': [self.settings.remnawave_internal_squad_uuid],
            'email': email,
            'telegramId': int(telegram_id),
            'description': f'Telegram VPN bot user {telegram_id}',
            'trafficLimitStrategy': 'NO_RESET',
        }
        if traffic_limit_bytes is not None:
            payload['trafficLimitBytes'] = traffic_limit_bytes
        if getattr(self.settings, 'remnawave_hwid_device_limit', 0) > 0:
            payload['hwidDeviceLimit'] = self.settings.remnawave_hwid_device_limit
        if getattr(self.settings, 'remnawave_external_squad_uuid', ''):
            payload['externalSquadUuid'] = self.settings.remnawave_external_squad_uuid

        method = 'PATCH' if current else 'POST'
        if current:
            payload['uuid'] = current.get('uuid')
            if not payload['uuid']:
                raise RuntimeError('Remnawave existing user has no uuid')
        else:
            payload['username'] = self._safe_username(username, telegram_id)

        _, data = await self._request(method, '/api/users', json_payload=payload, expected_status=(200, 201))
        user = self._unwrap(data)
        remnawave_user_id = str(user.get('uuid') or user.get('id') or '')
        subscription_url = self._subscription_url(user)
        return RemnawaveAccess(remnawave_user_id=remnawave_user_id, subscription_url=subscription_url, raw=user)
