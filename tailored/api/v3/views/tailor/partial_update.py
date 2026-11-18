from tailored.models import Tailor
from tailored.api.v3.serializers import TailorSerializer
from shop.utils.phone_validator import validate_and_normalize_phone
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Update tailor member",
    description="Partially updates a tailor member for the active shop.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Tailor id.")],
    request=inline_serializer(
        name="CustomWorkV3PatchTailorRequest",
        fields={
            "name": serializers.CharField(required=False, help_text="Updated tailor member name."),
            "phone": serializers.CharField(required=False, help_text="Updated phone number."),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact success message: Tailor member updated successfully."),
        400: OpenApiResponse(
            description=(
                "Exact error messages:\n"
                "- phone validation message from validate_and_normalize_phone\n"
                "- This phone number is already registered for this shop.\n"
                "- Name cannot be empty.\n"
                "- Name must be at least 2 characters long.\n"
                "- Invalid tailor data."
            )
        ),
        404: OpenApiResponse(description="Not found response for Tailor."),
    },
    examples=[
        OpenApiExample("Update request", value={"phone": "+919876543211"}, request_only=True),
        OpenApiExample("Update success", value={"status": "success", "message": "Tailor member updated successfully.", "data": {"id": 5, "name": "Rahul Tailor", "phone": "+919876543211", "is_deleted": False}}, response_only=True, status_codes=["200"]),
    ],
)
def partial_update(self, request, pk=None):
    try:
        tailor = Tailor.objects.get(pk=pk, shop_id=self.shop_id)
    except Tailor.DoesNotExist:
        return self.not_found_response(resource="Tailor")

    data = request.data.copy()

    # ---------- PHONE VALIDATION ----------
    if "phone" in data:
        raw_phone = data.get("phone")

        phone, error = validate_and_normalize_phone(
            raw_phone,
            raise_exception=False
        )

        if error:
            return self.validation_error_response(
                errors=None,
                message=error,
                dev_message=f"Phone validation failed: {raw_phone}"
            )

        data["phone"] = phone

    serializer = TailorSerializer(
        tailor,
        data=data,
        partial=True,
        context={"shop_id": self.shop_id}
    )

    if not serializer.is_valid():
        phone_errors = serializer.errors.get("phone")
        if phone_errors:
            return self.validation_error_response(
                errors=None,
                message=phone_errors[0]
            )

        name_errors = serializer.errors.get("name")
        if name_errors:
            return self.validation_error_response(
                errors=None,
                message=name_errors[0]
            )

        return self.validation_error_response(
            errors=None,
            message="Invalid tailor data."
        )

    updated_tailor = serializer.save()

    return self.success_response(
        data=TailorSerializer(updated_tailor).data,
        message="Tailor member updated successfully."
    )
