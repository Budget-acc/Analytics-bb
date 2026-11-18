def _get_vendor_or_404(self, pk):
    """
    Shop-scoped lookup that returns the standard envelope on a miss,
    instead of self.get_object()'s DRF-default {"detail": "Not found."}
    (which bypasses StandardResponseMixin entirely).
    Returns (vendor, error_response).
    """
    vendor = self.get_queryset().filter(pk=pk).first()
    if not vendor:
        return None, self.not_found_response(
            resource="Vendor",
            message="Vendor not found.",
            dev_message=f"Vendor '{pk}' not found for this shop." if pk else None,
        )
    return vendor, None


def _first_validation_error_message(errors):
    """
    Flattens a DRF serializer.errors dict down to one short message for
    validation_error_response's top-level `message` - e.g.
    {"name": ["A vendor with this name already exists."]} -> that string.

    Plain module-level function (not wired onto self) - takes only
    `errors`, so it's imported and called directly rather than via
    self.<name>(...), which would incorrectly bind `self` as its first
    positional argument.
    """
    for field_errors in errors.values():
        if field_errors:
            return str(field_errors[0])
    return "Invalid request."


__all__ = ["_get_vendor_or_404", "_first_validation_error_message"]
