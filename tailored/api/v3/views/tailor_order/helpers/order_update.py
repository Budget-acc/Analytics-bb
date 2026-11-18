from tailored.models import (
    TailorOrderItem, TailorMaterialUsage, TailorOrderAdditionalCharge, Tailor, TailorOrderItemAssignment,
)
from services.models import ProductVariant
from tailored.api.v3.utils.date_utils import parse_datetime, InvalidDateFormat
from tailored.tasks import delete_order_images_from_s3
from decimal import Decimal, InvalidOperation
from django.db.models import Sum
import logging

logger = logging.getLogger("api_requests")


def _update_order_items(self, order, items_data, shop_name):
    """
    Smart update for order items.
    Returns: (total_tailor_charge, total_customer_item_charge, total_material_charge)
    """
    # If explicitly sending empty array, delete all items
    if items_data is not None and len(items_data) == 0:
        order.items.all().delete()
        return Decimal('0'), Decimal('0'), Decimal('0'), Decimal('0')

    incoming_item_ids = set()
    total_tailor_charge = Decimal('0')
    total_customer_item_charge = Decimal('0')
    total_material_charge = Decimal('0')
    total_items_tax = Decimal('0')

    # Tax + discount are no longer applied per-item here. `customer_item_charge`
    # is always the raw, as-received value for every item - the caller
    # (partial_update.py) is the single place that applies discount/tax
    # across ALL of an order's items (not just the ones in this request),
    # without mutating any individual item's stored charge.

    # Prefetch tailors and materials (single field + assignments array)
    assigned_tailor_ids = set()
    material_variant_ids = set()

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

        materials = item.get("materials") or []
        for material in materials:
            variant_id = material.get("variant_id")
            if variant_id:
                try:
                    material_variant_ids.add(int(variant_id))
                except (ValueError, TypeError):
                    continue

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
            raise ValueError("Failed to retrieve tailor information.")

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
            raise ValueError("Failed to retrieve material information.")

    # Process each item
    for idx, item_data in enumerate(items_data, start=1):
        item_id = item_data.get("id")
        assignments_payload_raw = item_data.get("assignments")

        # Assignment-only mode: item has an ID but no product_name.
        # Only assignment operations are performed; all product fields are left untouched.
        is_assignment_only = bool(item_id and not item_data.get("product_name"))

        # No item id, no product_name, but assignments present →
        # If all assignment entries are update/delete (each has an assignment id),
        # resolve the parent item directly from the assignment record — no item id needed.
        # If any entry is a create (no assignment id), the item id is required.
        resolved_from_assignment = False
        if not item_id and not item_data.get("product_name") and assignments_payload_raw is not None:
            if not isinstance(assignments_payload_raw, list):
                raise ValueError(f"Item {idx}: Tailor assignments must be provided as a list.")
            # Try to resolve item from any existing assignment id (update/delete entries)
            first_asgn_id = None
            for entry in assignments_payload_raw:
                try:
                    first_asgn_id = int(entry["id"])
                    break
                except (ValueError, TypeError, KeyError):
                    pass
            if first_asgn_id is None:
                # No existing assignment id at all — all entries are creates, item id is required
                logger.warning(f"Item {idx}: cannot resolve item - no item id and no existing assignment id provided in payload.")
                raise ValueError(
                    f"Item {idx}: Unable to determine which item this assignment belongs to — include the item reference or full item details."
                )
            first_asgn = TailorOrderItemAssignment.objects.select_related("order_item").filter(
                id=first_asgn_id,
                order_item__order=order,
            ).first()
            if not first_asgn:
                raise ValueError(f"Item {idx}: Assignment {first_asgn_id} not found in this order.")
            order_item = first_asgn.order_item
            item_id = order_item.id
            incoming_item_ids.add(item_id)
            is_assignment_only = True
            resolved_from_assignment = True

        try:
            if is_assignment_only and not resolved_from_assignment:
                # Fetch existing item without touching any product fields
                try:
                    item_id = int(item_id)
                    order_item = TailorOrderItem.objects.filter(id=item_id, order=order).first()
                    if not order_item:
                        raise ValueError(f"Item {idx}: Item with ID {item_id} not found in this order.")
                except (ValueError, TypeError):
                    raise ValueError(f"Item {idx}: Invalid item ID.")
                incoming_item_ids.add(item_id)
                # Use existing DB values — no recalculation for fields not in payload
                customer_item_charge = order_item.customer_item_charge or Decimal("0")
                tailor_charge = Decimal("0")
                assigned_tailor = None
                expected_completion_date = None
            elif is_assignment_only:
                # resolved_from_assignment: order_item already resolved above
                customer_item_charge = order_item.customer_item_charge or Decimal("0")
                tailor_charge = Decimal("0")
                assigned_tailor = None
                expected_completion_date = None

            else:
                # --- Full item validation + save (unchanged) ---
                product_name = (item_data.get("product_name") or "").strip()
                if not product_name:
                    raise ValueError(f"Item {idx}: Product name is required.")

                product_type = (item_data.get("product_type") or "").strip()
                quantity = item_data.get("quantity", 1)
                assigned_tailor_id = item_data.get("assigned_tailor")
                measurements = item_data.get("measurements")
                reference_image_file = item_data.get("reference_image")

                # Validate charges with Decimal
                try:
                    tailor_charge = Decimal(str(item_data.get("tailor_charge") or 0)).quantize(Decimal('0.01'))
                    customer_tailoring_charge = Decimal(str(item_data.get("customer_tailoring_charge") or 0)).quantize(Decimal('0.01'))
                    customer_item_charge = Decimal(str(item_data.get("customer_item_charge") or 0)).quantize(Decimal('0.01'))

                    if tailor_charge < 0 or customer_tailoring_charge < 0 or customer_item_charge < 0:
                        raise ValueError()
                except (ValueError, TypeError, InvalidOperation):
                    raise ValueError(f"Item {idx}: Charges must be valid positive numbers.")

                expected_completion_date_str = item_data.get("expected_completion_date")
                expected_completion_date = None
                if expected_completion_date_str:
                    try:
                        expected_completion_date = parse_datetime(expected_completion_date_str)

                        # Validate against order pickup_date
                        if expected_completion_date and order.pickup_date:
                            if expected_completion_date > order.pickup_date:
                                raise ValueError(
                                    f"Item {idx}: Expected completion date must be on or before "
                                    f"the order pickup date ({order.pickup_date})."
                                )

                    except InvalidDateFormat as e:
                        raise ValueError(f"Item {idx}: Invalid date format for expected completion date.")

                notes = item_data.get("notes", "")
                size = item_data.get("size") or None

                # Convert measurements
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
                            raise ValueError(f"Item {idx}: Assigned tailor not found or inactive.")
                    except (ValueError, TypeError):
                        raise ValueError(f"Item {idx}: Invalid tailor ID.")

                # Handle reference image
                reference_image_url = ""
                if isinstance(reference_image_file, str):
                    # It's already a URL (from existing item)
                    reference_image_url = reference_image_file
                elif reference_image_file:
                    # New file upload: delete old image first (if updating)
                    if item_id:
                        order_item_obj = TailorOrderItem.objects.filter(id=item_id, order=order).first()
                        if order_item_obj and order_item_obj.reference_image:
                            delete_order_images_from_s3.delay([order_item_obj.reference_image])
                    try:
                        temp_item_id = f"{order.id}_item_{idx}"
                        reference_image_url = self._upload_image_to_s3(
                            reference_image_file, shop_name, temp_item_id
                        )
                    except ValueError as ve:
                        raise ValueError(f"Item {idx}: {str(ve)}")

                # UPDATE existing item
                if item_id:
                    try:
                        item_id = int(item_id)
                        incoming_item_ids.add(item_id)

                        order_item = TailorOrderItem.objects.filter(
                            id=item_id,
                            order=order
                        ).first()

                        if not order_item:
                            raise ValueError(f"Item {idx}: Item with ID {item_id} not found in this order.")

                        # Update fields
                        order_item.product_name = product_name
                        order_item.product_type = product_type
                        order_item.quantity = quantity
                        if reference_image_url:
                            order_item.reference_image = reference_image_url
                        order_item.assigned_tailor = assigned_tailor
                        order_item.tailor_charge = tailor_charge
                        order_item.customer_tailoring_charge = customer_tailoring_charge
                        order_item.customer_item_charge = customer_item_charge
                        order_item.measurements = measurements
                        order_item.size = size
                        order_item.expected_completion_date = expected_completion_date
                        order_item.notes = notes

                        order_item.save()

                    except (ValueError, TypeError):
                        raise ValueError(f"Item {idx}: Invalid item ID.")

                # CREATE new item
                else:
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
                    incoming_item_ids.add(order_item.id)

            # --- Single-key assignment reconciliation ---
            # Each entry in the array is routed by its shape:
            #   { tailor_id, tailor_charge, ... }           → CREATE new assignment
            #   { id, tailor_charge?, ... }                  → UPDATE existing assignment
            #   { id, _delete: true }                        → DELETE assignment
            # Absent key → existing assignments untouched.
            # Execution order: Delete → Update → Create (prevents unique-constraint races).
            assignments_payload = item_data.get("assignments")  # None = not provided

            if assignments_payload is not None:
                if not isinstance(assignments_payload, list):
                    raise ValueError(f"Item {idx}: assignments must be an array.")

                to_delete = []
                to_update = []
                to_create = []

                for entry in assignments_payload:
                    entry_id = entry.get("id")
                    if entry_id is not None:
                        if entry.get("_delete"):
                            to_delete.append(int(entry_id))
                        else:
                            to_update.append(entry)
                    else:
                        to_create.append(entry)

                # Step 1: Delete
                if to_delete:
                    TailorOrderItemAssignment.objects.filter(
                        id__in=to_delete,
                        order_item=order_item  # scoped to this item for safety
                    ).delete()

                # Step 2: Update existing rows
                for ua in to_update:
                    try:
                        ua_id = int(ua["id"])
                    except (ValueError, TypeError):
                        raise ValueError(f"Item {idx}: Invalid tailor assignment reference.")
                    asgn_obj = TailorOrderItemAssignment.objects.filter(
                        id=ua_id, order_item=order_item
                    ).first()
                    if not asgn_obj:
                        raise ValueError(f"Item {idx}: Assignment {ua_id} not found on this item.")
                    if "tailor_charge" in ua:
                        try:
                            new_tailor_charge = Decimal(str(ua["tailor_charge"])).quantize(Decimal("0.01"))
                            if new_tailor_charge < 0:
                                raise ValueError()
                            asgn_obj.tailor_charge = new_tailor_charge
                        except (ValueError, TypeError, InvalidOperation):
                            logger.warning(f"Item {idx}: invalid tailor_charge for assignment {ua_id}: {ua.get('tailor_charge')!r}")
                            raise ValueError(f"Item {idx}: Invalid charge amount for tailor assignment {ua_id}.")
                    if "tailoring_charge_from_customer" in ua:
                        try:
                            new_customer_charge = Decimal(str(ua["tailoring_charge_from_customer"])).quantize(Decimal("0.01"))
                            if new_customer_charge < 0:
                                raise ValueError()
                            asgn_obj.tailoring_charge_from_customer = new_customer_charge
                        except (ValueError, TypeError, InvalidOperation):
                            logger.warning(f"Item {idx}: invalid tailoring_charge_from_customer for assignment {ua_id}: {ua.get('tailoring_charge_from_customer')!r}")
                            raise ValueError(f"Item {idx}: Invalid customer charge amount for tailor assignment {ua_id}.")
                    if "expected_completion_date" in ua:
                        ecd = ua["expected_completion_date"]
                        if ecd:
                            try:
                                asgn_obj.expected_completion_date = parse_datetime(ecd)
                            except Exception:
                                logger.warning(f"Item {idx}: invalid expected_completion_date for assignment {ua_id}: {ecd!r}")
                                raise ValueError(f"Item {idx}: Invalid expected completion date for tailor assignment {ua_id}.")
                        else:
                            asgn_obj.expected_completion_date = None
                    asgn_obj.save()

                # Step 3: Create new rows
                existing_tailor_ids = set(
                    order_item.assignments.values_list("tailor_id", flat=True)
                )
                seen_new = set()
                for asgn in to_create:
                    try:
                        t_id = int(asgn.get("tailor_id"))
                    except (ValueError, TypeError):
                        logger.warning(f"Item {idx}: invalid tailor_id in new assignment: {asgn.get('tailor_id')!r}")
                        raise ValueError(f"Item {idx}: Invalid tailor selected for a new assignment.")
                    if t_id in existing_tailor_ids:
                        raise ValueError(f"Item {idx}: Tailor {t_id} is already assigned to this item.")
                    if t_id in seen_new:
                        raise ValueError(f"Item {idx}: Duplicate tailor ID {t_id} in assignments.")
                    seen_new.add(t_id)
                    tailor_obj = tailors_dict.get(t_id)
                    if not tailor_obj:
                        raise ValueError(f"Item {idx}: Tailor {t_id} not found or inactive.")
                    try:
                        asgn_charge = Decimal(str(asgn.get("tailor_charge") or 0)).quantize(Decimal("0.01"))
                        if asgn_charge < 0:
                            raise ValueError()
                    except (ValueError, TypeError, InvalidOperation):
                        logger.warning(f"Item {idx}: invalid tailor_charge in new assignment: {asgn.get('tailor_charge')!r}")
                        raise ValueError(f"Item {idx}: Invalid charge amount for one of the assigned tailors.")
                    try:
                        asgn_customer_charge = Decimal(str(asgn.get("tailoring_charge_from_customer") or 0)).quantize(Decimal("0.01"))
                        if asgn_customer_charge < 0:
                            raise ValueError()
                    except (ValueError, TypeError, InvalidOperation):
                        logger.warning(f"Item {idx}: invalid tailoring_charge_from_customer in new assignment: {asgn.get('tailoring_charge_from_customer')!r}")
                        raise ValueError(f"Item {idx}: Invalid customer charge amount for one of the assigned tailors.")
                    asgn_date_str = asgn.get("expected_completion_date")
                    asgn_date = None
                    if asgn_date_str:
                        try:
                            asgn_date = parse_datetime(asgn_date_str)
                        except Exception:
                            logger.warning(f"Item {idx}: invalid expected_completion_date in new assignment: {asgn_date_str!r}")
                            raise ValueError(f"Item {idx}: Invalid expected completion date for one of the assigned tailors.")
                    TailorOrderItemAssignment.objects.create(
                        order_item=order_item,
                        tailor=tailor_obj,
                        tailor_charge=asgn_charge,
                        tailoring_charge_from_customer=asgn_customer_charge,
                        expected_completion_date=asgn_date,
                    )
                    existing_tailor_ids.add(t_id)

                # Sync legacy fields to first remaining assignment
                first_asgn = (
                    order_item.assignments
                    .select_related("tailor")
                    .order_by("created_at", "id")
                    .first()
                )
                if first_asgn:
                    order_item.assigned_tailor = first_asgn.tailor
                    order_item.tailor_charge = first_asgn.tailor_charge
                    order_item.expected_completion_date = first_asgn.expected_completion_date
                else:
                    order_item.assigned_tailor = None
                    order_item.tailor_charge = Decimal("0")
                    order_item.expected_completion_date = None
                order_item.save(update_fields=["assigned_tailor", "tailor_charge", "expected_completion_date"])

                # Accumulate total tailor charge from all current DB rows (single aggregation query)
                asgn_total = order_item.assignments.aggregate(total=Sum('tailor_charge'))['total'] or Decimal('0')
                total_tailor_charge += asgn_total

            elif not is_assignment_only:
                # LEGACY PATH: single assigned_tailor field (only when product fields were provided)
                total_tailor_charge += tailor_charge
                if assigned_tailor:
                    existing_count = order_item.assignments.count()
                    if existing_count > 1:
                        logger.warning(
                            f"Item {order_item.id}: legacy path is overwriting {existing_count} "
                            f"multi-tailor assignment rows with a single assigned_tailor."
                        )
                    order_item.assignments.all().delete()
                    TailorOrderItemAssignment.objects.create(
                        order_item=order_item,
                        tailor=assigned_tailor,
                        tailor_charge=tailor_charge,
                        tailoring_charge_from_customer=customer_tailoring_charge,
                        expected_completion_date=expected_completion_date,
                    )

            total_customer_item_charge += customer_item_charge

            # Update materials for this item (check if 'materials' key exists)
            if "materials" in item_data:
                _update_item_materials(
                    self,
                    order_item,
                    item_data["materials"] or [],
                    materials_dict,
                    idx
                )

            # Calculate material charges
            for usage in order_item.material_usages.all():
                total_material_charge += usage.total_amount

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error processing item {idx}: {str(e)}", exc_info=True)
            raise ValueError(f"Item {idx}: An error occurred.")

    # DELETE items not in request
    items_to_delete = order.items.exclude(id__in=incoming_item_ids)
    if items_to_delete.exists():
        # Delete S3 images for deleted items
        for item in items_to_delete:
            if item.reference_image:
                delete_order_images_from_s3.delay([item.reference_image])
        items_to_delete.delete()

    return total_tailor_charge, total_customer_item_charge, total_material_charge, total_items_tax


