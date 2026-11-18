import logging
from datetime import datetime, date
from django.utils.timezone import make_aware
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("api_requests")


def combine_date_time(base_date, time_obj=None, default_end_of_day=False, default_start_of_day=False):
    """
    Combines a base datetime with a specific time object.
    If no time is provided and default_end_of_day is True, sets the time to 23:59:59.
    If no time is provided and default_start_of_day is True, sets the time to 00:00:01.
    """
    if time_obj:
        return base_date.replace(hour=time_obj.hour, minute=time_obj.minute, second=0)
    elif default_end_of_day:
        return base_date.replace(hour=23, minute=59, second=59)
    elif default_start_of_day:
        return base_date.replace(hour=0, minute=0, second=1)
    return base_date


def parse_date_string(date_str):
    formats = ["%d-%m-%Y", "%d-%m-%YT%H:%M:%S"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue


def _validate_dates(self, request_data):
    """
    Validate pickup, return, and cooling period dates.
    Returns (pickup_date, return_date, cooling_period_end, error_response)
    """
    pickup_date_str = request_data.get("pickup_date")
    return_date_str = request_data.get("return_date")
    cooling_period_end_str = request_data.get("cooling_period_end")

    # Extract time fields
    pickup_time_str = request_data.get("pickup_time")
    return_time_str = request_data.get("return_time")
    cooling_period_type = request_data.get("cooling_period_type", "after")

    if not pickup_date_str:
        return None, None, None, None, None, None, self.validation_error_response(
            errors={"pickup_date": ["Pickup date is required."]},
            message="Pickup date is required."
        )
    if not return_date_str:
        return None, None, None, None, None, None, self.validation_error_response(
            errors={"return_date": ["Return date is required."]},
            message="Return date is required."
        )

    try:
        # Validate and parse time fields FIRST
        pickup_time = None
        return_time = None

        if pickup_time_str:
            try:
                pickup_time = datetime.strptime(pickup_time_str, "%H:%M").time()
            except ValueError:
                return None, None, None, None, None, None, self.validation_error_response(
                    errors={"pickup_time": ["Invalid pickup time format. Use HH:MM (e.g., 10:30)."]},
                    message="Invalid pickup time format."
                )

        if return_time_str:
            try:
                return_time = datetime.strptime(return_time_str, "%H:%M").time()
            except ValueError:
                return None, None, None, None, None, None, self.validation_error_response(
                    errors={"return_time": ["Invalid return time format. Use HH:MM (e.g., 18:00)."]},
                    message="Invalid return time format."
                )

        pickup_date = make_aware(parse_date_string(pickup_date_str))
        return_date = make_aware(parse_date_string(return_date_str))

        pickup_date = combine_date_time(pickup_date, pickup_time, default_start_of_day=True)
        
        # If no return_time is provided, we default to the end of the day (23:59:59)
        return_date = combine_date_time(return_date, return_time, default_end_of_day=True)

        if return_date <= pickup_date:
            return None, None, None, None, None, None, self.validation_error_response(
                errors={"return_date": ["Return time must be after pickup time."]},
                message="Return time must be after pickup time."
            )

        # Validate cooling period type
        if cooling_period_type not in ["before", "after"]:
            return None, None, None, None, None, None, self.validation_error_response(
                errors={"cooling_period_type": ["Cooling period type must be 'before' or 'after'."]},
                message="Invalid cooling period type."
            )

        cooling_period_end = None
        if cooling_period_end_str:
            try:
                cooling_period_end = make_aware(parse_date_string(cooling_period_end_str))

                # Validate based on cooling period type
                if cooling_period_type == "before":
                    if cooling_period_end.date() > pickup_date.date():
                        return None, None, None, None, None, None, self.validation_error_response(
                            errors={"cooling_period_end": ["Cooling period (before) must be before the pickup date."]},
                            message="Cooling period must be before pickup date when type is 'before'."
                        )
                else:  # "after"
                    if cooling_period_end.date() < return_date.date():
                        return None, None, None, None, None, None, self.validation_error_response(
                            errors={"cooling_period_end": ["Cooling period (after) must be greater than or equal to return date."]},
                            message="Cooling period must be after return date when type is 'after'."
                        )
                    # Default cooling period end to 23:59:59 when type is 'after'
                    cooling_period_end = combine_date_time(cooling_period_end, default_end_of_day=True)
            except Exception:
                return None, None, None, None, None, None, self.validation_error_response(
                    errors={"cooling_period_end": ["Invalid cooling period end format."]},
                    message="Invalid cooling period end format."
                )

        return pickup_date, return_date, cooling_period_end, pickup_time, return_time, cooling_period_type, None

    except Exception:
        return None, None, None, None, None, None, self.validation_error_response(
            errors={"date": ["Invalid date format."]},
            message="Invalid date format."
        )


def _validate_pickup_not_before_booking(self, pickup_dt, booking_dt, field="pickup_date"):
    """
    The pickup date must not be earlier than the booking date (created_at /
    booked_date). Compared by local calendar date, so a same-day pickup is
    always valid (pickup without a time is stored as 00:00:01).
    Returns error_response or None.
    """
    if not pickup_dt or not booking_dt:
        return None
    if timezone.localtime(pickup_dt).date() >= timezone.localtime(booking_dt).date():
        return None

    if field == "booked_date":
        message = "Booked date cannot be later than the pickup date."
        error = "booked_date cannot be later than the pickup date."
    else:
        message = "Pickup date cannot be earlier than the booking date."
        error = message
    return self.validation_error_response(errors={field: [error]}, message=message)


def _validate_rental_duration_type(self, request_data):
    """
    Validate rental_duration_type field.
    Returns (rental_duration_type, error_response)
    """
    rental_duration_type = request_data.get("rental_duration_type", "daily")

    valid_choices = ["daily", "hourly"]
    if rental_duration_type not in valid_choices:
        return None, self.validation_error_response(
            errors={"rental_duration_type": [f"Invalid rental duration type. Must be one of: {', '.join(valid_choices)}"]},
            message="Invalid rental duration type."
        )

    return rental_duration_type, None


def _parse_and_validate_dates(self, booking, data, savepoint, old_pickup_date, old_return_date, old_cooling_end):
    """
    Parse and validate new dates with updated return extension logic.
    Return date can now be extended anytime (even if booking ended long ago).
    """
    # Returned bookings: pickup date stays locked (an unchanged re-send is allowed),
    # return date can be corrected - see the return-date checks below.
    is_returned_booking = booking.delivery_status == "returned"
    if is_returned_booking and data.get("pickup_date"):
        requested_pickup = parse_date_string(data.get("pickup_date"))
        if requested_pickup and requested_pickup.date() != old_pickup_date.date():
            transaction.savepoint_rollback(savepoint)
            return {
                'error': self.validation_error_response(
                    errors={"date": ["Cannot modify dates - products have been returned."]},
                    message="Cannot modify dates for returned bookings."
                )
            }

    pickup_date_str = data.get("pickup_date")
    return_date_str = data.get("return_date")
    cooling_period_end_str = data.get("cooling_period_end")

    # NEW: Get time fields
    pickup_time_str = data.get("pickup_time")
    return_time_str = data.get("return_time")
    cooling_period_type = data.get("cooling_period_type")

    # Get current cooling_period_type from booking details if not provided
    if cooling_period_type is None and booking.details:
        cooling_period_type = booking.details.get("cooling_period_type", "after")
    elif cooling_period_type is None:
        cooling_period_type = "after"  # Default

    new_pickup_date = old_pickup_date
    new_return_date = old_return_date
    new_cooling_end = old_cooling_end
    new_pickup_time = booking.pickup_time
    new_return_time = booking.return_time


    if not any([pickup_date_str, return_date_str, cooling_period_end_str, pickup_time_str, return_time_str, cooling_period_type in data]):
        return new_pickup_date, new_return_date, new_cooling_end, new_pickup_time, new_return_time, cooling_period_type

    try:
        current_date = timezone.now().date()

        # Dates are parsed after time validation below

        # NEW: Parse time fields
        if pickup_time_str:
            try:
                new_pickup_time = datetime.strptime(pickup_time_str, "%H:%M").time()
            except ValueError:
                transaction.savepoint_rollback(savepoint)
                return {
                    'error': self.validation_error_response(
                        errors={"pickup_time": ["Invalid pickup time format. Use HH:MM."]},
                        message="Invalid pickup time format."
                    )
                }

        if return_time_str:
            try:
                new_return_time = datetime.strptime(return_time_str, "%H:%M").time()
            except ValueError:
                transaction.savepoint_rollback(savepoint)
                return {
                    'error': self.validation_error_response(
                        errors={"return_time": ["Invalid return time format. Use HH:MM."]},
                        message="Invalid return time format."
                    )
                }

        # Validate cooling_period_type
        if cooling_period_type not in ["before", "after"]:
            transaction.savepoint_rollback(savepoint)
            return {
                'error': self.validation_error_response(
                    errors={"cooling_period_type": ["Must be 'before' or 'after'."]},
                    message="Invalid cooling period type."
                )
            }

        old_pickup_date_only = old_pickup_date.date()
        old_return_date_only = old_return_date.date()

        # Parse new dates
        if pickup_date_str:
            new_pickup_date = make_aware(parse_date_string(pickup_date_str))
            new_pickup_date = combine_date_time(new_pickup_date, new_pickup_time, default_start_of_day=True)
        elif new_pickup_time and not pickup_date_str:
            new_pickup_date = combine_date_time(new_pickup_date, new_pickup_time, default_start_of_day=True)

        if return_date_str:
            new_return_date = make_aware(parse_date_string(return_date_str))
            # If no return_time is provided, we default to the end of the day (23:59:59)
            new_return_date = combine_date_time(new_return_date, new_return_time, default_end_of_day=True)
        elif new_return_time and not return_date_str:
            new_return_date = combine_date_time(new_return_date, new_return_time)

        if cooling_period_end_str:
            new_cooling_end = make_aware(parse_date_string(cooling_period_end_str))
            if cooling_period_type == "after":
                new_cooling_end = combine_date_time(new_cooling_end, default_end_of_day=True)

        # Returned booking - return date correction (only when the day actually changes,
        # so an unchanged re-send on amount edits is unaffected):
        # must be after pickup and not in the future; a date-only "today" uses the current time.
        if is_returned_booking and return_date_str and new_return_date.date() != old_return_date_only:
            now_local = timezone.localtime(timezone.now()).replace(microsecond=0)
            if not return_time_str and new_return_date.date() == now_local.date():
                new_return_date = now_local
                # keep the stored return_time consistent with return_date
                new_return_time = now_local.time().replace(second=0, microsecond=0)

            if new_return_date <= new_pickup_date:
                transaction.savepoint_rollback(savepoint)
                return {
                    'error': self.validation_error_response(
                        errors={"return_date": ["Return date must be after pickup date."]},
                        message="Return date must be after pickup date."
                    )
                }

            if new_return_date > now_local:
                transaction.savepoint_rollback(savepoint)
                return {
                    'error': self.validation_error_response(
                        errors={"return_date": ["Return date cannot be later than the current date and time."]},
                        message="Return date must be in the past for a returned booking."
                    )
                }

        # Validation flags
        pickup_is_past = old_pickup_date_only < current_date
        return_is_past = old_return_date_only < current_date

        if pickup_is_past and return_is_past:
            # Both dates in past - cannot change pickup, but return can be extended
            if return_date_str:
                if new_return_date <= old_pickup_date:
                    transaction.savepoint_rollback(savepoint)
                    return {
                        'error': self.validation_error_response(
                            errors={"return_date": ["Return date must be after pickup date."]},
                            message="Return date must be after pickup date."
                        )
                    }
                # Returned bookings may correct the return date to a past date
                if new_return_date.date() < current_date and not is_returned_booking:
                    transaction.savepoint_rollback(savepoint)
                    return {
                        'error': self.validation_error_response(
                            errors={"return_date": ["Return date cannot be in the past."]},
                            message="Return date cannot be in the past."
                        )
                    }

                if old_cooling_end and not cooling_period_end_str:
                    if new_return_date > old_cooling_end:
                        transaction.savepoint_rollback(savepoint)
                        return {
                            'error': self.validation_error_response(
                                errors={"return_date": ["Return date cannot exceed the existing cooling period end. Include a new cooling_period_end in your request if you want to extend it."]},
                                message="Return date cannot exceed existing cooling period date."
                            )
                        }

        elif pickup_is_past:
            # Pickup in past - cannot change pickup, but return can be extended
            if return_date_str:
                if new_return_date <= old_pickup_date:
                    transaction.savepoint_rollback(savepoint)
                    return {
                        'error': self.validation_error_response(
                            errors={"return_date": ["Return date must be after pickup date."]},
                            message="Return date must be after pickup date."
                        )
                    }
                # Returned bookings may correct the return date to a past date
                if new_return_date.date() < current_date and not is_returned_booking:
                    transaction.savepoint_rollback(savepoint)
                    return {
                        'error': self.validation_error_response(
                            errors={"return_date": ["Return date cannot be in the past."]},
                            message="Return date cannot be in the past."
                        )
                    }

        else:
            # Normal case - both dates in future
            if pickup_date_str and new_pickup_date != old_pickup_date and new_pickup_date.date() < current_date:
                transaction.savepoint_rollback(savepoint)
                return {
                    'error': self.validation_error_response(
                        errors={"pickup_date": ["Pickup date cannot be in the past."]},
                        message="Pickup date cannot be in the past."
                    )
                }

            if return_date_str and new_return_date <= new_pickup_date:
                transaction.savepoint_rollback(savepoint)
                return {
                    'error': self.validation_error_response(
                        errors={"return_date": ["Return date must be after pickup date."]},
                        message="Return date must be after pickup date."
                    )
                }

        # Validate cooling period based on type
        if cooling_period_end_str:
            if cooling_period_type == "before":
                reference_pickup_date = new_pickup_date if pickup_date_str else old_pickup_date
                if new_cooling_end >= reference_pickup_date:
                    transaction.savepoint_rollback(savepoint)
                    return {
                        'error': self.validation_error_response(
                            errors={"cooling_period_end": ["Cooling period (before) must be before pickup date."]},
                            message="Cooling period must be before pickup date."
                        )
                    }
            else:  # "after"
                reference_return_date = new_return_date if return_date_str else old_return_date
                if new_cooling_end < reference_return_date:
                    transaction.savepoint_rollback(savepoint)
                    return {
                        'error': self.validation_error_response(
                            errors={"cooling_period_end": ["Cooling period (after) must be after return date."]},
                            message="Cooling period must be after return date."
                        )
                    }

        return new_pickup_date, new_return_date, new_cooling_end, new_pickup_time, new_return_time, cooling_period_type


    except ValueError:
        transaction.savepoint_rollback(savepoint)
        return {
            'error': self.validation_error_response(
                errors={"date": ["Invalid date format. Use DD-MM-YYYYTHH:MM:SS"]},
                message="Invalid date format."
            )
        }


def _get_effective_dates(self, pickup_date, return_date, cooling_period_end, cooling_period_type):
    """
    Calculate effective start and end dates for availability based on cooling period type.
    NEW: Replaces _get_effective_end_datetime with comprehensive date calculation.

    Returns: (effective_start_datetime, effective_end_datetime)
    """
    if not cooling_period_end:
        # No cooling period: use pickup to return
        return pickup_date, return_date

    # ---------------- BEFORE COOLING ----------------
    if cooling_period_type == "before":

        effective_start = cooling_period_end

        if cooling_period_end.date() == pickup_date.date():
            effective_start = datetime.combine(
                cooling_period_end.date(),
                pickup_date.time()
            )
            effective_start = make_aware(effective_start)

        return effective_start, return_date

    # ---------------- AFTER COOLING ----------------
    effective_end = cooling_period_end

    if cooling_period_end.date() == return_date.date():
        effective_end = datetime.combine(
            cooling_period_end.date(),
            return_date.time()
        )
        effective_end = make_aware(effective_end)

    return pickup_date, effective_end
