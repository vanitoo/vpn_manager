"""SOCKS5 product boundary.

Implementation belongs to the feature/socks5 branch. Keeping the module empty
and disabled by default makes the integration point testable without changing
the current VPN purchase and fulfillment flows.
"""

from app.products.registry import ProductModule

module = ProductModule(code="socks5")

__all__ = ["module"]

