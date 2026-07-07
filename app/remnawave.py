from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import aiohttp
from aiohttp import BasicAuth

from app.config import Settings

log = logging.getLogger(__name__)


@dataclass
class RemnawaveAccess:
    remnawave_user_id: str
    subscription_url: str
    raw: dict[str, Any]


class RemnawaveClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_error: str = ''

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.remnawave_base_url and self.settings.remnawave_api_token)

    def _headers(self) -> dict[str, str]:
        headers = {'Authorization': f'Bearer {self.settings.remnawave_api_token}', 'Content-Type': 'application/json'}
        if self.settings.remnawave_nginx_auth_enabled and self.settings.remnawave_nginx_cookie_name and self.settings.remnawave_nginx_cookie_value:
            headers['Cookie'] = f'{self.settings.remnawave_nginx_cookie_name}={self.settings.remnawave_nginx_cookie_value}'
        return headers

    def _basic_auth(self) -> BasicAuth | None:
        if self.settings.remnawave_nginx_auth_enabled and self.settings.remnawave_nginx_basic_login and self.settings.remnawave_nginx_basic_password:
            return BasicAuth(self.settings.remnawave_nginx_basic_login, self.settings.remnawave_nginx_basic_password)
        return None

    def _fallback_access(self, telegram_id: int) -> RemnawaveAccess:
        local_id = f"tg-{telegram_id}-{uuid.uuid4().hex[:8]}"
        base = self.settings.remnawave_subscription_base_url or 'https://example.com/sub'
        log.warning('Remnawave stub access issued for telegram_id=%s', telegram_id)
        return RemnawaveAccess(local_id, f"{base}/{local_id}", {'mode': 'local_stub'})

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

    async def _request(self, method: str, path: str, *, json_payload: dict[str, Any] | None = None, expected_status: tuple[int, ...] = (200,)) -> tuple[int, dict[str, Any]]:
        if not self.settings.remnawave_base_url:
            raise RuntimeError('REMNAWAVE_BASE_URL is empty')
        url = f"{self.settings.remnawave_base_url}{path}"
        safe_payload = json_payload.copy() if isinstance(json_payload, dict) else None
        log.info('Remnawave request: %s %s nginx_auth=%s payload=%s', method, path, self.settings.remnawave_nginx_auth_enabled, safe_payload)
        async with aiohttp.ClientSession(headers=self._headers(), auth=self._basic_auth()) as session:
            async with session.request(method, url, json=json_payload, timeout=30) as resp:
                text = await resp.text()
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {'raw': text[:1000]}
                log.info('Remnawave response: %s %s -> HTTP %s body=%s', method, path, resp.status, str(data)[:1200])
                if resp.status not in expected_status:
                    raise RuntimeError(f'Remnawave API {method} {path} HTTP {resp.status}: {data}')
                return resp.status, data if isinstance(data, dict) else {'response': data}

    @staticmethod
    def _unwrap(data: dict[str, Any]) -> Any:
        return data.get('response') if isinstance(data, dict) and 'response' in data else data

    async def diagnostics(self) -> str:
        if not self.settings.remnawave_base_url:
            return 'REMNAWAVE_BASE_URL пустой'
        if not self.settings.remnawave_api_token:
            return 'REMNAWAVE_API_TOKEN пустой'
        checks = []
        checks.append(f"nginx_auth={'on' if self.settings.remnawave_nginx_auth_enabled else 'off'} cookie={self.settings.remnawave_nginx_cookie_name or '-'} basic={'on' if self._basic_auth() else 'off'}")
        for path in ['/api/auth/session', '/api/internal-squads', '/api/users?page=0&size=1', '/api/nodes']:
            try:
                await self._request('GET', path, expected_status=(200, 404))
                checks.append(f'✅ {path}')
            except Exception as exc:
                checks.append(f'❌ {path}: {type(exc).__name__}: {exc}')
        squad = await self.resolve_internal_squad_uuid()
        checks.append(f"Squad: {squad or 'не найден'}")
        return '\n'.join(checks)

    async def list_internal_squads(self) -> list[dict[str, Any]]:
        candidates = ['/api/internal-squads', '/api/internal-squads/all', '/api/squads', '/api/squads/internal']
        for path in candidates:
            try:
                _, data = await self._request('GET', path, expected_status=(200, 404))
                payload = self._unwrap(data)
                if isinstance(payload, dict):
                    items = payload.get('internalSquads') or payload.get('squads') or payload.get('data') or payload.get('items') or []
                else:
                    items = payload
                if isinstance(items, list) and items:
                    log.info('Remnawave squads discovered via %s: %s', path, items)
                    return [x for x in items if isinstance(x, dict)]
            except Exception as exc:
                log.warning('Squad discovery failed for %s: %s', path, exc)
        return []

    async def resolve_internal_squad_uuid(self) -> str:
        manual = getattr(self.settings, 'remnawave_internal_squad_uuid', '')
        if manual:
            return manual
        squads = await self.list_internal_squads()
        for item in squads:
            value = item.get('uuid') or item.get('id') or item.get('squadUuid')
            if value:
                log.info('Auto-selected Remnawave internal squad: %s (%s)', value, item.get('name') or item.get('title') or '')
                return str(value)
        return ''

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

    async def create_or_extend_user(self, *, telegram_id: int, username: str | None, duration_days: int, traffic_gb: int) -> RemnawaveAccess:
        if not self.is_configured:
            self.last_error = 'Remnawave base URL or token is not configured'
            log.warning(self.last_error)
            return self._fallback_access(telegram_id)
        squad_uuid = await self.resolve_internal_squad_uuid()
        if not squad_uuid:
            self.last_error = 'Internal squad UUID not found. Fill REMNAWAVE_INTERNAL_SQUAD_UUID or check API access.'
            log.error(self.last_error)
            return self._fallback_access(telegram_id)

        email = self._email_for_user(telegram_id)
        current = await self._get_user_by_email(email)
        expire_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
        if current and current.get('expireAt'):
            try:
                current_expire = datetime.fromisoformat(str(current['expireAt']).replace('Z', '+00:00'))
                if current_expire > datetime.now(timezone.utc):
                    expire_at = current_expire + timedelta(days=duration_days)
            except ValueError:
                log.warning('Cannot parse current expireAt: %s', current.get('expireAt'))

        traffic_limit_bytes = int(traffic_gb) * 1024 * 1024 * 1024 if int(traffic_gb or 0) > 0 else None
        payload: dict[str, Any] = {'status': 'ACTIVE', 'expireAt': self._to_iso(expire_at), 'activeInternalSquads': [squad_uuid], 'email': email, 'telegramId': int(telegram_id), 'description': f'Telegram VPN bot user {telegram_id}', 'trafficLimitStrategy': 'NO_RESET'}
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
        if not isinstance(user, dict):
            raise RuntimeError(f'Remnawave returned unexpected user payload: {data}')
        remnawave_user_id = str(user.get('uuid') or user.get('id') or '')
        subscription_url = self._subscription_url(user)
        log.info('Remnawave user ready: telegram_id=%s uuid=%s subscription_url=%s', telegram_id, remnawave_user_id, subscription_url)
        return RemnawaveAccess(remnawave_user_id=remnawave_user_id, subscription_url=subscription_url, raw=user)
