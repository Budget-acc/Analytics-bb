from django.conf import settings
from ERP.utils.s3_cache_control import IMAGE_CACHE_CONTROL
from django.utils import timezone
import boto3, logging, uuid
from io import BytesIO
from PIL import Image, UnidentifiedImageError
from mimetypes import guess_extension, guess_type
from pathlib import Path

logger = logging.getLogger("api_requests")

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}

EXTENSION_TO_PIL_FORMAT = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
    ".gif": "GIF",
}

FORMAT_TO_EXTENSION = {
    "WEBP": ".webp",
}

FORMAT_TO_MIME = {
    "WEBP": "image/webp",
}

# Number of documents uploaded synchronously (inline in the request) before the
# rest are handed off to a background task. Shared across modules (Booking,
# Tailor Order, ...) so the sync/background split always stays consistent.
SYNC_DOCUMENT_UPLOAD_COUNT = 2


def upload_files_to_s3(files, shop, reporter, subfolder="bug_reports", max_files=5, max_size_bytes=3 * 1024 * 1024, max_image_dims=(1200, 1200), force_format=None):
    """
    Upload multiple image files to S3 and return list of objects with url and key.
    Always converts to PNG for consistency and reliability.
    """
    if not files:
        return []

    files_list = list(files)
    if len(files_list) > max_files:
        raise ValueError(f"Maximum {max_files} images are allowed.")

    shop_name = shop.s3_folder
    reporter_part = f"user_{getattr(reporter, 'id', 'anon')}"
    base_prefix = f"{shop_name}/{subfolder}/{reporter_part}/{timezone.now().strftime('%Y%m%d')}"

    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME,
    )
    bucket = settings.AWS_STORAGE_BUCKET_NAME
    uploaded_objects = []

    try:
        for f in files_list:

            # ---------------- Size Validation ----------------
            if not hasattr(f, "size") or f.size == 0:
                raise ValueError("One of the uploaded files is empty or invalid.")

            if f.size > max_size_bytes:
                raise ValueError(f"Each image must be smaller than {int(max_size_bytes / (1024*1024))}MB.")
            
            # ---------------- Extension Validation ----------------
            original_ext = Path(f.name).suffix.lower()

            if original_ext not in ALLOWED_EXTENSIONS:
                raise ValueError(
                    f"Invalid file extension '{original_ext}'. "
                    f"Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
                )

            # ---------------- MIME Validation ----------------
            mime_type = getattr(f, "content_type", None)

            guessed_mime = guess_type(f.name)[0]

            final_mime = mime_type or guessed_mime

            if not final_mime or final_mime not in ALLOWED_MIME_TYPES:
                raise ValueError(
                    f"Invalid MIME type '{final_mime}'. "
                    f"Allowed: JPEG, PNG, WEBP, GIF"
                )

            pil_format = EXTENSION_TO_PIL_FORMAT.get(original_ext)

            if not pil_format:
                raise ValueError(f"Unsupported image format: {original_ext}")

            if force_format:
                pil_format = force_format

            # ---------------- Load & Validate Image ----------------
            try:
                img = Image.open(f)
                img.verify()
            except UnidentifiedImageError:
                raise ValueError("Uploaded file is not a valid image.")
            except Exception:
                raise ValueError("Invalid or corrupted image uploaded.")

            # IMPORTANT: reopen image safely
            try:
                f.seek(0)
                img = Image.open(f)
            except Exception:
                raise ValueError("Image file could not be processed after validation.")

            # ---------------- Convert Mode Safely ----------------
            if img.mode in ("RGBA", "LA", "P"):
                if pil_format in ["JPG", "JPEG"]:
                    img = img.convert("RGB")
                else:
                    img = img.convert("RGBA")
            else:
                img = img.convert("RGB")

            # ---------------- Resize if Needed ----------------
            if max_image_dims and (
                img.width > max_image_dims[0] or img.height > max_image_dims[1]
            ):
                img.thumbnail(max_image_dims, Image.Resampling.LANCZOS)

            # ---------------- Prepare Upload ----------------
            uid = uuid.uuid4().hex
            upload_ext = FORMAT_TO_EXTENSION.get(pil_format, original_ext)
            upload_mime = FORMAT_TO_MIME.get(pil_format, final_mime)
            key = f"{base_prefix}/{uid}{upload_ext}"

            buffer = BytesIO()

            save_kwargs = {"optimize": True}

            if pil_format in ["JPG", "JPEG", "WEBP"]:
                save_kwargs["quality"] = 85

            img.save(buffer, format=pil_format, **save_kwargs)
            buffer.seek(0)

            # ---------------- Upload To S3 ----------------
            s3.upload_fileobj(
                buffer,
                bucket,
                key,
                ExtraArgs={"ContentType": upload_mime, "CacheControl": IMAGE_CACHE_CONTROL},
            )

            url = f"https://{bucket}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{key}"

            uploaded_objects.append(
                {
                    "url": url,
                    "key": key,
                    "content_type": upload_mime,
                }
            )

    except Exception as e:
        # ---------------- Cleanup Partial Uploads ----------------
        try:
            for obj in uploaded_objects:
                s3.delete_object(Bucket=bucket, Key=obj["key"])
        except Exception as cleanup_err:
            logger.error(f"Failed to cleanup partial uploads: {cleanup_err}")
        raise

    return uploaded_objects