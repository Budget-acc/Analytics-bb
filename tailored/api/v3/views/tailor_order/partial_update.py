from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from tailored.models import TailorOrder, TailorOrderItem, TailorOrderAdditionalCharge
from booking_v2.models import Client
from shop.utils.phone_validator import validate_and_normalize_phone
from tailored.api.v3.utils.date_utils import parse_datetime
from ERP.utils.shop_activity_logs import create_activity_log
from shop.api.v3.utils.tax_calculation import resolve_taxable_flags, split_inclusive_amount
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Update tailor order",
    description="Partially updates a tailor order. Supports selective updates to core fields, items, materials, charges, payment, security, and optional invoice dispatch.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Tailor order id.")],
    request=inline_serializer(
        name="CustomWorkV3PatchTailorOrderRequest",
        fields={
            "customer_order_type": serializers.CharField(required=False),
            "client_id": serializers.IntegerField(required=False, allow_null=True),
            "staff": serializers.IntegerField(required=False, allow_null=True),
            "paid_amount": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "discount_amount": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "security_amount": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "security_amount_is_paid": serializers.BooleanField(required=False, help_text="Whether the security amount has been collected. Defaults to current state if omitted."),
            "account_id": serializers.IntegerField(required=False, allow_null=True),
            "security_account_id": serializers.IntegerField(required=False, allow_null=True),
            "payment_method": serializers.CharField(required=False),
            "pickup_date": serializers.CharField(required=False),
            "return_date": serializers.CharField(required=False),
            "client_phone_number": serializers.CharField(required=False),
            "client_address": serializers.CharField(required=False),
            "notes": serializers.CharField(required=False),
            "status": serializers.CharField(required=False),
            "client": serializers.JSONField(required=False),
            "items": serializers.ListField(required=False, child=serializers.JSONField(), help_text="Items to update. Two modes: (1) FULL UPDATE — include 'product_name' and item fields; id present = update existing, id absent = create new. (2) ASSIGNMENT-ONLY — include 'id' with no 'product_name'; use 'assignments' key only. Each entry in 'assignments' is routed by shape: {tailor_id, tailor_charge, tailoring_charge_from_customer?, expected_completion_date?} = create new assignment; {id, tailor_charge?, tailoring_charge_from_customer?, expected_completion_date?} = update existing assignment; {id, _delete: true} = delete that assignment. All three shapes composable in one array. Omitting 'assignments' leaves existing assignments untouched."),
            "additional_charges": serializers.ListField(required=False, child=serializers.JSONField()),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success message is either 'Tailoring order updated successfully.' or the invoice-send helper success message if send_invoice is enabled."),
        400: OpenApiResponse(description="Exact error messages include, point by point:\n- Invalid amount format.\n- Cannot modify a completed order.\n- order item/material fabric validation messages from update helper with placeholders, including:\n- Item {idx}: Materials must be a list.\n- Item {idx}, Material {mat_idx}: Invalid variant ID.\n- Item {idx}, Material {mat_idx}: Fabric quantity is required.\n- Item {idx}, Material {mat_idx}: Invalid quantity.\n- Item {idx}, Material {mat_idx}: Quantity must be greater than 0.\n- Failed to retrieve material information.\n- Material ID {variant_id} not found.\n- Material '{product_name}' doesn't support fabric tracking.\n- Invalid data for '{product_name}'.\n- Insufficient fabric for '{product_name}'. Need {additional_meters}m more, available: {available_length}m.\n- expected completion date validation message from _validate_expected_completion_dates\n- Invalid order type.\n- Invalid pickup date format.\n- Invalid return date format.\n- Return date must be on or after the pickup date.\n- phone validation message from validate_and_normalize_phone\n- Invalid status value.\n- Client not found.\n- Invalid client ID.\n- client validation message from _get_or_create_client\n- Item {idx}: Product name is required.\n- Item {idx}: Charges must be valid positive numbers.\n- Item {idx}: Invalid date format for expected completion date.\n- Item {idx}: Measurements must be an object or array.\n- Item {idx}: Assigned tailor not found or inactive.\n- Item {idx}: Invalid tailor ID.\n- Item {idx}: Item with ID {item_id} not found in this order.\n- Item {idx}: Invalid item ID.\n- Item {idx}: Image file size must be less than 3MB.\n- Item {idx}: Invalid or unsupported image format.\n- Item {idx}: Failed to upload image. Please try again.\n- Item {idx}: An error occurred.\n- Item {item_idx}, Material {mat_idx}: Invalid material ID.\n- Item {item_idx}, Material {mat_idx}: Material not found.\n- Item {item_idx}, Material {mat_idx}: Invalid quantity or amount.\n- Item {item_idx}, Material {mat_idx}: Material usage not found.\n- Item {item_idx}, Material {mat_idx}: Invalid material usage ID.\n- Additional charges must be a list.\n- Charge {idx}: Name is required.\n- Charge {idx}: Amount must be a positive number.\n- Charge {idx}: Charge with ID {charge_id} not found.\n- Charge {idx}: Invalid charge ID.\n- Discount amount cannot exceed customer total charge of ₹{customer_total_charge}.\n- Payment amount cannot exceed remaining amount of ₹{remaining_after_discount}.\n- Payment amount cannot be negative.\n- Invalid account selected.\n- Invalid payment amount.\n- Security account is required for security amount.\n- Invalid security account selected.\n- Item {idx}: Tailor assignments must be provided as a list.\n- Item {idx}: Unable to determine which item this assignment belongs to — include the item reference or full item details.\n- Item {idx}: Invalid tailor assignment reference.\n- Item {idx}: Assignment {id} not found on this item.\n- Item {idx}: Invalid charge amount for tailor assignment {id}.\n- Item {idx}: Invalid customer charge amount for tailor assignment {id}.\n- Item {idx}: Invalid expected completion date for tailor assignment {id}.\n- Item {idx}: Invalid tailor selected for a new assignment.\n- Item {idx}: Tailor {id} is already assigned to this item.\n- Item {idx}: Duplicate tailor ID {id} in assignments.\n- Item {idx}: Invalid charge amount for one of the assigned tailors.\n- Item {idx}: Invalid customer charge amount for one of the assigned tailors.\n- Item {idx}: Invalid expected completion date for one of the assigned tailors."),
        404: OpenApiResponse(description="Exact error message: Order not found."),
        500: OpenApiResponse(description="Exact error messages include:\n- Failed to update tailoring items.\n- Failed to update charges.\n- Failed to save order.\n- Failed to update material inventory.\n- Failed to update order.\n- You don't have access to this shop.\n- Something went wrong. Please try again later."),
    },
    examples=[OpenApiExample("Patch success", value={"status": "success", "message": "Tailoring order updated successfully.", "data": {"order_id": 8, "shop_tailor_id": "TO-0008", "status": "in_progress", "paid_amount": "700.00"}}, response_only=True, status_codes=["200"])],
)
@transaction.atomic
def partial_update(self, request, pk=None):
    """
    Partially update an existing tailor order with smart item/charge management.

    IMPORTANT BEHAVIOR:
    - Fields NOT in request body → IGNORED (existing data preserved)
    - Fields IN request body → UPDATED using smart logic:
    * Items/charges WITH 'id' → UPDATE existing
    * Items/charges WITHOUT 'id' → CREATE new
    * Existing items/charges NOT in request → DELETE

    Fabric Management:
    - Only calculated when items are being updated
    - Validates availability for additional usage
    - Returns fabric to stock when usage decreases
    """
    # Create savepoint at the beginning to ensure rollback on any error
    savepoint = transaction.savepoint()
    urls_to_cleanup_after_commit = []

    try:
        # Fetch existing order
        try:
            order = TailorOrder.objects.select_related('shop', 'client').prefetch_related(
                'items__material_usages',
                'additional_charges'
            ).get(
                id=pk,
                shop_id=self.shop_id,
                deleted__isnull=True
            )
        except TailorOrder.DoesNotExist:
            transaction.savepoint_rollback(savepoint)
            return self.error_response(
                message="Order not found.",
                dev_message=f"TailorOrder {pk} not found in shop {self.shop_id}"
            )

        if order.status == "completed":
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"status": ["Cannot modify a completed order."]},
                message="Cannot modify a completed order.",
                dev_message=f"TailorOrder {pk} status=completed.",
            )

        # Parse nested form-data
        parsed_data = self._parse_nested_form_data(request.data, request.FILES)

        # Build data dict (values will be None if not in request)
        data = {
            "customer_order_type": request.data.get("customer_order_type"),
            "client_id": request.data.get("client_id"),
            "staff_id": request.data.get("staff"),
            "paid_amount": request.data.get("paid_amount"),
            "discount_amount": request.data.get("discount_amount"),
            "security_amount": request.data.get("security_amount"),
            "account_id": request.data.get("account_id"),
            "security_account_id": request.data.get("security_account_id"),
            "payment_method": request.data.get("payment_method"),
            "pickup_date": request.data.get("pickup_date"),
            "return_date": request.data.get("return_date"),
            "client_phone_number": request.data.get("client_phone_number"),
            "client_address": request.data.get("client_address"),
            "notes": request.data.get("notes"),
            "status": request.data.get("status"),
            "client": parsed_data.get("client", {})
        }

        # Parse money fields
        try:
            paid_amount = Decimal(str(data.get("paid_amount") or order.paid_amount)).quantize(Decimal('0.01'))
            discount_amount = Decimal(str(data.get("discount_amount") or order.discount_amount)).quantize(Decimal('0.01'))
            security_amount = Decimal(str(data.get("security_amount") or order.security_amount)).quantize(Decimal('0.01'))
        except (ValueError, TypeError, InvalidOperation):
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                message="Invalid amount format.",
                dev_message="Money field conversion failed"
            )

        # Tax: resolve once, gated on this order already having tax applied
        # (no retroactive enabling via update). Uses the order's own
        # snapshotted taxable_components/tax_rate/tax_calculation_type
        # (fixed at creation, never drifts even if the shop's live tax
        # configuration changes later).
        product_taxable = additional_taxable = security_taxable = False
        if getattr(order, "is_tax_applied", False):
            product_taxable, additional_taxable, security_taxable = resolve_taxable_flags(
                order.taxable_components
            )

        # security_amount is always stored exactly as received - no
        # inclusive-tax stripping. Tax on a taxable security amount is
        # accounted for at the TailorOrder level only, in the aggregate tax
        # block below.
        shop_name = order.shop.s3_folder

        # --- Calculate fabric adjustments BEFORE making changes ---
        fabric_adjustments = []
        items_being_updated = bool(parsed_data.get("items"))

        if items_being_updated:
            try:
                fabric_adjustments = self._calculate_fabric_adjustments_for_update(
                    order, parsed_data["items"]
                )
            except ValueError as e:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    message=str(e),
                    dev_message=str(e)
                )

        # Validate expected completion dates against pickup date
        if items_being_updated and (data.get("pickup_date") is not None or "items" in parsed_data):
            pickup_date_to_check = data.get("pickup_date") or order.pickup_date
            date_validation_error = self._validate_expected_completion_dates(
                parsed_data["items"],
                pickup_date_to_check
            )
            if date_validation_error:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    message=date_validation_error,
                    dev_message=date_validation_error
                )

        # --- Update basic order fields (only if provided) ---
        if data.get("customer_order_type") is not None:
            if data["customer_order_type"] not in ["shop", "rental", "sale"]:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    message="Invalid order type.",
                    dev_message=f"Invalid customer_order_type: {data['customer_order_type']}"
                )
            order.customer_order_type = data["customer_order_type"]

        if data.get("pickup_date") is not None:
            try:
                order.pickup_date = parse_datetime(data["pickup_date"])
            except (ValueError, TypeError) as e:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    message="Invalid pickup date format.",
                    dev_message=str(e)
                )

        if data.get("return_date") is not None:
            try:
                order.return_date = parse_datetime(data["return_date"])
            except (ValueError, TypeError) as e:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    message="Invalid return date format.",
                    dev_message=str(e)
                )

        if order.pickup_date and order.return_date and order.return_date < order.pickup_date:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                message="Return date must be on or after the pickup date.",
                dev_message="Invalid date range"
            )

        # if data.get("client_phone_number") is not None:
        #     order.client_phone_number = data["client_phone_number"].strip()
        if data.get("client_phone_number") is not None:
            raw_phone = data["client_phone_number"]
            if raw_phone:
                normalized_phone, phone_error = validate_and_normalize_phone(
                    raw_phone=raw_phone,
                    raise_exception=False
                )
                if phone_error:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"client_phone_number": [phone_error]},
                        message=phone_error,
                        dev_message=f"Invalid client_phone_number: {raw_phone}"
                    )
                order.client_phone_number = normalized_phone
            else:
                order.client_phone_number = None

        if data.get("client_address") is not None:
            order.client_address = data["client_address"]

        if data.get("notes") is not None:
            order.notes = data["notes"]

        if data.get("status") is not None:
            valid_statuses = [choice[0] for choice in TailorOrder.STATUS_CHOICES]
            if data["status"] not in valid_statuses:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    message="Invalid status value.",
                    dev_message=f"Status {data['status']} not in {valid_statuses}"
                )
            order.status = data["status"]

        if data.get("staff_id") is not None:
            if data["staff_id"] in [""]:
                order.staff = None
            else:
                try:
                    order.staff = self._validate_staff(data["staff_id"])
                except ValueError as e:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        message=str(e),
                        dev_message=str(e)
                    )

        # --- Update client (only if provided) ---
        if data.get("client_id"):
            try:
                client_id = int(data["client_id"])
                client = Client.objects.filter(id=client_id, shop_id=self.shop_id).first()
                if not client:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        message="Client not found.",
                        dev_message=f"Client {client_id} not found in shop {self.shop_id}"
                    )
                order.client = client
            except (ValueError, TypeError):
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    message="Invalid client ID.",
                    dev_message=f"client_id conversion failed: {data['client_id']}"
                )
        elif data.get("client"):
            client, created, error_msg = self._get_or_create_client(self.shop_id, data["client"])
            if error_msg:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"client": [error_msg]},
                    message=error_msg,
                    dev_message=f"Client validation failed: {error_msg}"
                )
            if client:
                order.client = client

        # --- Update items ONLY if explicitly provided in request ---
        total_tailor = order.total_tailor_charges
        total_customer_item = Decimal('0')
        total_material = Decimal('0')

        if items_being_updated:
            try:
                total_tailor, total_customer_item, total_material, _total_items_tax = self._update_order_items(
                    order, parsed_data["items"], shop_name
                )
            except ValueError as e:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    message=str(e),
                    dev_message=str(e)
                )
            except Exception as e:
                transaction.savepoint_rollback(savepoint)
                logger.error(f"Error updating order items: {str(e)}", exc_info=True)
                return self.error_response(
                    message="Failed to update tailoring items.",
                    dev_message=str(e)
                )

        # --- Update additional charges ONLY if explicitly provided ---
        total_additional = order.total_additional_charges
        charges_being_updated = bool(parsed_data.get("additional_charges"))

        if charges_being_updated:
            try:
                total_additional, _total_charges_tax = self._update_additional_charges(
                    order, parsed_data["additional_charges"]
                )
            except ValueError as e:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    message=str(e),
                    dev_message=str(e)
                )
            except Exception as e:
                transaction.savepoint_rollback(savepoint)
                logger.error(f"Error updating additional charges: {str(e)}", exc_info=True)
                return self.error_response(
                    message="Failed to update charges.",
                    dev_message=str(e)
                )

        # Calculate customer_total_charge if items or charges were updated -
        # it is Customer Item Charges + Additional Charges, no tax/discount
        # included (tax is added conditionally below, for Exclusive only).
        customer_total_charge = order.customer_total_charge
        if items_being_updated or charges_being_updated:
            customer_total_charge = total_customer_item + total_additional

        # Tax: recalculate whenever this order already has tax applied (no
        # retroactive enabling via update) - runs regardless of whether
        # items/charges were touched this call, since a security_amount
        # change elsewhere in this same request can also affect the taxable
        # base. Uses the order's own snapshotted taxable_components/tax_rate
        # (fixed at creation).
        #
        # Discount is applied against every item/charge on this order (not
        # just ones touched this call), so Item Total + Additional Charges +
        # Tax always relate consistently to customer_total_charge. This
        # means the totals are recomputed from ALL current siblings whenever
        # discount or any item/charge changes, even ones not in this
        # request's payload. Item/charge amounts themselves are never
        # mutated by tax/discount - they always hold exactly what was
        # received.
        tax_recalculated = False
        # discount_for_final_calc: 0 once discount has already been baked
        # into customer_total_charge below (tax-enabled path), so the
        # remaining-after-discount check further down doesn't subtract it
        # twice. Equals the real discount_amount otherwise.
        discount_for_final_calc = discount_amount
        discount_check_base = customer_total_charge
        if getattr(order, "is_tax_applied", False):
            # product_taxable/additional_taxable/security_taxable already
            # resolved earlier (from order.taxable_components, above).
            # Fresh DB queries, not order.items.all()/order.additional_charges.all() -
            # those return the queryset prefetched at the top of this view, which
            # _update_order_items/_update_additional_charges (above) never refreshes
            # since they mutate rows through separately-fetched instances.
            all_items = list(TailorOrderItem.objects.filter(order_id=order.id))
            all_charges = list(TailorOrderAdditionalCharge.objects.filter(order_id=order.id))
            new_security_amount = (
                security_amount if data.get("security_amount") is not None
                else (order.security_amount or Decimal('0'))
            )

            items_raw_total = sum(
                (Decimal(str(i.customer_item_charge)) for i in all_items),
                Decimal('0.00'),
            )
            charges_raw_total = sum(
                (Decimal(str(c.amount)) for c in all_charges),
                Decimal('0.00'),
            )
            combined_raw = items_raw_total + charges_raw_total
            # Raw item+charge total is the base for the discount-bound check
            # below (customer_total_charge is raw/pre-discount in both tax
            # branches now).
            discount_check_base = combined_raw

            tax_amount = Decimal('0.00')

            if order.tax_calculation_type == "inclusive":
                # Inclusive: item/charge amounts are NEVER mutated - they
                # keep exactly the raw value received (matches the Exclusive
                # branch below). Discount and tax are computed purely at the
                # combined-subtotal level: sum the taxable buckets (items/
                # charges/security, per taxable_components), subtract the
                # discount from that taxable subtotal, then extract GST
                # (inclusive) from the result. Only order.tax_amount/
                # taxable_amount reflect this - the individual item/charge/
                # security amounts are untouched. Mirrors the create-flow
                # formula exactly.
                taxable_subtotal = Decimal('0.00')
                if product_taxable:
                    taxable_subtotal += items_raw_total
                if additional_taxable:
                    taxable_subtotal += charges_raw_total
                if security_taxable:
                    taxable_subtotal += new_security_amount

                # Clamp at zero: discount_amount is only validated against
                # combined_raw below, not against this taxable-scoped
                # subtotal - when taxable_components excludes a bucket with
                # a large raw total, an otherwise-valid discount can exceed
                # taxable_subtotal. Without this floor, split_inclusive_amount
                # would return a negative base/tax that gets persisted as-is.
                discounted_taxable = max(Decimal('0.00'), taxable_subtotal - discount_amount)
                taxable_amount, tax_amount = split_inclusive_amount(discounted_taxable, order.tax_rate)

                subtotal = (items_raw_total + charges_raw_total).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

                order.subtotal = subtotal
                order.taxable_amount = taxable_amount
                order.tax_amount = tax_amount
                # customer_total_charge is Customer Item Charges + Additional
                # Charges - tax is already embedded (inclusive), so nothing
                # is added on top. discount is handled entirely separately,
                # downstream, via discount_for_final_calc.
                customer_total_charge = (items_raw_total + charges_raw_total).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )
                total_customer_item = items_raw_total
                total_additional = charges_raw_total
                discount_for_final_calc = discount_amount

            else:
                # Exclusive: item/charge amounts are NEVER mutated - they
                # keep exactly their raw value (already the case here, since
                # order_update.py's helpers only ever pass raw values
                # through). Same taxable-subtotal-minus-discount base as the
                # Inclusive branch above, but tax is added on top instead of
                # extracted from within. taxable_amount is the discounted
                # base itself (no split needed - exclusive tax isn't
                # embedded), and customer_total_charge is that base plus the
                # calculated tax.
                subtotal = (items_raw_total + charges_raw_total).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

                taxable_subtotal = Decimal('0.00')
                if product_taxable:
                    taxable_subtotal += items_raw_total
                if additional_taxable:
                    taxable_subtotal += charges_raw_total
                if security_taxable:
                    taxable_subtotal += new_security_amount

                discounted_taxable = max(Decimal('0.00'), taxable_subtotal - discount_amount)
                taxable_amount = discounted_taxable.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                tax_amount = (discounted_taxable * order.tax_rate / Decimal('100')).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )

                order.subtotal = subtotal
                order.taxable_amount = taxable_amount
                order.tax_amount = tax_amount
                # customer_total_charge is Customer Item Charges + Additional
                # Charges + tax (Exclusive tax is additive on top). discount
                # is handled entirely separately, downstream.
                customer_total_charge = (items_raw_total + charges_raw_total + tax_amount).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )
                # Items/charges untouched - totals stay at their raw sums.
                total_customer_item = items_raw_total
                total_additional = charges_raw_total
                discount_for_final_calc = discount_amount

            # total_customer_item/total_additional feed into
            # order.total_tailor_charges-adjacent bookkeeping below (only
            # written back when items/charges were explicitly touched this
            # call) - keep them in sync with the recomputed per-item sums.
            tax_recalculated = True

        # CRITICAL: Validate discount and payment amounts BEFORE any save operations
        if order.customer_order_type in ["rental", "sale"]:
            # Validate discount amount
            if discount_amount > discount_check_base:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    message=f"Discount amount cannot exceed customer total charge of ₹{discount_check_base}.",
                    dev_message=f"Discount {discount_amount} exceeds base {discount_check_base}"
                )

            # Calculate remaining amount after discount
            remaining_after_discount = customer_total_charge - discount_for_final_calc

            # Validate paid amount
            if paid_amount > remaining_after_discount:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    message=f"Payment amount cannot exceed remaining amount of ₹{remaining_after_discount}.",
                    dev_message=f"Paid amount {paid_amount} exceeds remaining amount {remaining_after_discount}"
                )

        # --- Update payment (only if provided) ---
        if data.get("paid_amount") is not None:
            try:
                paid_amount = Decimal(str(data["paid_amount"])).quantize(Decimal('0.01'))
                if paid_amount < 0:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        message="Payment amount cannot be negative.",
                        dev_message="Negative paid_amount"
                    )

                payment_method = data.get("payment_method") or "cash"
                account_id = data.get("account_id")
                payment_account = None

                if account_id:
                    # NEW FLOW: validate account
                    from finance.models import Account as _Account
                    try:
                        payment_account = _Account.objects.get(
                            id=account_id,
                            shop_id=self.shop_id,
                            deleted__isnull=True,
                            is_active=True,
                        )
                        payment_method = 'cash' if payment_account.account_type == 'cash' else 'upi'
                    except _Account.DoesNotExist:
                        transaction.savepoint_rollback(savepoint)
                        return self.validation_error_response(
                            errors={'account_id': ['Invalid or inactive account.']},
                            message='Invalid account selected.',
                        )
                else:
                    # Fallback: reuse existing account if any
                    existing = order.payments.filter(account__isnull=False).first()
                    if existing:
                        payment_account = existing.account
                        # Derive method from account type (prevents null payment_method in ledger)
                        payment_method = 'cash' if payment_account.account_type == 'cash' else 'upi'

                # Delete old ledger entries for account-linked payments
                if payment_account:
                    from finance.api.v3.utils import delete_ledger_entries as _delete_ledger_entries
                    old_ids = list(order.payments.filter(account__isnull=False).values_list('id', flat=True))
                    if old_ids:
                        _delete_ledger_entries(reference_ids=old_ids, reference_type='CustomerPayment')

                # Delete existing payments and create new
                order.payments.all().delete()
                self._create_payment(order, paid_amount, payment_method, account=payment_account)
                order.paid_amount = paid_amount

                # Create new ledger entry (new flow)
                if payment_account and paid_amount > 0:
                    from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries
                    payment_record = order.payments.filter(account=payment_account).order_by('-created_at').first()
                    if payment_record:
                        _create_ledger_entries(
                            payments=[{
                                'amount': float(paid_amount),
                                'direction': 'in',
                                'account': payment_account,
                                'payment_method': payment_method,
                                'reference_id': payment_record.id,
                                'created_at': payment_record.created_at,
                            }],
                            shop=order.shop,
                            transaction_type='tailor_order_payment',
                            reference_type='CustomerPayment',
                        )

            except (ValueError, TypeError, InvalidOperation):
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    message="Invalid payment amount.",
                    dev_message="paid_amount conversion failed"
                )

        # --- Update order totals ---
        try:
            if items_being_updated:
                order.total_tailor_charges = total_tailor
                order.customer_total_charge = customer_total_charge
            if charges_being_updated:
                order.total_additional_charges = total_additional
                order.customer_total_charge = customer_total_charge
            if tax_recalculated:
                # Tax fields (subtotal/taxable_amount/tax_amount) were already
                # set directly on `order` above; customer_total_charge must
                # also be persisted here even if items/charges weren't
                # touched this call (e.g. a security_amount-only change, or a
                # discount-only change, still redistributes across ALL
                # items/charges and thus affects the taxable base and total).
                # total_additional_charges must be refreshed too, since every
                # charge's stored amount may have just been rewritten above.
                order.total_additional_charges = total_additional
                order.customer_total_charge = customer_total_charge

            # Always update these fields if provided
            if data.get("discount_amount") is not None:
                order.discount_amount = discount_amount
            security_amount_changing = data.get("security_amount") is not None
            paid_flag_changing = "security_amount_is_paid" in request.data
            account_only_changing = (
                data.get("security_account_id") is not None
                and not security_amount_changing
                and order.security_amount > 0
            )

            if security_amount_changing or paid_flag_changing or account_only_changing:
                # Read current paid state from existing record; override if request sends the flag.
                existing_sp = order.security_payments.select_related('account').first()
                current_is_paid = existing_sp.is_paid if existing_sp else True

                resolved_flag, flag_error = self._resolve_security_paid_flag(
                    request.data, default=current_is_paid
                )
                if flag_error:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"security_amount_is_paid": [flag_error]},
                        message=flag_error,
                    )

                # Derive effective amount from the payment record when not explicitly changing.
                # Using order.security_amount risks a mismatch if the two fields drifted
                # (e.g. order created with default 0 but payment record has actual amount).
                if security_amount_changing:
                    effective_amount = security_amount
                elif existing_sp:
                    effective_amount = existing_sp.amount  # use actual payment record amount
                else:
                    effective_amount = order.security_amount  # fallback: no payment record yet

                # When only the paid flag is changing and there is no payment record at all,
                # there is nothing to update — skip sync silently.
                if not security_amount_changing and not account_only_changing and not existing_sp:
                    pass  # no record to mark paid/unpaid; skip sync entirely
                else:
                    # Resolve target account
                    security_account_id = data.get("security_account_id")
                    security_account = None

                    if security_account_id:
                        from finance.models import Account as _Account
                        try:
                            security_account = _Account.objects.get(
                                id=security_account_id,
                                shop_id=self.shop_id,
                                deleted__isnull=True,
                                is_active=True,
                            )
                        except _Account.DoesNotExist:
                            transaction.savepoint_rollback(savepoint)
                            return self.validation_error_response(
                                errors={'security_account_id': ['Invalid or inactive account.']},
                                message='Invalid security account selected.',
                            )
                    elif existing_sp and existing_sp.account_id:
                        security_account = existing_sp.account

                    # Require account when: creating a new record, changing amount, OR marking as paid.
                    # Prevents the silent "no-account + is_paid=True → ledger skipped" path.
                    if effective_amount > 0 and not security_account and (security_amount_changing or not existing_sp or resolved_flag):
                        transaction.savepoint_rollback(savepoint)
                        return self.validation_error_response(
                            errors={'security_account_id': ['Security account is required to process security payment.']},
                            message='Security account is required for security amount.',
                        )

                    security_payment_method = (
                        'cash' if (security_account and security_account.account_type == 'cash') else 'upi'
                    )
                    sync_error = self._sync_tailor_security_payment(
                        order=order,
                        security_amount=effective_amount,
                        security_amount_is_paid=resolved_flag,
                        payment_method=security_payment_method,
                        account=security_account,
                    )
                    if sync_error:
                        transaction.savepoint_rollback(savepoint)
                        return sync_error

                if security_amount_changing:
                    order.security_amount = security_amount


            if data.get("paid_amount") is not None:
                order.paid_amount = paid_amount

            # request.data may express remove_documents as a literal list (JSON body),
            # a JSON-string field, or bracket-notation array keys (remove_documents[0]=...,
            # the same convention used by items[]/additional_charges[] elsewhere in this
            # API) - _parse_remove_documents normalizes all three so removals aren't
            # silently dropped when sent in the bracket-notation form.
            parsed_remove_documents = self._parse_remove_documents(request.data)
            if request.FILES or parsed_remove_documents:
                doc_result, urls_to_delete = self._update_tailor_order_documents(
                    order, request, {"remove_documents": parsed_remove_documents}, savepoint
                )
                if doc_result:
                    return doc_result
                if urls_to_delete:
                    urls_to_cleanup_after_commit.extend(urls_to_delete)

            order.save()

            try:
                create_activity_log(
                    shop=order.shop,
                    user=request.user,
                    instance=order,
                    action="updated",
                    message=f"Order details updated."
                )
            except Exception as log_error:
                logger.exception(f"Failed to create activity log for tailor order update details: {log_error}")

        except Exception as e:
            transaction.savepoint_rollback(savepoint)
            logger.error(f"Failed to save order: {str(e)}", exc_info=True)
            return self.error_response(
                message="Failed to update order.",
                dev_message=str(e)
            )

        # --- Apply fabric length adjustments (only if items were updated) ---
        if fabric_adjustments:
            try:
                self._apply_fabric_adjustments(fabric_adjustments)
            except ValueError as e:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    message=str(e),
                    dev_message=str(e)
                )
            except Exception as e:
                transaction.savepoint_rollback(savepoint)
                logger.error(f"Failed to adjust fabric lengths: {str(e)}", exc_info=True)
                return self.error_response(
                    message="Failed to update material inventory.",
                    dev_message=str(e)
                )

        # Commit the transaction only if everything succeeds
        transaction.savepoint_commit(savepoint)

        if urls_to_cleanup_after_commit:
            self._cleanup_s3_documents(urls_to_cleanup_after_commit)

        success_message = self._handle_send_invoice(order, request.data, request.user, "Tailoring order updated successfully.")
        return self.success_response(
            message=success_message,
            data={
                "order_id": order.id,
                "shop_tailor_id": order.shop_tailor_id,
                "client_name": order.client.client_name if order.client else None,
                "total_tailor_charge": str(order.total_tailor_charges),
                "customer_total_charge": str(order.customer_total_charge),
                "total_additional_charges": str(order.total_additional_charges),
                "paid_amount": str(order.paid_amount),
                "discount_amount": str(order.discount_amount),
                "security_amount": str(order.security_amount),
                "status": order.status,
            },
            dev_message="Order updated successfully with fabric adjustments applied."
        )

    except PermissionDenied as e:
        transaction.savepoint_rollback(savepoint)
        logger.error(f"Permission denied: {str(e)}")
        return self.error_response(
            message="You don't have access to this shop.",
            dev_message=str(e)
        )

    except Exception as e:
        transaction.savepoint_rollback(savepoint)
        logger.error(f"Unexpected error in partial_update: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong. Please try again later.",
            dev_message=str(e)
        )
