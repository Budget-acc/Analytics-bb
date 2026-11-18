# booking_v2/api/v4/serializers/fields.py
from rest_framework import serializers
from collections import OrderedDict


class PreserveOrderJSONField(serializers.JSONField):
    """Preserves the exact order of JSON fields as received"""

    def to_representation(self, value):
        if isinstance(value, (dict, OrderedDict)) and value:
            return OrderedDict(value) if not isinstance(value, OrderedDict) else value
        return value

    def to_internal_value(self, data):
        if isinstance(data, dict):
            return OrderedDict(data)
        return super().to_internal_value(data)
