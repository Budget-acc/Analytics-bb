from shop.models import Staff


def _validate_staff_exists(self, staff_id):
    """
    Validate if staff exists and belongs to current shop
    Returns: Staff instance or None
    """
    try:
        staff = Staff.objects.get(id=staff_id, shop_id=self.shop_id)
        return staff
    except Staff.DoesNotExist:
        return None
    except (ValueError, TypeError):
        return None
