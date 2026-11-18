from rest_framework import viewsets
from rest_framework_simplejwt.authentication import JWTAuthentication
from authentication.permissions import IsAdminUserOnly
from ERP.utils.mixins import StandardResponseMixin
from drf_spectacular.utils import extend_schema


@extend_schema(tags=["Authentication Admin v1"])
class AdminViewSet(viewsets.ViewSet, StandardResponseMixin):
    permission_classes = [IsAdminUserOnly]
    authentication_classes = [JWTAuthentication]
