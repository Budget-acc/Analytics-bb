from booking_v2.models import Client
from django.utils.timezone import now
from rest_framework.exceptions import ValidationError, ParseError
from drf_spectacular.utils import OpenApiResponse, OpenApiExample, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V3"],
    summary="Create client",
    description=(
        "Creates a new client under the active shop.\n\n"
        "Purpose: Register a client before creating or managing bookings.\n\n"
        "Context: Supports duplicate checks and centralized phone normalization."
    ),
    request=inline_serializer(
        name="ClientCreateRequestV3",
        fields={
            "name": serializers.CharField(
                help_text=(
                    "Purpose: Stores display name of the client. "
                    "Location: request body. Format/Type: string. Required."
                )
            ),
            "phone_1": serializers.CharField(
                help_text=(
                    "Purpose: Primary contact number used for duplicate checks and communication. "
                    "Location: request body. Format/Type: phone string (normalized internally). Required."
                )
            ),
            "phone_2": serializers.CharField(
                required=False,
                allow_blank=True,
                help_text=(
                    "Purpose: Optional secondary contact number. "
                    "Location: request body. Format/Type: phone string (normalized internally). Optional."
                )
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success response with created client payload."),
        400: OpenApiResponse(
            description=(
                "Validation errors exact messages include: Invalid JSON format in request body.; "
                "Client name and phone are required.; Primary phone number is required.; "
                "Client with the same name and phone already exists."
            )
        ),
        500: OpenApiResponse(description="Server errors exact messages: Validation failed.; Failed to create client."),
    },
    examples=[
        OpenApiExample(
            "Create client request",
            value={"name": "Rahul Sharma", "phone_1": "+919876543210", "phone_2": "+919812345678"},
            request_only=True,
        )
    ],
)
def create(self, request):
    try:
        shop_id = self.shop_id

        try:
            data = request.data
        except ParseError as pe:
            return self.validation_error_response(
                errors={"json": [str(pe)]},
                message="Invalid JSON format in request body.",
                dev_message=str(pe)
            )

        name = request.data.get("name", "").strip().title()
        phone_1_raw = request.data.get("phone_1")
        phone_2_raw = request.data.get("phone_2")

        if not name:
            return self.validation_error_response(
                errors={"fields": ["Client name and phone_1 are required."]},
                message="Client name and phone are required."
            )

        if not phone_1_raw:
            return self.validation_error_response(
                errors={"phone_1": ["Primary phone number is required."]},
                message="Primary phone number is required."
            )

        phone_1, error = self._validate_and_convert_phone(phone_1_raw, "primary phone")
        if error:
            return self.validation_error_response(
                errors={"phone_1": [error]},
                message=error
            )

        # Validate and convert secondary phone (optional)
        phone_2 = None
        if phone_2_raw:
            phone_2, error = self._validate_and_convert_phone(phone_2_raw, "secondary phone")
            if error:
                return self.validation_error_response(
                    errors={"phone_2": [error]},
                    message=error
                )

        existing_client = Client.objects.filter(
            shop_id=shop_id,
            client_name=name,
            client_phone_1=phone_1
        ).first()

        if existing_client:
            return self.validation_error_response(
                errors={"duplicate": ["Client with the same name and phone already exists."]},
                message="Client with the same name and phone already exists.",
                dev_message={
                    "client": {
                        "id": existing_client.id,
                        "name": existing_client.client_name,
                        "phone_1": existing_client.client_phone_1,
                        "phone_2": existing_client.client_phone_2,
                    }
                }
            )

        client = Client.objects.create(
            shop_id=shop_id,
            client_name=name,
            client_phone_1=phone_1,
            client_phone_2=phone_2,
            created_at=now()
        )
        data = {
            "id": client.id,
            "name": client.client_name,
            "phone_1": client.client_phone_1,
            "phone_2": client.client_phone_2,
            "created_at": client.created_at
        }
        return self.success_response(
            data=data,
            message="Client created successfully."
        )

    except ValidationError as e:
        return self.error_response(
            message="Validation failed.",
            dev_message=e.message_dict
        )
    except Exception as e:
        logger.error(f"Failed to create client: {str(e)}", exc_info=True)
        return self.error_response(
            message="Failed to create client.",
            dev_message=str(e)
        )
