import uuid
import boto3
from django.conf import settings
from django.utils.text import slugify


def get_s3_client():
    return boto3.client(
        "s3",
        region_name=settings.AWS_S3_REGION_NAME,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


def generate_presigned_upload_url(
    key,
    content_type,
    expires_in=900,
    cache_control=None,
):
    s3_client = get_s3_client()

    upload_url = s3_client.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
            "Key": key,
            "ContentType": content_type,
            # Signed: the browser PUT must then send the same Cache-Control header.
            **({"CacheControl": cache_control} if cache_control else {}),
        },
        ExpiresIn=expires_in,
    )

    # Standard S3 virtual-hosted style URL format
    bucket_name = settings.AWS_STORAGE_BUCKET_NAME
    region = settings.AWS_S3_REGION_NAME

    if region == "us-east-1":
        file_url = f"https://{bucket_name}.s3.amazonaws.com/{key}"
    else:
        file_url = f"https://{bucket_name}.s3.{region}.amazonaws.com/{key}"

    return {
        "upload_url": upload_url,
        "file_url": file_url,
        "key": key,
    }





def sanitize_s3_name(value):
    """Convert a value into lowercase underscore-separated text."""
    value = str(value or "").strip()
    return slugify(value, allow_unicode=False).replace("-", "_") or "unnamed"


def generate_media_key(
    category_name,
    product_name,
    product_code,
    extension,
):
    category_folder = sanitize_s3_name(category_name)
    clean_product_name = sanitize_s3_name(product_name)
    clean_product_code = sanitize_s3_name(product_code)
    clean_extension = str(extension).lstrip(".").lower()

    unique_id = uuid.uuid4().hex

    filename = (
        f"{clean_product_name}_"
        f"{clean_product_code}_"
        f"{unique_id}."
        f"{clean_extension}"
    )

    return (
        f"marketplace/product/"
        f"{category_folder}/"
        f"{clean_product_code}/"
        f"{filename}"
    )

def delete_s3_object(key):
    if not key:
        return

    s3_client = get_s3_client()

    try:
        s3_client.delete_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=key,
        )
    except Exception as e:
        # Prevent failure during metadata cleanup if object is already missing
        pass