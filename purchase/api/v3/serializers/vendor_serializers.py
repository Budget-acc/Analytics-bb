from rest_framework import serializers
from ....models.vendor import Vendor
from phonenumber_field.serializerfields import PhoneNumberField


class VendorSerializer(serializers.ModelSerializer):
    """Serializer for creating and updating vendors"""
    
    contact_number = PhoneNumberField(region="IN", required=False, allow_blank=True)
    
    class Meta:
        model = Vendor
        fields = [
            'id',
            
            'name',
            'contact_number',
            'email',
            'location',
            'gst_number',
            'notes',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id','shop', 'created_at', 'updated_at']

    def validate(self, attrs):
        """
        Validate vendor uniqueness within shop.
        Uses explicit shop_id from context passed by the view.
        """
        # Get shop_id from serializer context (set by view's get_serializer_context)
        shop_id = self.context.get('shop_id')
        
        if not shop_id:
            raise serializers.ValidationError(
                "Serializer requires 'shop_id' in context. "
                "Ensure the view provides shop_id via get_serializer_context()."
            )

        name = attrs.get("name")

        queryset = Vendor.objects.filter(
            shop_id=shop_id,
            name__iexact=name,
            deleted__isnull=True,
        )

        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError({
                "name": "A vendor with this name already exists."
            })

        return attrs

    def validate_name(self, value):
        """Validate vendor name"""
        if not value or not value.strip():
            raise serializers.ValidationError("Vendor name cannot be empty.")
        return value.strip()


class VendorListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing vendors"""
    
    contact_number = serializers.CharField(source='contact_number.as_e164', read_only=True)
    
    class Meta:
        model = Vendor
        fields = [
            'id',
            'name',
            'contact_number',
            'email',
            'location',
            'is_active',
            'created_at',
        ]
        read_only_fields = fields
