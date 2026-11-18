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
MAX_PRODUCTS_PER_PDF = 5000

try:
    from django.conf import settings
    MAX_PRODUCTS_PER_PDF = getattr(settings, 'MAX_PRODUCTS_PER_PDF', 5000)
except Exception:
    pass


# ============================================================================
# MULTI-PDF COORDINATOR TASK
# ============================================================================
@shared_task(bind=True, max_retries=1, time_limit=900, soft_time_limit=870)
def generate_multi_pdf_task(self, parent_task_id, shop_id, request_params, total_products):
    """
    Coordinator task for large product sets (>5000 products).
    Splits into multiple 5000-product chunks and generates PDFs sequentially.
    """
    from services.models import PDFGenerationTask, ShopOwnerService
    from services.utils.pdf_snapshot import PDFSnapshotManager

    parent_task = None

    try:
        parent_task = PDFGenerationTask.objects.get(task_id=parent_task_id)
        parent_task.status = 'chunking'
        parent_task.save(update_fields=['status'])

        num_chunks = (total_products + MAX_PRODUCTS_PER_PDF - 1) // MAX_PRODUCTS_PER_PDF

        logger.info(f"[Multi-PDF] Splitting {total_products} products into {num_chunks} PDFs")

        chunk_tasks = []
        for chunk_index in range(num_chunks):
            offset = chunk_index * MAX_PRODUCTS_PER_PDF
            limit = min(MAX_PRODUCTS_PER_PDF, total_products - offset)

            chunk_task_id = f"{parent_task_id}_chunk_{chunk_index + 1}"

            PDFGenerationTask.objects.create(
                task_id=chunk_task_id,
                shop_id=shop_id,
                status='pending',
                request_params={**request_params, '_chunk_offset': offset, '_chunk_limit': limit},
                estimated_count=limit,
                request_hash=f"{PDFSnapshotManager.compute_request_hash(request_params)}_chunk_{chunk_index}"
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

        completed_pdfs = []
        start_time = timezone.now()

        for idx, chunk_info in enumerate(chunk_tasks):
            chunk_task_id = chunk_info['task_id']
            chunk_number = chunk_info['chunk_number']

            logger.info(f"[Multi-PDF] Generating PDF {chunk_number}/{num_chunks}")

            progress = int((idx / num_chunks) * 90)
            self.update_state(state='PROGRESS', meta={
                'progress': progress,
                'stage': f'Generating PDF {chunk_number}/{num_chunks}',
                'completed_chunks': idx,
                'total_chunks': num_chunks
            })

            result = generate_pdf_chunk_sequential.apply_async(
                args=[chunk_task_id, shop_id, chunk_info['offset'], chunk_info['limit'], request_params],
                countdown=0
            ).get(timeout=300)

            if result['status'] == 'completed':
                completed_pdfs.append({
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
        parent_task.request_params['_completed_pdfs'] = completed_pdfs
        parent_task.save()

        logger.info(f"[Multi-PDF] Completed all {num_chunks} PDFs in {parent_task.actual_generation_time:.2f}s")

        return {
            'task_id': parent_task_id,
            'status': 'completed',
            'total_chunks': num_chunks,
            'completed_pdfs': completed_pdfs,
            'total_time': parent_task.actual_generation_time
        }

    except SoftTimeLimitExceeded:
        logger.error(f"[Multi-PDF] Task {parent_task_id} exceeded time limit")

        if parent_task:
            parent_task.status = 'failed'
            parent_task.error_message = 'Multi-PDF generation exceeded 15-minute time limit'
            parent_task.save(update_fields=['status', 'error_message'])

        return {'task_id': parent_task_id, 'status': 'failed', 'error': 'Timeout'}

    except Exception as e:
        logger.error(f"[Multi-PDF] Failed {parent_task_id}: {str(e)}", exc_info=True)

        if parent_task:
            parent_task.status = 'failed'
            parent_task.error_message = str(e)[:500]
            parent_task.save(update_fields=['status', 'error_message'])

        return {'task_id': parent_task_id, 'status': 'failed', 'error': str(e)}


# ============================================================================
# CHUNK PDF GENERATOR (SEQUENTIAL)
# ============================================================================
@shared_task(bind=True, max_retries=1, time_limit=300, soft_time_limit=280)
def generate_pdf_chunk_sequential(self, chunk_task_id, shop_id, offset, limit, request_params):
    """
    Generate a single chunk PDF (up to 5000 products).
    Called sequentially by the coordinator task.
    """
    from services.models import PDFGenerationTask, ShopOwnerService
    from shop.models import Shop
    from services.utils.pdf_snapshot import PDFSnapshotManager
    from services.utils.product_report_pdf import ProductReportPDFService

    chunk_task = None

    try:
        chunk_task = PDFGenerationTask.objects.get(task_id=chunk_task_id)
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

        products_qs = _build_streaming_products_queryset(service_ids, request_params)
        products_qs = products_qs[offset:offset + limit]

        logger.info(f"[Chunk PDF] Loading {limit} products (offset {offset})...")
        products_list = list(products_qs)

        logger.info(f"[Chunk PDF] Generating PDF for {len(products_list)} products...")
        pdf_bytes = ProductReportPDFService.generate_product_report_pdf(
            shop=shop,
            products_data=products_list,
            service_name=service_name,
            search_term=request_params.get('search'),
            request_params=request_params
        )

        del products_list
        del shop
        gc.collect()

        request_hash = f"{PDFSnapshotManager.compute_request_hash(request_params)}_chunk_{offset}"
        s3_key, download_url, url_expires_at = PDFSnapshotManager.save_pdf(
            shop_id, None, request_hash, pdf_bytes, is_base_case=False
        )

        del pdf_bytes
        gc.collect()

        chunk_task.status = 'completed'
        chunk_task.s3_key = s3_key
        chunk_task.download_url = download_url
        chunk_task.completed_at = timezone.now()
        chunk_task.url_expires_at = url_expires_at
        chunk_task.actual_generation_time = (timezone.now() - start_time).total_seconds()
        chunk_task.save()

        logger.info(f"[Chunk PDF] Completed {chunk_task_id} in {chunk_task.actual_generation_time:.2f}s")

        return {
            'task_id': chunk_task_id,
            'status': 'completed',
            'download_url': download_url,
            's3_key': s3_key,
            'generation_time': chunk_task.actual_generation_time
        }

    except Exception as e:
        logger.error(f"[Chunk PDF] Failed {chunk_task_id}: {str(e)}", exc_info=True)

        if chunk_task:
            chunk_task.status = 'failed'
            chunk_task.error_message = str(e)[:500]
            chunk_task.save(update_fields=['status', 'error_message'])

        return {'task_id': chunk_task_id, 'status': 'failed', 'error': str(e)}


# ============================================================================
# SINGLE PDF GENERATION TASK (< 5000 products)
# ============================================================================
@shared_task(bind=True, max_retries=1, time_limit=300, soft_time_limit=280)
def generate_pdf_parallel_task(self, task_id, shop_id, request_params):
    """Generate single PDF with 5-minute timeout (for <= 5000 products)."""
    from services.models import PDFGenerationTask, ShopOwnerService
    from shop.models import Shop
    from services.utils.pdf_snapshot import PDFSnapshotManager
    from services.utils.product_report_pdf import ProductReportPDFService

    pdf_task = None
    lock_key = None
    lock_acquired = False

    try:
        pdf_task = PDFGenerationTask.objects.get(task_id=task_id)

        service_ids = _get_service_ids_fast(shop_id, request_params.get('shop_service_id'))
        is_base_case = not PDFSnapshotManager.has_filters(request_params)
        cache_key = PDFSnapshotManager.compute_cache_key(shop_id, service_ids, request_params)
        request_hash = PDFSnapshotManager.compute_request_hash(request_params)

        if is_base_case and cache_key:
            lock_key = f"pdf_base_lock_{shop_id}_{cache_key}"
        else:
            lock_key = f"pdf_filtered_lock_{shop_id}_{request_hash}"

        logger.info(f"[PDF] Task {task_id} attempting lock: {lock_key}")

        for attempt, delay in enumerate(LOCK_RETRY_DELAY):
            lock_acquired = acquire_pdf_lock(lock_key)

            if lock_acquired:
                logger.info(f"[PDF] Lock acquired on attempt {attempt + 1}")
                break

            if is_base_case and cache_key:
                logger.info(f"[PDF] Waiting {delay}s, checking cache...")
                time.sleep(delay)

                cache_result = PDFSnapshotManager.check_cached_pdf(shop_id, cache_key)
                if cache_result:
                    s3_key, download_url = cache_result

                    pdf_task.status = 'completed'
                    pdf_task.s3_key = s3_key
                    pdf_task.download_url = download_url
                    pdf_task.completed_at = timezone.now()
                    pdf_task.url_expires_at = timezone.now() + timedelta(hours=24)
                    pdf_task.actual_generation_time = (timezone.now() - pdf_task.created_at).total_seconds()
                    pdf_task.was_cached = True
                    pdf_task.cache_hit_type = 'full'
                    pdf_task.save()

                    logger.info(f"[PDF] PDF completed by another task during wait")
                    return {'task_id': task_id, 'status': 'completed', 'cached': True}
            else:
                time.sleep(delay)

        if not lock_acquired:
            raise Exception(f"Could not acquire lock after {len(LOCK_RETRY_DELAY)} attempts: {lock_key}")

        pdf_task.status = 'processing'
        pdf_task.save(update_fields=['status'])

        start_time = timezone.now()

        if is_base_case and cache_key:
            cache_result = PDFSnapshotManager.check_cached_pdf(shop_id, cache_key)
            if cache_result:
                s3_key, download_url = cache_result

                pdf_task.status = 'completed'
                pdf_task.s3_key = s3_key
                pdf_task.download_url = download_url
                pdf_task.completed_at = timezone.now()
                pdf_task.url_expires_at = timezone.now() + timedelta(hours=24)
                pdf_task.actual_generation_time = (timezone.now() - start_time).total_seconds()
                pdf_task.was_cached = True
                pdf_task.cache_hit_type = 'full'
                pdf_task.save()

                logger.info(f"[PDF] Found cached PDF after lock acquisition")
                return {'task_id': task_id, 'status': 'completed', 'cached': True}

        products_count = _get_products_count_fast(service_ids, request_params)

        pdf_task.estimated_count = products_count
        pdf_task.save(update_fields=['estimated_count'])

        logger.info(f"[PDF] Generating PDF for {products_count} products")

        self.update_state(state='PROGRESS', meta={'progress': 10, 'stage': 'Loading data'})

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

        self.update_state(state='PROGRESS', meta={'progress': 20, 'stage': 'Fetching products'})

        products_qs = _build_streaming_products_queryset(service_ids, request_params)

        logger.info(f"[PDF] Loading products with iterator...")
        products_list = []
        chunk_size = 500
        for product in products_qs.iterator(chunk_size=chunk_size):
            products_list.append(product)

            if len(products_list) % 100 == 0:
                progress = min(20 + int((len(products_list) / products_count) * 30), 50)
                self.update_state(state='PROGRESS', meta={
                    'progress': progress,
                    'stage': f'Loaded {len(products_list)}/{products_count} products'
                })

        self.update_state(state='PROGRESS', meta={'progress': 60, 'stage': 'Generating PDF'})

        logger.info(f"[PDF] Generating PDF for {len(products_list)} products...")
        pdf_bytes = ProductReportPDFService.generate_product_report_pdf(
            shop=shop,
            products_data=products_list,
            service_name=service_name,
            search_term=request_params.get('search'),
            request_params=request_params
        )

        self.update_state(state='PROGRESS', meta={'progress': 80, 'stage': 'Uploading to S3'})

        del products_list
        del shop
        gc.collect()

        logger.info(f"[PDF] Generated {len(pdf_bytes)} bytes, uploading to S3...")

        s3_key, download_url, url_expires_at = PDFSnapshotManager.save_pdf(
            shop_id, cache_key, request_hash, pdf_bytes, is_base_case
        )

        del pdf_bytes
        gc.collect()

        self.update_state(state='PROGRESS', meta={'progress': 95, 'stage': 'Finalizing'})

        pdf_task.status = 'completed'
        pdf_task.s3_key = s3_key
        pdf_task.download_url = download_url
        pdf_task.completed_at = timezone.now()
        pdf_task.url_expires_at = url_expires_at
        pdf_task.actual_generation_time = (timezone.now() - start_time).total_seconds()
        pdf_task.was_cached = False
        pdf_task.cache_hit_type = 'generated'
        pdf_task.request_hash = request_hash
        pdf_task.save()

        logger.info(f"[PDF] Completed {task_id} in {pdf_task.actual_generation_time:.2f}s")

        if not is_base_case:
            PDFSnapshotManager.cleanup_old_filtered_pdfs(shop_id, hours_old=24)

        return {
            'task_id': task_id,
            'status': 'completed',
            'cached': False,
            'actual_time': pdf_task.actual_generation_time
        }

    except SoftTimeLimitExceeded:
        logger.error(f"[PDF] Task {task_id} exceeded 5-minute soft time limit")

        if pdf_task:
            pdf_task.status = 'failed'
            pdf_task.error_message = 'PDF generation exceeded 5-minute time limit'
            pdf_task.completed_at = timezone.now()
            pdf_task.save(update_fields=['status', 'error_message', 'completed_at'])

        if lock_acquired and lock_key:
            release_pdf_lock(lock_key)

        return {
            'task_id': task_id,
            'status': 'failed',
            'error': 'Task timeout: PDF generation took longer than 5 minutes'
        }

    except Exception as e:
        logger.error(f"[PDF] Failed {task_id}: {str(e)}", exc_info=True)

        if pdf_task:
            pdf_task.status = 'failed'
            pdf_task.error_message = str(e)[:500]
            pdf_task.save(update_fields=['status', 'error_message'])

        return {'task_id': task_id, 'status': 'failed', 'error': str(e)}

    finally:
        if lock_acquired and lock_key:
            release_pdf_lock(lock_key)
            logger.info(f"[PDF] Released lock: {lock_key}")
