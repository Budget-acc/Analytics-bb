# # pdf_snapshot.py
import hashlib
import json
import logging
from typing import Dict, Optional, Tuple
from django.db.models import Max, Q
from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
from services.models import Product, ProductVariant, ShopOwnerService

logger = logging.getLogger("api_requests")

class PDFSnapshotManager:
    """
    Manages PDF generation with caching ONLY for base case (no filters).
    Filtered searches generate new PDFs each time.
    """
    
    LAYOUT_VERSION = "v1"
    PRESIGNED_URL_HOURS = 24
    
    @classmethod
    def has_filters(cls, filters: Dict) -> bool:
        """Check if any search/filter parameters are present."""
        search = filters.get('search', '').strip()
        min_price = filters.get('min_price', '').strip()
        max_price = filters.get('max_price', '').strip()
        in_stock_only = filters.get('in_stock_only', False)
        
        # Return True if ANY filter is active
        return bool(
            search or 
            min_price or 
            max_price or 
            in_stock_only
        )
    
    @classmethod
    def compute_cache_key(
        cls, 
        shop_id: int, 
        service_ids: list, 
        filters: Dict
    ) -> str:
        """
        Compute cache key. 
        If filters exist, return None (no caching).
        If no filters, return version hash for base case.
        """
        # If ANY filters are active, disable caching
        if cls.has_filters(filters):
            logger.info(f"[PDF] Filters active - caching DISABLED")
            return None
        
        # Only cache base case (no filters)
        try:
            sorted_service_ids = sorted(service_ids)
            
            product_updated_ts = Product.objects.filter(
                shop_service_id__in=service_ids,
                is_active=True
            ).aggregate(
                max_updated=Max('updated_at')
            )['max_updated']
            
            variant_structure_ts = ProductVariant.objects.filter(
                product__shop_service_id__in=service_ids,
                is_active=True
            ).aggregate(
                max_updated=Max('structure_updated_at')
            )['max_updated']
            
            variant_quantity_ts = ProductVariant.objects.filter(
                product__shop_service_id__in=service_ids,
                is_active=True
            ).aggregate(
                max_updated=Max('quantity_updated_at')
            )['max_updated']
            
            # Base case version (no filters)
            version_input = (
                f"{sorted_service_ids}|"
                f"{product_updated_ts}|"
                f"{variant_structure_ts}|"
                f"{variant_quantity_ts}|"
                f"{cls.LAYOUT_VERSION}"
            )
            
            cache_key = hashlib.md5(version_input.encode()).hexdigest()[:12]
            logger.info(f"[PDF] Base case cache key: {cache_key}")
            return cache_key
            
        except Exception as e:
            logger.error(f"[PDF] Cache key error: {e}")
            return None
    
    @classmethod
    def compute_request_hash(cls, request_params: Dict) -> str:
        """Compute hash for request parameters (for filtered requests lock)."""
        try:
            # Create consistent string from request params
            params_to_hash = {
                'shop_service_id': request_params.get('shop_service_id', ''),
                'search': request_params.get('search', ''),
                'search_by': request_params.get('search_by', 'name'),
                'in_stock_only': request_params.get('in_stock_only', False),
                'min_price': request_params.get('min_price', ''),
                'max_price': request_params.get('max_price', ''),
            }
            
            params_str = json.dumps(params_to_hash, sort_keys=True)
            request_hash = hashlib.md5(params_str.encode()).hexdigest()[:12]
            logger.info(f"[PDF] Request hash: {request_hash}")
            return request_hash
            
        except Exception as e:
            logger.error(f"[PDF] Request hash error: {e}")
            import time
            return hashlib.md5(str(time.time()).encode()).hexdigest()[:12]
    
    @classmethod
    def get_s3_key(
        cls,
        shop_id: int,
        cache_key: Optional[str],
        request_hash: Optional[str] = None
    ) -> str:
        """Generate S3 key. If no cache_key, use request_hash for filtered."""
        if cache_key:
            # Cached version (base case)
            return f"pdf_reports/{shop_id}/cached_{cache_key}.pdf"
        else:
            # Unique version for filtered requests
            if request_hash:
                return f"pdf_reports/{shop_id}/filtered_{request_hash}.pdf"
            else:
                timestamp = timezone.now().strftime("%Y%m%d_%H%M%S_%f")
                return f"pdf_reports/{shop_id}/filtered_{timestamp}.pdf"
    
    @classmethod
    def check_cached_pdf(
        cls,
        shop_id: int,
        cache_key: Optional[str]
    ) -> Optional[Tuple[str, str]]:
        """
        Check if cached PDF exists.
        Only checks cache for base case (no filters).
        """
        from services.s3_service import S3Service
        from services.models import PDFGenerationTask
        
        # If no cache_key (filtered request), always generate new
        if not cache_key:
            logger.info(f"[PDF] Filtered request - always generate new")
            return None
        
        s3_key = cls.get_s3_key(shop_id, cache_key)
        
        # Check if PDF exists in S3
        if not S3Service.file_exists(s3_key):
            logger.info(f"[PDF] Cache miss: {s3_key}")
            return None
        
        # Find the task that created this PDF
        pdf_task = PDFGenerationTask.objects.filter(
            shop_id=shop_id,
            s3_key=s3_key,
            status='completed'
        ).order_by('-completed_at').first()
        
        if not pdf_task:
            logger.warning(f"[PDF] PDF exists but no task record: {s3_key}")
            url = S3Service.get_presigned_url(s3_key, expires_in_hours=cls.PRESIGNED_URL_HOURS)
            return (s3_key, url)
        
        # Check if URL needs refresh
        if pdf_task.is_url_expired():
            logger.info(f"[PDF] Refreshing expired URL: {s3_key}")
            new_url = S3Service.get_presigned_url(s3_key, expires_in_hours=cls.PRESIGNED_URL_HOURS)
            
            # Atomic update to prevent race conditions
            from django.db.models import F
            PDFGenerationTask.objects.filter(
                id=pdf_task.id,
                url_expires_at__lt=timezone.now()
            ).update(
                download_url=new_url,
                url_expires_at=timezone.now() + timedelta(hours=cls.PRESIGNED_URL_HOURS)
            )
            
            if PDFGenerationTask.objects.filter(id=pdf_task.id).exists():
                pdf_task.refresh_from_db()
            
            return (s3_key, new_url)
        
        logger.info(f"[PDF] Using cached PDF: {s3_key}")
        return (s3_key, pdf_task.download_url)
    
    @classmethod
    def save_pdf(
        cls,
        shop_id: int,
        cache_key: Optional[str],
        request_hash: Optional[str],
        pdf_bytes: bytes,
        is_base_case: bool
    ) -> Tuple[str, str, timezone.datetime]:
        """
        Save PDF to S3.
        Returns: (s3_key, download_url, url_expires_at)
        """
        from services.s3_service import S3Service
        
        s3_key = cls.get_s3_key(shop_id, cache_key, request_hash)
        download_url = S3Service.upload_pdf(
            s3_key, 
            pdf_bytes, 
            expires_in_hours=cls.PRESIGNED_URL_HOURS
        )
        
        url_expires_at = timezone.now() + timedelta(hours=cls.PRESIGNED_URL_HOURS)
        
        if is_base_case:
            logger.info(f"[PDF] Saved base case PDF: {s3_key}")
        else:
            logger.info(f"[PDF] Saved filtered PDF (no cache): {s3_key}")
        
        return (s3_key, download_url, url_expires_at)
    
    @classmethod
    def cleanup_old_filtered_pdfs(cls, shop_id: int, hours_old: int = 24):
        """Clean up old filtered PDFs (keep base case PDFs)."""
        from services.s3_service import S3Service
        from services.models import PDFGenerationTask
        from django.utils import timezone
        from datetime import timedelta
        
        try:
            cutoff_time = timezone.now() - timedelta(hours=hours_old)
            
            # Find old filtered PDF tasks
            old_tasks = PDFGenerationTask.objects.filter(
                shop_id=shop_id,
                status='completed',
                created_at__lt=cutoff_time,
                s3_key__contains='filtered_'  # Only filtered PDFs
            ).values_list('s3_key', flat=True)
            
            if old_tasks:
                # Schedule async cleanup
                from services.tasks import cleanup_old_pdfs_async
                cleanup_old_pdfs_async.delay(list(old_tasks))
                
                # Mark tasks as expired
                PDFGenerationTask.objects.filter(
                    shop_id=shop_id,
                    status='completed',
                    created_at__lt=cutoff_time,
                    s3_key__contains='filtered_'
                ).update(status='expired')
                
                logger.info(f"[PDF] Scheduled cleanup for {len(old_tasks)} old filtered PDFs")
                    
        except Exception as e:
            logger.error(f"[PDF] Cleanup error: {e}")