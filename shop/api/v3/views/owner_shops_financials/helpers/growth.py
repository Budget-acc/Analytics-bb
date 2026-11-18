def _calculate_growth_percentage(self, current_value, previous_value):
    """Calculate growth percentage safely"""
    if not previous_value or previous_value == 0:
        return 0.0
    try:
        return float(round(((current_value - previous_value) / abs(previous_value)) * 100, 2))
    except (ZeroDivisionError, TypeError, ValueError):
        return 0.0
