import logging
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from booking_v2.models import Client
from pdf_service.models import PDFTask
from pdf_service.tasks import generate_client_details_pdf_task, compute_request_hash
from shop.api.admin_v1.utils.shop import get_active_shop_id
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

from .throttles import PDFGenerationThrottle

logger = logging.getLogger("api_requests")


class ExportClientDetailsPDFView(StandardResponseMixin, APIView):
    """Initiates async PDF generation for the active shop's client list."""
    authentication_classes = [CutoffJWTAuthentication]
    throttle_classes = [PDFGenerationThrottle]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]

    @extend_schema(
        tags=["PDF Management V3"],
        summary="Initiate client details PDF export",
        description=(
            "Creates an asynchronous PDF generation task listing every client belonging to "
            "the active shop, in a 5-column table: S.No., Date (client created, dd/mm/yyyy), "
            "Name, Phone Number 1, Phone Number 2 - newest client first.\n\n"
            "Purpose: gives shop owners/staff a printable/shareable record of their client "
            "book, in the same shop-header styling used by the Ledger, Transactions and "
            "Account History PDF reports.\n\n"
            "Context: called from the Clients screen when the user requests a PDF export. "
            "No date range or filters apply - this always exports the shop's current, "
            "non-deleted client list at generation time.\n\n"
            "No request parameters are required beyond the active shop context (resolved "
            "the same way as every other PDF export endpoint). If an equivalent pending/"
            "processing task for this shop already exists (within the last 15 minutes), "
            "that task is reused instead of starting a duplicate generation."
        ),
        responses={
            200: OpenApiResponse(description="Task initiation response or duplicate-task reuse response."),
            500: OpenApiResponse(description="Exact message: Something went wrong while initiating PDF generation."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="GET /api/v3/pdf/clients/export/pdf/",
                value={},
                request_only=True,
            ),
            OpenApiExample(
                "Success response - new task",
                value={
                    "status": "success",
                    "message": "Client details PDF generation initiated successfully.",
                    "data": {
                        "task_id": "6f2a1c3d-4b5e-6f70-8192-a3b4c5d6e7f8",
                        "status": "pending",
                        "estimated_transactions": 58,
                        "estimated_time": "1s",
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Success response - duplicate task reused",
                value={
                    "status": "success",
                    "message": "Using existing PDF generation task.",
                    "data": {
                        "task_id": "6f2a1c3d-4b5e-6f70-8192-a3b4c5d6e7f8",
                        "status": "processing",
                        "elapsed_seconds": 3,
                        "reused": True,
                        "message": "A duplicate request is already being processed.",
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Something went wrong while initiating PDF generation.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    def get(self, request):
        """Generate the shop's client-details PDF asynchronously with Celery."""
        try:
            shop_id = get_active_shop_id(request)

            # No filters for this report - the hash is intentionally constant
            # per shop, so every repeat click for the same shop hits the same
            # dedup key (more collision-prone than the date/filter-bearing
            # export endpoints), which is exactly why the atomic cache lock
            # below is required here.
            request_params = {}
            request_hash = compute_request_hash(request_params)

            # Atomic check-then-create lock: without this, two near-
            # simultaneous identical requests can both pass the existing_task
            # lookup below before either has inserted its PDFTask row, each
            # creating a duplicate PDFTask + duplicate Celery dispatch.
            # cache.add() is atomic (SETNX-style), same pattern used by
            # export_account_history.py for this exact race.
            lock_key = f"pdf_client_details_dedup_{shop_id}_{request_hash}"
            lock_acquired = cache.add(lock_key, "1", timeout=30)

            if not lock_acquired:
                # Another identical request is mid-flight. Give it a chance to
                # have already inserted its PDFTask row and reuse it; if not
                # yet visible, ask the client to retry rather than racing to
                # create a second duplicate task ourselves.
                existing_task = PDFTask.objects.filter(
                    shop_id=shop_id,
                    pdf_type='client_details',
                    request_hash=request_hash,
                    status__in=['pending', 'processing'],
                    created_at__gte=timezone.now() - timedelta(minutes=15)
                ).order_by('-created_at').first()

                if existing_task:
                    elapsed = (timezone.now() - existing_task.created_at).total_seconds()
                    return self.success_response(
                        data={
                            'task_id': existing_task.task_id,
                            'status': existing_task.status,
                            'elapsed_seconds': int(elapsed),
                            'reused': True,
                            'message': 'A duplicate request is already being processed.'
                        },
                        message="Using existing PDF generation task."
                    )

                return self.success_response(
                    data={
                        'status': 'pending',
                        'reused': True,
                        'message': 'A duplicate request is currently being processed. Please retry shortly.'
                    },
                    message="A duplicate request is currently being processed."
                )

            try:
                existing_task = PDFTask.objects.filter(
                    shop_id=shop_id,
                    pdf_type='client_details',
                    request_hash=request_hash,
                    status__in=['pending', 'processing'],
                    created_at__gte=timezone.now() - timedelta(minutes=15)
                ).order_by('-created_at').first()

                if existing_task:
                    logger.info(f"[Client Details PDF] Reusing existing task {existing_task.task_id}")
                    elapsed = (timezone.now() - existing_task.created_at).total_seconds()

                    return self.success_response(
                        data={
                            'task_id': existing_task.task_id,
                            'status': existing_task.status,
                            'elapsed_seconds': int(elapsed),
                            'reused': True,
                            'message': 'A duplicate request is already being processed.'
                        },
                        message="Using existing PDF generation task."
                    )

                # Cheap indexed count for the estimate - no row fetch. Stored
                # on the PDFTask now so the Celery task can reuse it instead
                # of recomputing an identical count.
                estimated_count = Client.objects.filter(
                    shop_id=shop_id, deleted__isnull=True
                ).count()
                estimated_seconds = max(1, estimated_count // 100)
                estimated_time = f"{estimated_seconds}s"

                task_id = str(uuid.uuid4())
                PDFTask.objects.create(
                    task_id=task_id,
                    pdf_type='client_details',
                    shop_id=shop_id,
                    user=request.user,
                    status='pending',
                    request_params=request_params,
                    request_hash=request_hash,
                    estimated_count=estimated_count,
                    estimated_time=estimated_time
                )

                generate_client_details_pdf_task.delay(task_id, shop_id, request_params)

                return self.success_response(
                    data={
                        'task_id': task_id,
                        'status': 'pending',
                        'estimated_transactions': estimated_count,
                        'estimated_time': estimated_time,
                    },
                    message="Client details PDF generation initiated successfully."
                )
            finally:
                cache.delete(lock_key)

        except Exception as e:
            logger.error(f"ExportClientDetailsPDFView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while initiating PDF generation.",
                dev_message=str(e)
            )
