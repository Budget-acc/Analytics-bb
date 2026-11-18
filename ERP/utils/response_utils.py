import logging
import time
from enum import Enum
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import exception_handler as drf_exception_handler
from django.utils.encoding import force_str
from typing import Any, Dict, Optional, Union
from .user_helpers import get_user_info, get_shop_info, get_client_ip

logger = logging.getLogger('api_requests')

class ResponseStatus(Enum):
    """Standardized response status codes"""
    SUCCESS = "success"
    FAILED = "failed" 
    ERROR = "error"
    PARTIAL_SUCCESS = "partial_success"
    NO_CONTENT = "no_content"
    VALIDATION_ERROR = "validation_error"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    MAINTENANCE = "maintenance"
    INSUFFICIENT_STOCK = "insufficient_stock"
    BOOKING_CONFLICT = "booking_conflict"
    PAYMENT_PENDING = "payment_pending"
    SUBSCRIPTION_EXPIRED = "subscription_expired"

class ResponseMessage:
    """Predefined messages for different scenarios"""
    
    # Success messages
    SUCCESS_RETRIEVED = "Data retrieved successfully"
    SUCCESS_CREATED = "Resource created successfully" 
    SUCCESS_UPDATED = "Resource updated successfully"
    SUCCESS_DELETED = "Resource deleted successfully"
    
    # Success messages - Business specific
    BOOKING_CREATED = "Booking created successfully"
    BOOKING_UPDATED = "Booking updated successfully"
    BOOKING_COMPLETED = "Booking marked as completed"
    PAYMENT_RECORDED = "Payment recorded successfully"
    CLIENT_REGISTERED = "Client registered successfully"
    PRODUCT_ADDED = "Product added to inventory"
    STOCK_UPDATED = "Stock updated successfully"
    TRANSFER_COMPLETED = "Product transfer completed successfully"
    SERVICE_ACTIVATED = "Service activated for shop"
    
    # Error messages - General
    ERROR_GENERAL = "An error occurred while processing your request"
    ERROR_VALIDATION = "Please check your input and try again"
    ERROR_NOT_FOUND = "The requested resource was not found"
    ERROR_PERMISSION = "You don't have permission to perform this action"
    ERROR_RATE_LIMIT = "Too many requests. Please try again later"
    ERROR_MAINTENANCE = "Service is temporarily unavailable"
    
    # Error messages - Business specific
    CLIENT_NOT_FOUND = "Client not found in your shop records"
    BOOKING_NOT_FOUND = "Booking not found"
    SALE_NOT_FOUND = "Sale record not found"
    PRODUCT_NOT_FOUND = "Product not found in your inventory"
    SHOP_NOT_FOUND = "Shop not found or inactive"
    USER_NOT_AUTHORIZED = "User not authorized for this shop"
    INSUFFICIENT_STOCK = "Insufficient stock available for this product"
    BOOKING_CONFLICT = "Booking conflict with existing reservation"
    PAYMENT_INCOMPLETE = "Payment is incomplete for this booking"
    SUBSCRIPTION_EXPIRED = "Shop subscription has expired"
    INVALID_PHONE = "Invalid phone number format"
    DUPLICATE_CLIENT = "Client with this phone number already exists"
    INVALID_DATE_RANGE = "Invalid date range specified"
    BOOKING_ALREADY_COMPLETED = "Booking is already marked as completed"
    TRANSFER_FAILED = "Product transfer failed"
    
    # Client-friendly messages - General
    CLIENT_SUCCESS = "Your request was completed successfully"
    CLIENT_ERROR = "We're sorry, something went wrong. Please try again"
    CLIENT_VALIDATION = "Please fill in all required fields correctly"
    CLIENT_NOT_FOUND = "The information you're looking for is not available"
    
    # Client-friendly messages - Business specific
    CLIENT_BOOKING_SUCCESS = "Your booking has been confirmed"
    CLIENT_BOOKING_UPDATED = "Your booking has been updated"
    CLIENT_PAYMENT_SUCCESS = "Your payment has been recorded"
    CLIENT_REGISTRATION_SUCCESS = "Welcome! Your profile has been created"
    CLIENT_BOOKING_NOT_FOUND = "We couldn't find your booking details"
    CLIENT_INSUFFICIENT_STOCK = "Sorry, this item is currently out of stock"
    CLIENT_BOOKING_CONFLICT = "This time slot is not available"
    CLIENT_PAYMENT_PENDING = "Your payment is still pending"
    CLIENT_SUBSCRIPTION_EXPIRED = "Service is temporarily unavailable"
    CLIENT_INVALID_PHONE = "Please enter a valid 10-digit phone number"
    CLIENT_DUPLICATE_REGISTRATION = "You are already registered with us"


