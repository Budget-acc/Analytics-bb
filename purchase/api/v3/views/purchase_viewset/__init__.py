from .base import PurchaseViewSet
from .helpers import (
    _get_or_create_vendor,
    _apply_vendor_updates,
    _resolve_existing_product_item,
    _create_new_product_item,
    _update_existing_purchase_item,
    _reverse_purchase_item_stock,
    _parse_purchase_date,
    _parse_payment_date,
    _to_decimal,
    _validate_without_products_fields,
    _resolve_account,
    _collect_purchase_document_files,
    _upload_purchase_documents,
    _cleanup_purchase_documents_sync,
    _parse_multipart_to_json,
    _process_advance_payments,
)
from .create import create
from .retrieve import retrieve
from .list import list
from .update import partial_update
from .destroy import destroy
from .add_payment import add_payment
from .delete_payment import delete_payment
from .send_invoice import send_invoice
from .mark_completed import mark_completed

# ---- Assign helper methods ----
PurchaseViewSet._get_or_create_vendor = _get_or_create_vendor
PurchaseViewSet._apply_vendor_updates = _apply_vendor_updates
PurchaseViewSet._resolve_existing_product_item = _resolve_existing_product_item
PurchaseViewSet._create_new_product_item = _create_new_product_item
PurchaseViewSet._update_existing_purchase_item = _update_existing_purchase_item
PurchaseViewSet._reverse_purchase_item_stock = _reverse_purchase_item_stock
PurchaseViewSet._parse_purchase_date = _parse_purchase_date
PurchaseViewSet._parse_payment_date = _parse_payment_date
PurchaseViewSet._to_decimal = _to_decimal
PurchaseViewSet._validate_without_products_fields = _validate_without_products_fields
PurchaseViewSet._resolve_account = _resolve_account
PurchaseViewSet._collect_purchase_document_files = _collect_purchase_document_files
PurchaseViewSet._upload_purchase_documents = _upload_purchase_documents
PurchaseViewSet._cleanup_purchase_documents_sync = _cleanup_purchase_documents_sync
PurchaseViewSet._parse_multipart_to_json = _parse_multipart_to_json
PurchaseViewSet._process_advance_payments = _process_advance_payments

# ---- Assign public/router-recognized methods ----
PurchaseViewSet.create = create
PurchaseViewSet.retrieve = retrieve
PurchaseViewSet.list = list
PurchaseViewSet.partial_update = partial_update
PurchaseViewSet.destroy = destroy
PurchaseViewSet.add_payment = add_payment
PurchaseViewSet.delete_payment = delete_payment
PurchaseViewSet.send_invoice = send_invoice
PurchaseViewSet.mark_completed = mark_completed

__all__ = ["PurchaseViewSet"]
