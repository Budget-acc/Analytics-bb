# booking_v2/api/v5/utils/image_utils.py
import uuid
import requests, mimetypes
from botocore.exceptions import ClientError
from django.core.files.base import ContentFile
import boto3
from django.conf import settings
from urllib.parse import urlparse
import logging
import hashlib

from shop.utils.s3_folder import legacy_s3_segment
from ERP.utils.s3_cache_control import IMAGE_CACHE_CONTROL, PRIVATE_IMAGE_CACHE_CONTROL

logger = logging.getLogger('api_requests')

def _thumbnail_version_from_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]

def _sanitize_shop_name(name: str) -> str:
    return legacy_s3_segment(name)

def copy_thumbnail_to_s3(original_url, shop_id, product_id, shop_name="", shop_folder=None):
    """
    Copy product thumbnail to S3 only if it doesn't already exist.
    Reuse existing file if already uploaded.

    shop_folder: the shop's stored S3 folder (Shop.s3_folder), used as-is.
    shop_name is the legacy fallback, sanitized into a folder segment.
    """
    try:
        # --- Validate URL ---
        if not is_safe_url(original_url):
            logger.warning(f"Unsafe or invalid URL rejected: {original_url}")
            return None

        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
        )

        # Try to determine file extension safely
        # Fallback to '.jpg' if undetectable
        extension = original_url.split('.')[-1].split('?')[0]
        if len(extension) > 5 or '/' in extension:
            extension = 'jpg'  # fallback for URLs without file extension

        if shop_folder:
            prefix = f"{shop_folder}/"
        else:
            prefix = f"{_sanitize_shop_name(shop_name)}/" if shop_name else ""
        version = _thumbnail_version_from_url(original_url)
        filename = f"{prefix}booking_thumbnails/{shop_id}/{product_id}_{version}.{extension}"
        s3_url = (
            f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3."
            f"{settings.AWS_S3_REGION_NAME}.amazonaws.com/{filename}"
        )

        # --- Check if file already exists in S3 ---
        try:
            s3.head_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=filename)
            logger.info(f"Thumbnail already exists in S3, reusing: {s3_url}")
            return s3_url
        except ClientError as e:
            if e.response["Error"]["Code"] != "404":
                logger.error(f"S3 check failed for {filename}: {str(e)}")
                return None
            # If 404 → proceed with upload

        # --- Download image ---
        response = requests.get(original_url, timeout=20)
        if response.status_code != 200:
            logger.error(f"Failed to download image (Status: {response.status_code}) from {original_url}")
            return None

        content_type = response.headers.get("content-type", "").lower()
        if not content_type.startswith("image/"):
            logger.error(f"Invalid content type: {content_type} for URL: {original_url}")
            return None

        # --- Derive extension from content-type (more reliable) ---
        guessed_ext = mimetypes.guess_extension(content_type) or ".jpg"
        if not filename.endswith(guessed_ext):
            filename = f"{prefix}booking_thumbnails/{shop_id}/{product_id}{guessed_ext}"
            s3_url = (
                f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3."
                f"{settings.AWS_S3_REGION_NAME}.amazonaws.com/{filename}"
            )

        # --- Upload image ---
        image_content = ContentFile(response.content)
        s3.upload_fileobj(
            image_content,
            settings.AWS_STORAGE_BUCKET_NAME,
            filename,
            ExtraArgs={"ContentType": content_type, "CacheControl": IMAGE_CACHE_CONTROL},
        )

        logger.info(f"Successfully uploaded thumbnail to S3: {s3_url}")
        return s3_url

    except requests.exceptions.Timeout:
        logger.error(f"Timeout while downloading image: {original_url}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed for {original_url}: {str(e)}")
    except ClientError as e:
        logger.error(f"S3 ClientError during upload: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error during thumbnail copy: {str(e)}", exc_info=True)

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
    

def upload_single_file_to_s3(file_obj, filename, content_type, shop_id, user_id, subfolder):
    """
    Upload single file to S3
    """
    import boto3
    from django.conf import settings
    from uuid import uuid4
    
    s3 = boto3.client(
        's3',
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME,
    )
    
    # Generate S3 key
    import os
    ext = os.path.splitext(filename)[1] or '.bin'
    key = f"{subfolder}/{uuid4()}{ext}"
    
    try:
        # Upload to S3
        s3.upload_fileobj(
            file_obj,
            settings.AWS_STORAGE_BUCKET_NAME,
            key,
            ExtraArgs={
                'ContentType': content_type,
                'ACL': 'private',  # Adjust as needed
                **({'CacheControl': PRIVATE_IMAGE_CACHE_CONTROL} if str(content_type).startswith('image/') else {}),
            }
        )
        
        # Generate URL
        url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{key}"
        return url
        
    except Exception as e:
        logger.error(f"Failed to upload to S3: {str(e)}")
        return None    
