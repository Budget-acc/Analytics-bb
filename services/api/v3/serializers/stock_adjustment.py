"""Serializers for the product stock-adjustment API.

They validate shape and type only. Ownership and amount checks need the
locked variant rows, so they run in services.utils.stock_adjustment.
Field help_text doubles as the Swagger documentation.
"""
from decimal import Decimal

from rest_framework import serializers

from services.constants.stock_adjustment import (
    MAX_ADJUSTMENT_ITEMS,
    MAX_NOTE_LENGTH,
    MAX_STOCK_VALUE,
    StockAdjustmentMessages as M,
)
from services.models import ProductVariantStockAdjustment

Action = ProductVariantStockAdjustment.Action
Reason = ProductVariantStockAdjustment.Reason


def _all_errors(msg, *keys):
    return {key: msg.message for key in keys}


def _choice_field(choices, msg, help_text, required=True, allow_null=False):
    return serializers.ChoiceField(
        choices=choices, required=required, allow_null=allow_null, help_text=help_text,
        error_messages=_all_errors(msg, "required", "null", "invalid_choice", "blank"),
    )


def _note_field(help_text):
    return serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=MAX_NOTE_LENGTH, help_text=help_text,
        error_messages=_all_errors(M.NOTE_TOO_LONG, "max_length", "invalid"),
    )


class StockAdjustmentItemSerializer(serializers.Serializer):
    variant_id = serializers.IntegerField(
        min_value=1, max_value=MAX_STOCK_VALUE,
        help_text=(
            "Purpose: The product variant whose stock or fabric is moved. Must belong to the product in the URL "
            "and must not be deleted; each variant can appear only once per request. "
            "Location: request body (variants[]). Format/Type: positive integer. Required."
        ),
        error_messages=_all_errors(M.INVALID_VARIANT_ID, "required", "null", "invalid", "min_value", "max_value", "max_string_length"),
    )
    quantity = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=MAX_STOCK_VALUE,
        help_text=(
            "Purpose: Number of units to move. mark_unavailable: cannot exceed the variant's current usable stock. "
            "restore: cannot exceed the variant's current unavailable_stock. "
            "Location: request body (variants[]). Format/Type: integer >= 1. "
            "Optional, but each item needs quantity and/or fabric_length."
        ),
        error_messages=_all_errors(M.INVALID_QUANTITY, "invalid", "min_value", "max_value", "max_string_length"),
    )
    fabric_length = serializers.DecimalField(
        required=False, allow_null=True, max_digits=10, decimal_places=2, min_value=Decimal("0.01"),
        help_text=(
            "Purpose: Fabric length in metres to move. Only for variants that track fabric (fabric_length is not null). "
            "mark_unavailable: cannot exceed the current usable fabric_length. restore: cannot exceed the current "
            "unavailable_fabric_length. Location: request body (variants[]). "
            "Format/Type: decimal string or number, > 0, up to 2 decimal places, at most 8 whole digits (e.g. \"4.50\"). "
            "Optional, but each item needs quantity and/or fabric_length."
        ),
        error_messages=_all_errors(
            M.INVALID_FABRIC_LENGTH,
            "invalid", "min_value", "max_value", "max_digits", "max_decimal_places", "max_whole_digits", "max_string_length",
        ),
    )
    reason = _choice_field(
        Reason.choices, M.INVALID_REASON, required=False, allow_null=True,
        help_text=(
            "Purpose: Overrides the top-level reason for this variant only. "
            "Location: request body (variants[]). Format/Type: string, one of damage, maintenance, other. "
            "Optional. Default: the top-level reason (null when none is sent)."
        ),
    )
    note = _note_field(
        "Purpose: Overrides the top-level note for this variant only. "
        "Location: request body (variants[]). Format/Type: string, at most 500 characters. "
        "Optional. Default: the top-level note. Required (here or at the top level) when the effective reason is other."
    )

    default_error_messages = {"invalid": M.INVALID_VARIANT_ID.message}

    def validate(self, attrs):
        if attrs.get("quantity") is None and attrs.get("fabric_length") is None:
            raise serializers.ValidationError(M.AMOUNT_REQUIRED.message)
        return attrs


