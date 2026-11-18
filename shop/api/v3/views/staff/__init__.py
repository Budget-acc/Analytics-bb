from .base import StaffViewSet
from .list import list
from .retrieve import retrieve
from .create import create
from .partial_update import partial_update
from .destroy import destroy
from .restore import restore

StaffViewSet.list = list
StaffViewSet.retrieve = retrieve
StaffViewSet.create = create
StaffViewSet.partial_update = partial_update
StaffViewSet.destroy = destroy
StaffViewSet.restore = restore
