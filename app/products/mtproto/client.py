from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp


@dataclass(frozen=True)
class MTProtoControlClient:
    base_url: str
    token: str = ''
    dry_run: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.dry_run or self.base_url)

    def _headers(self) -> dict[str, str]:
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        return headers

    async def health(self) -> dict[str, Any]:
        if self.dry_run:
            return {'ok': True, 'mode': 'dry-run'}
        if not self.base_url:
            raise RuntimeError('MTPROTO_CONTROL_URL is empty')
        async with aiohttp.ClientSession(headers=self._headers()) as session:
            async with session.get(f'{self.base_url.rstrip("/")}/health', timeout=10) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f'MTProto control health HTTP {resp.status}: {text[:300]}')
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {'ok': True, 'body': text[:300]}
                return data if isinstance(data, dict) else {'ok': True}

    async def apply(self, *, telegram_id: int, secret: str, enabled: bool) -> None:
        if self.dry_run:
            return
        if not self.base_url:
            raise RuntimeError('MTPROTO_CONTROL_URL is empty')
        payload = {'telegram_id': int(telegram_id), 'secret': secret, 'enabled': bool(enabled)}
        url = f'{self.base_url.rstrip("/")}/v1/users/{int(telegram_id)}'
        async with aiohttp.ClientSession(headers=self._headers()) as session:
            async with session.put(url, json=payload, timeout=20) as resp:
                text = await resp.text()
                if resp.status not in {200, 201, 204}:
                    raise RuntimeError(f'MTProto control PUT HTTP {resp.status}: {text[:300]}')
