import logging
import uuid
from datetime import timedelta
from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.conf import settings
from django.utils import timezone
from services.models import PDFGenerationTask
from services.utils.pdf_snapshot import PDFSnapshotManager
from services.utils.product_report_pdf import ProductReportPDFService

logger = logging.getLogger("api_requests")

MAX_PRODUCTS_FOR_STOCK_REPORT = getattr(settings, 'MAX_PRODUCTS_FOR_STOCK_REPORT', 1000)


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Start async product PDF generation",
    description="Starts PDF generation task (single or multi-chunk) with deduplication and optional cache reuse.",
    parameters=[
        OpenApiParameter("shop_service_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True, description="Shop service id."),
        OpenApiParameter("search", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Search term."),
        OpenApiParameter("search_by", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False, description="Search field."),
        OpenApiParameter("in_stock_only", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False, description="Stock filter true/false."),
        OpenApiParameter("min_price", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Minimum price."),
        OpenApiParameter("max_price", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, description="Maximum price."),
    ],
    responses={
        200: OpenApiResponse(description="Task accepted or reused, with task_id and progress metadata."),
        400: OpenApiResponse(description="Exact message: shop_service_id is required. Also returned when the service has more than 1,000 products, with message: Cannot generate Stock Report PDF: too many products."),
        404: OpenApiResponse(description="Exact message: Service not found or inactive."),
        500: OpenApiResponse(description="Exact message: Unable to start PDF generation."),
    },
    examples=[
        OpenApiExample("Request example", value={"shop_service_id": 8, "search": "shirt", "search_by": "name", "in_stock_only": True}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "PDF generation initiated successfully.", "data": {"task_id": "f9e9f1cf-1f29-4dde-a9c5-3e8eb8a8c5fb", "status": "pending", "estimated_products": 150, "is_multi_pdf": False}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["get"], url_path="generate-pdf-report")
def generate_pdf_report(self, request):
    """
    PDF generation with automatic chunking for >5000 products.
    Rejects the request with a validation error if the service has more than
    1000 products in total (MAX_PRODUCTS_FOR_STOCK_REPORT), checked before any
    task/cache work begins.
    Updated to handle in_stock_only filter correctly:
    - TRUE: Include products with stock/fabric > 0
    - FALSE: Include products with stock/fabric = 0
    - None/not provided: Include all products
    """
    try:
        shop_id = self.shop_id

        raw_service_id = request.query_params.get("shop_service_id", "")

        if not raw_service_id:
            return self.validation_error_response(
                errors={"shop_service_id": ["This field is required."]},
                message="shop_service_id is required."
            )

        from services.models import ShopOwnerService
        from services.tasks import _get_service_ids_fast, _get_products_count_fast
        try:
            shop_service = ShopOwnerService.objects.select_related(
                "general_service__main_category"
            ).get(
                id=int(raw_service_id),
                shop_id=shop_id,
                is_active=True
            )
            service_name = shop_service.general_service.name if shop_service.general_service else "Products"
            main_category_name = shop_service.general_service.main_category.name if shop_service.general_service and shop_service.general_service.main_category else None
        except (ValueError, ShopOwnerService.DoesNotExist):
            return self.not_found_response(
                resource="ShopOwnerService",
                message="Service not found or inactive."
            )

        # Restrict Stock Report PDF generation for services with too many products.
        # Checked before any duplicate-detection/cache/task work to avoid wasted processing.
        from services.models import Product
        total_product_count = Product.objects.filter(
            shop_service_id=shop_service.id,
            is_active=True
        ).count()

        if total_product_count > MAX_PRODUCTS_FOR_STOCK_REPORT:
            return self.validation_error_response(
                errors={"shop_service_id": [
                    f"This service has more than {MAX_PRODUCTS_FOR_STOCK_REPORT} products."
                ]},
                message="Cannot generate Stock Report PDF: too many products."
            )

        # Extract filters
        search = (request.query_params.get("search") or "").strip()
        search_by = request.query_params.get("search_by", "name")

        # Handle in_stock_only parameter
        in_stock_param = request.query_params.get("in_stock_only", "").strip().lower()
        if in_stock_param == "true":
            in_stock_only = True
        elif in_stock_param == "false":
            in_stock_only = False
        else:
            in_stock_only = None  # Not provided - include all products

        min_price = request.query_params.get("min_price", "").strip()
        max_price = request.query_params.get("max_price", "").strip()

        request_params = {
            'shop_service_id': raw_service_id,
            'search': search,
            'search_by': search_by,
            'in_stock_only': in_stock_only,
            'min_price': min_price,
            'max_price': max_price,
        }

        # Compute request hash for duplicate detection
        request_hash = PDFSnapshotManager.compute_request_hash(request_params)

        existing_task = PDFGenerationTask.objects.filter(
            shop_id=shop_id,
            request_hash=request_hash,
            status__in=['pending', 'processing', 'chunking'],
            created_at__gte=timezone.now() - timedelta(minutes=15)
        ).order_by('-created_at').first()

        if existing_task:
            logger.info(f"[PDF] Duplicate request detected, reusing task {existing_task.task_id}")

            elapsed = (timezone.now() - existing_task.created_at).total_seconds()
            is_multi_pdf = existing_task.total_chunks and existing_task.total_chunks > 1

            return self.success_response(
                data={
                    'task_id': existing_task.task_id,
                    'status': existing_task.status,
                    'estimated_products': existing_task.estimated_count,
                    'estimated_time': existing_task.estimated_time,
                    'elapsed_seconds': int(elapsed),
                    'reused': True,
                    'is_multi_pdf': is_multi_pdf,
                    'total_chunks': existing_task.total_chunks if is_multi_pdf else 1,
                    'message': 'A duplicate request is already being processed. Returning existing task.'
                },
                message="Using existing PDF generation task."
            )

        # Check cache (base case only)
        is_base_case = not PDFSnapshotManager.has_filters(request_params)

        if is_base_case:
            logger.info(f"[PDF] Base case request - checking cache")

            from services.tasks import _get_service_ids_fast
            service_ids = _get_service_ids_fast(shop_id, raw_service_id)

            cache_key = PDFSnapshotManager.compute_cache_key(
                shop_id, service_ids, request_params
            )

            if cache_key:
                cache_result = PDFSnapshotManager.check_cached_pdf(shop_id, cache_key)

                if cache_result:
                    s3_key, download_url = cache_result

                    logger.info(f"[PDF] Serving cached base case PDF")

                    return self.success_response(
                        data={
                            'status': 'completed',
                            'download_url': download_url,
                            'cached': True,
                            'generation_time': '< 0.1s',
                            'cache_type': 'base_case',
                            'is_multi_pdf': False,
                            'total_chunks': 1
                        },
                        message="PDF retrieved from cache successfully."
                    )

        service_ids = _get_service_ids_fast(shop_id, raw_service_id)
        estimated_count = _get_products_count_fast(service_ids, request_params)
        estimated_seconds, estimated_time = ProductReportPDFService.estimate_generation_time(estimated_count)

        task_id = str(uuid.uuid4())

        # Determine if multi-PDF generation needed
        MAX_PRODUCTS_PER_PDF = getattr(settings, 'MAX_PRODUCTS_PER_PDF', 5000)
        is_multi_pdf = estimated_count > MAX_PRODUCTS_PER_PDF

        # Prepare filter description for response
        filter_description = None
        if in_stock_only is True:
            if main_category_name == 'Materials':
                filter_description = 'Products with fabric length > 0'
            else:
                filter_description = 'Products with stock > 0'
        elif in_stock_only is False:
            if main_category_name == 'Materials':
                filter_description = 'Products with fabric length = 0'
            else:
                filter_description = 'Products with stock = 0'

        if is_multi_pdf:
            # Multi-PDF generation for >5000 products
            num_chunks = (estimated_count + MAX_PRODUCTS_PER_PDF - 1) // MAX_PRODUCTS_PER_PDF

            pdf_task = PDFGenerationTask.objects.create(
                task_id=task_id,
                shop_id=shop_id,
                status='pending',
                request_params=request_params,
                estimated_count=estimated_count,
                estimated_time=f"{estimated_seconds}s (split into {num_chunks} PDFs)",
                request_hash=request_hash,
                total_chunks=num_chunks
            )

            # Launch multi-PDF coordinator task
            from services.tasks import generate_multi_pdf_task
            generate_multi_pdf_task.delay(task_id, shop_id, request_params, estimated_count)

            return self.success_response(
                data={
                    'task_id': task_id,
                    'status': 'pending',
                    'estimated_products': estimated_count,
                    'estimated_time': estimated_time,
                    'is_multi_pdf': True,
                    'total_chunks': num_chunks,
                    'products_per_chunk': MAX_PRODUCTS_PER_PDF,
                    'note': f'Large dataset - generating {num_chunks} separate PDFs',
                    'main_category': main_category_name,
                    'filters_applied': {
                        'search': search,
                        'search_by': search_by,
                        'in_stock_only': in_stock_only,
                        'in_stock_filter_description': filter_description,
                        'min_price': min_price,
                        'max_price': max_price,
                    } if not is_base_case else None
                },
                message=f"Multi-PDF generation initiated ({num_chunks} PDFs)."
            )
        else:
            # Single PDF generation for <=5000 products
            pdf_task = PDFGenerationTask.objects.create(
                task_id=task_id,
                shop_id=shop_id,
                status='pending',
                request_params=request_params,
                estimated_count=estimated_count,
                estimated_time=estimated_time,
                request_hash=request_hash,
                total_chunks=1
            )

            from services.tasks import generate_pdf_parallel_task
            generate_pdf_parallel_task.delay(task_id, shop_id, request_params)

            return self.success_response(
                data={
                    'task_id': task_id,
                    'status': 'pending',
                    'estimated_products': estimated_count,
                    'estimated_time': estimated_time,
                    'is_multi_pdf': False,
                    'total_chunks': 1,
                    'note': 'Single PDF generation',
                    'main_category': main_category_name,
                    'filters_applied': {
                        'search': search,
                        'search_by': search_by,
                        'in_stock_only': in_stock_only,
                        'in_stock_filter_description': filter_description,
                        'min_price': min_price,
                        'max_price': max_price,
                    } if not is_base_case else None
                },
                message="PDF generation initiated successfully."
            )

    except Exception as e:
        logger.error(f"[PDF] Error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to start PDF generation.",
            dev_message=str(e)
        )
