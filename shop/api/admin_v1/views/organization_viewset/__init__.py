from .base import OrganizationViewSet
from .create import create
from .list import list
from .retrieve import retrieve
from .partial_update import partial_update
from .destroy import destroy
from .assign_shops import assign_shops

OrganizationViewSet.create = create
OrganizationViewSet.list = list
OrganizationViewSet.retrieve = retrieve
OrganizationViewSet.partial_update = partial_update
OrganizationViewSet.destroy = destroy
OrganizationViewSet.assign_shops = assign_shops
