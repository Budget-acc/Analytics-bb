from .base import SubscriptionDetailsViewSet
from .retrieve import retrieve

SubscriptionDetailsViewSet.list = retrieve

__all__ = ["SubscriptionDetailsViewSet"]
