from django.db.models import F
from decimal import Decimal, InvalidOperation
from services.models import ProductVariant
from tailored.models import Tailor
import logging

logger = logging.getLogger("api_requests")


def _validate_and_prepare_fabric_usage(self, items_data):
    """
    Validate fabric length requests against available stock.
    Returns: list of (variant_id, requested_length) tuples for deduction
    Raises: ValueError if validation fails
    """
    fabric_requests = []
    variant_ids = set()

    # Collect all requested variant IDs and lengths
    for idx, item in enumerate(items_data, start=1):
        materials = item.get("materials")
        if not materials:
            continue

        if not isinstance(materials, list):
            raise ValueError(f"Item {idx}: Materials must be a list.")

        for mat_idx, material in enumerate(materials, start=1):
            variant_id = material.get("variant_id")
            requested_length = material.get("quantity_meters")

            if not variant_id:
                continue

            # Validate variant_id is numeric
            try:
                variant_id = int(variant_id)
            except (ValueError, TypeError):
                raise ValueError(
                    f"Item {idx}, Material {mat_idx}: Invalid variant ID '{variant_id}'."
                )

            # Validate requested length exists
            if requested_length is None or requested_length == "":
                raise ValueError(
                    f"Item {idx}, Material {mat_idx}: Fabric quantity (meters) is required."
                )

            # Validate requested length is numeric
            try:
                requested_length = float(requested_length)
            except (ValueError, TypeError):
                raise ValueError(
                    f"Item {idx}, Material {mat_idx}: Fabric quantity must be a valid number."
                )

            # Validate quantity is positive
            if requested_length <= 0:
                raise ValueError(
                    f"Item {idx}, Material {mat_idx}: Fabric quantity must be greater than 0."
                )

            variant_ids.add(variant_id)
            fabric_requests.append((idx, mat_idx, variant_id, requested_length))

    if not fabric_requests:
        return []

    # OPTIMIZATION: Batch fetch all variants in single query
    try:
        variants_dict = {
            v.id: v for v in ProductVariant.objects.filter(
                id__in=variant_ids,
                product__shop_service__shop_id=self.shop_id,
                deleted__isnull=True  # Exclude soft-deleted variants
            ).select_related("product")
        }
    except Exception as e:
        logger.error(f"Database error fetching variants: {str(e)}", exc_info=True)
        raise ValueError("Failed to retrieve material information. Please try again.")

    # Validate availability and collect for deduction
    fabric_deductions = []
    for item_idx, mat_idx, variant_id, requested_length in fabric_requests:
        variant = variants_dict.get(variant_id)

        # Check if variant exists and belongs to this shop
        if not variant:
            raise ValueError(
                f"Item {item_idx}, Material {mat_idx}: Material not found in your shop."
            )

        # Check fabric_length availability
        available_length = variant.fabric_length
        if available_length is None:
            raise ValueError(
                f"Item {item_idx}, Material {mat_idx}: Material '{variant.product.name}' does not support fabric length tracking."
            )

        try:
            available_length_float = float(available_length)
        except (ValueError, TypeError):
            logger.error(f"Invalid fabric_length for variant {variant_id}: {available_length}")
            raise ValueError(
                f"Item {item_idx}, Material {mat_idx}: Invalid material data. Please contact support."
            )

        # Check if requested exceeds available
        if requested_length > available_length_float:
            raise ValueError(
                f"Item {item_idx}, Material {mat_idx}: Insufficient fabric available. "
                f"Requested: {requested_length}m, Available: {available_length_float}m."
            )

        fabric_deductions.append((variant_id, requested_length))

    return fabric_deductions


def _deduct_fabric_lengths(self, fabric_deductions):
    """
    Deduct fabric lengths from ProductVariants after order is created.
    Uses F() expressions for atomic updates.
    """
    if not fabric_deductions:
        return

    try:
        for variant_id, deduct_length in fabric_deductions:
            updated = ProductVariant.objects.filter(id=variant_id).update(
                fabric_length=F('fabric_length') - float(deduct_length)
            )
            if updated == 0:
                logger.error(f"Failed to update fabric_length for variant {variant_id}")
                raise ValueError(f"Failed to deduct fabric for material ID {variant_id}.")

    except Exception as e:
        logger.error(f"Error deducting fabric lengths: {str(e)}", exc_info=True)
        raise


