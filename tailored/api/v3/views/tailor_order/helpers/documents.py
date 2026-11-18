import logging
import json
import os
import re
import tempfile
import uuid
import boto3
from django.db import transaction
from django.conf import settings
from django.utils import timezone
from urllib.parse import urlparse
from ERP.utils.s3_image_uploads import upload_files_to_s3

logger = logging.getLogger("api_requests")

MAX_DOCUMENTS = 10
MAX_DOCUMENT_SIZE_BYTES = 3 * 1024 * 1024
ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/gif']


def _rollback(savepoint):
    """Roll back to the given savepoint if provided, else roll back the whole transaction.

    Both current call sites (create.py's doc_savepoint and partial_update.py's own
    savepoint) always pass a real savepoint - the `else` branch is a defensive
    fallback only, not an expected path. Do not add a new caller that passes
    savepoint=None: transaction.set_rollback(True) marks the entire connection's
    transaction broken, not just this helper's work, which will break any later
    query on the same connection (e.g. request-logging middleware) until the
    outer atomic block exits.
    """
    if savepoint is not None:
        transaction.savepoint_rollback(savepoint)
    else:
        transaction.set_rollback(True)


def _parse_remove_documents(self, request_data):
    """
    Normalize 'remove_documents' out of either a multipart QueryDict or a JSON body
    into a plain list of URLs.

    Multipart requests may send this the same way every other array field in this
    API is sent - bracket notation ('remove_documents[0]=<url>&remove_documents[1]=<url>')
    or repeated 'remove_documents[]=<url>' keys - neither of which land under the
    literal key 'remove_documents', so a plain `.get("remove_documents")` on the raw
    request data misses them entirely.
    """
    if not hasattr(request_data, 'lists'):
        # JSON payload: request_data is already a plain dict with a real list/string value.
        return request_data.get("remove_documents", []) if hasattr(request_data, 'get') else []

    if 'remove_documents[]' in request_data:
        return request_data.getlist('remove_documents[]')

    bracket_items = {}
    for key in request_data.keys():
        match = re.match(r"remove_documents\[(\d+)\]$", key)
        if match:
            bracket_items[int(match.group(1))] = request_data.get(key)

    if bracket_items:
        return [bracket_items[i] for i in sorted(bracket_items.keys())]

    if 'remove_documents' in request_data:
        values = request_data.getlist('remove_documents')
        return values if len(values) > 1 else request_data.get('remove_documents')

    return []


def _collect_document_files(self, request):
    """Collect uploaded document files from request in all supported formats."""
    if not request or not getattr(request, 'FILES', None):
        return []

    all_files = []

    for key in request.FILES.keys():
        if key.startswith('documents['):
            all_files.extend(request.FILES.getlist(key))

    if 'documents[]' in request.FILES:
        all_files.extend(request.FILES.getlist('documents[]'))

    if 'documents' in request.FILES:
        all_files.extend(request.FILES.getlist('documents'))

    if not all_files:
        # Fallback for unrecognized key spellings - but never sweep up files that
        # belong to other fields (e.g. items[N][reference_image]), which would
        # otherwise get double-uploaded as order-level documents.
        for key in request.FILES:
            if key.startswith('items['):
                continue
            all_files.extend(request.FILES.getlist(key))

    return all_files


def _validate_documents_sync(self, request, shop, savepoint=None, files=None):
    """
    Validate document files synchronously before order creation/update.
    Returns: (is_valid, error_response, files_list)
    """
    all_files = list(files) if files is not None else self._collect_document_files(request)

    if not all_files:
        return True, None, []

    if len(all_files) > MAX_DOCUMENTS:
        _rollback(savepoint)
        return False, self.validation_error_response(
            errors={"documents": [f"Maximum {MAX_DOCUMENTS} documents allowed. Current: {len(all_files)}"]},
            message=f"Cannot exceed {MAX_DOCUMENTS} documents per order."
        ), all_files

    for f in all_files:
        content_type = getattr(f, 'content_type', None)
        if content_type not in ALLOWED_IMAGE_TYPES:
            _rollback(savepoint)
            return False, self.validation_error_response(
                errors={"documents": [
                    f"Invalid file type: {content_type}. "
                    f"Allowed types: JPEG, JPG, PNG, WebP, GIF"
                ]},
                message="Invalid document file type."
            ), []

        file_size = getattr(f, 'size', None)
        if file_size is None:
            try:
                f.seek(0, 2)
                file_size = f.tell()
                f.seek(0)
            except Exception:
                file_size = None

        if file_size is not None and file_size > MAX_DOCUMENT_SIZE_BYTES:
            _rollback(savepoint)
            return False, self.validation_error_response(
                errors={"documents": [
                    f"File '{f.name}' is too large ({file_size / 1024 / 1024:.2f}MB). "
                    f"Maximum size is 3MB."
                ]},
                message="Document file is too large."
            ), []

    return True, None, all_files


