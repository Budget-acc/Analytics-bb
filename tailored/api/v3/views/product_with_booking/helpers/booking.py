import json
import logging
from datetime import date
from django.utils import timezone
from django.utils.timezone import make_aware
from booking_v2.models import (
    Client, BookingAdditionalCharge, BookedProductVariant, BookingV2,
    ClientRequirementV2, PaymentDetailsV2, SecurityDepositPayment
)
from shop.models import Staff
from tailored.models import CustomerPayment, TailorOrder
from tailored.api.v3.utils.date_utils import parse_date_string
from booking_v2.api.v5.utils.availability import add_product_variant_availability

logger = logging.getLogger("api_requests")


def _parse_optional_int(value):
    if value in [None, "", "null"]:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _validate_booking_data(self, data, all_variants_info):
    """Validate booking-related data"""
    # Validate client
    client_id = data.get("client_id")
    client_info = {}

    if "client[client_name]" in data:
        client_info = {
            "client_name": data.get("client[client_name]"),
            "client_phone_1": data.get("client[client_phone_1]"),
            "client_phone_2": data.get("client[client_phone_2]"),
            "phone_1_e164": data.get("client[phone_1_e164]"),
            "phone_2_e164": data.get("client[phone_2_e164]")
        }
    else:
        client_info = {
            "client_name": data.get("client_name"),
            "client_phone_1": data.get("client_phone_1"),
            "client_phone_2": data.get("client_phone_2"),
            "phone_1_e164": data.get("phone_1_e164"),
            "phone_2_e164": data.get("phone_2_e164")
        }

    # if not client_id and (not client_info["client_name"] or not client_info["client_phone_1"]):
    if not client_id and not (
        (client_info.get("client_name") and client_info.get("client_phone_1")) or
        (client_info.get("client_name") and client_info.get("phone_1_e164"))
    ):
        return None, "Client name and phone number are required."

    # Validate dates
    pickup_date_str = data.get("pickup_date")
    return_date_str = data.get("return_date")
    cooling_period_end_str = data.get("cooling_period_end")

    if not pickup_date_str or not return_date_str:
        return None, "Pickup date and return date are required."

    try:
        pickup_date = make_aware(parse_date_string(pickup_date_str))
        return_date = make_aware(parse_date_string(return_date_str))

        # if pickup_date.date() < date.today():
        #     return None, "Pickup date cannot be in the past."
        if return_date <= pickup_date:
            return None, "Return time must be after pickup time."

        cooling_period_end = None
        if cooling_period_end_str:
            cooling_period_end = make_aware(parse_date_string(cooling_period_end_str))
            if cooling_period_end < return_date:
                return None, "Cooling period end must be after return date."

    except Exception:
        return None, "Invalid date format."

    # Validate delivery status
    delivery_status = data.get("delivery_status", "booked")
    if delivery_status == "returned":
        return None, "New bookings can't be marked as returned."
    if delivery_status == "delivered" and pickup_date.date() != date.today():
        return None, "Can set to 'delivered' only on pickup day."

    # Calculate total amount from all variants
    product_total = sum([v["amount"] * v["quantity"] for v in all_variants_info])

    # Parse additional charges
    additional_charges = []
    additional_charges_str = data.get("additional_charges")
    if additional_charges_str:
        try:
            additional_charges = json.loads(additional_charges_str)
            if not isinstance(additional_charges, list):
                return None, "Additional charges must be a list."
        except json.JSONDecodeError:
            return None, "Invalid additional charges format."

    additional_total = 0
    for charge in additional_charges:
        if not isinstance(charge, dict) or "name" not in charge or "amount" not in charge:
            return None, "Each additional charge must have 'name' and 'amount'."
        try:
            amount = float(charge["amount"])
            if amount <= 0:
                return None, f"Additional charge '{charge['name']}' amount must be positive."
            additional_total += amount
        except (ValueError, TypeError):
            return None, f"Invalid amount for additional charge '{charge['name']}'."

    total_amount = product_total + additional_total

    # Validate discount
    discount = data.get("discount_amount", 0)
    try:
        discount = int(discount)
        if discount < 0:
            return None, "Discount cannot be negative."
        if discount > total_amount:
            return None, "Discount amount cannot exceed total amount."
    except (ValueError, TypeError):
        return None, "Invalid discount amount."

    # Validate security
    security = data.get("security_amount", 0)
    try:
        security = int(security)
        if security < 0:
            return None, "Security amount cannot be negative."
    except (ValueError, TypeError):
        return None, "Invalid security amount."

    # Validate advance
    advance = data.get("advance_amount", 0)
    try:
        advance = int(advance)
        if advance < 0:
            return None, "Advance amount must be positive."
        final_payable = total_amount - discount
        if advance > final_payable:
            return None, "Advance cannot be more than final total amount."
    except (ValueError, TypeError):
        return None, "Invalid advance amount."

    # Validate client address
    client_address = data.get("client_address")
    # if not client_address:
    #     return None, "Client address is required."

    # Validate details if provided
    details = data.get("details")
    if details is not None:
        try:
            if isinstance(details, str):
                details = json.loads(details)
            if not isinstance(details, dict):
                return None, "Details must be a valid JSON object."
        except json.JSONDecodeError:
            return None, "Invalid details format."

    booking_data = {
        "client_id": client_id,
        "client_info": client_info,
        "pickup_date": pickup_date,
        "return_date": return_date,
        "cooling_period_end": cooling_period_end,
        "delivery_status": delivery_status,
        "total_amount": total_amount,
        "discount": discount,
        "security": security,
        "advance": advance,
        "additional_charges": additional_charges,
        "client_address": client_address,
        "details": details,
        "purchase_mode": data.get("purchase_mode", "normal"),
        "payment_method": data.get("payment_method", "cash"),
        "account_id": _parse_optional_int(data.get("account_id")),
        "security_account_id": _parse_optional_int(data.get("security_account_id")),
        "description": data.get("description", ""),
        "staff_id": data.get("staff_id")
    }

    if data.get("account_id") not in [None, "", "null"] and booking_data["account_id"] is None:
        return None, "Invalid account ID."

    if data.get("security_account_id") not in [None, "", "null"] and booking_data["security_account_id"] is None:
        return None, "Invalid security account ID."

    if security > 0 and not booking_data["security_account_id"]:
        return None, "Security account ID is required when security amount is greater than zero."

    return booking_data, None


