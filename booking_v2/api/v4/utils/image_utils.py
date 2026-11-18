# booking_v2/api/v4/utils/image_utils.py
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
        # Validate URL
        if not is_safe_url(original_url):
            logger.warning(f"Unsafe URL rejected: {original_url}")
            return None

        # Download image with timeout
        response = requests.get(original_url, timeout=30)
        if response.status_code != 200:
            logger.error(f"Failed to download image. Status code: {response.status_code}")
            return None

        # Validate content type
        content_type = response.headers.get('content-type', '').lower()
        if not content_type.startswith('image/'):
            logger.error(f"Invalid content type: {content_type} for URL: {original_url}")
            return None

        # Determine file extension based on content type
        extension = content_type.split('/')[-1]  # e.g., 'jpeg', 'png'
        filename = f"booking_thumbnails/{shop_id}/{uuid.uuid4()}.{extension}"

        image_content = ContentFile(response.content)

        # Upload to S3
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
            ExtraArgs={"ContentType": content_type, "CacheControl": IMAGE_CACHE_CONTROL},
        )

        s3_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{filename}"
        logger.info(f"Successfully uploaded thumbnail to S3: {s3_url}")
        return s3_url

    except requests.exceptions.Timeout:
        logger.error(f"Timeout downloading image from {original_url}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed for {original_url}: {str(e)}")
    except Exception as e:
        logger.error(f"Thumbnail copy failed: {str(e)}", exc_info=True)

    return None


def is_safe_url(url):
    try:
        if not url or not isinstance(url, str):
            return False

        if not hasattr(settings, 'ALLOWED_IMAGE_DOMAINS'):
            logger.warning("ALLOWED_IMAGE_DOMAINS not configured")
            return False

        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.netloc in settings.ALLOWED_IMAGE_DOMAINS
    except Exception as e:
        logger.error(f"Error validating URL {url}: {str(e)}")
        return False
