"""Storage round trip of booking measurements (no database needed)."""
import json
from unittest import mock

from django.test import SimpleTestCase
from rest_framework import serializers

from booking_v2.api.v5.serializers import BookedProductVariantSerializer
from booking_v2.models import BookedProductVariant
from booking_v2.utils.measurements import from_stored_measurements, to_stored_measurements

PERSON = {"chest": "46", "sleeve_length": "467", "armhole": "468", "inseam": "56", "thigh": "JHH"}


def jsonb(value):
    """What Postgres jsonb returns: object keys sorted by (length, key)."""
    if isinstance(value, dict):
        return {key: jsonb(value[key]) for key in sorted(value, key=lambda k: (len(k), k))}
    if isinstance(value, list):
        return [jsonb(item) for item in value]
    return value


def round_trip(measurements):
    return from_stored_measurements(jsonb(json.loads(json.dumps(to_stored_measurements(measurements)))))


def assert_ordered_equal(test, actual, expected):
    test.assertEqual(json.dumps(actual), json.dumps(expected))


class MeasurementStorageTests(SimpleTestCase):
    def test_flat_storage_is_unchanged(self):
        flat = {"sleeve_length": "4", "chest": 46}
        old_conversion = [{"name": k, "value": v} for k, v in flat.items()]
        self.assertEqual(to_stored_measurements(flat), old_conversion)
        assert_ordered_equal(self, round_trip(flat), flat)

    def test_nested_keeps_structure_and_order(self):
        nested = {"Person 1": PERSON, "Person 2": {"sleeve_length": "24", "chest": "40"}}
        self.assertNotEqual(list(jsonb(PERSON)), list(PERSON))  # jsonb really reorders
        assert_ordered_equal(self, round_trip(nested), nested)

    def test_mixed_deep_and_empty(self):
        mixed = {
            "notes": "loose fit",
            "Person 1": {"waist": "30", "top": {"sleeve_length": "4", "chest": "40"}},
            "Person 2": {},
        }
        assert_ordered_equal(self, round_trip(mixed), mixed)
        self.assertEqual(to_stored_measurements(mixed)[2]["keys"], [])

    def test_non_dict_input_is_unchanged(self):
        stored = [{"name": "chest", "value": "46"}]
        self.assertIsNone(to_stored_measurements(None))
        self.assertIs(to_stored_measurements(stored), stored)
        self.assertEqual(from_stored_measurements({"chest": "46"}), {"chest": "46"})

    def test_legacy_rows_read_as_before(self):
        legacy = [{"name": "Person 1", "value": {"thigh": "JHH", "chest": "46"}}, {"name": "hip", "value": "38"}]
        self.assertEqual(from_stored_measurements(legacy), {m["name"]: m["value"] for m in legacy})
        self.assertEqual(from_stored_measurements(["junk", {"value": "x"}]), {})

    def test_out_of_sync_keys_never_drop_values(self):
        stored = [{"name": "P", "value": {"a": 1, "b": 2, "c": 3}, "keys": ["c", "zz", 5, ["a"], "c"]}]
        assert_ordered_equal(self, from_stored_measurements(stored), {"P": {"c": 3, "a": 1, "b": 2}})

    def test_stored_nested_form_still_detected_by_invoice_pdf(self):
        from whatsapp_integration.v3.pdf_template import BasePDFService

        self.assertTrue(BasePDFService._is_person_measurements(to_stored_measurements({"Person 1": PERSON})))

    def test_retrieve_serializer_returns_saved_order(self):
        # The parent representation needs related rows; only the measurements step is under test.
        item = BookedProductVariant(measurements=jsonb(to_stored_measurements({"Person 1": PERSON})))
        with mock.patch.object(serializers.ModelSerializer, "to_representation", return_value={}):
            data = BookedProductVariantSerializer().to_representation(item)
        assert_ordered_equal(self, data["measurements"], {"Person 1": PERSON})
