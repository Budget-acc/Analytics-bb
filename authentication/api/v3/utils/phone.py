import phonenumbers
from phonenumbers.phonenumberutil import NumberParseException

def normalize_phone(phone, default_region="IN"):
    """
    Normalizes a phone number to E.164 format (e.g., +919876543210).

    Args:
        phone (str): Raw phone input.
        default_region (str): Region code (e.g., 'IN' for India).

    Returns:
        str: Normalized phone number in E.164 format (or None if invalid).

    Raises:
        ValueError: If the phone number is invalid.
    """
    try:
        phone = str(phone).strip()
        if phone.startswith("0"):
            phone = "+91" + phone[1:]
        elif phone.startswith("91") and len(phone) == 12:
            phone = "+" + phone
        elif len(phone) == 10:
            phone = "+91" + phone
        elif not phone.startswith("+"):
            phone = "+" + phone

        parsed = phonenumbers.parse(phone, default_region)
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError("Invalid phone number.")

        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except NumberParseException:
        raise ValueError("Invalid phone number.")
