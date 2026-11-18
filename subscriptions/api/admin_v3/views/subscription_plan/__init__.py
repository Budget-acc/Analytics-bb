from .base import SubscriptionPlanViewSet
from .list import list
from .retrieve import retrieve
from .create import create
from .partial_update import partial_update
from .destroy import destroy

SubscriptionPlanViewSet.list = list
SubscriptionPlanViewSet.retrieve = retrieve
SubscriptionPlanViewSet.create = create
SubscriptionPlanViewSet.partial_update = partial_update
SubscriptionPlanViewSet.destroy = destroy

__all__ = ['SubscriptionPlanViewSet']
