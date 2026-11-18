from booking_v2.models import Client
from rest_framework.exceptions import ValidationError
from drf_spectacular.utils import OpenApiResponse, OpenApiExample, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Booking Management V3"],
    summary="Update client",
    description=(
        "Partially updates client data for the active shop.\n\n"
        "Purpose: Edit name and phone details while preserving duplicate constraints.\n\n"
        "Context: Use from client edit forms and booking correction flows."
    ),
    request=inline_serializer(
        name="ClientPartialUpdateRequestV3",
        fields={
            "name": serializers.CharField(
                required=False,
                help_text="Purpose: Updates client display name. Location: request body. Format/Type: string. Optional."
            ),
            "phone_1": serializers.CharField(
                required=False,
                help_text="Purpose: Updates primary phone number. Location: request body. Format/Type: phone string. Optional."
            ),
            "phone_2": serializers.CharField(
                required=False,
                allow_blank=True,
                allow_null=True,
                help_text="Purpose: Updates/clears secondary phone number. Location: request body. Format/Type: phone string or empty. Optional."
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success response with updated client payload."),
        400: OpenApiResponse(
            description=(
                "Validation errors exact messages include: Name and phone are required.; "
                "Client with the same name and phone already exists."
            )
        ),
        404: OpenApiResponse(description="Not found exact message: Client not found."),
        500: OpenApiResponse(description="Server errors exact messages: Validation failed.; Failed to update client."),
    },
    examples=[
        OpenApiExample(
            "Update client request",
            value={"name": "Rahul Kumar", "phone_1": "+919876543210"},
            request_only=True,
        )
    ],
)
def partial_update(self, request, pk=None):
    try:
        shop_id = self.shop_id

        client = Client.objects.get(pk=pk, shop=shop_id)

        name = request.data.get("name", client.client_name).strip().title()
        phone_1_raw = request.data.get("phone_1", client.client_phone_1)
        phone_2_raw = request.data.get("phone_2")

        if not name:
            return self.validation_error_response(
                errors={"fields": ["Name and phone_1 are required."]},
                message="Name and phone are required."
            )

        if phone_1_raw != client.client_phone_1:  # Only validate if changed
            phone_1, error = self._validate_and_convert_phone(phone_1_raw, "primary phone")
            if error:
                return self.validation_error_response(
                    errors={"phone_1": [error]},
                    message=error
                )
        else:
            phone_1 = client.client_phone_1

        if phone_2_raw is not None:  # Explicitly checking for updates
            if phone_2_raw:
                phone_2, error = self._validate_and_convert_phone(phone_2_raw, "secondary phone")
                if error:
                    return self.validation_error_response(
                        errors={"phone_2": [error]},
                        message=error
                    )
            else:
                phone_2 = None
        else:
            phone_2 = client.client_phone_2

        duplicate_client = Client.objects.filter(
            shop_id=shop_id,
            client_name=name,
            client_phone_1=phone_1
        ).exclude(pk=pk).first()
        if duplicate_client:
            return self.validation_error_response(
                errors={"duplicate": ["Client with the same name and phone already exists."]},
                message="Client with the same name and phone already exists.",
                dev_message={
                    "client": {
                        "id": duplicate_client.id,
                        "name": duplicate_client.client_name,
                        "phone_1": duplicate_client.client_phone_1,
                        "phone_2": duplicate_client.client_phone_2,
                    }
                }
            )

        client.client_name = name
        client.client_phone_1 = phone_1
        client.client_phone_2 = phone_2
        client.full_clean()
        client.save()

        data = {
            "id": client.id,
            "name": client.client_name,
            "phone_1": client.client_phone_1,
            "phone_2": client.client_phone_2,
            "created_at": client.created_at
        }
        return self.success_response(
            data=data,
            message="Client updated successfully."
        )

    except Client.DoesNotExist:
        return self.not_found_response(
            resource="Client",
            message="Client not found.",
            dev_message=f"Client with id {pk} not found for shop {shop_id}."
        )
    except ValidationError as e:
        return self.error_response(
            message="Validation failed.",
            dev_message=e.message_dict
        )
    except Exception as e:
        logger.error(f"Failed to update client: {str(e)}", exc_info=True)
        return self.error_response(
            message="Failed to update client.",
            dev_message=str(e)
        )
