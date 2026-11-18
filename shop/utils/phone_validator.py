import re
from rest_framework.exceptions import ValidationError

PHONE_REGEX = re.compile(r"^\+?[0-9]+$")

def validate_and_normalize_phone(
    raw_phone: str,
    raise_exception: bool = True
):
    """
    Validates and normalizes a phone number.

    Rules:
    - Only digits and optional leading '+'
    - Max 15 characters including '+'
    - Trims spaces (start, end, and between)
    """

    if not raw_phone:
        error = "Phone number is required."
        if raise_exception:
            raise ValidationError(error)
        return None, error


    # Convert to string & trim
    phone = str(raw_phone).strip()

    # Remove all spaces between numbers
    phone = phone.replace(" ", "")

    # Remove leading +
    if phone.startswith("+"):
        phone = phone[1:]  

    # Only digits and +
    if not PHONE_REGEX.match(phone):
        error = "Phone number can contain only digits and '+'."
        if raise_exception:
            raise ValidationError(error)
        return None, error

    # Length validation
    if len(phone) > 15:
        error = "Phone number must not exceed 15 characters."
        if raise_exception:
            raise ValidationError(error)
        return None, error

    return phone, None
