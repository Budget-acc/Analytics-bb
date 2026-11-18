from .base import BookingViewSet
from .list import list
from .retrieve import retrieve
from .create import create
from .partial_update import partial_update
from .destroy import destroy

BookingViewSet.list = list
BookingViewSet.retrieve = retrieve
BookingViewSet.create = create
BookingViewSet.partial_update = partial_update
BookingViewSet.destroy = destroy

__all__ = ["BookingViewSet"]
