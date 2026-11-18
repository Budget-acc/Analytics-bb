"""
Shared image-processing logic used by both the single-product upload flow
(services/tasks/image_tasks.py) and the bulk Google Drive import flow
(services/tasks/bulk_import_tasks.py) - kept in one place so the two never
drift apart.
"""
import io
import logging

logger = logging.getLogger("api_requests")

# Thumbnail spec - identical to the existing single-product creation flow.
THUMBNAIL_SIZE = (100, 100)
THUMBNAIL_QUALITY = 80


class ImageProcessingService:
    """Static helpers for producing the main product image and its thumbnail."""

    @staticmethod
    def _open_rgb(raw_bytes_or_image):
        """Opens raw bytes (or passes through an already-opened Image), EXIF-rotates,
        and converts to RGB."""
        from PIL import Image, ImageOps

        if hasattr(raw_bytes_or_image, "mode"):
            img = raw_bytes_or_image
        else:
            img = Image.open(io.BytesIO(raw_bytes_or_image))
            img = ImageOps.exif_transpose(img)

        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")
        return img

    @staticmethod
    def _center_crop_square(img):
        """Crops the longer dimension symmetrically around center - no distortion."""
        width, height = img.size
        if width == height:
            return img
        side = min(width, height)
        left = (width - side) // 2
        top = (height - side) // 2
        return img.crop((left, top, left + side, top + side))

    @classmethod
    def process_main(cls, raw_bytes, target_resolution, max_bytes, quality_start, quality_min):
        """
        EXIF-rotate, RGB-convert, center-crop to 1:1, resize to target_resolution,
        encode as WebP, iteratively lowering quality (and, if still too large at
        quality_min, shrinking the resolution ~10% and retrying) until under max_bytes.

        Returns (webp_bytes, final_resolution, opened_image) - the opened_image (the
        RGB, cropped, resized PIL Image before encoding) is returned too so callers
        (the bulk import chunk task) can pass it straight into process_thumbnail()
        without re-decoding the raw bytes a second time.
        """
        from PIL import Image

        img = cls._open_rgb(raw_bytes)
        img = cls._center_crop_square(img)

        resolution = target_resolution
        quality = quality_start
        webp_bytes = b""

        while True:
            resized = img.resize((resolution, resolution), Image.LANCZOS)
            buf = io.BytesIO()
            resized.save(buf, format="WEBP", quality=quality, method=6)
            webp_bytes = buf.getvalue()

            if len(webp_bytes) <= max_bytes or (quality <= quality_min and resolution <= 200):
                break

            if quality > quality_min:
                quality = max(quality_min, quality - 5)
            else:
                # Already at minimum quality - shrink resolution and restart the
                # quality loop at the starting quality for the new (smaller) size.
                resolution = max(200, int(resolution * 0.9))
                quality = quality_start

        return webp_bytes, resolution, img

    @staticmethod
    def process_thumbnail(raw_bytes_or_image, output_format="JPEG"):
        """
        Exact existing single-product thumbnail logic: (100,100) LANCZOS thumbnail,
        quality=80. Accepts either raw bytes or an already-opened/decoded PIL
        Image (the bulk flow passes in the image already processed by process_main
        to avoid decoding twice).

        output_format defaults to "JPEG" - the existing single-product flow
        (services/tasks/image_tasks.py) never passes this argument, so its
        behavior/output is unchanged. The bulk Google Drive import flow passes
        output_format="WEBP" to produce WebP thumbnails instead.
        """
        from PIL import Image

        if hasattr(raw_bytes_or_image, "mode"):
            img = raw_bytes_or_image
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
        else:
            img = Image.open(io.BytesIO(raw_bytes_or_image))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

        thumb = img.copy()
        thumb.thumbnail(THUMBNAIL_SIZE, Image.LANCZOS)
        buf = io.BytesIO()
        if output_format == "WEBP":
            thumb.save(buf, format="WEBP", quality=THUMBNAIL_QUALITY, method=6)
        else:
            thumb.save(buf, format="JPEG", quality=THUMBNAIL_QUALITY, optimize=False)
        return buf.getvalue()