def _calculate_fabric_adjustments_for_update(self, order, new_items_data):
    """
    Calculate fabric adjustments by comparing current vs new fabric usage.

    Returns: list of (variant_id, delta_meters) tuples
    - Positive delta = need to deduct more fabric
    - Negative delta = return fabric to stock

    Raises: ValueError if validation fails
    """
    # Get current fabric usage from DB
    current_usage = {}  # variant_id -> total_meters
    for item in order.items.all():
        for usage in item.material_usages.all():
            variant_id = usage.material_id
            current_usage[variant_id] = current_usage.get(variant_id, Decimal('0')) + usage.quantity_meters

    # Calculate new fabric usage from request
    new_usage = {}  # variant_id -> total_meters
    variant_ids = set()

    for idx, item in enumerate(new_items_data, start=1):
        # Only process materials if they're explicitly in the item
        if "materials" not in item:
            # If materials not in request, keep existing materials for this item
            item_id = item.get("id")
            if item_id:
                try:
                    item_id = int(item_id)
                    existing_item = order.items.filter(id=item_id).first()
                    if existing_item:
                        for usage in existing_item.material_usages.all():
                            variant_id = usage.material_id
                            new_usage[variant_id] = new_usage.get(variant_id, Decimal('0')) + usage.quantity_meters
                            variant_ids.add(variant_id)
                except (ValueError, TypeError):
                    pass
            continue

        materials = item.get("materials") or []
        if not isinstance(materials, list):
            raise ValueError(f"Item {idx}: Materials must be a list.")

        for mat_idx, material in enumerate(materials, start=1):
            variant_id = material.get("variant_id")
            if not variant_id:
                continue

            try:
                variant_id = int(variant_id)
            except (ValueError, TypeError):
                raise ValueError(f"Item {idx}, Material {mat_idx}: Invalid variant ID.")

            quantity = material.get("quantity_meters")
            if quantity is None or quantity == "":
                raise ValueError(f"Item {idx}, Material {mat_idx}: Fabric quantity is required.")

            try:
                quantity = Decimal(str(quantity))
            except (ValueError, TypeError, InvalidOperation):
                raise ValueError(f"Item {idx}, Material {mat_idx}: Invalid quantity.")

            if quantity <= 0:
                raise ValueError(f"Item {idx}, Material {mat_idx}: Quantity must be greater than 0.")

            variant_ids.add(variant_id)
            new_usage[variant_id] = new_usage.get(variant_id, Decimal('0')) + quantity

    # Calculate deltas
    all_variant_ids = set(current_usage.keys()) | set(new_usage.keys())
    fabric_adjustments = []
    additional_usage = {}

    for variant_id in all_variant_ids:
        current = current_usage.get(variant_id, Decimal('0'))
        new = new_usage.get(variant_id, Decimal('0'))
        delta = new - current

        if delta != 0:
            fabric_adjustments.append((variant_id, delta))
            if delta > 0:
                additional_usage[variant_id] = delta

    # Validate availability for additional fabric
    if additional_usage:
        try:
            variants_dict = {
                v.id: v for v in ProductVariant.objects.filter(
                    id__in=additional_usage.keys(),
                    product__shop_service__shop_id=self.shop_id,
                    deleted__isnull=True
                ).select_related("product")
            }
        except Exception as e:
            logger.error(f"Database error: {str(e)}", exc_info=True)
            raise ValueError("Failed to retrieve material information.")

        for variant_id, additional_meters in additional_usage.items():
            variant = variants_dict.get(variant_id)

            if not variant:
                raise ValueError(f"Material ID {variant_id} not found.")

            available_length = variant.fabric_length
            if available_length is None:
                raise ValueError(f"Material '{variant.product.name}' doesn't support fabric tracking.")

            try:
                available_length = Decimal(str(available_length))
            except (ValueError, TypeError, InvalidOperation):
                raise ValueError(f"Invalid data for '{variant.product.name}'.")

            if additional_meters > available_length:
                raise ValueError(
                    f"Insufficient fabric for '{variant.product.name}'. "
                    f"Need {additional_meters}m more, available: {available_length}m."
                )

    return fabric_adjustments