def _upload_documents_to_s3_sync(self, files, shop, reporter, savepoint=None):
    """Upload provided files to S3 synchronously (as WebP) and return (urls, error_response)."""
    if not files:
        return [], None

    try:
        uploaded_objects = upload_files_to_s3(
            files=files,
            shop=shop,
            reporter=reporter,
            subfolder="tailor_order_documents",
            max_files=MAX_DOCUMENTS,
            max_size_bytes=MAX_DOCUMENT_SIZE_BYTES,
            max_image_dims=(2000, 2000),
            force_format="WEBP",
        )

        uploaded_urls = [obj["url"] for obj in uploaded_objects]
        return uploaded_urls, None

    except ValueError as ve:
        _rollback(savepoint)
        logger.error(f"Validation error during S3 upload: {str(ve)}")
        return None, self.validation_error_response(
            errors={"documents": [str(ve)]},
            message="Invalid document images.",
            dev_message=str(ve)
        )
    except Exception as e:
        _rollback(savepoint)
        logger.error(f"S3 upload failed for tailor order documents: {str(e)}", exc_info=True)
        return None, self.error_response(
            message="Failed to upload document images. Please try again.",
            dev_message=f"S3 upload error: {str(e)}"
        )


def _save_temp_documents(self, request, order_id, files=None):
    """
    Save uploaded files to a temporary location for background upload.
    Assumes files have already been validated.
    """
    uploaded_files = []

    all_files = list(files) if files is not None else self._collect_document_files(request)

    for file in all_files:
        try:
            temp_dir = os.path.join(tempfile.gettempdir(), 'tailor_order_docs')
            os.makedirs(temp_dir, exist_ok=True)

            temp_filename = f"{order_id}_{uuid.uuid4()}_{file.name}"
            temp_filename = re.sub(r'[<>:"/\\|?*]', '_', temp_filename)
            temp_path = os.path.join(temp_dir, temp_filename)

            with open(temp_path, 'wb') as f:
                for chunk in file.chunks():
                    f.write(chunk)

            uploaded_files.append({
                'path': temp_path,
                'original_name': file.name,
                'content_type': file.content_type
            })

        except Exception as e:
            logger.error(f"Failed to save temp file {file.name}: {str(e)}", exc_info=True)
            for file_info in uploaded_files:
                if os.path.exists(file_info['path']):
                    try:
                        os.unlink(file_info['path'])
                    except Exception:
                        pass
            return {
                'error': self.error_response(
                    message="Failed to save document files.",
                    dev_message=str(e)
                )
            }

    return uploaded_files


def _cleanup_temp_files(self, uploaded_files):
    """Clean up temporary files."""
    for file_info in uploaded_files:
        if os.path.exists(file_info['path']):
            try:
                os.unlink(file_info['path'])
                logger.info(f"Cleaned up temp file: {file_info['path']}")
            except Exception as e:
                logger.error(f"Failed to cleanup temp file {file_info['path']}: {str(e)}")


