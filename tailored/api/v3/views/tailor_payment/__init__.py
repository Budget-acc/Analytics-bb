from .base import TailorPaymentViewSet
from .list import list
from .retrieve import retrieve
from .create import create
from .partial_update import partial_update
from .destroy import destroy
from .history import history

TailorPaymentViewSet.list = list
TailorPaymentViewSet.retrieve = retrieve
TailorPaymentViewSet.create = create
TailorPaymentViewSet.partial_update = partial_update
TailorPaymentViewSet.destroy = destroy
TailorPaymentViewSet.history = history

__all__ = ['TailorPaymentViewSet']
