from rest_framework import serializers
from collections import OrderedDict


class PreserveOrderJSONField(serializers.JSONField):
    """Preserves the exact order of JSON fields as received"""

    def to_representation(self, value):
        if isinstance(value, (dict, OrderedDict)) and value:
            # Ensure we return data in the same order it was stored
            return OrderedDict(value) if not isinstance(value, OrderedDict) else value
        return value

    def to_internal_value(self, data):
        if isinstance(data, dict):
            # Preserve input order by converting to OrderedDict
            return OrderedDict(data)
        return super().to_internal_value(data)
