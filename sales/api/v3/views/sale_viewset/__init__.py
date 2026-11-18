from .base import SaleViewSetBase
from .list_retrieve import SaleListRetrieveMixin
from .create import SaleCreateMixin
from .update import SaleUpdateMixin
from .payment import SalePaymentMixin
from .destroy import SaleDestroyMixin
from .invoice import SaleInvoiceMixin


class SaleViewSet(
    SaleListRetrieveMixin,
    SaleCreateMixin,
    SaleUpdateMixin,
    SalePaymentMixin,
    SaleDestroyMixin,
    SaleInvoiceMixin,
    SaleViewSetBase,
):
    pass
