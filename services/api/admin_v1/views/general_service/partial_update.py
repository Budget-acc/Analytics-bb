from rest_framework import status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from services.models import GeneralService
from services.api.admin_v1.serializers import GeneralServiceSerializer
from services.api.v3.utils.s3 import upload_image_to_s3, delete_s3_file


@extend_schema(
    tags=["Service Admin Management V1"],
    summary="Update general service",
    description="Partially updates a general service including optional icon replacement and old icon cleanup.",
    parameters=[
        OpenApiParameter(name="id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="General service id."),
    ],
    request=inline_serializer(
        name="ServiceAdminGeneralServicePatchRequest",
        fields={
            "name": serializers.CharField(required=False),
            "description": serializers.CharField(required=False, allow_blank=True),
            "main_category": serializers.IntegerField(required=False),
            "is_active": serializers.BooleanField(required=False),
            "icon": serializers.ImageField(required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: General service updated successfully."),
        400: OpenApiResponse(description="Exact or propagated errors in error field, including S3 validation messages and serializer validation errors."),
        500: OpenApiResponse(description="Error response body contains exact field: error = exception string."),
    },
    examples=[
        OpenApiExample("Request example", value={"name": "Alteration", "is_active": True}, request_only=True),
        OpenApiExample("Success response", value={"message": "General service updated successfully.", "data": {"id": 5, "name": "Alteration", "is_active": True}}, response_only=True, status_codes=["200"]),
        OpenApiExample("Validation response", value={"error": {"name": ["This field may not be blank."]}}, response_only=True, status_codes=["400"]),
    ],
)
def partial_update(self, request, pk=None):
    try:
        instance = get_object_or_404(GeneralService, pk=pk)
        data = request.data.copy()

        # Handle icon update
        new_icon_file = request.FILES.get("icon")
        if new_icon_file:
            service_name = data.get("name", instance.name).replace(" ", "_")
            path_prefix = f"Sub_services/{service_name}"

            try:
                new_icon_url = upload_image_to_s3(new_icon_file, path_prefix)

                # Delete old icon if it exists
                if instance.icon:
                    delete_s3_file(instance.icon)

                data["icon"] = new_icon_url
            except ValueError as ve:
                return Response({"error": str(ve)}, status=400)

        serializer = GeneralServiceSerializer(instance, data=data, partial=True)
        if serializer.is_valid():
            updated_instance = serializer.save()
            return Response({
                "message": "General service updated successfully.",
                "data": GeneralServiceSerializer(updated_instance).data
            }, status=status.HTTP_200_OK)

        return Response(
            {"error": serializer.errors}, status=status.HTTP_400_BAD_REQUEST
        )

    except Exception as e:
        return Response(
            {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
