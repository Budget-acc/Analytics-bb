"""
Celery tasks for bulk product creation from a Google Drive folder.

Batch-oriented by design (see plan): Drive listing streams page-by-page,
processing is fanned out into a handful of chunk tasks (default ~500 images
per task, not one task per image) to keep broker overhead low, and all
database writes within a chunk are done via bulk_create/bulk_update.
"""
import io
import logging
import os
import re
import uuid

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from shop.utils.s3_folder import legacy_s3_segment
from ERP.utils.s3_cache_control import IMAGE_CACHE_CONTROL

logger = logging.getLogger("api_requests")


def _sanitize_s3_segment(value: str, max_len: int = 50) -> str:
    return legacy_s3_segment(value, max_len)


def normalize_product_name(filename: str) -> str:
    """
    Strips the extension, lowercases, and collapses whitespace/-/_ into single
    spaces, so 'Black Shirt.jpg', 'black-shirt.JPG', and 'BLACK_SHIRT.png' all
    normalize identically for duplicate detection.
    """
    name = os.path.splitext(filename)[0]
    name = re.sub(r"[\s_-]+", " ", name).strip().lower()
    return name


def display_product_name(filename: str) -> str:
    """Product.name value: extension stripped, truncated to the model's max_length (200),
    with a warning logged if truncation occurred."""
    name = os.path.splitext(filename)[0].strip()
    if len(name) > 200:
        logger.warning(f"[Bulk Import] Product name truncated (was {len(name)} chars): {name!r}")
        name = name[:200]
    return name or f"product_{uuid.uuid4().hex[:8]}"


@shared_task(bind=True, time_limit=1200, soft_time_limit=1140,
             name='services.tasks.list_and_queue_bulk_import_task')
def list_and_queue_bulk_import_task(self, import_id):
    """
    Coordinator task: streams the Drive folder listing page-by-page, creates
    BulkProductImportItem rows in bulk (validating mimeType/size from Drive
    metadata alone, no downloads yet), then dispatches one chunk-processing
    task per BULK_IMPORT_CHUNK_SIZE items.
    """
    from services.models import BulkProductImport, BulkProductImportItem
    from services.utils.google_drive import (
        iter_drive_image_pages, ALLOWED_DRIVE_IMAGE_MIME_TYPES, DriveAccessError,
    )

    try:
        bulk_import = BulkProductImport.objects.get(id=import_id)
    except BulkProductImport.DoesNotExist:
        logger.error(f"[Bulk Import] list_and_queue: import_id={import_id} not found")
        return

    try:
        bulk_import.status = "listing"
        bulk_import.started_at = timezone.now()
        bulk_import.save(update_fields=["status", "started_at"])

        max_raw_bytes = getattr(settings, "BULK_IMPORT_MAX_RAW_INPUT_BYTES", 25_000_000)
        total_items = 0

        for page in iter_drive_image_pages(bulk_import.drive_folder_id):
            if not page:
                continue

            items_to_create = []
            for f in page:
                filename = f.get("name") or f"untitled_{f.get('id')}"
                mime_type = f.get("mimeType")
                size = int(f.get("size") or 0)

                if mime_type not in ALLOWED_DRIVE_IMAGE_MIME_TYPES:
                    items_to_create.append(BulkProductImportItem(
                        bulk_import=bulk_import, drive_file_id=f.get("id"), filename=filename,
                        normalized_name=normalize_product_name(filename),
                        status="invalid", error_message=f"Unsupported MIME type: {mime_type}",
                    ))
                elif size and size > max_raw_bytes:
                    items_to_create.append(BulkProductImportItem(
                        bulk_import=bulk_import, drive_file_id=f.get("id"), filename=filename,
                        normalized_name=normalize_product_name(filename),
                        status="invalid",
                        error_message=f"File too large ({size} bytes > {max_raw_bytes} byte limit).",
                    ))
                else:
                    items_to_create.append(BulkProductImportItem(
                        bulk_import=bulk_import, drive_file_id=f.get("id"), filename=filename,
                        normalized_name=normalize_product_name(filename),
                        status="pending",
                    ))

            BulkProductImportItem.objects.bulk_create(items_to_create, batch_size=500)
            total_items += len(items_to_create)

            # One UPDATE per page, not per file.
            bulk_import.total_items = total_items
            bulk_import.save(update_fields=["total_items"])

        bulk_import.status = "processing"
        bulk_import.save(update_fields=["status"])

        chunk_size = getattr(settings, "BULK_IMPORT_CHUNK_SIZE", 500)
        pending_ids = list(
            BulkProductImportItem.objects.filter(bulk_import=bulk_import, status="pending")
            .values_list("id", flat=True)
        )

        if not pending_ids:
            # Nothing valid to process (e.g. an empty or all-invalid folder).
            _finalize_import_if_done(bulk_import.id)
            return

        for i in range(0, len(pending_ids), chunk_size):
            chunk_ids = pending_ids[i:i + chunk_size]
            process_import_chunk_task.delay(bulk_import.id, chunk_ids)

        logger.info(
            f"[Bulk Import] import_id={import_id} listed {total_items} files, "
            f"queued {len(pending_ids)} for processing in "
            f"{(len(pending_ids) + chunk_size - 1) // chunk_size} chunk(s)."
        )

    except DriveAccessError as e:
        logger.error(f"[Bulk Import] import_id={import_id} Drive access error: {e}")
        bulk_import.status = "failed"
        bulk_import.error_message = str(e)[:500]
        bulk_import.completed_at = timezone.now()
        bulk_import.save(update_fields=["status", "error_message", "completed_at"])

    except SoftTimeLimitExceeded:
        logger.error(f"[Bulk Import] import_id={import_id} listing exceeded time limit")
        bulk_import.status = "failed"
        bulk_import.error_message = "Listing Google Drive folder exceeded the time limit."
        bulk_import.completed_at = timezone.now()
        bulk_import.save(update_fields=["status", "error_message", "completed_at"])

    except Exception as e:
        logger.error(f"[Bulk Import] import_id={import_id} listing failed: {e}", exc_info=True)
        bulk_import.status = "failed"
        bulk_import.error_message = str(e)[:500]
        bulk_import.completed_at = timezone.now()
        bulk_import.save(update_fields=["status", "error_message", "completed_at"])


