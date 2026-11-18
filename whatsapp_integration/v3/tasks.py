import base64, binascii, logging
import re
from celery import shared_task
from django.core.exceptions import ObjectDoesNotExist
from sales.models import Sale, SaleItem
from booking_v2.models import Client, BookingV2, BookedProductVariant
from tailored.models import TailorOrder
from whatsapp_integration.v3.services import WhatsAppService, notify_whatsapp_limit_exceeded
from whatsapp_integration.v3.pdf_template import BasePDFService, BookingPDFService, SalesPDFService, TailorOrderPDFService
from whatsapp_integration.v3.travelbot_service import resolve_whatsapp_provider, first_name as travelbot_first_name
from django.db.models import Prefetch
from django.db import transaction
from django.utils import timezone
from subscriptions.models import Feature, FeatureUsagePolicy, ShopFeatureLimit, ShopFeatureUsage
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import re
import phonenumbers
from phonenumbers import NumberParseException

logger = logging.getLogger("api_requests")

WHATSAPP_TEXT_VALIDATION_ERROR = (
    "Param text cannot have new-line/tab characters or more than 4 consecutive spaces"
)


def sanitize_whatsapp_text(value: str, fallback: str = "Customer") -> str:
    """
    Normalize text for WhatsApp template parameters.
    Meta rejects new lines, tabs, and long whitespace runs in text params.
    """
    normalized = str(value or "")
    normalized = normalized.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or fallback

def validate_and_format_phone(phone_number: str, field_name="phone"):
    """
    Validates and converts Indian phone numbers into E.164 format.

    Accepted formats:
    - +919876543210
    - 9876543210
    - 0919876543210
    - 919876543210
    - 00919876543210

    Returns:
        (formatted_e164_string, None)
    OR
        (None, error_message)
    """

    if not phone_number:
        return None, f"{field_name} is required."

    raw = str(phone_number).strip()

    # Remove spaces, dashes, brackets etc.
    cleaned = re.sub(r"[^\d+]", "", raw)

    # Case 1: Already E164
    if cleaned.startswith("+"):
        candidate = cleaned

    # Case 2: 10 digit number
    elif re.fullmatch(r"\d{10}", cleaned):
        candidate = "+91" + cleaned

    # Case 3: 11 digit starting with 0
    elif re.fullmatch(r"0\d{10}", cleaned):
        candidate = "+91" + cleaned[1:]

    # Case 4: 12 digit starting with 91
    elif re.fullmatch(r"91\d{10}", cleaned):
        candidate = "+" + cleaned

    # Case 5: 14 digit starting with 0091
    elif re.fullmatch(r"0091\d{10}", cleaned):
        candidate = "+" + cleaned[2:]

    else:
        return None, (
            f"Invalid {field_name} format. "
            "Expected 10 digits or valid Indian country code formats."
        )

    # Final Validation via phonenumbers
    try:
        parsed = phonenumbers.parse(candidate, None)
    except NumberParseException:
        return None, f"Invalid {field_name} format."

    if not phonenumbers.is_valid_number(parsed):
        return None, f"Invalid {field_name} number."

    # Only allow mobile numbers
    number_type = phonenumbers.number_type(parsed)
    if number_type == phonenumbers.PhoneNumberType.UNKNOWN:
        logger.error(
            f"{field_name} {candidate} has UNKNOWN type but is valid. Allowing for WhatsApp."
        )

    # Always return proper E164
    return phonenumbers.format_number(
        parsed,
        phonenumbers.PhoneNumberFormat.E164
    ), None

def _compute_whatsapp_period(subscription, reset_cycle):
    now = timezone.now()
    if reset_cycle == 'MONTHLY':
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if period_start.month == 12:
            period_end = period_start.replace(year=period_start.year + 1, month=1)
        else:
            period_end = period_start.replace(month=period_start.month + 1)
    elif reset_cycle == 'DAILY':
        period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        period_end = period_start + timedelta(days=1)
    else:  # LIFETIME
        period_start = subscription.start_date
        period_end = subscription.end_date
    return period_start, period_end


def check_whatsapp_limit(shop, feature_code='WHATSAPP_MESSAGE'):
    """Check if shop can send a WhatsApp message without incrementing usage.

    Returns (can_send: bool, error_message: str|None)
    """
    try:
        # Get feature
        try:
            feature = Feature.objects.get(code=feature_code, is_active=True)
        except Feature.DoesNotExist:
            return False, f"Feature '{feature_code}' not found or inactive"

        # Check subscription
        if not hasattr(shop, 'subscription') or not shop.subscription:
            return False, f"Shop {shop.id} has no active subscription"

        subscription = shop.subscription
        if not subscription.is_active():
            return False, f"Shop {shop.id} subscription is not active"

        # Get policy
        try:
            policy = FeatureUsagePolicy.objects.get(plan=subscription.plan, feature=feature)
        except FeatureUsagePolicy.DoesNotExist:
            return False, f"No usage policy found for feature '{feature_code}' in plan '{subscription.plan.name}'"

        base_limit = policy.limit
        reset_cycle = policy.reset_cycle

        # extra limit
        extra_limit = 0
        try:
            shop_limit = ShopFeatureLimit.objects.get(shop=shop, feature=feature, is_active=True)
            if shop_limit.valid_until and shop_limit.valid_until < timezone.now():
                logger.warning(f"Shop {shop.id} extra limit expired on {shop_limit.valid_until}")
            else:
                extra_limit = shop_limit.extra_limit
        except ShopFeatureLimit.DoesNotExist:
            pass

        total_limit = base_limit + extra_limit
        if total_limit <= 0:
            return False, f"Shop {shop.id} has zero or negative WhatsApp limit"

        period_start, period_end = _compute_whatsapp_period(subscription, reset_cycle)

        usage = ShopFeatureUsage.objects.filter(shop=shop, feature=feature, period_start=period_start).first()
        used = usage.used if usage else 0

        if used >= total_limit:
            logger.warning(
                f"Shop {shop.id} WhatsApp limit exceeded: {used}/{total_limit} (base: {base_limit}, extra: {extra_limit})"
            )
            return False, f"WhatsApp message limit exceeded ({used}/{total_limit})"

        return True, None
    except Exception as e:
        logger.error(f"Error checking WhatsApp usage for shop {getattr(shop, 'id', None)}: {str(e)}", exc_info=True)
        return False, f"Error checking usage: {str(e)}"


def increment_whatsapp_usage(shop, feature_code='WHATSAPP_MESSAGE'):
    """Reserve the next WhatsApp message allowance for a send.

    This should be called immediately before the actual WhatsApp send
    and under a select_for_update lock to prevent concurrent oversends.

    Returns (ok: bool, error_message: str|None)
    """
    try:
        with transaction.atomic():
            feature = Feature.objects.select_for_update().get(code=feature_code, is_active=True)

            if not hasattr(shop, 'subscription') or not shop.subscription:
                return False, f"Shop {shop.id} has no active subscription"

            subscription = shop.subscription
            try:
                policy = FeatureUsagePolicy.objects.select_for_update().get(plan=subscription.plan, feature=feature)
            except FeatureUsagePolicy.DoesNotExist:
                return False, f"No usage policy found for feature '{feature_code}' in plan '{subscription.plan.name}'"

            base_limit = policy.limit
            reset_cycle = policy.reset_cycle

            extra_limit = 0
            try:
                shop_limit = ShopFeatureLimit.objects.select_for_update().get(shop=shop, feature=feature, is_active=True)
                if shop_limit.valid_until and shop_limit.valid_until < timezone.now():
                    logger.warning(f"Shop {shop.id} extra limit expired on {shop_limit.valid_until}")
                else:
                    extra_limit = shop_limit.extra_limit
            except ShopFeatureLimit.DoesNotExist:
                pass

            total_limit = base_limit + extra_limit
            if total_limit <= 0:
                return False, f"Shop {shop.id} has zero or negative WhatsApp limit"

            period_start, period_end = _compute_whatsapp_period(subscription, reset_cycle)

            usage, created = ShopFeatureUsage.objects.select_for_update().get_or_create(
                shop=shop,
                feature=feature,
                period_start=period_start,
                defaults={'period_end': period_end, 'used': 0}
            )

            if usage.used >= total_limit:
                return False, f"WhatsApp message limit exceeded ({usage.used}/{total_limit})"

            usage.used += 1
            usage.save(update_fields=['used'])

            logger.info(
                f"Shop {shop.id} WhatsApp usage reserved: {usage.used}/{total_limit} "
                f"(base: {base_limit}, extra: {extra_limit}, period: {reset_cycle})"
            )

            return True, None
    except Exception as e:
        logger.error(f"Error reserving WhatsApp usage for shop {getattr(shop, 'id', None)}: {str(e)}", exc_info=True)
        return False, f"Error reserving usage: {str(e)}"


