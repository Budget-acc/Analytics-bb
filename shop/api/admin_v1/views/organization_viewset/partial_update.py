from rest_framework import serializers
from django.db.models import Q, Count
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from shop.models import Organization
from shop.api.admin_v1.serializers import OrganizationSerializer
from shop.api.admin_v1.utils.image_upload import upload_org_image


@extend_schema(
    tags=["Organization Admin Management V1"],
    summary="Update organization",
    description=(
        "Partially updates an organization's details.\n\n"
        "Purpose: Allows admins to update the name, contact details, address, active status, or "
        "public listing visibility of an organization. When the name changes, the slug is "
        "automatically re-generated from the new name.\n\n"
        "Context: slug and public_token are system-managed and cannot be updated by the admin "
        "directly. They are omitted from the request body. The slug updates automatically "
        "when the name changes.\n\n"
        "Authentication: JWT — admin users only.\n\n"
        "Path parameters — Required:\n"
        "- id (integer): The database ID of the organization to update.\n\n"
        "Body (JSON) — All fields optional (at least one required):\n"
        "- name (string): New name. Must be unique (case-insensitive). Max 255 characters.\n"
        "- phone, phone2 (string): Contact numbers.\n"
        "- street (string): Street / address.\n"
        "- gst_number (string): GST number.\n"
        "- place (string): City/town.\n"
        "- email (string): Email address.\n"
        "- city, state (string): City and state.\n"
        "- pincode (string): Postal code.\n"
        "- is_active (boolean): Active status.\n"
        "- public_listing_enabled (boolean): Public catalog visibility.\n"
        "- show_prices_in_public_listing (boolean): Whether product prices are visible in "
        "the org's public combined catalog."
    ),
    parameters=[
        OpenApiParameter(
            name="id",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Identifies the organization to update. "
                "Location: URL path parameter. "
                "Format/Type: integer. "
                "Required."
            ),
        ),
    ],
    request=inline_serializer(
        name="OrganizationAdminV1PatchRequest",
        fields={
            "name": serializers.CharField(required=False, help_text="New organization name. Must be unique. Max 255 characters."),
            "phone": serializers.CharField(required=False, allow_blank=True, help_text="Primary contact number."),
            "phone2": serializers.CharField(required=False, allow_blank=True, help_text="Secondary contact number."),
            "street": serializers.CharField(required=False, allow_blank=True, help_text="Street / address."),
            "gst_number": serializers.CharField(required=False, allow_blank=True, help_text="GST number."),
            "place": serializers.CharField(required=False, allow_blank=True, help_text="City/town of the organization."),
            "email": serializers.EmailField(required=False, allow_blank=True, help_text="Email address."),
            "city": serializers.CharField(required=False, allow_blank=True, help_text="City."),
            "state": serializers.CharField(required=False, allow_blank=True, help_text="State."),
            "pincode": serializers.CharField(required=False, allow_blank=True, help_text="Postal code."),
            "is_active": serializers.BooleanField(required=False, help_text="Active status of the organization."),
            "public_listing_enabled": serializers.BooleanField(required=False, help_text="Whether the public combined catalog is enabled."),
            "show_prices_in_public_listing": serializers.BooleanField(required=False, help_text="Whether product prices are visible in the org's public combined catalog."),
            "image": serializers.ImageField(required=False, help_text="Organization thumbnail. Resized to max 300px and converted to WebP before storage."),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Organization updated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation error messages:\n"
                "- An organization named '{name}' already exists."
            )
        ),
        404: OpenApiResponse(description="Exact message: Organization not found."),
    },
    examples=[
        OpenApiExample(
            "Request example",
            value={"name": "Royal Bridal Rentals", "phone": "9999999999", "place": "Kochi", "is_active": True},
            request_only=True,
        ),
        OpenApiExample(
            "Success response",
            value={
                "status": "success",
                "message": "Organization updated successfully.",
                "data": {
                    "id": 1,
                    "name": "Royal Bridal Rentals",
                    "slug": "royal-bridal-rentals",
                    "public_token": "X7h29LmQa3",
                    "phone": "9999999999",
                    "phone2": None,
                    "street": None,
                    "gst_number": None,
                    "place": "Kochi",
                    "email": None,
                    "city": None,
                    "state": None,
                    "pincode": None,
                    "is_active": True,
                    "public_listing_enabled": True,
                    "show_prices_in_public_listing": True,
                    "created_at": "2026-06-19T10:00:00Z",
                    "shop_count": 3,
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
        OpenApiExample(
            "Duplicate name response",
            value={"status": "error", "message": "An organization named 'Royal Bridal Rentals' already exists."},
            response_only=True,
            status_codes=["400"],
        ),
        OpenApiExample(
            "Not found response",
            value={"status": "error", "message": "Organization not found."},
            response_only=True,
            status_codes=["404"],
        ),
    ],
)
def partial_update(self, request, pk=None):
    try:
        org = Organization.objects.get(pk=pk)
    except Organization.DoesNotExist:
        return self.not_found_response(resource="Organization")

    # QueryDict.copy() uses deepcopy which cannot pickle file objects from
    # multipart requests. A plain dict comprehension is safe for both JSON and multipart.
    data = {k: v for k, v in request.data.items()}

    # Prevent user from supplying slug or public_token directly
    data.pop("slug", None)
    data.pop("public_token", None)

    # Name uniqueness check (case-insensitive, excluding current org)
    new_name = data.get("name", "").strip()
    if new_name:
        if Organization.objects.filter(~Q(pk=org.pk), name__iexact=new_name).exists():
            return self.error_response(
                message=f"An organization named '{new_name}' already exists.",
            )
        # Reset slug so Organization.save() re-generates it from the new name
        org.slug = ""

    # Handle optional image upload — keep existing URL if no new file is provided
    image_url = org.image
    image_file = request.FILES.get("image")
    if image_file:
        new_url, img_error = upload_org_image(image_file, org.name)
        if img_error:
            return self.validation_error_response(
                errors={"image": [img_error]},
                message=img_error,
            )
        image_url = new_url

    serializer = OrganizationSerializer(org, data=data, partial=True)
    if serializer.is_valid():
        updated_org = serializer.save(image=image_url)
        # Re-fetch with shop_count annotation
        updated_org = (
            Organization.objects
            .annotate(shop_count=Count("shops"))
            .get(pk=updated_org.pk)
        )
        return self.success_response(
            data=OrganizationSerializer(updated_org).data,
            message="Organization updated successfully.",
        )
    else:
        first_error = next(iter(serializer.errors.values()))[0]
        return self.validation_error_response(
            errors=serializer.errors,
            message=str(first_error),
        )
