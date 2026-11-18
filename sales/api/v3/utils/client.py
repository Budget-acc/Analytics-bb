from booking_v2.models import Client


def get_or_create_client(shop_id, client_data):
    """
    Get existing client by normalized phone/name or create new one.
    Returns (client, created_flag)
    """
    client_name = (client_data.get("client_name") or "").strip().title()
    phone_1 = client_data.get("client_phone_1")
    phone_2 = client_data.get("client_phone_2")

    # Normalize phone numbers the same way as Client.save()
    def normalize_phone(phone):
        if not phone:
            return None
        phone = str(phone).strip().replace("+91", "").lstrip("0")
        if len(phone) > 10:
            phone = phone[-10:]
        return int(phone) if phone.isdigit() else None

    phone_1 = normalize_phone(phone_1)
    phone_2 = normalize_phone(phone_2)

    if not client_name or not phone_1:
        return None, False

    # Check if client already exists with normalized values
    client = Client.objects.filter(
        shop_id=shop_id,
        client_name=client_name,
        client_phone_1=phone_1
    ).first()

    if client:
        return client, False

    # Otherwise create new client
    client = Client.objects.create(
        shop_id=shop_id,
        client_name=client_name,
        client_phone_1=phone_1,
        client_phone_2=phone_2
    )
    return client, True
