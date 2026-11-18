from rest_framework import viewsets
from authentication.permissions import IsAdminUserOnly
from rest_framework_simplejwt.authentication import JWTAuthentication


class ProductBulkImportViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminUserOnly]
    authentication_classes = [JWTAuthentication]
