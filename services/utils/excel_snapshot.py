# excel_snapshot.py
import hashlib
import logging
from typing import Dict, Optional, Tuple
from django.db.models import Max
from django.utils import timezone
from datetime import timedelta
from services.models import Product, ProductVariant
from services.utils.pdf_snapshot import PDFSnapshotManager

logger = logging.getLogger("api_requests")


class ExcelSnapshotManager:
    """
    Manages Excel generation with caching ONLY for base case (no filters).
    Mirrors PDFSnapshotManager but targets ExcelGenerationTask and .xlsx S3 objects,
    with its own layout version so cache keys never collide with the PDF report.
    """

    LAYOUT_VERSION = "excel_v1"
    PRESIGNED_URL_HOURS = 24

    # Filter/hash logic is format-agnostic - reuse PDFSnapshotManager directly.
    has_filters = PDFSnapshotManager.has_filters
    compute_request_hash = PDFSnapshotManager.compute_request_hash

    @classmethod
    def compute_cache_key(
        cls,
        shop_id: int,
        service_ids: list,
        filters: Dict
    ) -> Optional[str]:
        """
        Compute cache key. If filters exist, return None (no caching).
        If no filters, return version hash for base case.
        """
        if cls.has_filters(filters):
            logger.info(f"[Excel] Filters active - caching DISABLED")
            return None

        try:
            sorted_service_ids = sorted(service_ids)

            product_updated_ts = Product.objects.filter(
                shop_service_id__in=service_ids,
                is_active=True
            ).aggregate(max_updated=Max('updated_at'))['max_updated']

            variant_structure_ts = ProductVariant.objects.filter(
                product__shop_service_id__in=service_ids,
                is_active=True
            ).aggregate(max_updated=Max('structure_updated_at'))['max_updated']

            variant_quantity_ts = ProductVariant.objects.filter(
                product__shop_service_id__in=service_ids,
                is_active=True
            ).aggregate(max_updated=Max('quantity_updated_at'))['max_updated']

            version_input = (
                f"{sorted_service_ids}|"
                f"{product_updated_ts}|"
                f"{variant_structure_ts}|"
                f"{variant_quantity_ts}|"
                f"{cls.LAYOUT_VERSION}"
            )

            cache_key = hashlib.md5(version_input.encode()).hexdigest()[:12]
            logger.info(f"[Excel] Base case cache key: {cache_key}")
            return cache_key

        except Exception as e:
            logger.error(f"[Excel] Cache key error: {e}")
            return None

    @classmethod
    def get_s3_key(
        cls,
        shop_id: int,
        cache_key: Optional[str],
        request_hash: Optional[str] = None
    ) -> str:
        """Generate S3 key. If no cache_key, use request_hash for filtered."""
        if cache_key:
            return f"excel_reports/{shop_id}/cached_{cache_key}.xlsx"
        if request_hash:
            return f"excel_reports/{shop_id}/filtered_{request_hash}.xlsx"
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S_%f")
        return f"excel_reports/{shop_id}/filtered_{timestamp}.xlsx"

    @classmethod
    def check_cached_excel(
        cls,
        shop_id: int,
        cache_key: Optional[str],
        download_filename: Optional[str] = None
    ) -> Optional[Tuple[str, str]]:
        """
        Check if cached Excel file exists.
        Only checks cache for base case (no filters).
        """
        from services.s3_service import S3Service
        from services.models import ExcelGenerationTask

        if not cache_key:
            logger.info(f"[Excel] Filtered request - always generate new")
            return None

        s3_key = cls.get_s3_key(shop_id, cache_key)

        if not S3Service.file_exists(s3_key):
            logger.info(f"[Excel] Cache miss: {s3_key}")
            return None

        excel_task = ExcelGenerationTask.objects.filter(
            shop_id=shop_id,
            s3_key=s3_key,
            status='completed'
        ).order_by('-completed_at').first()

        if not excel_task:
            logger.warning(f"[Excel] File exists but no task record: {s3_key}")
            url = S3Service.get_excel_presigned_url(
                s3_key, expires_in_hours=cls.PRESIGNED_URL_HOURS, download_filename=download_filename
            )
            return (s3_key, url)

        if excel_task.is_url_expired():
            logger.info(f"[Excel] Refreshing expired URL: {s3_key}")
            new_url = S3Service.get_excel_presigned_url(
                s3_key, expires_in_hours=cls.PRESIGNED_URL_HOURS, download_filename=download_filename
            )

            ExcelGenerationTask.objects.filter(
                id=excel_task.id,
                url_expires_at__lt=timezone.now()
            ).update(
                download_url=new_url,
                url_expires_at=timezone.now() + timedelta(hours=cls.PRESIGNED_URL_HOURS)
            )

            if ExcelGenerationTask.objects.filter(id=excel_task.id).exists():
                excel_task.refresh_from_db()

            return (s3_key, new_url)

        logger.info(f"[Excel] Using cached Excel: {s3_key}")
        return (s3_key, excel_task.download_url)

    @classmethod
    def save_excel(
        cls,
        shop_id: int,
        cache_key: Optional[str],
        request_hash: Optional[str],
        excel_bytes: bytes,
        is_base_case: bool,
        download_filename: Optional[str] = None
    ) -> Tuple[str, str, timezone.datetime]:
        """
        Save Excel file to S3.
        Returns: (s3_key, download_url, url_expires_at)
        """
        from services.s3_service import S3Service

        s3_key = cls.get_s3_key(shop_id, cache_key, request_hash)
        download_url = S3Service.upload_excel(
            s3_key,
            excel_bytes,
            expires_in_hours=cls.PRESIGNED_URL_HOURS,
            download_filename=download_filename
        )

        url_expires_at = timezone.now() + timedelta(hours=cls.PRESIGNED_URL_HOURS)

        if is_base_case:
            logger.info(f"[Excel] Saved base case Excel: {s3_key}")
        else:
            logger.info(f"[Excel] Saved filtered Excel (no cache): {s3_key}")

        return (s3_key, download_url, url_expires_at)

    @classmethod
    def cleanup_old_filtered_excels(cls, shop_id: int, hours_old: int = 24):
        """Clean up old filtered Excel files (keep base case files)."""
        from services.models import ExcelGenerationTask
        from services.tasks import cleanup_old_pdfs_async

        try:
            cutoff_time = timezone.now() - timedelta(hours=hours_old)

            old_tasks = ExcelGenerationTask.objects.filter(
                shop_id=shop_id,
                status='completed',
                created_at__lt=cutoff_time,
                s3_key__contains='filtered_'
            ).values_list('s3_key', flat=True)

            if old_tasks:
                cleanup_old_pdfs_async.delay(list(old_tasks))

                ExcelGenerationTask.objects.filter(
                    shop_id=shop_id,
                    status='completed',
                    created_at__lt=cutoff_time,
                    s3_key__contains='filtered_'
                ).update(status='expired')

                logger.info(f"[Excel] Scheduled cleanup for {len(old_tasks)} old filtered Excels")

        except Exception as e:
            logger.error(f"[Excel] Cleanup error: {e}")
