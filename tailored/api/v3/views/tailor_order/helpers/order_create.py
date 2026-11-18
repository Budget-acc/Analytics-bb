from tailored.models import (
    TailorOrder, TailorOrderItem, TailorMaterialUsage,
    TailorOrderAdditionalCharge, CustomerPayment, Tailor, TailorOrderItemAssignment,
)
from services.models import ProductVariant
from tailored.api.v3.utils.date_utils import parse_datetime, InvalidDateFormat
from decimal import Decimal, InvalidOperation
import logging

logger = logging.getLogger("api_requests")


def _create_tailor_order(self, request, data, client, staff, discount_amount, security_amount, effective_dt=None):
    """Create main TailorOrder record"""
    return TailorOrder.objects.create(
        shop_id=self.shop_id,
        created_by=request.user,
        client=client,
        client_phone_number=(data.get("client_phone_number") or "").strip(),
        staff=staff,
        customer_order_type=data.get("customer_order_type", "shop"),
        pickup_date=parse_datetime(data.get("pickup_date")),
        return_date=parse_datetime(data.get("return_date")),
        client_address=data.get("client_address"),
        notes=data.get("notes"),
        discount_amount=discount_amount,
        security_amount=security_amount,
        **({"created_at": effective_dt} if effective_dt else {}),
    )