def _create_booking_with_multiple_products(self, request, shop, booking_data, all_variants_info, products):
    """Create booking with multiple products and their variants with optimized S3 backup"""
    try:
        from finance.models import Account as _Account

        payment_account = None
        security_account = None

        if booking_data.get("account_id"):
            try:
                payment_account = _Account.objects.get(
                    id=booking_data["account_id"],
                    shop_id=shop.id,
                    deleted__isnull=True,
                    is_active=True,
                )
            except _Account.DoesNotExist:
                return None, "Invalid account selected for payment."

        if booking_data.get("security_account_id"):
            try:
                security_account = _Account.objects.get(
                    id=booking_data["security_account_id"],
                    shop_id=shop.id,
                    deleted__isnull=True,
                    is_active=True,
                )
            except _Account.DoesNotExist:
                return None, "Invalid account selected for security payment."

        # Get or create client
        if booking_data["client_id"]:
            try:
                client = Client.objects.get(id=booking_data["client_id"], shop=shop)
            except Client.DoesNotExist:
                return None, "Client not found."
        else:
            client, created, error_msg = self._get_or_create_client(shop.id, booking_data["client_info"])
            if error_msg:
                return None, error_msg

        # Get staff if provided
        staff = None
        if booking_data["staff_id"]:
            try:
                staff = Staff.objects.get(id=booking_data["staff_id"], shop=shop)
            except Staff.DoesNotExist:
                return None, "Staff not found."

        # Get tailor order details for payment history and creation date
        tailor_order_data = _get_tailor_order_data(self, all_variants_info)

        # Determine booking creation date (use tailor order date if available)
        booking_created_at = tailor_order_data["created_at"] if tailor_order_data else timezone.now()

        # Create booking
        booking = BookingV2.objects.create(
            user=request.user,
            shop=shop,
            client_identity=client,
            client_address=booking_data["client_address"],
            pickup_date=booking_data["pickup_date"],
            return_date=booking_data["return_date"],
            cooling_period_end=booking_data["cooling_period_end"],
            total_amount=booking_data["total_amount"],
            advance_amount=booking_data["advance"],
            discount_amount=booking_data["discount"],
            security_amount=booking_data["security"],
            purchase_mode=booking_data["purchase_mode"],
            payment_status=(booking_data["advance"] >= (booking_data["total_amount"] - booking_data["discount"])),
            delivery_status=booking_data["delivery_status"],
            details=booking_data["details"],
            staff=staff,
            created_at=booking_created_at
        )

        # Create additional charges in bulk
        if booking_data["additional_charges"]:
            charge_objects = [
                BookingAdditionalCharge(
                    booking=booking,
                    name=charge["name"],
                    amount=charge["amount"]
                ) for charge in booking_data["additional_charges"]
            ]
            BookingAdditionalCharge.objects.bulk_create(charge_objects)

        # Prepare booked items with pre-populated backup fields and S3 thumbnails
        booked_items = []

        # Process S3 thumbnail backups in parallel for existing thumbnails
        thumbnail_tasks = []
        variant_thumbnail_map = {}

        for variant_info in all_variants_info:
            variant = variant_info["variant"]
            original_thumbnail = variant.product.thumbnail_image

            if original_thumbnail:
                thumbnail_tasks.append((variant.id, original_thumbnail, shop.id, variant.product.id))

        # Process thumbnails in parallel if any exist
        if thumbnail_tasks:
            variant_thumbnail_map = self._process_thumbnails_parallel(thumbnail_tasks)

        # Create booked items with pre-populated backup fields
        for variant_info in all_variants_info:
            variant = variant_info["variant"]
            measurements = variant_info.get("measurements")

            if isinstance(measurements, dict):
                measurements = [{"name": k, "value": v} for k, v in measurements.items()]

            # Get the S3 backup thumbnail URL (if exists)
            backup_thumbnail = variant_thumbnail_map.get(variant.id, "")

            booked_items.append(BookedProductVariant(
                booking=booking,
                product_variant=variant,
                amount=variant_info["amount"],
                quantity=variant_info["quantity"],
                user=request.user,
                shop=shop,
                measurements=measurements,
                # Pre-populate backup fields to bypass save() method
                product_name=variant.product.name,
                variant_attribute=variant.attribute,
                color=variant.product.color,
                category=variant.product.category,
                model=variant.product.model,
                product_thumbnail=backup_thumbnail
            ))

        # Bulk create with pre-populated backup fields
        BookedProductVariant.objects.bulk_create(booked_items)

        # Update availability for all variants
        stock_end_date = booking_data["cooling_period_end"] if booking_data["cooling_period_end"] else booking_data["return_date"]
        availability_variants = [{
            "id": v["variant"].id,
            "quantity": v["quantity"]
        } for v in all_variants_info]

        add_product_variant_availability(
            availability_variants,
            booking_data["pickup_date"].date(),
            stock_end_date.date()
        )

        # Create client requirement if provided
        if booking_data["description"]:
            ClientRequirementV2.objects.create(
                shop=shop,
                booking=booking,
                description=booking_data["description"]
            )

        success, error = _copy_tailor_payment_history(
                self,
                booking,
                tailor_order_data,
                booking_data["advance"],
                fallback_account=payment_account,
            )
        if not success:
            return None, error

        success, error = _copy_tailor_security_history(
            self,
            booking,
            tailor_order_data,
            requested_security_account=security_account,
        )
        if not success:
            return None, error

        return booking, None

    except Exception as e:
        logger.error(f"Booking creation failed: {str(e)}", exc_info=True)
        return None, f"Failed to create booking: {str(e)}"


