from .base import ClientViewSet
from .list import list
from .retrieve import retrieve
from .create import create
from .partial_update import partial_update
from .destroy import destroy
# Same Client Details Excel export as v3 (GET, query params, file response).
from booking_v2.api.v3.views.client_viewset.export_excel import export_excel

ClientViewSet.list = list
ClientViewSet.retrieve = retrieve
ClientViewSet.create = create
ClientViewSet.partial_update = partial_update
ClientViewSet.destroy = destroy
ClientViewSet.export_excel = export_excel

__all__ = ["ClientViewSet"]
