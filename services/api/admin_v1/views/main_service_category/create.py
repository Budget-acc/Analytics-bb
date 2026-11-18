from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from services.api.admin_v1.serializers import MainServiceCategorySerializer
from services.api.v3.utils.s3 import upload_image_to_s3


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="Create main service category",
    description="Creates a new main service category and optionally uploads icon to S3.",
    request=inline_serializer(
        name="ServiceAdminMainServiceCreateRequest",
        fields={
            "name": serializers.CharField(required=True),
            "is_active": serializers.BooleanField(required=False, default=True),
            "icon": serializers.ImageField(required=False),
        },
    ),
    responses={
        201: OpenApiResponse(description="Exact message: Main service category created successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages can include:\n"
                "- No image file provided.\n"
                "- Image must be less than 3MB.\n"
                "- Invalid or unsupported image format.\n"
                "- A category with this name already exists. Please use a different name.\n"
                "- Validation errors in error field."
            )
        ),
        500: OpenApiResponse(description="Exact message: An unexpected error occurred. Please try again later."),
    },
    examples=[
        OpenApiExample("Request example", value={"name": "Clothing", "is_active": True}, request_only=True),
        OpenApiExample("Success response", value={"message": "Main service category created successfully.", "data": {"id": 4, "name": "Clothing", "is_active": True}}, response_only=True, status_codes=["201"]),
        OpenApiExample("Duplicate response", value={"message": "A category with this name already exists. Please use a different name."}, response_only=True, status_codes=["400"]),
    ],
)
def create(self, request):
    try:
        data = request.data.copy()

        # Handle icon upload if present
        icon_file = request.FILES.get("icon")
        icon_url = None

        if icon_file:
            category_name = data.get("name", "main_service").replace(" ", "_")
            path_prefix = f"Main_services/{category_name}"
            try:
                icon_url = upload_image_to_s3(icon_file, path_prefix, max_size=(100, 100))
            except ValueError as ve:
                return Response({"error": str(ve)}, status=400)

        if icon_url:
            data["icon"] = icon_url

        # Create serializer instance
        serializer = MainServiceCategorySerializer(data=data)

        # Validate and save if valid
        if serializer.is_valid():
            serializer.save()
            return Response({
                "message": "Main service category created successfully.",
                "data": serializer.data
            }, status=status.HTTP_201_CREATED)

        # Check for name duplication error and return custom message
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