class StockAdjustmentRequestSerializer(serializers.Serializer):
    action = _choice_field(
        Action.choices, M.INVALID_ACTION,
        help_text=(
            "Purpose: Direction of the move for every item in the request. "
            "mark_unavailable moves amounts from usable stock / fabric_length to unavailable; "
            "restore moves them back. Location: request body. "
            "Format/Type: string, one of mark_unavailable, restore. Required."
        ),
    )
    reason = _choice_field(
        Reason.choices, M.INVALID_REASON, required=False, allow_null=True,
        help_text=(
            "Purpose: Why the stock is moved; stored on every history row so damage and maintenance stay "
            "distinguishable. Used for every item that does not set its own reason. Location: request body. "
            "Format/Type: string, one of damage, maintenance, other. Optional. Default: null (no reason stored)."
        ),
    )
    note = _note_field(
        "Purpose: Free-text detail stored in the history (e.g. what was damaged or repaired). Used for every item "
        "that does not set its own note. Location: request body. Format/Type: string, at most 500 characters. "
        "Optional. Required when the effective reason of an item is other."
    )
    variants = StockAdjustmentItemSerializer(
        many=True, allow_empty=False, max_length=MAX_ADJUSTMENT_ITEMS,
        help_text=(
            "Purpose: The variants to adjust. Send one item for a single variant or several for a multi-variant "
            "product; all items are applied together or none are. Location: request body. "
            "Format/Type: array of objects, 1 to 100 items. Required."
        ),
        error_messages={
            "required": M.VARIANTS_REQUIRED.message,
            "null": M.VARIANTS_REQUIRED.message,
            "empty": M.VARIANTS_REQUIRED.message,
            "not_a_list": M.VARIANTS_REQUIRED.message,
            "max_length": M.TOO_MANY_VARIANTS.message,
        },
    )

    def validate(self, attrs):
        default_reason = attrs.get("reason")  # optional: None stores no reason
        default_note = (attrs.get("note") or "").strip()

        seen, duplicates = set(), set()
        for item in attrs["variants"]:
            variant_id = item["variant_id"]
            if variant_id in seen:
                duplicates.add(variant_id)
            seen.add(variant_id)

            # Resolve each item's effective reason/note once, here, so the
            # service never has to know about top-level defaults.
            item["reason"] = item.get("reason") or default_reason
            item["note"] = (item.get("note") or "").strip() or default_note

        if duplicates:
            raise serializers.ValidationError({
                "variants": [M.DUPLICATE_VARIANTS.message],
                "duplicate_variant_ids": sorted(duplicates),
            })
        if any(item["reason"] == Reason.OTHER and not item["note"] for item in attrs["variants"]):
            raise serializers.ValidationError({"note": [M.NOTE_REQUIRED_FOR_OTHER.message]})
        return attrs


class StockAdjustmentHistoryQuerySerializer(serializers.Serializer):
    variant_id = serializers.IntegerField(required=False, min_value=1, max_value=MAX_STOCK_VALUE)
    action = serializers.ChoiceField(choices=Action.choices, required=False)
    reason = serializers.ChoiceField(choices=Reason.choices, required=False)
    batch_id = serializers.UUIDField(required=False)


class UnavailableStockQuerySerializer(serializers.Serializer):
    shop_service_id = serializers.IntegerField(
        required=False, min_value=1, max_value=MAX_STOCK_VALUE,
        error_messages={
            key: M.INVALID_SERVICE_ID.message
            for key in ("invalid", "min_value", "max_value", "max_string_length", "null")
        },
    )
    # Same as the product list: no validation errors, blank search = no search.
    search = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    search_by = serializers.CharField(required=False, allow_blank=True, default="name")


class StockAdjustmentHistorySerializer(serializers.ModelSerializer):
    variant = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariantStockAdjustment
        fields = [
            "id", "batch_id", "action", "reason", "note", "variant",
            "quantity", "fabric_length",
            "previous_stock", "updated_stock",
            "previous_unavailable_stock", "updated_unavailable_stock",
            "previous_fabric_length", "updated_fabric_length",
            "previous_unavailable_fabric_length", "updated_unavailable_fabric_length",
            "created_by", "created_at",
        ]
        read_only_fields = fields

    def get_variant(self, obj):
        return {"id": obj.variant_id, "attribute": obj.variant.attribute}

    def get_created_by(self, obj):
        user = obj.created_by
        if user is None:
            return None
        return {"id": user.id, "full_name": user.full_name}
