import logging
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin
from finance.api.v3.views.tax_report_viewset.helpers import build_tax_report_queryset
from pdf_service.api.v3.utils.helpers import parse_date
from pdf_service.models import PDFTask
from pdf_service.tasks import generate_tax_report_pdf_task, compute_request_hash
from shop.api.admin_v1.utils.shop import get_active_shop_id
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

from .throttles import PDFGenerationThrottle

logger = logging.getLogger("api_requests")


class ExportTaxReportPDFView(StandardResponseMixin, APIView):
    """Initiates async PDF generation for the consolidated Tax Report."""
    authentication_classes = [CutoffJWTAuthentication]
    throttle_classes = [PDFGenerationThrottle]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
    ]

    @extend_schema(
        tags=["PDF Management V3"],
        summary="Initiate tax report PDF export",
        description=(
            "Creates an asynchronous PDF generation task for the consolidated Tax Report "
            "(shop details, GST number, statement period, overall summary — total taxable "
            "amount, total tax, CGST, SGST — and the full transaction table) over an optional "
            "date range. If an equivalent pending task exists in the recent window, that task "
            "is reused. Mirrors the same tax-applicable Bookings/Sales/Tailor Orders data as "
            "GET /finance/v3/tax-report/, but without pagination - every matching row is "
            "included in the PDF."
        ),
        parameters=[
            OpenApiParameter(
                name="from_date",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Lower bound (inclusive) of the date range. Accepted formats: YYYY-MM-DD or "
                    "DD-MM-YYYY. Optional. Displayed in the PDF as 'Statement From', always "
                    "formatted DD/MM/YYYY regardless of the input format submitted. "
                    "start_date is accepted as a legacy alias."
                ),
            ),
            OpenApiParameter(
                name="to_date",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Upper bound (inclusive) of the date range. Accepted formats: YYYY-MM-DD or "
                    "DD-MM-YYYY. Optional. Displayed in the PDF as 'Statement To', always "
                    "formatted DD/MM/YYYY regardless of the input format submitted. "
                    "end_date is accepted as a legacy alias."
                ),
            ),
        ],
        responses={
            200: OpenApiResponse(description="Task initiation response or duplicate-task reuse response."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages:\n"
                    "- Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY.\n"
                    "- start_date cannot be after end_date.\n"
                    "- Date range too large. Please select a smaller range."
                )
            ),
            500: OpenApiResponse(description="Exact message: Something went wrong while initiating PDF generation."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="GET /api/v3/pdf/tax-report/export/pdf/?from_date=01-07-2026&to_date=31-07-2026",
                value={"from_date": "01-07-2026", "to_date": "31-07-2026"},
                request_only=True,
            ),
            OpenApiExample(
                "Success response - new task",
                value={
                    "status": "success",
                    "message": "Tax report PDF generation initiated successfully.",
                    "data": {
                        "task_id": "9c8b7a6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
                        "status": "pending",
                        "estimated_transactions": 58,
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
                        "task_id": "9c8b7a6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d",
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
                "Invalid date format response",
                value={
                    "status": "validation_error",
                    "message": "Invalid date format.",
                    "data": {
                        "validation_errors": {
                            "date": ["Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY."]
                        }
                    },
                },
                response_only=True,
                status_codes=["400"],
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
        """Generate the consolidated Tax Report PDF asynchronously with Celery."""
        try:
            shop_id = get_active_shop_id(request)

            # from_date/to_date are the primary accepted names (matching
            # ExportLedgerPDFView's convention); start_date/end_date are kept
            # as fallbacks for the originally documented names.
            start_date_str = request.query_params.get('from_date') or request.query_params.get('start_date')
            end_date_str = request.query_params.get('to_date') or request.query_params.get('end_date')

            start_date = None
            end_date = None

            if start_date_str:
                start_date = parse_date(start_date_str)
                if not start_date:
                    return self.validation_error_response(
                        errors={"date": ["Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY."]},
                        message="Invalid date format."
                    )

            if end_date_str:
                end_date = parse_date(end_date_str)
                if not end_date:
                    return self.validation_error_response(
                        errors={"date": ["Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY."]},
                        message="Invalid date format."
                    )

            if start_date and end_date:
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
                'start_date': start_date.isoformat() if start_date else None,
                'end_date': end_date.isoformat() if end_date else None,
            }

            request_hash = compute_request_hash(request_params)

            # Atomic check-then-create lock: without this, two near-simultaneous
            # identical requests can both pass the existing_task lookup below
            # before either has inserted its PDFTask row, each creating a
            # duplicate PDFTask + duplicate Celery dispatch. cache.add() is
            # atomic (SETNX-style), same pattern used in export_account_history.py.
            lock_key = f"pdf_tax_report_dedup_{shop_id}_{request_hash}"
            lock_acquired = cache.add(lock_key, "1", timeout=30)

            if not lock_acquired:
                existing_task = PDFTask.objects.filter(
                    shop_id=shop_id,
                    pdf_type='tax_report',
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
                    pdf_type='tax_report',
                    request_hash=request_hash,
                    status__in=['pending', 'processing'],
                    created_at__gte=timezone.now() - timedelta(minutes=15)
                ).order_by('-created_at').first()

                if existing_task:
                    logger.info(f"[Tax Report PDF] Reusing existing task {existing_task.task_id}")
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

                # Estimate row count (cheap indexed count, not the full row fetch)
                estimated_count = build_tax_report_queryset(shop_id, start_date, end_date).count()
                estimated_seconds = max(1, estimated_count // 100)
                estimated_time = f"{estimated_seconds}s"

                task_id = str(uuid.uuid4())
                PDFTask.objects.create(
                    task_id=task_id,
                    pdf_type='tax_report',
                    shop_id=shop_id,
                    user=request.user,
                    status='pending',
                    request_params=request_params,
                    request_hash=request_hash,
                    estimated_count=estimated_count,
                    estimated_time=estimated_time
                )

                generate_tax_report_pdf_task.delay(task_id, shop_id, request_params)

                if start_date and end_date:
                    period = f"{start_date.strftime('%d-%m-%Y')} to {end_date.strftime('%d-%m-%Y')}"
                else:
                    period = "All time"

                return self.success_response(
                    data={
                        'task_id': task_id,
                        'status': 'pending',
                        'estimated_transactions': estimated_count,
                        'estimated_time': estimated_time,
                        'period': period
                    },
                    message="Tax report PDF generation initiated successfully."
                )
            finally:
                cache.delete(lock_key)

        except ValueError as ve:
            return self.validation_error_response(
                errors={"detail": [str(ve)]},
                message="Invalid input. Please check your filters and try again."
            )
        except Exception as e:
            logger.error(f"ExportTaxReportPDFView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while initiating PDF generation.",
                dev_message=str(e)
            )
