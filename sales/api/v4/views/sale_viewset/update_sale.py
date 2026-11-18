import logging

from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import action

from ERP.utils.shop_activity_logs import create_activity_log
from sales.models import Sale, SaleItem
from shop.models import Staff

logger = logging.getLogger('api_requests')


class SaleUpdateSaleMixin:

    @extend_schema(
        tags=["Sales Management V4"],
        summary="Update sale (combined)",
        description="Unified endpoint to update staff, phone fields, variants, discount, account routing, and basic metadata in one request.",
        parameters=[OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Sale id.")],
        request=inline_serializer(
            name="SalesV4UpdateSaleRequest",
            fields={
                "staff_id": serializers.IntegerField(required=False),
                "client_phone": serializers.CharField(required=False),
                "client_phone_e164": serializers.CharField(required=False),
                "variants": inline_serializer(
                    name="SalesV4UpdateSaleVariant",
                    many=True,
                    required=False,
                    fields={
                        "id": serializers.IntegerField(required=True),
                        "quantity": serializers.IntegerField(required=False, default=1),
                        "amount": serializers.FloatField(required=False),
                    },
                ),
                "discount": serializers.FloatField(required=False),
                "paid_amount": serializers.FloatField(required=False, help_text="If omitted, defaults to the final payable amount (total_amount - discount) as before. If provided, honored as-is (e.g. for partial payments), subject to the same validation as Create."),
                "payment_method": serializers.CharField(required=False),
                "account_id": serializers.IntegerField(required=False),
                "description": serializers.CharField(required=False, allow_blank=True),
                "client_address": serializers.CharField(required=False, allow_blank=True),
                "sale_date": serializers.CharField(required=False),
                "send_invoice": serializers.BooleanField(required=False),
            },
        ),
        responses={
            200: OpenApiResponse(description="Success message starts with: Sale updated successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages can include:\n"
                    "- Staff not found.\n"
                    "- Staff not found for this shop.\n"
                    "- Product not found.\n"
                    "- The selected product is no longer available and cannot be used for a sale.\n"
                    "- Quantity must be greater than zero.\n"
                    "- Discount cannot exceed total sale amount.\n"
                    "- Final amount cannot be negative after discount.\n"
                    "- Invalid account selected.\n"
                    "- Invalid or inactive account for this shop.\n"
                    "- No variants provided"
                )
            ),
            404: OpenApiResponse(description="Exact message: Sale not found."),
            500: OpenApiResponse(description="Exact message: Unable to update sale."),
        },
        examples=[
            OpenApiExample("Request example", value={"staff_id": 3, "client_phone": "9876500000", "variants": [{"id": 11, "quantity": 1, "amount": 1500}], "discount": 80, "account_id": 2, "send_invoice": True}, request_only=True),
            OpenApiExample("Success response", value={"status": "success", "message": "Sale updated successfully. Invoice queued for sending.", "data": {"sale_id": 55, "staff_updated": True, "client_phone_updated": True, "variants_updated": True, "basic_info_updated": True}}, response_only=True, status_codes=["200"]),
            OpenApiExample("Staff error response", value={"status": "validation_error", "message": "Staff not found.", "data": {"validation_errors": {"staff_id": ["Staff not found for this shop."]}}}, response_only=True, status_codes=["400"]),
        ],
    )
    @transaction.atomic
    @action(detail=False, methods=["patch"], url_path=r"update-sale/(?P<pk>\d+)")
    def update_sale(self, request, pk=None):
        """Update sale (basic info and/or variants) in a single API"""
        try:
            shop_id = self.shop_id
            sale = (
                Sale.objects
                .select_for_update()
                .filter(shop_id=shop_id, id=pk)
                .first()
            )
            if not sale:
                return self.not_found_response(message="Sale not found.")

            sale_items = (
                SaleItem.objects
                .select_for_update()
                .filter(sale=sale)
                .select_related("product_variant")
            )
            old_items = {item.product_variant.id: item for item in sale_items}

            data = request.data

            # Reject newly added variants that are soft-deleted or whose product is
            # soft-deleted. Runs before any sale.save() since early returns don't
            # roll back. Items already on the sale are left as-is.
            incoming_variants = data.get("variants")
            if isinstance(incoming_variants, list) and incoming_variants:
                new_variant_ids = []
                for v in incoming_variants:
                    vid = v.get("id") if isinstance(v, dict) else None
                    try:
                        if int(vid) in old_items:
                            continue
                    except (TypeError, ValueError):
                        continue  # _update_variants_logic handles invalid IDs
                    new_variant_ids.append(vid)

                if new_variant_ids:
                    variant_error = self._validate_variant_products(new_variant_ids)
                    if variant_error:
                        return variant_error

            response_data = {"sale_id": sale.id}
            log_changes = []

            # Handle staff update
            if "staff_id" in data:
                staff_id = data.get("staff_id")
                try:
                    staff = Staff.objects.get(id=staff_id, shop=shop_id)
                    sale.staff = staff
                    sale.save(update_fields=["staff"])
                    response_data["staff_updated"] = True
                    log_changes.append(f"assigned staff '{staff.name}'")
                except Staff.DoesNotExist:
                    return self.validation_error_response(
                        errors={"staff_id": ["Staff not found for this shop."]},
                        message="Staff not found."
                    )

            if "client_phone" in data:
                raw_phone = data["client_phone"]
                if raw_phone:
                    client_phone, phone_error = self._validate_client_phone(raw_phone)
                    if phone_error:
                        return self.validation_error_response(
                            errors={"client_phone": [phone_error]},
                            message=phone_error
                        )
                    sale.client_phone = client_phone
                else:
                    sale.client_phone = None
                sale.save(update_fields=["client_phone"])
                response_data["client_phone_updated"] = True
                log_changes.append("updated client phone")

            if "client_phone_e164" in data:
                raw_phone_e164 = data["client_phone_e164"]

                if raw_phone_e164:
                    from ERP.utils.utils import validate_and_format_phone
                    formatted_phone, phone_error = validate_and_format_phone(
                        raw_phone_e164,
                        field_name="client phone"
                    )
                    if phone_error:
                        return self.validation_error_response(
                            errors={"client_phone_e164": [phone_error]},
                            message=phone_error
                        )
                    sale.client_phone_e164 = formatted_phone
                else:
                    sale.client_phone_e164 = None

                sale.save(update_fields=["client_phone_e164"])
                response_data["client_phone_e164_updated"] = True
                log_changes.append("updated client_phone_e164")

            # Handle variants update
            if "variants" in data or "discount" in data or "account_id" in data or "paid_amount" in data:
                variants_input = data.get("variants", [
                    {"id": vid, "quantity": item.quantity, "amount": item.price}
                    for vid, item in old_items.items()
                ])
                variants_result = self._update_variants_logic(
                    sale, old_items,
                    variants_input,
                    payment_method=data.get("payment_method"),
                    new_discount=data.get("discount"),
                    account_id=data.get("account_id"),
                    new_paid_amount=data.get("paid_amount"),
                )

                if variants_result.get("status") != "success":
                    return self.validation_error_response(message=variants_result.get("message", "Variant update failed."))

                response_data.update(variants_result["data"])
                log_changes.append("updated variants and pricing")

            # Handle basic info update
            basic_result = self._update_basic_info_logic(sale, data)

            if basic_result["status"] != "success":
                return self.validation_error_response(
                    errors=basic_result.get("errors"),
                    message=basic_result.get("message")
                )

            response_data.update(basic_result["data"])
            log_changes.append("updated basic info")

            # Create activity log BEFORE returning response
            try:
                create_activity_log(
                    shop=sale.shop,
                    user=request.user,
                    instance=sale,
                    action="updated",
                    message=(
                        f"Changes: {', '.join(log_changes) if log_changes else 'none'}."
                    )
                )
            except Exception as log_error:
                logger.exception(f"Failed to create activity log for update_sale: {log_error}")

            effective_phone = data.get("client_phone_e164") or sale.client_phone_e164
            success_message = self._handle_send_invoice(
                sale, data, request.user, "Sale updated successfully.",
                client_phone_e164=effective_phone
            )
            return self.success_response(data=response_data, message=success_message)

        except Exception as e:
            logger.error(f"Error updating sale {pk}: {e}", exc_info=True)
            return self.error_response("Unable to update sale.")