def _create_order_items(
    self, order, items_data, shop_name, tax_config=None, product_taxable=False,
    items_raw_total=Decimal("0.00"),
):
    """
    Create all order items and their associated materials.
    Returns total tailor charge, total customer item charge, total material
    charge, and total_items_tax (always 0). Materials are created WITHOUT
    fabric deduction (handled separately).

    customer_item_charge is always stored raw, as-received - no discount/tax
    adjustment happens here for any tax mode. discount/tax distribution (for
    Inclusive tax) happens in create.py's aggregate block, after all items
    exist, via a bucket-level gross-up + proportional redistribution.
    """
    total_tailor_charge = Decimal('0')
    total_customer_item_charge = Decimal('0')
    total_material_charge = Decimal('0')
    total_items_tax = Decimal('0')

    # --- Collect all assigned_tailor IDs (single field + assignments array) ---
    assigned_tailor_ids = set()
    for item in items_data:
        tid = item.get("assigned_tailor")
        if tid:
            try:
                assigned_tailor_ids.add(int(tid))
            except (ValueError, TypeError):
                continue
        # NEW: also collect from assignments array
        for asgn in (item.get("assignments") or []):
            tid = asgn.get("tailor_id")
            if tid:
                try:
                    assigned_tailor_ids.add(int(tid))
                except (ValueError, TypeError):
                    continue

    # --- Prefetch tailors belonging to this shop and not soft-deleted ---
    tailors_dict = {}
    if assigned_tailor_ids:
        try:
            tailors_qs = Tailor.objects.filter(
                id__in=assigned_tailor_ids,
                shop_id=self.shop_id,
                is_deleted=False
            )
            tailors_dict = {t.id: t for t in tailors_qs}
        except Exception as e:
            logger.error(f"Error fetching tailors: {str(e)}", exc_info=True)
            raise ValueError("Failed to retrieve tailor information. Please try again.")

    # --- Collect all material variant IDs for batch fetch ---
    material_variant_ids = set()
    for item in items_data:
        materials = item.get("materials") or []
        for material in materials:
            variant_id = material.get("variant_id")
            if variant_id:
                try:
                    material_variant_ids.add(int(variant_id))
                except (ValueError, TypeError):
                    continue

    # --- Prefetch material variants ---
    materials_dict = {}
    if material_variant_ids:
        try:
            materials_qs = ProductVariant.objects.filter(
                id__in=material_variant_ids,
                product__shop_service__shop_id=self.shop_id,
                deleted__isnull=True
            ).select_related("product")
            materials_dict = {v.id: v for v in materials_qs}
        except Exception as e:
            logger.error(f"Error fetching material variants: {str(e)}", exc_info=True)
            raise ValueError("Failed to retrieve material information. Please try again.")

    # --- Iterate over items and create order items ---
    for idx, item in enumerate(items_data, start=1):
        try:
            product_name = (item.get("product_name") or "").strip()
            if not product_name:
                raise ValueError(f"Item {idx}: Product name is required.")

            product_type = (item.get("product_type") or "").strip()
            quantity = item.get("quantity", 1)
            assigned_tailor_id = item.get("assigned_tailor")
            measurements = item.get("measurements")
            reference_image_file = item.get("reference_image")

            # Get customer_item_charge from item data
            customer_item_charge = item.get("customer_item_charge", 0)

            # Validate and convert charges using Decimal
            try:
                tailor_charge = Decimal(str(item.get("tailor_charge") or 0)).quantize(Decimal('0.01'))
                customer_tailoring_charge = Decimal(str(item.get("customer_tailoring_charge") or 0)).quantize(Decimal('0.01'))
                customer_item_charge = Decimal(str(customer_item_charge or 0)).quantize(Decimal('0.01'))

                if tailor_charge < 0 or customer_tailoring_charge < 0 or customer_item_charge < 0:
                    raise ValueError()
            except (ValueError, TypeError, InvalidOperation):
                raise ValueError(f"Item {idx}: Charges must be valid positive numbers.")

            # customer_item_charge is NEVER mutated here for either tax mode
            # - it stays exactly the raw value received. Discount and tax
            # (Inclusive) are computed purely at the aggregate/bucket level
            # in create.py, AFTER all items exist - see create.py's
            # aggregate tax block.
            expected_completion_date_str = item.get("expected_completion_date")
            expected_completion_date = None
            if expected_completion_date_str:
                try:
                    expected_completion_date = parse_datetime(expected_completion_date_str)
                except InvalidDateFormat as e:
                    raise ValueError(f"Item {idx}: Invalid date format for expected date.")

            notes = item.get("notes", "")
            size = item.get("size") or None

            # Convert measurements dict to list of dicts if needed
            if measurements:
                if isinstance(measurements, dict):
                    measurements = [{"name": k, "value": v} for k, v in measurements.items()]
                elif not isinstance(measurements, list):
                    raise ValueError(f"Item {idx}: Measurements must be an object or array.")

            # Validate assigned tailor
            assigned_tailor = None
            if assigned_tailor_id:
                try:
                    assigned_tailor_id = int(assigned_tailor_id)
                    assigned_tailor = tailors_dict.get(assigned_tailor_id)
                    if not assigned_tailor:
                        raise ValueError(
                            f"Item {idx}: Assigned tailor not found or inactive in this shop."
                        )
                except (ValueError, TypeError):
                    raise ValueError(f"Item {idx}: Invalid tailor ID.")

            # Handle reference image upload
            reference_image_url = ""
            if reference_image_file:
                try:
                    temp_item_id = f"{order.id}_item_{idx}"
                    reference_image_url = self._upload_image_to_s3(
                        reference_image_file, shop_name, temp_item_id
                    )
                except ValueError as ve:
                    raise ValueError(f"Item {idx}: {str(ve)}")

            # Create TailorOrderItem
            try:
                order_item = TailorOrderItem.objects.create(
                    order=order,
                    product_name=product_name,
                    product_type=product_type,
                    quantity=quantity,
                    reference_image=reference_image_url,
                    assigned_tailor=assigned_tailor,
                    tailor_charge=tailor_charge,
                    customer_tailoring_charge=customer_tailoring_charge,
                    customer_item_charge=customer_item_charge,
                    measurements=measurements,
                    size=size,
                    expected_completion_date=expected_completion_date,
                    notes=notes
                )
            except Exception as e:
                logger.error(f"Failed to create order item {idx}: {str(e)}", exc_info=True)
                raise ValueError(f"Item {idx}: Failed to save. Please try again.")

            # --- Create TailorOrderItemAssignment rows (dual-path) ---
            assignments_data = item.get("assignments") or []
            if assignments_data:
                # NEW PATH: multiple tailors via assignments array
                seen_tailor_ids = set()
                for asgn in assignments_data:
                    try:
                        t_id = int(asgn.get("tailor_id"))
                    except (ValueError, TypeError):
                        raise ValueError(f"Item {idx}: Invalid tailor ID in assignments.")
                    if t_id in seen_tailor_ids:
                        raise ValueError(f"Item {idx}: Duplicate tailor ID {t_id} in assignments.")
                    seen_tailor_ids.add(t_id)
                    tailor_obj = tailors_dict.get(t_id)
                    if not tailor_obj:
                        raise ValueError(f"Item {idx}: Tailor {t_id} not found or inactive in this shop.")
                    try:
                        asgn_charge = Decimal(str(asgn.get("tailor_charge") or 0)).quantize(Decimal('0.01'))
                        if asgn_charge < 0:
                            raise ValueError()
                    except (ValueError, TypeError, InvalidOperation):
                        logger.warning(f"Item {idx}: invalid tailor_charge value in assignments: {asgn.get('tailor_charge')!r}")
                        raise ValueError(f"Item {idx}: Invalid charge amount for one of the assigned tailors.")
                    try:
                        asgn_customer_charge = Decimal(str(asgn.get("tailoring_charge_from_customer") or 0)).quantize(Decimal('0.01'))
                        if asgn_customer_charge < 0:
                            raise ValueError()
                    except (ValueError, TypeError, InvalidOperation):
                        logger.warning(f"Item {idx}: invalid tailoring_charge_from_customer value in assignments: {asgn.get('tailoring_charge_from_customer')!r}")
                        raise ValueError(f"Item {idx}: Invalid customer charge amount for one of the assigned tailors.")
                    asgn_date_str = asgn.get("expected_completion_date")
                    asgn_date = None
                    if asgn_date_str:
                        try:
                            asgn_date = parse_datetime(asgn_date_str)
                        except (InvalidDateFormat, Exception):
                            logger.warning(f"Item {idx}: invalid expected_completion_date value in assignments: {asgn_date_str!r}")
                            raise ValueError(f"Item {idx}: Invalid expected completion date for one of the assigned tailors.")
                    TailorOrderItemAssignment.objects.create(
                        order_item=order_item,
                        tailor=tailor_obj,
                        tailor_charge=asgn_charge,
                        tailoring_charge_from_customer=asgn_customer_charge,
                        expected_completion_date=asgn_date,
                    )
                    total_tailor_charge += asgn_charge

                # Mirror first assignment into legacy fields for backward compat (single save after loop)
                first_asgn = order_item.assignments.select_related("tailor").order_by("id").first()
                if first_asgn:
                    order_item.assigned_tailor = first_asgn.tailor
                    order_item.tailor_charge = first_asgn.tailor_charge
                    order_item.expected_completion_date = first_asgn.expected_completion_date
                    order_item.save(update_fields=["assigned_tailor", "tailor_charge", "expected_completion_date"])
            else:
                # LEGACY PATH: single tailor via assigned_tailor field
                total_tailor_charge += tailor_charge
                if assigned_tailor:
                    TailorOrderItemAssignment.objects.create(
                        order_item=order_item,
                        tailor=assigned_tailor,
                        tailor_charge=tailor_charge,
                        tailoring_charge_from_customer=customer_tailoring_charge,
                        expected_completion_date=expected_completion_date,
                    )

            total_customer_item_charge += customer_item_charge

            # --- Create associated materials (WITHOUT fabric deduction) ---
            materials = item.get("materials") or []
            if materials and isinstance(materials, list):
                for mat_idx, m in enumerate(materials, start=1):
                    try:
                        variant_id = m.get("variant_id")
                        if not variant_id:
                            continue

                        try:
                            variant_id = int(variant_id)
                        except (ValueError, TypeError):
                            raise ValueError(f"Item {idx}, Material {mat_idx}: Invalid material ID.")

                        # Get pre-fetched variant
                        variant = materials_dict.get(variant_id)
                        if not variant:
                            raise ValueError(
                                f"Item {idx}, Material {mat_idx}: Material not found in your shop."
                            )

                        # Validate quantity and amount
                        try:
                            qty = Decimal(str(m.get("quantity_meters") or 0))
                            total_amount = Decimal(str(m.get("total_amount") or 0))
                            if qty < 0 or total_amount < 0:
                                raise ValueError()
                        except (ValueError, TypeError, InvalidOperation):
                            raise ValueError(
                                f"Item {idx}, Material {mat_idx}: Quantity and amount must be valid numbers."
                            )

                        total_material_charge += total_amount

                        # Create material usage WITHOUT triggering save() deduction
                        try:
                            TailorMaterialUsage.objects.create(
                                order_item=order_item,
                                material=variant,
                                quantity_meters=qty,
                                total_amount=total_amount
                            )
                        except Exception as e:
                            logger.error(f"Failed to create material usage: {str(e)}", exc_info=True)
                            raise ValueError(f"Item {idx}, Material {mat_idx}: Failed to save material.")

                    except ValueError:
                        raise
                    except Exception as e:
                        logger.error(f"Unexpected error creating material {mat_idx}: {str(e)}", exc_info=True)
                        raise ValueError(f"Item {idx}, Material {mat_idx}: An error occurred.")

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error creating order item {idx}: {str(e)}", exc_info=True)
            raise ValueError(f"Item {idx}: An error occurred. Please try again.")

    return total_tailor_charge, total_customer_item_charge, total_material_charge, total_items_tax


