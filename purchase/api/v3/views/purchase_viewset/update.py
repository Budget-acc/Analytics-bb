import logging

from django.db import transaction
from django.db.models import Sum
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

from purchase.models import Purchase, AdditionalCharge
from ERP.utils.shop_activity_logs import create_activity_log
from finance.api.v3.utils import (
    create_ledger_entries as _create_ledger_entries,
    delete_ledger_entries as _delete_ledger_entries,
)

from .helpers.products import MAX_PRODUCTS_PER_PURCHASE
from .helpers.documents import MAX_PURCHASE_DOCUMENTS

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Purchase Management V3"],
    summary="Update purchase",
    description=(
        "Updates a purchase - mirrors create()'s field coverage, adapted for "
        "partial edits. purchase_code and purchase_type are immutable.\n\n"
        "- **Header**: invoice_number, notes, purchase_date "
        "(same DD-MM-YYYY/DD-MM-YYYYTHH:MM:SS parsing as create()). "
        "is_completed is NOT editable here - use the dedicated mark-completed "
        "action, which validates the purchase is fully paid first. Vendor: "
        "vendor_id (switch to a different existing vendor, no field "
        "updates) or vendor{} (matched by name - same get-or-create-or-"
        "update logic as create(): a name match is reused and updated with "
        "any differing provided fields, otherwise a new vendor is created). "
        "Both vendor fields are optional here - omit both to leave the "
        "vendor unchanged.\n"
        "- **Totals** (purchase_total, total_quantity, tax_rate, tax_amount): "
        "each editable independently on any purchase_type. purchase_total is "
        "always taken directly from the request body when provided (validated "
        "as a non-negative number) and is never derived from or affected by "
        "item changes. total_quantity, however, is still overwritten by the "
        "items-derived total whenever items are added/edited/deleted in the "
        "same request - items remain the source of truth for quantity when "
        "present.\n"
        "- **Items**: new_existing_products/new_new_products (add, same "
        "flat/multi-variant shapes as create()), delete_item_ids (remove, "
        "reverses stock), update_existing_items (true in-place edit of an "
        "already-added item's quantity/unit_purchase_price/increment_stock/"
        "update_product_cost - adjusts stock by the delta only, does not "
        "repoint product_variant/line_group/item_source).\n"
        "- **Additional charges**: new_additional_charges (add), "
        "delete_additional_charge_ids (remove, reverses ledger entry), "
        "update_additional_charges (in-place edit of charge_name/amount/"
        "account_id - deletes and recreates its ledger entry against the "
        "same reference_id so DailyBalance stays correct for both the old "
        "and new account/date).\n"
        "- **Documents**: delete_documents (list of existing URLs to "
        "remove - deletes both the URL from documents and the underlying "
        "file from S3) plus new files via multipart/form-data, capped at "
        "3 total combined with whatever remains after deletions.\n\n"
        "paid_amount/advance payments are intentionally not editable here - "
        "use the dedicated add-payment/delete-payment actions."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Purchase ID."),
    ],
    request=inline_serializer(
        name="PurchaseUpdateV3Request",
        fields={
            "invoice_number": serializers.CharField(required=False),
            "notes": serializers.CharField(required=False),
            "vendor_id": serializers.IntegerField(required=False),
            "vendor": serializers.DictField(required=False),
            "purchase_date": serializers.CharField(required=False),
            "purchase_total": serializers.DecimalField(max_digits=12, decimal_places=2, required=False),
            "total_quantity": serializers.IntegerField(required=False),
            "tax_rate": serializers.DecimalField(max_digits=5, decimal_places=2, required=False),
            "tax_amount": serializers.DecimalField(max_digits=12, decimal_places=2, required=False),
            "new_existing_products": serializers.ListField(child=serializers.DictField(), required=False),
            "new_new_products": serializers.ListField(child=serializers.DictField(), required=False),
            "update_existing_items": serializers.ListField(
                child=serializers.DictField(), required=False,
                help_text="[{item_id, quantity?, unit_purchase_price?, increment_stock?, update_product_cost?}, ...]",
            ),
            "delete_item_ids": serializers.ListField(child=serializers.IntegerField(), required=False),
            "new_additional_charges": serializers.ListField(child=serializers.DictField(), required=False),
            "update_additional_charges": serializers.ListField(
                child=serializers.DictField(), required=False,
                help_text="[{id, charge_name?, amount?, account_id?}, ...]",
            ),
            "delete_additional_charge_ids": serializers.ListField(child=serializers.IntegerField(), required=False),
            "delete_documents": serializers.ListField(child=serializers.CharField(), required=False),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Purchase updated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation errors. Exact messages include:\n"
                "- Vendor not found.\n"
                "- Invalid purchase date format.\n"
                "- Purchase date cannot be a future date.\n"
                "- Invalid purchase total.\n"
                "- Invalid total quantity.\n"
                "- Invalid tax rate.\n"
                "- Invalid tax amount.\n"
                "- A maximum of 20 products are allowed per purchase.\n"
                "- Product not found.\n"
                "- Invalid quantity.\n"
                "- Invalid purchase price.\n"
                "- Product has no variants.\n"
                "- variant_id is required for this product.\n"
                "- Variant not found for this product.\n"
                "- variant_id is required for each variant entry.\n"
                "- Invalid variant quantity.\n"
                "- Variant {id} not found for this product.\n"
                "- Product name is required.\n"
                "- Shop service is required for new products.\n"
                "- Shop service not found.\n"
                "- Purchase item not found for this purchase.\n"
                "- Cannot delete purchase item because the associated newly created product has active or historical bookings.\n"
                "- One or more additional charge ids do not belong to this purchase.\n"
                "- Additional charge not found for this purchase.\n"
                "- charge_name and a positive amount are required.\n"
                "- Invalid charge amount.\n"
                "- Invalid account selected.\n"
                "- Cannot exceed 3 attachments per purchase.\n"
                "- Invalid document file type.\n"
                "- Invalid document files."
            )
        ),
        404: OpenApiResponse(description="Exact message: Purchase not found."),
        500: OpenApiResponse(
            description=(
                "Exact messages include:\n"
                "- Something went wrong while updating the purchase. (generic/unexpected failure)\n"
                "- Failed to create new product.\n"
                "- Failed to upload document files. Please try again."
            )
        ),
    },
)
@transaction.atomic
def partial_update(self, request, pk=None):
    savepoint = transaction.savepoint()
    try:
        purchase = Purchase.objects.select_for_update().filter(
            id=pk, shop_id=self.shop_id, deleted__isnull=True
        ).first()
        if not purchase:
            transaction.savepoint_rollback(savepoint)
            return self.not_found_response(resource="Purchase", message="Purchase not found.")

        shop = purchase.shop

        content_type = request.content_type or ""
        if "multipart/form-data" in content_type:
            data = self._parse_multipart_to_json(request.data)
        else:
            data = dict(request.data)

        # is_completed is intentionally not editable here - it can only be
        # set via the dedicated mark-completed action, which enforces the
        # purchase is fully paid first. Allowing it here would bypass that
        # validation and the invariant delete_payment.py already relies on
        # (payments can't be deleted from a completed purchase).
        header_fields = {}
        for field in ("invoice_number", "notes"):
            if field in data:
                header_fields[field] = data.get(field)
        for key, value in header_fields.items():
            setattr(purchase, key, value)

        # Vendor - same get-or-create-or-update logic as create(), optional
        # here (a partial update need not touch the vendor at all).
        if "vendor_id" in data or "vendor" in data:
            vendor, error = self._get_or_create_vendor(data, shop, user=request.user)
            if error:
                transaction.savepoint_rollback(savepoint)
                return error
            purchase.vendor = vendor

        # purchase_date - must resolve before any ledger entries are
        # created further down, since those stamp created_at=purchase.purchase_date.
        if "purchase_date" in data:
            purchase_date, error = self._parse_purchase_date(data)
            if error:
                transaction.savepoint_rollback(savepoint)
                return error
            purchase.purchase_date = purchase_date

        # purchase_total/total_quantity/tax_rate/tax_amount - independently-
        # optional edits, allowed on any purchase_type. purchase_total is
        # always taken directly from the request body and saved as-is - it
        # is never recomputed from item totals, even for a with_products
        # purchase. total_quantity, in contrast, is still overwritten below
        # by the items-aggregate recompute whenever items are actually
        # touched in this same request - only total_quantity stays item-derived.
        if "purchase_total" in data:
            value, error = self._to_decimal(data.get("purchase_total"), "purchase_total")
            if error:
                transaction.savepoint_rollback(savepoint)
                return error
            purchase.purchase_total = value
        if "total_quantity" in data:
            try:
                total_quantity = int(data.get("total_quantity"))
                if total_quantity <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"total_quantity": ["total_quantity must be a positive integer."]},
                    message="Invalid total quantity.",
                )
            purchase.total_quantity = total_quantity
        if "tax_rate" in data:
            value, error = self._to_decimal(data.get("tax_rate"), "tax_rate")
            if error:
                transaction.savepoint_rollback(savepoint)
                return error
            purchase.tax_rate = value
        if "tax_amount" in data:
            value, error = self._to_decimal(data.get("tax_amount"), "tax_amount")
            if error:
                transaction.savepoint_rollback(savepoint)
                return error
            purchase.tax_amount = value

        new_existing = data.get("new_existing_products") or []
        new_new = data.get("new_new_products") or []
        update_existing_items = data.get("update_existing_items") or []
        delete_item_ids = data.get("delete_item_ids") or []

        current_item_count = purchase.items.count() - len(delete_item_ids)
        if current_item_count + len(new_existing) + len(new_new) > MAX_PRODUCTS_PER_PURCHASE:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"products": [f"A maximum of {MAX_PRODUCTS_PER_PURCHASE} products are allowed per purchase."]},
                message=f"A maximum of {MAX_PRODUCTS_PER_PURCHASE} products are allowed per purchase.",
            )

        if delete_item_ids:
            items_to_delete = list(purchase.items.filter(id__in=delete_item_ids))
            for item in items_to_delete:
                success, error = self._reverse_purchase_item_stock(item, shop, reason="removed via update")
                if error:
                    transaction.savepoint_rollback(savepoint)
                    return error
                item.delete()

        for item_data in update_existing_items:
            updated_item, error = self._update_existing_purchase_item(purchase, shop, item_data)
            if error:
                transaction.savepoint_rollback(savepoint)
                return error

        for item_data in new_existing:
            purchase_items, error = self._resolve_existing_product_item(purchase, shop, item_data)
            if error:
                transaction.savepoint_rollback(savepoint)
                return error
        for item_data in new_new:
            purchase_items, error = self._create_new_product_item(purchase, shop, item_data)
            if error:
                transaction.savepoint_rollback(savepoint)
                return error

        if new_existing or new_new or delete_item_ids or update_existing_items:
            # purchase_total is intentionally NOT recomputed here - it is
            # always taken directly from the request body (see the
            # "purchase_total" in data block above) and must never be
            # derived from item totals. Only total_quantity stays item-derived.
            totals = purchase.items.aggregate(qty=Sum("quantity"))
            purchase.total_quantity = totals["qty"] or 0

        delete_charge_ids = data.get("delete_additional_charge_ids") or []
        if delete_charge_ids:
            charges_to_delete = list(purchase.additional_charges.filter(id__in=delete_charge_ids))
            if len(charges_to_delete) != len(set(delete_charge_ids)):
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"delete_additional_charge_ids": ["One or more ids do not belong to this purchase."]},
                    message="One or more additional charge ids do not belong to this purchase.",
                )
            from finance.api.v3.utils import update_daily_balance as _update_daily_balance
            for charge in charges_to_delete:
                if charge.account_id:
                    _delete_ledger_entries(reference_ids=[charge.id], reference_type="AdditionalCharge")
                    _update_daily_balance(shop=shop.id, account=charge.account_id, date=charge.created_at.date())
                charge.delete()

        update_charges = data.get("update_additional_charges") or []
        for charge_data in update_charges:
            charge = purchase.additional_charges.filter(id=charge_data.get("id")).first()
            if not charge:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"update_additional_charges": ["Additional charge not found for this purchase."]},
                    message="Additional charge not found for this purchase.",
                )
            if "charge_name" in charge_data:
                new_charge_name = (charge_data.get("charge_name") or "").strip()
                if not new_charge_name:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"update_additional_charges": ["charge_name and a positive amount are required."]},
                        message="Invalid additional charge.",
                    )
                charge.charge_name = new_charge_name

            new_amount = charge.amount
            if "amount" in charge_data:
                new_amount, error = self._to_decimal(charge_data.get("amount"), "amount")
                if error or new_amount <= 0:
                    transaction.savepoint_rollback(savepoint)
                    return self.validation_error_response(
                        errors={"amount": ["amount must be a positive number."]},
                        message="Invalid charge amount.",
                    )

            new_account, new_payment_method = charge.account, charge.payment_method
            if "account_id" in charge_data:
                new_account, new_payment_method, error = self._resolve_account(shop, charge_data.get("account_id"))
                if error:
                    transaction.savepoint_rollback(savepoint)
                    return error

            old_account_id = charge.account_id
            charge.amount = new_amount
            charge.account = new_account
            charge.payment_method = new_payment_method
            charge.save()  # recalculates additional_charges_total via its own save() hook

            # No "edit a ledger entry in place" utility exists anywhere in
            # this codebase - delete the old entry, create a new one for
            # the same reference_id (the charge's id never changes). Both
            # utilities recalculate DailyBalance internally for their
            # respective account/date, so an account change is handled
            # correctly too (old account's day decreases, new account's
            # day increases).
            if old_account_id:
                _delete_ledger_entries(reference_ids=[charge.id], reference_type="AdditionalCharge")
            if new_account:
                _create_ledger_entries(
                    payments=[{
                        "amount": new_amount, "direction": "out", "account": new_account,
                        "payment_method": new_payment_method, "reference_id": charge.id,
                        "created_at": purchase.purchase_date,
                    }],
                    shop=shop, transaction_type="purchase_additional_charge", reference_type="AdditionalCharge",
                )

        new_charges = data.get("new_additional_charges") or []
        charge_ledger_payments = []
        for charge_data in new_charges:
            charge_name = (charge_data.get("charge_name") or "").strip()
            amount, error = self._to_decimal(charge_data.get("amount"), "amount")
            if not charge_name or error or amount <= 0:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"new_additional_charges": ["charge_name and a positive amount are required."]},
                    message="Invalid additional charge.",
                )
            account, payment_method, error = self._resolve_account(shop, charge_data.get("account_id"))
            if error:
                transaction.savepoint_rollback(savepoint)
                return error
            charge = AdditionalCharge.objects.create(
                purchase=purchase, charge_name=charge_name, amount=amount,
                account=account, payment_method=payment_method,
            )
            charge_ledger_payments.append({
                "amount": amount, "direction": "out", "account": account,
                "payment_method": payment_method, "reference_id": charge.id,
                "created_at": purchase.purchase_date,
            })
        if charge_ledger_payments:
            _create_ledger_entries(
                payments=charge_ledger_payments, shop=shop,
                transaction_type="purchase_additional_charge", reference_type="AdditionalCharge",
            )

        if delete_charge_ids or new_charges or update_charges:
            # AdditionalCharge.save()/.delete() update additional_charges_total
            # via a raw queryset .update() that bypasses this in-memory
            # purchase object - refresh before the unconditional save()
            # below, or it would overwrite the DB's correct value with a
            # stale one and recompute grand_total incorrectly.
            purchase.refresh_from_db(fields=["additional_charges_total"])

        # Documents - delete_documents removes existing URLs (and their S3
        # objects, post-commit below), new files (multipart) are added,
        # capped at MAX_PURCHASE_DOCUMENTS combined.
        delete_documents = set(data.get("delete_documents") or [])
        # Only URLs that were actually on this purchase - guards against
        # deleting an S3 object for a typo'd/foreign URL sent by mistake.
        documents_to_delete_from_s3 = [d for d in (purchase.documents or []) if d in delete_documents]
        remaining_documents = [d for d in (purchase.documents or []) if d not in delete_documents]
        document_files = self._collect_purchase_document_files(request) if request.FILES else []
        if document_files or delete_documents:
            if len(remaining_documents) + len(document_files) > MAX_PURCHASE_DOCUMENTS:
                transaction.savepoint_rollback(savepoint)
                return self.validation_error_response(
                    errors={"documents": [f"Maximum {MAX_PURCHASE_DOCUMENTS} attachments allowed."]},
                    message=f"Cannot exceed {MAX_PURCHASE_DOCUMENTS} attachments per purchase.",
                )
            new_urls = []
            if document_files:
                new_urls, error = self._upload_purchase_documents(document_files, shop, request.user, savepoint)
                if error:
                    return error
            purchase.documents = remaining_documents + new_urls

        # Nested atomic() so any DB-level failure on this final save (e.g. a
        # constraint violation) is recoverable without poisoning the outer
        # transaction - same fix as create.py's Purchase.objects.create()
        # and helpers/vendor.py's _get_or_create_vendor: a bare try/except
        # around a raw DB error here would leave the connection's
        # needs_rollback flag stuck even after the except block's
        # savepoint_rollback below, breaking every later query in the
        # request (TransactionManagementError).
        with transaction.atomic():
            purchase.save()
        transaction.savepoint_commit(savepoint)

        # S3 deletion isn't part of the DB transaction and can't be rolled
        # back by it - only run this after the commit succeeds, so a
        # later-in-request failure (which would restore purchase.documents
        # via rollback) can never leave a DB reference pointing at an
        # already-deleted S3 object.
        if documents_to_delete_from_s3:
            try:
                self._cleanup_purchase_documents_sync(documents_to_delete_from_s3)
            except Exception as cleanup_err:
                logger.warning(f"S3 cleanup failed for purchase {purchase.id} document deletions: {cleanup_err}")

        try:
            create_activity_log(
                shop=shop, user=request.user, instance=purchase,
                action="updated", message=f"Purchase {purchase.purchase_code} updated.",
            )
        except Exception as log_error:
            logger.exception(f"Failed to create activity log for purchase update: {log_error}")

        return self.success_response(
            data={
                "purchase_id": purchase.id,
                "grand_total": purchase.grand_total,
                "paid_amount": purchase.paid_amount,
                "outstanding_balance": purchase.outstanding_balance,
            },
            message="Purchase updated successfully.",
        )

    except Exception as e:
        transaction.savepoint_rollback(savepoint)
        logger.error(f"Purchase update error for id {pk}: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while updating the purchase.",
            dev_message=str(e),
        )


__all__ = ["partial_update"]
