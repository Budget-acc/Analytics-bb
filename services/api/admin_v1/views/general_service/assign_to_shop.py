from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from services.models import GeneralService, ShopOwnerService
from shop.models import Shop


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="Assign services to shop",
    description="Assigns a list of general services to a target shop, returning assigned and skipped service names.",
    request=inline_serializer(
        name="ServiceAdminAssignToShopRequest",
        fields={
            "shop_id": serializers.IntegerField(required=True),
            "service_ids": serializers.ListField(required=True, child=serializers.IntegerField(), help_text="List of general service ids."),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Service assignment completed."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Both 'shop_id' and 'service_ids' (as a list) are required.\n"
                "- Shop with ID {shop_id} does not exist.\n"
                "- Generic exception message in error field."
            )
        ),
        500: OpenApiResponse(description="Error response body contains exact field: error = exception string."),
    },
    examples=[
        OpenApiExample("Request example", value={"shop_id": 2, "service_ids": [1, 3, 5]}, request_only=True),
        OpenApiExample("Success response", value={"message": "Service assignment completed.", "assigned": ["Tailoring"], "skipped": ["Dry Clean"]}, response_only=True, status_codes=["200"]),
        OpenApiExample("Validation response", value={"error": "Both 'shop_id' and 'service_ids' (as a list) are required."}, response_only=True, status_codes=["400"]),
    ],
)
@action(detail=False, methods=["post"], url_path="assign-to-shop")
def assign_to_shop(self, request):
    try:
        shop_id = request.data.get("shop_id")
        service_ids = request.data.get("service_ids", [])

        if not shop_id or not isinstance(service_ids, list):
            return Response({
                "error": "Both 'shop_id' and 'service_ids' (as a list) are required."
            }, status=400)

        try:
            shop = Shop.objects.get(id=shop_id)
        except Shop.DoesNotExist:
            return Response({"error": f"Shop with ID {shop_id} does not exist."}, status=400)

        created_services = []
        skipped_services = []

        for sid in service_ids:
            try:
                service = GeneralService.objects.get(id=sid)
                obj, created = ShopOwnerService.objects.get_or_create(
                    shop=shop, general_service=service
                )
                if created:
                    created_services.append(service.name)
                else:
                    skipped_services.append(service.name)
            except GeneralService.DoesNotExist:
                skipped_services.append(f"Service ID {sid} (Not Found)")

        return Response({
            "message": "Service assignment completed.",
            "assigned": created_services,
            "skipped": skipped_services
        }, status=200)

    except Exception as e:
        return Response({"error": str(e)}, status=500)
