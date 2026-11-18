from .base import BookingViewSet
from .helpers import (
    _get_or_create_client,
    _validate_client,
    _validate_dates,
    _validate_rental_duration_type,
    _validate_pickup_not_before_booking,
    _parse_and_validate_dates,
    _get_effective_dates,
    _validate_delivery_status,
    _validate_variants,
    _validate_variant_products,
    _get_variant,
    _process_variants,
    _need_availability_check,
    _handle_availability_check,
    _update_variants,
    _update_variant_amounts_only,
    _validate_amounts,
    _resolve_security_paid_flag,
    _resolve_security_paid_at,
    _sync_security_deposit_payment,
    _create_security_deposit_payment,
    _create_payment_records,
    _update_security_deposit_payment,
    _validate_payments,
    _validate_purchase_mode,
    _validate_staff,
    _create_booking_record,
    _create_additional_charges,
    _create_booked_items,
    _update_availability,
    _create_client_requirement,
    _handle_send_invoice,
    _validate_basic_fields,
    _update_client_requirement,
    _process_dates_and_variants,
    _update_additional_charges,
    _calculate_and_validate_totals,
    _update_other_fields,
    _build_success_response,
    _validate_and_upload_documents,
    _cleanup_s3_documents,
    _cleanup_s3_documents_sync,
    _update_booking_documents,
    _parse_multipart_to_json,
    _collect_document_files,
    _save_temp_documents,
    _cleanup_temp_files,
    _validate_documents_sync,
    _upload_documents_to_s3_sync,
)
from .create import create
from .retrieve import retrieve
from .list import list
from .desktop_list import desktop_list
from .update_delivery_status import update_delivery_status
from .add_payment import add_payment
from .update_booking_status import update_booking_status
from .update_booking_details import update_booking_details
from .send_invoice import send_invoice
from .delete_payment import delete_payment
from .variant_availability import variant_availability
from .add_refund import add_refund
from .delete_refund import delete_refund
from .add_security_adjustment import add_security_adjustment
from .delete_security_adjustment import delete_security_adjustment
from .delete_security_deposit_payment import delete_security_deposit_payment
from .partial_return import partial_return
from .destroy import destroy

# ---- Assign helper methods ----
BookingViewSet._get_or_create_client = _get_or_create_client
BookingViewSet._validate_client = _validate_client
BookingViewSet._validate_dates = _validate_dates
BookingViewSet._validate_rental_duration_type = _validate_rental_duration_type
BookingViewSet._validate_pickup_not_before_booking = _validate_pickup_not_before_booking
BookingViewSet._parse_and_validate_dates = _parse_and_validate_dates
BookingViewSet._get_effective_dates = _get_effective_dates
BookingViewSet._validate_delivery_status = _validate_delivery_status
BookingViewSet._validate_variants = _validate_variants
BookingViewSet._validate_variant_products = _validate_variant_products
BookingViewSet._get_variant = _get_variant
BookingViewSet._process_variants = _process_variants
BookingViewSet._need_availability_check = _need_availability_check
BookingViewSet._handle_availability_check = _handle_availability_check
BookingViewSet._update_variants = _update_variants
BookingViewSet._update_variant_amounts_only = _update_variant_amounts_only
BookingViewSet._validate_amounts = _validate_amounts
BookingViewSet._resolve_security_paid_flag = _resolve_security_paid_flag
BookingViewSet._resolve_security_paid_at = _resolve_security_paid_at
BookingViewSet._sync_security_deposit_payment = _sync_security_deposit_payment
BookingViewSet._create_security_deposit_payment = _create_security_deposit_payment
BookingViewSet._create_payment_records = _create_payment_records
BookingViewSet._update_security_deposit_payment = _update_security_deposit_payment
BookingViewSet._validate_payments = _validate_payments
BookingViewSet._validate_purchase_mode = _validate_purchase_mode
BookingViewSet._validate_staff = _validate_staff
BookingViewSet._create_booking_record = _create_booking_record
BookingViewSet._create_additional_charges = _create_additional_charges
BookingViewSet._create_booked_items = _create_booked_items
BookingViewSet._update_availability = _update_availability
BookingViewSet._create_client_requirement = _create_client_requirement
BookingViewSet._handle_send_invoice = _handle_send_invoice
BookingViewSet._validate_basic_fields = _validate_basic_fields
BookingViewSet._update_client_requirement = _update_client_requirement
BookingViewSet._process_dates_and_variants = _process_dates_and_variants
BookingViewSet._update_additional_charges = _update_additional_charges
BookingViewSet._calculate_and_validate_totals = _calculate_and_validate_totals
BookingViewSet._update_other_fields = _update_other_fields
BookingViewSet._build_success_response = _build_success_response
BookingViewSet._validate_and_upload_documents = _validate_and_upload_documents
BookingViewSet._cleanup_s3_documents = _cleanup_s3_documents
BookingViewSet._cleanup_s3_documents_sync = _cleanup_s3_documents_sync
BookingViewSet._update_booking_documents = _update_booking_documents
BookingViewSet._parse_multipart_to_json = _parse_multipart_to_json
BookingViewSet._collect_document_files = _collect_document_files
BookingViewSet._save_temp_documents = _save_temp_documents
BookingViewSet._cleanup_temp_files = _cleanup_temp_files
BookingViewSet._validate_documents_sync = _validate_documents_sync
BookingViewSet._upload_documents_to_s3_sync = _upload_documents_to_s3_sync

# ---- Assign public methods ----
BookingViewSet.create = create
BookingViewSet.retrieve = retrieve
BookingViewSet.list = list
BookingViewSet.desktop_list = desktop_list
BookingViewSet.update_delivery_status = update_delivery_status
BookingViewSet.add_payment = add_payment
BookingViewSet.update_booking_status = update_booking_status
BookingViewSet.update_booking_details = update_booking_details
BookingViewSet.send_invoice = send_invoice
BookingViewSet.delete_payment = delete_payment
BookingViewSet.variant_availability = variant_availability
BookingViewSet.add_refund = add_refund
BookingViewSet.delete_refund = delete_refund
BookingViewSet.add_security_adjustment = add_security_adjustment
BookingViewSet.delete_security_adjustment = delete_security_adjustment
BookingViewSet.delete_security_deposit_payment = delete_security_deposit_payment
BookingViewSet.partial_return = partial_return
BookingViewSet.destroy = destroy

__all__ = ["BookingViewSet"]
