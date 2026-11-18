from shop.models import Staff
from tailored.api.v3.utils.date_utils import parse_datetime
import logging

logger = logging.getLogger("api_requests")


def _validate_staff(self, staff_id):
    """
    Validate that staff belongs to the current shop.
    Returns: Staff object if valid
    Raises: ValueError if validation fails
    """
    if not staff_id:
        return None

    try:
        staff_id = int(staff_id)
    except (ValueError, TypeError):
        raise ValueError("Invalid staff ID format.")

    try:
        staff = Staff.objects.get(
            id=staff_id,
            shop_id=self.shop_id,
            is_deleted=False
        )
        return staff

    except Staff.DoesNotExist:
        raise ValueError("Staff not found or inactive in this shop.")


def _validate_expected_completion_dates(self, items_data, pickup_date):
    """
    Validate that all expected_completion_date values are on or before pickup_date.
    Returns: None if validation passes, error message if fails.
    """
    if not pickup_date:
        # If no pickup_date is set, validation passes
        return None

    try:
        pickup_dt = parse_datetime(pickup_date) if isinstance(pickup_date, str) else pickup_date
        if not pickup_dt:
            return None

    except (ValueError, TypeError):
        # If pickup_date is invalid, we'll skip this validation
        return None

    for idx, item in enumerate(items_data, start=1):
        expected_date_str = item.get("expected_completion_date")
        if expected_date_str:
            try:
                expected_dt = parse_datetime(expected_date_str)
                if expected_dt and expected_dt > pickup_dt:
                    return f"Item {idx}: Expected completion date must be on or before the order pickup date."
            except (ValueError, TypeError) as e:
                # Let other validations handle invalid date format
                continue

    return None
