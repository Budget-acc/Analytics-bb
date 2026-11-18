from rest_framework import serializers
from marketplace.models.fabric import Fabric


class FabricSerializer(serializers.ModelSerializer):
    class Meta:
        model = Fabric
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
            raise serializers.ValidationError(
                "Fabric name cannot be empty."
            )

        queryset = Fabric.all_objects.filter(
            name__iexact=value
        )

        if self.instance:
            queryset = queryset.exclude(
                pk=self.instance.pk
            )

        active_duplicate = queryset.filter(
            deleted__isnull=True
        ).first()

        if active_duplicate:
            raise serializers.ValidationError(
                "A fabric with this name already exists."
            )

        soft_deleted = queryset.filter(
            deleted__isnull=False
        ).first()

        if soft_deleted and not self.instance:
            pass

        return value