# booking_v2/tasks/thumbnail_tasks.py
from celery import shared_task
import logging

logger = logging.getLogger("api_requests")


@shared_task(name="booking_v2.tasks.thumbnail_tasks.generate_booking_thumbnails_task")
def generate_booking_thumbnails_task(booking_id):
    """
    Async task to copy thumbnails to S3 for all booked items in a booking
    """
    from booking_v2.models import BookedProductVariant
    from booking_v2.api.v5.utils.image_utils import copy_thumbnail_to_s3

    try:
        # Get all booked items for this booking
        items = BookedProductVariant.objects.filter(
            booking_id=booking_id,
            product_thumbnail__startswith="http",  # Only if has URL (not S3)
        ).select_related("product_variant__product", "shop")

        for item in items:
            if item.product_variant and item.product_variant.product.thumbnail_image:
                # Call the S3 copy function
                s3_url = copy_thumbnail_to_s3(
                    item.product_variant.product.thumbnail_image,
                    item.shop.id,
                    item.product_variant.product.id,
                    shop_folder=item.shop.s3_folder,
                )
                if s3_url:
                    item.product_thumbnail = s3_url
                    item.save(update_fields=["product_thumbnail"])

        logger.info(f"Generated thumbnails for booking {booking_id}: {len(items)} items")

    except Exception as e:
        logger.error(
            f"Failed to generate thumbnails for booking {booking_id}: {str(e)}",
            exc_info=True,
        )
