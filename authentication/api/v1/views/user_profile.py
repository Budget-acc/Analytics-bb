from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from authentication.models import User
from authentication.api.v1.serializers import UserProfileSerializer
from shop.api.admin_v1.utils.shop import get_active_shop_id
from shop.models import Shop, ShopUser
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers


class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    @extend_schema(
        tags=["User Management V1"],
        summary="Get user profile",
        description=(
            "Returns the authenticated user's profile along with the active shop details.\n\n"
            "Purpose: Provides profile data required by user-facing screens after login.\n\n"
            "Context: User is identified from JWT token; active shop is resolved from request context."
        ),
        responses={
            200: inline_serializer(
                name="UserProfileSuccessV1",
                fields={
                    "user": inline_serializer(
                        name="UserProfilePayloadV1",
                        fields={
                            "id": serializers.IntegerField(help_text="Authenticated user ID."),
                            "phone": serializers.CharField(help_text="User phone in normalized format."),
                            "full_name": serializers.CharField(help_text="User full name."),
                            "last_name": serializers.CharField(allow_null=True, help_text="User last name, if available."),
                            "email": serializers.EmailField(allow_null=True, help_text="User email, if available."),
                            "role": serializers.CharField(help_text="User role."),
                            "shop_id": serializers.IntegerField(help_text="Active shop ID."),
                            "shop_name": serializers.CharField(help_text="Active shop name."),
                            "shop_phone": serializers.CharField(allow_null=True, help_text="Active shop phone."),
                            "shop_address": serializers.CharField(allow_null=True, help_text="Active shop address."),
                            "gst_number": serializers.CharField(allow_null=True, help_text="Active shop GST number."),
                            "shop_image": serializers.CharField(allow_null=True, help_text="Shop image URL/path."),
                            "terms_and_conditions": serializers.CharField(allow_null=True, help_text="Shop terms and conditions text."),
                            "multiple_shops": serializers.BooleanField(help_text="True when user is associated with more than one shop."),
                        },
                    )
                },
            ),
            404: inline_serializer(
                name="UserProfileNotFoundV1",
                fields={
                    "error": serializers.CharField(help_text="User not found or shop not found.")
                },
            ),
        },
    )
    def get(self, request):
        try:
            user = User.objects.get(id=request.user.id)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

        # Get active shop id
        try:
            shop_id = get_active_shop_id(request)
            shop = Shop.objects.get(id=shop_id)
        except Shop.DoesNotExist:
            return Response({"error": "Shop not found"}, status=status.HTTP_404_NOT_FOUND)

        associated_shops_count = ShopUser.objects.filter(user=user).count()
        has_multiple_shops = associated_shops_count > 1

        # Serialize user
        user_data = UserProfileSerializer(user).data

        # Merge shop details into user_data
        user_data.update({
            "shop_id": shop.id,
            "shop_name": shop.name,
            "shop_phone": shop.phone,
            "shop_address": shop.address,
            "gst_number": shop.gst_number,
            "shop_image": shop.img,
            "terms_and_conditions": shop.terms_and_conditions,
            "multiple_shops":has_multiple_shops,
        })

        return Response({"user": user_data}, status=status.HTTP_200_OK)
