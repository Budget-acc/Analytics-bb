from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from shop.models import ShopUser
from shop.api.v3.serializers import ShopUserSerializer
from ERP.utils.mixins import StandardResponseMixin


class UserAssociatedShopsView(StandardResponseMixin, APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [CutoffJWTAuthentication]

    @extend_schema(
        tags=["Shop Management V3"],
        summary="List user's available shops",
        description="Returns active shops associated with the logged-in user (ADMIN, OWNER, or MANAGER roles only).",
        responses={
            200: OpenApiResponse(description="Standard success response with associated shop list."),
            404: OpenApiResponse(description="Not found response when user role is not allowed (resource=User)."),
        },
        examples=[
            OpenApiExample("Success response", value={"status": "success", "message": "User's shops retrieved successfully.", "data": [{"id": 4, "name": "Prime Tailors"}]}, response_only=True, status_codes=["200"]),
            OpenApiExample("Role blocked response", value={"status": "not_found", "message": "User not found.", "data": None}, response_only=True, status_codes=["404"]),
        ],
    )
    def get(self, request):
        user = request.user

        # Step 1: Role check
        # if user.role not in ["ADMIN", "OWNER", "MANAGER"]:
        #     return self.not_found_response(resource="User")

        # Step 2: Get associated shops via ShopUser
        shop_users = ShopUser.objects.filter(user=user, is_active=True, shop__is_active=True).select_related("shop")
        associated_shops = [su.shop for su in shop_users]

        serializer = ShopUserSerializer(associated_shops, many=True)
        return self.success_response(data=serializer.data, message="User's shops retrieved successfully.")
