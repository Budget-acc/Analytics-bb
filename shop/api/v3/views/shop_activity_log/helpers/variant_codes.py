from services.models import ProductVariant
import logging

logger = logging.getLogger(__name__)


def _fetch_variant_codes(self, variant_logs):
    """Fetch variant unique codes in bulk"""
    variant_codes_map = {}

    # Group by content_type and object_id
    content_type_ids = set()
    object_ids = set()

    for log in variant_logs:
        if log.content_type_id and log.object_id:
            content_type_ids.add(log.content_type_id)
            object_ids.add(log.object_id)

    if not content_type_ids or not object_ids:
        return variant_codes_map

    try:
        # Get both Product and ProductVariant content types
        from django.contrib.contenttypes.models import ContentType

        product_content_type = ContentType.objects.get(app_label='services', model='product')
        product_variant_content_type = ContentType.objects.get(app_label='services', model='productvariant')

        # Handle ProductVariant logs
        variant_logs_filtered = [
            log for log in variant_logs
            if log.content_type_id == product_variant_content_type.id
        ]

        if variant_logs_filtered:
            variant_ids = [log.object_id for log in variant_logs_filtered]

            # Use all_objects to include soft-deleted records
            variants = ProductVariant.all_objects.filter(
                id__in=variant_ids
            ).values('id', 'unique_code')

            # Create mapping
            for variant in variants:
                variant_codes_map[(product_variant_content_type.id, variant['id'])] = variant['unique_code']

    except ContentType.DoesNotExist as e:
        logger.warning(f"Content type not found: {e}")
    except Exception as e:
        logger.error(f"Error fetching variant codes: {e}")

    return variant_codes_map