def release_whatsapp_usage(shop, feature_code='WHATSAPP_MESSAGE'):
    """Release a previously reserved WhatsApp usage count when send does not complete."""
    try:
        with transaction.atomic():
            feature = Feature.objects.select_for_update().get(code=feature_code, is_active=True)

            if not hasattr(shop, 'subscription') or not shop.subscription:
                return False, f"Shop {shop.id} has no active subscription"

            subscription = shop.subscription
            try:
                policy = FeatureUsagePolicy.objects.select_for_update().get(plan=subscription.plan, feature=feature)
            except FeatureUsagePolicy.DoesNotExist:
                return False, f"No usage policy found for feature '{feature_code}' in plan '{subscription.plan.name}'"

            base_limit = policy.limit
            reset_cycle = policy.reset_cycle

            extra_limit = 0
            try:
                shop_limit = ShopFeatureLimit.objects.select_for_update().get(shop=shop, feature=feature, is_active=True)
                if shop_limit.valid_until and shop_limit.valid_until < timezone.now():
                    logger.warning(f"Shop {shop.id} extra limit expired on {shop_limit.valid_until}")
                else:
                    extra_limit = shop_limit.extra_limit
            except ShopFeatureLimit.DoesNotExist:
                pass

            total_limit = base_limit + extra_limit
            if total_limit <= 0:
                return False, f"Shop {shop.id} has zero or negative WhatsApp limit"

            period_start, period_end = _compute_whatsapp_period(subscription, reset_cycle)

            usage = ShopFeatureUsage.objects.select_for_update().filter(
                shop=shop,
                feature=feature,
                period_start=period_start,
            ).first()

            if not usage or usage.used <= 0:
                logger.warning(f"No reserved WhatsApp usage to release for shop {shop.id}")
                return False, f"No reserved usage to release for shop {shop.id}"

            usage.used -= 1
            usage.save(update_fields=['used'])

            logger.info(
                f"Shop {shop.id} WhatsApp usage released: {usage.used}/{total_limit} "
                f"(base: {base_limit}, extra: {extra_limit}, period: {reset_cycle})"
            )

            return True, None
    except Exception as e:
        logger.error(f"Error releasing WhatsApp usage for shop {getattr(shop, 'id', None)}: {str(e)}", exc_info=True)
        return False, f"Error releasing usage: {str(e)}"


def increment_whatsapp_usage_unconditional(shop, feature_code='WHATSAPP_MESSAGE'):
    """
    Record one more successfully-sent WhatsApp message for this shop/period,
    with NO limit check and no possibility of being refused.

    Used only for sends made through the shop's own Wayon token
    (provider == "shop_wayon"): that token is the shop's own, so
    BookieBuddy's WHATSAPP_MESSAGE limit must never block or reserve
    against it (see check_whatsapp_limit/increment_whatsapp_usage, used for
    bookiebuddy_wayon/wacto instead) - but the send still needs to be
    counted so the total-messages-sent record stays accurate. Call this
    only after the send has been confirmed successful; never on failure.

    Mirrors increment_whatsapp_usage's period/locking mechanics, minus the
    `if usage.used >= total_limit: return False` gate - this must never
    refuse to count a message that was actually sent.
    """
    try:
        with transaction.atomic():
            feature = Feature.objects.select_for_update().get(code=feature_code, is_active=True)

            if not hasattr(shop, 'subscription') or not shop.subscription:
                return False, f"Shop {shop.id} has no active subscription"

            subscription = shop.subscription
            try:
                policy = FeatureUsagePolicy.objects.select_for_update().get(plan=subscription.plan, feature=feature)
            except FeatureUsagePolicy.DoesNotExist:
                return False, f"No usage policy found for feature '{feature_code}' in plan '{subscription.plan.name}'"

            reset_cycle = policy.reset_cycle
            period_start, period_end = _compute_whatsapp_period(subscription, reset_cycle)

            usage, created = ShopFeatureUsage.objects.select_for_update().get_or_create(
                shop=shop,
                feature=feature,
                period_start=period_start,
                defaults={'period_end': period_end, 'used': 0}
            )

            usage.used += 1
            usage.save(update_fields=['used'])

            logger.info(
                f"Shop {shop.id} WhatsApp usage recorded (shop-owned Wayon, unmetered): "
                f"{usage.used} (period: {reset_cycle})"
            )

            return True, None
    except Exception as e:
        logger.error(f"Error recording shop-owned-Wayon WhatsApp usage for shop {getattr(shop, 'id', None)}: {str(e)}", exc_info=True)
        return False, f"Error recording usage: {str(e)}"


