# booking_v2/tasks/document_tasks.py
from celery import shared_task
import boto3
import logging
import os
import uuid
from pathlib import Path
from mimetypes import guess_extension
from django.conf import settings
from ERP.utils.s3_cache_control import PRIVATE_IMAGE_CACHE_CONTROL
from urllib.parse import urlparse
from .utils import cleanup_orphaned_temp_files

logger = logging.getLogger("api_requests")


@shared_task(
    name="booking_v2.tasks.document_tasks.cleanup_s3_documents_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def cleanup_s3_documents_task(self, document_urls):
    """
    Asynchronously delete booking documents from S3.

    Args:
        document_urls (list): List of S3 URLs to delete

    Returns:
        dict: Summary of deletion results

    Features:
        - Non-blocking deletion
        - Automatic retry on failure (max 3 attempts)
        - Detailed logging
        - Graceful error handling
    """
    if not document_urls:
        logger.info("[S3 Cleanup] No documents to delete")
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
                # Parse URL safely using urlparse
                parsed = urlparse(url)

                # Validate it's actually from our bucket
                if parsed.netloc != expected_host:
                    logger.warning(
                        f"[S3 Cleanup] Skipping deletion: URL {url} doesn't match expected bucket host {expected_host}"
                    )
                    failed_urls.append({"url": url, "reason": "invalid_bucket"})
                    continue

                # Extract key from path (remove leading slash)
                key = parsed.path.lstrip("/")

                if not key:
                    logger.warning(f"[S3 Cleanup] Skipping deletion: No key found in URL {url}")
                    failed_urls.append({"url": url, "reason": "no_key"})
                    continue

                # Delete from S3
                s3.delete_object(Bucket=bucket, Key=key)
                deleted_count += 1
                logger.info(f"[S3 Cleanup] Successfully deleted S3 object: {key}")

            except Exception as e:
                logger.error(f"[S3 Cleanup] Failed to delete S3 document {url}: {str(e)}", exc_info=True)
                failed_urls.append({"url": url, "reason": str(e)})

        # Log summary
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

        # Retry the task
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
    name="booking_v2.tasks.document_tasks.upload_booking_documents_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def upload_booking_documents_task(self, booking_id, file_data, shop_id, user_id):
    """
    Async task to upload documents to S3 with verified cleanup
    """
    from booking_v2.models import BookingV2

    try:
        booking = BookingV2.objects.get(id=booking_id)
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

                # Generate S3 key
                ext = Path(file_info["original_name"]).suffix

                if not ext and file_info.get("content_type"):
                    ext = guess_extension(file_info["content_type"])

                if not ext:
                    ext = ".bin"

                ext = ext.lower()

                key = f"bookings/{booking_id}/documents/{uuid.uuid4()}{ext}"

                # Upload with verification
                with open(temp_path, "rb") as f:
                    s3.upload_fileobj(
                        f,
                        settings.AWS_STORAGE_BUCKET_NAME,
                        key,
                        ExtraArgs={
                            "ContentType": file_info["content_type"],
                            "ACL": "private",
                            **({"CacheControl": PRIVATE_IMAGE_CACHE_CONTROL} if str(file_info["content_type"]).startswith("image/") else {}),
                        },
                    )

                # VERIFY the upload by checking S3
                try:
                    s3.head_object(
                        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                        Key=key,
                    )

                    # Generate URL
                    s3_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{key}"
                    uploaded_urls.append(s3_url)

                    logger.info(f"Verified S3 upload: {s3_url}")

                    # ONLY delete temp file after successful verification
                    os.unlink(temp_path)
                    logger.info(f"Deleted verified temp file: {temp_path}")

                except Exception as verify_error:
                    logger.error(f"S3 verification failed for {key}: {str(verify_error)}")
                    # Don't delete temp file - retry will handle it
                    continue

            except Exception as e:
                logger.error(
                    f"Failed to process document {file_info['original_name']}: {str(e)}",
                    exc_info=True,
                )
                # Don't delete on error - let retry mechanism handle it

        # Update booking: append uploaded URLs and update documents_details progress
        if uploaded_urls:
            existing_docs = booking.documents or []
            booking.documents = existing_docs + uploaded_urls

            details = booking.documents_details or {}
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
            booking.documents_details = {
                "status": status,
                "total_count": total_count,
                "uploaded_count": uploaded_count,
            }

            booking.save(update_fields=["documents", "documents_details"])

        # FINAL CLEANUP: Remove any leftover temp files
        cleanup_orphaned_temp_files(file_data)

        return {"booking_id": booking_id, "uploaded_count": len(uploaded_urls)}

    except Exception as e:
        logger.error(f"Task failed for booking {booking_id}: {str(e)}")
        # Cleanup on final failure
        cleanup_orphaned_temp_files(file_data)
        raise self.retry(exc=e)
