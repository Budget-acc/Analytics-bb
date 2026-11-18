import logging
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from finance.api.v3.utils.account_history import get_account_history_data
from finance.models import Account
from pdf_service.api.v3.utils.helpers import parse_date
from pdf_service.models import PDFTask
from pdf_service.tasks import generate_account_history_pdf_task, compute_request_hash
from shop.api.admin_v1.utils.shop import get_active_shop_id
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

from .throttles import PDFGenerationThrottle

logger = logging.getLogger("api_requests")


class ExportAccountHistoryPDFView(StandardResponseMixin, APIView):
    """Initiates async PDF generation for a single account's bank-statement-style history."""
    authentication_classes = [CutoffJWTAuthentication]
    throttle_classes = [PDFGenerationThrottle]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]

    @extend_schema(
        tags=["PDF Management V3"],
        summary="Initiate account history PDF export",
        description=(
            "Creates an asynchronous PDF generation task for one account's transaction "
            "history (bank-statement style: Date & Time | Description | Debit | Credit | "
            "Balance) over a date range. If an equivalent pending task exists in the "
            "recent window, that task is reused."
        ),
        parameters=[
            OpenApiParameter(
                name="account_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                required=True,
                description="Account id.",
            ),
            OpenApiParameter(
                name="start_date",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Start date. Accepted formats: YYYY-MM-DD or DD-MM-YYYY.",
            ),
            OpenApiParameter(
                name="end_date",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=True,
                description="End date. Accepted formats: YYYY-MM-DD or DD-MM-YYYY.",
            ),
        ],
        responses={
            200: OpenApiResponse(description="Task initiation response or duplicate-task reuse response."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages:\n"
                    "- start_date and end_date are required.\n"
                    "- Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY.\n"
                    "- start_date cannot be after end_date.\n"
                    "- Date range too large. Please select a smaller range."
                )
            ),
            404: OpenApiResponse(description="Exact message: Account not found."),
            500: OpenApiResponse(description="Exact message: Something went wrong while initiating PDF generation."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="GET /api/v3/pdf/account/5/export/pdf/?start_date=01-07-2026&end_date=31-07-2026",
                value={"account_id": 5, "start_date": "01-07-2026", "end_date": "31-07-2026"},
                request_only=True,
            ),
            OpenApiExample(
                "Success response - new task",
                value={
                    "status": "success",
                    "message": "Account history PDF generation initiated successfully.",
                    "data": {
                        "task_id": "1e2b3c4d-5f6a-7b8c-9d0e-1f2a3b4c5d6e",
                        "status": "pending",
                        "estimated_transactions": 42,
                        "estimated_time": "1s",
                        "period": "01-07-2026 to 31-07-2026",
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
                        "task_id": "1e2b3c4d-5f6a-7b8c-9d0e-1f2a3b4c5d6e",
                        "status": "processing",
                        "elapsed_seconds": 4,
                        "reused": True,
                        "message": "A duplicate request is already being processed.",
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Missing dates response",
                value={
                    "status": "validation_error",
                    "message": "start_date and end_date are required.",
                    "data": {
                        "validation_errors": {
                            "date": ["start_date and end_date are required."]
                        }
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Not found response",
                value={
                    "status": "not_found",
                    "message": "Account not found.",
                    "data": None,
                },
                response_only=True,
                status_codes=["404"],
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
    def get(self, request, account_id=None):
        """Generate an account's bank-statement history PDF asynchronously with Celery."""
        try:
            shop_id = get_active_shop_id(request)

            account = Account.objects.filter(
                id=account_id, shop_id=shop_id, deleted__isnull=True
            ).first()
            if not account:
                return self.not_found_response(
                    message="Account not found.",
                    dev_message=f"Account id={account_id} not found for shop {shop_id}.",
                )

            start_date_str = request.query_params.get('start_date')
            end_date_str = request.query_params.get('end_date')

            if not start_date_str or not end_date_str:
                return self.validation_error_response(
                    errors={"date": ["start_date and end_date are required."]},
                    message="start_date and end_date are required."
                )

            start_date = parse_date(start_date_str)
            end_date = parse_date(end_date_str)

            if not start_date or not end_date:
                return self.validation_error_response(
                    errors={"date": ["Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY."]},
                    message="Invalid date format."
                )

            if start_date > end_date:
                return self.validation_error_response(
                    errors={"date": ["start_date cannot be after end_date."]},
                    message="start_date cannot be after end_date."
                )

            if (end_date - start_date).days > 365:
                return self.validation_error_response(
                    errors={"date": ["Date range cannot exceed 1 year (365 days)."]},
                    message="Date range too large. Please select a smaller range."
                )

            request_params = {
                'account_id': account_id,
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
            }

            request_hash = compute_request_hash(request_params)

            # Atomic check-then-create lock: without this, two near-simultaneous
            # identical requests can both pass the existing_task lookup below
            # before either has inserted its PDFTask row, each creating a
            # duplicate PDFTask + duplicate Celery dispatch. cache.add() is
            # atomic (SETNX-style, same pattern used elsewhere in this codebase,
            # e.g. booking_v2/api/v5/views/booking_viewset/helpers/booking_create.py),
            # so only one request at a time can hold this key.
            lock_key = f"pdf_account_history_dedup_{shop_id}_{request_hash}"
            lock_acquired = cache.add(lock_key, "1", timeout=30)

            if not lock_acquired:
                # Another identical request is mid-flight. Give it a chance to
                # have already inserted its PDFTask row and reuse it; if not
                # yet visible, ask the client to retry rather than racing to
                # create a second duplicate task ourselves.
                existing_task = PDFTask.objects.filter(
                    shop_id=shop_id,
                    pdf_type='account_history',
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
                    pdf_type='account_history',
                    request_hash=request_hash,
                    status__in=['pending', 'processing'],
                    created_at__gte=timezone.now() - timedelta(minutes=15)
                ).order_by('-created_at').first()

                if existing_task:
                    logger.info(f"[Account History PDF] Reusing existing task {existing_task.task_id}")
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

                # Estimate entry count (cheap indexed count, not the full row fetch)
                _, ledger_qs, _, _ = get_account_history_data(account, start_date, end_date)
                estimated_count = ledger_qs.count()
                estimated_seconds = max(1, estimated_count // 100)
                estimated_time = f"{estimated_seconds}s"

                task_id = str(uuid.uuid4())
                PDFTask.objects.create(
                    task_id=task_id,
                    pdf_type='account_history',
                    shop_id=shop_id,
                    user=request.user,
                    status='pending',
                    request_params=request_params,
                    request_hash=request_hash,
                    estimated_count=estimated_count,
                    estimated_time=estimated_time
                )

                generate_account_history_pdf_task.delay(task_id, shop_id, request_params)

                return self.success_response(
                    data={
                        'task_id': task_id,
                        'status': 'pending',
                        'estimated_transactions': estimated_count,
                        'estimated_time': estimated_time,
                        'period': f"{start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')}"
                    },
                    message="Account history PDF generation initiated successfully."
                )
            finally:
                cache.delete(lock_key)

        except ValueError as ve:
            return self.validation_error_response(
                errors={"detail": [str(ve)]},
                message="Invalid input. Please check your filters and try again."
            )
        except Exception as e:
            logger.error(f"ExportAccountHistoryPDFView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while initiating PDF generation.",
                dev_message=str(e)
            )
