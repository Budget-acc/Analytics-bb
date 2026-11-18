from rest_framework import viewsets
from authentication.permissions import IsAdminUserOnly
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.parsers import JSONParser
from shop.models import BugReport
from ERP.utils.mixins import StandardResponseMixin


class BugReportAdminViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    Admin endpoints for BugReport:
      - GET /admin/bugreports/         -> list (with filters & pagination)
      - GET /admin/bugreports/{pk}/    -> retrieve
      - PATCH /admin/bugreports/{pk}/  -> partial update (only status)
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUserOnly]
    parser_classes = [JSONParser]

    def get_queryset(self):

        return BugReport.objects.select_related("shop", "reporter").all()
