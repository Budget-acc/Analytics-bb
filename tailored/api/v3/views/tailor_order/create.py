from datetime import datetime as _datetime
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils.timezone import now as _now, make_aware as _make_aware, localtime as _localtime
from rest_framework.exceptions import PermissionDenied
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from tailored.models import TailorOrder
from booking_v2.models import Client
from shop.models import Shop
from shop.utils.phone_validator import validate_and_normalize_phone
from tailored.api.v3.utils.date_utils import parse_datetime, InvalidDateFormat
from shop.api.v3.utils.tax_calculation import (
    get_transaction_tax_config, resolve_apply_tax, resolve_taxable_flags, split_inclusive_amount,
)
from ERP.utils.s3_image_uploads import SYNC_DOCUMENT_UPLOAD_COUNT
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Create tailor order",
    description="Creates a tailor order for the active shop. Supports nested multipart data for items, materials, charges, client data, payments, security, and optional invoice dispatch.",
    request=inline_serializer(
        name="CustomWorkV3CreateTailorOrderRequest",
        fields={
            "customer_order_type": serializers.CharField(required=False, help_text="Allowed values: shop, rental, sale. Defaults to shop."),
            "client_id": serializers.IntegerField(required=False, allow_null=True, help_text="Existing client id for rental or sale orders."),
            "staff": serializers.IntegerField(required=False, allow_null=True, help_text="Optional staff id."),
            "paid_amount": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "discount_amount": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "security_amount": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
            "security_amount_is_paid": serializers.BooleanField(required=False, help_text="Whether the security amount has been collected. Defaults to true."),
            "account_id": serializers.IntegerField(required=False, allow_null=True),
            "security_account_id": serializers.IntegerField(required=False, allow_null=True),
            "payment_method": serializers.CharField(required=False),
            "pickup_date": serializers.CharField(required=False, help_text="Required for rental orders. ISO 8601 datetime."),
            "return_date": serializers.CharField(required=False, help_text="Required for rental orders. ISO 8601 datetime."),
            "client_phone_number": serializers.CharField(required=False),
            "client_address": serializers.CharField(required=False),
            "notes": serializers.CharField(required=False),
            "items": serializers.ListField(required=True, child=serializers.JSONField(), help_text="Nested tailoring items. Each item may include: product_name, quantity, customer_tailoring_charge, customer_item_charge, measurements, reference image, materials, and optionally either 'assigned_tailor' (single tailor, legacy) or 'assignments' (array of {tailor_id, tailor_charge, tailoring_charge_from_customer?, expected_completion_date?} for multiple tailors)."),
            "additional_charges": serializers.ListField(required=False, child=serializers.JSONField(), help_text="Optional additional charges."),
            "client": serializers.JSONField(required=False, help_text="Optional inline client payload when client_id is not supplied."),
            "apply_tax": serializers.BooleanField(
                required=False,
                default=True,
                help_text=(
                    "Purpose: Turns tax calculation on or off for this transaction only. "
                    "Location: request body. Format/Type: boolean (true/false; multipart: \"true\"/\"false\"). "
                    "true (default): the shop's active tax configuration is applied as usual. "
                    "false: no tax is applied to this transaction (is_tax_applied=false); the shop's tax "
                    "configuration and other transactions are not affected. Optional. Default: true."
                ),
            ),
            "booked_date": serializers.CharField(
                required=False,
                allow_null=True,
                help_text=(
                    "Optional historical order date. "
                    "Formats: DD-MM-YYYY (e.g. 20-06-2026) or DD-MM-YYYYTHH:MM:SS (e.g. 20-06-2026T10:30:00). "
                    "When only a date is provided, the current server time is used as the time component "
                    "so the timestamp is never midnight. "
                    "Cannot be a future date. "
                    "If omitted, defaults to the current date and time. "
                    "Affects order created_at, payment records, ledger entries, and daily balance calculations."
                ),
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Success message is either 'Tailoring order created successfully.' or the invoice-send helper success message if send_invoice is enabled."),
        400: OpenApiResponse(description="Exact error messages include, point by point:\n- phone validation message from validate_and_normalize_phone\n- Payment amount cannot be negative.\n- Discount amount cannot be negative.\n- Security amount cannot be negative.\n- Invalid payment amount. Please enter a valid number.\n- Invalid account selected.\n- Invalid security account selected.\n- Invalid staff ID format.\n- Staff not found or inactive in this shop.\n- Invalid order type. Please select a valid option.\n- Pickup date and return date are required for rental orders.\n- Return date must be on or after the pickup date.\n- Invalid date format. Please use ISO 8601 format.\n- Invalid client ID.\n- Client not found.\n- Client name and phone number are required.\n- Client information is required.\n- At least one tailoring item is required.\n- Item {idx}: At least one material is required.\n- expected completion date validation message from _validate_expected_completion_dates\n- Item {idx}: Materials must be a list.\n- Item {idx}, Material {mat_idx}: Invalid variant ID '{variant_id}'.\n- Item {idx}, Material {mat_idx}: Fabric quantity (meters) is required.\n- Item {idx}, Material {mat_idx}: Fabric quantity must be a valid number.\n- Item {idx}, Material {mat_idx}: Fabric quantity must be greater than 0.\n- Item {idx}, Material {mat_idx}: Material not found in your shop.\n- Item {idx}, Material {mat_idx}: Material '{product_name}' does not support fabric length tracking.\n- Item {idx}, Material {mat_idx}: Invalid material data. Please contact support.\n- Item {idx}, Material {mat_idx}: Insufficient fabric available. Requested: {requested}m, Available: {available}m.\n- Item {idx}: Product name is required.\n- Item {idx}: Charges must be valid positive numbers.\n- Item {idx}: Invalid date format for expected date.\n- Item {idx}: Measurements must be an object or array.\n- Item {idx}: Assigned tailor not found or inactive in this shop.\n- Item {idx}: Invalid tailor ID.\n- Item {idx}: Invalid charge amount for one of the assigned tailors.\n- Item {idx}: Invalid customer charge amount for one of the assigned tailors.\n- Item {idx}: Invalid expected completion date for one of the assigned tailors.\n- Item {idx}: Image file size must be less than 3MB.\n- Item {idx}: Invalid or unsupported image format.\n- Item {idx}: Failed to upload image. Please try again.\n- Item {idx}: Failed to save. Please try again.\n- Item {idx}, Material {mat_idx}: Invalid material ID.\n- Item {idx}, Material {mat_idx}: Quantity and amount must be valid numbers.\n- Item {idx}, Material {mat_idx}: Failed to save material.\n- Item {idx}, Material {mat_idx}: An error occurred.\n- Item {idx}: An error occurred. Please try again.\n- Discount amount cannot exceed customer total charge of ₹{customer_total_charge}.\n- Payment amount cannot exceed remaining amount of ₹{remaining_after_discount}.\n- Validation error from Django ValidationError handling\n- Invalid date format from InvalidDateFormat handling\n- apply_tax must be a boolean value."),
        500: OpenApiResponse(description="Exact error messages include:\n- Your shop information could not be found. Please contact support.\n- Failed to create the order. Please try again.\n- Failed to create tailoring items.\n- Failed to add charges.\n- Failed to finalize order. Please try again.\n- Failed to record payment.\n- Failed to create ledger entry.\n- Failed to update material inventory. Please try again.\n- Failed to create security ledger entry.\n- You don't have access to this shop.\n- Something went wrong. Please try again later."),
    },
    examples=[
        OpenApiExample("Create request", value={"customer_order_type": "sale", "client_id": 4, "paid_amount": "500.00", "discount_amount": "100.00", "items": [{"product_name": "Sherwani", "product_type": "custom", "quantity": 1, "tailor_charge": "800", "customer_tailoring_charge": "1000", "customer_item_charge": "1800", "assigned_tailor_id": 5, "materials": [{"variant_id": 21, "quantity_meters": "2.5", "total_amount": "900"}]}], "additional_charges": [{"name": "Urgent work", "amount": "250"}]}, request_only=True),
        OpenApiExample("Create request without tax", value={"customer_order_type": "sale", "client_id": 4, "apply_tax": False, "items": [{"product_name": "Sherwani", "quantity": 1, "customer_item_charge": "1800", "materials": [{"variant_id": 21, "quantity_meters": "2.5", "total_amount": "900"}]}]}, request_only=True),
        OpenApiExample("Create success", value={"status": "success", "message": "Tailoring order created successfully.", "data": {"order_id": 8, "shop_tailor_id": "TO-0008", "client_name": "Aman", "total_tailor_charge": "800.00", "customer_total_charge": "2050.00", "paid_amount": "500.00", "discount_amount": "100.00", "security_amount": "0.00"}}, response_only=True, status_codes=["200"]),
    ],
)
@transaction.atomic
def create(self, request):
    """
    Create a new tailor order with fabric length validation and deduction.

    Flow:
    1. Parse and validate all input data
    2. Validate fabric lengths BEFORE creating anything
    3. Create order, items, materials (NO fabric deduction yet)
    4. After successful creation, deduct fabric lengths atomically
    """
    preview_urls = []    # Track sync-uploaded preview urls for cleanup on rollback
    uploaded_files = []  # Track temp files for cleanup / background upload
    # Savepoint used ONLY for document validation/upload failures: this view has no
    # other explicit savepoint (unlike partial_update.py) and instead uses bare
    # transaction.set_rollback(True) elsewhere. set_rollback(True) marks the ENTIRE
    # outer transaction broken (not just this view's work) - any later query on the
    # same connection (e.g. request-logging middleware calling get_shop_info) then
    # raises TransactionManagementError before the view can even return its response.
    # A real savepoint lets document failures roll back cleanly without poisoning
    # the whole connection.
    doc_savepoint = transaction.savepoint()
    try:
        # Resolve effective datetime from optional booked_date field FIRST —
        # before any DB writes so an early return doesn't leave partial state
        # inside the @transaction.atomic block.
        # Pattern mirrors update_payment.py / add_payment.py.
        booked_date_str = request.data.get("booked_date")
        effective_dt = None
        booked_date_is_date_only = False
        if booked_date_str:
            # Try date+time format first
            try:
                effective_dt = _make_aware(_datetime.strptime(booked_date_str, "%d-%m-%YT%H:%M:%S"))
            except ValueError:
                # Date-only: combine with current time (never midnight)
                try:
                    parsed_date = _datetime.strptime(booked_date_str, "%d-%m-%Y").date()
                    effective_dt = _make_aware(
                        _datetime.combine(parsed_date, _localtime(_now()).time())
                    )
                    booked_date_is_date_only = True
                except ValueError:
                    return self.validation_error_response(
                        errors={"booked_date": ["Invalid format. Use DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS."]},
                        message="Invalid booked_date format.",
                    )
            # Reject future dates; use date-only comparison when no time was provided
            eff_for_future = effective_dt.date() if booked_date_is_date_only else effective_dt
            now_for_future = _now().date()        if booked_date_is_date_only else _now()
            if eff_for_future > now_for_future:
                return self.validation_error_response(
                    errors={"booked_date": ["booked_date cannot be a future date."]},
                    message="Booked date cannot be a future date.",
                )

        # Per-transaction tax switch (default: tax applies as configured)
        apply_tax, apply_tax_error = resolve_apply_tax(request.data)
        if apply_tax_error:
            return self.validation_error_response(
                errors={"apply_tax": [apply_tax_error]},
                message=apply_tax_error,
            )

        # Parse nested form-data
        parsed_data = self._parse_nested_form_data(request.data, request.FILES)

        # Merge parsed data with top-level fields
        data = {
            "customer_order_type": request.data.get("customer_order_type", "shop"),
            "client_id": request.data.get("client_id"),
            "staff_id": request.data.get("staff"),
            "paid_amount": request.data.get("paid_amount", 0),
            "discount_amount": request.data.get("discount_amount", 0),
            "security_amount": request.data.get("security_amount", 0),
            "account_id": request.data.get("account_id"),
            "security_account_id": request.data.get("security_account_id"),
            "payment_method": request.data.get("payment_method", "cash"),
            "pickup_date": request.data.get("pickup_date"),
            "return_date": request.data.get("return_date"),
            "client_phone_number": request.data.get("client_phone_number"),
            "client_address": request.data.get("client_address"),
            "customer_item_charge": request.data.get("customer_item_charge"),
            "notes": request.data.get("notes"),
            "items": parsed_data["items"],
            "additional_charges": parsed_data["additional_charges"],
            "client": parsed_data["client"]
        }

        validated_phone_number = None
        raw_phone = data.get("client_phone_number")
        if raw_phone:
            normalized_phone, phone_error = validate_and_normalize_phone(
                raw_phone=raw_phone,
                raise_exception=False
            )
            if phone_error:
                return self.validation_error_response(
                    errors={"client_phone_number": [phone_error]},
                    message=phone_error,
                    dev_message=f"Invalid client_phone_number: {raw_phone}"
                )
            validated_phone_number = normalized_phone

        # Validate basic fields
        try:
            client_id = data.get("client_id")
            client_data = data.get("client", {})
            staff_id = data.get("staff_id")
            paid_amount = Decimal(str(data.get("paid_amount") or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            discount_amount = Decimal(str(data.get("discount_amount") or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            security_amount = Decimal(str(data.get("security_amount") or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            payment_method = data.get("payment_method", "cash")
            account_id = data.get("account_id")
            security_account_id = data.get("security_account_id")
            customer_order_type = data.get("customer_order_type", "shop")

            if paid_amount < 0:
                return self.validation_error_response(
                    message="Payment amount cannot be negative.",
                    dev_message="Negative paid_amount provided."
                )

            if discount_amount < 0:
                return self.validation_error_response(
                    message="Discount amount cannot be negative.",
                    dev_message="Negative discount_amount provided."
                )

            # Validate security_amount
            if security_amount < 0:
                return self.validation_error_response(
                    message="Security amount cannot be negative.",
                    dev_message="Negative security_amount provided."
                )

        except (ValueError, TypeError):
            return self.validation_error_response(
                message="Invalid payment amount. Please enter a valid number.",
                dev_message="paid_amount conversion failed."
            )

        # Parse optional account IDs for new account-based flow.
        if account_id not in [None, ""]:
            try:
                account_id = int(account_id)
            except (ValueError, TypeError):
                return self.validation_error_response(
                    errors={"account_id": ["Invalid account ID."]},
                    message="Invalid account selected.",
                    dev_message=f"account_id is not an integer: {account_id}"
                )
        else:
            account_id = None

        if security_account_id not in [None, ""]:
            try:
                security_account_id = int(security_account_id)
            except (ValueError, TypeError):
                return self.validation_error_response(
                    errors={"security_account_id": ["Invalid security account ID."]},
                    message="Invalid security account selected.",
                    dev_message=f"security_account_id is not an integer: {security_account_id}"
                )
        else:
            security_account_id = None

        staff = None
        if staff_id:
            try:
                staff = self._validate_staff(staff_id)
            except ValueError as e:
                return self.validation_error_response(
                    message=str(e),
                    dev_message=str(e)
                )

        # Validate customer_order_type
        if customer_order_type not in ["shop", "rental", "sale"]:
            return self.validation_error_response(
                message="Invalid order type. Please select a valid option.",
                dev_message=f"Invalid customer_order_type: {customer_order_type}"
            )

        pickup_date = data.get("pickup_date")
        return_date = data.get("return_date")

        # Validate booked_date ≤ pickup_date (only when pickup_date is provided, i.e. rental orders).
        # When booked_date was date-only (no time component), compare only the date portions so
        # that "same calendar day" is always accepted regardless of what time pickup_date has.
        if effective_dt and pickup_date:
            try:
                pickup_dt_for_check = parse_datetime(pickup_date)
                eff_cmp = effective_dt.date() if booked_date_is_date_only else effective_dt
                pu_cmp  = pickup_dt_for_check.date() if booked_date_is_date_only else pickup_dt_for_check
                if eff_cmp > pu_cmp:
                    return self.validation_error_response(
                        errors={"booked_date": ["booked_date cannot be later than the pickup date."]},
                        message="Booked date cannot be later than the pickup date.",
                    )
            except (InvalidDateFormat, Exception):
                pass  # pickup_date format errors are caught in the rental validation block below

        # --- Validate rental-specific fields ---
        client = None
        if customer_order_type == "rental":
            if not pickup_date or not return_date:
                return self.validation_error_response(
                    message="Pickup date and return date are required for rental orders.",
                    dev_message="Missing pickup_date or return_date for rental order."
                )

            try:
                pickup_dt = parse_datetime(pickup_date)
                return_dt = parse_datetime(return_date)
                if return_dt < pickup_dt:
                    return self.validation_error_response(
                        message="Return date must be on or after the pickup date.",
                        dev_message=f"Return date {return_date} is before pickup date {pickup_date}."
                    )
            except (ValueError, TypeError) as e:
                return self.validation_error_response(
                    message="Invalid date format. Please use ISO 8601 format.",
                    dev_message=f"Date parsing error: {str(e)}"
                )

        if customer_order_type == "rental" or customer_order_type == "sale":
            if client_id:
                try:
                    client_id = int(client_id)
                except (ValueError, TypeError):
                    return self.validation_error_response(
                        message="Invalid client ID.",
                        dev_message=f"client_id is not an integer: {client_id}"
                    )

                client = Client.objects.filter(id=client_id, shop_id=self.shop_id).first()
                if not client:
                    return self.validation_error_response(
                        message="Client not found.",
                        dev_message=f"No client with ID {client_id} in shop {self.shop_id}."
                    )
            elif client_data:
                client, created, error_msg = self._get_or_create_client(self.shop_id, client_data)
                if error_msg:
                    return self.validation_error_response(
                        errors={"client": [error_msg]},
                        message=error_msg,
                        dev_message=f"Client validation failed: {error_msg}"
                    )

                if not client:
                    return self.validation_error_response(
                        message="Client name and phone number are required.",
                        dev_message="Client creation returned None."
                    )
            else:
                return self.validation_error_response(
                    message="Client information is required.",
                    dev_message="No client_id or client_data for order."
                )

        # Fetch shop data once
        try:
            shop = Shop.objects.only("id", "name", "s3_folder_name").get(id=self.shop_id)
            shop_name = shop.s3_folder
        except Shop.DoesNotExist:
            logger.error(f"Shop {self.shop_id} not found.")
            return self.error_response(
                message="Your shop information could not be found. Please contact support.",
                dev_message=f"Shop {self.shop_id} does not exist."
            )

        # Collect + validate documents as early as possible (mirrors Booking's flow)
        document_files = self._collect_document_files(request) if request.FILES else []
        total_document_count = len(document_files)

        if document_files:
            is_valid, doc_error_response, document_files = self._validate_documents_sync(
                request, shop, savepoint=doc_savepoint, files=document_files
            )
            if not is_valid:
                return doc_error_response

        # Tax: resolve the shop's active tax configuration once, if any.
        # security_amount is always stored exactly as received - it is never
        # tax-adjusted at the individual-field level. Tax on a taxable
        # security amount is accounted for at the TailorOrder level only, in
        # the aggregate tax block below.
        # apply_tax=false: this order only takes the existing no-tax path.
        tax_config = get_transaction_tax_config(shop, apply_tax)
        product_taxable = additional_taxable = security_taxable = False
        if tax_config:
            product_taxable, additional_taxable, security_taxable = resolve_taxable_flags(
                tax_config.taxable_components
            )

        # Validate account IDs for the new account-based flow.
        payment_account = None
        security_account = None
        if account_id or security_account_id:
            from finance.models import Account as _Account

            if account_id:
                try:
                    payment_account = _Account.objects.get(
                        id=account_id,
                        shop_id=self.shop_id,
                        deleted__isnull=True,
                        is_active=True,
                    )
                    payment_method = 'cash' if payment_account.account_type == 'cash' else 'upi'
                except _Account.DoesNotExist:
                    return self.validation_error_response(
                        errors={'account_id': ['Invalid or inactive account.']},
                        message='Invalid account selected.',
                    )

            if security_account_id:
                try:
                    security_account = _Account.objects.get(
                        id=security_account_id,
                        shop_id=self.shop_id,
                        deleted__isnull=True,
                        is_active=True,
                    )
                except _Account.DoesNotExist:
                    return self.validation_error_response(
                        errors={'security_account_id': ['Invalid or inactive account.']},
                        message='Invalid security account selected.',
                    )

        # --- Validate items ---
        items_data = data.get("items", [])
        if not items_data:
            return self.validation_error_response(
                message="At least one tailoring item is required.",
                dev_message="Empty or missing items list."
            )

        # Check that each item has at least one material
        for idx, item in enumerate(items_data, start=1):
            materials = item.get("materials", [])
            if not materials or not isinstance(materials, list) or len(materials) == 0:
                return self.validation_error_response(
                    message=f"Item {idx}: At least one material is required.",
                    dev_message=f"Item {idx} has no materials."
                )

        # Validate expected completion dates against pickup date
        date_validation_error = self._validate_expected_completion_dates(items_data, pickup_date)
        if date_validation_error:
            return self.validation_error_response(
                message=date_validation_error,
                dev_message=date_validation_error
            )

        # VALIDATE FABRIC LENGTHS BEFORE CREATING ORDER
        try:
            fabric_deductions = self._validate_and_prepare_fabric_usage(items_data)
        except ValueError as e:
            return self.validation_error_response(
                message=str(e),
                dev_message=str(e)
            )

        # --- Create order ---
        try:
            order = self._create_tailor_order(request, data, client, staff, discount_amount, security_amount, effective_dt=effective_dt)
        except Exception as e:
            transaction.set_rollback(True)
            logger.error(f"Failed to create tailor order: {str(e)}", exc_info=True)
            return self.error_response(
                message="Failed to create the order. Please try again.",
                dev_message=str(e)
            )

        # Initialize documents + documents_details
        order.documents = []
        order.documents_details = {
            "status": "uploading" if total_document_count > SYNC_DOCUMENT_UPLOAD_COUNT else "completed",
            "total_count": total_document_count,
            "uploaded_count": 0,
        }
        order.save(update_fields=["documents", "documents_details"])

        # items_raw_total/charges_raw_total: the raw (as-received) item and
        # additional-charge totals, computed before either creation helper
        # runs. Items/charges are always stored raw (see order_create.py) -
        # discount plays no role in per-item/charge storage or in the tax
        # calculation itself (see the aggregate tax block below).
        raw_additional_charges = data.get("additional_charges", []) or []
        items_raw_total = sum(
            (Decimal(str(item.get("customer_item_charge") or 0)) for item in items_data),
            Decimal("0.00"),
        )
        charges_raw_total = sum(
            (Decimal(str(c.get("amount") or 0)) for c in raw_additional_charges if isinstance(c, dict)),
            Decimal("0.00"),
        )

        try:
            total_tailor, total_customer_item, total_material, total_items_tax = self._create_order_items(
                order, items_data, shop_name, tax_config=tax_config, product_taxable=product_taxable,
                items_raw_total=items_raw_total,
            )
        except ValueError as e:
            transaction.set_rollback(True)
            return self.validation_error_response(message=str(e), dev_message=str(e))
        except Exception as e:
            transaction.set_rollback(True)
            logger.error(f"Error creating order items: {str(e)}", exc_info=True)
            return self.error_response(
                message="Failed to create tailoring items.",
                dev_message=str(e)
            )

        # --- Add additional charges ---
        try:
            total_additional, total_charges_tax = self._create_additional_charges(
                order, raw_additional_charges, tax_config=tax_config, additional_taxable=additional_taxable,
                charges_raw_total=charges_raw_total,
            )
        except Exception as e:
            transaction.set_rollback(True)
            logger.error(f"Error creating additional charges: {str(e)}", exc_info=True)
            return self.error_response(
                message="Failed to add charges.",
                dev_message=str(e)
            )

        # Tax: aggregate the tax fields once items/charges have been
        # created. Items/charges were created with raw values (see
        # order_create.py) and are NEVER mutated for tax, in either mode.
        # For inclusive tax, the taxable buckets (items/charges/security,
        # per taxable_components) are summed, the discount is subtracted
        # from that combined taxable subtotal, and GST is extracted
        # (inclusive) from the result - only tax_amount/taxable_amount
        # reflect this. For exclusive tax, `subtotal` is the raw,
        # undiscounted total - tax/discount are computed purely at this
        # aggregate level.
        subtotal = (total_customer_item + Decimal(str(total_additional))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        tax_fields = None
        # discount_for_final_calc: 0 once discount has already been baked
        # into customer_total_charge below (tax-enabled path), so the
        # remaining-after-discount check further down doesn't subtract it
        # twice. Equals the real discount_amount when tax is disabled.
        discount_for_final_calc = discount_amount
        if tax_config and tax_config.tax_calculation_type == "inclusive":
            # Inclusive: item/charge amounts are NEVER mutated - they keep
            # exactly the raw value received (matches the Exclusive branch
            # below). Discount and tax are computed purely at the combined-
            # subtotal level: sum the taxable buckets (items/charges/
            # security, per taxable_components), subtract the discount from
            # that taxable subtotal, then extract GST (inclusive) from the
            # result. Only tax_amount/taxable_amount (TailorOrder-level)
            # reflect this - the individual item/charge/security amounts
            # are untouched. Mirrors the Booking fix exactly.
            taxable_subtotal = Decimal("0.00")
            if product_taxable:
                taxable_subtotal += items_raw_total
            if additional_taxable:
                taxable_subtotal += charges_raw_total
            if security_taxable:
                taxable_subtotal += security_amount

            # Clamp at zero: discount_amount is only validated against the
            # raw item+charge total below (discount_check_base), not against
            # this taxable-scoped subtotal - when taxable_components
            # excludes a bucket with a large raw total, an otherwise-valid
            # discount can exceed taxable_subtotal. Without this floor,
            # split_inclusive_amount would return a negative base/tax that
            # gets persisted as-is.
            discounted_taxable = max(Decimal("0.00"), taxable_subtotal - discount_amount)
            taxable_amount, tax_amount = split_inclusive_amount(discounted_taxable, tax_config.tax_rate)

            # subtotal/total_customer_item/total_additional are the raw,
            # undiscounted sums - matches what's actually stored per item/
            # charge (unchanged).
            subtotal = (items_raw_total + charges_raw_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_customer_item = items_raw_total
            total_additional = charges_raw_total

            tax_fields = {
                "subtotal": subtotal,
                "taxable_amount": taxable_amount,
                "tax_amount": tax_amount,
                "tax_rate": tax_config.tax_rate,
                "tax_name": tax_config.tax_name,
                "tax_calculation_type": tax_config.tax_calculation_type,
                "is_tax_applied": True,
                "taxable_components": tax_config.taxable_components,
            }
            # customer_total_charge is Customer Item Charges + Additional
            # Charges - tax is already embedded (inclusive), so nothing is
            # added on top. discount is handled entirely separately,
            # downstream, via discount_for_final_calc.
            customer_total_charge = (items_raw_total + charges_raw_total).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            discount_for_final_calc = discount_amount
        elif tax_config:
            # Exclusive: same taxable-subtotal-minus-discount base as the
            # Inclusive branch above, but tax is added on top instead of
            # extracted from within. taxable_amount is the discounted base
            # itself (no split needed - exclusive tax isn't embedded), and
            # customer_total_charge is that base plus the calculated tax.
            taxable_subtotal = Decimal("0.00")
            if product_taxable:
                taxable_subtotal += items_raw_total
            if additional_taxable:
                taxable_subtotal += charges_raw_total
            if security_taxable:
                taxable_subtotal += security_amount

            discounted_taxable = max(Decimal("0.00"), taxable_subtotal - discount_amount)
            taxable_amount = discounted_taxable.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            tax_amount = (discounted_taxable * tax_config.tax_rate / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            tax_fields = {
                "subtotal": subtotal,
                "taxable_amount": taxable_amount,
                "tax_amount": tax_amount,
                "tax_rate": tax_config.tax_rate,
                "tax_name": tax_config.tax_name,
                "tax_calculation_type": tax_config.tax_calculation_type,
                "is_tax_applied": True,
                "taxable_components": tax_config.taxable_components,
            }
            # customer_total_charge is Customer Item Charges + Additional
            # Charges + tax (Exclusive tax is additive on top). discount is
            # handled entirely separately, downstream, via
            # discount_for_final_calc.
            customer_total_charge = (items_raw_total + charges_raw_total + tax_amount).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            discount_for_final_calc = discount_amount
        else:
            customer_total_charge = (items_raw_total + charges_raw_total).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        # Validate discount and payment amounts for rental/sale orders
        if customer_order_type in ["rental", "sale"]:
            # Validate discount amount against the raw, pre-discount item+
            # charge total in all cases (tax-enabled or not) - matches
            # customer_total_charge's own raw semantics.
            discount_check_base = (items_raw_total + charges_raw_total) if tax_config else customer_total_charge
            if discount_amount > discount_check_base:
                transaction.set_rollback(True)
                return self.validation_error_response(
                    message=f"Discount amount cannot exceed customer total charge of ₹{discount_check_base}.",
                    dev_message=f"Discount {discount_amount} exceeds base {discount_check_base}"
                )

            # Calculate remaining amount after discount
            remaining_after_discount = customer_total_charge - discount_for_final_calc

            # Validate paid amount
            if paid_amount > remaining_after_discount:
                transaction.set_rollback(True)
                return self.validation_error_response(
                    message=f"Payment amount cannot exceed remaining amount of ₹{remaining_after_discount}.",
                    dev_message=f"Paid amount {paid_amount} exceeds remaining amount {remaining_after_discount}"
                )

        # --- Update order totals ---
        try:
            order.total_tailor_charges = total_tailor
            order.total_additional_charges = Decimal(str(total_additional))
            order.customer_total_charge = customer_total_charge
            order.paid_amount = paid_amount
            order.discount_amount = discount_amount
            order.security_amount = security_amount
            if tax_fields:
                order.subtotal = tax_fields["subtotal"]
                order.taxable_amount = tax_fields["taxable_amount"]
                order.tax_amount = tax_fields["tax_amount"]
                order.tax_rate = tax_fields["tax_rate"]
                order.tax_name = tax_fields["tax_name"]
                order.tax_calculation_type = tax_fields["tax_calculation_type"]
                order.is_tax_applied = tax_fields["is_tax_applied"]
                order.taxable_components = tax_fields["taxable_components"]
            order.save()
        except Exception as e:
            transaction.set_rollback(True)
            logger.error(f"Failed to update order totals: {str(e)}", exc_info=True)
            return self.error_response(
                message="Failed to finalize order. Please try again.",
                dev_message=str(e)
            )

        # --- Record payment ---
        try:
            self._create_payment(order, paid_amount, payment_method, account=payment_account, effective_dt=effective_dt)
        except Exception as e:
            transaction.set_rollback(True)
            logger.error(f"Error creating payment: {str(e)}", exc_info=True)
            return self.error_response(
                message="Failed to record payment.",
                dev_message=str(e)
            )

        # --- Create ledger entry for payment (new flow) ---
        if payment_account and paid_amount and float(paid_amount) > 0:
            try:
                from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries
                payment_record = order.payments.filter(account=payment_account).order_by('-created_at').first()
                if payment_record:
                    _create_ledger_entries(
                        payments=[{
                            'amount': paid_amount,
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
            except Exception as e:
                transaction.set_rollback(True)
                logger.error(f"Error creating ledger entry for payment: {str(e)}", exc_info=True)
                return self.error_response(message="Failed to create ledger entry.", dev_message=str(e))

        # DEDUCT FABRIC LENGTHS (only after successful order creation)
        try:
            self._deduct_fabric_lengths(fabric_deductions)
        except ValueError as e:
            transaction.set_rollback(True)
            return self.validation_error_response(
                message=str(e),
                dev_message=str(e)
            )
        except Exception as e:
            transaction.set_rollback(True)
            logger.error(f"Failed to deduct fabric lengths: {str(e)}", exc_info=True)
            return self.error_response(
                message="Failed to update material inventory. Please try again.",
                dev_message=str(e)
            )

        # --- Sync security payment record and ledger (is_paid-aware) ---
        security_amount_is_paid, paid_flag_error = self._resolve_security_paid_flag(
            request.data, default=True
        )
        if paid_flag_error:
            transaction.set_rollback(True)
            return self.validation_error_response(
                errors={"security_amount_is_paid": [paid_flag_error]},
                message=paid_flag_error,
            )

        security_payment_method = (
            'cash' if (security_account and security_account.account_type == 'cash') else 'upi'
        )
        sync_error = self._sync_tailor_security_payment(
            order=order,
            security_amount=security_amount,
            security_amount_is_paid=security_amount_is_paid,
            payment_method=security_payment_method,
            account=security_account,
            effective_dt=effective_dt,
        )
        if sync_error:
            transaction.set_rollback(True)
            return sync_error

        # Upload first SYNC_DOCUMENT_UPLOAD_COUNT documents synchronously, save rest for background upload
        if total_document_count > 0:
            preview_files = document_files[:SYNC_DOCUMENT_UPLOAD_COUNT]
            remaining_files = document_files[SYNC_DOCUMENT_UPLOAD_COUNT:]

            preview_urls, doc_error = self._upload_documents_to_s3_sync(
                preview_files, shop, request.user, savepoint=doc_savepoint
            )
            if doc_error:
                return doc_error

            if remaining_files:
                uploaded_files = self._save_temp_documents(request, order.id, files=remaining_files)
                if isinstance(uploaded_files, dict) and 'error' in uploaded_files:
                    transaction.savepoint_rollback(doc_savepoint)
                    if preview_urls:
                        self._cleanup_s3_documents_sync(preview_urls)
                    return uploaded_files['error']

            order.documents = preview_urls or []
            order.documents_details = {
                "status": "uploading" if remaining_files else "completed",
                "total_count": total_document_count,
                "uploaded_count": len(order.documents),
            }
            order.save(update_fields=["documents", "documents_details"])

            # Queue background upload only after the outer transaction actually commits
            if uploaded_files:
                from tailored.tasks import upload_tailor_order_documents_task
                order_id, shop_id, user_id = order.id, shop.id, request.user.id
                files_to_upload = uploaded_files
                transaction.on_commit(
                    lambda: upload_tailor_order_documents_task.delay(order_id, files_to_upload, shop_id, user_id)
                )

        success_message = self._handle_send_invoice(order, request.data, request.user, "Tailoring order created successfully.")

        return self.success_response(
            message=success_message,
            data={
                "order_id": order.id,
                "shop_tailor_id": order.shop_tailor_id,
                "client_name": client.client_name if client else None,
                "total_tailor_charge": str(total_tailor),
                "customer_total_charge": str(customer_total_charge),
                "total_additional_charges": str(total_additional),
                "paid_amount": str(paid_amount),
                "discount_amount": str(discount_amount),
                "security_amount": str(security_amount),
            },
            dev_message="Order created successfully with fabric deductions applied."
        )

    except ValidationError as ve:
        if uploaded_files:
            self._cleanup_temp_files(uploaded_files)
        if preview_urls:
            self._cleanup_s3_documents_sync(preview_urls)

        if hasattr(ve, "message_dict"):
            errors = ve.message_dict
            first_field = next(iter(errors))
            first_msg = errors[first_field][0] if errors[first_field] else "Validation error."
            return self.validation_error_response(
                errors=errors,
                message=first_msg
            )
        else:
            msgs = ve.messages if hasattr(ve, "messages") else [str(ve)]
            return self.validation_error_response(
                errors={"detail": msgs},
                message=msgs[0] if msgs else "Validation error."
            )

    except InvalidDateFormat as e:
        return self.validation_error_response(
            errors={"joining_date": [e.dev_message]},
            message=e.message,
            dev_message=e.dev_message
        )

    except PermissionDenied as e:
        logger.error(f"Permission denied: {str(e)}")
        return self.error_response(
            message="You don't have access to this shop.",
            dev_message=str(e)
        )

    except Exception as e:
        if uploaded_files:
            self._cleanup_temp_files(uploaded_files)
        if preview_urls:
            self._cleanup_s3_documents_sync(preview_urls)

        logger.error(f"Unexpected error in TailorOrderViewSet.create: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong. Please try again later.",
            dev_message=str(e)
        )
