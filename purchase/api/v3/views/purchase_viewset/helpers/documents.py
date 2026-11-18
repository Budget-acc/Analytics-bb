import logging

from django.conf import settings
from django.db import transaction

from ERP.utils.s3_image_uploads import upload_files_to_s3

logger = logging.getLogger("api_requests")

MAX_PURCHASE_DOCUMENTS = 3
ALLOWED_DOCUMENT_TYPES = ["image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif", "application/pdf"]


def _collect_purchase_document_files(self, request):
    """Collect uploaded document files from the request, any of the
    conventional key shapes used elsewhere in this codebase."""
    if not request or not getattr(request, "FILES", None):
        return []

    files = []
    for key in request.FILES.keys():
        if key.startswith("documents["):
            # Matches both "documents[0]"-style keys and the literal
            # "documents[]" key - do not also collect "documents[]"
            # separately below, or its files get added twice.
            files.extend(request.FILES.getlist(key))
    if "documents" in request.FILES:
        files.extend(request.FILES.getlist("documents"))
    if not files:
        for key in request.FILES:
            files.extend(request.FILES.getlist(key))
    return files


def _upload_purchase_documents(self, files, shop, user, savepoint=None):
    """
    Validate (count + MIME type) and upload up to MAX_PURCHASE_DOCUMENTS
    files synchronously to S3 - no async split needed at this small cap.
    Returns (urls, error_response).
    """
    if not files:
        return [], None

    if len(files) > MAX_PURCHASE_DOCUMENTS:
        if savepoint:
            transaction.savepoint_rollback(savepoint)
        return None, self.validation_error_response(
            errors={"documents": [f"Maximum {MAX_PURCHASE_DOCUMENTS} attachments allowed."]},
            message=f"Cannot exceed {MAX_PURCHASE_DOCUMENTS} attachments per purchase.",
        )

    for f in files:
        content_type = getattr(f, "content_type", None)
        if content_type not in ALLOWED_DOCUMENT_TYPES:
            if savepoint:
                transaction.savepoint_rollback(savepoint)
            return None, self.validation_error_response(
                errors={"documents": [f"Invalid file type: {content_type}."]},
                message="Invalid document file type.",
            )

    try:
        uploaded = upload_files_to_s3(
            files=files,
            shop=shop,
            reporter=user,
            subfolder="purchase_documents",
            max_files=MAX_PURCHASE_DOCUMENTS,
            max_size_bytes=5 * 1024 * 1024,
        )
        return [obj["url"] for obj in uploaded], None
    except ValueError as ve:
        if savepoint:
            transaction.savepoint_rollback(savepoint)
        return None, self.validation_error_response(
            errors={"documents": [str(ve)]},
            message="Invalid document files.",
            dev_message=str(ve),
        )
    except Exception as e:
        if savepoint:
            transaction.savepoint_rollback(savepoint)
        logger.error(f"S3 upload failed for purchase documents: {str(e)}", exc_info=True)
        return None, self.error_response(
            message="Failed to upload document files. Please try again.",
            dev_message=f"S3 upload error: {str(e)}",
        )


def _cleanup_purchase_documents_sync(self, document_urls):
    """
    Synchronously delete these S3 objects. Best-effort - logs and
    continues past any individual failure, never raises. Ported from
    booking_v2's _cleanup_s3_documents_sync (same bucket/host validation
    and per-URL error handling), renamed to fit this module's convention.

    Must only be called after the DB transaction has committed - S3
    deletion isn't part of the transaction and can't be rolled back by
    it, so calling this before a possible savepoint_rollback could delete
    an object that the (restored) purchase.documents still references.
    """
    if not document_urls:
        return

    import boto3
    from urllib.parse import urlparse

    try:
        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
        )
        bucket = settings.AWS_STORAGE_BUCKET_NAME
        expected_host = f"{bucket}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com"

        for url in document_urls:
            try:
                parsed = urlparse(url)
                if parsed.netloc != expected_host:
                    logger.warning(
                        f"[Purchase S3 Cleanup] Skipping deletion: URL {url} doesn't match expected bucket host {expected_host}."
                    )
                    continue
                key = parsed.path.lstrip("/")
                if not key:
                    logger.warning(f"[Purchase S3 Cleanup] Skipping deletion: No key found in URL {url}.")
                    continue
                s3.delete_object(Bucket=bucket, Key=key)
                logger.info(f"[Purchase S3 Cleanup] Successfully deleted S3 object: {key}")
            except Exception as e:
                logger.error(f"[Purchase S3 Cleanup] Failed to delete S3 document {url}: {str(e)}", exc_info=True)
                # Continue with other deletions even if one fails.
    except Exception as e:
        logger.error(f"[Purchase S3 Cleanup] Failed to initialize S3 client: {str(e)}", exc_info=True)


__all__ = [
    "MAX_PURCHASE_DOCUMENTS",
    "_collect_purchase_document_files",
    "_upload_purchase_documents",
    "_cleanup_purchase_documents_sync",
]