def _update_item_materials(self, order_item, materials_data, materials_dict, item_idx):
    """
    Update materials for a single order item.

    This method is ONLY called when 'materials' is explicitly in the item data.

    Logic:
    - Materials WITH 'id': UPDATE existing
    - Materials WITHOUT 'id': CREATE new
    - Existing materials whose IDs are NOT in materials_data: DELETE

    If materials_data is an empty list [], ALL materials for this item are deleted.
    """
    if not isinstance(materials_data, list):
        raise ValueError(f"Item {item_idx}: Materials must be a list.")

    # If explicitly empty array, delete all materials
    if len(materials_data) == 0:
        order_item.material_usages.all().delete()
        return

    incoming_material_ids = set()

    for mat_idx, m in enumerate(materials_data, start=1):
        try:
            material_id = m.get("id")  # TailorMaterialUsage ID
            variant_id = m.get("variant_id")

            if not variant_id:
                continue

            try:
                variant_id = int(variant_id)
            except (ValueError, TypeError):
                raise ValueError(f"Item {item_idx}, Material {mat_idx}: Invalid material ID.")

            variant = materials_dict.get(variant_id)
            if not variant:
                raise ValueError(f"Item {item_idx}, Material {mat_idx}: Material not found.")

            try:
                qty = Decimal(str(m.get("quantity_meters") or 0))
                total_amount = Decimal(str(m.get("total_amount") or 0))
                if qty < 0 or total_amount < 0:
                    raise ValueError()
            except (ValueError, TypeError, InvalidOperation):
                raise ValueError(f"Item {item_idx}, Material {mat_idx}: Invalid quantity or amount.")

            # UPDATE existing material usage
            if material_id:
                try:
                    material_id = int(material_id)
                    incoming_material_ids.add(material_id)

                    usage = TailorMaterialUsage.objects.filter(
                        id=material_id,
                        order_item=order_item
                    ).first()

                    if not usage:
                        raise ValueError(f"Item {item_idx}, Material {mat_idx}: Material usage not found.")

                    usage.material = variant
                    usage.quantity_meters = qty
                    usage.total_amount = total_amount
                    usage.save()

                except (ValueError, TypeError):
                    raise ValueError(f"Item {item_idx}, Material {mat_idx}: Invalid material usage ID.")

            # CREATE new material usage
            else:
                usage = TailorMaterialUsage.objects.create(
                    order_item=order_item,
                    material=variant,
                    quantity_meters=qty,
                    total_amount=total_amount
                )
                incoming_material_ids.add(usage.id)

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error processing material {mat_idx}: {str(e)}", exc_info=True)
            raise ValueError(f"Item {item_idx}, Material {mat_idx}: An error occurred.")

    # DELETE materials not in request
    materials_to_delete = order_item.material_usages.exclude(id__in=incoming_material_ids)
    if materials_to_delete.exists():
        materials_to_delete.delete()


