from django.db.models import Q


def _apply_product_variant_search(self, product_qs, search_field, search_value, service_obj=None):
    """
    Apply unified search for available product endpoints.

    Rules:
    - `name` search => match product name OR variant attribute.
    - Works for gadget services and non-gadget services the same way.
    """
    standard_fields = ["name", "color", "category", "model"]

    if search_field not in standard_fields:
        return None, self.validation_error_response(
            errors={"search_field": [f"Invalid search_field. Allowed: {', '.join(standard_fields)}"]},
            message="Invalid search field."
        )

    variant_name_q = Q(
        variants__is_active=True,
        variants__attribute__icontains=search_value
    )

    if search_field == "name":
        # Always search in BOTH product name and variant attribute
        return product_qs.filter(
            Q(name__icontains=search_value) | variant_name_q
        ).distinct(), None

    return product_qs.filter(
        Q(**{f"{search_field}__icontains": search_value}) | variant_name_q
    ).distinct(), None
