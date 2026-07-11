from __future__ import annotations

import base64
import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

import aiohttp

from app.config import Settings
from app.payments.base import CreatedPayment, PaymentProviderError, PaymentStatus

API_BASE = 'https://api.cryptomus.com/v1'


class CryptomusProvider:
    name = 'cryptomus'

    def __init__(self, settings: Settings):
        self.settings = settings
        self.merchant_id = settings.cryptomus_merchant_id
        self.api_key = settings.cryptomus_api_key

    @property
    def is_configured(self) -> bool:
        return bool(self.merchant_id and self.api_key)

    def _headers(self, payload: dict[str, Any]) -> dict[str, str]:
        raw = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        encoded = base64.b64encode(raw).decode('ascii')
        sign = hashlib.md5((encoded + self.api_key).encode('utf-8')).hexdigest()
        return {'merchant': self.merchant_id, 'sign': sign, 'Content-Type': 'application/json'}

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_configured:
            raise PaymentProviderError('Cryptomus не настроен: укажите CRYPTOMUS_MERCHANT_ID и CRYPTOMUS_API_KEY')
        async with aiohttp.ClientSession() as session:
            async with session.post(f'{API_BASE}{path}', json=payload, headers=self._headers(payload), timeout=30) as response:
                data = await response.json(content_type=None)
                if response.status >= 400 or data.get('state') == 1:
                    raise PaymentProviderError(f'Cryptomus API error {response.status}: {data}')
                return data

    async def create_payment(
        self,
        *,
        amount_rub: int,
        description: str,
        return_url: str,
        metadata: dict[str, Any],
        receipt_customer: dict[str, str] | None = None,
    ) -> CreatedPayment:
        order_id = str(metadata.get('payment_id') or metadata.get('order_id') or uuid.uuid4())
        payload = {
            'amount': str(Decimal(str(amount_rub)).quantize(Decimal('0.01'))),
            'currency': self.settings.cryptomus_currency or 'RUB',
            'order_id': order_id,
            'url_return': self.settings.cryptomus_return_url or return_url,
            'is_payment_multiple': False,
            'lifetime': int(self.settings.pending_payment_ttl_minutes * 60),
            'additional_data': json.dumps(metadata, ensure_ascii=False),
        }
        data = await self._post('/payment', payload)
        result = data.get('result') or {}
        payment_url = result.get('url') or result.get('url_callback') or ''
        payment_id = result.get('uuid') or order_id
        if not payment_url:
            raise PaymentProviderError(f'Cryptomus не вернул URL оплаты: {data}')
        return CreatedPayment(provider=self.name, provider_payment_id=str(payment_id), status=str(result.get('status') or 'pending'), payment_url=payment_url, raw=data)

    async def get_status(self, provider_payment_id: str) -> PaymentStatus:
        data = await self._post('/payment/info', {'uuid': provider_payment_id})
        result = data.get('result') or {}
        status = str(result.get('payment_status') or result.get('status') or '')
        paid = status in {'paid', 'paid_over'}
        return PaymentStatus(provider=self.name, provider_payment_id=provider_payment_id, status=status, paid=paid, raw=data)
