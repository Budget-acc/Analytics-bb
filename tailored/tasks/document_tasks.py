from celery import shared_task
import boto3
import logging
import os
import uuid
from io import BytesIO
from PIL import Image
from django.conf import settings
from ERP.utils.s3_cache_control import PRIVATE_IMAGE_CACHE_CONTROL
from urllib.parse import urlparse

logger = logging.getLogger("api_requests")


def cleanup_orphaned_temp_files(file_data):
    """Clean up any temp files that still exist."""
    cleaned = 0
    for file_info in file_data:
        temp_path = file_info["path"]
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
                cleaned += 1
            except Exception as e:
                logger.error(f"Failed to cleanup orphaned file {temp_path}: {str(e)}")

    if cleaned > 0:
        logger.info(f"Cleaned up {cleaned} orphaned temp files")


@shared_task(
    name="tailored.tasks.document_tasks.cleanup_s3_tailor_order_documents_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def cleanup_s3_tailor_order_documents_task(self, document_urls):
    """
    Asynchronously delete tailor order documents from S3.
    """
    if not document_urls:
        logger.info("[S3 Cleanup] No tailor order documents to delete")
        return {"status": "skipped", "deleted": 0, "failed": 0}

    deleted_count = 0
    failed_urls = []

    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
        )

        bucket = settings.AWS_STORAGE_BUCKET_NAME
        expected_host = f"{bucket}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com"

        for url in document_urls:
            try:
                parsed = urlparse(url)

                if parsed.netloc != expected_host:
                    logger.warning(
                        f"[S3 Cleanup] Skipping deletion: URL {url} doesn't match expected bucket host {expected_host}"
                    )
                    failed_urls.append({"url": url, "reason": "invalid_bucket"})
                    continue

                key = parsed.path.lstrip("/")

                if not key:
                    logger.warning(f"[S3 Cleanup] Skipping deletion: No key found in URL {url}")
                    failed_urls.append({"url": url, "reason": "no_key"})
                    continue

                s3.delete_object(Bucket=bucket, Key=key)
                deleted_count += 1
                logger.info(f"[S3 Cleanup] Successfully deleted S3 object: {key}")

            except Exception as e:
                logger.error(f"[S3 Cleanup] Failed to delete S3 document {url}: {str(e)}", exc_info=True)
                failed_urls.append({"url": url, "reason": str(e)})

        logger.info(
            f"[S3 Cleanup] Completed: {deleted_count} deleted, {len(failed_urls)} failed out of {len(document_urls)} total"
        )

        return {
            "status": "completed",
            "total": len(document_urls),
            "deleted": deleted_count,
            "failed": len(failed_urls),
            "failed_urls": failed_urls,
        }

    except Exception as e:
        logger.error(f"[S3 Cleanup] Failed to initialize S3 client: {str(e)}", exc_info=True)

        try:
            raise self.retry(exc=e)
        except self.MaxRetriesExceededError:
            logger.error(f"[S3 Cleanup] Max retries exceeded for {len(document_urls)} documents")
            return {
                "status": "failed",
                "total": len(document_urls),
                "deleted": 0,
                "failed": len(document_urls),
                "error": str(e),
            }


@shared_task(
    name="tailored.tasks.document_tasks.upload_tailor_order_documents_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def upload_tailor_order_documents_task(self, order_id, file_data, shop_id, user_id):
    """
    Async task to convert temp document files to WebP and upload them to S3,
    with verified upload before deleting each temp file.
    """
    from tailored.models import TailorOrder

    try:
        order = TailorOrder.objects.get(id=order_id)
        uploaded_urls = []

        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
        )

        for file_info in file_data:
            temp_path = file_info["path"]
            try:
                if not os.path.exists(temp_path):
                    logger.error(f"Temp file does not exist: {temp_path}")
                    continue

                key = f"tailor_orders/{order_id}/documents/{uuid.uuid4()}.webp"

                try:
                    with Image.open(temp_path) as img:
                        img.load()
                        if img.mode in ("RGBA", "LA", "P"):
                            img = img.convert("RGBA")
                        else:
                            img = img.convert("RGB")

                        if img.width > 2000 or img.height > 2000:
                            img.thumbnail((2000, 2000), Image.Resampling.LANCZOS)

                        buffer = BytesIO()
                        img.save(buffer, format="WEBP", quality=85, optimize=True)
                        buffer.seek(0)
                except Exception as conv_error:
                    logger.error(f"Failed to convert {temp_path} to WebP: {str(conv_error)}", exc_info=True)
                    continue

                s3.upload_fileobj(
                    buffer,
                    settings.AWS_STORAGE_BUCKET_NAME,
                    key,
                    ExtraArgs={
                        "ContentType": "image/webp",
                        "ACL": "private",
                        "CacheControl": PRIVATE_IMAGE_CACHE_CONTROL,
                    },
                )

                try:
                    s3.head_object(
                        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                        Key=key,
                    )

                    s3_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{key}"
                    uploaded_urls.append(s3_url)

                    logger.info(f"Verified S3 upload: {s3_url}")

                    os.unlink(temp_path)
                    logger.info(f"Deleted verified temp file: {temp_path}")

                except Exception as verify_error:
                    logger.error(f"S3 verification failed for {key}: {str(verify_error)}")
                    continue

            except Exception as e:
                logger.error(
                    f"Failed to process document {file_info['original_name']}: {str(e)}",
                    exc_info=True,
                )

        if uploaded_urls:
            existing_docs = order.documents or []
            order.documents = existing_docs + uploaded_urls

            details = order.documents_details or {}
            total_count = details.get("total_count")
            if not isinstance(total_count, int):
                total_count = len(existing_docs) + len(file_data or [])

            uploaded_count = details.get("uploaded_count")
            if not isinstance(uploaded_count, int):
                uploaded_count = len(existing_docs)

            uploaded_count = uploaded_count + len(uploaded_urls)
            if uploaded_count > total_count:
                uploaded_count = total_count

            status = "completed" if uploaded_count >= total_count else "uploading"
            order.documents_details = {
                "status": status,
                "total_count": total_count,
                "uploaded_count": uploaded_count,
            }

            order.save(update_fields=["documents", "documents_details"])

        cleanup_orphaned_temp_files(file_data)

        return {"order_id": order_id, "uploaded_count": len(uploaded_urls)}

    except Exception as e:
        logger.error(f"Task failed for tailor order {order_id}: {str(e)}")
        cleanup_orphaned_temp_files(file_data)
        raise self.retry(exc=e)
