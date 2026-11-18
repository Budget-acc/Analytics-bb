from django.utils.timezone import now
from rest_framework.exceptions import ValidationError, ParseError
from booking_v2.models import Client
from ERP.utils.utils import validate_and_format_phone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V4"],
    summary="Create client",
    description=(
        "Creates a new client under the active shop with E.164 phone number validation.\n\n"
        "Purpose: Register a client before creating or managing bookings.\n\n"
        "Context: Duplicate check is applied on (name + phone_1_e164) combination. "
        "Phone numbers are validated and normalized to E.164 format."
    ),
    request=inline_serializer(
        name="ClientCreateRequestV4",
        fields={
            "client_name": serializers.CharField(
                help_text="Purpose: Display name of the client. Location: request body. Format/Type: string (title-cased internally). Required."
            ),
            "phone_1_e164": serializers.CharField(
                help_text="Purpose: Primary contact number used for duplicate checks and communication. Location: request body. Format/Type: E.164 phone string (e.g. +919876543210). Required."
            ),
            "phone_2_e164": serializers.CharField(
                required=False,
                allow_blank=True,
                help_text="Purpose: Optional secondary contact number. Location: request body. Format/Type: E.164 phone string. Optional."
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success response with created client payload (id, client_name, phone_1_e164, phone_2_e164, created_at)."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Exact messages (one per case):\n"
                "- Invalid JSON format in request body.\n"
                "- Client name and phone are required.\n"
                "- Primary phone number is required.\n"
                "- Client with the same name and phone already exists."
            )
        ),
        500: OpenApiResponse(
            description="Server errors. Exact messages (one per case):\n- Validation failed.\n- Failed to create client."
        ),
    },
    examples=[
        OpenApiExample(
            "Create client request",
            value={"client_name": "Rahul Sharma", "phone_1_e164": "+919876543210", "phone_2_e164": "+919812345678"},
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

        client_name = request.data.get("client_name", "").strip().title()
        phone_1_raw = request.data.get("phone_1_e164")
        phone_2_raw = request.data.get("phone_2_e164")

        if not client_name:
            return self.validation_error_response(
                errors={"fields": ["Client name and phone_1 are required."]},
                message="Client name and phone are required."
            )

        if not phone_1_raw:
            return self.validation_error_response(
                errors={"phone_1": ["Primary phone number is required."]},
                message="Primary phone number is required."
            )

        # Validate primary phone
        phone_1, error = validate_and_format_phone(phone_1_raw, "Primary phone")
        if error:
            return self.validation_error_response(
                errors={"phone_1": [error]},
                message=error
            )

        # Validate secondary phone (optional)
        phone_2 = None
        if phone_2_raw:
            phone_2, error = validate_and_format_phone(phone_2_raw, "Secondary phone")
            if error:
                return self.validation_error_response(
                    errors={"phone_2": [error]},
                    message=error
                )

        existing_client = Client.objects.filter(
            shop_id=shop_id,
            client_name=client_name,
            phone_1_e164=phone_1
        ).first()

        if existing_client:
            return self.validation_error_response(
                errors={"duplicate": ["Client with the same name and phone already exists."]},
                message="Client with the same name and phone already exists.",
                dev_message={
                    "client": {
                        "id": existing_client.id,
                        "client_name": existing_client.client_name,
                        "phone_1_e164": str(existing_client.phone_1_e164) if existing_client.phone_1_e164 else None,
                        "phone_2_e164": str(existing_client.phone_2_e164) if existing_client.phone_2_e164 else None,
                    }
                }
            )

        client = Client.objects.create(
            shop_id=shop_id,
            client_name=client_name,
            phone_1_e164=phone_1,
            phone_2_e164=phone_2,
            created_at=now()
        )
        data = {
            "id": client.id,
            "client_name": client.client_name,
            "phone_1_e164": str(client.phone_1_e164) if client.phone_1_e164 else None,
            "phone_2_e164": str(client.phone_2_e164) if client.phone_2_e164 else None,
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
