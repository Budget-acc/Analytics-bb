import re


@staticmethod
def _build_serializer_message(errors):
    """
    Convert DRF serializer errors to a readable single message.
    Example:
    - "Ensure model field has no more than 50 characters."
    """
    if not errors or not isinstance(errors, dict):
        return "Please check your product details and try again."

    try:
        field, detail = next(iter(errors.items()))  # first error only
    except StopIteration:
        return "Please check your product details and try again."

    first_error = detail[0] if isinstance(detail, (list, tuple)) and detail else detail
    first_error_text = str(first_error)
    first_error_text_lower = first_error_text.lower().strip()

    # non-field validation
    if field == "non_field_errors":
        return first_error_text or "Please check your product details and try again."

    # convert key -> readable field label
    field_label = str(field).replace("_", " ")

    # custom max-length wording with field name
    # e.g. "Ensure this field has no more than 50 characters."
    m = re.search(r"no more than (\d+) characters", first_error_text, re.IGNORECASE)
    if m:
        return f"Ensure {field_label} field has no more than {m.group(1)} characters."

    # common DRF message patterns
    if first_error_text_lower == "this field is required.":
        return f"{field_label.capitalize()} field is required."

    if first_error_text_lower == "this field may not be blank.":
        return f"{field_label.capitalize()} field may not be blank."

    if first_error_text_lower.startswith("enter a valid"):
        return f"Enter a valid {field_label}."

    # fallback generic field-aware message
    return f"{field_label.capitalize()}: {first_error_text}"


@staticmethod
def _get_serializer_error_metadata(errors):
    """Return non-sensitive serializer error metadata for logs/dev message."""
    if not isinstance(errors, dict):
        return {
            "error_fields": [],
            "error_count": 0,
        }

    error_fields = list(errors.keys())
    error_count = 0

    for value in errors.values():
        if isinstance(value, (list, tuple)):
            error_count += len(value) if len(value) > 0 else 1
        else:
            error_count += 1

    return {
        "error_fields": error_fields,
        "error_count": error_count,
    }
