from rest_framework import serializers
from marketplace.models.marketplace_category import MarketplaceCategory


class MarketplaceCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = MarketplaceCategory
        fields = [
            "id",
            "name",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Name cannot be empty.")
        
        # Check for existing records (case-insensitive, including soft-deleted)
        queryset = MarketplaceCategory.all_objects.filter(name__iexact=value)
        
        # Exclude self during update
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        
        # Check for active duplicates
        active_duplicate = queryset.filter(deleted__isnull=True).first()
        if active_duplicate:
            raise serializers.ValidationError("A category with this name already exists.")
        
        # Check for soft-deleted duplicates (can be restored)
        soft_deleted = queryset.filter(deleted__isnull=False).first()
        if soft_deleted and not self.instance:
            # During create, if a soft-deleted record exists, restore it instead of failing
            # (This logic should live in the view, not serializer)
            pass
        
        return value