@shared_task(bind=True, time_limit=1800, soft_time_limit=1740,
             name='services.tasks.process_import_chunk_task')
def process_import_chunk_task(self, import_id, item_ids):
    """
    Processes one chunk (default up to 500) of BulkProductImportItem rows
    sequentially within this single task: download from Drive -> process
    image -> upload to S3 -> collect results -> a single bulk_create for
    Product/ProductVariant covering the whole chunk -> bulk_update items ->
    one counter update on the parent BulkProductImport.
    """
    from services.models import (
        BulkProductImport, BulkProductImportItem, Product, ProductVariant, ShopOwnerService,
    )
    from services.utils.google_drive import get_drive_service, download_drive_file
    from services.utils.image_processing_service import ImageProcessingService
    from services.s3_service import S3Service

    try:
        bulk_import = BulkProductImport.objects.select_related("shop", "shop_service").get(id=import_id)
    except BulkProductImport.DoesNotExist:
        logger.error(f"[Bulk Import] process_chunk: import_id={import_id} not found")
        return

    if bulk_import.cancel_requested:
        logger.info(f"[Bulk Import] import_id={import_id} cancelled - skipping chunk of {len(item_ids)} items")
        _finalize_import_if_done(import_id)
        return

    items = list(BulkProductImportItem.objects.filter(id__in=item_ids, status="pending"))
    if not items:
        return

    BulkProductImportItem.objects.filter(id__in=[i.id for i in items]).update(status="processing")

    shop = bulk_import.shop
    shop_service = bulk_import.shop_service
    shop_name_seg = shop.s3_folder
    service_name_seg = _sanitize_s3_segment(shop_service.general_service.name if shop_service.general_service else "general")

    bucket = settings.AWS_STORAGE_BUCKET_NAME
    s3_client = S3Service.get_client()

    try:
        drive_service = get_drive_service()
    except Exception as e:
        logger.error(f"[Bulk Import] import_id={import_id} could not init Drive service: {e}")
        BulkProductImportItem.objects.filter(id__in=[i.id for i in items]).update(
            status="failed", error_message="Google Drive service unavailable."
        )
        _apply_chunk_counters(bulk_import, processed=len(items), success=0, failed=len(items), skipped=0)
        _finalize_import_if_done(import_id)
        return

    ready_items = []  # items successfully processed+uploaded, awaiting product creation

    for item in items:
        try:
            item.attempt_count += 1
            raw_bytes = download_drive_file(drive_service, item.drive_file_id)

            webp_bytes, _resolution, opened_image = ImageProcessingService.process_main(
                raw_bytes,
                target_resolution=bulk_import.target_resolution,
                max_bytes=bulk_import.max_image_bytes,
                quality_start=bulk_import.jpeg_quality_start,
                quality_min=bulk_import.jpeg_quality_min,
            )
            thumb_bytes = ImageProcessingService.process_thumbnail(opened_image, output_format="WEBP")

            name_seg = _sanitize_s3_segment(item.normalized_name or f"product_{uuid.uuid4().hex}")
            main_key = f"{shop_name_seg}/product_images/{service_name_seg}/{name_seg}_{uuid.uuid4().hex}.webp"
            thumb_key = f"{shop_name_seg}/product_images/{service_name_seg}/{name_seg}_thumb_{uuid.uuid4().hex}.webp"

            s3_client.put_object(Bucket=bucket, Key=main_key, Body=webp_bytes, ContentType="image/webp", CacheControl=IMAGE_CACHE_CONTROL)
            s3_client.put_object(Bucket=bucket, Key=thumb_key, Body=thumb_bytes, ContentType="image/webp", CacheControl=IMAGE_CACHE_CONTROL)

            item.image_url = f"https://{bucket}.s3.amazonaws.com/{main_key}"
            item.thumbnail_url = f"https://{bucket}.s3.amazonaws.com/{thumb_key}"
            ready_items.append(item)

        except Exception as e:
            logger.error(
                f"[Bulk Import] import_id={import_id} item_id={item.id} filename={item.filename!r} failed: {e}",
                exc_info=True,
            )
            item.status = "failed"
            item.error_message = str(e)[:500]

    created_count = 0
    try:
        # --- De-duplicate the successfully-processed items in this chunk ---
        # Locks the shop_service row for the duration of the read-then-create
        # section below, serializing concurrent chunk tasks (of this import or
        # any other) that target the same shop_service. Without this lock, two
        # chunks racing on the same normalized name could both pass the
        # existing_names check before either commits, creating duplicate
        # products - Product has no DB-level uniqueness on (shop_service, name)
        # to fall back on.
        with transaction.atomic():
            ShopOwnerService.objects.select_for_update().get(id=shop_service.id)

            existing_names = set(
                Product.objects.filter(shop_service=shop_service, deleted__isnull=True)
                .values_list("name", flat=True)
            )
            # Apply the exact same normalization used for incoming filenames (no extension
            # to strip here, but whitespace/-/_ collapsing must match for an apples-to-apples
            # comparison - otherwise e.g. an existing "Black-Shirt" wouldn't match an
            # incoming "Black Shirt.jpg", both of which normalize to "black shirt").
            existing_names_normalized = {normalize_product_name(n) for n in existing_names if n}

            seen_in_chunk = set()
            to_create = []
            for item in ready_items:
                norm = item.normalized_name
                if norm in existing_names_normalized or norm in seen_in_chunk:
                    item.status = "duplicate"
                    item.error_message = "A product with this name already exists for this service."
                    continue
                seen_in_chunk.add(norm)
                to_create.append(item)

            if to_create:
                products = [
                    Product(
                        shop_service=shop_service,
                        name=display_product_name(item.filename),
                        image=item.image_url,
                        thumbnail_image=item.thumbnail_url,
                    )
                    for item in to_create
                ]
                created_products = Product.objects.bulk_create(products, batch_size=500)

                variants = [
                    ProductVariant(
                        product=product,
                        shop=shop,
                        attribute="",
                        stock=bulk_import.default_quantity,
                    )
                    for product in created_products
                ]
                created_variants = ProductVariant.objects.bulk_create(variants, batch_size=500)

                for product, variant in zip(created_products, created_variants):
                    variant.unique_code = f"S{shop_service.id}-P{product.id}-V{variant.id}"
                ProductVariant.objects.bulk_update(created_variants, ["unique_code"], batch_size=500)

                for item, product in zip(to_create, created_products):
                    item.status = "created"
                    item.product = product
                    created_count += 1

        # --- Persist final item statuses for the whole chunk in one query ---
        BulkProductImportItem.objects.bulk_update(
            items,
            ["status", "image_url", "thumbnail_url", "product", "error_message", "attempt_count"],
            batch_size=500,
        )

    except Exception as e:
        # Anything failing past this point (dedupe query, bulk_create, or the
        # final bulk_update) must still converge to a terminal item/import
        # state - items were already flipped to "processing" above, and
        # leaving them there would strand this chunk (and the whole import)
        # in "processing" forever with no automatic recovery.
        logger.error(
            f"[Bulk Import] import_id={import_id} chunk processing failed: {e}", exc_info=True
        )
        for item in items:
            if item.status not in ("failed", "duplicate", "invalid", "created"):
                item.status = "failed"
                item.error_message = f"Chunk processing error: {str(e)[:450]}"
        BulkProductImportItem.objects.bulk_update(
            items,
            ["status", "image_url", "thumbnail_url", "product", "error_message", "attempt_count"],
            batch_size=500,
        )
        created_count = 0

    failed_count = sum(1 for i in items if i.status == "failed")
    duplicate_or_invalid_count = sum(1 for i in items if i.status in ("duplicate", "invalid"))

    _apply_chunk_counters(
        bulk_import,
        processed=len(items),
        success=created_count,
        failed=failed_count,
        skipped=duplicate_or_invalid_count,
    )

    self.update_state(state="PROGRESS", meta={
        "processed": bulk_import.processed_count,
        "total": bulk_import.total_items,
    })

    _finalize_import_if_done(import_id)


