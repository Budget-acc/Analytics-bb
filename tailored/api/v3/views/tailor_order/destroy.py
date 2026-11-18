from django.db import transaction
from django.db.models import Prefetch
from tailored.models import TailorOrder, TailorOrderItem, TailorMaterialUsage, CustomerPayment
from tailored.tasks import delete_order_images_from_s3
from safedelete.models import HARD_DELETE
from ERP.utils.shop_activity_logs import create_activity_log
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
import logging

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Delete tailor order",
    description="Hard deletes a tailor order. For non-completed orders it restores tracked fabric before deletion and performs best-effort ledger and image cleanup.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Tailor order id.")],
    responses={
        200: OpenApiResponse(description="Success response with cleanup summary."),
        400: OpenApiResponse(description="Exact error messages:\n- Invalid order ID provided.\n- Cannot delete order: Critical fabric restoration failures detected."),
        404: OpenApiResponse(description="Exact error message: Order not found."),
        500: OpenApiResponse(description="Exact error messages:\n- A critical error occurred during deletion. Please contact support.\n- Access to this shop has been deactivated."),
    },
    examples=[OpenApiExample("Delete success", value={"status": "success", "message": "Order TO-0008 deleted successfully.", "data": {"deleted_order_id": "TO-0008", "status_at_deletion": "initiated", "fabric_restoration": {"required": True, "materials_restored": 2, "restoration_failures": 0}, "total_items_deleted": 1}}, response_only=True, status_codes=["200"])],
)
def destroy(self, request, pk=None):
    """
    Optimized delete method with comprehensive edge case handling.

    Logic:
    - If status is 'completed': Direct hard delete (no fabric restoration)
    - If status is NOT 'completed': Restore fabric lengths before delete
    - Ledger cleanup is best-effort and never blocks deletion
    """

    # Validate input
    try:
        order_id = int(pk)
    except (ValueError, TypeError):
        return self.validation_error_response(
            message="Invalid order ID provided.",
            dev_message=f"pk is not a valid integer: {pk}"
        )

    try:
        with transaction.atomic():
            # Fetch and immediately lock the order row so no concurrent request
            # can modify or delete it between our fetch and the hard-delete below.
            try:
                order = TailorOrder.objects.select_for_update(of=('self',)).select_related(
                    'shop',
                    'client',
                    'created_by'
                ).prefetch_related(
                    Prefetch(
                        'items',
                        queryset=TailorOrderItem.objects.select_related(
                            'assigned_tailor'
                        ).prefetch_related(
                            Prefetch(
                                'material_usages',
                                queryset=TailorMaterialUsage.objects.select_related(
                                    'material',
                                    'material__product'
                                )
                            )
                        )
                    ),
                    'additional_charges',
                    'payments'
                ).get(
                    id=order_id,
                    shop_id=self.shop_id,
                    deleted__isnull=True
                )
            except TailorOrder.DoesNotExist:
                return self.not_found_response(
                    resource="Tailor Order",
                    message="Order not found.",
                    dev_message=f"Order {order_id} not found in shop {self.shop_id} or already deleted"
                )

            # Capture order details for response and logging
            order_details = {
                'shop_tailor_id': order.shop_tailor_id,
                'status': order.status,
                'total_items': order.items.count(),
                'shop_name': order.shop.name,
                'client_name': order.client.client_name if order.client else None,
                'created_by': order.created_by.phone if order.created_by else None
            }

            # PHASE 1: Fabric Restoration (for non-completed orders)
            needs_fabric_restoration = order.status != 'completed'
            fabric_restoration_summary = {'restored_count': 0, 'failed_count': 0, 'details': []}

            if needs_fabric_restoration:
                fabric_restoration_summary = self._execute_fabric_restoration(order)

                # If critical fabric restoration fails, abort deletion
                if fabric_restoration_summary['failed_count'] > 0:
                    critical_failures = [
                        detail for detail in fabric_restoration_summary['details']
                        if detail.get('critical_failure', False)
                    ]
                    if critical_failures:
                        return self.error_response(
                            message="Cannot delete order: Critical fabric restoration failures detected.",
                            dev_message=f"Critical failures: {critical_failures}"
                        )

            # Collect image URLs and ledger IDs before hard delete while relations exist.
            image_urls = [
                item.reference_image.strip()
                for item in order.items.all()
                if item.reference_image and item.reference_image.strip()
            ]

            payment_ids = list(CustomerPayment.objects.filter(
                order=order, account__isnull=False
            ).values_list('id', flat=True))
            has_security_payments = order.security_payments.filter(account__isnull=False).exists()

            # NEW: collect IDs for additional ledger sources before hard-delete removes relations
            from tailored.models import TailorOrderRefund, TailorOrderSecurityDepositAdjustment
            tailor_refund_ids = list(
                TailorOrderRefund.objects.filter(order=order)
                .values_list('id', flat=True)
            )
            tailor_security_adj_ids = list(
                TailorOrderSecurityDepositAdjustment.objects.filter(order=order)
                .values_list('id', flat=True)
            )

            # Delete order — primary action, must always succeed.
            order.delete(force_policy=HARD_DELETE)

        # Best-effort ledger cleanup — outside atomic so deletion is never blocked.
        try:
            from finance.api.v3.utils import delete_ledger_entries as _delete_ledger_entries
            if payment_ids:
                _delete_ledger_entries(reference_ids=payment_ids, reference_type='CustomerPayment')
            if has_security_payments:
                _delete_ledger_entries(reference_ids=[order_id], reference_type='TailorOrderSecurity')
            if tailor_refund_ids:                                                # NEW
                _delete_ledger_entries(reference_ids=tailor_refund_ids, reference_type='TailorOrderRefund')
            if tailor_security_adj_ids:                                         # NEW
                _delete_ledger_entries(reference_ids=tailor_security_adj_ids, reference_type='TailorOrderSecurityDepositAdjustment')
        except Exception as ledger_err:
            logger.warning(
                f"Ledger cleanup failed for tailor order {order_id} — orphaned entries may exist. Error: {ledger_err}"
            )

        if image_urls:
            try:
                delete_order_images_from_s3.delay(image_urls)
            except Exception as celery_error:
                logger.warning(
                    f"Celery unavailable — skipping async S3 cleanup. "
                    f"Error: {celery_error}. Image URLs: {image_urls}"
                )

        try:
            create_activity_log(
                shop=order.shop,
                user=request.user,
                action="deleted",
                instance=order,
                message=(
                    f"Deleted order {order_details['shop_tailor_id']} (status: {order_details['status']})."
                )
            )
        except Exception:
            logger.exception("Failed to create activity log for successful deletion")

        return self.success_response(
            data={
                "deleted_order_id": order_details['shop_tailor_id'],
                "status_at_deletion": order_details['status'],
                "fabric_restoration": {
                    "required": needs_fabric_restoration,
                    "materials_restored": fabric_restoration_summary['restored_count'],
                    "restoration_failures": fabric_restoration_summary['failed_count']
                },
                "total_items_deleted": order_details['total_items']
            },
            message=f"Order {order_details['shop_tailor_id']} deleted successfully.",
            dev_message=f"Hard deleted order with comprehensive cleanup. Status: {order_details['status']}"
        )

    except Exception as e:
        logger.error(
            f"Critical error during order deletion. Order ID: {order_id}, "
            f"Shop: {self.shop_id}, Error: {str(e)}",
            exc_info=True
        )
        return self.error_response(
            message="A critical error occurred during deletion. Please contact support.",
            dev_message=f"Deletion failed: {str(e)}"
        )
