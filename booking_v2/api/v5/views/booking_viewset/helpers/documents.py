import logging
import json
from django.db import transaction
from django.utils import timezone
from ERP.utils.s3_image_uploads import upload_files_to_s3
import boto3
from django.conf import settings
from urllib.parse import urlparse
from booking_v2.tasks import cleanup_s3_documents_task

logger = logging.getLogger("api_requests")


def _validate_and_upload_documents(self, request, shop, savepoint):
    """
    Validate and upload document images to S3.
    Returns (uploaded_urls, error_response)

    SECURITY: Validates MIME types to prevent malicious file uploads.
    """
    # Allowed image MIME types
    ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']

    files = request.FILES.getlist("documents") or request.FILES.getlist("documents[]") or []

    if not files:
        files = [file for key, file in request.FILES.items() if key.startswith("documents")]

    if not files:
        return [], None

    # Validate MIME types BEFORE uploading
    for f in files:
        content_type = getattr(f, 'content_type', None)
        if content_type not in ALLOWED_IMAGE_TYPES:
            transaction.savepoint_rollback(savepoint)
            logger.warning(
                f"Rejected file upload: Invalid content type '{content_type}'. "
                f"File: {getattr(f, 'name', 'unknown')}"
            )
            return None, self.validation_error_response(
                errors={"documents": [
                    f"Invalid file type: {content_type}. "
                    f"Allowed types: JPEG, PNG, WebP, GIF"
                ]},
                message="Invalid document file type."
            )

    try:
        uploaded_objects = upload_files_to_s3(
            files=files,
            shop=shop,
            reporter=request.user,
            subfolder="booking_documents",
            max_files=10,
            max_size_bytes=3 * 1024 * 1024,  # 3MB per image
            max_image_dims=(2000, 2000),
        )

        uploaded_urls = [obj["url"] for obj in uploaded_objects]
        logger.info(f"Successfully uploaded {len(uploaded_urls)} documents to S3")

        return uploaded_urls, None

    except ValueError as ve:
        transaction.savepoint_rollback(savepoint)
        logger.error(f"Validation error during S3 upload: {str(ve)}")
        return None, self.validation_error_response(
            errors={"documents": [str(ve)]},
            message="Invalid document images.",
            dev_message=str(ve)
        )
    except Exception as e:
        transaction.savepoint_rollback(savepoint)
        logger.error(f"S3 upload failed for booking documents: {str(e)}", exc_info=True)
        return None, self.error_response(
            message="Failed to upload document images. Please try again.",
            dev_message=f"S3 upload error: {str(e)}"
        )


def _cleanup_s3_documents(self, document_urls):
    """
    Wrapper to trigger async S3 document cleanup.

    This method immediately returns and schedules cleanup in background.
    Used for non-critical cleanup operations where immediate deletion isn't required.

    For critical cleanup (e.g., after failed transactions), use _cleanup_s3_documents_sync().
    """
    if not document_urls:
        return

    try:
        # Dispatch async task
        task = cleanup_s3_documents_task.delay(document_urls)
        logger.info(f"[S3 Cleanup] Queued async cleanup task {task.id} for {len(document_urls)} documents")
    except Exception as e:
        logger.error(f"[S3 Cleanup] Failed to queue async cleanup task: {str(e)}", exc_info=True)
        # Fallback to synchronous cleanup if Celery is unavailable
        logger.warning("[S3 Cleanup] Falling back to synchronous cleanup")
        self._cleanup_s3_documents_sync(document_urls)


