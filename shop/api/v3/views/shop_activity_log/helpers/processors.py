def _process_logs(self, logs_page):
    """Process logs with optimized data fetching"""

    # Collect variant_deleted logs for bulk unique-code lookup.
    variant_deleted_logs = [
        log for log in logs_page
        if log.action == "variant_deleted" and log.content_type and log.object_id
    ]

    # Fetch variant unique codes in bulk for variant_deleted logs
    variant_codes_map = {}
    if variant_deleted_logs:
        variant_codes_map = self._fetch_variant_codes(variant_deleted_logs)

    # Build result items in the same order as logs_page (already newest-first).
    result_items = []
    for log in logs_page:
        item = self._build_log_item(log)
        if log.action == "variant_deleted" and log.content_type and log.object_id:
            variant_key = (log.content_type_id, log.object_id)
            unique_code = variant_codes_map.get(variant_key)
            if unique_code:
                item["unique_code"] = unique_code
        result_items.append(item)

    return result_items
