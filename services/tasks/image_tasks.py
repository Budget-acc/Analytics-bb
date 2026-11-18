import io
import logging
from urllib.parse import urlparse
from celery import shared_task
from ERP.utils.s3_cache_control import IMAGE_CACHE_CONTROL

logger = logging.getLogger("api_requests")


@shared_task(name='services.tasks.process_and_upload_product_image')
def process_and_upload_product_image(product_id, raw_image_bytes, filename, thumb_filename):
    """
    Celery task: encode raw image bytes as JPEG (full + thumbnail) and upload both to S3.
    Updates the product row with the final URLs when done.
    """
    try:
        from PIL import Image
        from services.models import Product
        from services.s3_service import S3Service
        from services.utils.image_processing_service import ImageProcessingService

        # Decode and convert to RGB JPEG
        img = Image.open(io.BytesIO(raw_image_bytes))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')

        # Full-size JPEG
        full_buf = io.BytesIO()
        img.save(full_buf, format='JPEG', quality=85, optimize=False)
        full_bytes = full_buf.getvalue()

        # Thumbnail JPEG (max 100x100, preserving aspect ratio)
        thumb_bytes = ImageProcessingService.process_thumbnail(img)

        # Upload both to S3 using the boto3 client directly (S3Service has no generic upload method)
        from django.conf import settings
        client = S3Service.get_client()
        bucket = settings.AWS_STORAGE_BUCKET_NAME

        client.put_object(
            Bucket=bucket,
            Key=filename,
            Body=full_bytes,
            ContentType='image/jpeg',
            CacheControl=IMAGE_CACHE_CONTROL,
        )
        full_url = f"https://{bucket}.s3.amazonaws.com/{filename}"

        client.put_object(
            Bucket=bucket,
            Key=thumb_filename,
            Body=thumb_bytes,
            ContentType='image/jpeg',
            CacheControl=IMAGE_CACHE_CONTROL,
        )
        thumb_url = f"https://{bucket}.s3.amazonaws.com/{thumb_filename}"

        # Update product row
        Product.objects.filter(pk=product_id).update(
            image=full_url,
            thumbnail_image=thumb_url,
        )

        logger.info(f"[Image Task] product_id={product_id} uploaded OK: {filename}")

    except Exception as e:
        logger.error(
            f"[Image Task] Failed for product_id={product_id}: {e}",
            exc_info=True,
        )
        raise


@shared_task(name='services.tasks.generate_thumbnail_from_url')
def generate_thumbnail_from_url(product_id, image_url, thumb_filename):
    """
    Celery task: download a product image already uploaded to S3 via pre-signed URL,
    generate a 100x100 thumbnail, upload it, and update the product row.
    Used by the pre-signed URL upload flow — the main image is already in S3.
    The existing process_and_upload_product_image task is NOT affected.
    """
    try:
        from PIL import Image
        from services.models import Product
        from services.s3_service import S3Service
        from services.utils.image_processing_service import ImageProcessingService

        from django.conf import settings

        bucket = settings.AWS_STORAGE_BUCKET_NAME
        client = S3Service.get_client()

        # Derive S3 key from the URL.
        # Handles virtual-hosted style with or without a region subdomain:
        #   https://<bucket>.s3.amazonaws.com/<key>
        #   https://<bucket>.s3.<region>.amazonaws.com/<key>
        # Also handles path-style:
        #   https://s3.amazonaws.com/<bucket>/<key>
        parsed = urlparse(image_url)
        hostname = parsed.hostname or ""
        path = parsed.path.lstrip("/")
        if hostname.startswith(f"{bucket}.s3"):
            # Virtual-hosted style (any region) — the path IS the key
            source_key = path
        elif f"/{bucket}/" in parsed.path:
            # Path-style URL — key follows the bucket segment
            source_key = parsed.path.split(f"/{bucket}/", 1)[-1]
        else:
            # Best-effort: use the path component (strips scheme + host)
            source_key = path
            logger.warning(
                f"[Thumbnail Task] Could not reliably parse S3 key from URL '{image_url}' "
                f"for bucket '{bucket}'. Using path component: '{source_key}'"
            )

        # Download the already-uploaded image
        response = client.get_object(Bucket=bucket, Key=source_key)
        raw_bytes = response["Body"].read()

        # Generate thumbnail
        thumb_bytes = ImageProcessingService.process_thumbnail(raw_bytes)

        # Upload thumbnail to S3
        client.put_object(
            Bucket=bucket,
            Key=thumb_filename,
            Body=thumb_bytes,
            ContentType="image/jpeg",
            CacheControl=IMAGE_CACHE_CONTROL,
        )
        thumb_url = f"https://{bucket}.s3.amazonaws.com/{thumb_filename}"

        # Update product row with thumbnail URL
        Product.objects.filter(pk=product_id).update(thumbnail_image=thumb_url)

        # Tag original image as permanent — product is confirmed saved in DB.
        # This protects it from the S3 lifecycle rule that deletes temporary objects.
        # Wrapped separately so a tagging failure never blocks the thumbnail result.
        try:
            client.put_object_tagging(
                Bucket=bucket,
                Key=source_key,
                Tagging={"TagSet": [{"Key": "status", "Value": "permanent"}]},
            )
            logger.info(f"[Thumbnail Task] product_id={product_id} image tagged permanent: {source_key}")
        except Exception as tag_exc:
            logger.warning(
                f"[Thumbnail Task] Tagging failed for product_id={product_id} key={source_key}: {tag_exc}"
            )

        logger.info(f"[Thumbnail Task] product_id={product_id} thumbnail uploaded: {thumb_filename}")

    except Exception as e:
        logger.error(
            f"[Thumbnail Task] Failed for product_id={product_id}: {e}",
            exc_info=True,
        )
        raise


def _synchronous_image_upload_fallback(product_id, raw_image_bytes, filename, thumb_filename):
    """
    Fallback for when the Celery broker is unreachable.
    Runs in a daemon thread — same logic as the Celery task but without retry support.
    """
    try:
        process_and_upload_product_image(product_id, raw_image_bytes, filename, thumb_filename)
    except Exception as e:
        logger.error(
            f"[Image Fallback] Failed for product_id={product_id}: {e}",
            exc_info=True,
        )


def _synchronous_thumbnail_fallback(product_id, image_url, thumb_filename):
    """
    Fallback for when the Celery broker is unreachable (pre-signed URL flow).
    Runs in a daemon thread — same logic as generate_thumbnail_from_url but without retry support.
    """
    try:
        generate_thumbnail_from_url(product_id, image_url, thumb_filename)
    except Exception as e:
        logger.error(
            f"[Thumbnail Fallback] Failed for product_id={product_id}: {e}",
            exc_info=True,
        )
