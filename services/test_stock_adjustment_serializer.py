"""DB-free tests for the stock-adjustment request serializer (shape and type only)."""
from django.test import SimpleTestCase

from services.api.v3.serializers.stock_adjustment import StockAdjustmentRequestSerializer
from services.constants.stock_adjustment import StockAdjustmentMessages as M

ITEM = {"variant_id": 101, "quantity": 1}


def validate(**payload):
    serializer = StockAdjustmentRequestSerializer(data=payload)
    return serializer.is_valid(), serializer


class OptionalReasonTests(SimpleTestCase):
    def test_reason_can_be_omitted_or_null(self):
        for payload in ({}, {"reason": None}):
            with self.subTest(payload=payload):
                ok, serializer = validate(action="mark_unavailable", variants=[ITEM], **payload)
                self.assertTrue(ok, serializer.errors)
                self.assertIsNone(serializer.validated_data["variants"][0]["reason"])

    def test_explicit_and_per_item_reasons_still_apply(self):
        ok, serializer = validate(
            action="restore", reason="damage",
            variants=[ITEM, {"variant_id": 102, "quantity": 1, "reason": "maintenance"}],
        )
        self.assertTrue(ok, serializer.errors)
        self.assertEqual([item["reason"] for item in serializer.validated_data["variants"]], ["damage", "maintenance"])
        ok, serializer = validate(action="restore", variants=[{**ITEM, "reason": "maintenance"}])
        self.assertTrue(ok, serializer.errors)
        self.assertEqual(serializer.validated_data["variants"][0]["reason"], "maintenance")

    def test_invalid_reason_and_other_without_note_still_rejected(self):
        ok, serializer = validate(action="restore", reason="lost", variants=[ITEM])
        self.assertFalse(ok)
        self.assertIn(M.INVALID_REASON.message, str(serializer.errors))
        ok, serializer = validate(action="restore", reason="other", variants=[ITEM])
        self.assertFalse(ok)
        self.assertIn(M.NOTE_REQUIRED_FOR_OTHER.message, str(serializer.errors))

    def test_action_and_variants_still_required(self):
        ok, serializer = validate(variants=[ITEM])
        self.assertFalse(ok)
        self.assertIn("action", serializer.errors)
        ok, serializer = validate(action="restore")
        self.assertFalse(ok)
        self.assertIn("variants", serializer.errors)
