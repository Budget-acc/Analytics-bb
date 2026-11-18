from .base import MainServiceCategoryViewSet
from .list import list
from .retrieve import retrieve
from .create import create
from .partial_update import partial_update
from .destroy import destroy

MainServiceCategoryViewSet.list = list
MainServiceCategoryViewSet.retrieve = retrieve
MainServiceCategoryViewSet.create = create
MainServiceCategoryViewSet.partial_update = partial_update
MainServiceCategoryViewSet.destroy = destroy

__all__ = ['MainServiceCategoryViewSet']
