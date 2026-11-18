import logging

from django.db import transaction
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from purchase.models import Purchase
from ERP.utils.shop_activity_logs import create_activity_log

logger = logging.getLogger("api_requests")


class _ItemDeletionBlocked(Exception):
    """Internal signal to force a rollback of the atomic block below when
    _reverse_purchase_item_stock rejects a booking-linked 'new' item -
    returning directly from inside `with transaction.atomic():` would
    commit whatever partial work happened earlier in the loop, since a
    plain `return` (no exception) is treated as success by Atomic.__exit__."""
    def __init__(self, error_response):
        self.error_response = error_response


@extend_schema(
    tags=["Purchase Management V3"],
    summary="Delete purchase",
    description=(
        "Soft-deletes a purchase and atomically cleans up everything "
        "attached to it: reverses any stock it added, deletes all "
        "PurchasePayment and AdditionalCharge rows, and deletes every "
        "ledger entry referencing them (plus any legacy header-level "
        "settlement entry). A completed purchase cannot be deleted - "
        "outstanding/paid balances do not block deletion, only "
        "is_completed does. Deleting an item whose product was created "
        "inline for this purchase (item_source='new') also soft-deletes "
        "that ProductVariant (and the Product itself if it was its last "
        "remaining variant) - blocked entirely, purchase included, if that "
        "product has ever been booked (active or historical). The entire "
        "operation (purchase, payments, charges, ledger entries, stock, "
        "product cleanup) is one atomic transaction - if any part fails, "
        "everything rolls back and nothing is left orphaned or partially "
        "deleted."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Purchase ID."),
    ],
    responses={
        200: OpenApiResponse(description="Exact message: Purchase deleted successfully."),
        400: OpenApiResponse(
            description=(
                "Exact messages include: Cannot delete a completed purchase.; "
                "Cannot delete purchase item because the associated newly created "
                "product has active or historical bookings."
            )
        ),
        404: OpenApiResponse(description="Exact message: Purchase not found."),
        500: OpenApiResponse(description="Exact message: Unable to delete purchase."),
    },
)
def destroy(self, request, pk=None):
    try:
        purchase = (
            Purchase.objects.filter(id=pk, shop_id=self.shop_id, deleted__isnull=True)
            .select_related("shop")
            .prefetch_related("items")
            .first()
        )
        if not purchase:
            return self.not_found_response(
                resource="Purchase",
                message="Purchase not found.",
            )

        if purchase.is_completed:
            return self.validation_error_response(
                errors={"is_completed": ["A completed purchase cannot be deleted."]},
                message="Cannot delete a completed purchase.",
            )

        # Collect ledger-relevant ids before any deletion, while relations
        # are still accessible.
        payment_ids = list(
            purchase.payments.filter(account__isnull=False).values_list("id", flat=True)
        )
        charge_ids = list(
            purchase.additional_charges.filter(account__isnull=False).values_list("id", flat=True)
        )
        items = list(purchase.items.all())
        shop = purchase.shop

        # Everything financial/structural is one atomic unit - purchase,
        # payments, additional charges, their ledger entries, and stock
        # reversal all succeed together or all roll back together.
        try:
            with transaction.atomic():
                for item in items:
                    success, error = self._reverse_purchase_item_stock(item, shop, reason="purchase deleted")
                    if error:
                        raise _ItemDeletionBlocked(error)

                from finance.api.v3.utils import delete_ledger_entries as _delete_ledger_entries
                if payment_ids:
                    _delete_ledger_entries(reference_ids=payment_ids, reference_type="PurchasePayment")
                if charge_ids:
                    _delete_ledger_entries(reference_ids=charge_ids, reference_type="AdditionalCharge")
                # Legacy: purchases created before the split-payment refactor
                # may still carry a header-level 'Purchase'-referenced
                # settlement entry (create.py no longer creates these, but
                # old rows may still exist) - harmless no-op otherwise.
                _delete_ledger_entries(reference_ids=[purchase.id], reference_type="Purchase")

                # Bulk delete - Purchase is being deleted in this same block,
                # so PurchasePayment/AdditionalCharge's own delete() side
                # effects (recalculating the parent's totals) would be
                # pointless work; bulk QuerySet.delete() skips them for free.
                purchase.payments.all().delete()
                purchase.additional_charges.all().delete()
                purchase.delete()
        except _ItemDeletionBlocked as e:
            return e.error_response

        try:
            create_activity_log(
                shop=shop,
                user=request.user,
                instance=purchase,
                action="deleted",
                message=f"Purchase {purchase.purchase_code} deleted.",
            )
        except Exception as log_err:
            logger.warning(f"Failed to create activity log for purchase delete: {log_err}")

        return self.success_response(message="Purchase deleted successfully.")

    except Exception as e:
        logger.error(f"Error deleting purchase {pk}: {e}", exc_info=True)
        return self.error_response(message="Unable to delete purchase.", dev_message=str(e))


__all__ = ["destroy"]
