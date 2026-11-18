from .base import ShopTaxConfigurationViewSet
from .list import list
from .create import create
from .partial_update import partial_update
from .destroy import destroy

ShopTaxConfigurationViewSet.list = list
ShopTaxConfigurationViewSet.create = create
ShopTaxConfigurationViewSet.partial_update = partial_update
ShopTaxConfigurationViewSet.destroy = destroy

__all__ = ['ShopTaxConfigurationViewSet']
