from rest_framework import status
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from services.api.admin_v1.serializers import GeneralServiceSerializer
from services.api.v3.utils.s3 import upload_image_to_s3


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="Create general service",
    description="Creates a new general service category, optionally uploading icon to S3.",
    request=inline_serializer(
        name="ServiceAdminGeneralServiceCreateRequest",
        fields={
            "name": serializers.CharField(required=True, help_text="General service name."),
            "description": serializers.CharField(required=False, allow_blank=True),
            "main_category": serializers.IntegerField(required=False, help_text="Main service category id."),
            "is_active": serializers.BooleanField(required=False, default=True),
            "icon": serializers.ImageField(required=False, help_text="Optional image file under 3MB."),
        },
    ),
    responses={
        201: OpenApiResponse(description="Exact message: General service created successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages can include:\n"
                "- No image file provided.\n"
                "- Image must be less than 3MB.\n"
                "- Invalid or unsupported image format.\n"
                "- A category with this name already exists. Please use a different name.\n"
                "- Serializer validation errors returned in error field."
            )
        ),
        500: OpenApiResponse(description="Exact message: An unexpected error occurred. Please try again later."),
    },
    examples=[
        OpenApiExample("Request example", value={"name": "Tailoring", "description": "Tailoring services", "main_category": 1, "is_active": True}, request_only=True),
        OpenApiExample("Success response", value={"message": "General service created successfully.", "data": {"id": 5, "name": "Tailoring", "description": "Tailoring services", "is_active": True}}, response_only=True, status_codes=["201"]),
        OpenApiExample("Duplicate response", value={"error": "A category with this name already exists. Please use a different name."}, response_only=True, status_codes=["400"]),
        OpenApiExample("Server error response", value={"error": "An unexpected error occurred. Please try again later."}, response_only=True, status_codes=["500"]),
    ],
)
def create(self, request):
    try:
        data = request.data.copy()

        if "is_active" not in data:
            data["is_active"] = True

        # Upload icon if present
        icon_file = request.FILES.get("icon")
        icon_url = None

        if icon_file:
            service_name = data.get("name", "service").replace(" ", "_")
            path_prefix = f"Sub_services/{service_name}"
            try:
                icon_url = upload_image_to_s3(icon_file, path_prefix)
            except ValueError as e:
                return Response({"error": str(e)}, status=400)

        # Inject icon URL if uploaded
        if icon_url:
            data["icon"] = icon_url

        serializer = GeneralServiceSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response({
                "message": "General service created successfully.",
                "data": serializer.data
            }, status=status.HTTP_201_CREATED)

        return Response({"error": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    except Exception as e:
        if "unique_generalservice_name_ci" in str(e):
            return Response({
                "error": "A category with this name already exists. Please use a different name."
            }, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "error": "An unexpected error occurred. Please try again later."
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
