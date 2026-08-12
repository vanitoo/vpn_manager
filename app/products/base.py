from __future__ import annotations

from typing import Any, Protocol


class ProductService(Protocol):
    """Minimal contract for independently fulfilled products."""

    product_type: str

    async def issue(self, order_id: int) -> Any: ...

    async def extend(self, subscription_id: int, days: int) -> Any: ...

    async def disable(self, subscription_id: int) -> None: ...

    async def get_access(self, subscription_id: int) -> Any: ...

