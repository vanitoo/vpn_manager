from __future__ import annotations

from app.config import Settings
from app.payments.base import PaymentProviderError


class PlategaProvider:
    name = 'platega'

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.platega_merchant_id and self.settings.platega_api_key)

    async def create_payment(self, **kwargs):
        raise PaymentProviderError('Platega модуль подключен как заглушка. Нужны актуальные API-параметры Platega для создания счета.')

    async def get_status(self, provider_payment_id: str):
        raise PaymentProviderError('Platega проверка статуса пока не реализована.')
