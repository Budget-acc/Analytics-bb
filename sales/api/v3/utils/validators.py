from datetime import datetime

from django.utils import timezone


def validate_amounts(total_before_discount, paid_amount, discount_amount):
    """
    Validate sale amounts:
    - total_before_discount = sum of all line items
    - final_amount = total_before_discount - discount_amount
    - paid_amount should not exceed final_amount
    - all amounts must be non-negative
    """
    if total_before_discount <= 0:
        return False, "Total sale amount must be greater than zero."
    if paid_amount < 0:
        return False, "Paid amount cannot be negative."
    if discount_amount < 0:
        return False, "Discount amount cannot be negative."
    if discount_amount > total_before_discount:
        return False, "Discount cannot exceed total sale amount."

    final_amount = total_before_discount - discount_amount
    if paid_amount > final_amount:
        return False, "Paid amount cannot exceed the amount due after discount."

    return True, None


def validate_sale_date(sale_date):
    """
    Validate sale_date:
    - Supports 'YYYY-MM-DD' or 'DD-MM-YYYY'
    - Cannot be in the future
    - Year must be within a reasonable range (2000 to current year + 1)
    Returns (date_obj, error_message)
    """
    if not sale_date:
        return timezone.now().date(), None  # default to today

    date_formats = ["%Y-%m-%d", "%d-%m-%Y"]
    date_obj = None

    for fmt in date_formats:
        try:
            if isinstance(sale_date, str):
                date_obj = datetime.strptime(sale_date, fmt).date()
            elif isinstance(sale_date, datetime):
                date_obj = sale_date.date()
            else:
                date_obj = sale_date  # assume already a date object
            break
        except (ValueError, TypeError):
            continue

    if not date_obj:
        return None, "Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY."

    current_year = timezone.now().year
    if not (2000 <= date_obj.year <= current_year + 1):
        return None, f"Year {date_obj.year} is invalid. Must be between 2000 and {current_year + 1}."

    return date_obj, None


def verify_variant_belongs_to_shop(variant_id, shop_id):
    """Verify that variant belongs to the shop"""
    from services.models import ProductVariant

    variant = ProductVariant.objects.filter(
        id=variant_id,
        product__shop_service__shop_id=shop_id,
        is_active=True
    ).first()
    return variant
