from __future__ import annotations

from app.config import Settings
from app.payments.base import PaymentProviderError


class LavaProvider:
    name = 'lava'

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.lava_shop_id and self.settings.lava_api_key)

    async def create_payment(self, **kwargs):
        raise PaymentProviderError('Lava модуль подключен как заглушка. Нужны актуальные API-параметры Lava для создания счета.')

    async def get_status(self, provider_payment_id: str):
        raise PaymentProviderError('Lava проверка статуса пока не реализована.')
