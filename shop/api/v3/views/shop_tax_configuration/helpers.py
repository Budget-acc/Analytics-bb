def get_first_validation_error_message(errors, fallback):
    """Return the first message from a DRF errors dict, or a fallback string."""
    for value in errors.values():
        if isinstance(value, list) and value:
            return str(value[0])
        if isinstance(value, str):
            return value
    return fallback
