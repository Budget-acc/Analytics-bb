import json
import logging
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

from django.db import transaction
from rest_framework.response import Response

from authentication.api.v1.utils import normalize_phone
from finance.models import Account
from services.api.v3.utils.s3 import upload_image_to_s3_with_resize
from shop.api.admin_v1.serializers import UserSerializer
from shop.models import Shop, ShopUser, ShopNumberingSequence
from shop.api.admin_v1.utils.organization_resolver import resolve_organization
from shop.utils.s3_folder import allocate_s3_folder_name

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="Create user with shop",
    description="Creates user, shop, and shop-user link atomically; optionally uploads shop image and seeds default accounts.",
    request=inline_serializer(
        name="ShopAdminV1CreateUserAndShopRequest",
        fields={
            "full_name": serializers.CharField(required=True),
            "phone": serializers.CharField(required=True),
            "password": serializers.CharField(required=True),
            "role": serializers.CharField(required=True),
            "name": serializers.CharField(required=True, help_text="Shop name"),
            "place": serializers.CharField(required=True, help_text="Shop place"),
            "secret_password": serializers.CharField(required=False),
            "secondary_password": serializers.CharField(required=False),
            "booking_start_id": serializers.IntegerField(required=False),
            "sale_start_id": serializers.IntegerField(required=False),
            "tailor_start_id": serializers.IntegerField(required=False),
            "subscription_renewal_price": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "terms_and_conditions": serializers.JSONField(required=False),
            "image": serializers.ImageField(required=False),
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
        201: OpenApiResponse(description="Exact message: User, shop, and link created successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Shop secret password is required.\n"
                "- Both 'name' and 'place' are required for shop.\n"
                "- Shop '{name}' already exists in '{place}'.\n"
                "- Invalid JSON format for terms_and_conditions.\n"
                "- Booking start ID must be an integer.\n"
                "- Booking start ID must be a positive integer.\n"
                "- Sale start ID must be an integer.\n"
                "- Sale start ID must be a positive integer.\n"
                "- Tailor order start ID must be an integer.\n"
                "- Tailor order start ID must be a positive integer.\n"                
                "- Subscription renewal price must be a valid decimal value.\n"
                "- Subscription renewal price cannot be negative.\n"
                "- Image upload failed. Please try again.\n"
                "- phone normalization errors from normalize_phone.\n"
                "- UserSerializer validation errors returned under errors field."
            )
        ),
        500: OpenApiResponse(description="Exact message: An unexpected error occurred. Please try again later."),
    },
    examples=[
        OpenApiExample("Request example", value={"full_name": "Admin User", "phone": "9999999999", "password": "Secret@123", "role": "OWNER", "name": "Prime Tailors", "place": "Kochi", "secret_password": "1234"}, request_only=True),
        OpenApiExample("Success response", value={"message": "User, shop, and link created successfully.", "user": {"id": 3, "phone": "9999999999"}, "shop": {"id": 4, "name": "Prime Tailors", "place": "Kochi", "img": "https://..."}}, response_only=True, status_codes=["201"]),
    ],
)
def create(self, request):
    img_url = None

    try:
        data = request.data.dict()

        # Backward-compatible key mapping for GST number expected by frontend.
        if not data.get("gst_number") and data.get("shop_gst_number"):
            data["gst_number"] = data.get("shop_gst_number")

        # Validate and normalize shop password.
        shop_password = data.get("secret_password") or data.get("secondary_password")
        if shop_password is not None:
            shop_password = str(shop_password).strip()
        if not shop_password:
            return Response(
                {"errors": {"secret_password": ["Shop secret password is required."]}},
                status=400,
            )

        # --- STEP 1: Normalize and validate phone ---
        try:
            data["phone"] = normalize_phone(data.get("phone", ""))
        except ValueError as e:
            return Response({"errors": {"phone": [str(e)]}}, status=400)

        # --- STEP 2: Validate user data (before any DB writes) ---
        user_data = {
            "phone": data.get("phone"),
            "full_name": data.get("full_name"),
            "last_name": data.get("last_name"),
            "email": data.get("email"),
            "role": data.get("role"),
            "password": data.get("password"),
        }
        user_serializer = UserSerializer(data=user_data)
        if not user_serializer.is_valid():
            return Response({"errors": user_serializer.errors}, status=400)

        # --- STEP 3: Validate shop fields (before any DB writes) ---
        name = data.get("name", "").strip()
        place = data.get("place", "").strip()
        if not name or not place:
            return Response({"error": "Both 'name' and 'place' are required for shop."}, status=400)

        if Shop.objects.filter(name__iexact=name, place__iexact=place).exists():
            return Response({"error": f"Shop '{name}' already exists in '{place}'."}, status=400)

        # --- STEP 3b: Read org fields now (before they're needed inside atomic),
        #              but defer actual resolution to inside the atomic block.
        #              Use .get() (scalar) not .pop() (returns list for QueryDict).
        organization_id = data.get("organization_id") or None
        organization_name = data.get("organization_name") or None

        # Collect org contact fields (org_ prefix avoids collision with shop fields)
        _ORG_FIELDS = ["phone", "phone2", "street", "gst_number", "place", "email", "city", "state", "pincode"]
        org_extra_fields = {f: data[f"org_{f}"] for f in _ORG_FIELDS if data.get(f"org_{f}")}

        # Parse terms_and_conditions if it's a JSON string
        terms_raw = data.get("terms_and_conditions")
        if terms_raw and isinstance(terms_raw, str):
            try:
                data["terms_and_conditions"] = json.loads(terms_raw)
            except json.JSONDecodeError:
                return Response({"error": "Invalid JSON format for terms_and_conditions."}, status=400)

        try:
            booking_start_id = self._parse_positive_int(data.get("booking_start_id"), "Booking start ID")
            sale_start_id = self._parse_positive_int(data.get("sale_start_id"), "Sale start ID")
            tailor_start_id = self._parse_positive_int(data.get("tailor_start_id"), "Tailor order start ID")
        except ValueError as ve:
            return Response({"error": str(ve)}, status=400)

        subscription_renewal_price = self._parse_price(
            data.get("subscription_renewal_price"),
            "Subscription renewal price"
        )

        # --- STEP 4: Upload image (outside transaction — S3 is not transactional) ---
        # Allocate the shop's permanent S3 folder up front so the logo lands in
        # the same folder Shop.save() stores (passed to Shop.objects.create below).
        s3_folder_name = allocate_s3_folder_name(name, place)
        shop_image = request.FILES.get("image")
        if shop_image:
            try:
                img_url = upload_image_to_s3_with_resize(
                    file=shop_image,
                    path_prefix=f"{s3_folder_name}/shop_images"
                )
            except Exception:
                logger.exception("Image upload failed during shop creation")
                return Response({"error": "Image upload failed. Please try again."}, status=400)

        # --- STEP 5: Atomic DB writes — all succeed or all rollback ---
        with transaction.atomic():
            # Resolve org INSIDE the atomic block so any newly created org is
            # rolled back automatically if the subsequent Shop/User creation fails.
            org, org_error = resolve_organization(
                organization_id=organization_id,
                organization_name=organization_name,
                org_extra_fields=org_extra_fields,
            )
            if org_error:
                return Response({"error": org_error}, status=400)

            shop = Shop.objects.create(
                name=name,
                place=place,
                phone=data.get("shop_phone"),
                phone2=data.get("shop_phone2"),
                address=data.get("shop_address"),
                gst_number=data.get("gst_number"),
                email=data.get("shop_email"),
                city=data.get("shop_city"),
                state=data.get("shop_state"),
                pincode=data.get("shop_pincode"),
                img=img_url,
                s3_folder_name=s3_folder_name,
                terms_and_conditions=data.get("terms_and_conditions"),
                password=shop_password,
                subscription_renewal_price=subscription_renewal_price,
                organization=org,
                show_prices_in_public_listing=data.get(
                    "show_prices_in_public_listing", True
                ) in [True, "true", "True", 1, "1"],
            )

            # booking_start_id/sale_start_id/tailor_start_id live on
            # ShopNumberingSequence (source of truth), not on Shop - Shop's
            # own columns for these are legacy/unused. Shop.save()'s
            # _ensure_product_code_prefix() hook already created a bare
            # ShopNumberingSequence row for this shop above; this fills in
            # the three start-id values the caller provided.
            ShopNumberingSequence.objects.update_or_create(
                shop=shop,
                defaults={
                    "booking_start_id": booking_start_id,
                    "sale_start_id": sale_start_id,
                    "tailor_start_id": tailor_start_id,
                },
            )

            user = user_serializer.save()

            ShopUser.objects.create(shop=shop, user=user, role=user.role)

            Account.objects.bulk_create([
                Account(shop=shop, name="Cash in Hand", account_type="cash", is_default=True, is_active=True),
                Account(shop=shop, name="Default Bank Account", account_type="bank", is_default=False, is_active=True),
            ])

        return Response({
            "message": "User, shop, and link created successfully.",
            "user": UserSerializer(user).data,
            "shop": {
                "id": shop.id,
                "name": shop.name,
                "place": shop.place,
                "img": img_url,
                "organization": {"id": org.id, "name": org.name, "slug": org.slug, "public_token": org.public_token} if org else None,
            }
        }, status=201)

    except Exception as e:
        logger.exception("ShopUser creation failed")
        if img_url:
            self._cleanup_s3_image(img_url)
        return Response(
            {"error": "An unexpected error occurred. Please try again later."},
            status=500
        )
