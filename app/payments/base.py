from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class PaymentProviderError(RuntimeError):
    pass


@dataclass
class CreatedPayment:
    provider: str
    provider_payment_id: str
    status: str
    payment_url: str
    raw: dict[str, Any]


@dataclass
class PaymentStatus:
    provider: str
    provider_payment_id: str
    status: str
    paid: bool
    raw: dict[str, Any]