def _get_tailor_order_data(self, all_variants_info):
    """
    Extract tailor order information from variants info.

    Returns:
        dict: {"tailor_order_id": int, "created_at": datetime} or None
    """
    # Find the first variant that has tailor_order_item info
    for variant_info in all_variants_info:
        tailor_order_item = variant_info.get('tailor_order_item')
        if tailor_order_item and hasattr(tailor_order_item, 'order'):
            return {
                "tailor_order_id": tailor_order_item.order.id,
                "created_at": tailor_order_item.order.created_at
            }

    return None


def _copy_tailor_payment_history(self, booking, tailor_order_data, expected_advance_amount, fallback_account=None):
    """
    Copy all payment records from CustomerPayment to PaymentDetailsV2.

    Args:
        booking: BookingV2 instance
        tailor_order_data: dict with tailor_order_id and created_at
        expected_advance_amount: int - advance amount from request

    Returns:
        tuple: (success: bool, error_message: str or None)
    """
    if not tailor_order_data or not tailor_order_data.get("tailor_order_id"):
        # No tailor order data - create single payment record as fallback
        payment_method = "cash"
        if fallback_account:
            payment_method = 'cash' if fallback_account.account_type == 'cash' else 'gpay'
        PaymentDetailsV2.objects.create(
            booking=booking,
            payment_amount=expected_advance_amount,
            payment_method=payment_method,
            account=fallback_account,
            created_at=timezone.now()
        )
        if fallback_account and expected_advance_amount > 0:
            from finance.api.v3.utils import create_ledger_entries as _create_ledger_entries
            payment_record = PaymentDetailsV2.objects.filter(
                booking=booking,
                account=fallback_account
            ).order_by('-created_at').first()
            if payment_record:
                _create_ledger_entries(
                    payments=[{
                        'amount': expected_advance_amount,
                        'direction': 'in',
                        'account': fallback_account,
                        'payment_method': payment_method,
                        'reference_id': payment_record.id,
                        'created_at': payment_record.created_at,
                    }],
                    shop=booking.shop,
                    transaction_type='booking_payment',
                    reference_type='PaymentDetailsV2',
                )
        return True, None

    tailor_order_id = tailor_order_data["tailor_order_id"]

    try:
        from finance.api.v3.utils import (
            create_ledger_entries as _create_ledger_entries,
            delete_ledger_entries as _delete_ledger_entries,
        )

        # Fetch all customer payments for this tailor order in single query
        customer_payments = list(CustomerPayment.objects.filter(
            order_id=tailor_order_id
        ).order_by('created_at').values(
            'id',
            'payment_amount',
            'payment_method',
            'account_id',
            'created_at'
        ))

        if not customer_payments:
            # No payment history - create single payment record
            if expected_advance_amount > 0:
                payment_method = "cash"
                if fallback_account:
                    payment_method = 'cash' if fallback_account.account_type == 'cash' else 'gpay'
                payment_record = PaymentDetailsV2.objects.create(
                    booking=booking,
                    payment_amount=expected_advance_amount,
                    payment_method=payment_method,
                    account=fallback_account,
                    created_at=tailor_order_data["created_at"]
                )

                if fallback_account:
                    _create_ledger_entries(
                        payments=[{
                            'amount': expected_advance_amount,
                            'direction': 'in',
                            'account': fallback_account,
                            'payment_method': payment_method,
                            'reference_id': payment_record.id,
                            'created_at': tailor_order_data["created_at"],
                        }],
                        shop=booking.shop,
                        transaction_type='booking_payment',
                        reference_type='PaymentDetailsV2',
                    )
            return True, None

        # Validate: Sum of payment history should match expected advance
        total_payments = sum(p['payment_amount'] for p in customer_payments)

        if abs(total_payments - expected_advance_amount) > 0.01:  # Allow for minor floating point differences
            logger.warning(
                f"Payment history mismatch for tailor order {tailor_order_id}. "
                f"Expected: {expected_advance_amount}, History total: {total_payments}"
            )

        payment_method_mapping = {
            'upi': 'gpay',
            'cash': 'cash'
        }

        # Create payment records preserving exact timestamps and account linkage.
        payment_records_to_create_ledger = []
        old_ledger_reference_ids = []

        for payment in customer_payments:
            # Map payment method from CustomerPayment to PaymentDetailsV2 format
            original_method = payment['payment_method'].lower()
            mapped_method = payment_method_mapping.get(original_method, 'cash')

            payment_record = PaymentDetailsV2.objects.create(
                booking=booking,
                payment_amount=payment['payment_amount'],
                payment_method=mapped_method,
                account_id=payment['account_id'],
                created_at=payment['created_at']
            )

            if payment['account_id']:
                old_ledger_reference_ids.append(payment['id'])
                payment_records_to_create_ledger.append({
                    'amount': payment['payment_amount'],
                    'direction': 'in',
                    'account': payment_record.account,
                    'payment_method': mapped_method,
                    'reference_id': payment_record.id,
                    'created_at': payment['created_at'],
                })

        # Transfer ledger ownership from old tailor payment rows to new booking rows.
        if old_ledger_reference_ids:
            _delete_ledger_entries(
                reference_ids=old_ledger_reference_ids,
                reference_type='CustomerPayment',
            )

        if payment_records_to_create_ledger:
            _create_ledger_entries(
                payments=payment_records_to_create_ledger,
                shop=booking.shop,
                transaction_type='booking_payment',
                reference_type='PaymentDetailsV2',
            )

        logger.info(
            f"Successfully copied {len(customer_payments)} payment records "
            f"from tailor order {tailor_order_id} to booking {booking.id}. "
            f"Total amount: {total_payments}"
        )

        return True, None

    except Exception as e:
        logger.error(f"Error copying payment history from tailor order {tailor_order_id}: {str(e)}", exc_info=True)
        return False, f"Failed to copy payment history: {str(e)}"


