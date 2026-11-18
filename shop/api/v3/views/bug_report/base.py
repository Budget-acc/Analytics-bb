from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.exceptions import PermissionDenied
from ERP.middleware.enhanced_jwt_auth import CutoffJWTAuthentication
from shop.api.v3.utils import get_active_shop_id
from ERP.utils.mixins import StandardResponseMixin
import logging

logger = logging.getLogger(__name__)


class BugReportViewSet(StandardResponseMixin, viewsets.ViewSet):
    """
    Create-only viewset for BugReport (can be extended later with list/retrieve/update).
    Expects multipart/form-data when uploading images. Field names:
      - title (required)
      - description (required)
      - more_details (optional JSON string or omit)
      - images[] (optional multiple files)
    """
    authentication_classes = [CutoffJWTAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            self.shop_id = get_active_shop_id(request)
        except Exception as e:
            logger.error(f"Shop permission denied in BugReportViewSet: {str(e)}", exc_info=True)
            raise PermissionDenied({
                "status": "permission_denied",
                "message": "Access to this shop has been deactivated.",
                "data": None
            })