def _create_additional_charges(
    self, order, charges, tax_config=None, additional_taxable=False,
    charges_raw_total=Decimal("0.00"),
):
    """Add any additional charges.

    Returns (total_additional, total_charges_tax). `amount` is always stored
    raw, as-received - no discount/tax adjustment happens here for any tax
    mode. `total_charges_tax` is always 0; discount/tax distribution (for
    Inclusive tax) happens in create.py's aggregate block, after all charges
    exist, via a bucket-level gross-up + proportional redistribution.
    """
    total_additional = Decimal('0')
    total_charges_tax = Decimal('0')

    if not charges or not isinstance(charges, list):
        return Decimal('0'), Decimal('0')

    for charge in charges:
        name = (charge.get("name") or "").strip()
        amount = charge.get("amount", 0)

        if not name:
            continue

        # Convert amount to Decimal safely
        try:
            amount = Decimal(str(amount))
        except (ValueError, TypeError, InvalidOperation):
            continue

        if amount <= 0:
            continue

        # `amount` is NEVER mutated here for either tax mode - it stays
        # exactly the raw value received. Discount and tax (Inclusive) are
        # computed purely at the aggregate/bucket level in create.py, AFTER
        # all charges exist - see create.py's aggregate tax block.
        try:
            TailorOrderAdditionalCharge.objects.create(
                order=order,
                name=name,
                amount=amount,
            )
            total_additional += amount
        except Exception as e:
            logger.error(f"Failed to create additional charge: {str(e)}", exc_info=True)
            # Continue with other charges

    return total_additional, total_charges_tax


