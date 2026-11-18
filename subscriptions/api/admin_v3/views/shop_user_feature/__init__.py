from .base import ShopUserFeatureViewSet
from .list import list
from .retrieve import retrieve
from .create import create
from .partial_update import partial_update
from .destroy import destroy

ShopUserFeatureViewSet.list = list
ShopUserFeatureViewSet.retrieve = retrieve
ShopUserFeatureViewSet.create = create
ShopUserFeatureViewSet.partial_update = partial_update
ShopUserFeatureViewSet.destroy = destroy

__all__ = ['ShopUserFeatureViewSet']
