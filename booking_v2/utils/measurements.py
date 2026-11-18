"""Storage format for BookedProductVariant.measurements.

The column is Postgres jsonb, which does not keep object-key order (keys are
stored sorted by length, then alphabetically). Lists keep their order, so:

- A request dict is stored as an ordered list of {"name", "value"} entries -
  the format the booking APIs have always used for flat measurements:
      {"chest": "46"}  ->  [{"name": "chest", "value": "46"}]
- A nested (per-person) value stays a dict, so every existing reader (older
  API serializers, invoice PDF) keeps working, and its key order is saved
  next to it in "keys":
      {"Person 1": {"chest": "46", "sleeve_length": "4"}}
      ->  [{"name": "Person 1", "value": {...}, "keys": ["chest", "sleeve_length"]}]
  A "keys" item is a key name, or [key name, keys of its own nested dict].
"""


def _key_order(value):
    return [[key, _key_order(item)] if isinstance(item, dict) else key for key, item in value.items()]


def _apply_order(value, order):
    """Rebuild a dict in saved key order. Keys missing from the order are
    appended, so no value is ever dropped."""
    result = {}
    for item in order if isinstance(order, list) else []:
        key, sub_order = (item[0], item[1]) if isinstance(item, list) and len(item) == 2 else (item, None)
        if not isinstance(key, str) or key not in value or key in result:
            continue
        nested = value[key]
        result[key] = _apply_order(nested, sub_order) if isinstance(nested, dict) and sub_order is not None else nested
    for key, nested in value.items():
        result.setdefault(key, nested)
    return result


def to_stored_measurements(measurements):
    """Request measurements -> stored form. Non-dict input (None, an already
    stored list) is returned unchanged, as before."""
    if not isinstance(measurements, dict):
        return measurements
    stored = []
    for name, value in measurements.items():
        entry = {"name": name, "value": value}
        if isinstance(value, dict):
            entry["keys"] = _key_order(value)
        stored.append(entry)
    return stored


def ordered_value(entry):
    """A stored entry's value in saved key order (uses "keys" when present).
    Entries without "keys" and non-dict values are returned unchanged."""
    value = entry.get("value")
    if isinstance(value, dict) and "keys" in entry:
        return _apply_order(value, entry["keys"])
    return value


def from_stored_measurements(stored):
    """Stored measurements -> response dict, in the order they were saved.
    Rows saved without "keys" read exactly as before; non-list values are
    returned unchanged."""
    if not isinstance(stored, list):
        return stored
    result = {}
    for entry in stored:
        if not isinstance(entry, dict) or "name" not in entry:
            continue
        result[entry["name"]] = ordered_value(entry)
    return result
