import logging

from django.core.exceptions import PermissionDenied
from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from services.models import ShopOwnerService
from shop.api.v3.utils.shop import get_active_shop_id
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

logger = logging.getLogger('api_requests')


class ReorderServicesView(StandardResponseMixin, APIView):
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]
    authentication_classes = [CutoffJWTAuthentication]

    @extend_schema(
        tags=["Service & Product Management V3"],
        summary="Reorder shop services",
        description=(
            "Sets the display order of the active shop's services. "
            "Pass an ordered list of ShopOwnerService IDs — position in the list becomes the display_order. "
            "All provided IDs must belong to the active shop; any foreign or invalid ID returns a 400 error. "
            "Services omitted from the list retain their existing display_order."
        ),
        request=inline_serializer(
            name="ReorderServicesRequest",
            fields={
                "service_ids": serializers.ListField(
                    child=serializers.IntegerField(),
                    help_text="Ordered list of ShopOwnerService IDs. First item gets display_order=0.",
                )
            },
        ),
        responses={
            200: OpenApiResponse(description="Standard success envelope confirming reorder."),
            400: OpenApiResponse(description="service_ids is missing or not a non-empty list."),
            403: OpenApiResponse(description="You do not have permission to access this shop."),
            404: OpenApiResponse(description="One or more service IDs do not belong to the active shop."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                value={"service_ids": [5, 2, 8, 1]},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={"status": "success", "message": "Service order updated.", "data": None},
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Validation error — empty list",
                value={"status": "validation_error", "message": "Validation failed.", "errors": {"service_ids": ["Required non-empty list."]}},
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Service not found",
                value={"status": "not_found", "message": "Service not found.", "dev_message": "99 does not belong to this shop; 100 does not belong to this shop", "data": None},
                response_only=True,
                status_codes=["404"],
            ),
        ],
    )
    def patch(self, request):
        try:
            shop_id = get_active_shop_id(request)
        except PermissionDenied:
            return self.permission_denied_response(
                message="You do not have permission to access this shop."
            )

        service_ids = request.data.get("service_ids")
        if not isinstance(service_ids, list) or not service_ids:
            return self.validation_error_response(
                errors={"service_ids": ["Required non-empty list."]}
            )

        if len(service_ids) > 100:
            return self.validation_error_response(
                errors={"service_ids": ["Cannot reorder more than 100 services at once."]}
            )

        with transaction.atomic():
            records = ShopOwnerService.objects.select_for_update().filter(
                shop_id=shop_id, is_active=True, id__in=service_ids
            )
            id_to_record = {r.id: r for r in records}

            invalid_ids = [sid for sid in service_ids if sid not in id_to_record]
            if invalid_ids:
                dev_msg = "; ".join(f"{sid} does not belong to the shop." for sid in invalid_ids)
                return self.not_found_response(
                    message="Service not found.",
                    dev_message=dev_msg,
                )

            to_update = []
            for order, sid in enumerate(service_ids):
                record = id_to_record[sid]
                record.display_order = order
                to_update.append(record)

            ShopOwnerService.objects.bulk_update(to_update, ["display_order"])

        logger.info(
            f"Services reordered for shop {shop_id}: {service_ids}",
            extra={"shop_id": shop_id, "service_ids": service_ids},
        )
        return self.success_response(message="Service order updated.")
