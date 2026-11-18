from rest_framework.response import Response
from rest_framework import status
from django.db.models import Q
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from shop.models import Shop, Organization
from shop.api.admin_v1.serializers import ShopSerializer
from booking_v2.models import BookingV2
from sales.models import Sale
from tailored.models import TailorOrder
from purchase.models import Purchase
from PIL import Image
import boto3
import uuid
from io import BytesIO
from django.conf import settings
from ERP.utils.s3_cache_control import IMAGE_CACHE_CONTROL


@extend_schema(
    tags=["Shop Admin Management V1"],
    summary="Update shop",
    description=(
        "Partially updates a shop with uniqueness checks and optional image upload to S3.\n\n"
        "- show_prices_in_public_listing (boolean, optional): Whether product prices are "
        "visible in this shop's public listing."
    ),
    parameters=[
        OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Shop id."),
    ],
    request=inline_serializer(
        name="ShopAdminV1PatchShopRequest",
        fields={
            "name": serializers.CharField(required=False),
            "place": serializers.CharField(required=False),
            "booking_start_id": serializers.IntegerField(required=False),
            "sale_start_id": serializers.IntegerField(required=False),
            "purchase_start_id": serializers.IntegerField(required=False),
            "img": serializers.ImageField(required=False),
            "organization_id": serializers.IntegerField(
                required=False,
                allow_null=True,
                help_text=(
                    "ID of an existing active organization to associate this shop with. "
                    "Pass null to remove the current organization link."
                ),
            ),
            "show_prices_in_public_listing": serializers.BooleanField(
                required=False,
                help_text="Whether product prices are visible in this shop's public listing.",
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Shop updated successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Cannot modify booking invoice start id once the shop has existing bookings.\n"
                "- Cannot modify sale invoice start id once the shop has existing sales.\n"
                "- Cannot modify purchase invoice start id once the shop has existing purchases.\n"
                "- A shop with name '{check_name}' already exists in '{check_place}'.\n"
                "- Image must be less than 3MB.\n"
                "- Invalid or unsupported image format.\n"
                "- 'organization_id' must be a valid integer.\n"
                "- Organization not found or inactive.\n"
                "- Serializer errors returned in error field."
            )
        ),
        404: OpenApiResponse(description="Exact message: Shop not found."),
    },
    examples=[
        OpenApiExample("Request — update name", value={"name": "Prime Tailors Updated", "place": "Kochi"}, request_only=True),
        OpenApiExample("Request — link existing org", value={"organization_id": 1}, request_only=True),
        OpenApiExample("Request — remove org", value={"organization_id": None}, request_only=True),
        OpenApiExample("Success response", value={"message": "Shop updated successfully.", "data": {"id": 4, "name": "Prime Tailors Updated", "organization": {"id": 1, "name": "Royal Wedding Rentals"}}}, response_only=True, status_codes=["200"]),
    ],
)
def partial_update(self, request, pk=None):
    try:
        shop = Shop.objects.select_related("organization").get(pk=pk)
    except Shop.DoesNotExist:
        return Response({"error": "Shop not found."}, status=status.HTTP_404_NOT_FOUND)

    data = request.data.copy()

    name = data.get("name", "").strip()
    place = data.get("place", "").strip()

    # --- Resolve organization (optional, only when key(s) present in request) ---
    org = shop.organization  # default: keep existing

    if "organization_id" in data or "organization_name" in data:
        organization_id = data.get("organization_id") or None   # empty string / null → None
        data.pop("organization_id", None)
        data.pop("organization_name", None)   # creating a new org via partial_update is not allowed

        if organization_id is None:
            org = None   # explicit removal
        else:
            try:
                org_id = int(organization_id)
            except (ValueError, TypeError):
                return Response({"error": "'organization_id' must be a valid integer."}, status=status.HTTP_400_BAD_REQUEST)
            try:
                org = Organization.objects.get(pk=org_id, is_active=True)
            except Organization.DoesNotExist:
                return Response({"error": "Organization not found or inactive."}, status=status.HTTP_400_BAD_REQUEST)

    if "booking_start_id" in data:
        if BookingV2.objects.filter(shop=shop).exists():
            return Response(
                {"error": "Cannot modify booking invoice start id once the shop has existing bookings."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # Prevent changing sale_start_id if the shop already has sales
    if "sale_start_id" in data:
        if Sale.objects.filter(shop=shop).exists():
            return Response(
                {"error": "Cannot modify sale invoice start id once the shop has existing sales."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
    if "tailor_start_id" in data:
        if TailorOrder.objects.filter(shop=shop).exists():
            return Response(
                {"error": "Cannot modify Custom work invoice start id once the shop has existing custom work."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    if "purchase_start_id" in data:
        if Purchase.objects.filter(shop=shop).exists():
            return Response(
                {"error": "Cannot modify purchase invoice start id once the shop has existing purchases."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # Determine if uniqueness check is needed
    should_check_uniqueness = "name" in data or "place" in data

    # Use fallback from existing object if not in request
    check_name = name if name else shop.name
    check_place = place if place else shop.place

    if should_check_uniqueness:
        exists = Shop.objects.all().filter(
            ~Q(id=shop.id),
            name__iexact=check_name,
            place__iexact=check_place
        ).exists()
        if exists:
            return Response(
                {"error": f"A shop with name '{check_name}' already exists in '{check_place}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # Handle image file
    image_file = request.FILES.get("img")
    img_url = None

    if image_file:
        if image_file.size > 3 * 1024 * 1024:
            return Response({"error": "Image must be less than 3MB."}, status=400)

        try:
            img = Image.open(image_file)
            img = img.convert("RGBA")
        except Exception:
            return Response({"error": "Invalid or unsupported image format."}, status=400)

        # Stored folder, not the (possibly new) name - a rename must not start a new folder.
        filename = f"{shop.s3_folder}/shop_images/{uuid.uuid4().hex}.png"

        img_io = BytesIO()
        img.save(img_io, format="PNG")
        img_io.seek(0)

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

    data.pop("img", None)

    serializer = ShopSerializer(shop, data=data, partial=True)
    if serializer.is_valid():
        shop = serializer.save(img=img_url or shop.img, organization=org)
        shop = Shop.objects.select_related("organization").get(pk=shop.pk)
        return Response({
            "message": "Shop updated successfully.",
            "data": ShopSerializer(shop).data
        }, status=status.HTTP_200_OK)
    else:
        return Response({"error": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
