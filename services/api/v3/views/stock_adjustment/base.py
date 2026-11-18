from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from services.constants.stock_adjustment import DEV_MESSAGE_BY_MESSAGE, StockAdjustmentMessages as M
from services.models import Product
from shop.api.v3.utils.shop import get_active_shop_id
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission


class ProductStockAdjustmentViewSet(StandardResponseMixin, viewsets.ViewSet):
    """Move product variant stock / fabric out of service and back.

    Independent of the existing product and variant APIs: they are not
    changed, and the booking / availability logic keeps reading `stock` and
    `fabric_length` as the usable amounts.
    """
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
    ]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)

        try:
            self.shop_id = get_active_shop_id(request)
        except Exception:
            self.permission_denied(
                request,
                message="You do not have permission to access this shop."
            )

    def _get_product(self, product_id):
        """The product if it belongs to the active shop (one query), else None."""
        return (
            Product.objects
            .select_related("shop_service__shop")
            .only("id", "name", "shop_service__id", "shop_service__shop__id")
            .filter(
                id=product_id,
                shop_service__shop_id=self.shop_id,
                shop_service__deleted__isnull=True,
            )
            .first()
        )

    def _product_not_found_response(self):
        return self.not_found_response(
            resource="Product",
            message=M.PRODUCT_NOT_FOUND.message,
            dev_message=M.PRODUCT_NOT_FOUND.dev_message,
        )

    def _request_error_response(self, errors):
        """400 for serializer errors.

        The shared validation response never sends field errors to the client
        (`data` is null), so `message` is the first error and `dev_message`
        names the field that failed, e.g. variants[1].quantity.
        """
        path, first = _first_error(errors)
        if first not in DEV_MESSAGE_BY_MESSAGE:
            message, dev_message = M.INVALID_REQUEST
            return self.validation_error_response(errors=errors, message=message, dev_message=dev_message)

        duplicates = [str(variant_id) for variant_id in errors.get("duplicate_variant_ids", [])]
        dev_message = DEV_MESSAGE_BY_MESSAGE[first].format(ids=", ".join(duplicates))
        if path:
            dev_message = f"{dev_message} Field: {path}."
        return self.validation_error_response(errors=errors, message=first, dev_message=dev_message)


def _first_error(errors, path=""):
    """Return (field path, message) of the first error in a DRF error structure."""
    if isinstance(errors, dict):
        items = errors.items()
    elif isinstance(errors, (list, tuple)):
        items = enumerate(errors)
    else:
        return path, str(errors)

    for key, value in items:
        if not value:
            continue  # valid list items appear as {}
        if isinstance(key, int):
            child = f"{path}[{key}]"
        elif key == "non_field_errors" or (isinstance(value, (list, tuple)) and value and isinstance(value[0], str)):
            # A list of messages: report the field itself, not the message index.
            child = path if key == "non_field_errors" else (f"{path}.{key}" if path else key)
            return child, str(value[0])
        else:
            child = f"{path}.{key}" if path else key
        found = _first_error(value, child)
        if found[1]:
            return found
    return path, None


def decimal_str(value):
    """Decimal -> "0.00" string (JSON-safe, same format DRF DecimalField uses); None stays None."""
    return None if value is None else f"{value:.2f}"
