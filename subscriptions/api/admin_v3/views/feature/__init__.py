from .base import FeatureViewSet
from .list import list
from .retrieve import retrieve
from .create import create
from .partial_update import partial_update
from .destroy import destroy
from .list_shops_by_feature import list_shops_by_feature

FeatureViewSet.list = list
FeatureViewSet.retrieve = retrieve
FeatureViewSet.create = create
FeatureViewSet.partial_update = partial_update
FeatureViewSet.destroy = destroy
FeatureViewSet.list_shops_by_feature = list_shops_by_feature

__all__ = ['FeatureViewSet']