def _cleanup_s3_documents_sync(self, document_urls):
    """
    Synchronous S3 document cleanup for critical operations.

    Use this for:
    - Transaction rollback cleanup (need immediate deletion)
    - Cleanup after upload failures

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
                # Parse URL safely using urlparse
                parsed = urlparse(url)

                # Validate it's actually from our bucket
                if parsed.netloc != expected_host:
                    logger.warning(
                        f"[S3 Cleanup Sync] Skipping deletion: URL {url} doesn't match expected bucket host {expected_host}"
                    )
                    continue

                # Extract key from path (remove leading slash)
                key = parsed.path.lstrip('/')

                if not key:
                    logger.warning(f"[S3 Cleanup Sync] Skipping deletion: No key found in URL {url}")
                    continue

                s3.delete_object(Bucket=bucket, Key=key)
                logger.info(f"[S3 Cleanup Sync] Successfully deleted S3 object: {key}")

            except Exception as e:
                logger.error(f"[S3 Cleanup Sync] Failed to delete S3 document {url}: {str(e)}", exc_info=True)
                # Continue with other deletions even if one fails

    except Exception as e:
        logger.error(f"[S3 Cleanup Sync] Failed to initialize S3 client: {str(e)}", exc_info=True)


def _update_booking_documents(self, booking, request, data, savepoint):
    """
    Handle document updates for partial_update.
    Supports:
    - Adding new documents (via FILES)
    - Removing specific documents (via 'remove_documents' field with list of URLs)
    - Replacing all documents (via 'documents' field with list of URLs to keep + new uploads)

    Returns (error_response, urls_to_delete_after_commit)
    """
    # Get current documents
    current_documents = booking.documents or []

    # Track URLs to delete AFTER transaction commits
    urls_to_delete_after_commit = []

    # Check if user wants to remove specific documents
    remove_documents = data.get("remove_documents", [])

    # Allow clients to send JSON-string arrays
    if isinstance(remove_documents, str):
        raw = remove_documents.strip()
        if raw in ["", "[]", "null", "None"]:
            remove_documents = []
        elif raw.startswith('['):
            try:
                parsed = json.loads(raw)
                remove_documents = parsed
            except Exception:
                # Keep original; validation below will raise a clear error
                pass

    if remove_documents:
        if not isinstance(remove_documents, list):
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"remove_documents": ["Must be a list of URLs to remove."]},
                message="Invalid remove_documents format."
            ), None

        # Validate URLs to remove exist in current documents
        invalid_urls = [url for url in remove_documents if url not in current_documents]
        if invalid_urls:
            transaction.savepoint_rollback(savepoint)
            return self.validation_error_response(
                errors={"remove_documents": ["Some URLs don't exist in current documents."]},
                message="Cannot remove non-existent documents.",
                dev_message=f"Invalid URLs: {invalid_urls}"
            ), None

        # DON'T delete from S3 yet - just track them for later
        urls_to_delete_after_commit.extend(remove_documents)
        logger.info(f"Marked {len(remove_documents)} documents for deletion after commit")

        # Remove from current list
        current_documents = [url for url in current_documents if url not in remove_documents]

    # Check for new document uploads
    new_document_urls, error = self._validate_and_upload_documents(request, booking.shop, savepoint)
    if error:
        return error, None

    # Add new documents to current list
    if new_document_urls:
        current_documents.extend(new_document_urls)
        logger.info(f"Added {len(new_document_urls)} new documents")

    # Enforce maximum documents limit (e.g., 10)
    MAX_DOCUMENTS = 10
    if len(current_documents) > MAX_DOCUMENTS:
        # Use SYNC cleanup for transaction rollback scenario
        if new_document_urls:
            logger.warning(f"Document limit exceeded. Cleaning up {len(new_document_urls)} newly uploaded files synchronously")
            self._cleanup_s3_documents_sync(new_document_urls)

        transaction.savepoint_rollback(savepoint)
        return self.validation_error_response(
            errors={"documents": [f"Maximum {MAX_DOCUMENTS} documents allowed. Current: {len(current_documents)}"]},
            message=f"Cannot exceed {MAX_DOCUMENTS} documents per booking."
        ), None

    # Update booking with new document list
    booking.documents = current_documents

    # Update documents_details based only on CURRENT uploaded documents (no dependence on previous counts)
    current_count = len(current_documents)
    booking.documents_details = {
        "status": "completed",
        "total_count": current_count,
        "uploaded_count": current_count,
        "updated_at": timezone.now().isoformat(),
    }

    # Return URLs to delete after commit (will use async cleanup)
    return None, urls_to_delete_after_commit


def _parse_multipart_to_json(self, data):
    """
    Convert multipart/form-data with bracket notation to proper JSON structure.
    Handles both single item arrays and nested objects.
    Also parses JSON strings for complex fields.

    Examples:
    - client[client_name] -> {"client": {"client_name": "value"}}
    - client -> '{"client_name": "value"}' (JSON string) -> {"client": {"client_name": "value"}}
    - variants[id] -> {"variants": [{"id": "value"}]}
    - variants -> '[{"id": 95}]' (JSON string) -> {"variants": [{"id": 95}]}
    - documents[0], documents[1] -> handled by FILES
    """
    import json

    result = {}

    # Track nested objects and arrays separately
    nested_objects = {}  # For client[field]
    array_items = {}     # For variants[field], additional_charges[field]

    for key, value in data.items():
        # Skip internal fields
        if key.startswith('_'):
            continue

        # Simple fields without brackets
        if '[' not in key:
            result[key] = value
            # Check if it's a JSON string for known complex fields
            if key in self.JSON_STRING_FIELDS:
                if isinstance(value, str) and value.strip().startswith(('{', '[')):
                    try:
                        parsed = json.loads(value)
                        result[key] = parsed
                        logger.debug(f"Parsed JSON string for field '{key}'")
                        continue
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse JSON string for '{key}': {e}")
                        result[key] = value
                        continue

        # Parse bracket notation
        base_key = key.split('[')[0]
        remaining = key[len(base_key)+1:].rstrip(']')

        # Determine if it's a nested object or array based on base_key
        if base_key in ['client']:  # Nested object
            if base_key not in nested_objects:
                nested_objects[base_key] = {}
            nested_objects[base_key][remaining] = value

        elif base_key in ['variants', 'additional_charges', 'remove_documents', 'payments']:  # Array items
            if base_key not in array_items:
                array_items[base_key] = {}
            array_items[base_key][remaining] = value
        else:
            result[key] = value

    # Add nested objects to result (only if not already added as JSON string)
    for key, obj in nested_objects.items():
        if key not in result:
            result[key] = obj

    # Convert array items to list format (single item arrays from form-data)
    for key, obj in array_items.items():
        if key not in result:  # Only if not already parsed from JSON string
            if key == 'remove_documents':
                # Collect all values as strings (they are URLs)
                result[key] = list(obj.values())
            else:
                # Convert to list with single item
                converted_obj = {}
                for k, v in obj.items():
                    try:
                        converted_obj[k] = int(v)
                    except (ValueError, TypeError):
                        converted_obj[k] = v

                result[key] = [converted_obj]  # Single item array

    # Convert string numbers for simple fields with better error handling
    for key in ['advance_amount', 'discount_amount', 'security_amount']:
        if key in result:
            try:
                result[key] = int(result[key])
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Failed to convert {key}={result[key]} to int: {e}. "
                    f"Keeping original value."
                )
                # Keep original value, validation will catch it later

    # security_payment_method is a string, no conversion needed
    if 'security_payment_method' not in result:
        result['security_payment_method'] = 'cash'

    # Strict boolean conversion for send_invoice
    if 'send_invoice' in result:
        value = result['send_invoice']
        if isinstance(value, bool):
            result['send_invoice'] = value
        elif isinstance(value, str):
            value_lower = value.lower().strip()
            if value_lower in ['true', '1']:
                result['send_invoice'] = True
            elif value_lower in ['false', '0', '']:
                result['send_invoice'] = False
            else:
                logger.warning(
                    f"Invalid boolean value for send_invoice: '{value}'. "
                    f"Expected true/false or 1/0. Defaulting to False."
                )
                result['send_invoice'] = False
        elif isinstance(value, int):
            result['send_invoice'] = bool(value)
        else:
            logger.warning(f"Unexpected type for send_invoice: {type(value)}. Defaulting to False.")
            result['send_invoice'] = False

    # Staff ID conversion with error handling
    if 'staff_id' in result:
        try:
            result['staff_id'] = int(result['staff_id'])
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to convert staff_id={result['staff_id']} to int: {e}")
            # Keep original, validation will handle it

    return result


def _collect_document_files(self, request):
    """Collect uploaded document files from request in all supported formats."""
    if not request or not getattr(request, 'FILES', None):
        return []

    all_files = []

    # Collect files in all formats
    for key in request.FILES.keys():
        if key.startswith('documents['):
            all_files.extend(request.FILES.getlist(key))

    if 'documents[]' in request.FILES:
        all_files.extend(request.FILES.getlist('documents[]'))

    if 'documents' in request.FILES:
        all_files.extend(request.FILES.getlist('documents'))

    # If still no files, try all keys
    if not all_files:
        for key in request.FILES:
            all_files.extend(request.FILES.getlist(key))

    return all_files


def _save_temp_documents(self, request, booking_id, files=None):
    """
    Save uploaded files to temporary location.
    Assumes files have already been validated.
    """
    uploaded_files = []

    all_files = list(files) if files is not None else self._collect_document_files(request)

    for file in all_files:
        try:
            import os
            import uuid
            import tempfile

            # Use tempfile.gettempdir() for cross-platform compatibility
            temp_dir = os.path.join(tempfile.gettempdir(), 'booking_docs')
            os.makedirs(temp_dir, exist_ok=True)

            # Generate unique filename
            temp_filename = f"{booking_id}_{uuid.uuid4()}_{file.name}"
            # Clean filename for cross-platform compatibility
            import re
            temp_filename = re.sub(r'[<>:"/\\|?*]', '_', temp_filename)
            temp_path = os.path.join(temp_dir, temp_filename)

            # Save file temporarily
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
            # Cleanup any saved files
            for file_info in uploaded_files:
                import os
                if os.path.exists(file_info['path']):
                    try:
                        os.unlink(file_info['path'])
                    except:
                        pass
            return {
                'error': self.error_response(
                    message="Failed to save document files.",
                    dev_message=str(e)
                )
            }

    return uploaded_files


def _cleanup_temp_files(self, uploaded_files):
    """
    Clean up temporary files
    """
    import os

    for file_info in uploaded_files:
        if os.path.exists(file_info['path']):
            try:
                os.unlink(file_info['path'])
                logger.info(f"Cleaned up temp file: {file_info['path']}")
            except Exception as e:
                logger.error(f"Failed to cleanup temp file {file_info['path']}: {str(e)}")


def _validate_documents_sync(self, request, shop, savepoint, files=None):
    """
    Validate document files synchronously before booking creation.
    Returns: (is_valid, error_response, files_list)
    """
    # Allowed image MIME types
    ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/gif']

    all_files = list(files) if files is not None else self._collect_document_files(request)

    if not all_files:
        return True, None, []  # No files to validate

    # Enforce maximum document count early
    MAX_DOCUMENTS = 10
    if len(all_files) > MAX_DOCUMENTS:
        transaction.savepoint_rollback(savepoint)
        return False, self.validation_error_response(
            errors={"documents": [f"Maximum {MAX_DOCUMENTS} documents allowed. Current: {len(all_files)}"]},
            message=f"Cannot exceed {MAX_DOCUMENTS} documents per booking."
        ), all_files

    # Validate each file
    for f in all_files:
        # Check MIME type
        content_type = getattr(f, 'content_type', None)
        if content_type not in ALLOWED_IMAGE_TYPES:
            transaction.savepoint_rollback(savepoint)
            return False, self.validation_error_response(
                errors={"documents": [
                    f"Invalid file type: {content_type}. "
                    f"Allowed types: JPEG, JPG, PNG, WebP, GIF"
                ]},
                message="Invalid document file type."
            ), []

        # Check file size (max 3MB)
        max_size_bytes = 3 * 1024 * 1024
        file_size = getattr(f, 'size', None)
        if file_size is None:
            try:
                f.seek(0, 2)
                file_size = f.tell()
                f.seek(0)
            except Exception:
                file_size = None

        if file_size > max_size_bytes:
            transaction.savepoint_rollback(savepoint)
            return False, self.validation_error_response(
                errors={"documents": [
                    f"File '{f.name}' is too large ({file_size / 1024 / 1024:.2f}MB). "
                    f"Maximum size is 3MB."
                ]},
                message="Document file is too large."
            ), []

        # Additional validation can be added here (e.g., check dimensions)
        # For now, we just validate MIME type and size

    return True, None, all_files


def _upload_documents_to_s3_sync(self, files, shop, reporter, savepoint):
    """Upload provided files to S3 synchronously and return (urls, error_response)."""
    if not files:
        return [], None

    try:
        uploaded_objects = upload_files_to_s3(
            files=files,
            shop=shop,
            reporter=reporter,
            subfolder="booking_documents",
            max_files=10,
            max_size_bytes=3 * 1024 * 1024,
            max_image_dims=(2000, 2000),
        )

        uploaded_urls = [obj["url"] for obj in uploaded_objects]
        return uploaded_urls, None

    except ValueError as ve:
        transaction.savepoint_rollback(savepoint)
        logger.error(f"Validation error during S3 upload: {str(ve)}")
        return None, self.validation_error_response(
            errors={"documents": [str(ve)]},
            message="Invalid document images.",
            dev_message=str(ve)
        )
    except Exception as e:
        transaction.savepoint_rollback(savepoint)
        logger.error(f"S3 upload failed for booking documents: {str(e)}", exc_info=True)
        return None, self.error_response(
            message="Failed to upload document images. Please try again.",
            dev_message=f"S3 upload error: {str(e)}"
        )
