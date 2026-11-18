import logging
import uuid
from datetime import timedelta
from rest_framework.decorators import action
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from django.conf import settings
from django.utils import timezone
from services.models import ExcelGenerationTask
from services.utils.excel_snapshot import ExcelSnapshotManager
from services.utils.product_report_excel import ProductReportExcelService, build_excel_filename

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Service & Product Management V3"],
    summary="Start async product Excel generation",
    description="Starts Excel generation task (single or multi-chunk) with deduplication and optional cache reuse. Faster and lighter than the PDF report for shops with thousands of products.",
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
        400: OpenApiResponse(description="Exact message: shop_service_id is required."),
        404: OpenApiResponse(description="Exact message: Service not found or inactive."),
        500: OpenApiResponse(description="Exact message: Unable to start Excel generation."),
    },
    examples=[
        OpenApiExample("Request example", value={"shop_service_id": 8, "search": "shirt", "search_by": "name", "in_stock_only": True}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Excel generation initiated successfully.", "data": {"task_id": "f9e9f1cf-1f29-4dde-a9c5-3e8eb8a8c5fb", "status": "pending", "estimated_products": 150, "is_multi_excel": False}}, response_only=True, status_codes=["200"]),
    ],
)
@action(detail=False, methods=["get"], url_path="generate-excel-report")
def generate_excel_report(self, request):
    """
    Excel generation with automatic chunking for very large product sets.
    Handles in_stock_only filter the same way as the PDF report:
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

        search = (request.query_params.get("search") or "").strip()
        search_by = request.query_params.get("search_by", "name")

        in_stock_param = request.query_params.get("in_stock_only", "").strip().lower()
        if in_stock_param == "true":
            in_stock_only = True
        elif in_stock_param == "false":
            in_stock_only = False
        else:
            in_stock_only = None

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

        request_hash = ExcelSnapshotManager.compute_request_hash(request_params)

        existing_task = ExcelGenerationTask.objects.filter(
            shop_id=shop_id,
            request_hash=request_hash,
            status__in=['pending', 'processing', 'chunking'],
            created_at__gte=timezone.now() - timedelta(minutes=15)
        ).order_by('-created_at').first()

        if existing_task:
            logger.info(f"[Excel] Duplicate request detected, reusing task {existing_task.task_id}")

            elapsed = (timezone.now() - existing_task.created_at).total_seconds()
            is_multi_excel = existing_task.total_chunks and existing_task.total_chunks > 1

            return self.success_response(
                data={
                    'task_id': existing_task.task_id,
                    'status': existing_task.status,
                    'estimated_products': existing_task.estimated_count,
                    'estimated_time': existing_task.estimated_time,
                    'elapsed_seconds': int(elapsed),
                    'reused': True,
                    'is_multi_excel': is_multi_excel,
                    'total_chunks': existing_task.total_chunks if is_multi_excel else 1,
                    'message': 'A duplicate request is already being processed. Returning existing task.'
                },
                message="Using existing Excel generation task."
            )

        is_base_case = not ExcelSnapshotManager.has_filters(request_params)

        if is_base_case:
            logger.info(f"[Excel] Base case request - checking cache")

            service_ids = _get_service_ids_fast(shop_id, raw_service_id)

            cache_key = ExcelSnapshotManager.compute_cache_key(
                shop_id, service_ids, request_params
            )

            if cache_key:
                from shop.models import Shop
                shop_name = Shop.objects.only('id', 'name').get(id=shop_id).name
                download_filename = build_excel_filename(shop_name, service_name)
                cache_result = ExcelSnapshotManager.check_cached_excel(shop_id, cache_key, download_filename=download_filename)

                if cache_result:
                    s3_key, download_url = cache_result

                    logger.info(f"[Excel] Serving cached base case Excel")

                    return self.success_response(
                        data={
                            'status': 'completed',
                            'download_url': download_url,
                            'cached': True,
                            'generation_time': '< 0.1s',
                            'cache_type': 'base_case',
                            'is_multi_excel': False,
                            'total_chunks': 1
                        },
                        message="Excel retrieved from cache successfully."
                    )

        service_ids = _get_service_ids_fast(shop_id, raw_service_id)
        estimated_count = _get_products_count_fast(service_ids, request_params)
        estimated_seconds, estimated_time = ProductReportExcelService.estimate_generation_time(estimated_count)

        task_id = str(uuid.uuid4())

        MAX_PRODUCTS_PER_EXCEL = getattr(settings, 'MAX_PRODUCTS_PER_EXCEL', 20000)
        is_multi_excel = estimated_count > MAX_PRODUCTS_PER_EXCEL

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

        if is_multi_excel:
            num_chunks = (estimated_count + MAX_PRODUCTS_PER_EXCEL - 1) // MAX_PRODUCTS_PER_EXCEL

            ExcelGenerationTask.objects.create(
                task_id=task_id,
                shop_id=shop_id,
                status='pending',
                request_params=request_params,
                estimated_count=estimated_count,
                estimated_time=f"{estimated_seconds}s (split into {num_chunks} Excel files)",
                request_hash=request_hash,
                total_chunks=num_chunks
            )

            from services.tasks import generate_multi_excel_task
            generate_multi_excel_task.delay(task_id, shop_id, request_params, estimated_count)

            return self.success_response(
                data={
                    'task_id': task_id,
                    'status': 'pending',
                    'estimated_products': estimated_count,
                    'estimated_time': estimated_time,
                    'is_multi_excel': True,
                    'total_chunks': num_chunks,
                    'products_per_chunk': MAX_PRODUCTS_PER_EXCEL,
                    'note': f'Large dataset - generating {num_chunks} separate Excel files',
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
                message=f"Multi-Excel generation initiated ({num_chunks} files)."
            )
        else:
            ExcelGenerationTask.objects.create(
                task_id=task_id,
                shop_id=shop_id,
                status='pending',
                request_params=request_params,
                estimated_count=estimated_count,
                estimated_time=estimated_time,
                request_hash=request_hash,
                total_chunks=1
            )

            from services.tasks import generate_excel_parallel_task
            generate_excel_parallel_task.delay(task_id, shop_id, request_params)

            return self.success_response(
                data={
                    'task_id': task_id,
                    'status': 'pending',
                    'estimated_products': estimated_count,
                    'estimated_time': estimated_time,
                    'is_multi_excel': False,
                    'total_chunks': 1,
                    'note': 'Single Excel generation',
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
                message="Excel generation initiated successfully."
            )

    except Exception as e:
        logger.error(f"[Excel] Error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unable to start Excel generation.",
            dev_message=str(e)
        )