class StandardResponse:
    """Utility class for creating standardized API responses with integrated logging"""
    
    @staticmethod
    def _log_response(request, response_data, http_status, log_level='info'):
        """Log the response with context information"""
        try:
            user_display, user_id = get_user_info(getattr(request, 'user', None))
            shop_display, shop_id = get_shop_info(request)
            
            # Get processing time if available
            processing_time = None
            if hasattr(request, '_start_time'):
                processing_time = round((time.time() - request._start_time) * 1000, 2)

            # Get request body if available
            request_body = getattr(request, '_logged_body', None)
            client_ip = get_client_ip(request) if request else "Unknown"


            log_data = {
                "status_code": http_status,
                "response_status": response_data.get('status'),
                "request_method": request.method,
                "request_path": request.get_full_path(),
                "user_id": user_id,
                "user_name": user_display,
                "shop_id": shop_id,
                "shop_name": shop_display,
                "request_body": request_body,
                "processing_time_ms": processing_time,
                "response_message": response_data.get('message'),
                "dev_message": response_data.get('dev_message'),  
                "client_ip": client_ip,           
            }

            if log_level == 'error':
                logger.error("API Error Response", extra=log_data)
            else:
                logger.info("API Response:", extra=log_data)
                
        except Exception as e:
            logger.exception("Error logging response: %s", str(e))
    
    @staticmethod
    def success(
        data: Any = None,
        message: str = ResponseMessage.SUCCESS_RETRIEVED,
        dev_message: str = None,
        http_status: int = status.HTTP_200_OK,
        meta: Dict = None,
        request = None
    ) -> Response:
        """Create a success response with logging"""
        response_data = StandardResponse._create_response_data(
            status_code=ResponseStatus.SUCCESS.value,
            data=data,
            message=message,
            dev_message=dev_message,
            meta=meta
        )
        
        if request:
            StandardResponse._log_response(request, response_data, http_status, 'info')
        
        return Response(response_data, status=http_status)
    
    @staticmethod
    def error(
        message: str = ResponseMessage.ERROR_GENERAL,
        dev_message: str = None,
        data: Any = None,
        http_status: int = status.HTTP_400_BAD_REQUEST,
        error_code: str = None,
        meta: Dict = None,
        request = None
    ) -> Response:
        """Create an error response with logging"""
        response_data = StandardResponse._create_response_data(
            status_code=ResponseStatus.ERROR.value,
            data=data,
            message=message,
            dev_message=dev_message,
            error_code=error_code,
            meta=meta
        )
        
        if request:
            StandardResponse._log_response(request, response_data, http_status, 'error')
        
        return Response(response_data, status=http_status)
    
    @staticmethod
    def validation_error(
        errors: Dict,
        message: str = ResponseMessage.ERROR_VALIDATION,
        dev_message: str = None,
        request = None
    ) -> Response:
        """Create a validation error response with logging"""
        response_data = StandardResponse._create_response_data(
            status_code=ResponseStatus.VALIDATION_ERROR.value,
            data={"validation_errors": errors},
            message=message or ResponseMessage.ERROR_VALIDATION,
            dev_message=dev_message
        )
        
        if request:
            StandardResponse._log_response(request, response_data, status.HTTP_400_BAD_REQUEST, 'error')
        
        return Response(response_data, status=status.HTTP_400_BAD_REQUEST)
        
    @staticmethod
    def no_content(
        message: str = "No content available",
        request = None
    ) -> Response:
        """Create a no content response with logging"""
        response_data = StandardResponse._create_response_data(
            status_code=ResponseStatus.NO_CONTENT.value,
            message=message,
        )
        
        if request:
            StandardResponse._log_response(request, response_data, status.HTTP_204_NO_CONTENT, 'info')
        
        return Response(response_data, status=status.HTTP_204_NO_CONTENT)
    
    @staticmethod
    def not_found(
        message: str = ResponseMessage.ERROR_NOT_FOUND,
        dev_message: str = None,
        resource: str = None,
        request = None
    ) -> Response:
        """Create a not found response with logging"""
        dev_msg = dev_message or (f"Resource '{resource}' not found" if resource else None)
        response_data = StandardResponse._create_response_data(
            status_code=ResponseStatus.NOT_FOUND.value,
            message=message,
            dev_message=dev_msg
        )
        
        if request:
            StandardResponse._log_response(request, response_data, status.HTTP_404_NOT_FOUND, 'error')
        
        return Response(response_data, status=status.HTTP_404_NOT_FOUND)
    
    @staticmethod
    def insufficient_stock(
        product_name: str = None,
        available_stock: int = None,
        requested_quantity: int = None,
        message: str = ResponseMessage.INSUFFICIENT_STOCK,
        request = None
    ) -> Response:
        """Create an insufficient stock response"""
        data = {}
        if product_name:
            data["product_name"] = product_name
        if available_stock is not None:
            data["available_stock"] = available_stock
        if requested_quantity is not None:
            data["requested_quantity"] = requested_quantity
            
        response_data = StandardResponse._create_response_data(
            status_code=ResponseStatus.INSUFFICIENT_STOCK.value,
            message=message,
            data=data,
            dev_message=f"Stock check failed: {requested_quantity} requested, {available_stock} available for {product_name}"
        )
        
        if request:
            StandardResponse._log_response(request, response_data, status.HTTP_400_BAD_REQUEST, 'error')
        
        return Response(response_data, status=status.HTTP_400_BAD_REQUEST)
    
    @staticmethod
    def booking_conflict(
        conflict_details: Dict = None,
        message: str = ResponseMessage.BOOKING_CONFLICT,
        request = None
    ) -> Response:
        """Create a booking conflict response"""
        response_data = StandardResponse._create_response_data(
            status_code=ResponseStatus.BOOKING_CONFLICT.value,
            message=message,
            data=conflict_details or {},
            dev_message="Booking time slot conflict detected"
        )
        
        if request:
            StandardResponse._log_response(request, response_data, status.HTTP_409_CONFLICT, 'error')
        
        return Response(response_data, status=status.HTTP_409_CONFLICT)
    
    @staticmethod
    def subscription_expired(
        shop_name: str = None,
        expiry_date: str = None,
        message: str = ResponseMessage.SUBSCRIPTION_EXPIRED,
        request = None
    ) -> Response:
        """Create a subscription expired response"""
        data = {}
        if shop_name:
            data["shop_name"] = shop_name
        if expiry_date:
            data["expiry_date"] = expiry_date
            
        response_data = StandardResponse._create_response_data(
            status_code=ResponseStatus.SUBSCRIPTION_EXPIRED.value,
            message=message,
            data=data,
            dev_message=f"Subscription expired for shop: {shop_name} on {expiry_date}"
        )
        
        if request:
            StandardResponse._log_response(request, response_data, status.HTTP_402_PAYMENT_REQUIRED, 'error')
        
        return Response(response_data, status=status.HTTP_402_PAYMENT_REQUIRED)
    
    @staticmethod
    def payment_pending(
        booking_id: str = None,
        pending_amount: int = None,
        message: str = ResponseMessage.PAYMENT_INCOMPLETE,
        request = None
    ) -> Response:
        """Create a payment pending response"""
        data = {}
        if booking_id:
            data["booking_id"] = booking_id
        if pending_amount is not None:
            data["pending_amount"] = pending_amount
            
        response_data = StandardResponse._create_response_data(
            status_code=ResponseStatus.PAYMENT_PENDING.value,
            message=message,
            data=data,
            dev_message=f"Payment pending for booking {booking_id}: ₹{pending_amount}"
        )
        
        if request:
            StandardResponse._log_response(request, response_data, status.HTTP_402_PAYMENT_REQUIRED, 'error')
        
        return Response(response_data, status=status.HTTP_402_PAYMENT_REQUIRED)
    
    @staticmethod
    def _create_response_data(
        status_code: str,
        message: str,
        data: Any = None,
        dev_message: str = None,
        error_code: str = None,
        meta: Dict = None
    ) -> Dict:
        """Internal method to create standardized response structure"""
        
        response_data = {
            "status": status_code,
            "message": message,
        }
        
        if status_code == ResponseStatus.SUCCESS.value:
            response_data["data"] = data if data is not None else {}
        else:
            # Explicitly set data as null in case of error
            response_data["data"] = None
            
        # Add developer message if provided
        if dev_message:
            response_data["dev_message"] = dev_message
        
        # Add error code if provided
        if error_code:
            response_data["error_code"] = error_code
        
        # Add metadata if provided
        if meta:
            response_data["meta"] = meta
        
        return response_data

    @staticmethod
    def permission_denied(
        message: str = ResponseMessage.ERROR_PERMISSION,
        dev_message: str = None,
        data: Any = None,
        http_status: int = status.HTTP_403_FORBIDDEN,
        error_code: str = None,
        meta: Dict = None,
        request = None
    ) -> Response:
        """Create a permission denied response"""
        response_data = StandardResponse._create_response_data(
            status_code=ResponseStatus.PERMISSION_DENIED.value,
            message=message,
            dev_message=dev_message,
            data=data,
            error_code=error_code,
            meta=meta
        )

        if request:
            StandardResponse._log_response(request, response_data, http_status, 'error')
        
        return Response(response_data, status=http_status)
