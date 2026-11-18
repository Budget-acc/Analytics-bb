from services.models import ShopOwnerService


def _get_destination_service(self, shop_id, general_service, user, create_if_missing=False):
    """Get destination service, optionally create if missing and user has permission."""
    destination_service = ShopOwnerService.objects.select_related('shop').filter(
        shop_id=shop_id,
        general_service=general_service,
        is_active=True,
        shop__shop_users__user=user,
        shop__shop_users__role__in=self.ALLOWED_ROLES,
        shop__shop_users__is_active=True        
    ).first()

    if destination_service:
        return destination_service

    # Check user permission
    # if not self._check_user_permission(user, shop_id):
    #     raise PermissionDenied("You are not authorized to transfer stock to this shop.")

    if create_if_missing:
        destination_service, _ = ShopOwnerService.objects.get_or_create(
            shop_id=shop_id,
            general_service=general_service,
            defaults={"is_active": True}
        )
        return destination_service

    return None
