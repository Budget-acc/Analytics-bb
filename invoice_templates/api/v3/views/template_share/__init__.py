from .base import TemplateShareViewSet
from .generate import generate
from .retrieve_active import retrieve_active
from .revoke import revoke
from .lookup import lookup
from .import_shared import import_shared

# GET / DELETE share/{custom_invoice_id}/ are the ViewSet's retrieve / destroy routes.
TemplateShareViewSet.retrieve = retrieve_active
TemplateShareViewSet.destroy = revoke
TemplateShareViewSet.generate = generate
TemplateShareViewSet.lookup = lookup
TemplateShareViewSet.import_shared = import_shared

__all__ = ["TemplateShareViewSet"]
