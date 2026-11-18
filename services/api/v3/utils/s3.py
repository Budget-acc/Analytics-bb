
from django.conf import settings
from ERP.utils.s3_cache_control import IMAGE_CACHE_CONTROL
import boto3
import uuid
from io import BytesIO
from PIL import Image


def delete_s3_file(file_url):
    try:
        bucket_name = settings.AWS_STORAGE_BUCKET_NAME
        region = settings.AWS_S3_REGION_NAME

        # Extract key from URL
        key = file_url.split(f"s3.{region}.amazonaws.com/")[-1]

        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=region,
        )
        s3.delete_object(Bucket=bucket_name, Key=key)
    except Exception as e:
        print("Failed to delete old image from S3:", str(e))

def upload_image_to_s3(file, path_prefix, max_size=(200, 200)):
    """
    Upload an image to AWS S3 after optional resizing and compression.
    - Resizes if larger than max_size
    - Compresses PNG with optimize flag
    - Returns the S3 image URL
    """
    if not file:
        raise ValueError("No image file provided.")

    if file.size > 3 * 1024 * 1024:
        raise ValueError("Image must be less than 3MB.")

    try:
        img = Image.open(file)
        img = img.convert("RGBA")

        # Resize only if larger than max_size
        if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
            img.thumbnail(max_size, Image.LANCZOS)  # Keeps aspect ratio

    except Exception:
        raise ValueError("Invalid or unsupported image format.")

    filename = f"{path_prefix}/{uuid.uuid4().hex}.png"

    buffer = BytesIO()


    img.save(buffer, format="PNG")
    buffer.seek(0)

    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME,
    )

    bucket = settings.AWS_STORAGE_BUCKET_NAME

    s3.upload_fileobj(buffer, bucket, filename, ExtraArgs={"ContentType": "image/png", "CacheControl": IMAGE_CACHE_CONTROL})

    return f"https://{bucket}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{filename}"


def upload_product_image_with_thumbnail_to_s3(file, path_prefix):
    """
    Upload a product image and thumbnail to AWS S3.
    Returns (image_url, thumbnail_url).
    """
    if not file:
        raise ValueError("No image file provided.")

    if file.size > 3 * 1024 * 1024:
        raise ValueError("Image must be less than 3MB.")

    try:
        img = Image.open(file)
        img = img.convert("RGBA")
    except Exception:
        raise ValueError("Invalid or unsupported image format.")

    uid = uuid.uuid4().hex
    image_name = f"{path_prefix}/{uid}.png"
    thumb_name = f"{path_prefix}/{uid}_thumb.png"

    # Prepare original image
    main_io = BytesIO()
    img.save(main_io, format="PNG")
    main_io.seek(0)

    # Prepare thumbnail
    thumb_io = BytesIO()
    thumb = img.copy()
    thumb.thumbnail((100, 100), Image.Resampling.LANCZOS)
    thumb.save(thumb_io, format="PNG", optimize=True)
    thumb_io.seek(0)

    # Upload both
    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME,
    )

    bucket = settings.AWS_STORAGE_BUCKET_NAME

    s3.upload_fileobj(main_io, bucket, image_name, ExtraArgs={"ContentType": "image/png", "CacheControl": IMAGE_CACHE_CONTROL})
    s3.upload_fileobj(thumb_io, bucket, thumb_name, ExtraArgs={"ContentType": "image/png", "CacheControl": IMAGE_CACHE_CONTROL})

    base_url = f"https://{bucket}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com"
    return f"{base_url}/{image_name}", f"{base_url}/{thumb_name}"

def upload_thumbnail_from_bytesio_to_s3(image_file, path_prefix):
    """
    Accepts a BytesIO image and uploads a thumbnail version to AWS S3.
    Returns the thumbnail URL.
    """
    try:
        # print("[DEBUG] Opening image from BytesIO")
        img = Image.open(image_file)
        img = img.convert("RGBA")
        # print("[DEBUG] Image successfully opened and converted to RGBA")
    except Exception as e:
        # print(f"[ERROR] Failed to open image from BytesIO: {str(e)}")
        raise ValueError("Invalid or unsupported image format.")

    uid = uuid.uuid4().hex
    thumb_name = f"{path_prefix}/{uid}_thumb.png"
    # print(f"[DEBUG] Generated thumbnail path: {thumb_name}")

    # Prepare thumbnail
    try:
        thumb_io = BytesIO()
        thumb = img.copy()
        thumb.thumbnail((100, 100), Image.Resampling.LANCZOS)
        thumb.save(thumb_io, format="PNG", optimize=True)
        thumb_io.seek(0)
        # print("[DEBUG] Thumbnail image prepared successfully")
    except Exception as e:
        # print(f"[ERROR] Failed to create thumbnail image: {str(e)}")
        raise

    try:
        # print("[DEBUG] Uploading thumbnail to S3")
        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
        )

        bucket = settings.AWS_STORAGE_BUCKET_NAME
        s3.upload_fileobj(thumb_io, bucket, thumb_name, ExtraArgs={"ContentType": "image/png", "CacheControl": IMAGE_CACHE_CONTROL})
        # print("[DEBUG] Thumbnail uploaded successfully")
    except Exception as e:
        # print(f"[ERROR] Failed to upload thumbnail to S3: {str(e)}")
        raise

    url = f"https://{bucket}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{thumb_name}"
    # print(f"[DEBUG] Thumbnail URL: {url}")
    return url

# Shop with User
def upload_image_to_s3_with_resize(file, path_prefix, max_size=None):
    """
    Uploads an image to S3 with optional resizing.

    Args:
        file (InMemoryUploadedFile): The image file.
        path_prefix (str): Path prefix for S3 folder.
        max_size (tuple or None): (width, height). If None, skips resizing.

    Returns:
        str: Public URL of the uploaded image.

    Raises:
        ValueError: If file is invalid or upload fails.
    """
    if not file:
        raise ValueError("No image file provided.")

    if file.size > 3 * 1024 * 1024:
        raise ValueError("Image must be less than 3MB.")

    try:
        img = Image.open(file)
        img = img.convert("RGBA")

        # Resize only if max_size is provided and image is larger
        if max_size and (img.width > max_size[0] or img.height > max_size[1]):
            img.thumbnail(max_size, Image.LANCZOS)
    except Exception as e:
        raise ValueError("Invalid or unsupported image format.") from e

    # Generate file name
    filename = f"{path_prefix}/{uuid.uuid4().hex}.png"

    # Convert image to bytes
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
        )

        s3.upload_fileobj(
            buffer,
            settings.AWS_STORAGE_BUCKET_NAME,
            filename,
            ExtraArgs={"ContentType": "image/png", "CacheControl": IMAGE_CACHE_CONTROL}
        )

        return f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{filename}"

    except Exception as e:
        raise ValueError(f"Image upload to S3 failed: {str(e)}")
