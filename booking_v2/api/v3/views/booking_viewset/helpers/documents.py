from django.conf import settings
from urllib.parse import urlparse
import boto3
from botocore.exceptions import ClientError
import logging

logger = logging.getLogger("api_requests")


def _cleanup_booking_documents_sync(self, document_urls):
    """
    Synchronously delete booking documents from S3.
    Returns list of URLs that failed to delete.
    """
    if not document_urls:
        return []

    failed_urls = []
    deleted_count = 0

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
                # Parse URL safely
                parsed = urlparse(url)

                # Validate it's from our bucket
                if parsed.netloc != expected_host:
                    logger.warning(
                        f"[S3 Cleanup Sync] Skipping deletion: URL doesn't match expected bucket host"
                    )
                    failed_urls.append(url)
                    continue

                # Extract key from path
                key = parsed.path.lstrip('/')

                if not key:
                    logger.warning(f"[S3 Cleanup Sync] No key found in URL")
                    failed_urls.append(url)
                    continue

                # Try to delete from S3
                s3.delete_object(Bucket=bucket, Key=key)
                deleted_count += 1
                logger.info(f"[S3 Cleanup Sync] Successfully deleted: {key}")

            except ClientError as ce:
                if ce.response['Error']['Code'] == 'NoSuchKey':
                    # File doesn't exist - not a failure
                    logger.info(f"[S3 Cleanup Sync] Document already deleted: {url}")
                    deleted_count += 1
                else:
                    logger.error(f"[S3 Cleanup Sync] Failed to delete {url}: {str(ce)}")
                    failed_urls.append(url)
            except Exception as e:
                logger.error(f"[S3 Cleanup Sync] Failed to delete {url}: {str(e)}")
                failed_urls.append(url)

        logger.info(
            f"[S3 Cleanup Sync] Completed: {deleted_count} deleted, {len(failed_urls)} failed"
        )

        return failed_urls

    except Exception as e:
        logger.error(f"[S3 Cleanup Sync] Failed to initialize S3 client: {str(e)}")
        # If S3 client fails, all URLs failed
        return document_urls


def get_booking_effective_window(self, booking):
    """
    Returns effective stock blocking window.
    """

    pickup = booking.pickup_date
    return_dt = booking.return_date
    cooling = booking.cooling_period_end

    # Scenario A: Cooling before pickup
    if cooling and cooling < pickup:
        return cooling, return_dt

    # Scenario B: Cooling after return
    if cooling and return_dt and cooling > return_dt:
        return pickup, cooling

    # Scenario C: No cooling
    return pickup, return_dt
