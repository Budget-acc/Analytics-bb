from rest_framework import viewsets
from authentication.permissions import IsAdminUserOnly
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication


class SubscriptionPlanViewSet(viewsets.ViewSet):
    """
    Handles CRUD for SubscriptionPlan with manual validation and soft-delete awareness.
    """
    permission_classes = [IsAdminUserOnly]
    authentication_classes = [CutoffJWTAuthentication]
