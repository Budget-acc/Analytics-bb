from rest_framework import serializers

ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"]
ALLOWED_VIDEO_TYPES = ["video/mp4", "video/webm", "video/quicktime"]


class PresignedMediaFileSerializer(serializers.Serializer):
    file_name = serializers.CharField(max_length=255)
    content_type = serializers.CharField(max_length=100)


class PresignedMediaRequestSerializer(serializers.Serializer):
    media_type = serializers.ChoiceField(choices=["image", "video"])
    files = PresignedMediaFileSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        media_type = attrs.get("media_type")
        files = attrs.get("files", [])

        if media_type == "image":
            if len(files) > 4:
                raise serializers.ValidationError({
                    "files": "You can request a maximum of 4 image upload URLs at a time."
                })
            for index, file_data in enumerate(files):
                content_type = file_data.get("content_type", "").lower()
                if content_type not in ALLOWED_IMAGE_TYPES:
                    raise serializers.ValidationError({
                        "files": f"File {index + 1} has an unsupported image content_type: '{content_type}'."
                    })

        elif media_type == "video":
            if len(files) > 1:
                raise serializers.ValidationError({
                    "files": "You can request a maximum of 1 video upload URL at a time."
                })
            for index, file_data in enumerate(files):
                content_type = file_data.get("content_type", "").lower()
                if content_type not in ALLOWED_VIDEO_TYPES:
                    raise serializers.ValidationError({
                        "files": f"File {index + 1} has an unsupported video content_type: '{content_type}'."
                    })

        return attrs


        

class SaveProductMediaSerializer(
    serializers.Serializer
):

    media_type = serializers.ChoiceField(
        choices=[
            "image",
            "video",
        ]
    )

    url = serializers.URLField()

    key = serializers.CharField(
        max_length=1000,
    )

    thumbnail_url = serializers.URLField(
        required=False,
        allow_blank=True,
    )

    alt = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    title = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )

    provider = serializers.CharField(
        max_length=50,
        required=False,
        allow_blank=True,
    )

    sort_order = serializers.IntegerField(
        required=False,
        min_value=0,
    )