def _cleanup_s3_documents_sync(self, document_urls):
    """
    Synchronous S3 document cleanup for critical operations (e.g. transaction rollback).
    For post-commit cleanup, prefer _cleanup_s3_documents() (async).
    """
    if not document_urls:
        return

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
                        f"[S3 Cleanup Sync] Skipping deletion: URL {url} doesn't match expected bucket host {expected_host}"
                    )
                    continue

                key = parsed.path.lstrip('/')
                if not key:
                    logger.warning(f"[S3 Cleanup Sync] Skipping deletion: No key found in URL {url}")
                    continue

                s3.delete_object(Bucket=bucket, Key=key)
                logger.info(f"[S3 Cleanup Sync] Successfully deleted S3 object: {key}")

            except Exception as e:
                logger.error(f"[S3 Cleanup Sync] Failed to delete S3 document {url}: {str(e)}", exc_info=True)

    except Exception as e:
        logger.error(f"[S3 Cleanup Sync] Failed to initialize S3 client: {str(e)}", exc_info=True)


def _cleanup_s3_documents(self, document_urls):
    """
    Wrapper to trigger async S3 document cleanup.
    Falls back to synchronous cleanup if Celery is unavailable.
    """
    if not document_urls:
        return

    try:
        from tailored.tasks import cleanup_s3_tailor_order_documents_task
        task = cleanup_s3_tailor_order_documents_task.delay(document_urls)
        logger.info(f"[S3 Cleanup] Queued async cleanup task {task.id} for {len(document_urls)} documents")
    except Exception as e:
        logger.error(f"[S3 Cleanup] Failed to queue async cleanup task: {str(e)}", exc_info=True)
        logger.warning("[S3 Cleanup] Falling back to synchronous cleanup")
        self._cleanup_s3_documents_sync(document_urls)


def _update_tailor_order_documents(self, order, request, data, savepoint=None):
    """
    Handle document updates for partial_update.
    Supports:
    - Adding new documents (via FILES)
    - Removing specific documents (via 'remove_documents' field with list of URLs)

    Returns (error_response, urls_to_delete_after_commit)
    """
    current_documents = order.documents or []
    urls_to_delete_after_commit = []

    remove_documents = data.get("remove_documents", [])

    if isinstance(remove_documents, str):
        raw = remove_documents.strip()
        if raw in ["", "[]", "null", "None"]:
            remove_documents = []
        elif raw.startswith('['):
            try:
                remove_documents = json.loads(raw)
            except Exception:
                pass

    if remove_documents:
        if not isinstance(remove_documents, list):
            _rollback(savepoint)
            return self.validation_error_response(
                errors={"remove_documents": ["Must be a list of URLs to remove."]},
                message="Invalid remove_documents format."
            ), None

        invalid_urls = [url for url in remove_documents if url not in current_documents]
        if invalid_urls:
            _rollback(savepoint)
            return self.validation_error_response(
                errors={"remove_documents": ["Some URLs don't exist in current documents."]},
                message="Cannot remove non-existent documents.",
                dev_message=f"Invalid URLs: {invalid_urls}"
            ), None

        urls_to_delete_after_commit.extend(remove_documents)
        logger.info(f"Marked {len(remove_documents)} documents for deletion after commit")
        current_documents = [url for url in current_documents if url not in remove_documents]

    new_files = self._collect_document_files(request)
    is_valid, error_response, validated_files = self._validate_documents_sync(
        request, order.shop, savepoint, files=new_files
    )
    if not is_valid:
        return error_response, None

    new_document_urls, error = self._upload_documents_to_s3_sync(
        validated_files, order.shop, request.user, savepoint
    )
    if error:
        return error, None

    if new_document_urls:
        current_documents.extend(new_document_urls)
        logger.info(f"Added {len(new_document_urls)} new documents")

    if len(current_documents) > MAX_DOCUMENTS:
        if new_document_urls:
            logger.warning(f"Document limit exceeded. Cleaning up {len(new_document_urls)} newly uploaded files synchronously")
            self._cleanup_s3_documents_sync(new_document_urls)

        _rollback(savepoint)
        return self.validation_error_response(
            errors={"documents": [f"Maximum {MAX_DOCUMENTS} documents allowed. Current: {len(current_documents)}"]},
            message=f"Cannot exceed {MAX_DOCUMENTS} documents per order."
        ), None

    order.documents = current_documents
    current_count = len(current_documents)
    order.documents_details = {
        "status": "completed",
        "total_count": current_count,
        "uploaded_count": current_count,
        "updated_at": timezone.now().isoformat(),
    }

    return None, urls_to_delete_after_commit