def _create_payment(self, order, paid_amount, payment_method, account=None, effective_dt=None):
    """Record a payment for this order"""
    if paid_amount and float(paid_amount) > 0:
        CustomerPayment.objects.create(
            order=order,
            payment_amount=paid_amount,
            payment_method=payment_method or "cash",
            account=account,
            **({"created_at": effective_dt} if effective_dt else {}),
        )


def _handle_send_invoice(self, order, data, user, base_message):
    """
    Check send_invoice flag and queue a WhatsApp invoice task if eligible.
    Returns the final success message reflecting the invoice outcome.
    Task is wrapped in transaction.on_commit to fire only after commit.
    """
    from django.core.cache import cache
    from django.db import transaction

    send_invoice = data.get("send_invoice", False)
    if send_invoice not in [True, "true", "True", 1, "1"]:
        return base_message

    try:
        from subscriptions.models import has_feature_access
        from shop.models import Shop, ShopUser

        shop_obj = Shop.objects.select_related('subscription').get(id=self.shop_id)

        if not hasattr(shop_obj, 'subscription') or not shop_obj.subscription:
            return f"{base_message} Invoice not sent - Shop does not have an active subscription."
        elif not shop_obj.subscription.is_active():
            return f"{base_message} Invoice not sent - Shop subscription is inactive or expired."

        shop_user = ShopUser.objects.filter(shop_id=self.shop_id, user=user).first()
        if not shop_user:
            return f"{base_message} Invoice not sent - User not associated with this shop."
        elif not has_feature_access(shop_user, "WHATSAPP_MESSAGE"):
            return f"{base_message} Invoice not sent - Feature not available."

        cache_key = f"wa_task_queue_tailor_{shop_obj.id}"
        try:
            current_count = cache.incr(cache_key)
        except ValueError:
            cache.add(cache_key, 1, timeout=60)
            current_count = 1

        if current_count > 10:
            cache.decr(cache_key)
            return f"{base_message} Too many pending invoices, please try again later."

        from whatsapp_integration.v3.tasks import send_tailor_order_invoice_task

        def _queue_invoice_task():
            """
            Runs after the DB transaction commits.
            Broker failures (Redis/RabbitMQ unavailable, connection timeout) are
            caught here so they never propagate to the customer-facing request.
            """
            try:
                send_tailor_order_invoice_task.delay(order.id)
            except Exception as queue_err:
                logger.error(
                    f"Failed to queue WhatsApp invoice task for tailor order {order.id}: {queue_err}",
                    exc_info=True,
                )

        transaction.on_commit(_queue_invoice_task)
        return f"{base_message} Invoice queued for sending."

    except Exception as e:
        logger.error(f"WhatsApp invoice validation/queue failed for tailor order {order.id}: {str(e)}", exc_info=True)
        return f"{base_message} WhatsApp invoice not sent"
