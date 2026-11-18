from .response_utils import StandardResponse, ResponseMessage
from rest_framework.views import exception_handler
from rest_framework import status

class StandardResponseMixin:
    """Mixin to add standardized response methods to any view class"""
    
    def success_response(self, data=None, message=None, **kwargs):
        return StandardResponse.success(
            data=data, 
            message=message or ResponseMessage.SUCCESS_RETRIEVED,
            request=getattr(self, 'request', None),
            **kwargs
        )
    
    def error_response(self, message=None, **kwargs):
        return StandardResponse.error(
            message=message or ResponseMessage.ERROR_GENERAL,
            request=getattr(self, 'request', None),
            **kwargs
        )
    
    def validation_error_response(self, errors=None, **kwargs):
        return StandardResponse.validation_error(
            errors, 
            request=getattr(self, 'request', None),
            **kwargs
        )
    
    def not_found_response(self, resource=None,message=None, **kwargs):
        return StandardResponse.not_found(
            resource=resource, 
            message= message or ResponseMessage.ERROR_NOT_FOUND,
            request=getattr(self, 'request', None),
            **kwargs
        )
   
    # Business-specific response methods
    def booking_success_response(self, booking_data, booking_id=None, **kwargs):
        """Success response for booking operations"""
        return StandardResponse.success(
            data=booking_data,
            message=ResponseMessage.BOOKING_CREATED,
            request=getattr(self, 'request', None),
            meta={"booking_id": booking_id} if booking_id else None,
            **kwargs
        )
    
    def booking_not_found_response(self, booking_id=None, **kwargs):
        """Booking not found response"""
        return StandardResponse.not_found(
            message=ResponseMessage.BOOKING_NOT_FOUND,
            dev_message=f"Booking {booking_id} not found" if booking_id else None,
            request=getattr(self, 'request', None),
            **kwargs
        )
    
    def client_success_response(self, client_data, **kwargs):
        """Success response for client operations"""
        return StandardResponse.success(
            data=client_data,
            message=ResponseMessage.CLIENT_REGISTERED,
            request=getattr(self, 'request', None),
            **kwargs
        )
    
    def client_not_found_response(self, client_id=None, **kwargs):
        """Client not found response"""
        return StandardResponse.not_found(
            message=ResponseMessage.CLIENT_NOT_FOUND,
            dev_message=f"Client {client_id} not found" if client_id else None,
            request=getattr(self, 'request', None),
            **kwargs
        )
    
    def duplicate_client_response(self, phone_number=None, **kwargs):
        """Duplicate client registration response"""
        return StandardResponse.validation_error(
            errors={"client_phone_1": [ResponseMessage.DUPLICATE_CLIENT]},
            message=ResponseMessage.DUPLICATE_CLIENT,
            dev_message=f"Client with phone {phone_number} already exists" if phone_number else None,
            **kwargs
        )
    
    def invalid_phone_response(self, phone_number=None, **kwargs):
        """Invalid phone number response"""
        return StandardResponse.validation_error(
            errors={"phone": [ResponseMessage.INVALID_PHONE]},
            message=ResponseMessage.INVALID_PHONE,
            dev_message=f"Invalid phone format: {phone_number}" if phone_number else None,
            **kwargs
        )
    
    def insufficient_stock_response(self, product_name=None, available=None, requested=None, message=None, **kwargs):
        """Insufficient stock response"""
        return StandardResponse.insufficient_stock(
            product_name=product_name,
            available_stock=available,
            requested_quantity=requested,
            message=message or ResponseMessage.INSUFFICIENT_STOCK,
            request=getattr(self, 'request', None),
            **kwargs
        )
    
    def booking_conflict_response(self, conflict_details=None, **kwargs):
        """Booking conflict response"""
        return StandardResponse.booking_conflict(
            conflict_details=conflict_details,
            request=getattr(self, 'request', None),
            **kwargs
        )
    
    def subscription_expired_response(self, shop_name=None, expiry_date=None, **kwargs):
        """Subscription expired response"""
        return StandardResponse.subscription_expired(
            shop_name=shop_name,
            expiry_date=expiry_date,
            request=getattr(self, 'request', None),
            **kwargs
        )
    
    def payment_pending_response(self, booking_id=None, pending_amount=None, **kwargs):
        """Payment pending response"""
        return StandardResponse.payment_pending(
            booking_id=booking_id,
            pending_amount=pending_amount,
            request=getattr(self, 'request', None),
            **kwargs
        )
    
    def payment_success_response(self, payment_data, **kwargs):
        """Payment recorded successfully response"""
        return StandardResponse.success(
            data=payment_data,
            message=ResponseMessage.PAYMENT_RECORDED,
            request=getattr(self, 'request', None),
            **kwargs
        )
    
    def product_success_response(self, product_data, **kwargs):
        """Product operation success response"""
        return StandardResponse.success(
            data=product_data,
            message=ResponseMessage.PRODUCT_ADDED,
            request=getattr(self, 'request', None),
            **kwargs
        )
    
    def product_not_found_response(self, product_id=None, **kwargs):
        """Product not found response"""
        return StandardResponse.not_found(
            message=ResponseMessage.PRODUCT_NOT_FOUND,
            dev_message=f"Product {product_id} not found" if product_id else None,
            request=getattr(self, 'request', None),
            **kwargs
        )
    
    def shop_not_found_response(self, shop_id=None, **kwargs):
        """Shop not found response"""
        return StandardResponse.not_found(
            message=ResponseMessage.SHOP_NOT_FOUND,
            dev_message=f"Shop {shop_id} not found or inactive" if shop_id else None,
            request=getattr(self, 'request', None),
            **kwargs
        )
    
    def unauthorized_shop_response(self, shop_id=None, **kwargs):
        """User not authorized for shop response"""
        return StandardResponse.error(
            message=ResponseMessage.USER_NOT_AUTHORIZED,
            dev_message=f"User not authorized for shop {shop_id}" if shop_id else None,
            http_status=status.HTTP_403_FORBIDDEN,
            request=getattr(self, 'request', None),
            **kwargs
        )    

    def permission_denied_response(self, message=None, **kwargs):
        """Permission denied response"""
        return StandardResponse.permission_denied(
            message=message or ResponseMessage.ERROR_PERMISSION,
            http_status=status.HTTP_403_FORBIDDEN,
            request=getattr(self, 'request', None),
            **kwargs
        )    