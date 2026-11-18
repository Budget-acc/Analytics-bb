from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management.base import BaseCommand

from services.models import Product


MAX_RETRIES = 2


class Command(BaseCommand):
    help = "Delete S3 PNG images for products that have already been converted to JPEG"

    def add_arguments(self, parser):
        parser.add_argument("--shop-id", type=int, default=None)
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--chunk-size", type=int, default=200)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate deletions without actually removing files from S3",
        )

    def handle(self, *args, **options):
        shop_id = options["shop_id"]
        workers = options["workers"]
        chunk_size = options["chunk_size"]
        dry_run = options["dry_run"]

        scope = f"shop {shop_id}" if shop_id else "all shops"
        self.stdout.write(f"Starting PNG cleanup for {scope}")

        queryset = Product.objects.filter(image__iendswith=".jpg").only("id", "image", "thumbnail_image")

        if shop_id:
            if shop_id <= 0:
                raise ValueError("Invalid shop_id")
            queryset = queryset.filter(shop_service__shop_id=shop_id)

        queryset = queryset.order_by("id")

        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
        )
        bucket = settings.AWS_STORAGE_BUCKET_NAME

        deleted = 0
        skipped = 0
        failed = 0

        def _drain(futures):
            nonlocal deleted, skipped, failed
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result == "deleted":
                        deleted += 1
                    else:
                        skipped += 1
                    if (deleted + skipped) % 100 == 0:
                        self.stdout.write(f"Processed {deleted + skipped} (deleted={deleted}, skipped={skipped})")
                except Exception as e:
                    failed += 1
                    self.stdout.write(f"Failed: {str(e)}")

        with ThreadPoolExecutor(max_workers=workers) as executor:

            batch = []
            for product in queryset.iterator(chunk_size=chunk_size):
                batch.append(product)
                if len(batch) >= chunk_size:
                    _drain([
                        executor.submit(self.process_product, p, s3, bucket, dry_run)
                        for p in batch
                    ])
                    batch = []

            if batch:
                _drain([
                    executor.submit(self.process_product, p, s3, bucket, dry_run)
                    for p in batch
                ])

        self.stdout.write(f"Completed. Deleted: {deleted}, Skipped: {skipped}, Failed: {failed}")

    def _s3_key_from_url(self, url):
        parsed = urlparse(url)
        return parsed.path.lstrip("/")

    def _object_exists(self, s3, bucket, key):
        try:
            s3.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return False
            raise

    def process_product(self, product, s3, bucket, dry_run):
        image_url = product.image
        if not image_url:
            return "skipped"

        jpg_key = self._s3_key_from_url(image_url)
        base_key = jpg_key.rsplit(".", 1)[0]

        png_key = f"{base_key}.png"

        thumb_png_key = None
        thumb_url = product.thumbnail_image
        if thumb_url and thumb_url.lower().endswith(".jpg"):
            thumb_jpg_key = self._s3_key_from_url(thumb_url)
            thumb_png_key = thumb_jpg_key.rsplit(".", 1)[0] + ".png"

        if not self._object_exists(s3, bucket, png_key):
            return "skipped"

        if dry_run:
            print(f"DRY RUN → would delete {png_key}")
            return "deleted"

        s3.delete_object(Bucket=bucket, Key=png_key)

        if thumb_png_key and self._object_exists(s3, bucket, thumb_png_key):
            s3.delete_object(Bucket=bucket, Key=thumb_png_key)

        return "deleted"
