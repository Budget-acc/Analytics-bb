from phonenumber_field.phonenumber import PhoneNumber
from phonenumbers import NumberParseException
import phonenumbers


def validate_and_format_phone(raw_phone, field_name="phone"):
    """
    Validate phone number:
    - Must include country code (+)
    - Must be valid mobile number
    - Returns E.164 string
    """

    if not raw_phone:
        return None, f"{field_name} is required."

    raw_phone = str(raw_phone).strip()

    # Must start with +
    if not raw_phone.startswith("+"):
        return None, f"{field_name} must include country code (e.g. +91XXXXXXXXXX)."

    try:
        parsed = phonenumbers.parse(raw_phone, None)
    except NumberParseException:
        return None, f"Invalid {field_name} format."

    if not phonenumbers.is_valid_number(parsed):
        return None, f"Invalid {field_name} number."

    # Only allow MOBILE numbers
    number_type = phonenumbers.number_type(parsed)
    if number_type not in (
        phonenumbers.PhoneNumberType.MOBILE,
        phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE,
    ):
        return None, f"{field_name} must be a valid mobile number."

    return phonenumbers.format_number(
        parsed,
        phonenumbers.PhoneNumberFormat.E164
    ), None