def _update_additional_charges(self, order, charges_data):
    """
    Smart update for additional charges.

    This method is ONLY called when 'additional_charges' is explicitly in the request.

    Logic:
    - Charges WITH 'id': UPDATE existing
    - Charges WITHOUT 'id': CREATE new
    - Existing charges whose IDs are NOT in charges_data: DELETE

    If charges_data is an empty list [], ALL charges are deleted.

    Returns: (total_additional_charges, total_charges_tax)
    """
    from tailored.models import TailorOrderAdditionalCharge

    if not isinstance(charges_data, list):
        raise ValueError("Additional charges must be a list.")

    # If explicitly empty array, delete all charges
    if len(charges_data) == 0:
        order.additional_charges.all().delete()
        return Decimal('0'), Decimal('0')

    # Tax + discount are no longer applied per-charge here. `amount` is
    # always the raw, as-received value for every charge - the caller
    # (partial_update.py) is the single place that applies discount/tax
    # across ALL of an order's charges, without mutating any individual
    # charge's stored amount.
    incoming_charge_ids = set()
    total_additional = Decimal('0')
    total_charges_tax = Decimal('0')

    for idx, charge_data in enumerate(charges_data, start=1):
        charge_id = charge_data.get("id")
        name = (charge_data.get("name") or "").strip()
        amount = charge_data.get("amount")

        if not name:
            raise ValueError(f"Charge {idx}: Name is required.")

        try:
            amount = Decimal(str(amount or 0))
            if amount <= 0:
                raise ValueError()
        except (ValueError, TypeError, InvalidOperation):
            raise ValueError(f"Charge {idx}: Amount must be a positive number.")

        try:
            # UPDATE existing charge
            if charge_id:
                try:
                    charge_id = int(charge_id)
                    incoming_charge_ids.add(charge_id)

                    charge = TailorOrderAdditionalCharge.objects.filter(
                        id=charge_id,
                        order=order
                    ).first()

                    if not charge:
                        raise ValueError(f"Charge {idx}: Charge with ID {charge_id} not found.")

                    charge.name = name
                    charge.amount = amount
                    charge.save()

                except (ValueError, TypeError):
                    raise ValueError(f"Charge {idx}: Invalid charge ID.")

            # CREATE new charge
            else:
                charge = TailorOrderAdditionalCharge.objects.create(
                    order=order,
                    name=name,
                    amount=amount,
                )
                incoming_charge_ids.add(charge.id)

            total_additional += amount

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error processing charge {idx}: {str(e)}", exc_info=True)
            raise ValueError(f"Charge {idx}: Failed to save.")

    # DELETE charges not in request
    charges_to_delete = order.additional_charges.exclude(id__in=incoming_charge_ids)
    if charges_to_delete.exists():
        charges_to_delete.delete()

    return total_additional, total_charges_tax
