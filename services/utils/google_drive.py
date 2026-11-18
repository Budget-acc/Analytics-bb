"""
Google Drive integration for bulk product image import.

Requires the `google-api-python-client` and `google-auth` packages (not yet
installed in this environment - manual `pip install` prerequisite) and a
service-account JSON credential file, whose path is read from the
GOOGLE_DRIVE_SERVICE_ACCOUNT_PATH env var (mirrors how
notifications/api/v3/utils/firebase_utils.py loads FIREBASE_SERVICE_ACCOUNT_PATH).
"""
import io
import os
import logging

logger = logging.getLogger("api_requests")

DRIVE_SERVICE = None

# MIME types accepted as bulk-import source images.
ALLOWED_DRIVE_IMAGE_MIME_TYPES = frozenset({
    "image/jpeg", "image/jpg", "image/png", "image/webp",
})


class DriveAccessError(Exception):
    """Raised when the configured service account cannot access a given folder/file."""


def get_drive_service():
    """
    Lazy singleton Google Drive v3 API client, built from a service-account
    credential file. Raises a clear error if the credential path is missing
    or invalid, instead of failing deep inside a background task.
    """
    global DRIVE_SERVICE
    if DRIVE_SERVICE:
        return DRIVE_SERVICE

    service_account_path = os.getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT_PATH")
    if not service_account_path or not os.path.exists(service_account_path):
        logger.error("GOOGLE_DRIVE_SERVICE_ACCOUNT_PATH is not set or file does not exist")
        raise DriveAccessError(
            "GOOGLE_DRIVE_SERVICE_ACCOUNT_PATH must be set to a valid service account JSON file path."
        )

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        credentials = service_account.Credentials.from_service_account_file(
            service_account_path,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        DRIVE_SERVICE = build("drive", "v3", credentials=credentials, cache_discovery=False)
        logger.info("Google Drive service initialized successfully")
        return DRIVE_SERVICE
    except ImportError as e:
        logger.error(f"Google Drive SDK not installed: {e}")
        raise DriveAccessError(
            "google-api-python-client / google-auth are not installed."
        ) from e
    except Exception:
        logger.exception("Failed to initialize Google Drive service")
        raise


def validate_folder_access(folder_id):
    """
    Confirms the folder exists and is accessible to the service account.
    Called synchronously from the API's create() action, before any
    BulkProductImport row is created, so an inaccessible folder fails fast
    with a clean error instead of a doomed background job.
    Raises DriveAccessError on failure.
    """
    drive_service = get_drive_service()
    try:
        file_meta = drive_service.files().get(
            fileId=folder_id, fields="id, name, mimeType", supportsAllDrives=True
        ).execute()
    except Exception as e:
        logger.warning(f"[Drive] Folder access validation failed for folder_id={folder_id}: {e}")
        raise DriveAccessError(
            "Google Drive folder not found or not accessible to the service account."
        ) from e

    if file_meta.get("mimeType") != "application/vnd.google-apps.folder":
        raise DriveAccessError("The provided Google Drive ID is not a folder.")

    return file_meta


def iter_drive_image_pages(folder_id, page_size=None):
    """
    Generator yielding one page of image-file metadata at a time (not the
    full listing collected into memory) - required for folders with tens of
    thousands of files. Each yielded item is a dict with keys: id, name,
    mimeType, size (bytes, as a string per Drive API - caller should int() it).
    """
    from django.conf import settings

    page_size = page_size or getattr(settings, "BULK_IMPORT_DRIVE_PAGE_SIZE", 500)
    drive_service = get_drive_service()

    # Drive query language requires literal single quotes inside string values
    # to be backslash-escaped - folder_id is caller-supplied, so build the
    # query safely instead of interpolating it raw (unescaped, a crafted
    # folder_id could break out of the 'folder_id' in parents clause and
    # inject additional query terms).
    safe_folder_id = folder_id.replace("\\", "\\\\").replace("'", "\\'")
    query = f"'{safe_folder_id}' in parents and mimeType contains 'image/' and trashed = false"
    page_token = None

    while True:
        response = drive_service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType, size)",
            pageSize=page_size,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()

        yield response.get("files", [])

        page_token = response.get("nextPageToken")
        if not page_token:
            break


def download_drive_file(drive_service, file_id):
    """Downloads a single Drive file's bytes into memory. One file at a time."""
    from googleapiclient.http import MediaIoBaseDownload

    request = drive_service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()
