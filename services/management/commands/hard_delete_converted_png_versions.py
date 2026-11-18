from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management.base import BaseCommand

from services.models import Product


class Command(BaseCommand):
    help = "Hard delete ONLY previously soft-deleted PNG images"

    def add_arguments(self, parser):
        parser.add_argument("--shop-id", type=int, default=None)
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--chunk-size", type=int, default=200)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        shop_id = options["shop_id"]
        workers = options["workers"]
        chunk_size = options["chunk_size"]
        dry_run = options["dry_run"]

        queryset = Product.objects.filter(image__iendswith=".jpg").only("image", "thumbnail_image")

        if shop_id:
            queryset = queryset.filter(shop_service__shop_id=shop_id)

        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
        )

        bucket = settings.AWS_STORAGE_BUCKET_NAME

        deleted, skipped, failed = 0, 0, 0

        def _drain(futures):
            nonlocal deleted, skipped, failed
            for f in as_completed(futures):
                try:
                    res = f.result()
                    if res == "deleted":
                        deleted += 1
                    else:
                        skipped += 1
                except Exception as e:
                    failed += 1
                    self.stdout.write(f"Error: {e}")

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

        self.stdout.write(f"Done → Deleted={deleted}, Skipped={skipped}, Failed={failed}")

    def _s3_key(self, url):
        return urlparse(url).path.lstrip("/")

    def is_soft_deleted(self, s3, bucket, key):
        try:
            s3.head_object(Bucket=bucket, Key=key)
            return False  # still exists
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return True
            raise

    def hard_delete_all_versions(self, s3, bucket, key):
        paginator = s3.get_paginator("list_object_versions")

        deleted_any = False

        for page in paginator.paginate(Bucket=bucket, Prefix=key):
            for v in page.get("Versions", []):
                if v["Key"] == key:
                    s3.delete_object(
                        Bucket=bucket,
                        Key=key,
                        VersionId=v["VersionId"]
                    )
                    deleted_any = True

            for d in page.get("DeleteMarkers", []):
                if d["Key"] == key:
                    s3.delete_object(
                        Bucket=bucket,
                        Key=key,
                        VersionId=d["VersionId"]
                    )
                    deleted_any = True

        return "deleted" if deleted_any else "skipped"

    def process_product(self, product, s3, bucket, dry_run):
        if not product.image:
            return "skipped"

        jpg_key = self._s3_key(product.image)
        base = jpg_key.rsplit(".", 1)[0]

        png_key = f"{base}.png"

        # Only process if already soft deleted
        if not self.is_soft_deleted(s3, bucket, png_key):
            return "skipped"

        if dry_run:
            print(f"DRY RUN → HARD delete {png_key}")
            return "deleted"

        return self.hard_delete_all_versions(s3, bucket, png_key)