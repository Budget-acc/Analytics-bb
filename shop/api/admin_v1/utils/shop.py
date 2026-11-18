import logging
from rest_framework.exceptions import PermissionDenied
from shop.models import Shop, ShopUser

logger = logging.getLogger("api_requests")

def get_active_shop_id(request):
    shop_id = request.headers.get("X-Active-Shop-ID")
    user = request.user
    print("Shop ID in request:", shop_id)
    logger.info(f"Shop ID in request: {shop_id}")

    if shop_id:
        try:
            shop_id = int(shop_id)
        except ValueError:
            raise PermissionDenied("Invalid Shop ID format. Must be an integer.")

        # Checking if shop exists, not soft-deleted, and is active
        shop = Shop.objects.filter(id=shop_id, deleted=None).first()
        if not shop:
            raise PermissionDenied("Invalid or deleted Shop ID provided.")
        if not shop.is_active:
            raise PermissionDenied("This shop is inactive.")

        # Checking if user is actively a member of the shop (ShopUser not soft-deleted and is_active)
        shop_user = ShopUser.objects.filter(
            user=user, shop_id=shop_id, deleted=None
        ).first()
        if not shop_user:
            raise PermissionDenied("You don't have access to this shop.")
        if not shop_user.is_active:
            raise PermissionDenied("Your access to this shop has been deactivated.")

        return shop_id

    # Fallback to first assigned (non-deleted, active) shop user entry
    first_shop_user = (
        ShopUser.objects.filter(user=user, deleted=None, is_active=True)
        .select_related("shop")
        .order_by("created_at")
        .first()
    )

    if not first_shop_user:
        raise PermissionDenied("User is not associated with any active shop.")

    if not first_shop_user.shop or first_shop_user.shop.deleted or not first_shop_user.shop.is_active:
        raise PermissionDenied("Associated shop is inactive or deleted.")

    return first_shop_user.shop.id
