from booking_v2.models import Client
from ERP.utils.utils import validate_and_format_phone
import logging

logger = logging.getLogger("api_requests")


def _get_or_create_client(self, shop_id, client_data):
    """
    New E164-based get or create client.
    Only supports phone_1_e164 and phone_2_e164.
    """

    client_name = (client_data.get("client_name") or "").strip().title()
    phone_1_raw = client_data.get("phone_1_e164")
    phone_2_raw = client_data.get("phone_2_e164")

    if not client_name:
        return None, False, "Client name is required."

    if not phone_1_raw:
        return None, False, "Primary phone number is required."

    # Validate primary phone
    validated_phone_1, error = validate_and_format_phone(
        phone_1_raw, "Primary phone"
    )
    if error:
        return None, False, error

    # Validate secondary phone (optional)
    validated_phone_2 = None
    if phone_2_raw:
        validated_phone_2, error = validate_and_format_phone(
            phone_2_raw, "Secondary phone"
        )
        if error:
            return None, False, error

    # Duplicate check using name + primary phone
    client = Client.objects.filter(
        shop_id=shop_id,
        client_name=client_name,
        phone_1_e164=validated_phone_1
    ).first()

    if client:
        update_fields = []

        # If secondary provided and different → update only secondary
        if validated_phone_2 and client.phone_2_e164 != validated_phone_2:
            client.phone_2_e164 = validated_phone_2
            update_fields.append("phone_2_e164")

        if update_fields:
            client.save(update_fields=update_fields)

        return client, False, None

    # Create new client
    try:
        client = Client.objects.create(
            shop_id=shop_id,
            client_name=client_name,
            phone_1_e164=validated_phone_1,
            phone_2_e164=validated_phone_2,
        )
        return client, True, None
    except Exception as e:
        logger.error(f"Failed to create client: {str(e)}")
        return None, False, "Failed to create client."
