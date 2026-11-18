from datetime import datetime
from django.core.exceptions import ValidationError


def _get_target_year(self, request):
    """
    Get year from query params or default to current year
    Returns: int year
    """
    year = request.query_params.get('year')
    if year:
        try:
            year = int(year)
            if year < 2000 or year > 2100:
                raise ValueError("Year must be between 2000 and 2100")
            return year
        except (ValueError, TypeError):
            raise ValidationError("Invalid year format. Year must be a valid integer.")

    return datetime.now().year

def _get_target_year_month(self, request):
    """
    Get year and month from query params.
    Defaults to current year & month if not provided.
    """
    now = datetime.now()
    year = request.query_params.get('year')
    month = request.query_params.get('month')

    # Default values
    year = int(year) if year else now.year
    month = int(month) if month else now.month

    if year < 2000 or year > 2100:
        raise ValidationError("Year must be between 2000 and 2100")

    if month < 1 or month > 12:
        raise ValidationError("Month must be between 1 and 12")

    return year, month