@shared_task(bind=True, max_retries=3)
def send_sale_invoice_task(self, sale_id: int):
    """
    Generate sale PDF, upload to S3, and send via WhatsApp.
    Validates phone numbers and uses public S3 URLs for Wacto API.
    """
    try:
        # Fetch sale with related objects
        sale = (
            Sale.objects
            .select_related("shop", "client", "staff")
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=SaleItem.objects.select_related(
                        "product_variant",
                        "product_variant__product",
                        "product_variant__product__shop_service",
                        "product_variant__product__shop_service__general_service",
                        "product_variant__product__shop_service__general_service__main_category",
                    )
                ),
                "payments",
            )
            .get(id=sale_id)
        )

        shop = sale.shop

        # Validate client phone number
        # client_phone_raw = sale.client_phone
        client_phone_raw = str(sale.client_phone_e164) if sale.client_phone_e164 else None 
        if not client_phone_raw:
            error_msg = f"Sale {sale.id} has no client phone number. Cannot send WhatsApp invoice."
            logger.error(error_msg)
            raise ValueError(error_msg)

        client_phone, client_phone_error = validate_and_format_phone(client_phone_raw)
        if client_phone_error:
            error_msg = f"Invalid client phone number for sale {sale.id}: {client_phone_error}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Validate shop name
        shop_name = shop.name if hasattr(shop, 'name') and shop.name else None
        if not shop_name:
            error_msg = f"Shop {shop.id} has no name. Cannot send WhatsApp invoice."
            logger.error(error_msg)
            raise ValueError(error_msg)
        shop_name = sanitize_whatsapp_text(shop_name, fallback="Shop")

        # Validate shop phone number
        shop_phone_raw = shop.phone if hasattr(shop, 'phone') else None
        if not shop_phone_raw:
            error_msg = f"Shop {shop.id} has no phone number. Cannot send WhatsApp invoice."
            logger.error(error_msg)
            raise ValueError(error_msg)

        shop_phone, shop_phone_error = validate_and_format_phone(shop_phone_raw)
        if shop_phone_error:
            error_msg = f"Invalid shop phone number for sale {sale.id}: {shop_phone_error}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Get customer name (fallback to "Customer" if not available)
        customer_name = "Customer"
        if sale.client and hasattr(sale.client, 'client_name') and sale.client.client_name:
            customer_name = sale.client.client_name
        customer_name = sanitize_whatsapp_text(customer_name, fallback="Customer")

        # Resolve provider first - shop_wayon (the shop's own token) is
        # exempt from BookieBuddy's WHATSAPP_MESSAGE limit/reservation
        # entirely; bookiebuddy_wayon and wacto both keep the existing
        # gated behavior unchanged.
        provider = resolve_whatsapp_provider(shop)
        gate_usage = provider["provider"] != "shop_wayon"

        # Check WhatsApp usage limit before proceeding (no side-effects)
        if gate_usage:
            can_send, limit_error = check_whatsapp_limit(shop)

            if not can_send:
                logger.warning(
                    f"Skipping sale invoice for sale {sale.id}: {limit_error}"
                )
                try:
                    notify_whatsapp_limit_exceeded(shop, "sale", sale.shop_sale_id or sale.id, content_object=sale)
                except Exception:
                    logger.error(f"Failed to send WhatsApp-limit-exceeded notification for sale {sale.id}", exc_info=True)
                # Don't raise error, just return False to indicate message wasn't sent
                return False

        # Generate PDF
        logger.info(f"Generating PDF for Sale {sale.id}")
        pdf_base64 = SalesPDFService.generate_sale_pdf(
            shop=shop,
            client=sale.client_phone,
            sale=sale,
            items=list(sale.items.all()),
            invoice_prefix="SAL"
        )

        # Validate base64
        try:
            base64.b64decode(pdf_base64, validate=True)
        except (binascii.Error, ValueError):
            error_msg = f"Invalid base64 PDF generated for sale {sale.id}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Upload PDF to S3 to get public URL (Wacto requires PUBLIC URLs!)
        filename = f"Sale_{sale.shop_sale_id or sale.id}.pdf"
        logger.info(f"Uploading PDF to S3: {filename}")

        whatsapp_service = WhatsAppService()
        pdf_url = whatsapp_service.upload_pdf_to_s3(pdf_base64, filename)

        if not pdf_url:
            error_msg = f"Failed to upload PDF to S3 for sale {sale.id}"
            logger.error(error_msg)
            raise Exception(error_msg)

        # Reserve WhatsApp usage count immediately before the actual send -
        # only for bookiebuddy_wayon/wacto (shop_wayon is never reserved
        # against BookieBuddy's limit; see the unconditional post-send
        # increment below instead).
        if gate_usage:
            reserved_ok, reserved_err = increment_whatsapp_usage(shop)
            if not reserved_ok:
                logger.warning(
                    f"Skipping sale invoice for sale {sale.id}: {reserved_err}"
                )
                try:
                    notify_whatsapp_limit_exceeded(shop, "sale", sale.shop_sale_id or sale.id, content_object=sale)
                except Exception:
                    logger.error(f"Failed to send WhatsApp-limit-exceeded notification for sale {sale.id}", exc_info=True)
                return False

        try:
            # Send via WhatsApp with S3 URL
            logger.info(f"Sending WhatsApp invoice for sale {sale.id}")
            logger.info(f"  Customer: {customer_name}")
            logger.info(f"  Shop: {shop_name}")
            logger.info(f"  Client Phone: {client_phone}")
            logger.info(f"  Shop Phone: {shop_phone}")
            logger.info(f"  PDF URL: {pdf_url}")

            if provider["provider"] == "shop_wayon":
                response = provider["service"].send_invoice_template(
                    client_phone,
                    travelbot_first_name(customer_name),
                    "sale_invoice_2",
                    pdf_url,
                    filename,
                )
            elif provider["provider"] == "bookiebuddy_wayon":
                formatted_shop_phone = whatsapp_service._format_phone_number(shop_phone)
                shop_phone_display = whatsapp_service._with_plus(formatted_shop_phone)
                response = provider["service"].send_bookiebuddy_invoice_template(
                    client_phone,
                    travelbot_first_name(customer_name),
                    shop_name,
                    shop_phone_display,
                    formatted_shop_phone,
                    "sale_invoice_2",
                    pdf_url,
                    filename,
                )
            else:
                response = whatsapp_service.send_sale_invoice_template(
                    phone_number=client_phone,
                    customer_name=customer_name,
                    shop_name=shop_name,
                    shop_phone=shop_phone,
                    pdf_url=pdf_url,
                    filename=filename
                )
        except Exception:
            if gate_usage:
                release_ok, release_err = release_whatsapp_usage(shop)
                if not release_ok:
                    logger.error(f"Failed to release reserved WhatsApp usage for sale {sale.id}: {release_err}")
            raise

        if not response.get("success"):
            error_text = response.get("error", "")
            status_code = response.get("status_code", None)
            logger.error(
                f"Failed to send WhatsApp sale invoice: "
                f"SaleID={sale.id}, ShopID={shop.id}, ClientPhone={client_phone}, ShopPhone={shop_phone}, CustomerName={customer_name}, "
                f"Payload={{'phone_number': {client_phone}, 'customer_name': {customer_name}, 'shop_name': {shop_name}, 'shop_phone': {shop_phone}, 'pdf_url': {pdf_url}, 'filename': {filename}}}, "
                f"Error={error_text}, StatusCode={status_code}, Response={response}"
            )
            if gate_usage:
                release_ok, release_err = release_whatsapp_usage(shop)
                if not release_ok:
                    logger.error(f"Failed to release reserved WhatsApp usage for sale {sale.id}: {release_err}")
            if WHATSAPP_TEXT_VALIDATION_ERROR in error_text:
                raise ValueError(error_text)
            if "replace" in error_text or "undefined" in error_text:
                raise ValueError(error_text)
            raise Exception(error_text)

        if not gate_usage:
            # shop_wayon: never reserved/gated against BookieBuddy's limit -
            # record the successful send now, unconditionally, purely for
            # an accurate total-sent count.
            increment_whatsapp_usage_unconditional(shop)

        logger.info(f"WhatsApp invoice sent successfully for sale {sale.id}")
        return True

    except ObjectDoesNotExist as e:
        error_msg = f"Sale not found: {e}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    except ValueError as e:
        # Don't retry for validation errors
        logger.error(f"Validation error in send_sale_invoice_task: {e}")
        raise
    except Exception as e:
        logger.error(f"Exception in send_sale_invoice_task: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True, max_retries=3)
def send_booking_invoice_task(self, booking_id: int):
    """
    Generate booking invoice PDF and send via WhatsApp in the background.
    Validates and formats phone numbers before sending.
    """
    try:
        # Fetch booking with related objects
        booking = (
            BookingV2.objects
            .select_related(
                "shop",
                "client_identity",
                "staff",
                "service__general_service__main_category",
            )
            .prefetch_related(
                Prefetch(
                    "booked_items",
                    queryset=BookedProductVariant.objects.filter(
                        deleted__isnull=True
                    ).select_related(
                        "product_variant__product__shop_service__general_service__main_category"
                    ),
                ),
                "additional_charges",
            )
            .get(id=booking_id)
        )

        # Extract main service name from active booked items (prefetched, zero extra queries).
        # Falls back to booking.service FK (select_related) if all items are soft-deleted.
        main_service_name = ""
        for item in booking.booked_items.all():
            try:
                name = (
                    item.product_variant
                    .product
                    .shop_service
                    .general_service
                    .main_category
                    .name
                )
                if name:
                    main_service_name = name
                    break
            except AttributeError:
                continue

        if not main_service_name:
            try:
                main_service_name = (
                    booking.service.general_service.main_category.name or ""
                )
            except AttributeError:
                pass

        client = booking.client_identity  
        shop = booking.shop

        # Validate client phone number
        client_phone_raw = str(client.phone_1_e164) if client.phone_1_e164 else None
        if not client_phone_raw:
            error_msg = f"Client {client.id} has no phone number. Cannot send WhatsApp invoice."
            logger.error(error_msg)
            raise ValueError(error_msg)

        client_phone, client_phone_error = validate_and_format_phone(client_phone_raw)
        if client_phone_error:
            error_msg = f"Invalid client phone number for booking {booking.id}: {client_phone_error}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Validate shop name
        shop_name = shop.name if hasattr(shop, 'name') and shop.name else None
        if not shop_name:
            error_msg = f"Shop {shop.id} has no name. Cannot send WhatsApp invoice."
            logger.error(error_msg)
            raise ValueError(error_msg)
        shop_name = sanitize_whatsapp_text(shop_name, fallback="Shop")

        # Validate shop phone number
        shop_phone_raw = shop.phone if hasattr(shop, 'phone') else None
        if not shop_phone_raw:
            error_msg = f"Shop {shop.id} has no phone number. Cannot send WhatsApp invoice."
            logger.error(error_msg)
            raise ValueError(error_msg)

        shop_phone, shop_phone_error = validate_and_format_phone(shop_phone_raw)
        if shop_phone_error:
            error_msg = f"Invalid shop phone number for booking {booking.id}: {shop_phone_error}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Validate customer name
        customer_name = client.client_name if hasattr(client, 'client_name') and client.client_name else "Customer"
        if not customer_name or not customer_name.strip():
            customer_name = "Customer"  # Default fallback
        customer_name = sanitize_whatsapp_text(customer_name, fallback="Customer")

        # Resolve provider first - shop_wayon (the shop's own token) is
        # exempt from BookieBuddy's WHATSAPP_MESSAGE limit/reservation
        # entirely; bookiebuddy_wayon and wacto both keep the existing
        # gated behavior unchanged.
        provider = resolve_whatsapp_provider(shop)
        gate_usage = provider["provider"] != "shop_wayon"

        # Check WhatsApp usage limit before proceeding (no side-effects)
        if gate_usage:
            can_send, limit_error = check_whatsapp_limit(shop)

            if not can_send:
                logger.warning(
                    f"Skipping booking invoice for booking {booking.id}: {limit_error}"
                )
                try:
                    notify_whatsapp_limit_exceeded(shop, "booking", booking.shop_booking_id or booking.id, content_object=booking)
                except Exception:
                    logger.error(f"Failed to send WhatsApp-limit-exceeded notification for booking {booking.id}", exc_info=True)
                # Don't raise error, just return False to indicate message wasn't sent
                return False

        # Generate PDF
        logger.info(f"Generating PDF for Booking {booking.id}")
        pdf_base64 = BookingPDFService.generate_transaction_pdf(
            shop=shop,
            client=client,
            transaction=booking,
            items=list(booking.booked_items.all()),
            invoice_prefix="BOOK",
            main_service_name=main_service_name,
        )

        # Validate base64
        try:
            base64.b64decode(pdf_base64, validate=True)
        except (binascii.Error, ValueError):
            error_msg = f"Invalid base64 PDF generated for booking {booking.id}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Upload PDF to S3 to get public URL (Wacto requires PUBLIC URLs!)
        filename = BasePDFService.build_invoice_filename(customer_name, booking.shop_booking_id or booking.id)
        logger.info(f"Uploading PDF to S3: {filename}")
        
        whatsapp_service = WhatsAppService()
        pdf_url = whatsapp_service.upload_pdf_to_s3(pdf_base64, filename)
        
        if not pdf_url:
            error_msg = f"Failed to upload PDF to S3 for booking {booking.id}"
            logger.error(error_msg)
            raise Exception(error_msg)

        if gate_usage:
            reserved_ok, reserved_err = increment_whatsapp_usage(shop)
            if not reserved_ok:
                logger.warning(
                    f"Skipping booking invoice for booking {booking.id}: {reserved_err}"
                )
                try:
                    notify_whatsapp_limit_exceeded(shop, "booking", booking.shop_booking_id or booking.id, content_object=booking)
                except Exception:
                    logger.error(f"Failed to send WhatsApp-limit-exceeded notification for booking {booking.id}", exc_info=True)
                return False

        try:
            # Send via WhatsApp with S3 URL
            logger.info(f"Sending WhatsApp invoice for booking {booking.id}")
            logger.info(f"  Customer: {customer_name}")
            logger.info(f"  Shop: {shop_name}")
            logger.info(f"  Client Phone: {client_phone}")
            logger.info(f"  Shop Phone: {shop_phone}")
            logger.info(f"  PDF URL: {pdf_url}")

            if provider["provider"] == "shop_wayon":
                response = provider["service"].send_invoice_template(
                    client_phone,
                    travelbot_first_name(customer_name),
                    "rental_invoice_5",
                    pdf_url,
                    filename,
                )
            elif provider["provider"] == "bookiebuddy_wayon":
                formatted_shop_phone = whatsapp_service._format_phone_number(shop_phone)
                shop_phone_display = whatsapp_service._with_plus(formatted_shop_phone)
                response = provider["service"].send_bookiebuddy_invoice_template(
                    client_phone,
                    travelbot_first_name(customer_name),
                    shop_name,
                    shop_phone_display,
                    formatted_shop_phone,
                    "rental_invoice_5",
                    pdf_url,
                    filename,
                )
            else:
                response = whatsapp_service.send_booking_invoice_template(
                    phone_number=client_phone,
                    customer_name=customer_name,  # Validated customer name
                    shop_name=shop_name,  # Validated shop name
                    shop_phone=shop_phone,  # Formatted 10-digit shop phone
                    pdf_url=pdf_url,  # Public S3 URL
                    filename=filename
                )
        except Exception:
            if gate_usage:
                release_ok, release_err = release_whatsapp_usage(shop)
                if not release_ok:
                    logger.error(f"Failed to release reserved WhatsApp usage for booking {booking.id}: {release_err}")
            raise

        if not response.get("success"):
            error_text = response.get("error", "")
            status_code = response.get("status_code", None)
            logger.error(
                f"Failed to send WhatsApp booking invoice: "
                f"BookingID={booking.id}, ShopID={shop.id}, ClientPhone={client_phone}, ShopPhone={shop_phone}, CustomerName={customer_name}, "
                f"Payload={{'phone_number': {client_phone}, 'customer_name': {customer_name}, 'shop_name': {shop_name}, 'shop_phone': {shop_phone}, 'pdf_url': {pdf_url}, 'filename': {filename}}}, "
                f"Error={error_text}, StatusCode={status_code}, Response={response}"
            )
            if gate_usage:
                release_ok, release_err = release_whatsapp_usage(shop)
                if not release_ok:
                    logger.error(f"Failed to release reserved WhatsApp usage for booking {booking.id}: {release_err}")
            if WHATSAPP_TEXT_VALIDATION_ERROR in error_text:
                raise ValueError(error_text)
            if "replace" in error_text or "undefined" in error_text:
                raise ValueError(error_text)
            raise Exception(error_text)

        if not gate_usage:
            increment_whatsapp_usage_unconditional(shop)

        logger.info(f"WhatsApp invoice sent successfully for booking {booking.id}")
        return True

    except ObjectDoesNotExist as e:
        error_msg = f"Booking or Client not found: {e}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    except ValueError as e:
        # Don't retry for validation errors
        logger.error(f"Validation error in send_booking_invoice_task: {e}")
        raise
    except Exception as e:
        logger.error(f"Exception in send_booking_invoice_task: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True, max_retries=3)
def send_tailor_order_invoice_task(self, order_id: int):
    """
    Generate tailor order invoice PDF, upload to S3, and send via WhatsApp.
    Validates phone numbers and uses public S3 URLs for Wacto API.
    """
    try:
        order = (
            TailorOrder.objects
            .select_related("shop", "client", "staff")
            .prefetch_related(
                "items",
                "additional_charges",
                "payments",
                "security_payments",
                "security_adjustments",
                "refund_entries",
            )
            .get(id=order_id)
        )
        shop = order.shop

        # Resolve client phone (prefer E164 from linked client, fallback to stored number)
        client_phone_raw = None
        if order.client and order.client.phone_1_e164:
            client_phone_raw = str(order.client.phone_1_e164)
        if not client_phone_raw and order.client_phone_number:
            client_phone_raw = str(order.client_phone_number)
        if not client_phone_raw:
            error_msg = f"TailorOrder {order.id} has no client phone number. Cannot send WhatsApp invoice."
            logger.error(error_msg)
            raise ValueError(error_msg)

        client_phone, client_phone_error = validate_and_format_phone(client_phone_raw)
        if client_phone_error:
            error_msg = f"Invalid client phone number for tailor order {order.id}: {client_phone_error}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Validate shop name
        shop_name = shop.name if hasattr(shop, 'name') and shop.name else None
        if not shop_name:
            error_msg = f"Shop {shop.id} has no name. Cannot send WhatsApp invoice."
            logger.error(error_msg)
            raise ValueError(error_msg)
        shop_name = sanitize_whatsapp_text(shop_name, fallback="Shop")

        # Validate shop phone number
        shop_phone_raw = shop.phone if hasattr(shop, 'phone') else None
        if not shop_phone_raw:
            error_msg = f"Shop {shop.id} has no phone number. Cannot send WhatsApp invoice."
            logger.error(error_msg)
            raise ValueError(error_msg)

        shop_phone, shop_phone_error = validate_and_format_phone(shop_phone_raw)
        if shop_phone_error:
            error_msg = f"Invalid shop phone number for tailor order {order.id}: {shop_phone_error}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Customer name
        customer_name = "Customer"
        if order.client and hasattr(order.client, 'client_name') and order.client.client_name:
            customer_name = order.client.client_name
        customer_name = sanitize_whatsapp_text(customer_name, fallback="Customer")

        # Resolve provider first - shop_wayon (the shop's own token) is
        # exempt from BookieBuddy's WHATSAPP_MESSAGE limit/reservation
        # entirely; bookiebuddy_wayon and wacto both keep the existing
        # gated behavior unchanged.
        provider = resolve_whatsapp_provider(shop)
        gate_usage = provider["provider"] != "shop_wayon"

        # Check WhatsApp usage limit before proceeding (no side-effects)
        if gate_usage:
            can_send, limit_error = check_whatsapp_limit(shop)
            if not can_send:
                logger.warning(f"Skipping tailor order invoice for order {order.id}: {limit_error}")
                try:
                    notify_whatsapp_limit_exceeded(shop, "tailor_order", order.shop_tailor_id or order.id, content_object=order)
                except Exception:
                    logger.error(f"Failed to send WhatsApp-limit-exceeded notification for tailor order {order.id}", exc_info=True)
                return False

        # Generate PDF
        logger.info(f"Generating PDF for TailorOrder {order.id}")
        pdf_base64 = TailorOrderPDFService.generate_tailor_order_pdf(
            shop=shop,
            client=order.client,
            tailor_order=order,
            items=list(order.items.all()),
        )

        # Validate base64
        try:
            base64.b64decode(pdf_base64, validate=True)
        except (binascii.Error, ValueError):
            error_msg = f"Invalid base64 PDF generated for tailor order {order.id}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Upload PDF to S3 to get public URL (Wacto requires PUBLIC URLs!)
        filename = BasePDFService.build_invoice_filename(customer_name, order.shop_tailor_id or order.id)
        logger.info(f"Uploading PDF to S3: {filename}")

        whatsapp_service = WhatsAppService()
        pdf_url = whatsapp_service.upload_pdf_to_s3(pdf_base64, filename)

        if not pdf_url:
            error_msg = f"Failed to upload PDF to S3 for tailor order {order.id}"
            logger.error(error_msg)
            raise Exception(error_msg)

        if gate_usage:
            reserved_ok, reserved_err = increment_whatsapp_usage(shop)
            if not reserved_ok:
                logger.warning(
                    f"Skipping tailor order invoice for order {order.id}: {reserved_err}"
                )
                try:
                    notify_whatsapp_limit_exceeded(shop, "tailor_order", order.shop_tailor_id or order.id, content_object=order)
                except Exception:
                    logger.error(f"Failed to send WhatsApp-limit-exceeded notification for tailor order {order.id}", exc_info=True)
                return False

        try:
            # Send via WhatsApp with S3 URL
            logger.info(f"Sending WhatsApp invoice for tailor order {order.id}")
            logger.info(f"  Customer: {customer_name}")
            logger.info(f"  Shop: {shop_name}")
            logger.info(f"  Client Phone: {client_phone}")
            logger.info(f"  Shop Phone: {shop_phone}")
            logger.info(f"  PDF URL: {pdf_url}")

            if provider["provider"] == "shop_wayon":
                response = provider["service"].send_invoice_template(
                    client_phone,
                    travelbot_first_name(customer_name),
                    "custom_work_2",
                    pdf_url,
                    filename,
                )
            elif provider["provider"] == "bookiebuddy_wayon":
                formatted_shop_phone = whatsapp_service._format_phone_number(shop_phone)
                shop_phone_display = whatsapp_service._with_plus(formatted_shop_phone)
                response = provider["service"].send_bookiebuddy_invoice_template(
                    client_phone,
                    travelbot_first_name(customer_name),
                    shop_name,
                    shop_phone_display,
                    formatted_shop_phone,
                    "custom_work_2",
                    pdf_url,
                    filename,
                )
            else:
                response = whatsapp_service.send_tailor_order_invoice_template(
                    phone_number=client_phone,
                    customer_name=customer_name,
                    shop_name=shop_name,
                    shop_phone=shop_phone,
                    pdf_url=pdf_url,
                    filename=filename,
                )
        except Exception:
            if gate_usage:
                release_ok, release_err = release_whatsapp_usage(shop)
                if not release_ok:
                    logger.error(f"Failed to release reserved WhatsApp usage for tailor order {order.id}: {release_err}")
            raise

        if not response.get("success"):
            error_text = response.get("error", "")
            status_code = response.get("status_code", None)
            logger.error(
                f"Failed to send WhatsApp tailor order invoice: "
                f"OrderID={order.id}, ShopID={shop.id}, ClientPhone={client_phone}, ShopPhone={shop_phone}, CustomerName={customer_name}, "
                f"Error={error_text}, StatusCode={status_code}, Response={response}"
            )
            if gate_usage:
                release_ok, release_err = release_whatsapp_usage(shop)
                if not release_ok:
                    logger.error(f"Failed to release reserved WhatsApp usage for tailor order {order.id}: {release_err}")
            if WHATSAPP_TEXT_VALIDATION_ERROR in error_text:
                raise ValueError(error_text)
            if "replace" in error_text or "undefined" in error_text:
                raise ValueError(error_text)
            raise Exception(error_text)

        if not gate_usage:
            increment_whatsapp_usage_unconditional(shop)

        logger.info(f"WhatsApp invoice sent successfully for tailor order {order.id}")
        return True

    except ObjectDoesNotExist as e:
        error_msg = f"TailorOrder not found: {e}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    except ValueError as e:
        logger.error(f"Validation error in send_tailor_order_invoice_task: {e}")
        raise
    except Exception as e:
        logger.error(f"Exception in send_tailor_order_invoice_task: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True)
def send_pickup_ready_notifications(self):
    """
    Send 'Pickup Ready' WhatsApp notifications for today's pickup bookings.
    Only sends to shops with active subscriptions and WHATSAPP_MESSAGE feature.
    Validates pickup time hasn't expired. For same-day bookings (pickup = return),
    only sends pickup notification if pickup time hasn't passed yet.
    """
    from booking_v2.models import BookingV2
    from subscriptions.models import has_feature_access
    from shop.models import ShopUser
    from django.conf import settings
    
    try:
        # Check if WhatsApp reminders are enabled
        if not settings.WHATSAPP_REMINDERS_ENABLED:
            logger.info("WhatsApp pickup ready notifications are disabled (WHATSAPP_REMINDERS_ENABLED=false)")
            return {"sent": 0, "failed": 0, "disabled": True}
        
        now = timezone.now()
        today = now.date()
        logger.info(f"Starting pickup ready notifications for {today} at {now.time()}")
        
        # Fetch bookings with optimized queries
        bookings = (
            BookingV2.objects
            .filter(
                pickup_date__date=today,
                delivery_status__in=['booked', 'ready to deliver'],
                pickup_date__gt=now  # Pickup time must be in the future
            )
            .select_related(
                'shop',
                'shop__subscription',
                'client_identity'
            )
            .only(
                'id',
                'pickup_date',
                'return_date',
                'shop__id',
                'shop__name',
                'shop__phone',
                'client_identity__client_name',
                'client_identity__phone_1_e164'
            )
        )
        
        # Group bookings by shop for batch processing
        from collections import defaultdict
        bookings_by_shop = defaultdict(list)
        
        for booking in bookings:
            # Skip if no client identity or phone
            if not booking.client_identity or not booking.client_identity.phone_1_e164:
                logger.warning(f"Booking {booking.id} has no client phone, skipping")
                continue
            
            # For same-day bookings (pickup and return on same day)
            # Skip pickup notification if pickup time has already passed
            if booking.return_date and booking.return_date.date() == today:
                if booking.pickup_date <= now:
                    logger.info(f"Booking {booking.id} is same-day with passed pickup time, skipping pickup notification")
                    continue
                
            # Check shop has subscription
            if not hasattr(booking.shop, 'subscription') or not booking.shop.subscription:
                continue
                
            # Check subscription is active
            if not booking.shop.subscription.is_active():
                continue
                
            bookings_by_shop[booking.shop.id].append(booking)
        
        # Process bookings by shop
        total_sent = 0
        total_failed = 0
        
        whatsapp_service = WhatsAppService()
        
        for shop_id, shop_bookings in bookings_by_shop.items():
            # Get a shop user to check feature access (use first booking's shop)
            shop = shop_bookings[0].shop
            
            # Check if any user in shop has WHATSAPP_MESSAGE feature
            shop_users = ShopUser.objects.filter(shop_id=shop_id, is_active=True)
            has_feature = False
            
            for shop_user in shop_users:
                if has_feature_access(shop_user, "BOOKING_REMINDERS_WHATSAPP"):
                    has_feature = True
                    break
            
            if not has_feature:
                logger.info(f"Shop {shop_id} does not have BOOKING_REMINDERS_WHATSAPP feature, skipping {len(shop_bookings)} bookings")
                continue
            
            # Validate shop phone
            shop_phone, shop_phone_error = validate_and_format_phone(shop.phone)
            if shop_phone_error:
                logger.error(f"Invalid shop phone for shop {shop_id}: {shop_phone_error}")
                continue
            
            # Send notifications for this shop's bookings
            for booking in shop_bookings:
                try:
                    # Resolve provider first - shop_wayon (the shop's own
                    # token) is exempt from BookieBuddy's WHATSAPP_MESSAGE
                    # limit/reservation entirely; bookiebuddy_wayon and
                    # wacto both keep the existing gated behavior unchanged.
                    provider = resolve_whatsapp_provider(shop)
                    gate_usage = provider["provider"] != "shop_wayon"

                    # Check WhatsApp usage limit BEFORE sending (no side-effects)
                    if gate_usage:
                        can_send, limit_error = check_whatsapp_limit(shop)

                        if not can_send:
                            logger.warning(
                                f"Skipping pickup notification for booking {booking.id}: {limit_error}"
                            )
                            total_failed += 1
                            continue

                    # Validate client phone
                    client_phone_raw = str(booking.client_identity.phone_1_e164) if booking.client_identity.phone_1_e164 else None
                    client_phone, client_phone_error = validate_and_format_phone(
                        client_phone_raw
                    )
                    if client_phone_error:
                        logger.error(f"Invalid client phone for booking {booking.id}: {client_phone_error}")
                        total_failed += 1
                        continue

                    # Get customer/shop names safe for WhatsApp template params
                    customer_name = sanitize_whatsapp_text(
                        booking.client_identity.client_name,
                        fallback="Customer"
                    )
                    shop_name = sanitize_whatsapp_text(shop.name, fallback="Shop")

                    if gate_usage:
                        reserved_ok, reserved_err = increment_whatsapp_usage(shop)
                        if not reserved_ok:
                            logger.warning(
                                f"Skipping pickup notification for booking {booking.id}: {reserved_err}"
                            )
                            total_failed += 1
                            continue

                    try:
                        # Send pickup ready template
                        if provider["provider"] == "shop_wayon":
                            response = provider["service"].send_reminder_template(
                                client_phone,
                                travelbot_first_name(customer_name),
                                "pickup_reminder_2",
                            )
                        elif provider["provider"] == "bookiebuddy_wayon":
                            formatted_shop_phone = whatsapp_service._format_phone_number(shop_phone)
                            shop_phone_display = whatsapp_service._with_plus(formatted_shop_phone)
                            response = provider["service"].send_bookiebuddy_reminder_template(
                                client_phone,
                                travelbot_first_name(customer_name),
                                shop_name,
                                shop_phone_display,
                                formatted_shop_phone,
                                "pickup_reminder_2",
                            )
                        else:
                            response = whatsapp_service.send_pickup_ready_template(
                                phone_number=client_phone,
                                customer_name=customer_name,
                                shop_name=shop_name,
                                shop_phone=shop_phone
                            )
                    except Exception as e:
                        if gate_usage:
                            release_ok, release_err = release_whatsapp_usage(shop)
                            if not release_ok:
                                logger.error(f"Failed to release reserved WhatsApp usage for booking {booking.id}: {release_err}")
                        total_failed += 1
                        logger.error(f"Error sending pickup ready for booking {booking.id}: {str(e)}", exc_info=True)
                        continue

                    if response.get("success"):
                        if not gate_usage:
                            # shop_wayon: never reserved/gated - record the
                            # successful send now, unconditionally, purely
                            # for an accurate total-sent count.
                            increment_whatsapp_usage_unconditional(shop)
                        total_sent += 1
                        logger.info(f"Pickup ready notification sent for booking {booking.id}")
                    else:
                        if gate_usage:
                            release_ok, release_err = release_whatsapp_usage(shop)
                            if not release_ok:
                                logger.error(f"Failed to release reserved WhatsApp usage for booking {booking.id}: {release_err}")
                        total_failed += 1
                        error_msg = response.get('error', 'Unknown error')
                        status_code = response.get('status_code', None)
                        logger.error(
                            f"Failed to send pickup ready WhatsApp notification: "
                            f"BookingID={booking.id}, ShopID={shop.id}, ClientPhone={client_phone}, ShopPhone={shop_phone}, CustomerName={customer_name}, "
                            f"Payload={{'phone_number': {client_phone}, 'customer_name': {customer_name}, 'shop_name': {shop_name}, 'shop_phone': {shop_phone}}}, "
                            f"Error={error_msg}, StatusCode={status_code}, Response={response}"
                        )

                except Exception as e:
                    total_failed += 1
                    logger.error(f"Error sending pickup ready for booking {booking.id}: {str(e)}", exc_info=True)
        
        logger.info(f"Pickup ready notifications complete. Sent: {total_sent}, Failed: {total_failed}")
        return {"sent": total_sent, "failed": total_failed}
        
    except Exception as e:
        logger.error(f"Error in send_pickup_ready_notifications: {str(e)}", exc_info=True)
        raise


@shared_task(bind=True)
def send_return_reminder_notifications(self):
    """
    Send 'Return Reminder' WhatsApp notifications for today's return bookings.
    Only sends to shops with active subscriptions and WHATSAPP_MESSAGE feature.
    Validates return time hasn't expired. For same-day bookings (pickup = return),
    only sends return notification if pickup time has already passed.
    Includes all relevant statuses: booked, ready to deliver, delivered.
    """
    from booking_v2.models import BookingV2
    from subscriptions.models import has_feature_access
    from shop.models import ShopUser
    from django.conf import settings
    
    try:
        # Check if WhatsApp reminders are enabled
        if not settings.WHATSAPP_REMINDERS_ENABLED:
            logger.info("WhatsApp return reminder notifications are disabled (WHATSAPP_REMINDERS_ENABLED=false)")
            return {"sent": 0, "failed": 0, "disabled": True}
        
        now = timezone.now()
        today = now.date()
        logger.info(f"Starting return reminder notifications for {today} at {now.time()}")
        
        # Fetch bookings with optimized queries
        # Include 'booked', 'ready to deliver', 'delivered' because some shops forget to update status
        bookings = (
            BookingV2.objects
            .filter(
                return_date__date=today,
                delivery_status__in=['booked', 'ready to deliver', 'delivered'],
                return_date__gt=now  # Return time must be in the future
            )
            .select_related(
                'shop',
                'shop__subscription',
                'client_identity'
            )
            .only(
                'id',
                'pickup_date',
                'return_date',
                'shop__id',
                'shop__name',
                'shop__phone',
                'client_identity__client_name',
                'client_identity__phone_1_e164'
            )
        )
        
        # Group bookings by shop for batch processing
        from collections import defaultdict
        bookings_by_shop = defaultdict(list)
        
        for booking in bookings:
            # Skip if no client identity or phone
            if not booking.client_identity or not booking.client_identity.phone_1_e164:
                logger.warning(f"Booking {booking.id} has no client phone, skipping")
                continue
            
            # For same-day bookings (pickup and return on same day)
            # Only send return notification if pickup time has already passed
            if booking.pickup_date.date() == today:
                if booking.pickup_date > now:
                    logger.info(f"Booking {booking.id} is same-day with future pickup time, skipping return notification")
                    continue
                
            # Check shop has subscription
            if not hasattr(booking.shop, 'subscription') or not booking.shop.subscription:
                continue
                
            # Check subscription is active
            if not booking.shop.subscription.is_active():
                continue
                
            bookings_by_shop[booking.shop.id].append(booking)
        
        # Process bookings by shop
        total_sent = 0
        total_failed = 0
        
        whatsapp_service = WhatsAppService()
        
        for shop_id, shop_bookings in bookings_by_shop.items():
            # Get a shop user to check feature access (use first booking's shop)
            shop = shop_bookings[0].shop
            
            # Check if any user in shop has WHATSAPP_MESSAGE feature
            shop_users = ShopUser.objects.filter(shop_id=shop_id, is_active=True)
            has_feature = False
            
            for shop_user in shop_users:
                if has_feature_access(shop_user, "BOOKING_REMINDERS_WHATSAPP"):
                    has_feature = True
                    break
            
            if not has_feature:
                logger.info(f"Shop {shop_id} does not have BOOKING_REMINDERS_WHATSAPP feature, skipping {len(shop_bookings)} bookings")
                continue
            
            # Validate shop phone
            shop_phone, shop_phone_error = validate_and_format_phone(shop.phone)
            if shop_phone_error:
                logger.error(f"Invalid shop phone for shop {shop_id}: {shop_phone_error}")
                continue
            
            # Send notifications for this shop's bookings
            for booking in shop_bookings:
                try:
                    # Resolve provider first - shop_wayon (the shop's own
                    # token) is exempt from BookieBuddy's WHATSAPP_MESSAGE
                    # limit/reservation entirely; bookiebuddy_wayon and
                    # wacto both keep the existing gated behavior unchanged.
                    provider = resolve_whatsapp_provider(shop)
                    gate_usage = provider["provider"] != "shop_wayon"

                    # Check WhatsApp usage limit BEFORE sending
                    if gate_usage:
                        can_send, limit_error = check_whatsapp_limit(shop)

                        if not can_send:
                            logger.warning(
                                f"Skipping return reminder for booking {booking.id}: {limit_error}"
                            )
                            total_failed += 1
                            continue

                    # Validate client phone
                    client_phone_raw = str(booking.client_identity.phone_1_e164) if booking.client_identity.phone_1_e164 else None
                    client_phone, client_phone_error = validate_and_format_phone(
                        client_phone_raw
                    )
                    if client_phone_error:
                        logger.error(f"Invalid client phone for booking {booking.id}: {client_phone_error}")
                        total_failed += 1
                        continue

                    # Get customer/shop names safe for WhatsApp template params
                    customer_name = sanitize_whatsapp_text(
                        booking.client_identity.client_name,
                        fallback="Customer"
                    )
                    shop_name = sanitize_whatsapp_text(shop.name, fallback="Shop")

                    if gate_usage:
                        reserved_ok, reserved_err = increment_whatsapp_usage(shop)
                        if not reserved_ok:
                            logger.warning(
                                f"Skipping return reminder for booking {booking.id}: {reserved_err}"
                            )
                            total_failed += 1
                            continue

                    try:
                        # Send return reminder template
                        if provider["provider"] == "shop_wayon":
                            response = provider["service"].send_reminder_template(
                                client_phone,
                                travelbot_first_name(customer_name),
                                "return_reminder_1",
                            )
                        elif provider["provider"] == "bookiebuddy_wayon":
                            formatted_shop_phone = whatsapp_service._format_phone_number(shop_phone)
                            shop_phone_display = whatsapp_service._with_plus(formatted_shop_phone)
                            response = provider["service"].send_bookiebuddy_reminder_template(
                                client_phone,
                                travelbot_first_name(customer_name),
                                shop_name,
                                shop_phone_display,
                                formatted_shop_phone,
                                "return_reminder_1",
                            )
                        else:
                            response = whatsapp_service.send_return_reminder_template(
                                phone_number=client_phone,
                                customer_name=customer_name,
                                shop_name=shop_name,
                                shop_phone=shop_phone
                            )
                    except Exception as e:
                        if gate_usage:
                            release_ok, release_err = release_whatsapp_usage(shop)
                            if not release_ok:
                                logger.error(f"Failed to release reserved WhatsApp usage for booking {booking.id}: {release_err}")
                        total_failed += 1
                        logger.error(f"Error sending return reminder for booking {booking.id}: {str(e)}", exc_info=True)
                        continue

                    if response.get("success"):
                        if not gate_usage:
                            # shop_wayon: never reserved/gated - record the
                            # successful send now, unconditionally, purely
                            # for an accurate total-sent count.
                            increment_whatsapp_usage_unconditional(shop)
                        total_sent += 1
                        logger.info(f"Return reminder sent for booking {booking.id}")
                    else:
                        if gate_usage:
                            release_ok, release_err = release_whatsapp_usage(shop)
                            if not release_ok:
                                logger.error(f"Failed to release reserved WhatsApp usage for booking {booking.id}: {release_err}")
                        total_failed += 1
                        error_msg = response.get('error', 'Unknown error')
                        status_code = response.get('status_code', None)
                        logger.error(
                            f"Failed to send return reminder WhatsApp notification: "
                            f"BookingID={booking.id}, ShopID={shop.id}, ClientPhone={client_phone}, ShopPhone={shop_phone}, CustomerName={customer_name}, "
                            f"Payload={{'phone_number': {client_phone}, 'customer_name': {customer_name}, 'shop_name': {shop_name}, 'shop_phone': {shop_phone}}}, "
                            f"Error={error_msg}, StatusCode={status_code}, Response={response}"
                        )

                except Exception as e:
                    total_failed += 1
                    logger.error(f"Error sending return reminder for booking {booking.id}: {str(e)}", exc_info=True)
        
        logger.info(f"Return reminder notifications complete. Sent: {total_sent}, Failed: {total_failed}")
        return {"sent": total_sent, "failed": total_failed}
        
    except Exception as e:
        logger.error(f"Error in send_return_reminder_notifications: {str(e)}", exc_info=True)
        raise


# ---------------------------------------------------------------------------
# WhatsApp invoice PDF cleanup (Celery Beat, daily)
# ---------------------------------------------------------------------------
# WhatsAppService.upload_pdf_to_s3 (services.py) uploads every WhatsApp invoice
# PDF under "whatsapp-invoices/<YYYY>/<MM>/<DD>/". They are only needed until
# Wacto has fetched them, so a daily task PERMANENTLY deletes everything PDF
# under that prefix. On a versioned bucket a plain DeleteObject only adds a
# delete marker (data stays stored), so every version and delete marker is
# deleted explicitly by VersionId.
# The root folder itself is preserved: the "whatsapp-invoices/" marker object is
# never deleted, and is created once (zero-byte) if missing, so the folder stays
# visible even when empty. Only PDFs and date subfolders inside it are removed.

# Must match the key prefix used by WhatsAppService.upload_pdf_to_s3 (services.py:574).
WHATSAPP_INVOICE_S3_PREFIX = "whatsapp-invoices/"
S3_DELETE_BATCH = 1000          # S3 DeleteObjects hard limit per request
S3_LIST_PAGE_SIZE = 1000
CLEANUP_LOG_SAMPLE = 50         # max per-object errors logged individually


def _is_whatsapp_invoice_key(key):
    """PDFs and date-subfolder markers INSIDE the invoice prefix - never the
    root folder marker ("whatsapp-invoices/") itself, which is preserved."""
    if not key or not key.startswith(WHATSAPP_INVOICE_S3_PREFIX) or key == WHATSAPP_INVOICE_S3_PREFIX:
        return False
    return key.lower().endswith(".pdf") or key.endswith("/")


def _root_folder_marker_exists(client, bucket):
    """True / False, or None if it could not be checked."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        client.head_object(Bucket=bucket, Key=WHATSAPP_INVOICE_S3_PREFIX)
        return True
    except ClientError as e:
        if _s3_error_code(e) in ("404", "NoSuchKey", "NotFound"):
            return False
        logger.error(f"[WhatsApp PDF Cleanup] Could not check root folder marker: {_s3_error_code(e)} - {e}")
        return None
    except BotoCoreError as e:
        logger.error(f"[WhatsApp PDF Cleanup] Could not check root folder marker: {e}")
        return None


def _ensure_root_folder_marker(client, bucket, dry_run=False):
    """Keeps the root "whatsapp-invoices/" folder visible. An existing marker is
    left untouched; a missing one is created once as a zero-byte object.
    Returns "kept" | "created" | "would create" | "error"."""
    from botocore.exceptions import BotoCoreError, ClientError

    exists = _root_folder_marker_exists(client, bucket)
    if exists:
        return "kept"
    if exists is None:
        return "error"
    if dry_run:
        return "would create"
    try:
        client.put_object(Bucket=bucket, Key=WHATSAPP_INVOICE_S3_PREFIX, Body=b"")
    except (ClientError, BotoCoreError) as e:
        logger.error(f"[WhatsApp PDF Cleanup] Could not create root folder marker: {_s3_error_code(e)} - {e}")
        return "error"
    logger.info(f"[WhatsApp PDF Cleanup] Root folder marker '{WHATSAPP_INVOICE_S3_PREFIX}' was missing - created")
    return "created"


def _s3_error_code(exc):
    return getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)


def _iter_invoice_versions(client, bucket):
    """Yields (key, version_id, is_delete_marker) for every stored version and
    delete marker under the prefix. On an unversioned bucket each object is
    returned once with VersionId "null" - deleting by it is still permanent."""
    paginator = client.get_paginator("list_object_versions")
    for page in paginator.paginate(
        Bucket=bucket,
        Prefix=WHATSAPP_INVOICE_S3_PREFIX,
        PaginationConfig={"PageSize": S3_LIST_PAGE_SIZE},
    ):
        for version in page.get("Versions", []):
            yield version["Key"], version.get("VersionId"), False
        for marker in page.get("DeleteMarkers", []):
            yield marker["Key"], marker.get("VersionId"), True


def _iter_invoice_current_objects(client, bucket):
    """Fallback for a confirmed-unversioned bucket without ListBucketVersions."""
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(
        Bucket=bucket,
        Prefix=WHATSAPP_INVOICE_S3_PREFIX,
        PaginationConfig={"PageSize": S3_LIST_PAGE_SIZE},
    ):
        for obj in page.get("Contents", []):
            yield obj["Key"], None, False


def _get_bucket_versioning(client, bucket):
    """Returns (status, mfa_delete). status: Enabled | Suspended | Unversioned | Unknown."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        resp = client.get_bucket_versioning(Bucket=bucket)
    except (ClientError, BotoCoreError) as e:
        logger.warning(
            f"[WhatsApp PDF Cleanup] Could not read bucket versioning ({_s3_error_code(e)}); "
            f"will rely on version listing"
        )
        return "Unknown", "Unknown"
    return resp.get("Status") or "Unversioned", resp.get("MFADelete") or "Disabled"


def _delete_invoice_batch(client, bucket, batch, stats):
    """Permanently deletes one batch (<= 1000) of (key, version_id, is_marker)."""
    from botocore.exceptions import BotoCoreError, ClientError

    requested = {(key, version_id): is_marker for key, version_id, is_marker in batch}
    objects = [
        {"Key": key, "VersionId": version_id} if version_id else {"Key": key}
        for key, version_id, _ in batch
    ]
    try:
        resp = client.delete_objects(
            Bucket=bucket, Delete={"Objects": objects, "Quiet": False}
        )
    except (ClientError, BotoCoreError) as e:
        stats["failed"] += len(batch)
        logger.error(
            f"[WhatsApp PDF Cleanup] Delete batch of {len(batch)} failed: {_s3_error_code(e)} - {e}"
        )
        return

    stats["batches"] += 1
    for deleted in resp.get("Deleted", []):
        key, version_id = deleted.get("Key"), deleted.get("VersionId")
        if version_id is None and deleted.get("DeleteMarker"):
            # Deleted without a VersionId and S3 created a delete marker: the
            # object is only hidden, its data is still stored (soft delete).
            stats["soft_deleted"] += 1
            logger.error(
                f"[WhatsApp PDF Cleanup] Delete marker created instead of permanent delete for "
                f"'{key}' - bucket is versioned; data NOT removed"
            )
            continue
        if requested.get((key, version_id), False):
            stats["delete_markers_deleted"] += 1
        else:
            stats["versions_deleted"] += 1

    for error in resp.get("Errors", []):
        stats["failed"] += 1
        if stats["failed"] <= CLEANUP_LOG_SAMPLE:
            logger.error(
                f"[WhatsApp PDF Cleanup] Could not delete '{error.get('Key')}' "
                f"(version {error.get('VersionId')}): {error.get('Code')} - {error.get('Message')}"
            )


def verify_whatsapp_invoice_pdfs_removed(client=None, bucket=None, sample_size=10):
    """Counts invoice PDF versions / delete markers still stored under the prefix."""
    from django.conf import settings
    from services.s3_service import S3Service

    client = client or S3Service.get_client()
    bucket = bucket or settings.AWS_STORAGE_BUCKET_NAME
    remaining, samples = 0, []
    for key, version_id, is_marker in _iter_invoice_versions(client, bucket):
        if not _is_whatsapp_invoice_key(key):
            continue
        remaining += 1
        if len(samples) < sample_size:
            samples.append(f"{key} (version {version_id}{', delete marker' if is_marker else ''})")
    return remaining, samples


def run_whatsapp_invoice_pdf_cleanup(dry_run=False, time_budget_seconds=780, client=None, bucket=None):
    """Permanently deletes every WhatsApp invoice PDF and date subfolder (all
    versions and delete markers) under WHATSAPP_INVOICE_S3_PREFIX, keeping the
    root folder marker. Shared by the Beat task and the management command.
    Returns a summary dict."""
    import time
    from itertools import chain
    from botocore.exceptions import BotoCoreError, ClientError
    from celery.exceptions import SoftTimeLimitExceeded
    from django.conf import settings
    from services.s3_service import S3Service

    started = time.monotonic()
    stats = {
        "dry_run": dry_run,
        "versioning": None,
        "versions_deleted": 0,
        "delete_markers_deleted": 0,
        "would_delete_versions": 0,
        "would_delete_delete_markers": 0,
        "failed": 0,
        "soft_deleted": 0,
        "skipped_non_pdf": 0,
        "root_marker_entries_preserved": 0,
        "batches": 0,
        "root_folder": None,
        "root_folder_present": None,
        "remaining_after_verify": None,
        "stopped_early": False,
        "aborted": None,
        "samples": [],
    }

    def finish():
        stats["duration_s"] = round(time.monotonic() - started, 2)
        has_error = (
            stats["aborted"] or stats["failed"] or stats["soft_deleted"] or stats["root_folder"] == "error"
        )
        level = logging.ERROR if has_error else logging.INFO
        logger.log(level, f"[WhatsApp PDF Cleanup] Summary: {stats}")
        return stats

    def abort(reason):
        stats["aborted"] = reason
        logger.error(f"[WhatsApp PDF Cleanup] Aborted, nothing deleted: {reason}")
        return finish()

    prefix = WHATSAPP_INVOICE_S3_PREFIX
    if not prefix or prefix.startswith("/") or not prefix.endswith("/"):
        return abort(f"unsafe prefix {prefix!r}")

    client = client or S3Service.get_client()
    bucket = bucket or settings.AWS_STORAGE_BUCKET_NAME
    if not bucket:
        return abort("AWS_STORAGE_BUCKET_NAME is not configured")

    versioning, mfa_delete = _get_bucket_versioning(client, bucket)
    stats["versioning"] = versioning
    logger.info(
        f"[WhatsApp PDF Cleanup] Start: bucket={bucket} prefix={prefix} versioning={versioning} "
        f"mfa_delete={mfa_delete} dry_run={dry_run}"
    )
    if mfa_delete == "Enabled":
        return abort("bucket has MFA Delete enabled - versions cannot be deleted via the API")

    # Prefer version listing: deleting by VersionId is permanent whatever the
    # versioning state. Only a confirmed-unversioned bucket may fall back to
    # plain listing (a plain delete is permanent only there).
    entries = _iter_invoice_versions(client, bucket)
    use_versions = True
    try:
        first = next(entries, None)
    except (ClientError, BotoCoreError) as e:
        code = _s3_error_code(e)
        if versioning != "Unversioned":
            return abort(
                f"cannot list object versions ({code}) on a bucket with versioning={versioning}; "
                f"a delete without VersionId would only add delete markers (soft delete)"
            )
        logger.warning(f"[WhatsApp PDF Cleanup] list_object_versions denied ({code}); bucket is unversioned, "
                       f"using list_objects_v2 (plain delete is permanent here)")
        use_versions = False
        entries = _iter_invoice_current_objects(client, bucket)
        first = None
    else:
        if first is not None:
            entries = chain([first], entries)

    batch = []
    try:
        for key, version_id, is_marker in entries:
            if time.monotonic() - started > time_budget_seconds:
                stats["stopped_early"] = True
                logger.warning("[WhatsApp PDF Cleanup] Time budget reached; remaining PDFs are deleted next run")
                break
            if key == WHATSAPP_INVOICE_S3_PREFIX:
                # Root folder marker (any version / delete marker) - always kept.
                stats["root_marker_entries_preserved"] += 1
                continue
            if not _is_whatsapp_invoice_key(key):
                stats["skipped_non_pdf"] += 1
                continue
            if dry_run:
                stats["would_delete_delete_markers" if is_marker else "would_delete_versions"] += 1
                if len(stats["samples"]) < 10:
                    stats["samples"].append(key)
                continue
            batch.append((key, version_id, is_marker))
            if len(batch) >= S3_DELETE_BATCH:
                _delete_invoice_batch(client, bucket, batch, stats)
                logger.debug(f"[WhatsApp PDF Cleanup] Batch {stats['batches']} processed")
                batch = []
        if batch:
            _delete_invoice_batch(client, bucket, batch, stats)
    except SoftTimeLimitExceeded:
        stats["stopped_early"] = True
        logger.warning("[WhatsApp PDF Cleanup] Soft time limit reached; remaining PDFs are deleted next run")
        return finish()
    except (ClientError, BotoCoreError) as e:
        return abort(f"listing failed mid-run: {_s3_error_code(e)} - {e}")

    # Keep the root folder: never deleted above; created once if it is missing.
    stats["root_folder"] = _ensure_root_folder_marker(client, bucket, dry_run=dry_run)
    stats["root_folder_present"] = stats["root_folder"] in ("kept", "created")

    if dry_run or stats["stopped_early"]:
        return finish()

    # Verify: nothing (no version, no delete marker) may remain under the prefix.
    try:
        if use_versions:
            remaining, samples = verify_whatsapp_invoice_pdfs_removed(client, bucket)
        else:
            samples = [k for k, _, _ in _iter_invoice_current_objects(client, bucket) if _is_whatsapp_invoice_key(k)]
            remaining, samples = len(samples), samples[:10]
    except (ClientError, BotoCoreError) as e:
        logger.error(f"[WhatsApp PDF Cleanup] Verification listing failed: {_s3_error_code(e)} - {e}")
        return finish()

    stats["remaining_after_verify"] = remaining
    if remaining:
        stats["samples"] = samples
        logger.error(
            f"[WhatsApp PDF Cleanup] {remaining} PDF version(s)/marker(s) still stored after cleanup, e.g. {samples}"
        )
    else:
        logger.info("[WhatsApp PDF Cleanup] Permanent deletion verified: no PDF versions or delete markers remain")
    return finish()


@shared_task(bind=True, soft_time_limit=840, time_limit=900)
def cleanup_whatsapp_invoice_pdfs(self, dry_run=False):
    """Daily (Celery Beat) permanent deletion of all WhatsApp invoice PDFs in S3."""
    return run_whatsapp_invoice_pdf_cleanup(dry_run=dry_run)