def _apply_fabric_adjustments(self, fabric_adjustments):
    """
    Apply fabric adjustments atomically using F() expressions.

    - Positive delta = deduct more fabric (fabric_length decreases)
    - Negative delta = return fabric to stock (fabric_length increases)
    """
    if not fabric_adjustments:
        return

    try:
        for variant_id, delta_meters in fabric_adjustments:
            if delta_meters == 0:
                continue

            updated = ProductVariant.objects.filter(id=variant_id).update(
                fabric_length=F('fabric_length') - delta_meters
            )

            if updated == 0:
                logger.error(f"Failed to update fabric for variant {variant_id}")
                raise ValueError(f"Failed to adjust fabric for material ID {variant_id}.")

    except Exception as e:
        logger.error(f"Error applying fabric adjustments: {str(e)}", exc_info=True)
        raise


def _execute_fabric_restoration(self, order, allow_missing_variants=False):
    """
    Execute fabric restoration with robust error handling and edge case management.

    Args:
        order: TailorOrder instance
        allow_missing_variants: bool - if True, missing or soft-deleted variants won't block deletion

    Returns:
        dict: Summary of restoration results with detailed breakdown
    """
    restoration_summary = {
        'restored_count': 0,
        'failed_count': 0,
        'skipped_count': 0,
        'details': []
    }

    # Collect all material usages
    all_usages = [
        {
            'usage_id': usage.id,
            'variant_id': usage.material.id,
            'quantity_meters': usage.quantity_meters,
            'material_name': usage.material.product.name,
            'attribute': usage.material.attribute or 'default',
            'item_name': item.product_name,
            'current_fabric_length': usage.material.fabric_length
        }
        for item in order.items.all()
        for usage in item.material_usages.all()
    ]

    if not all_usages:
        logger.info(f"No material usages found for order {order.shop_tailor_id}")
        return restoration_summary

    # Group by variant_id for batch restoration
    variant_totals = {}
    for usage in all_usages:
        variant_id = usage['variant_id']
        if variant_id not in variant_totals:
            variant_totals[variant_id] = {
                'total_to_restore': 0,
                'usages': [],
                'material_info': {
                    'name': usage['material_name'],
                    'attribute': usage['attribute'],
                    'current_length': usage['current_fabric_length']
                }
            }
        variant_totals[variant_id]['total_to_restore'] += usage['quantity_meters']
        variant_totals[variant_id]['usages'].append(usage)

    # Perform restoration
    for variant_id, data in variant_totals.items():
        material_info = data['material_info']
        total_restore = data['total_to_restore']

        try:
            # Skip zero or negative quantities
            if total_restore <= 0:
                restoration_summary['skipped_count'] += 1
                restoration_summary['details'].append({
                    'variant_id': variant_id,
                    'material_name': material_info['name'],
                    'status': 'skipped',
                    'reason': 'Zero or negative quantity'
                })
                continue

            # Skip variants that don't track fabric
            if material_info['current_length'] is None:
                restoration_summary['skipped_count'] += 1
                restoration_summary['details'].append({
                    'variant_id': variant_id,
                    'material_name': material_info['name'],
                    'status': 'skipped',
                    'reason': 'Material does not support fabric length tracking'
                })
                continue

            # Atomic update using F()
            updated_count = ProductVariant.objects.filter(
                id=variant_id,
                deleted__isnull=True
            ).update(fabric_length=F('fabric_length') + total_restore)

            if updated_count == 0:
                # Variant missing or soft-deleted
                is_critical = not allow_missing_variants
                restoration_summary['failed_count'] += 1
                restoration_summary['details'].append({
                    'variant_id': variant_id,
                    'material_name': material_info['name'],
                    'status': 'failed',
                    'reason': 'Variant not found or deleted',
                    'critical_failure': is_critical
                })
                if is_critical:
                    logger.warning(f"Critical: Variant {variant_id} missing, cannot restore fabric")
                else:
                    logger.info(f"Non-critical: Variant {variant_id} missing, skipping restoration")
            else:
                # Success
                restoration_summary['restored_count'] += 1
                restoration_summary['details'].append({
                    'variant_id': variant_id,
                    'material_name': material_info['name'],
                    'quantity_restored': str(total_restore),
                    'usage_count': len(data['usages']),
                    'status': 'success'
                })
                logger.debug(f"Restored {total_restore}m fabric to variant {variant_id} ({material_info['name']})")

        except Exception as e:
            restoration_summary['failed_count'] += 1
            restoration_summary['details'].append({
                'variant_id': variant_id,
                'material_name': material_info.get('name', 'Unknown'),
                'status': 'error',
                'reason': str(e),
                'critical_failure': True
            })
            logger.error(f"Error restoring fabric for variant {variant_id}: {str(e)}", exc_info=True)

    return restoration_summary
