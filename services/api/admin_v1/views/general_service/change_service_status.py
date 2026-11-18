from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from services.models import ShopOwnerService


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="Change shop service status",
    description="Toggles active status of a shop-owner service record.",
    request=inline_serializer(
        name="ServiceAdminChangeServiceStatusRequest",
        fields={
            "shop_service_id": serializers.IntegerField(required=True),
            "is_active": serializers.CharField(required=True, help_text="Boolean or 'true'/'false' string."),
        },
    ),
    responses={
        200: OpenApiResponse(description="Returns updated_service object."),
        400: OpenApiResponse(
            description=(
                "Exact messages:\n"
                "- Both 'shop_service_id' and 'is_active' are required.\n"
                "- Invalid value for 'is_active'. Use true or false.\n"
                "- Invalid type for 'is_active'. Must be boolean or 'true'/'false' string.\n"
                "- Generic exception string in error field."
            )
        ),
        404: OpenApiResponse(description="Exact message: ShopOwnerService not found."),
        500: OpenApiResponse(description="Error response body contains exact field: error = exception string."),
    },
    examples=[
        OpenApiExample("Request example", value={"shop_service_id": 10, "is_active": "true"}, request_only=True),
        OpenApiExample("Success response", value={"updated_service": {"id": 10, "name": "Tailoring", "description": "...", "icon": "https://...", "main_service": "Clothing", "is_active": True, "created_at": "2026-05-04T10:20:00Z"}}, response_only=True, status_codes=["200"]),
        OpenApiExample("Invalid value response", value={"error": "Invalid value for 'is_active'. Use true or false."}, response_only=True, status_codes=["400"]),
    ],
)
@action(detail=False, methods=["post"], url_path="shop/change-service-status")
def change_service_status(self, request):
    try:
        shop_service_id = request.data.get("shop_service_id")
        is_active = request.data.get("is_active")

        # Validation
        if shop_service_id is None or is_active is None:
            return Response({
                "error": "Both 'shop_service_id' and 'is_active' are required."
            }, status=400)

        try:
            service_obj = ShopOwnerService.objects.select_related(
                "general_service", "general_service__main_category"
            ).get(id=shop_service_id)
        except ShopOwnerService.DoesNotExist:
            return Response({"error": "ShopOwnerService not found."}, status=404)

        if isinstance(is_active, str):
            if is_active.lower() == "true":
                is_active = True
            elif is_active.lower() == "false":
                is_active = False
            else:
                return Response({"error": "Invalid value for 'is_active'. Use true or false."}, status=400)
        elif isinstance(is_active, bool):
            pass
        else:
            return Response({"error": "Invalid type for 'is_active'. Must be boolean or 'true'/'false' string."}, status=400)

        service_obj.is_active = is_active
        service_obj.save()

        response_data = {
            "id": service_obj.id,
            "name": service_obj.general_service.name,
            "description": service_obj.general_service.description,
            "icon": service_obj.general_service.icon,
            "main_service": service_obj.general_service.main_category.name if service_obj.general_service.main_category else None,
            "is_active": service_obj.is_active,
            "created_at": service_obj.created_at
        }

        return Response({"updated_service": response_data}, status=200)

    except Exception as e:
        return Response({"error": str(e)}, status=500)
