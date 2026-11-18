from booking_v2.models import Client
from ERP.utils.utils import validate_and_format_phone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V4"],
    summary="Update client",
    description=(
        "Partially updates a client's name and/or phone numbers within the active shop.\n\n"
        "Purpose: Edit client details while preserving E.164 phone validation and duplicate constraints.\n\n"
        "Context: Use from client edit forms or booking correction flows."
    ),
    request=inline_serializer(
        name="ClientPartialUpdateRequestV4",
        fields={
            "client_name": serializers.CharField(
                required=False,
                help_text="Purpose: Updates the client display name. Location: request body. Format/Type: string (title-cased internally). Optional."
            ),
            "phone_1_e164": serializers.CharField(
                required=False,
                help_text="Purpose: Updates the primary E.164 phone number. Location: request body. Format/Type: E.164 phone string (e.g. +919876543210). Optional."
            ),
            "phone_2_e164": serializers.CharField(
                required=False,
                allow_blank=True,
                allow_null=True,
                help_text="Purpose: Updates or clears the secondary phone number. Location: request body. Format/Type: E.164 phone string, or empty string to clear. Optional."
            ),
        },
    ),
    responses={
        200: OpenApiResponse(
            description="Success response with updated client payload (id, client_name, phone_1_e164, phone_2_e164)."
        ),
        400: OpenApiResponse(
            description=(
                "Validation errors. Exact messages (one per case):\n"
                "- Name cannot be empty.\n"
                "- Primary phone number is required.\n"
                "- Client with same name and phone already exists."
            )
        ),
        404: OpenApiResponse(
            description="Not found.\n\nExact message:\n- Client not found."
        ),
        500: OpenApiResponse(
            description="Server error.\n\nExact message:\n- Failed to update client."
        ),
    },
    examples=[
        OpenApiExample(
            "Update client request",
            value={"client_name": "Rahul Kumar", "phone_1_e164": "+919876543210"},
            request_only=True,
        )
    ],
)
def partial_update(self, request, pk=None):
    try:
        shop_id = self.shop_id
        client = Client.objects.get(id=pk, shop_id=shop_id)

        # Start with existing values
        name = client.client_name
        phone_1 = client.phone_1_e164
        phone_2 = client.phone_2_e164

        # Update name if provided
        if "client_name" in request.data:
            new_name = request.data.get("client_name", "").strip().title()
            if not new_name:
                return self.validation_error_response(
                    errors={"client_name": ["Name cannot be empty."]},
                    message="Name cannot be empty."
                )
            name = new_name

        # Update primary phone
        if "phone_1_e164" in request.data:
            phone_1_raw = request.data.get("phone_1_e164")

            if not phone_1_raw:
                return self.validation_error_response(
                    errors={"phone_1": ["Primary phone number is required."]},
                    message="Primary phone number is required."
                )

            validated_phone, error = validate_and_format_phone(
                phone_1_raw, "Primary phone"
            )
            if error:
                return self.validation_error_response(
                    errors={"phone_1_e164": [error]},
                    message=error
                )

            phone_1 = validated_phone

        # Update secondary phone
        if "phone_2_e164" in request.data:
            phone_2_raw = request.data.get("phone_2_e164")

            if phone_2_raw == "":
                phone_2 = None
            elif phone_2_raw:
                validated_phone, error = validate_and_format_phone(
                    phone_2_raw, "Secondary phone"
                )
                if error:
                    return self.validation_error_response(
                        errors={"phone_2_e164": [error]},
                        message=error
                    )
                phone_2 = validated_phone
            else:
                phone_2 = None

        # Duplicate check
        if Client.objects.filter(
            shop_id=shop_id,
            client_name=name,
            phone_1_e164=phone_1
        ).exclude(pk=pk).exists():
            return self.validation_error_response(
                errors={"duplicate": ["Client with same name and phone already exists."]},
                message="Client with same name and phone already exists."
            )

        # Save only changed fields
        update_fields = []

        if client.client_name != name:
            client.client_name = name
            update_fields.append("client_name")

        if client.phone_1_e164 != phone_1:
            client.phone_1_e164 = phone_1
            update_fields.append("phone_1_e164")

        if client.phone_2_e164 != phone_2:
            client.phone_2_e164 = phone_2
            update_fields.append("phone_2_e164")

        if update_fields:
            client.save(update_fields=update_fields)

        return self.success_response(
            data={
                "id": client.id,
                "client_name": client.client_name,
                "phone_1_e164": str(client.phone_1_e164),
                "phone_2_e164": str(client.phone_2_e164) if client.phone_2_e164 else None,
                "created_at": client.created_at
            },
            message="Client updated successfully."
        )

    except Client.DoesNotExist:
        return self.not_found_response(
            resource="Client",
            message="Client not found."
        )
    except Exception as e:
        logger.error(f"Failed to update client: {str(e)}", exc_info=True)
        return self.error_response(
            message="Failed to update client.",
            dev_message=str(e)
        )
