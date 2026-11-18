import re
import logging

logger = logging.getLogger("api_requests")


def _parse_nested_form_data(self, request_data, request_files):
    """
    Parse flattened form-data into nested structure.
    Handles: items[0][key], materials[0][key], measurements[key], etc.
    Also matches files: items[0][reference_image]
    """
    # JSON requests pass a plain dict; .lists() is a QueryDict-only method.
    # No bracket-notation keys exist in JSON payloads, so return safe empty defaults.
    if not hasattr(request_data, 'lists'):
        return {"items": [], "additional_charges": [], "client": {}}

    items_dict = {}
    charges_dict = {}

    # Parse items and their nested data
    for key, value_list in request_data.lists():  # Use .lists() to get list values
        # Get the actual value (form-data sends values as lists)
        value = value_list[0] if value_list else ""

        if key.startswith("items["):
            # Extract item index: items[0][...] -> 0
            match = re.match(r"items\[(\d+)\]\[(.+)\]", key)
            if match:
                item_idx = int(match.group(1))
                field_path = match.group(2)

                if item_idx not in items_dict:
                    items_dict[item_idx] = {}

                # Handle nested arrays like materials[0][quantity_meters] or measurements[Chest]
                if "[" in field_path:
                    _set_nested_dict_value(self, items_dict[item_idx], field_path, value)
                else:
                    items_dict[item_idx][field_path] = value

        elif key.startswith("additional_charges["):
            # Extract charge index: additional_charges[0][...] -> 0
            match = re.match(r"additional_charges\[(\d+)\]\[(.+)\]", key)
            if match:
                charge_idx = int(match.group(1))
                field_name = match.group(2)

                if charge_idx not in charges_dict:
                    charges_dict[charge_idx] = {}

                charges_dict[charge_idx][field_name] = value

    # Attach file uploads to items
    for file_key, file_obj in request_files.items():
        if file_key.startswith("items["):
            match = re.match(r"items\[(\d+)\]\[(.+)\]", file_key)
            if match:
                item_idx = int(match.group(1))
                field_name = match.group(2)

                if item_idx not in items_dict:
                    items_dict[item_idx] = {}

                items_dict[item_idx][field_name] = file_obj

    # Convert dicts to sorted lists
    items_list = [items_dict[i] for i in sorted(items_dict.keys())] if items_dict else []
    charges_list = [charges_dict[i] for i in sorted(charges_dict.keys())] if charges_dict else []

    # Handle client data
    client_data = {}
    for key, value_list in request_data.lists():
        value = value_list[0] if value_list else ""
        if key.startswith("client["):
            match = re.match(r"client\[(.+)\]", key)
            if match:
                field_name = match.group(1)
                client_data[field_name] = value

    return {
        "items": items_list,
        "additional_charges": charges_list,
        "client": client_data
    }


def _set_nested_dict_value(self, target_dict, field_path, value):
    """
    Helper method to set nested values in dictionary structure.
    Handles paths like:
    - materials[0][variant_id] -> dict["materials"][0]["variant_id"] = value
    - measurements[Chest] -> dict["measurements"]["Chest"] = value
    """
    # Split by [ and ] to get parts
    parts = re.split(r"[\[\]]", field_path)
    parts = [p for p in parts if p]  # Remove empty strings

    if not parts:
        return

    current = target_dict

    # Process all parts except the last one
    for i in range(len(parts) - 1):
        part = parts[i]

        # Check if this part is a numeric index
        try:
            idx = int(part)
            # This is a list index
            if not isinstance(current, list):
                # This shouldn't happen in correct parsing
                logger.error(f"Expected list but got {type(current)} for part {part}")
                return

            # Ensure list has enough elements
            while len(current) <= idx:
                current.append({})

            current = current[idx]

        except ValueError:
            # This is a dictionary key
            if not isinstance(current, dict):
                logger.error(f"Expected dict but got {type(current)} for part {part}")
                return

            # Check if next part is numeric to decide what to create
            next_part = parts[i + 1] if i + 1 < len(parts) else None
            is_next_numeric = False

            if next_part:
                try:
                    int(next_part)
                    is_next_numeric = True
                except ValueError:
                    pass

            # Create the appropriate structure
            if part not in current:
                if is_next_numeric:
                    current[part] = []
                else:
                    current[part] = {}

            current = current[part]

    # Set the final value
    final_part = parts[-1]

    try:
        # Try to interpret as list index
        idx = int(final_part)
        if isinstance(current, list):
            while len(current) <= idx:
                current.append(None)
            current[idx] = value
        else:
            # Fallback: treat as dict key
            current[final_part] = value
    except ValueError:
        # It's a dict key
        if isinstance(current, dict):
            current[final_part] = value
        else:
            logger.error(f"Cannot set key '{final_part}' on {type(current)}")
