import logging
from shop.api.v3.utils.shop import get_active_shop_id
from shop.models import Shop, ShopUser


logger = logging.getLogger('api_requests')



def get_user_info(user):
    try:
        if user and hasattr(user, 'is_authenticated') and user.is_authenticated:
            # Try different ways to get the user's name
            full_name = ""
            if hasattr(user, 'get_full_name') and callable(user.get_full_name):
                full_name = user.get_full_name()
            elif hasattr(user, 'full_name') and user.full_name:
                full_name = user.full_name
            elif hasattr(user, 'first_name') and hasattr(user, 'last_name'):
                full_name = f"{user.first_name} {user.last_name}".strip()
            elif hasattr(user, 'username'):
                full_name = user.username
            else:
                full_name = "Authenticated User"  # Default for authenticated users

            return full_name, user.id
        
        # For anonymous users or unauthenticated
        return "Anonymous", None
        
    except Exception as e:
        logger.exception("Error in get_user_info: %s", str(e))
        return "Unknown User", None


def get_shop_info(request):
    try:
        logger.debug("get_shop_info -> Request Headers: %s", dict(request.headers))
        
        # First try to get shop from headers
        try:
            shop_id = get_active_shop_id(request)
            if shop_id:
                shop = Shop.objects.filter(id=shop_id, deleted=None, is_active=True).first()
                if shop:
                    return shop.name, shop.id
        except Exception:
            logger.debug("Couldn't get shop from headers, trying fallback")

        # Fallback to user's associated shops
        user = getattr(request, 'user', None)
        if user and hasattr(user, 'is_authenticated') and user.is_authenticated:
            shop_user = ShopUser.objects.filter(
                user=user,
                deleted=None,
                is_active=True
            ).select_related('shop').order_by('created_at').first()
            
            if shop_user and shop_user.shop and not shop_user.shop.deleted and shop_user.shop.is_active:
                return shop_user.shop.name, shop_user.shop.id

    except Exception as e:
        logger.exception("Error in get_shop_info: %s", str(e))
    
    return "Unknown Shop", None


# def flatten_error(detail):
#     """
#     Convert DRF ErrorDetail or dict of lists to plain string or dict of strings
#     """
#     if isinstance(detail, list) and detail and hasattr(detail[0], 'code'):
#         return str(detail[0])
#     elif isinstance(detail, dict):
#         flat = {}
#         for key, value in detail.items():
#             if isinstance(value, list) and value:
#                 flat[key] = str(value[0])
#             else:
#                 flat[key] = str(value)
#         return flat
#     return str(detail)


def get_client_ip(request):
    """Get the client's IP address from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', 'Unknown')
    return ip