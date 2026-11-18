from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone
from datetime import timedelta
import logging
import time
import gc

from .cleanup_tasks import acquire_pdf_lock, release_pdf_lock
from .pdf_utils import _get_service_ids_fast, _get_products_count_fast, _build_streaming_products_queryset

logger = logging.getLogger("api_requests")

LOCK_RETRY_DELAY = [2, 3, 5, 8]
MAX_PRODUCTS_PER_EXCEL = 20000

try:
    from django.conf import settings
    MAX_PRODUCTS_PER_EXCEL = getattr(settings, 'MAX_PRODUCTS_PER_EXCEL', 20000)
except Exception:
    pass

# Excel report needs sale_price in addition to the PDF report's default field set.
EXCEL_EXTRA_ONLY_FIELDS = ['sale_price']


# ============================================================================
# MULTI-EXCEL COORDINATOR TASK
# ============================================================================
@shared_task(bind=True, max_retries=1, time_limit=900, soft_time_limit=870)
def generate_multi_excel_task(self, parent_task_id, shop_id, request_params, total_products):
    """
    Coordinator task for very large product sets (>MAX_PRODUCTS_PER_EXCEL).
    Splits into chunks and generates Excel files sequentially.
    """
    from services.models import ExcelGenerationTask
    from services.utils.excel_snapshot import ExcelSnapshotManager

    parent_task = None

    try:
        parent_task = ExcelGenerationTask.objects.get(task_id=parent_task_id)
        parent_task.status = 'chunking'
        parent_task.save(update_fields=['status'])

        num_chunks = (total_products + MAX_PRODUCTS_PER_EXCEL - 1) // MAX_PRODUCTS_PER_EXCEL

        logger.info(f"[Multi-Excel] Splitting {total_products} products into {num_chunks} Excel files")

        chunk_tasks = []
        for chunk_index in range(num_chunks):
            offset = chunk_index * MAX_PRODUCTS_PER_EXCEL
            limit = min(MAX_PRODUCTS_PER_EXCEL, total_products - offset)

            chunk_task_id = f"{parent_task_id}_chunk_{chunk_index + 1}"

            ExcelGenerationTask.objects.create(
                task_id=chunk_task_id,
                shop_id=shop_id,
                status='pending',
                request_params={**request_params, '_chunk_offset': offset, '_chunk_limit': limit},
                estimated_count=limit,
                request_hash=f"{ExcelSnapshotManager.compute_request_hash(request_params)}_chunk_{chunk_index}"
            )

            chunk_tasks.append({
                'task_id': chunk_task_id,
                'chunk_number': chunk_index + 1,
                'offset': offset,
                'limit': limit,
                'status': 'pending'
            })

        parent_task.total_chunks = num_chunks
        parent_task.request_params['_chunk_tasks'] = chunk_tasks
        parent_task.save(update_fields=['total_chunks', 'request_params'])

        completed_excels = []
        start_time = timezone.now()

        for idx, chunk_info in enumerate(chunk_tasks):
            chunk_task_id = chunk_info['task_id']
            chunk_number = chunk_info['chunk_number']

            logger.info(f"[Multi-Excel] Generating Excel {chunk_number}/{num_chunks}")

            progress = int((idx / num_chunks) * 90)
            self.update_state(state='PROGRESS', meta={
                'progress': progress,
                'stage': f'Generating Excel {chunk_number}/{num_chunks}',
                'completed_chunks': idx,
                'total_chunks': num_chunks
            })

            result = generate_excel_chunk_sequential.apply_async(
                args=[chunk_task_id, shop_id, chunk_info['offset'], chunk_info['limit'], request_params],
                countdown=0
            ).get(timeout=300)

            if result['status'] == 'completed':
                completed_excels.append({
                    'chunk_number': chunk_number,
                    'task_id': chunk_task_id,
                    'download_url': result['download_url'],
                    's3_key': result['s3_key'],
                    'product_count': chunk_info['limit'],
                    'generation_time': result.get('generation_time', 0)
                })
                chunk_tasks[idx]['status'] = 'completed'
                chunk_tasks[idx]['download_url'] = result['download_url']
            else:
                chunk_tasks[idx]['status'] = 'failed'
                chunk_tasks[idx]['error'] = result.get('error', 'Unknown error')

        parent_task.status = 'completed'
        parent_task.completed_at = timezone.now()
        parent_task.actual_generation_time = (timezone.now() - start_time).total_seconds()
        parent_task.request_params['_chunk_tasks'] = chunk_tasks
        parent_task.request_params['_completed_excels'] = completed_excels
        parent_task.save()

        logger.info(f"[Multi-Excel] Completed all {num_chunks} Excel files in {parent_task.actual_generation_time:.2f}s")

        return {
            'task_id': parent_task_id,
            'status': 'completed',
            'total_chunks': num_chunks,
            'completed_excels': completed_excels,
            'total_time': parent_task.actual_generation_time
        }

    except SoftTimeLimitExceeded:
        logger.error(f"[Multi-Excel] Task {parent_task_id} exceeded time limit")

        if parent_task:
            parent_task.status = 'failed'
            parent_task.error_message = 'Multi-Excel generation exceeded 15-minute time limit'
            parent_task.save(update_fields=['status', 'error_message'])

        return {'task_id': parent_task_id, 'status': 'failed', 'error': 'Timeout'}

    except Exception as e:
        logger.error(f"[Multi-Excel] Failed {parent_task_id}: {str(e)}", exc_info=True)

        if parent_task:
            parent_task.status = 'failed'
            parent_task.error_message = str(e)[:500]
            parent_task.save(update_fields=['status', 'error_message'])

        return {'task_id': parent_task_id, 'status': 'failed', 'error': str(e)}