def _apply_chunk_counters(bulk_import, processed, success, failed, skipped):
    """One UPDATE for the whole chunk's counters, not one per image."""
    from django.db.models import F
    from services.models import BulkProductImport

    BulkProductImport.objects.filter(id=bulk_import.id).update(
        processed_count=F("processed_count") + processed,
        success_count=F("success_count") + success,
        failed_count=F("failed_count") + failed,
        skipped_count=F("skipped_count") + skipped,
    )
    bulk_import.refresh_from_db(fields=["processed_count", "success_count", "failed_count", "skipped_count", "total_items"])


def _finalize_import_if_done(import_id):
    """If every item for this import has left pending/processing, marks the
    parent BulkProductImport with its terminal status."""
    from services.models import BulkProductImport, BulkProductImportItem

    bulk_import = BulkProductImport.objects.get(id=import_id)
    if bulk_import.status in ("completed", "completed_with_errors", "failed", "cancelled"):
        return

    outstanding = BulkProductImportItem.objects.filter(
        bulk_import=bulk_import, status__in=["pending", "processing"]
    ).exists()
    if outstanding:
        return

    if bulk_import.cancel_requested:
        final_status = "cancelled"
    elif bulk_import.total_items == 0:
        final_status = "completed"
    elif bulk_import.failed_count == 0:
        final_status = "completed"
    elif bulk_import.success_count == 0:
        final_status = "failed"
    else:
        final_status = "completed_with_errors"

    bulk_import.status = final_status
    bulk_import.completed_at = timezone.now()
    bulk_import.save(update_fields=["status", "completed_at"])
    logger.info(f"[Bulk Import] import_id={import_id} finalized with status={final_status}")


@shared_task(bind=True, time_limit=300, name='services.tasks.cleanup_old_bulk_imports_task')
def cleanup_old_bulk_imports_task(self):
    """Periodic cleanup: deletes terminal BulkProductImport rows (cascading to
    their items) older than BULK_IMPORT_RETENTION_DAYS."""
    from datetime import timedelta
    from services.models import BulkProductImport

    retention_days = getattr(settings, "BULK_IMPORT_RETENTION_DAYS", 90)
    cutoff = timezone.now() - timedelta(days=retention_days)

    old_imports = BulkProductImport.objects.filter(
        status__in=["completed", "completed_with_errors", "failed", "cancelled"],
        completed_at__lt=cutoff,
    )
    count = old_imports.count()
    old_imports.delete()
    logger.info(f"[Bulk Import Cleanup] Deleted {count} import(s) older than {retention_days} days.")
    return count
