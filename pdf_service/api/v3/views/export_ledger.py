import logging
import uuid
from datetime import timedelta

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from pdf_service.models import PDFTask
from pdf_service.tasks import generate_ledger_pdf_task, compute_request_hash
from shop.api.admin_v1.utils.shop import get_active_shop_id
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import SubscriptionActivePermission

from .mixins import ShopLedgerTransactionExportMixin
from .throttles import PDFGenerationThrottle

logger = logging.getLogger("api_requests")


class ExportLedgerPDFView(ShopLedgerTransactionExportMixin, APIView):
    """New endpoint for ledger book PDF generation."""
    authentication_classes = [CutoffJWTAuthentication]
    throttle_classes = [PDFGenerationThrottle]
    permission_classes = [
        IsAuthenticated,
        SubscriptionActivePermission,
        ]    

    @extend_schema(
        tags=["PDF Management V3"],
        summary="Initiate ledger PDF export",
        description=(
            "Creates an asynchronous PDF generation task for ledger-book export over a date range. "
            "If an equivalent pending task exists in the recent window, that task is reused."
        ),
        parameters=[
            OpenApiParameter(
                name="from_date",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Start date. Accepted formats: YYYY-MM-DD or DD-MM-YYYY.",
            ),
            OpenApiParameter(
                name="to_date",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=True,
                description="End date. Accepted formats: YYYY-MM-DD or DD-MM-YYYY.",
            ),
            OpenApiParameter(
                name="client_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Optional client filter.",
            ),
            OpenApiParameter(
                name="type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Optional comma-separated transaction categories. Example: bookings,sales.",
            ),
            OpenApiParameter(
                name="txn_types",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Alternative to type. Can be repeated or comma-separated. Default fallback is all.",
            ),
        ],
        responses={
            200: OpenApiResponse(description="Task initiation response or duplicate-task reuse response."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages:\n"
                    "- Please provide date range.\n"
                    "- from_date and to_date are required.\n"
                    "- Invalid date format.\n"
                    "- Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY.\n"
                    "- Invalid date range.\n"
                    "- Start date cannot be later than end date.\n"
                    "- Date range too large. Please select a smaller range.\n"
                    "- Date range cannot exceed 1 year (365 days).\n"
                    "- Invalid input. Please check your filters and try again."
                )
            ),
            500: OpenApiResponse(description="Exact message: Something went wrong while initiating PDF generation."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="GET /api/v3/pdf/ledger/export/pdf/?from_date=01-01-2026&to_date=31-01-2026&txn_types=all",
                value={
                    "from_date": "01-01-2026",
                    "to_date": "31-01-2026",
                    "txn_types": "all",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response - new task",
                value={
                    "status": "success",
                    "message": "Ledger PDF generation initiated successfully.",
                    "data": {
                        "task_id": "f6f6dd53-c379-4f0f-bf75-11f70683b46f",
                        "status": "pending",
                        "estimated_transactions": 240,
                        "estimated_time": "3s",
                        "period": "01-01-2026 to 31-01-2026",
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
                        "task_id": "f6f6dd53-c379-4f0f-bf75-11f70683b46f",
                        "status": "processing",
                        "elapsed_seconds": 9,
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
                "Date range too large response",
                value={
                    "status": "validation_error",
                    "message": "Date range too large. Please select a smaller range.",
                    "data": {
                        "validation_errors": {
                            "date": ["Date range cannot exceed 1 year (365 days)."]
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
        """Generate ledger book PDF asynchronously with Celery."""
        try:
            shop_id = get_active_shop_id(request)

            # Extract and validate parameters
            from_date_str = request.query_params.get('from_date')
            to_date_str = request.query_params.get('to_date')
            client_id = request.query_params.get('client_id')

            # Support both comma-separated (type=bookings,sales) and multiple params (txn_types=bookings&txn_types=sales)
            txn_types_param = request.query_params.get('type') or request.query_params.get('txn_types')
            if txn_types_param:
                # Comma-separated values
                txn_types = [t.strip().lower() for t in txn_types_param.split(',') if t.strip()]
            else:
                # Multiple query parameters
                txn_types = request.query_params.getlist('txn_types')
                txn_types = [t.strip().lower() for t in txn_types if t.strip()]

            # Default to 'all' if nothing provided
            if not txn_types:
                txn_types = ['all']

            if not from_date_str or not to_date_str:
                return self.validation_error_response(
                    errors={"date": ["from_date and to_date are required."]},
                    message="Please provide date range."
                )

            # Validate dates
            try:
                from_date = self.parse_date(from_date_str)
                to_date = self.parse_date(to_date_str)

                if not from_date or not to_date:
                    return self.validation_error_response(
                        errors={"date": ["Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY."]},
                        message="Invalid date format."
                    )

                if from_date > to_date:
                    return self.validation_error_response(
                        errors={"date": ["Start date cannot be later than end date."]},
                        message="Invalid date range."
                    )

                # Validate max date range (1 year to prevent OOM)
                if (to_date - from_date).days > 365:
                    return self.validation_error_response(
                        errors={"date": ["Date range cannot exceed 1 year (365 days)."]},
                        message="Date range too large. Please select a smaller range."
                    )

            except ValueError as ve:
                return self.validation_error_response(
                    errors={"date": [str(ve)]},
                    message="Invalid date format."
                )

            request_params = {
                'from_date': from_date.isoformat(),
                'to_date': to_date.isoformat(),
                'client_id': client_id,
                'txn_types': txn_types,
            }

            # Check for duplicate requests
            request_hash = compute_request_hash(request_params)

            existing_task = PDFTask.objects.filter(
                shop_id=shop_id,
                pdf_type='ledger_book',
                request_hash=request_hash,
                status__in=['pending', 'processing'],
                created_at__gte=timezone.now() - timedelta(minutes=15)
            ).order_by('-created_at').first()

            if existing_task:
                logger.info(f"[Ledger PDF] Reusing existing task {existing_task.task_id}")
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

            # Estimate transaction count
            transactions = self.get_transactions(
                shop_id, from_date, to_date, txn_types, client_id
            )
            estimated_count = len(transactions)

            estimated_seconds = max(1, estimated_count // 80)  # Ledger is slightly slower
            estimated_time = f"{estimated_seconds}s"

            # Create task
            task_id = str(uuid.uuid4())
            pdf_task = PDFTask.objects.create(
                task_id=task_id,
                pdf_type='ledger_book',
                shop_id=shop_id,
                user=request.user,
                status='pending',
                request_params=request_params,
                request_hash=request_hash,
                estimated_count=estimated_count,
                estimated_time=estimated_time
            )

            # Launch Celery task
            generate_ledger_pdf_task.delay(task_id, shop_id, request_params)

            return self.success_response(
                data={
                    'task_id': task_id,
                    'status': 'pending',
                    'estimated_transactions': estimated_count,
                    'estimated_time': estimated_time,
                    'period': f"{from_date.strftime('%d-%m-%Y')} to {to_date.strftime('%d-%m-%Y')}"
                },
                message="Ledger PDF generation initiated successfully."
            )

        except ValueError as ve:
            return self.validation_error_response(
                errors={"detail": [str(ve)]},
                message="Invalid input. Please check your filters and try again."
            )
        except Exception as e:
            logger.error(f"ExportLedgerPDFView error: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while initiating PDF generation.",
                dev_message=str(e)
            )