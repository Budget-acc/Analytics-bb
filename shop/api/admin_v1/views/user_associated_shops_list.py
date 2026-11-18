from rest_framework.views import APIView
from rest_framework.response import Response
from authentication.permissions import IsAdminUserOnly
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework_simplejwt.authentication import JWTAuthentication
from shop.models import ShopUser
from authentication.models import User
from django.utils.timezone import localtime
import logging

logger = logging.getLogger("api_requests")


class UserAssociatedShopsListView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUserOnly]

    @extend_schema(
        tags=["Shop Admin Management V1"],
        summary="List shops associated with user",
        description="Returns active shops linked to a specific user id.",
        parameters=[OpenApiParameter("user_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True, description="User id."),
        ],
        responses={
            200: OpenApiResponse(description="Response with shops array."),
            400: OpenApiResponse(description="Exact message: Missing 'user_id' query parameter."),
            404: OpenApiResponse(description="Exact message: User not found."),
            500: OpenApiResponse(description="Exact message: An error occurred processing your request."),
        },
        examples=[
            OpenApiExample("Success response", value={"shops": [{"shop_id": 4, "shop_name": "Prime Tailors", "place": "Kochi", "role": "OWNER", "linked_on": "04-05-2026"}]}, response_only=True, status_codes=["200"]),
            OpenApiExample("Missing user id", value={"error": "Missing 'user_id' query parameter."}, response_only=True, status_codes=["400"]),
        ],
    )
    def get(self, request):
        try:
            user_id = request.query_params.get("user_id")

            if not user_id:
                return Response({"error": "Missing 'user_id' query parameter."}, status=400)

            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                return Response({"error": "User not found."}, status=404)

            shop_links = ShopUser.objects.filter(user=user, is_active=True).select_related("shop")

            shops_data = [
                {
                    "shop_id": su.shop.id,
                    "shop_name": su.shop.name,
                    "place": su.shop.place,
                    "role": su.role,
                    "linked_on": localtime(su.created_at).strftime("%d-%m-%Y"),
                }
                for su in shop_links
            ]

            return Response({"shops": shops_data}, status=200)

        except Exception as e:
            logger.exception("Error in UserAssociatedShopsListView.get")
            return Response({"error": "An error occurred processing your request."}, status=500)
