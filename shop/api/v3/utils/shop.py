# utils.py
from rest_framework.exceptions import PermissionDenied
from shop.models import Shop, ShopUser
import logging

logger = logging.getLogger("api_requests")


def get_active_shop_id(request):
    shop_id = request.headers.get("X-Active-Shop-ID")
    user = request.user
    print(f"Shop ID in request: {shop_id}")
    logger.info(f"Shop ID in request: {shop_id}")

    if shop_id:
        try:
            shop_id = int(shop_id)
        except ValueError:
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Invalid Shop ID format. Must be an integer.",
                "data": None
            })

        shop = Shop.objects.filter(id=shop_id, deleted=None).first()
        if not shop:
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Invalid or deleted Shop ID provided.",
                "data": None
            })
        if not shop.is_active:
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "This shop is inactive.",
                "data": None
            })
        shop_user = ShopUser.objects.filter(
            user=user, shop_id=shop_id, deleted=None
        ).first()
        if not shop_user:
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "You do not have permission to access this shop.",
                "data": None
            })
        if not shop_user.is_active:
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Your access to this shop has been deactivated.",
                "data": None
            })
        return shop_id

    first_shop_user = (
        ShopUser.objects.filter(user=user, deleted=None, is_active=True)
        .select_related("shop")
        .order_by("created_at")
        .first()
    )

    if not first_shop_user:
        raise PermissionDenied({
            "status": "permission_denied",
            "message": "User is not associated with any active shop.",
            "data": None
        })
    if not first_shop_user.shop or first_shop_user.shop.deleted or not first_shop_user.shop.is_active:
        raise PermissionDenied({
            "status": "permission_denied",
            "message": "Associated shop is inactive or deleted.",
            "data": None
        })

    return first_shop_user.shop.id




def get_active_shop_and_user(request):
    """
    Resolve the active shop and the ShopUser object for the current request.
    Priority:
      1. X-Active-Shop-ID header
      2. Fallback: user's first active shop
    """
    user = request.user
    shop_id = request.headers.get("X-Active-Shop-ID")
    logger.info(f"Shop ID in request: {shop_id}")

    if shop_id:
        try:
            shop_id = int(shop_id)
        except ValueError:
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Invalid Shop ID format. Must be an integer.",
                "data": None
            })

        shop = Shop.objects.filter(id=shop_id, deleted=None, is_active=True).first()
        if not shop:
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Invalid, inactive, or deleted Shop ID provided.",
                "data": None
            })

        shop_user = ShopUser.objects.filter(
            user=user, shop=shop, deleted=None, is_active=True
        ).first()
        if not shop_user:
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "You do not have permission to access this shop.",
                "data": None
            })

        return shop, shop_user

    # fallback: first active shop of the user
    shop_user = (
        ShopUser.objects.filter(user=user, deleted=None, is_active=True)
        .select_related("shop")
        .order_by("created_at")
        .first()
    )
    if not shop_user or not shop_user.shop or shop_user.shop.deleted or not shop_user.shop.is_active:
        raise PermissionDenied({
            "status": "permission_denied",
            "message": "User is not associated with any active shop.",
            "data": None
        })

    return shop_user.shop, shop_user
