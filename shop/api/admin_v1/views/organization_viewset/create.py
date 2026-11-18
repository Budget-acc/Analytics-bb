from rest_framework import serializers
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from django.db.models import Count
from shop.models import Organization
from shop.models.organization import generate_unique_org_token
from shop.api.admin_v1.serializers import OrganizationSerializer
from shop.api.admin_v1.utils.image_upload import upload_org_image


@extend_schema(
    tags=["Organization Admin Management V1"],
    summary="Create organization",
    description=(
        "Creates a new organization (business group).\n\n"
        "Purpose: Allows admins to register a business entity that can own multiple shop "
        "branches. Each organization gets an auto-generated slug (derived from its name) "
        "and a unique public token used for the combined public catalog URL.\n\n"
        "Context: Called when onboarding a multi-branch business. After creating the "
        "organization, shops (branches) are linked to it via the shop's organization field.\n\n"
        "Authentication: JWT — admin users only.\n\n"
        "Accepted inputs:\n"
        "Body (JSON) — Required:\n"
        "- name (string): Full name of the organization. Must be unique (case-insensitive). "
        "Max 255 characters.\n\n"
        "Body (JSON) — Optional:\n"
        "- phone, phone2 (string): Contact numbers. Max 15 characters.\n"
        "- street (string): Street / address.\n"
        "- gst_number (string): GST number. Max 50 characters.\n"
        "- place (string): City/town of the organization. Max 100 characters.\n"
        "- email (string): Email address.\n"
        "- city, state (string): City and state. Max 100 characters each.\n"
        "- pincode (string): Postal code. Max 10 characters.\n"
        "- is_active (boolean): Whether the organization is active. Default: true.\n"
        "- public_listing_enabled (boolean): Whether the public combined catalog is enabled. "
        "Default: true.\n"
        "- show_prices_in_public_listing (boolean): Whether product prices are visible in "
        "the org's public combined catalog. Default: true.\n\n"
        "Auto-generated (not user-supplied):\n"
        "- slug: URL-safe slug derived from name.\n"
        "- public_token: 10-character nanoid used in the public catalog URL."
    ),
    request=inline_serializer(
        name="OrganizationAdminV1CreateRequest",
        fields={
            "name": serializers.CharField(
                required=True,
                help_text="Full name of the organization. Must be unique. Max 255 characters.",
            ),
            "phone": serializers.CharField(required=False, allow_blank=True, help_text="Primary contact number."),
            "phone2": serializers.CharField(required=False, allow_blank=True, help_text="Secondary contact number."),
            "street": serializers.CharField(required=False, allow_blank=True, help_text="Street / address."),
            "gst_number": serializers.CharField(required=False, allow_blank=True, help_text="GST number."),
            "place": serializers.CharField(required=False, allow_blank=True, help_text="City/town of the organization."),
            "email": serializers.EmailField(required=False, allow_blank=True, help_text="Email address."),
            "city": serializers.CharField(required=False, allow_blank=True, help_text="City."),
            "state": serializers.CharField(required=False, allow_blank=True, help_text="State."),
            "pincode": serializers.CharField(required=False, allow_blank=True, help_text="Postal code."),
            "is_active": serializers.BooleanField(
                required=False,
                default=True,
                help_text="Whether the organization is active. Default: true.",
            ),
            "public_listing_enabled": serializers.BooleanField(
                required=False,
                default=True,
                help_text="Whether the public combined catalog is enabled. Default: true.",
            ),
            "show_prices_in_public_listing": serializers.BooleanField(
                required=False,
                default=True,
                help_text="Whether product prices are visible in the org's public combined catalog. Default: true.",
            ),
            "image": serializers.ImageField(required=False, help_text="Organization thumbnail. Resized to max 300px and converted to WebP before storage."),
        },
    ),
    responses={
        201: OpenApiResponse(description="Exact message: Organization created successfully."),
        400: OpenApiResponse(
            description=(
                "Validation error messages:\n"
                "- 'name' is required.\n"
                "- An organization named '{name}' already exists."
            )
        ),
        500: OpenApiResponse(description="Exact message pattern: An error occurred: {exception message}"),
    },
    examples=[
        OpenApiExample(
            "Request example",
            value={
                "name": "Royal Wedding Rentals",
                "phone": "9999999999",
                "email": "info@royalwedding.com",
                "place": "Kochi",
                "is_active": True,
                "public_listing_enabled": True,
                "show_prices_in_public_listing": True,
            },
            request_only=True,
        ),
        OpenApiExample(
            "Success response",
            value={
                "status": "success",
                "message": "Organization created successfully.",
                "data": {
                    "id": 1,
                    "name": "Royal Wedding Rentals",
                    "slug": "royal-wedding-rentals",
                    "public_token": "X7h29LmQa3",
                    "phone": "9999999999",
                    "phone2": None,
                    "street": None,
                    "gst_number": None,
                    "place": "Kochi",
                    "email": "info@royalwedding.com",
                    "city": None,
                    "state": None,
                    "pincode": None,
                    "is_active": True,
                    "public_listing_enabled": True,
                    "show_prices_in_public_listing": True,
                    "created_at": "2026-06-19T10:00:00Z",
                    "shop_count": 0,
                },
            },
            response_only=True,
            status_codes=["201"],
        ),
        OpenApiExample(
            "Duplicate name response",
            value={"status": "error", "message": "An organization named 'Royal Wedding Rentals' already exists."},
            response_only=True,
            status_codes=["400"],
        ),
    ],
)
def create(self, request):
    try:
        # QueryDict.copy() uses deepcopy which cannot pickle file objects from
        # multipart requests. A plain dict comprehension is safe for both JSON and multipart.
        data = {k: v for k, v in request.data.items()}
        name = data.get("name", "").strip()

        if not name:
            return self.validation_error_response(
                errors={"name": ["'name' is required."]},
                message="'name' is required.",
            )

        if Organization.objects.filter(name__iexact=name).exists():
            return self.error_response(
                message=f"An organization named '{name}' already exists.",
            )

        # Auto-generate public token — not user-supplied
        public_token = generate_unique_org_token()

        # Handle optional image upload (multipart/form-data)
        image_url = None
        image_file = request.FILES.get("image")
        if image_file:
            image_url, img_error = upload_org_image(image_file, name)
            if img_error:
                return self.validation_error_response(
                    errors={"image": [img_error]},
                    message=img_error,
                )

        serializer = OrganizationSerializer(data=data)
        if serializer.is_valid():
            org = serializer.save(public_token=public_token, image=image_url)
            # Re-fetch with shop_count annotation so the serializer can include it
            org_with_count = (
                Organization.objects
                .annotate(shop_count=Count("shops"))
                .get(pk=org.pk)
            )
            return self.success_response(
                message="Organization created successfully.",
            )
        else:
            first_error = next(iter(serializer.errors.values()))[0]
            return self.validation_error_response(
                errors=serializer.errors,
                message=str(first_error),
            )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return self.error_response(
            message=f"An error occurred: {str(e)}",
        )
