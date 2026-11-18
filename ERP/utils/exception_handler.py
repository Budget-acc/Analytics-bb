import logging
import time
from rest_framework.views import exception_handler as drf_exception_handler
from django.utils.encoding import force_str
from .user_helpers import get_user_info, get_shop_info, flatten_error, get_client_ip

logger = logging.getLogger('api_requests')


def custom_exception_handler(exc, context):
    """Enhanced custom exception handler with standardized responses"""
    from .response_utils import StandardResponse, ResponseMessage, ResponseStatus
    response = drf_exception_handler(exc, context)


    if response is not None:
        request = context.get("request")
        client_ip = get_client_ip(request) if request else "Unknown"

        try:
            user_display, user_id = get_user_info(getattr(request, 'user', None))
        except Exception as e:
            logger.debug(f"get_user_info failed: {e}")
            user_display, user_id = "Unknown", None

        try:
            shop_display, shop_id = get_shop_info(request)
        except Exception as e:
            logger.debug(f"get_shop_info failed: {e}")
            shop_display, shop_id = "Unknown Shop", None

        # Get processing time if available
        processing_time = None
        if hasattr(request, '_start_time'):
            processing_time = round((time.time() - request._start_time) * 1000, 2)

        # Flatten response data for logging
        flattened_response_data = flatten_error(response.data)

        # Get request body
        request_body = getattr(request, '_logged_body', None)

        # Create standardized response based on status code
        if response.status_code == 404:
            standardized_response = StandardResponse._create_response_data(
                status_code=ResponseStatus.NOT_FOUND.value,
                message=ResponseMessage.ERROR_NOT_FOUND,
                dev_message=str(flattened_response_data),
                meta={"errors": flattened_response_data}
            )
        elif response.status_code == 403:
            standardized_response = StandardResponse._create_response_data(
                status_code=ResponseStatus.PERMISSION_DENIED.value,
                message=ResponseMessage.ERROR_PERMISSION,
                dev_message=str(flattened_response_data),
                meta={"errors": flattened_response_data}
            )
        elif response.status_code == 400:
            if isinstance(flattened_response_data, dict):
                # Check for specific business validation errors
                if any(key in flattened_response_data for key in ['client_phone_1', 'client_phone_2', 'phone']):
                    standardized_response = StandardResponse._create_response_data(
                        status_code=ResponseStatus.VALIDATION_ERROR.value,
                        message=ResponseMessage.INVALID_PHONE,
                        dev_message="Phone number validation failed",
                        meta={"errors": flattened_response_data}
                    )
                elif 'stock' in str(flattened_response_data).lower():
                    standardized_response = StandardResponse._create_response_data(
                        status_code=ResponseStatus.INSUFFICIENT_STOCK.value,
                        message=ResponseMessage.INSUFFICIENT_STOCK,
                        dev_message="Stock validation failed",
                        meta={"errors": flattened_response_data}
                    )
                else:
                    standardized_response = StandardResponse._create_response_data(
                        status_code=ResponseStatus.VALIDATION_ERROR.value,
                        message=ResponseMessage.ERROR_VALIDATION,
                        dev_message="Validation errors occurred",
                        meta={"errors": flattened_response_data}
                    )
            else:
                standardized_response = StandardResponse._create_response_data(
                    status_code=ResponseStatus.ERROR.value,
                    message=ResponseMessage.ERROR_GENERAL,
                    dev_message=str(flattened_response_data),
                    meta={"errors": flattened_response_data}
                )
        elif response.status_code == 429:
            standardized_response = StandardResponse._create_response_data(
                status_code=ResponseStatus.RATE_LIMITED.value,
                message=ResponseMessage.ERROR_RATE_LIMIT,
                dev_message=str(flattened_response_data)
            )
        elif response.status_code == 402:
            standardized_response = StandardResponse._create_response_data(
                status_code=ResponseStatus.SUBSCRIPTION_EXPIRED.value,
                message=ResponseMessage.SUBSCRIPTION_EXPIRED,
                dev_message=str(flattened_response_data)
            )
        elif response.status_code == 409:
            standardized_response = StandardResponse._create_response_data(
                status_code=ResponseStatus.BOOKING_CONFLICT.value,
                message=ResponseMessage.BOOKING_CONFLICT,
                dev_message=str(flattened_response_data)
            )
        else:
            standardized_response = StandardResponse._create_response_data(
                status_code=ResponseStatus.ERROR.value,
                message=ResponseMessage.ERROR_GENERAL,
                dev_message=str(flattened_response_data),
                meta={"errors": flattened_response_data}
            )

        # Log the error with context
        logger.error(
            f"API Exception: {standardized_response.get('message')}",
            extra={
                "status_code": response.status_code,
                "response_status": standardized_response.get('status'),
                "request_method": request.method,
                "request_path": request.get_full_path(),
                "user_id": user_id,
                "user_name": user_display,
                "shop_id": shop_id,
                "shop_name": shop_display,
                "request_body": request_body,
                "request":request,
                "response_data": flattened_response_data,
                "processing_time_ms": processing_time,
                "dev_message": standardized_response.get('dev_message'),
                "client_ip": client_ip
            }
        )

        # Replace original response data with standardized format
        response.data = standardized_response

    return response

