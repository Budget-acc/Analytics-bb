from .base import ClientViewSet
from .list import list
from .retrieve import retrieve
from .create import create
from .partial_update import partial_update
from .destroy import destroy
from .export_excel import export_excel
from .helpers import _validate_and_convert_phone

ClientViewSet.list = list
ClientViewSet.retrieve = retrieve
ClientViewSet.create = create
ClientViewSet.partial_update = partial_update
ClientViewSet.destroy = destroy
ClientViewSet.export_excel = export_excel
ClientViewSet._validate_and_convert_phone = _validate_and_convert_phone

__all__ = ["ClientViewSet"]
