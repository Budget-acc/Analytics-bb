from PIL import Image
import boto3
import uuid
from io import BytesIO
from django.conf import settings
from ERP.utils.s3_cache_control import IMAGE_CACHE_CONTROL

MAX_SIZE_BYTES = 3 * 1024 * 1024   # 3 MB — matches shop upload standard
THUMBNAIL_MAX_DIM = 300             # px, longest edge
SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "GIF", "TIFF"}


def upload_org_image(image_file, org_name: str):
    """
    Validate, resize (max 300px on the longest edge), convert to WebP, and upload to S3.

    Returns:
        (url: str, None)   on success
        (None, error: str) on validation or upload failure
    """
    # Size check
    if image_file.size > MAX_SIZE_BYTES:
        return None, "Image must be less than 3MB."

    # Open and validate format
    try:
        img = Image.open(image_file)
        img.verify()            # detect corrupt / truncated files
        image_file.seek(0)
        img = Image.open(image_file)    # must reopen after verify()
        if img.format not in SUPPORTED_FORMATS:
            return None, "Invalid or unsupported image format."
    except Exception:
        return None, "Invalid or unsupported image format."

    # Resize — Image.thumbnail() shrinks in-place maintaining aspect ratio;
    # does nothing if already within bounds.
    img = img.convert("RGB")   # WebP encoder requires RGB or RGBA; normalises palette/P modes
    img.thumbnail((THUMBNAIL_MAX_DIM, THUMBNAIL_MAX_DIM), Image.LANCZOS)

    # Encode as WebP
    img_io = BytesIO()
    img.save(img_io, format="WEBP", quality=85)
    img_io.seek(0)

    # S3 path: {org_name}/thumbnails/{uuid}.webp
    safe_name = org_name.replace(" ", "_")
    filename = f"{safe_name}/thumbnails/{uuid.uuid4().hex}.webp"

    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME,
    )
    try:
        s3.upload_fileobj(
            img_io,
            settings.AWS_STORAGE_BUCKET_NAME,
            filename,
            ExtraArgs={"ContentType": "image/webp", "CacheControl": IMAGE_CACHE_CONTROL},
        )
    except Exception:
        return None, "Failed to upload image. Please try again."

    bucket = settings.AWS_STORAGE_BUCKET_NAME
    region = settings.AWS_S3_REGION_NAME
    url = f"https://{bucket}.s3.{region}.amazonaws.com/{filename}"
    return url, None
