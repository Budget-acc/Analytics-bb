import boto3
import uuid
import logging
from io import BytesIO
from PIL import Image
from django.conf import settings
from ERP.utils.s3_cache_control import IMAGE_CACHE_CONTROL

logger = logging.getLogger("api_requests")


def _upload_image_to_s3(self, image_file, shop_name, order_item_id):
    """
    Upload a single image to S3 for customized orders.
    Returns the image URL or raises ValueError.

    Path format: shop_name/customized_order/{order_item_id}_{uuid}.png
    """
    # Validate file size (max 3MB)
    if image_file.size > 3 * 1024 * 1024:
        raise ValueError("Image file size must be less than 3MB.")

    # Validate and open image
    try:
        img = Image.open(image_file)
        img = img.convert("RGBA")
    except Exception as e:
        logger.error(f"Image validation error: {str(e)}", exc_info=True)
        raise ValueError("Invalid or unsupported image format.")

    # Prepare filename
    # shop_name is the shop's stored S3 folder (Shop.s3_folder) - used as-is.
    shop_folder = shop_name or "shop"
    filename = f"{shop_folder}/customized_order/{order_item_id}_{uuid.uuid4().hex}.png"

    # Save image to BytesIO
    img_io = BytesIO()
    img.save(img_io, format="PNG")
    img_io.seek(0)

    #  Upload to S3
    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
        )
        s3.upload_fileobj(
            img_io,
            settings.AWS_STORAGE_BUCKET_NAME,
            filename,
            ExtraArgs={"ContentType": "image/png", "CacheControl": IMAGE_CACHE_CONTROL}
        )
    except Exception as e:
        logger.error(f"S3 upload failed: {str(e)}", exc_info=True)
        raise ValueError("Failed to upload image. Please try again.")

    # Generate and return image URL
    bucket = settings.AWS_STORAGE_BUCKET_NAME
    region = settings.AWS_S3_REGION_NAME
    image_url = f"https://{bucket}.s3.{region}.amazonaws.com/{filename}"
    return image_url
