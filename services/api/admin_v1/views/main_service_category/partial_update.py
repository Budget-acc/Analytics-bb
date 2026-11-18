from rest_framework import status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from services.models import MainServiceCategory
from services.api.admin_v1.serializers import MainServiceCategorySerializer
from services.api.v3.utils.s3 import upload_image_to_s3, delete_s3_file


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="Update main service category",
    description="Partially updates main service category fields with optional icon replacement.",
    parameters=[OpenApiParameter(name="id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Main service category id.")],
    request=inline_serializer(
        name="ServiceAdminMainServicePatchRequest",
        fields={
            "name": serializers.CharField(required=False),
            "is_active": serializers.BooleanField(required=False),
            "icon": serializers.ImageField(required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Main service category updated successfully."),
        400: OpenApiResponse(description="Validation or S3/image errors in error or message fields, including duplicate-name message."),
        500: OpenApiResponse(description="Exact message: An unexpected error occurred. Please try again later."),
    },
    examples=[
        OpenApiExample("Request example", value={"name": "Updated Category", "is_active": True}, request_only=True),
        OpenApiExample("Success response", value={"message": "Main service category updated successfully.", "data": {"id": 4, "name": "Updated Category", "is_active": True}}, response_only=True, status_codes=["200"]),
        OpenApiExample("Duplicate response", value={"message": "A category with this name already exists. Please use a different name."}, response_only=True, status_codes=["400"]),
    ],
)
def partial_update(self, request, pk=None):
    try:
        instance = get_object_or_404(MainServiceCategory, pk=pk)
        data = request.data.copy()

        # Handle icon update
        icon_file = request.FILES.get("icon")
        if icon_file:
            category_name = data.get("name", instance.name).replace(" ", "_")
            path_prefix = f"Main_services/{category_name}"
            try:
                new_icon_url = upload_image_to_s3(icon_file, path_prefix, max_size=(100, 100))

                # Delete old icon if it exists
                if instance.icon:
                    delete_s3_file(instance.icon)

                data["icon"] = new_icon_url
            except ValueError as ve:
                return Response({"error": str(ve)}, status=400)

        # Serialize with partial update
        serializer = MainServiceCategorySerializer(instance, data=data, partial=True)

        if serializer.is_valid():
            updated = serializer.save()
            return Response({
                "message": "Main service category updated successfully.",
                "data": MainServiceCategorySerializer(updated).data
            }, status=status.HTTP_200_OK)

        # Handle uniqueness error for name
        errors = serializer.errors
        if "name" in errors:
            name_errors = errors["name"]
            if any("already exists" in str(err) for err in name_errors):
                return Response({
                    "message": "A category with this name already exists. Please use a different name."
                }, status=status.HTTP_400_BAD_REQUEST)

        # Other validation errors
        return Response({"error": errors}, status=status.HTTP_400_BAD_REQUEST)

    except Exception as e:
        return Response({
            "message": "An unexpected error occurred. Please try again later.",
            "details": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
