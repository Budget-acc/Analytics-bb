import string
import re


def _sanitize_address_field(self, value, max_length=128):
    """Sanitize address field to prevent injection and ensure data quality."""
    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None

    ALLOWED_CHARS = set(string.ascii_letters + string.digits + string.whitespace + ",.-()/&'")
    cleaned = ''.join(c for c in text if c in ALLOWED_CHARS)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].strip()

    return cleaned if cleaned else None

def _build_shop_address(self, shop_obj):
    """Build sanitized shop address from shop object fields."""
    address_fields = [('place', 100), ('city', 100)]

    cleaned_parts = []
    for field_name, max_len in address_fields:
        field_value = getattr(shop_obj, field_name, None)
        cleaned = self._sanitize_address_field(field_value, max_len)
        if cleaned:
            cleaned_parts.append(cleaned)

    address = ", ".join(cleaned_parts)

    MAX_TOTAL_LENGTH = 500
    if len(address) > MAX_TOTAL_LENGTH:
        address = address[:MAX_TOTAL_LENGTH].rsplit(',', 1)[0]

    return address if address else "Address not available"
