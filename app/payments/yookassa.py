from __future__ import annotations

import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import aiohttp
from aiohttp import BasicAuth

from app.config import Settings
from app.payments.base import CreatedPayment, PaymentProviderError, PaymentStatus

API_BASE = 'https://api.yookassa.ru/v3'


def _amount(value_rub: int | float | Decimal) -> str:
    return str(Decimal(str(value_rub)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


class YooKassaProvider:
    name = 'yookassa'

    def __init__(self, settings: Settings):
        self.settings = settings
        self.shop_id = settings.yookassa_shop_id
        self.secret_key = settings.yookassa_secret_key

    @property
    def is_configured(self) -> bool:
        return bool(self.shop_id and self.secret_key)

    def _auth(self) -> BasicAuth:
        return BasicAuth(self.shop_id, self.secret_key)

    async def create_payment(
        self,
        *,
        amount_rub: int,
        description: str,
        return_url: str,
        metadata: dict[str, Any],
        receipt_customer: dict[str, str] | None = None,
    ) -> CreatedPayment:
        if not self.is_configured:
            raise PaymentProviderError('ЮKassa не настроена: укажите YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY')

        payload: dict[str, Any] = {
            'amount': {'value': _amount(amount_rub), 'currency': 'RUB'},
            'capture': True,
            'confirmation': {'type': 'redirect', 'return_url': self.settings.yookassa_return_url or return_url},
            'description': description[:128],
            'metadata': {k: str(v) for k, v in metadata.items()},
        }

        if receipt_customer:
            payload['receipt'] = {
                'customer': receipt_customer,
                'items': [
                    {
                        'description': description[:128] or 'Доступ к VPN-сервису',
                        'quantity': '1.00',
                        'amount': {'value': _amount(amount_rub), 'currency': 'RUB'},
                        'vat_code': 1,
                        'payment_subject': 'service',
                        'payment_mode': 'full_payment',
                    }
                ],
            }

        headers = {'Idempotence-Key': str(uuid.uuid4())}
        async with aiohttp.ClientSession(auth=self._auth()) as session:
            async with session.post(f'{API_BASE}/payments', json=payload, headers=headers, timeout=30) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    raise PaymentProviderError(f'ЮKassa create payment error {resp.status}: {data}')

        confirmation = data.get('confirmation') or {}
        payment_url = confirmation.get('confirmation_url') or ''
        if not payment_url:
            raise PaymentProviderError(f'ЮKassa не вернула confirmation_url: {data}')
        return CreatedPayment(
            provider=self.name,
            provider_payment_id=data['id'],
            status=data.get('status', 'pending'),
            payment_url=payment_url,
            raw=data,
        )

    async def get_status(self, provider_payment_id: str) -> PaymentStatus:
        if not self.is_configured:
            raise PaymentProviderError('ЮKassa не настроена')
        async with aiohttp.ClientSession(auth=self._auth()) as session:
            async with session.get(f'{API_BASE}/payments/{provider_payment_id}', timeout=30) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    raise PaymentProviderError(f'ЮKassa status error {resp.status}: {data}')
        status = data.get('status', '')
        return PaymentStatus(
            provider=self.name,
            provider_payment_id=provider_payment_id,
            status=status,
            paid=status == 'succeeded' or bool(data.get('paid') and status in {'succeeded', 'waiting_for_capture'}),
            raw=data,
        )
