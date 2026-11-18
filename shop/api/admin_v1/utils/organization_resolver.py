from django.db import IntegrityError
from django.utils.text import slugify
from shop.models import Organization
from shop.models.organization import generate_unique_org_token


def resolve_organization(organization_id=None, organization_name=None, org_extra_fields=None):
    """
    Resolves the organization to associate with a shop during create or update.

    Returns:
        (Organization | None, str | None) — (org_instance, error_message)

    Rules:
        - Both organization_id and organization_name supplied
              → error: ambiguous input
        - organization_id only
              → validate org exists and is active; return instance or error
        - organization_name only
              → check no duplicate name; create new org with optional extra fields; return instance
        - Neither supplied
              → return (None, None) — org association is optional

    org_extra_fields (dict | None):
        Optional contact/address fields (phone, phone2, street, gst_number, place,
        email, city, state, pincode) to persist on the newly created org.
        Ignored when resolving by organization_id.
    """
    has_id = organization_id is not None and str(organization_id).strip() != ""
    has_name = organization_name is not None and str(organization_name).strip() != ""

    if has_id and has_name:
        return None, "Provide either 'organization_id' or 'organization_name', not both."

    if has_id:
        try:
            org_id = int(organization_id)
        except (TypeError, ValueError):
            return None, "'organization_id' must be a valid integer."

        try:
            org = Organization.objects.get(pk=org_id)
        except Organization.DoesNotExist:
            return None, "Organization not found."

        if not org.is_active:
            return None, "Organization is inactive and cannot be assigned."

        return org, None

    if has_name:
        name = str(organization_name).strip()

        if Organization.objects.filter(name__iexact=name).exists():
            return None, f"An organization named '{name}' already exists."

        slug = slugify(name)
        if Organization.objects.filter(slug=slug).exists():
            return None, f"An organization with a similar name already exists (slug '{slug}' is taken)."

        # Filter org_extra_fields to only valid Organization contact fields
        ALLOWED_EXTRA_FIELDS = {"phone", "phone2", "street", "gst_number", "place", "email", "city", "state", "pincode"}
        extra = {k: v for k, v in (org_extra_fields or {}).items() if k in ALLOWED_EXTRA_FIELDS and v}

        try:
            org = Organization.objects.create(
                name=name,
                public_token=generate_unique_org_token(),
                is_active=True,
                public_listing_enabled=True,
                **extra,
            )
        except IntegrityError:
            return None, f"An organization named '{name}' already exists."
        return org, None

    # Neither provided — no org, no error
    return None, None