# ============================================================================
# CHUNK EXCEL GENERATOR (SEQUENTIAL)
# ============================================================================
@shared_task(bind=True, max_retries=1, time_limit=300, soft_time_limit=280)
def generate_excel_chunk_sequential(self, chunk_task_id, shop_id, offset, limit, request_params):
    """
    Generate a single chunk Excel file (up to MAX_PRODUCTS_PER_EXCEL products).
    Called sequentially by the coordinator task.
    """
    from services.models import ExcelGenerationTask, ShopOwnerService
    from shop.models import Shop
    from services.utils.excel_snapshot import ExcelSnapshotManager
    from services.utils.product_report_excel import ProductReportExcelService, build_excel_filename

    chunk_task = None

    try:
        chunk_task = ExcelGenerationTask.objects.get(task_id=chunk_task_id)
        chunk_task.status = 'processing'
        chunk_task.save(update_fields=['status'])

        start_time = timezone.now()

        service_ids = _get_service_ids_fast(shop_id, request_params.get('shop_service_id'))

        shop = Shop.objects.only(
            'id', 'name', 'address', 'city', 'state', 'pincode', 'phone', 'email', 'img'
        ).get(id=shop_id)

        service_name = None
        if request_params.get('shop_service_id'):
            try:
                service = ShopOwnerService.objects.select_related('general_service').get(
                    id=int(request_params['shop_service_id'])
                )
                service_name = service.general_service.name if service.general_service else None
            except Exception:
                pass

        products_qs = _build_streaming_products_queryset(service_ids, request_params, extra_only=['sale_price'])
        products_qs = products_qs[offset:offset + limit]

        shop_name = shop.name

        logger.info(f"[Chunk Excel] Generating Excel for offset {offset}, limit {limit}...")
        excel_bytes = ProductReportExcelService.generate_product_report_excel(
            shop=shop,
            products_data=products_qs,
            service_name=service_name,
            search_term=request_params.get('search'),
            request_params=request_params
        )

        del shop
        gc.collect()

        request_hash = f"{ExcelSnapshotManager.compute_request_hash(request_params)}_chunk_{offset}"
        chunk_number = offset // MAX_PRODUCTS_PER_EXCEL + 1
        download_filename = build_excel_filename(shop_name, service_name, suffix=f"part{chunk_number}")
        s3_key, download_url, url_expires_at = ExcelSnapshotManager.save_excel(
            shop_id, None, request_hash, excel_bytes, is_base_case=False, download_filename=download_filename
        )

        del excel_bytes
        gc.collect()

        chunk_task.status = 'completed'
        chunk_task.s3_key = s3_key
        chunk_task.download_url = download_url
        chunk_task.completed_at = timezone.now()
        chunk_task.url_expires_at = url_expires_at
        chunk_task.actual_generation_time = (timezone.now() - start_time).total_seconds()
        chunk_task.save()

        logger.info(f"[Chunk Excel] Completed {chunk_task_id} in {chunk_task.actual_generation_time:.2f}s")

        return {
            'task_id': chunk_task_id,
            'status': 'completed',
            'download_url': download_url,
            's3_key': s3_key,
            'generation_time': chunk_task.actual_generation_time
        }

    except Exception as e:
        logger.error(f"[Chunk Excel] Failed {chunk_task_id}: {str(e)}", exc_info=True)

        if chunk_task:
            chunk_task.status = 'failed'
            chunk_task.error_message = str(e)[:500]
            chunk_task.save(update_fields=['status', 'error_message'])

        return {'task_id': chunk_task_id, 'status': 'failed', 'error': str(e)}


