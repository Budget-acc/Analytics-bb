# booking_v2/tasks/utils.py
import logging
import os

logger = logging.getLogger("api_requests")


def cleanup_orphaned_temp_files(file_data):
    """Clean up any temp files that still exist"""
    cleaned = 0
    for file_info in file_data:
        temp_path = file_info["path"]
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
                cleaned += 1
            except Exception as e:
                logger.error(f"Failed to cleanup orphaned file {temp_path}: {str(e)}")

    if cleaned > 0:
        logger.info(f"Cleaned up {cleaned} orphaned temp files")
