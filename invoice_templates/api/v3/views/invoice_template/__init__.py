from .base import InvoiceTemplateViewSet
from .list import list
from .retrieve import retrieve
from .preview import preview

InvoiceTemplateViewSet.list = list
InvoiceTemplateViewSet.retrieve = retrieve
InvoiceTemplateViewSet.preview = preview

__all__ = ["InvoiceTemplateViewSet"]