# ============================================================================
# SINGLE EXCEL GENERATION TASK (<= MAX_PRODUCTS_PER_EXCEL)
# ============================================================================
@shared_task(bind=True, max_retries=1, time_limit=300, soft_time_limit=280)
def generate_excel_parallel_task(self, task_id, shop_id, request_params):
    """Generate single Excel report with 5-minute timeout."""
    from services.models import ExcelGenerationTask, ShopOwnerService
    from shop.models import Shop
    from services.utils.excel_snapshot import ExcelSnapshotManager
    from services.utils.product_report_excel import ProductReportExcelService, build_excel_filename

    excel_task = None
    lock_key = None
    lock_acquired = False

    try:
        excel_task = ExcelGenerationTask.objects.get(task_id=task_id)

        service_ids = _get_service_ids_fast(shop_id, request_params.get('shop_service_id'))
        is_base_case = not ExcelSnapshotManager.has_filters(request_params)
        cache_key = ExcelSnapshotManager.compute_cache_key(shop_id, service_ids, request_params)
        request_hash = ExcelSnapshotManager.compute_request_hash(request_params)

        shop_name = Shop.objects.only('id', 'name').get(id=shop_id).name

        service_name = None
        if request_params.get('shop_service_id'):
            try:
                service = ShopOwnerService.objects.select_related('general_service').get(
                    id=int(request_params['shop_service_id'])
                )
                service_name = service.general_service.name if service.general_service else None
            except Exception:
                pass

        download_filename = build_excel_filename(shop_name, service_name)

        if is_base_case and cache_key:
            lock_key = f"excel_base_lock_{shop_id}_{cache_key}"
        else:
            lock_key = f"excel_filtered_lock_{shop_id}_{request_hash}"

        logger.info(f"[Excel] Task {task_id} attempting lock: {lock_key}")

        for attempt, delay in enumerate(LOCK_RETRY_DELAY):
            lock_acquired = acquire_pdf_lock(lock_key)

            if lock_acquired:
                logger.info(f"[Excel] Lock acquired on attempt {attempt + 1}")
                break

            if is_base_case and cache_key:
                logger.info(f"[Excel] Waiting {delay}s, checking cache...")
                time.sleep(delay)

                cache_result = ExcelSnapshotManager.check_cached_excel(shop_id, cache_key, download_filename=download_filename)
                if cache_result:
                    s3_key, download_url = cache_result

                    excel_task.status = 'completed'
                    excel_task.s3_key = s3_key
                    excel_task.download_url = download_url
                    excel_task.completed_at = timezone.now()
                    excel_task.url_expires_at = timezone.now() + timedelta(hours=24)
                    excel_task.actual_generation_time = (timezone.now() - excel_task.created_at).total_seconds()
                    excel_task.was_cached = True
                    excel_task.cache_hit_type = 'full'
                    excel_task.save()

                    logger.info(f"[Excel] Excel completed by another task during wait")
                    return {'task_id': task_id, 'status': 'completed', 'cached': True}
            else:
                time.sleep(delay)

        if not lock_acquired:
            raise Exception(f"Could not acquire lock after {len(LOCK_RETRY_DELAY)} attempts: {lock_key}")

        excel_task.status = 'processing'
        excel_task.save(update_fields=['status'])

        start_time = timezone.now()

        if is_base_case and cache_key:
            cache_result = ExcelSnapshotManager.check_cached_excel(shop_id, cache_key, download_filename=download_filename)
            if cache_result:
                s3_key, download_url = cache_result

                excel_task.status = 'completed'
                excel_task.s3_key = s3_key
                excel_task.download_url = download_url
                excel_task.completed_at = timezone.now()
                excel_task.url_expires_at = timezone.now() + timedelta(hours=24)
                excel_task.actual_generation_time = (timezone.now() - start_time).total_seconds()
                excel_task.was_cached = True
                excel_task.cache_hit_type = 'full'
                excel_task.save()

                logger.info(f"[Excel] Found cached Excel after lock acquisition")
                return {'task_id': task_id, 'status': 'completed', 'cached': True}

        products_count = _get_products_count_fast(service_ids, request_params)

        excel_task.estimated_count = products_count
        excel_task.save(update_fields=['estimated_count'])

        logger.info(f"[Excel] Generating Excel for {products_count} products")

        self.update_state(state='PROGRESS', meta={'progress': 10, 'stage': 'Loading data'})

        shop = Shop.objects.only(
            'id', 'name', 'address', 'city', 'state', 'pincode', 'phone', 'email', 'img'
        ).get(id=shop_id)

        self.update_state(state='PROGRESS', meta={'progress': 30, 'stage': 'Fetching products'})

        products_qs = _build_streaming_products_queryset(service_ids, request_params, extra_only=['sale_price'])

        self.update_state(state='PROGRESS', meta={'progress': 50, 'stage': 'Generating Excel'})

        logger.info(f"[Excel] Generating Excel for {products_count} products...")
        excel_bytes = ProductReportExcelService.generate_product_report_excel(
            shop=shop,
            products_data=products_qs.iterator(chunk_size=500),
            service_name=service_name,
            search_term=request_params.get('search'),
            request_params=request_params
        )

        self.update_state(state='PROGRESS', meta={'progress': 80, 'stage': 'Uploading to S3'})

        del shop
        gc.collect()

        logger.info(f"[Excel] Generated {len(excel_bytes)} bytes, uploading to S3...")

        s3_key, download_url, url_expires_at = ExcelSnapshotManager.save_excel(
            shop_id, cache_key, request_hash, excel_bytes, is_base_case, download_filename=download_filename
        )

        del excel_bytes
        gc.collect()

        self.update_state(state='PROGRESS', meta={'progress': 95, 'stage': 'Finalizing'})

        excel_task.status = 'completed'
        excel_task.s3_key = s3_key
        excel_task.download_url = download_url
        excel_task.completed_at = timezone.now()
        excel_task.url_expires_at = url_expires_at
        excel_task.actual_generation_time = (timezone.now() - start_time).total_seconds()
        excel_task.was_cached = False
        excel_task.cache_hit_type = 'generated'
        excel_task.request_hash = request_hash
        excel_task.save()

        logger.info(f"[Excel] Completed {task_id} in {excel_task.actual_generation_time:.2f}s")

        if not is_base_case:
            ExcelSnapshotManager.cleanup_old_filtered_excels(shop_id, hours_old=24)

        return {
            'task_id': task_id,
            'status': 'completed',
            'cached': False,
            'actual_time': excel_task.actual_generation_time
        }

    except SoftTimeLimitExceeded:
        logger.error(f"[Excel] Task {task_id} exceeded 5-minute soft time limit")

        if excel_task:
            excel_task.status = 'failed'
            excel_task.error_message = 'Excel generation exceeded 5-minute time limit'
            excel_task.completed_at = timezone.now()
            excel_task.save(update_fields=['status', 'error_message', 'completed_at'])

        if lock_acquired and lock_key:
            release_pdf_lock(lock_key)

        return {
            'task_id': task_id,
            'status': 'failed',
            'error': 'Task timeout: Excel generation took longer than 5 minutes'
        }

    except Exception as e:
        logger.error(f"[Excel] Failed {task_id}: {str(e)}", exc_info=True)

        if excel_task:
            excel_task.status = 'failed'
            excel_task.error_message = str(e)[:500]
            excel_task.save(update_fields=['status', 'error_message'])

        return {'task_id': task_id, 'status': 'failed', 'error': str(e)}

    finally:
        if lock_acquired and lock_key:
            release_pdf_lock(lock_key)
            logger.info(f"[Excel] Released lock: {lock_key}")
