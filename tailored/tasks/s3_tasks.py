from celery import shared_task
import boto3
from django.conf import settings
import logging
from urllib.parse import urlparse, unquote

logger = logging.getLogger("api_requests")

@shared_task(name='tailored.tasks.delete_order_images_from_s3')
def delete_order_images_from_s3(image_urls):
    """
    Delete images from S3 asynchronously with robust key extraction.
    Handles:
      - Virtual-hosted style (https://bucket.s3.region.amazonaws.com/key)
      - Path-style (https://s3.region.amazonaws.com/bucket/key)
      - s3://bucket/key format
      - Custom CDN/CloudFront URLs (with query params)
      - Presigned URLs
    """
    if not image_urls:
        return

    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME,
    )
    bucket_name = settings.AWS_STORAGE_BUCKET_NAME

    for url in image_urls:
        try:
            key = extract_s3_key_from_url(url, bucket_name)
            if not key:
                logger.warning(f"Could not extract key from URL: {url}")
                continue

            s3.delete_object(Bucket=bucket_name, Key=key)
            logger.info(f"Deleted S3 image: {url}")

        except Exception as e:
            logger.error(f"Failed to delete S3 image {url}: {str(e)}", exc_info=True)


def extract_s3_key_from_url(url, bucket_name):
    """
    Extract object key from various S3 URL formats.
    """
    if not url:
        return None

    # Case 1: s3://bucket/key
    if url.startswith("s3://"):
        parsed = urlparse(url)
        if parsed.netloc == bucket_name:
            return unquote(parsed.path.lstrip("/"))
        return None

    # Case 2: Standard https/http URL
    parsed = urlparse(url)
    path = parsed.path.lstrip("/")
    netloc = parsed.netloc.lower()

    if netloc.startswith(bucket_name.lower() + ".s3"):
        return unquote(path)

    if netloc.startswith("s3.") and path.startswith(bucket_name.lower() + "/"):
        return unquote(path[len(bucket_name) + 1:])

    if "." in path:
        return unquote(path)

    # Last fallback: return None
    return None
