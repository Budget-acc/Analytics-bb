from rest_framework import viewsets
from authentication.permissions import IsAdminUserOnly
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from ERP.utils.mixins import StandardResponseMixin


class OrganizationViewSet(viewsets.ViewSet, StandardResponseMixin):
    """
    Handles CRUD for Organization (business group / multi-branch entity).

    Authentication : JWT (admin only)
    Permissions    : IsAdminUserOnly
    Soft delete    : Not applicable — Organization uses standard Django hard delete.
                     Deleting an organization nullifies the organization FK on all
                     linked shops automatically (SET_NULL on_delete).
    """
    permission_classes = [IsAdminUserOnly]
    authentication_classes = [JWTAuthentication]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
