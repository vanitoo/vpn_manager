"""Optional product modules.

The existing VPN flow remains the default application flow. New products are
registered here and must not import or mutate VPN handlers when disabled.
"""

from app.products.registry import ProductModule, enabled_product_modules

__all__ = ["ProductModule", "enabled_product_modules"]

