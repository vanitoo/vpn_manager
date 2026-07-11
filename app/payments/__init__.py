from __future__ import annotations

from app.config import Settings
from app.payments.cryptomus import CryptomusProvider
from app.payments.yookassa import YooKassaProvider
from app.payments.lava import LavaProvider
from app.payments.platega import PlategaProvider


def build_provider(name: str, settings: Settings):
    if name == 'yookassa':
        return YooKassaProvider(settings)
    if name == 'cryptomus':
        return CryptomusProvider(settings)
    if name == 'lava':
        return LavaProvider(settings)
    if name == 'platega':
        return PlategaProvider(settings)
    raise ValueError(f'Unknown provider: {name}')
