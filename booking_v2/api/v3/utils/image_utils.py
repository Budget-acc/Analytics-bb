# booking_v2/api/v3/utils/image_utils.py
import uuid
import requests
from django.core.files.base import ContentFile
import boto3
from ERP.utils.s3_cache_control import IMAGE_CACHE_CONTROL
from django.conf import settings
from urllib.parse import urlparse
from django.conf import settings
import logging

logger = logging.getLogger('api_requests')


def copy_thumbnail_to_s3(original_url, shop_id):
    try:
        # Add URL validation
        if not is_safe_url(original_url):
            logger.warning(f"Unsafe URL rejected: {original_url}")
            return None

        # Add timeout to prevent hanging
        response = requests.get(original_url, timeout=30)
        if response.status_code == 200:
            # Add content type validation
            content_type = response.headers.get('content-type', '').lower()
            if not content_type.startswith('image/'):
                logger.error(f"Invalid content type: {content_type} for URL: {original_url}")
                return None

            image_content = ContentFile(response.content)
            filename = f"booking_thumbnails/{shop_id}/{uuid.uuid4()}.jpg"

            s3 = boto3.client(
                "s3",
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=settings.AWS_S3_REGION_NAME,
            )

            s3.upload_fileobj(
                image_content,
                settings.AWS_STORAGE_BUCKET_NAME,
                filename,
                ExtraArgs={"ContentType": "image/jpeg", "CacheControl": IMAGE_CACHE_CONTROL},
            )

            s3_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{filename}"
            logger.info(f"Successfully uploaded thumbnail to S3: {s3_url}")
            return s3_url
        else:
            logger.error(f"Failed to download image. Status code: {response.status_code}")

    except requests.exceptions.Timeout:
        logger.error(f"Timeout downloading image from {original_url}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed for {original_url}: {str(e)}")
    except Exception as e:
        logger.error(f"Thumbnail copy failed: {str(e)}", exc_info=True)
    return None


def is_safe_url(url):
    try:
        # Add input validation
        if not url or not isinstance(url, str):
            return False

        parsed = urlparse(url)

        # Check if ALLOWED_IMAGE_DOMAINS is configured
        if not hasattr(settings, 'ALLOWED_IMAGE_DOMAINS'):
            logger.warning("ALLOWED_IMAGE_DOMAINS not configured")
            return False

        return (
            parsed.scheme == "https"
            and parsed.netloc in settings.ALLOWED_IMAGE_DOMAINS
        )
    except Exception as e:
        logger.error(f"Error validating URL {url}: {str(e)}")
        return False
