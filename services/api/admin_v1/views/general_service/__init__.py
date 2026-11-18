from .base import GeneralServiceViewSet
from .list import list
from .retrieve import retrieve
from .create import create
from .partial_update import partial_update
from .destroy import destroy
from .assign_to_shop import assign_to_shop
from .list_shop_services import list_shop_services
from .change_service_status import change_service_status
from .by_category import by_category

GeneralServiceViewSet.list = list
GeneralServiceViewSet.retrieve = retrieve
GeneralServiceViewSet.create = create
GeneralServiceViewSet.partial_update = partial_update
GeneralServiceViewSet.destroy = destroy
GeneralServiceViewSet.assign_to_shop = assign_to_shop
GeneralServiceViewSet.list_shop_services = list_shop_services
GeneralServiceViewSet.change_service_status = change_service_status
GeneralServiceViewSet.by_category = by_category

__all__ = ['GeneralServiceViewSet']
