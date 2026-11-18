from shop.utils.phone_validator import validate_and_normalize_phone


def _validate_and_convert_phone(self, phone_str, field_name="phone"):
    """
    Validate and convert phone to integer for storage.
    Returns (phone_int, error_message)
    """
    if not phone_str:
        return None, None

    # Validate using centralized validator
    normalized_phone, error = validate_and_normalize_phone(
        raw_phone=phone_str,
        raise_exception=False
    )

    if error:
        return None, error

    # Convert to integer (remove '+' if present)
    try:
        phone_int = int(normalized_phone.replace('+', ''))
        return phone_int, None
    except (ValueError, AttributeError):
        return None, f"Invalid {field_name} format."
