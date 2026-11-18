import boto3
import uuid
import requests
import logging
from io import BytesIO
from PIL import Image
from django.conf import settings
from ERP.utils.s3_cache_control import IMAGE_CACHE_CONTROL
from concurrent.futures import ThreadPoolExecutor
from booking_v2.api.v5.utils.image_utils import copy_thumbnail_to_s3

logger = logging.getLogger("api_requests")


def _process_images_parallel(self, validated_products, shop):
    """Process product images in parallel - handles both file uploads and existing URLs"""
    def process_single_image(product_data, index):
        image_file = product_data['image_file']
        image_url = product_data.get('image_url', '')
        thumbnail_url = product_data.get('thumbnail_url', '')

        # If image URL is provided, use it directly (no upload needed)
        if image_url and image_url.startswith('http'):
            logger.info(f"Processing product {index+1} with image URL: {image_url}")
            # If thumbnail URL is also provided, use both
            if thumbnail_url and thumbnail_url.startswith('http'):
                return index, image_url, thumbnail_url, None
            else:
                # Generate thumbnail from existing image URL
                try:
                    # Download image and create thumbnail
                    response = requests.get(image_url, timeout=30)
                    if response.status_code == 200:
                        img = Image.open(BytesIO(response.content))
                        img = img.convert("RGBA")

                        # Create thumbnail
                        thumb_io = BytesIO()
                        thumb_img = img.copy()
                        thumb_img.thumbnail((100, 100), Image.Resampling.LANCZOS)
                        thumb_img.save(thumb_io, format="PNG", optimize=True)
                        thumb_io.seek(0)

                        # Upload thumbnail to S3
                        shop_name = shop.s3_folder
                        service_name = (product_data['shop_service'].general_service.name or "general").replace(" ", "_")
                        product_name_clean = (product_data['name'] or f"product_{uuid.uuid4()}").replace(" ", "_")

                        thumb_filename = f"{shop_name}/product_images/{service_name}/{product_name_clean}_thumb_{uuid.uuid4().hex}.png"

                        s3 = boto3.client(
                            "s3",
                            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                            region_name=settings.AWS_S3_REGION_NAME,
                        )

                        s3.upload_fileobj(thumb_io, settings.AWS_STORAGE_BUCKET_NAME, thumb_filename,
                                        ExtraArgs={"ContentType": "image/png", "CacheControl": IMAGE_CACHE_CONTROL})

                        bucket = settings.AWS_STORAGE_BUCKET_NAME
                        region = settings.AWS_S3_REGION_NAME
                        thumb_url = f"https://{bucket}.s3.{region}.amazonaws.com/{thumb_filename}"

                        logger.info(f"Successfully created thumbnail for product {index+1} from URL")

                        return index, image_url, thumb_url, None
                    else:
                        logger.warning(f"Failed to download image from URL: {image_url}, status: {response.status_code}")
                        return index, image_url, "", f"Failed to download image from URL: {image_url}"

                except Exception as e:
                    logger.error(f"Thumbnail creation from URL failed: {str(e)}")
                    return index, image_url, "", f"Failed to create thumbnail from URL: {str(e)}"

        # If file is uploaded, process it (original logic)
        elif image_file:
            # Validate file size
            if image_file.size > 3 * 1024 * 1024:
                return index, None, None, f"Product {index+1}: Image must be less than 3MB."

            try:
                # Validate and open image
                img = Image.open(image_file)
                img = img.convert("RGBA")
            except Exception as e:
                logger.error(f"Image processing error for product {index+1}: {str(e)}")
                return index, None, None, f"Product {index+1}: Invalid image format."

            # Generate filenames
            shop_name = shop.s3_folder
            service_name = (product_data['shop_service'].general_service.name or "general").replace(" ", "_")
            product_name_clean = (product_data['name'] or f"product_{uuid.uuid4()}").replace(" ", "_")

            filename = f"{shop_name}/product_images/{service_name}/{product_name_clean}_{uuid.uuid4().hex}.png"
            thumb_filename = f"{shop_name}/product_images/{service_name}/{product_name_clean}_thumb_{uuid.uuid4().hex}.png"

            try:
                # Save main image
                main_io = BytesIO()
                img.save(main_io, format="PNG")
                main_io.seek(0)

                # Save thumbnail
                thumb_io = BytesIO()
                thumb_img = img.copy()
                thumb_img.thumbnail((100, 100), Image.Resampling.LANCZOS)
                thumb_img.save(thumb_io, format="PNG", optimize=True)
                thumb_io.seek(0)

                # Upload to S3
                s3 = boto3.client(
                    "s3",
                    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                    region_name=settings.AWS_S3_REGION_NAME,
                )

                s3.upload_fileobj(main_io, settings.AWS_STORAGE_BUCKET_NAME, filename,
                                ExtraArgs={"ContentType": "image/png", "CacheControl": IMAGE_CACHE_CONTROL})
                s3.upload_fileobj(thumb_io, settings.AWS_STORAGE_BUCKET_NAME, thumb_filename,
                                ExtraArgs={"ContentType": "image/png", "CacheControl": IMAGE_CACHE_CONTROL})

                # Generate URLs
                bucket = settings.AWS_STORAGE_BUCKET_NAME
                region = settings.AWS_S3_REGION_NAME
                image_url = f"https://{bucket}.s3.{region}.amazonaws.com/{filename}"
                thumb_url = f"https://{bucket}.s3.{region}.amazonaws.com/{thumb_filename}"

                logger.info(f"Successfully uploaded images for product {index+1}: {filename}")
                return index, image_url, thumb_url, None

            except Exception as e:
                logger.error(f"S3 upload failed for product {index+1}: {str(e)}")
                return index, None, None, f"Product {index+1}: Failed to upload image."

        # No image provided - return empty strings
        else:
            return index, "", "", None

    # Process images in parallel (max 3 concurrent uploads)
    results = [None] * len(validated_products)

    if len(validated_products) == 0:
        return results

    with ThreadPoolExecutor(max_workers=min(3, len(validated_products))) as executor:
        futures = [
            executor.submit(process_single_image, product_data, i)
            for i, product_data in enumerate(validated_products)
        ]

        for future in futures:
            try:
                index, image_url, thumb_url, error = future.result()
                results[index] = (image_url, thumb_url, error)
            except Exception as e:
                logger.error(f"Unexpected error in image processing: {str(e)}")
                # Mark this index as failed
                for i in range(len(results)):
                    if results[i] is None:
                        results[i] = (None, None, f"Unexpected error: {str(e)}")
                        break

    return results


def _process_thumbnails_parallel(self, thumbnail_tasks):
    """
    Process S3 thumbnail backups in parallel for booking creation.

    Args:
        thumbnail_tasks: List of tuples (variant_id, original_url, shop_id, product_id)

    Returns:
        dict: {variant_id: backup_s3_url}
    """
    def copy_single_thumbnail(task):
        variant_id, original_url, shop_id, product_id = task
        try:
            # Use the existing utility function from booking_v2
            backup_url = copy_thumbnail_to_s3(original_url, shop_id, product_id)
            return variant_id, backup_url or ""
        except Exception as e:
            logger.warning(f"Failed to backup thumbnail for variant {variant_id}: {str(e)}")
            return variant_id, ""

    # Process thumbnails in parallel (max 3 concurrent)
    variant_thumbnail_map = {}

    if thumbnail_tasks:
        with ThreadPoolExecutor(max_workers=min(3, len(thumbnail_tasks))) as executor:
            futures = [
                executor.submit(copy_single_thumbnail, task)
                for task in thumbnail_tasks
            ]

            for future in futures:
                variant_id, backup_url = future.result()
                variant_thumbnail_map[variant_id] = backup_url

    return variant_thumbnail_map
