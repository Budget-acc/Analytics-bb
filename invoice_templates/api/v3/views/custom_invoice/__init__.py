from .base import CustomInvoiceViewSet
from .list import list
from .create import create
from .retrieve import retrieve
from .partial_update import partial_update
from .destroy import destroy
from .preview import preview_custom_invoice

CustomInvoiceViewSet.list = list
CustomInvoiceViewSet.create = create
CustomInvoiceViewSet.retrieve = retrieve
CustomInvoiceViewSet.partial_update = partial_update
CustomInvoiceViewSet.destroy = destroy
CustomInvoiceViewSet.preview_custom_invoice = preview_custom_invoice

__all__ = ["CustomInvoiceViewSet"]
