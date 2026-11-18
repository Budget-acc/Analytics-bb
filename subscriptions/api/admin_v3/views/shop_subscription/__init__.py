from .base import ShopSubscriptionViewSet
from .subscribe import subscribe
from .add_features import add_features
from .cancel import cancel
from .update_subscription import update_subscription
from .update_feature import update_feature
from .delete_feature import delete_feature

ShopSubscriptionViewSet.subscribe = subscribe
ShopSubscriptionViewSet.add_features = add_features
ShopSubscriptionViewSet.cancel = cancel
ShopSubscriptionViewSet.update_subscription = update_subscription
ShopSubscriptionViewSet.update_feature = update_feature
ShopSubscriptionViewSet.delete_feature = delete_feature

__all__ = ['ShopSubscriptionViewSet']
