import logging
import threading

from shop.utils.s3_folder import legacy_s3_segment

logger = logging.getLogger("api_requests")


def _sanitize_s3_segment(value: str, max_len: int = 50) -> str:
    """Replace non-alphanumeric characters (except _ and -) and truncate."""
    return legacy_s3_segment(value, max_len)


def _dispatch_image_task(product_id, raw_image_bytes, filename, thumb_filename):
    """
    Dispatch image processing off the request thread immediately.
    The daemon thread attempts Celery first; falls back to direct upload if broker unavailable.
    apply_async() is intentionally inside the thread - its broker connection timeout
    must never block the HTTP response.
    """
    from services.tasks import process_and_upload_product_image, _synchronous_image_upload_fallback

    def _worker():
        try:
            process_and_upload_product_image.apply_async(
                kwargs={
                    'product_id': product_id,
                    'raw_image_bytes': raw_image_bytes,
                    'filename': filename,
                    'thumb_filename': thumb_filename,
                },
                countdown=0,
            )
        except Exception as broker_exc:
            logger.warning(
                f"[Image Dispatch] Celery broker unavailable: {broker_exc}. Using direct upload fallback."
            )
            _synchronous_image_upload_fallback(product_id, raw_image_bytes, filename, thumb_filename)

    threading.Thread(target=_worker, daemon=True).start()


def _dispatch_thumbnail_task(product_id, image_url, thumb_filename):
    """
    Dispatch thumbnail generation for the pre-signed URL upload flow.
    Runs in a daemon thread — broker failures fall back to direct synchronous execution
    so the thumbnail is never permanently lost due to a Redis blip.
    """
    from services.tasks.image_tasks import generate_thumbnail_from_url, _synchronous_thumbnail_fallback

    def _worker():
        try:
            generate_thumbnail_from_url.delay(product_id, image_url, thumb_filename)
        except Exception as broker_exc:
            logger.warning(
                f"[Thumbnail Dispatch] Celery broker unavailable for product_id={product_id}: {broker_exc}. "
                f"Using direct fallback."
            )
            _synchronous_thumbnail_fallback(product_id, image_url, thumb_filename)

    threading.Thread(target=_worker, daemon=True).start()