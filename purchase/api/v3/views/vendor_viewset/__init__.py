from .base import VendorViewSet
from .helpers import _get_vendor_or_404
from .list import list
from .create import create
from .retrieve import retrieve
from .update import update, partial_update
from .destroy import destroy
from .purchases import purchases
from .vendor_monthly_summary import vendor_monthly_summary
from .vendor_payment_history import vendor_payment_history

# ---- Assign helper methods ----
VendorViewSet._get_vendor_or_404 = _get_vendor_or_404

# ---- Assign public/router-recognized methods ----
VendorViewSet.list = list
VendorViewSet.create = create
VendorViewSet.retrieve = retrieve
VendorViewSet.update = update
VendorViewSet.partial_update = partial_update
VendorViewSet.destroy = destroy
VendorViewSet.purchases = purchases
VendorViewSet.vendor_monthly_summary = vendor_monthly_summary
VendorViewSet.vendor_payment_history = vendor_payment_history

__all__ = ["VendorViewSet"]
