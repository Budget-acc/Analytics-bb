from shop.models import ShopUser


def _check_user_permission(self, user, shop_ids):
    """
    Check if user has permission (allowed roles) in all given shop_ids.
    shop_ids: int or list of int
    """
    if isinstance(shop_ids, int) or isinstance(shop_ids, str):
        shop_ids = [int(shop_ids)]
    allowed_count = ShopUser.objects.filter(
        shop_id__in=shop_ids,
        user=user,
        role__in=self.ALLOWED_ROLES,
        is_active=True
    ).values('shop_id').distinct().count()
    return allowed_count == len(shop_ids)
