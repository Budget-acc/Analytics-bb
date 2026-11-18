from booking_v2.models import Client
from shop.utils.phone_validator import validate_and_normalize_phone
from ERP.utils.utils import validate_and_format_phone
import logging

logger = logging.getLogger("api_requests")


def _get_or_create_client(self, shop_id, client_data):
    """
    Backward compatible get or create client.
    Supports both old integer phones and new E164 phones.
    """

    client_name = (client_data.get("client_name") or "").strip().title()

    if not client_name:
        return None, False, "Client name is required."

    # Detect payload type
    old_phone_1 = client_data.get("client_phone_1")
    old_phone_2 = client_data.get("client_phone_2")

    new_phone_1 = client_data.get("phone_1_e164")
    new_phone_2 = client_data.get("phone_2_e164")

    use_new_format = bool(new_phone_1)

    # CASE 1: NEW FORMAT (E164)
    if use_new_format:

        validated_phone_1, error = validate_and_format_phone(
            new_phone_1, "Primary phone"
        )
        if error:
            return None, False, error

        validated_phone_2 = None
        if new_phone_2:
            validated_phone_2, error = validate_and_format_phone(
                new_phone_2, "Secondary phone"
            )
            if error:
                return None, False, error

        # Check duplicate using E164
        client = Client.objects.filter(
            shop_id=shop_id,
            client_name=client_name,
            phone_1_e164=validated_phone_1
        ).first()

        if client:
            if validated_phone_2 and client.phone_2_e164 != validated_phone_2:
                client.phone_2_e164 = validated_phone_2
                client.save(update_fields=["phone_2_e164"])
            return client, False, None

        # Create new client (ONLY new fields)
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

    # CASE 2: OLD FORMAT
    else:
        if not old_phone_1:
            return None, False, "Client phone number is required."

        # Old normalization (no strict validation)
        normalized_phone_1, error = validate_and_normalize_phone(
            old_phone_1, raise_exception=False
        )
        if error:
            return None, False, error

        normalized_phone_2 = None
        if old_phone_2:
            normalized_phone_2, error = validate_and_normalize_phone(
                old_phone_2, raise_exception=False
            )
            if error:
                return None, False, error

        # Convert to E164 using default +91
        e164_phone_1 = f"+91{normalized_phone_1}"
        e164_phone_2 = f"+91{normalized_phone_2}" if normalized_phone_2 else None

        # Validate converted E164
        validated_phone_1, error = validate_and_format_phone(
            e164_phone_1, "Primary phone"
        )
        if error:
            return None, False, error

        validated_phone_2 = None
        if e164_phone_2:
            validated_phone_2, error = validate_and_format_phone(
                e164_phone_2, "Secondary phone"
            )
            if error:
                return None, False, error

        # Check duplicate using OLD field (keep logic same)
        client = Client.objects.filter(
            shop_id=shop_id,
            client_name=client_name,
            client_phone_1=normalized_phone_1
        ).first()

        if client:
            update_fields = []

            if normalized_phone_2 and client.client_phone_2 != normalized_phone_2:
                client.client_phone_2 = normalized_phone_2
                update_fields.append("client_phone_2")

            # Also update E164 fields if missing
            if not client.phone_1_e164:
                client.phone_1_e164 = validated_phone_1
                update_fields.append("phone_1_e164")

            if validated_phone_2 and client.phone_2_e164 != validated_phone_2:
                client.phone_2_e164 = validated_phone_2
                update_fields.append("phone_2_e164")

            if update_fields:
                client.save(update_fields=update_fields)

            return client, False, None

        # Create new client (SAVE BOTH OLD + NEW)
        try:
            client = Client.objects.create(
                shop_id=shop_id,
                client_name=client_name,
                client_phone_1=normalized_phone_1,
                client_phone_2=normalized_phone_2,
                phone_1_e164=validated_phone_1,
                phone_2_e164=validated_phone_2,
            )
            return client, True, None
        except Exception as e:
            logger.error(f"Failed to create client: {str(e)}")
            return None, False, "Failed to create client."
