from rest_framework.serializers import ModelSerializer
from authentication.models import User


class UserProfileSerializer(ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "phone",
            "full_name",
            "last_name",
            "email",
            "role",
        ]
