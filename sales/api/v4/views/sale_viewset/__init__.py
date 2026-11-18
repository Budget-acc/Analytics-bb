from .base import SaleViewSetBase
from .list import SaleListMixin
from .retrieve import SaleRetrieveMixin
from .create import SaleCreateMixin
from .partial_update import SalePartialUpdateMixin
from .update_sale import SaleUpdateSaleMixin
from .payment import SalePaymentMixin
from .destroy import SaleDestroyMixin
from .send_invoice import SaleInvoiceMixin


class SaleViewSet(
    SaleListMixin,
    SaleRetrieveMixin,
    SaleCreateMixin,
    SalePartialUpdateMixin,
    SaleUpdateSaleMixin,
    SalePaymentMixin,
    SaleDestroyMixin,
    SaleInvoiceMixin,
    SaleViewSetBase,
):
    pass