def _copy_tailor_security_history(self, booking, tailor_order_data, requested_security_account=None):
    """
    Copy TailorOrder security payment to SecurityDepositPayment and transfer
    ledger ownership from TailorOrderSecurity -> SecurityDepositPayment.
    """
    if not tailor_order_data or not tailor_order_data.get("tailor_order_id"):
        return True, None

    tailor_order_id = tailor_order_data["tailor_order_id"]

    try:
        from finance.models import LedgerEntry
        from finance.api.v3.utils import (
            create_ledger_entries as _create_ledger_entries,
            delete_ledger_entries as _delete_ledger_entries,
        )

        tailor_order = TailorOrder.objects.prefetch_related('security_payments__account').get(id=tailor_order_id)

        security_amount = booking.security_amount or tailor_order.security_amount or 0
        if security_amount <= 0:
            return True, None

        existing_security = SecurityDepositPayment.objects.filter(booking=booking).first()
        if existing_security:
            return True, None

        old_security_ledger = LedgerEntry.objects.filter(
            reference_type='TailorOrderSecurity',
            reference_id=tailor_order.id,
            deleted__isnull=True,
        ).order_by('created_at').first()

        security_created_at = old_security_ledger.created_at if old_security_ledger else tailor_order.created_at
        _sp = tailor_order.security_payments.first() if not requested_security_account else None
        _tailor_account = _sp.account if _sp else None
        target_security_account = requested_security_account or _tailor_account
        security_method = 'cash'
        if target_security_account:
            security_method = 'cash' if target_security_account.account_type == 'cash' else 'upi'

        security_record = SecurityDepositPayment.objects.create(
            booking=booking,
            amount=security_amount,
            payment_method=security_method,
            account=target_security_account,
            is_paid=True,
            paid_at=security_created_at,
            created_at=security_created_at,
        )

        if tailor_order.security_payments.filter(account__isnull=False).exists():
            _delete_ledger_entries(
                reference_ids=[tailor_order.id],
                reference_type='TailorOrderSecurity',
            )

        if target_security_account:
            _create_ledger_entries(
                payments=[{
                    'amount': security_amount,
                    'direction': 'in',
                    'account': target_security_account,
                    'payment_method': security_method,
                    'reference_id': security_record.id,
                    'created_at': security_created_at,
                }],
                shop=booking.shop,
                transaction_type='security_in',
                reference_type='SecurityDepositPayment',
            )

        return True, None
    except TailorOrder.DoesNotExist:
        return False, f"Tailor order {tailor_order_id} not found for security copy."
    except Exception as e:
        logger.error(f"Error copying security history from tailor order {tailor_order_id}: {str(e)}", exc_info=True)
        return False, f"Failed to copy security history: {str(e)}"
