from .base import ShopUserViewSet
from .create import create
from .create_user_with_shop_assignment import create_user_with_shop_assignment

ShopUserViewSet.create = create
ShopUserViewSet.create_user_with_shop_assignment = create_user_with_shop_assignment
