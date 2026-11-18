from .base import ProductAdminViewSet
from .transfer_history import transfer_history
from .transfer_stats import transfer_stats
from .products_with_variants import products_with_variants

ProductAdminViewSet.transfer_history = transfer_history
ProductAdminViewSet.transfer_stats = transfer_stats
ProductAdminViewSet.products_with_variants = products_with_variants

__all__ = ['ProductAdminViewSet']
