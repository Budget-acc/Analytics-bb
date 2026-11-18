from rest_framework import viewsets
from authentication.permissions import IsAdminUserOnly
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from decimal import Decimal, InvalidOperation
import boto3
import logging
from django.conf import settings

logger = logging.getLogger("api_requests")


class ShopUserViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminUserOnly]
    authentication_classes = [JWTAuthentication]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _parse_positive_int(self, value, field_name):
        """Helper to validate that value is a positive integer"""
        if value is None or value == "":
            return 0
        try:
            ivalue = int(value)
        except (ValueError, TypeError):
            raise ValueError(f"{field_name} must be an integer.")
        if ivalue < 0:
            raise ValueError(f"{field_name} must be a positive integer.")
        return ivalue

    def _parse_price(self, value, field_name):
        """
        Validate price as positive decimal
        """
        if value in [None, ""]:
            return None

        try:
            price = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError(f"{field_name} must be a valid decimal value.")

        if price < 0:
            raise ValueError(f"{field_name} cannot be negative.")

        return price

    def _cleanup_s3_image(self, img_url):
        """Helper method to clean up uploaded S3 image"""
        try:
            s3 = boto3.client(
                "s3",
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=settings.AWS_S3_REGION_NAME,
            )
            bucket = settings.AWS_STORAGE_BUCKET_NAME
            key = img_url.split(f"{bucket}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/")[-1]
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception as s3_err:
            logger.warning(f"Failed to delete uploaded image from S3: {s3_err}")
