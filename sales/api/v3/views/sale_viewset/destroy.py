import logging

from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from ERP.utils.shop_activity_logs import create_activity_log
from sales.models import Sale

logger = logging.getLogger('api_requests')


class SaleDestroyMixin:

    @extend_schema(
        tags=["Sales Management V3"],
        summary="Delete sale",
        description="Deletes a sale record and restores stock for sale items. Attempts related ledger cleanup for payment records.",
        parameters=[
            OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Sale id."),
        ],
        responses={
            200: OpenApiResponse(description="Exact message: Sale deleted successfully."),
            404: OpenApiResponse(description="Exact message: The requested resource was not found"),
            500: OpenApiResponse(description="Exact message: Unable to delete sale."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="DELETE /api/v3/sales/sales/120/",
                value={"pk": 120},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={"status": "success", "message": "Sale deleted successfully.", "data": {}},
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Not found response",
                value={"status": "not_found", "message": "The requested resource was not found", "data": None},
                response_only=True,
                status_codes=["404"],
            ),
            OpenApiExample(
                "Server error response",
                value={"status": "error", "message": "Unable to delete sale.", "data": None},
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    @transaction.atomic
    def destroy(self, request, pk=None):
        """Delete a sale"""
        try:
            shop_id = self.shop_id
            sale = Sale.objects.filter(shop_id=shop_id, id=pk).first()
            if not sale:
                return self.not_found_response("Sale not found.")

            payment_ids = list(
                sale.payments.filter(account__isnull=False).values_list("id", flat=True)
            )

            sale_shop = sale.shop
            sale_shop_sale_id = sale.shop_sale_id

            # Restore stock for all items
            for item in sale.items.all():
                variant = item.product_variant
                variant.stock += item.quantity
                variant.save()

            sale.delete()

            if payment_ids:
                try:
                    from finance.api.v3.utils import delete_ledger_entries as _delete_ledger_entries
                    _delete_ledger_entries(reference_ids=payment_ids, reference_type='SalesPaymentDetails')
                except Exception as ledger_err:
                    logger.warning(
                        f"Ledger cleanup failed for sale {pk} — orphaned entries may exist. Error: {ledger_err}"
                    )

            try:
                create_activity_log(
                    shop=sale_shop,
                    user=request.user,
                    instance=sale,
                    action="deleted",
                    message=f"Sale #{sale_shop_sale_id} deleted."
                )
            except Exception as log_err:
                logger.warning(f"Failed to create activity log for sale delete: {log_err}")

            return self.success_response(message="Sale deleted successfully.")

        except Exception as e:
            logger.error(f"Error deleting sale {pk} for shop {shop_id}: {e}", exc_info=True)
            return self.error_response("Unable to delete sale.")
