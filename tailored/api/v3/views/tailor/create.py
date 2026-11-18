from tailored.models import Tailor
from tailored.api.v3.serializers import TailorSerializer
from shop.utils.phone_validator import validate_and_normalize_phone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Create tailor member",
    description="Creates a tailor member for the active shop.",
    request=inline_serializer(
        name="CustomWorkV3CreateTailorRequest",
        fields={
            "name": serializers.CharField(required=True, help_text="Tailor member name."),
            "phone": serializers.CharField(required=True, help_text="Phone number to normalize and store uniquely per shop."),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact success message: Tailor member created successfully."),
        400: OpenApiResponse(
            description=(
                "Exact error messages:\n"
                "- Missing required fields.\n"
                "- Name is required.\n"
                "- Phone number is required.\n"
                "- phone validation message from validate_and_normalize_phone\n"
                "- This phone number is already registered for this shop.\n"
                "- Name cannot be empty.\n"
                "- Name must be at least 2 characters long.\n"
                "- Invalid input data."
            )
        ),
        500: OpenApiResponse(description="Exact error message: An unexpected error occurred while creating tailor."),
    },
    examples=[
        OpenApiExample("Create request", value={"name": "Rahul Tailor", "phone": "+91 9876543210"}, request_only=True),
        OpenApiExample("Create success", value={"status": "success", "message": "Tailor member created successfully.", "data": {"id": 5, "name": "Rahul Tailor", "phone": "+919876543210", "is_deleted": False}}, response_only=True, status_codes=["200"]),
    ],
)
def create(self, request):
    try:
        name = request.data.get("name")
        raw_phone = request.data.get("phone")

        # Required fields
        if not name or not raw_phone:
            errors = {}
            if not name:
                errors["name"] = ["Name is required."]
            if not raw_phone:
                errors["phone"] = ["Phone number is required."]

            return self.validation_error_response(
                errors=errors,
                message="Missing required fields."
            )

        # PHONE FORMAT VALIDATION
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

        # Inject normalized phone
        data = request.data.copy()
        data["phone"] = phone

        serializer = TailorSerializer(
            data=data,
            context={"shop_id": self.shop_id}
        )

        if not serializer.is_valid():
            # Duplicate phone
            phone_errors = serializer.errors.get("phone")
            if phone_errors:
                return self.validation_error_response(
                    errors=None,
                    message=phone_errors[0],
                    dev_message=f"Duplicate phone attempted: {phone}"
                )

            # Name error
            name_errors = serializer.errors.get("name")
            if name_errors:
                return self.validation_error_response(
                    errors=None,
                    message=name_errors[0]
                )

            return self.validation_error_response(
                errors=None,
                message="Invalid input data.",
                dev_message=str(serializer.errors)
            )

        tailor = serializer.save(shop_id=self.shop_id)

        return self.success_response(
            data=TailorSerializer(tailor).data,
            message="Tailor member created successfully."
        )

    except Exception as e:
        logger.error(f"Error creating tailor: {str(e)}", exc_info=True)
        return self.error_response(
            message="An unexpected error occurred while creating tailor.",
            dev_message=str(e)
        )
