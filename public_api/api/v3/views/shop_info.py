from rest_framework.views import APIView
from drf_spectacular.utils import (
    OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer,
)
from rest_framework import serializers
from services.models import ShopOwnerService
from services.api.v3.serializers.service import ShopOwnerServiceSerializer
from ERP.utils.mixins import StandardResponseMixin
from public_api.api.v3.utils import resolve_public_token
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Public Shop API"],
    summary="Get public shop info and services",
    description=(
        "Returns basic shop details and all active service categories for a publicly listed shop.\n\n"
        "Purpose: Provides everything needed to render the public shop landing page — "
        "shop name, contact details, address, and the list of service categories available.\n\n"
        "Context: Called when a customer scans the shop's QR code or opens the shared public URL. "
        "Use the id from each returned service as the service_id query parameter when calling "
        "the products API.\n\n"
        "No authentication is required. The shop is identified entirely by the public_token "
        "embedded in the URL path.\n\n"
        "Accepted inputs:\n"
        "Path parameters (required):\n"
        "- public_token: 10-character nanoid that uniquely identifies the shop for public access."
    ),
    parameters=[
        OpenApiParameter(
            name="public_token",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Uniquely identifies the shop for public access without exposing the "
                "internal shop database ID. "
                "Location: URL path parameter. "
                "Format/Type: string (10-character nanoid, e.g. X7h29LmQa3). "
                "Required."
            ),
        ),
    ],
    responses={
        200: inline_serializer(
            name="PublicShopInfoResponse",
            fields={
                "status": serializers.CharField(),
                "message": serializers.CharField(
                    help_text="Shop info retrieved successfully."
                ),
                "data": inline_serializer(
                    name="PublicShopInfoData",
                    fields={
                        "shop": inline_serializer(
                            name="PublicShopDetail",
                            fields={
                                "name": serializers.CharField(help_text="Shop name."),
                                "phone": serializers.CharField(
                                    allow_null=True,
                                    help_text="Primary contact phone number. May be null.",
                                ),
                                "phone2": serializers.CharField(
                                    allow_null=True,
                                    help_text="Secondary contact phone number. May be null.",
                                ),
                                "address": serializers.CharField(
                                    allow_null=True,
                                    help_text="Street address. May be null.",
                                ),
                                "gst_number": serializers.CharField(
                                    allow_null=True,
                                    help_text="GST registration number. May be null.",
                                ),
                                "img": serializers.CharField(
                                    allow_null=True,
                                    help_text="Shop logo/image URL. May be null.",
                                ),
                                "place": serializers.CharField(
                                    allow_null=True,
                                    help_text="Locality or area name. May be null.",
                                ),
                                "email": serializers.CharField(
                                    allow_null=True,
                                    help_text="Shop email address. May be null.",
                                ),
                                "city": serializers.CharField(
                                    allow_null=True,
                                    help_text="City. May be null.",
                                ),
                                "state": serializers.CharField(
                                    allow_null=True,
                                    help_text="State. May be null.",
                                ),
                                "pincode": serializers.CharField(
                                    allow_null=True,
                                    help_text="Postal/ZIP code. May be null.",
                                ),
                            },
                        ),
                        "associated_shops": serializers.ListField(
                            child=inline_serializer(
                                name="PublicOrgShopItem",
                                fields={
                                    "id": serializers.IntegerField(),
                                    "name": serializers.CharField(),
                                    "place": serializers.CharField(allow_null=True),
                                    "address": serializers.CharField(allow_null=True),
                                    "phone": serializers.CharField(allow_null=True),
                                    "phone2": serializers.CharField(allow_null=True),
                                },
                            ),
                            help_text=(
                                "Associated active shop branches. "
                                "Populated only for organization tokens; empty list for shop tokens."
                            ),
                        ),
                        "services": serializers.ListField(
                            child=serializers.DictField(),
                            help_text=(
                                "Active service categories for the shop. "
                                "Each item: id, service_name, description, icon, "
                                "main_service_name, display_order."
                            ),
                        ),
                    },
                ),
            },
        ),
        404: OpenApiResponse(
            description=(
                "Not found. Exact message: Shop not found or public access is disabled."
            )
        ),
    },
)
class PublicShopInfoView(StandardResponseMixin, APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, public_token):
        token_type, token_obj = resolve_public_token(public_token)
        if token_type is None:
            return self.not_found_response(resource="Shop")

        if token_type == "shop":
            shop = token_obj
            services = (
                ShopOwnerService.objects.filter(shop=shop, is_active=True)
                .select_related("general_service", "general_service__main_category")
                .order_by("display_order", "id")
            )
            shop_data = {
                "name": shop.name,
                "phone": shop.phone,
                "phone2": shop.phone2,
                "address": shop.address,
                "gst_number": shop.gst_number,
                "img": shop.img,
                "place": shop.place,
                "email": shop.email,
                "city": shop.city,
                "state": shop.state,
                "pincode": shop.pincode,
                "slug": shop.slug,
                "public_token": shop.public_token,
            }
            services_data = ShopOwnerServiceSerializer(services, many=True).data
            shops_list = []

        else:  # org
            org = token_obj
            shop_data = {
                "name": org.name,
                "phone": org.phone,
                "phone2": org.phone2,
                "address": org.street,
                "gst_number": org.gst_number,
                "img": org.image,
                "place": org.place,
                "email": org.email,
                "city": org.city,
                "state": org.state,
                "pincode": org.pincode,
                "slug": org.slug,
                "public_token": org.public_token,
            }
            org_shops_qs = org.shops.filter(
                is_active=True, show_in_org_catalog=True
            ).only("id", "name", "place", "address", "phone", "phone2").order_by("catalog_display_order", "id")

            shops_list = [
                {
                    "id": s.id,
                    "name": s.name,
                    "place": s.place,
                    "address": s.address,
                    "phone": s.phone,
                    "phone2": s.phone2,
                }
                for s in org_shops_qs
            ]

            shop_ids = [s["id"] for s in shops_list]
            services_qs = (
                ShopOwnerService.objects
                .filter(shop_id__in=shop_ids, is_active=True)
                .select_related("general_service", "general_service__main_category")
                .order_by("display_order", "id")
            )
            # Group by general_service to deduplicate across shops.
            # Queryset is ordered by display_order so first-seen value is the lowest.
            grouped = {}
            for s in services_qs:
                gs = s.general_service
                if gs is None:
                    continue
                gid = gs.id
                if gid not in grouped:
                    grouped[gid] = {
                        "service_ids": [],
                        "service_name": gs.name,
                        "description": gs.description,
                        "icon": gs.icon,
                        "main_service_name": gs.main_category.name if gs.main_category else None,
                        "display_order": s.display_order,
                    }
                grouped[gid]["service_ids"].append(s.id)
            services_data = list(grouped.values())

        return self.success_response(
            data={"shop": shop_data, "associated_shops": shops_list, "services": services_data},
            message="Shop info retrieved successfully.",
        )
