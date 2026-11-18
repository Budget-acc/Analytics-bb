from .base import InvoicePreferenceViewSet
from .get_preference import get_preference
from .set_default import set_default
from .toggle_customization import toggle_customization
from .update_terms import update_terms

# GET preference/ is the ViewSet's list route.
InvoicePreferenceViewSet.list = get_preference
InvoicePreferenceViewSet.set_default = set_default
InvoicePreferenceViewSet.toggle_customization = toggle_customization
InvoicePreferenceViewSet.update_terms = update_terms

__all__ = ["InvoicePreferenceViewSet"]
