import logging

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.custom_pagination import StandardPageNumberPagination
from ERP.utils.mixins import StandardResponseMixin
from shop.api.v3.utils import get_active_shop_id

from purchase.models.vendor import Vendor
from ...serializers import VendorSerializer, VendorListSerializer
from subscriptions.api.admin_v3.utils.role_permissions import ManagementPermission
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger("api_requests")


class VendorViewSet(StandardResponseMixin, viewsets.ModelViewSet):
    """Vendor CRUD APIs. File-per-action package, mirrors
    purchase_viewset/'s structure (purchase/api/v3/views/purchase_viewset/base.py)."""

    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ManagementPermission
    ]

    pagination_class = StandardPageNumberPagination

    queryset = Vendor.objects.all()

    SEARCH_FIELDS = [
        "name",
        "contact_number",
        "email",
        "gst_number",
    ]

    ORDERING_FIELDS = [
        "name",
        "-name",
        "created_at",
        "-created_at",
        "updated_at",
        "-updated_at",
    ]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.error(f"Shop permission denied in VendorViewSet: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Your access to this shop has been deactivated.",
                "data": None,
                "dev_message": str(e),
            })

    def get_serializer_context(self):
        """
        Add shop_id explicitly to serializer context for validation.
        This avoids fragile parser_context access in serializers.
        """
        context = super().get_serializer_context()
        context['shop_id'] = self.shop_id
        return context

    def get_queryset(self):
        return Vendor.objects.filter(
            shop_id=self.shop_id,
            deleted__isnull=True,
        ).order_by("-created_at")

    def get_serializer_class(self):
        if self.action == "list":
            return VendorListSerializer

        return VendorSerializer

    def perform_create(self, serializer):
        serializer.save(
            shop_id=self.shop_id,
            created_by=self.request.user,
        )

    def perform_update(self, serializer):
        serializer.save(
            updated_by=self.request.user,
        )


__all__ = ["VendorViewSet"]
