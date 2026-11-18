from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from django.core.exceptions import PermissionDenied
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from services.models import ShopOwnerService
from services.api.v3.serializers import ShopOwnerServiceSerializer
from shop.api.v3.utils.shop import get_active_shop_id


class SelectedServicesView(StandardResponseMixin, APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [CutoffJWTAuthentication]

    @extend_schema(
        tags=["Service & Product Management V3"],
        summary="List selected shop services",
        description="Returns active services selected by the currently active shop, ordered by display_order (ascending) then id.",
        responses={
            200: OpenApiResponse(description="Standard success envelope with service list."),
            403: OpenApiResponse(
                description=(
                    "Error messages:\n"
                    "- You do not have permission to access this shop."
                )
            ),
        },
        examples=[
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Services retrieved successfully.",
                    "data": [
                        {"id": 12, "service_name": "Tailoring", "description": "Custom tailoring", "icon": "https://example.com/icon.png", "main_service_name": "Clothing", "display_order": 0},
                        {"id": 5, "service_name": "Embroidery", "description": "Embroidery work", "icon": "https://example.com/icon2.png", "main_service_name": "Clothing", "display_order": 1},
                    ],
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Permission denied",
                value={"status": "permission_denied", "message": "You do not have permission to access this shop.", "data": None},
                response_only=True,
                status_codes=["403"],
            ),
        ],
    )
    def get(self, request):
        try:
            shop_id = get_active_shop_id(request)
        except PermissionDenied:
            return self.permission_denied_response(
                message="You do not have permission to access this shop."
            )

        selected_services = ShopOwnerService.objects.filter(
            shop_id=shop_id,
            is_active=True
        ).select_related(
            "general_service__main_category",
            "shop"
        ).order_by("display_order", "id")

        serializer = ShopOwnerServiceSerializer(selected_services, many=True)
        return self.success_response(data=serializer.data, message="Services retrieved successfully.")
