from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from shop.models import Shop
from shop.api.admin_v1.serializers import ShopSerializer
from shop.api.admin_v1.utils.organization_resolver import resolve_organization
from shop.utils.s3_folder import allocate_s3_folder_name
from finance.models import Account
from PIL import Image
import boto3
import uuid
from io import BytesIO
from django.conf import settings
from ERP.utils.s3_cache_control import IMAGE_CACHE_CONTROL


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="Create shop",
    description=(
        "Creates a shop, optionally uploads image to S3, and creates default finance accounts.\n\n"
        "- show_prices_in_public_listing (boolean, optional): Whether product prices are "
        "visible in this shop's public listing. Default: true."
    ),
    request=inline_serializer(
        name="ShopAdminV1CreateShopRequest",
        fields={
            "name": serializers.CharField(required=True),
            "place": serializers.CharField(required=True),
            "phone": serializers.CharField(required=False, allow_blank=True),
            "phone2": serializers.CharField(required=False, allow_blank=True),
            "address": serializers.CharField(required=False, allow_blank=True),
            "email": serializers.EmailField(required=False, allow_blank=True),
            "city": serializers.CharField(required=False, allow_blank=True),
            "state": serializers.CharField(required=False, allow_blank=True),
            "pincode": serializers.CharField(required=False, allow_blank=True),
            "img": serializers.ImageField(required=False),
            "show_prices_in_public_listing": serializers.BooleanField(
                required=False,
                default=True,
                help_text="Whether product prices are visible in this shop's public listing. Default: true.",
            ),
            "organization_id": serializers.IntegerField(
                required=False,
                help_text=(
                    "ID of an existing active organization to associate this shop with. "
                    "Mutually exclusive with organization_name."
                ),
            ),
            "organization_name": serializers.CharField(
                required=False,
                help_text=(
                    "Name for a new organization to create and associate with this shop. "
                    "Mutually exclusive with organization_id."
                ),
            ),
            "org_phone": serializers.CharField(required=False, allow_blank=True, help_text="Organization primary phone (only when creating new org)."),
            "org_phone2": serializers.CharField(required=False, allow_blank=True, help_text="Organization secondary phone (only when creating new org)."),
            "org_street": serializers.CharField(required=False, allow_blank=True, help_text="Organization street/address (only when creating new org)."),
            "org_gst_number": serializers.CharField(required=False, allow_blank=True, help_text="Organization GST number (only when creating new org)."),
            "org_place": serializers.CharField(required=False, allow_blank=True, help_text="Organization place/city (only when creating new org)."),
            "org_email": serializers.EmailField(required=False, allow_blank=True, help_text="Organization email (only when creating new org)."),
            "org_city": serializers.CharField(required=False, allow_blank=True, help_text="Organization city (only when creating new org)."),
            "org_state": serializers.CharField(required=False, allow_blank=True, help_text="Organization state (only when creating new org)."),
            "org_pincode": serializers.CharField(required=False, allow_blank=True, help_text="Organization pincode (only when creating new org)."),
        },
    ),
    responses={
        201: OpenApiResponse(description="Exact message: Shop created successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Both 'name' and 'place' are required.\n"
                "- A shop named '{name}' already exists in '{place}'.\n"
                "- Image must be less than 3MB.\n"
                "- Invalid or unsupported image format.\n"
                "- Provide either 'organization_id' or 'organization_name', not both.\n"
                "- Organization not found.\n"
                "- Organization is inactive and cannot be assigned.\n"
                "- An organization named '{name}' already exists.\n"
                "- First serializer error message returned in error field."
            )
        ),
        500: OpenApiResponse(description="Exact message pattern: An error occurred: {exception message}"),
    },
    examples=[
        OpenApiExample("Request — link existing org", value={"name": "Prime Tailors", "place": "Kochi", "phone": "9999999999", "organization_id": 1}, request_only=True),
        OpenApiExample("Request — create new org", value={"name": "Prime Tailors", "place": "Kochi", "organization_name": "Royal Wedding Rentals"}, request_only=True),
        OpenApiExample("Success response", value={"message": "Shop created successfully.", "data": {"id": 4, "name": "Prime Tailors", "place": "Kochi", "show_prices_in_public_listing": True, "organization": {"id": 1, "name": "Royal Wedding Rentals", "slug": "royal-wedding-rentals", "public_token": "X7h29LmQa3"}}}, response_only=True, status_codes=["201"]),
        OpenApiExample("Duplicate shop response", value={"error": "A shop named 'Prime Tailors' already exists in 'Kochi'."}, response_only=True, status_codes=["400"]),
    ],
)
def create(self, request):
    try:
        data = request.data.copy()
        data.pop("img", None)
        name = data.get("name", "").strip()
        place = data.get("place", "").strip()

        if not name or not place:
            return Response(
                {"error": "Both 'name' and 'place' are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check for uniqueness (case-insensitive)
        exists = Shop.objects.filter(
            name__iexact=name,
            place__iexact=place
        ).exists()
        if exists:
            return Response(
                {"error": f"A shop named '{name}' already exists in '{place}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # --- Resolve organization (optional) ---
        # Read scalars with .get() (QueryDict.pop returns a list), then strip keys from data
        organization_id = data.get("organization_id") or None
        organization_name = data.get("organization_name") or None
        data.pop("organization_id", None)
        data.pop("organization_name", None)

        # Collect org contact fields (org_ prefix avoids collision with shop fields)
        _ORG_FIELDS = ["phone", "phone2", "street", "gst_number", "place", "email", "city", "state", "pincode"]
        org_extra_fields = {f: data[f"org_{f}"] for f in _ORG_FIELDS if data.get(f"org_{f}")}

        # NOTE: resolve_organization() may CREATE a new org row.
        # It is called inside transaction.atomic() below so that any newly
        # created org is rolled back automatically if the subsequent Shop save fails.

        # Handle image file upload
        image_file = request.FILES.get("img")
        img_url = None
        s3_folder_name = None

        if image_file:
            if image_file.size > 3 * 1024 * 1024:
                return Response({"error": "Image must be less than 3MB."}, status=400)

            try:
                img = Image.open(image_file)
                img = img.convert("RGBA")
            except Exception:
                return Response({"error": "Invalid or unsupported image format."}, status=400)

            # Allocate the shop's permanent S3 folder now so the logo lands in the
            # same folder Shop.save() stores (passed to serializer.save below).
            s3_folder_name = allocate_s3_folder_name(name, place)
            filename = f"{s3_folder_name}/shop_images/{uuid.uuid4().hex}.png"

            # Convert image to bytes
            img_io = BytesIO()
            img.save(img_io, format="PNG")
            img_io.seek(0)

            # Upload to S3
            s3 = boto3.client(
                "s3",
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=settings.AWS_S3_REGION_NAME,
            )
            s3.upload_fileobj(
                img_io,
                settings.AWS_STORAGE_BUCKET_NAME,
                filename,
                ExtraArgs={"ContentType": "image/png", "CacheControl": IMAGE_CACHE_CONTROL}
            )

            bucket = settings.AWS_STORAGE_BUCKET_NAME
            region = settings.AWS_S3_REGION_NAME
            img_url = f"https://{bucket}.s3.{region}.amazonaws.com/{filename}"

        # Create the shop instance
        serializer = ShopSerializer(data=data)
        if serializer.is_valid():
            with transaction.atomic():
                # Resolve org inside the atomic block — if org creation succeeds but
                # the shop save below fails, Django rolls back the new org row too.
                org, org_error = resolve_organization(
                    organization_id=organization_id,
                    organization_name=organization_name,
                    org_extra_fields=org_extra_fields,
                )
                if org_error:
                    return Response({"error": org_error}, status=status.HTTP_400_BAD_REQUEST)
                extra = {"s3_folder_name": s3_folder_name} if s3_folder_name else {}
                shop = serializer.save(img=img_url, organization=org, **extra)
                Account.objects.bulk_create([
                    Account(shop=shop, name="Cash in Hand", account_type="cash", is_default=True, is_active=True),
                    Account(shop=shop, name="Default Bank Account", account_type="bank", is_default=False, is_active=True),
                ])
            # shop.organization is already the full org instance passed to serializer.save(),
            # so no re-fetch is needed — get_organization() will not hit the DB again.
            return Response(
                {"message": "Shop created successfully.", "data": ShopSerializer(shop).data},
                status=status.HTTP_201_CREATED
            )
        else:
            # return Response({"error": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
            errors = serializer.errors
            first_error = next(iter(errors.values()))[0]
            return Response({"error": str(first_error)}, status=status.HTTP_400_BAD_REQUEST)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({"error": f"An error occurred: {str(e)}"}, status=500)
