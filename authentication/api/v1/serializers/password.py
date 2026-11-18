from rest_framework import serializers
from django.contrib.auth.hashers import check_password


class UpdateSecondaryPasswordSerializer(serializers.Serializer):
    old_secondary_password = serializers.CharField(write_only=True)
    new_secondary_password = serializers.CharField(write_only=True)

    def validate(self, data):
        user = self.context["request"].user

        if user.secondary_password != data["old_secondary_password"]:
            raise serializers.ValidationError("Wallet password is incorrect.")

        if check_password(data["new_secondary_password"], user.password):
            raise serializers.ValidationError("Wallet password cannot be the same as the main password.")

        if data["new_secondary_password"] == user.secondary_password:
            raise serializers.ValidationError("New wallet password cannot be the same as the current one.")

        return data

    def save(self, **kwargs):
        user = self.context["request"].user
        user.secondary_password = self.validated_data["new_secondary_password"]
        user.save()
        return user
