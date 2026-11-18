import logging

from django.db import transaction
from django.db.models import Prefetch
from rest_framework.decorators import action
from rest_framework import serializers
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer

from booking_v2.models import Client
from sales.models import Sale, SaleItem
from shop.models import Staff

from ...utils.validators import validate_amounts, validate_sale_date, verify_variant_belongs_to_shop
from ...utils.client import get_or_create_client
from ...utils.stock import calculate_available_stock

logger = logging.getLogger('api_requests')


class SaleUpdateMixin:

    @extend_schema(
        tags=["Sales Management V3"],
        summary="Update sale client",
        description="Updates client details attached to a sale, using either existing client_id or new client details.",
        parameters=[
            OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Sale id."),
        ],
        request=inline_serializer(
            name="SalesUpdateClientV3Request",
            fields={
                "client_id": serializers.IntegerField(required=False, help_text="Existing client id."),
                "client_name": serializers.CharField(required=False, help_text="Client name when creating/finding client."),
                "client_phone_1": serializers.CharField(required=False, help_text="Primary client phone for create/find flow."),
                "client_phone_2": serializers.CharField(required=False, allow_blank=True),
                "client_address": serializers.CharField(required=False, allow_blank=True),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message: Client information updated successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages can include:\n"
                    "- Validation error\n"
                    "- Client name and phone number are required.\n"
                    "- Invalid phone number format."
                )
            ),
            404: OpenApiResponse(description="Exact messages can include: The requested resource was not found / The information you're looking for is not available"),
            500: OpenApiResponse(description="Exact message: Unable to update client information."),
        },
        examples=[
            OpenApiExample("Request example", value={"client_id": 22, "client_address": "Andheri East"}, request_only=True),
            OpenApiExample("Success response", value={"status": "success", "message": "Client information updated successfully.", "data": {"sale_id": 120}}, response_only=True, status_codes=["200"]),
            OpenApiExample("Validation response", value={"status": "validation_error", "message": "Please check your input and try again", "data": {"validation_errors": {"client": ["Client name and phone number are required."]}}}, response_only=True, status_codes=["400"]),
            OpenApiExample("Not found response", value={"status": "not_found", "message": "The requested resource was not found", "data": None}, response_only=True, status_codes=["404"]),
        ],
    )
    @transaction.atomic
    @action(detail=False, methods=["patch"], url_path=r"update-client/(?P<pk>\d+)")
    def update_client(self, request, pk=None):
        """Update client information for a sale"""
        try:
            shop_id = self.shop_id
            sale = Sale.objects.filter(shop_id=shop_id, id=pk).first()
            if not sale:
                return self.not_found_response("Sale not found.")

            data = request.data
            client_id = data.get("client_id")

            if client_id:
                # Use existing client
                client = Client.objects.filter(id=client_id, shop_id=shop_id).first()
                if not client:
                    return self.client_not_found_response()
                sale.client = client
            else:
                # Create or update client details
                client_name = data.get("client_name", "").strip()
                client_phone_1 = data.get("client_phone_1")

                if not client_name or not client_phone_1:
                    return self.validation_error_response({
                        "client": ["Client name and phone number are required."]
                    })

                client_data = {
                    "client_name": client_name,
                    "client_phone_1": client_phone_1,
                    "client_phone_2": data.get("client_phone_2"),
                }
                client, _ = get_or_create_client(shop_id, client_data)
                if not client:
                    return self.validation_error_response({
                        "client_phone_1": ["Invalid phone number format."]
                    })
                sale.client = client

            # Update address if provided
            if "client_address" in data:
                sale.address = data["client_address"]

            sale.save()

            return self.success_response(
                data={"sale_id": sale.id},
                message="Client information updated successfully."
            )

        except Exception as e:
            logger.error(f"Error updating client for sale {pk} in shop {shop_id}: {e}", exc_info=True)
            return self.error_response("Unable to update client information.")

    @extend_schema(
        tags=["Sales Management V3"],
        summary="Update sale variants",
        description="Replaces or adjusts sale line items with stock checks and total recalculation.",
        parameters=[OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Sale id.")],
        request=inline_serializer(
            name="SalesUpdateVariantsV3Request",
            fields={
                "variants": inline_serializer(
                    name="SalesUpdateVariantV3Item",
                    many=True,
                    fields={
                        "id": serializers.IntegerField(required=True),
                        "quantity": serializers.IntegerField(required=False, default=1),
                        "amount": serializers.FloatField(required=False),
                    },
                ),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message: Sale items updated successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages can include:\n"
                    "- No variants provided\n"
                    "- Quantity must be greater than zero\n"
                    "- New total cannot be less than the paid amount."
                )
            ),
            404: OpenApiResponse(description="Exact messages can include: The requested resource was not found / Product not found in your inventory"),
            500: OpenApiResponse(description="Exact message: Unable to update sale items."),
        },
        examples=[
            OpenApiExample("Request example", value={"variants": [{"id": 11, "quantity": 2, "amount": 1500}]}, request_only=True),
            OpenApiExample("Success response", value={"status": "success", "message": "Sale items updated successfully.", "data": {"sale_id": 120}}, response_only=True, status_codes=["200"]),
            OpenApiExample("Validation response", value={"status": "validation_error", "message": "No variants provided", "data": {"validation_errors": {"variants": ["No variants provided"]}}}, response_only=True, status_codes=["400"]),
        ],
    )
    @transaction.atomic
    @action(detail=False, methods=["patch"], url_path=r"update-variants/(?P<pk>\d+)")
    def update_variants(self, request, pk=None):
        """Update sale items (variants) after validating stock"""
        try:
            shop_id = self.shop_id
            sale = (
                Sale.objects.filter(shop_id=shop_id, id=pk)
                .prefetch_related(
                    Prefetch("items", queryset=SaleItem.objects.select_related("product_variant"))
                )
                .first()
            )
            if not sale:
                return self.not_found_response("Sale not found.")

            new_variants = request.data.get("variants", [])
            if not new_variants:
                return self.validation_error_response(
                    errors={"variants": ["No variants provided"]},
                    message="No variants provided"
                )

            decrease_stock = sale.decrease_stock

            old_items = {item.product_variant.id: item for item in sale.items.all()}
            new_variants_map = {v["id"]: v for v in new_variants}

            # --- Step 1: Validate all new variants and stock ---
            stock_changes = {}
            variant_objects = {}
            for variant_id, new_v in new_variants_map.items():
                quantity = new_v.get("quantity", 1)
                price = new_v.get("amount", None)

                if quantity <= 0:
                    return self.validation_error_response(
                        errors={"quantity": ["Quantity must be greater than zero"]},
                        message="Quantity must be greater than zero"
                    )

                from services.models import ProductVariant
                variant = ProductVariant.objects.select_for_update().filter(
                    id=variant_id,
                    product__shop_service__shop_id=shop_id,
                    is_active=True
                ).first()
                if not variant:
                    return self.product_not_found_response()
                variant_objects[variant_id] = variant

                old_item = old_items.get(variant_id)
                diff = quantity - (old_item.quantity if old_item else 0)

                if decrease_stock and diff > 0:
                    available = calculate_available_stock(variant)
                    if diff > available:
                        return self.insufficient_stock_response(
                            product_name=f"{variant.product.name} - {variant.attribute}",
                            available=available,
                            requested=diff,
                            message="Product have upcoming bookings, cannot reduce stock"
                        )

                stock_changes[variant_id] = diff

            # --- Step 2: Apply changes (delete old items, create/update new items, adjust stock) ---
            for variant_id, old_item in old_items.items():
                if variant_id not in new_variants_map:
                    if decrease_stock:
                        old_item.product_variant.stock += old_item.quantity
                        old_item.product_variant.save(update_fields=["stock"])
                    old_item.delete()

            # Create/update new items
            from sales.models import SaleItem as _SaleItem
            for variant_id, new_v in new_variants_map.items():
                quantity = new_v.get("quantity", 1)
                price = new_v.get("amount", None)
                variant = variant_objects[variant_id]
                old_item = old_items.get(variant_id)
                diff = stock_changes[variant_id]

                if old_item:
                    if decrease_stock:
                        variant.stock -= diff
                        variant.save(update_fields=["stock"])

                    old_item.quantity = quantity
                    if price is not None:
                        old_item.price = price
                    old_item.subtotal = old_item.quantity * old_item.price
                    old_item.save()
                else:
                    subtotal = quantity * (price or variant.price)
                    _SaleItem.objects.create(
                        sale=sale,
                        product_variant=variant,
                        quantity=quantity,
                        price=price or variant.price,
                        subtotal=subtotal
                    )
                    if decrease_stock:
                        variant.stock -= quantity
                        variant.save(update_fields=["stock"])

            # --- Step 3: Recalculate totals ---
            total = sum(item.quantity * item.price for item in sale.items.all())
            if total < sale.paid_amount:
                return self.validation_error_response(
                    message="New total cannot be less than the paid amount."
                )

            sale.total_amount = total
            sale.paid_amount = min(sale.paid_amount, total)
            sale.save(update_fields=["total_amount", "paid_amount"])

            return self.success_response(
                data={"sale_id": sale.id},
                message="Sale items updated successfully."
            )

        except Exception as e:
            logger.error(f"Error updating sale items for sale {pk} in shop {shop_id}: {e}", exc_info=True)
            return self.error_response("Unable to update sale items.")

    @extend_schema(
        tags=["Sales Management V3"],
        summary="Update sale basic fields",
        description="Partially updates sale description, date, and discount.",
        parameters=[OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Sale id.")],
        request=inline_serializer(
            name="SalesPartialUpdateV3Request",
            fields={
                "description": serializers.CharField(required=False, allow_blank=True),
                "sale_date": serializers.CharField(required=False, help_text="Formats: YYYY-MM-DD or DD-MM-YYYY."),
                "discount": serializers.FloatField(required=False),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message: Sale updated successfully."),
            400: OpenApiResponse(description="Validation errors from sale_date/discount validators. Exact messages include Invalid date format and discount/paid amount constraint messages."),
            404: OpenApiResponse(description="Exact message: The requested resource was not found"),
            500: OpenApiResponse(description="Exact message: Unable to update sale."),
        },
        examples=[
            OpenApiExample("Request example", value={"description": "Updated note", "discount": 150}, request_only=True),
            OpenApiExample("Success response", value={"status": "success", "message": "Sale updated successfully.", "data": {"sale_id": 120}}, response_only=True, status_codes=["200"]),
            OpenApiExample("Validation response", value={"status": "validation_error", "message": "Discount cannot exceed total sale amount.", "data": {"validation_errors": {"discount": ["Discount cannot exceed total sale amount."]}}}, response_only=True, status_codes=["400"]),
        ],
    )
    @transaction.atomic
    def partial_update(self, request, pk=None):
        """Update sale basic information"""
        try:
            shop_id = self.shop_id
            sale = Sale.objects.filter(shop_id=shop_id, id=pk).first()
            if not sale:
                return self.not_found_response("Sale not found.")

            data = request.data

            # Update basic fields
            if "description" in data:
                sale.description = data["description"]

            if "sale_date" in data:
                # Validate sale_date
                sale_date, error_msg = validate_sale_date(data["sale_date"])
                if error_msg:
                    return self.validation_error_response(
                        errors={"sale_date": [error_msg]},
                        message=error_msg
                    )
                sale.sale_date = sale_date

            if "discount" in data:
                discount_amount = data["discount"]
                # Validate new discount
                is_valid, error_msg = validate_amounts(sale.total_amount, sale.paid_amount, discount_amount)
                if not is_valid:
                    return self.validation_error_response(
                        errors={"discount": [error_msg]},
                        message=error_msg
                    )
                sale.discount = discount_amount

            sale.save()

            return self.success_response(
                data={"sale_id": sale.id},
                message="Sale updated successfully."
            )

        except Exception as e:
            logger.error(f"Error updating sale {pk} for shop {shop_id}: {e}", exc_info=True)
            return self.error_response("Unable to update sale.")

    @extend_schema(
        tags=["Sales Management V3"],
        summary="Update sale (combined)",
        description="Combined endpoint that can update client, staff, variants, discount, and basic sale details in one request.",
        parameters=[OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Sale id.")],
        request=inline_serializer(
            name="SalesUpdateSaleV3Request",
            fields={
                "staff_id": serializers.IntegerField(required=False),
                "client_id": serializers.IntegerField(required=False),
                "client_name": serializers.CharField(required=False),
                "client_phone_1": serializers.CharField(required=False),
                "client_phone_2": serializers.CharField(required=False, allow_blank=True),
                "variants": inline_serializer(
                    name="SalesUpdateSaleV3Variant",
                    many=True,
                    required=False,
                    fields={
                        "id": serializers.IntegerField(required=True),
                        "quantity": serializers.IntegerField(required=False, default=1),
                        "amount": serializers.FloatField(required=False),
                    },
                ),
                "discount": serializers.FloatField(required=False),
                "description": serializers.CharField(required=False, allow_blank=True),
                "client_address": serializers.CharField(required=False, allow_blank=True),
                "sale_date": serializers.CharField(required=False),
                "payment_method": serializers.CharField(required=False),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message: Sale updated successfully."),
            400: OpenApiResponse(description="Validation errors from client/staff/variant/date/discount guards. Exact messages mirror internal helpers used by this endpoint."),
            404: OpenApiResponse(description="Exact messages can include: The requested resource was not found / The information you're looking for is not available / Product not found in your inventory"),
            500: OpenApiResponse(description="Exact message: Unable to update sale."),
        },
        examples=[
            OpenApiExample("Request example", value={"staff_id": 3, "discount": 120, "variants": [{"id": 11, "quantity": 1, "amount": 1500}]}, request_only=True),
            OpenApiExample("Success response", value={"status": "success", "message": "Sale updated successfully.", "data": {"sale_id": 120, "staff_updated": True, "basic_info_updated": True}}, response_only=True, status_codes=["200"]),
            OpenApiExample("Staff validation response", value={"status": "validation_error", "message": "Staff not found.", "data": {"validation_errors": {"staff_id": ["Staff not found for this shop."]}}}, response_only=True, status_codes=["400"]),
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
                return self.not_found_response("Sale not found.")

            sale_items = (
                SaleItem.objects
                .select_for_update()
                .filter(sale=sale)
                .select_related("product_variant")
            )
            old_items = {item.product_variant.id: item for item in sale_items}

            data = request.data
            response_data = {"sale_id": sale.id}

            staff = None
            # Optional: Validate staff if provided
            if "staff_id" in data:
                staff_id = data.get("staff_id")
                try:
                    staff = Staff.objects.get(id=staff_id, shop=shop_id)
                    sale.staff = staff
                    sale.save(update_fields=["staff"])
                    response_data["staff_updated"] = True
                except Staff.DoesNotExist:
                    return self.validation_error_response(
                        errors={"staff_id": ["Staff not found for this shop."]},
                        message="Staff not found."
                    )

            # --- Handle client update (new or existing) ---
            if "client_id" in data or "client_name" in data:
                client = None
                if data.get("client_id"):
                    client = Client.objects.filter(id=data["client_id"], shop_id=shop_id).first()
                    if not client:
                        return self.client_not_found_response()
                else:
                    client_name = data.get("client_name", "").strip()
                    client_phone_1 = data.get("client_phone_1")
                    client_phone_2 = data.get("client_phone_2")

                    if not client_name or not client_phone_1:
                        return self.validation_error_response(
                            errors={"client": ["Please provide client name and phone number."]},
                            message="Client information is required",
                            dev_message="Missing client_name or client_phone_1 in update request"
                        )

                    client_data = {
                        "client_name": client_name,
                        "client_phone_1": client_phone_1,
                        "client_phone_2": client_phone_2,
                    }
                    client, _ = get_or_create_client(shop_id, client_data)
                    if not client:
                        return self.validation_error_response(
                            errors={"client": ["Invalid client details provided."]},
                            message="Client information is required"
                        )

                # Assign new client to sale
                sale.client = client
                sale.save(update_fields=["client"])
                response_data["client_updated"] = True

            # --- Handle variants update ---
            if "variants" in data or "discount" in data:
                variants_input = data.get("variants", [
                    {"id": vid, "quantity": item.quantity, "amount": item.price}
                    for vid, item in old_items.items()
                ])
                variants_result = self._update_variants_logic(
                    sale, old_items,
                    variants_input,
                    payment_method=data.get("payment_method"),
                    new_discount=data.get("discount")
                )
                # variants_result = self._update_variants_logic(
                #     sale, old_items,
                #     data["variants"],
                #     payment_method=data.get("payment_method"),
                #     new_discount=data.get("discount")
                #     )
                if variants_result.get("status") != "success":
                    return variants_result
                response_data.update(variants_result.data.get("data", {}))

            # --- Handle basic info update ---
            basic_result = self._update_basic_info_logic(sale, data)
            if basic_result.get("status") != "success":
                return basic_result
            response_data.update(basic_result.data.get("data", {}))

            return self.success_response(
                data=response_data,
                message="Sale updated successfully."
            )

        except Exception as e:
            logger.error(f"Error updating sale {pk} for shop {shop_id}: {e}", exc_info=True)
            return self.error_response("Unable to update sale.")
