import io
import time
import requests
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image

import boto3
from django.conf import settings
from ERP.utils.s3_cache_control import IMAGE_CACHE_CONTROL
from django.core.management.base import BaseCommand
from django.db import transaction

from services.models import Product


THUMB_SIZE = (200, 200)
MAX_RETRIES = 2


class Command(BaseCommand):
    help = "Convert PNG product images to JPEG for a given shop"

    def add_arguments(self, parser):
        parser.add_argument("--shop-id", type=int, required=True)

        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--chunk-size", type=int, default=200)
        parser.add_argument("--quality", type=int, default=95)

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate conversion without uploading or saving",
        )

    def handle(self, *args, **options):

        shop_id = options["shop_id"]
        workers = options["workers"]
        chunk_size = options["chunk_size"]
        quality = options["quality"]
        dry_run = options["dry_run"]

        if shop_id <= 0:
            raise ValueError("Invalid shop_id")

        self.stdout.write(f"Starting conversion for shop {shop_id}")

        queryset = (
            Product.objects
            .filter(
                shop_service__shop_id=shop_id,
                image__iendswith=".png"
            )
            .only("id", "image", "thumbnail_image")
            .order_by("id")
        )

        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
        )

        processed = 0
        failed = 0

        with ThreadPoolExecutor(max_workers=workers) as executor:

            batch = []
            for product in queryset.iterator(chunk_size=chunk_size):
                batch.append(product)

                if len(batch) >= chunk_size:
                    futures = [
                        executor.submit(self.process_product, p, s3, quality, dry_run)
                        for p in batch
                    ]
                    for future in as_completed(futures):
                        try:
                            future.result()
                            processed += 1
                            if processed % 100 == 0:
                                self.stdout.write(f"Processed {processed}")
                        except Exception as e:
                            failed += 1
                            self.stdout.write(f"Failed: {str(e)}")
                    batch = []

            # Process remaining items
            if batch:
                futures = [
                    executor.submit(self.process_product, p, s3, quality, dry_run)
                    for p in batch
                ]
                for future in as_completed(futures):
                    try:
                        future.result()
                        processed += 1
                        if processed % 100 == 0:
                            self.stdout.write(f"Processed {processed}")
                    except Exception as e:
                        failed += 1
                        self.stdout.write(f"Failed: {str(e)}")

        self.stdout.write(f"Completed. Success: {processed}, Failed: {failed}")

    def download_with_retry(self, url):

        for attempt in range(MAX_RETRIES):

            try:
                response = requests.get(url, timeout=20)

                if response.status_code == 200:
                    return response.content

            except requests.exceptions.RequestException:
                pass

            time.sleep(2 ** attempt)

        raise Exception("Image download failed after retries")

    def process_product(self, product, s3, quality, dry_run):

        image_url = product.image

        if not image_url:
            return

        content = self.download_with_retry(image_url)

        img = Image.open(io.BytesIO(content))

        if img.format != "PNG":
            return

        # Handle transparency
        if img.mode in ("RGBA", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
        else:
            img = img.convert("RGB")

        parsed = urlparse(image_url)
        key = parsed.path.lstrip("/")

        base_key = key.rsplit(".", 1)[0]

        new_key = f"{base_key}.jpg"
        thumb_key = f"{base_key}_thumb.jpg"

        image_buffer = io.BytesIO()
        img.save(image_buffer, format="JPEG", quality=quality, optimize=True)
        image_buffer.seek(0)

        thumb = img.copy()
        thumb.thumbnail(THUMB_SIZE)

        thumb_buffer = io.BytesIO()
        thumb.save(thumb_buffer, format="JPEG", quality=80, optimize=True)
        thumb_buffer.seek(0)

        bucket = settings.AWS_STORAGE_BUCKET_NAME

        if dry_run:
            print(f"DRY RUN → {product.id}: {new_key}")
            return

        s3.upload_fileobj(
            image_buffer,
            bucket,
            new_key,
            ExtraArgs={"ContentType": "image/jpeg", "CacheControl": IMAGE_CACHE_CONTROL}
        )

        s3.upload_fileobj(
            thumb_buffer,
            bucket,
            thumb_key,
            ExtraArgs={"ContentType": "image/jpeg", "CacheControl": IMAGE_CACHE_CONTROL}
        )

        domain = getattr(settings, "AWS_S3_CUSTOM_DOMAIN", None)

        if domain:
            new_image_url = f"https://{domain}/{new_key}"
            new_thumb_url = f"https://{domain}/{thumb_key}"
        else:
            new_image_url = f"https://{bucket}.s3.amazonaws.com/{new_key}"
            new_thumb_url = f"https://{bucket}.s3.amazonaws.com/{thumb_key}"

        with transaction.atomic():
            Product.objects.filter(id=product.id).update(
                image=new_image_url,
                thumbnail_image=new_thumb_url
            )