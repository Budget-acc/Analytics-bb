import json
import logging

logger = logging.getLogger("api_requests")


def _parse_multipart_to_json(self, data):
    """
    For multipart/form-data requests, `vendor`/`existing_products`/
    `new_products`/`additional_charges` are expected as JSON-encoded
    strings (documents come separately via request.FILES) - much simpler
    than Booking's bracket-notation parsing since Purchase's payload
    doesn't need per-field multipart keys.
    """
    # `data` is a Django QueryDict for multipart requests - the plain
    # dict(query_dict) constructor can turn single-value scalar fields
    # into single-element lists (QueryDict supports multiple values per
    # key), which then breaks any downstream code expecting a plain str/
    # int (e.g. purchase_date's strptime). QueryDict.dict() is Django's
    # own documented API for flattening to single (last) value per key -
    # use it when available, falling back to dict() for plain dicts
    # (already-parsed JSON requests never reach this function, but
    # defensive either way).
    result = data.dict() if hasattr(data, "dict") else dict(data)
    for key in self.JSON_STRING_FIELDS:
        value = result.get(key)
        if isinstance(value, str) and value.strip().startswith(("{", "[")):
            try:
                result[key] = json.loads(value)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse JSON string for '{key}': {e}")
    return result


__all__ = ["_parse_multipart_to_json"]
