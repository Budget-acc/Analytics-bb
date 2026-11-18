from rest_framework import viewsets
from authentication.permissions import IsAdminUserOnly
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from ERP.utils.mixins import StandardResponseMixin

class FeatureViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    Handles CRUD for Feature with manual validation and soft-delete awareness.
    """
    permission_classes = [IsAdminUserOnly]
    authentication_classes = [CutoffJWTAuthentication]
