from rest_framework import viewsets
from authentication.permissions import IsAdminUserOnly
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser


class GeneralServiceViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminUserOnly]
    authentication_classes = [JWTAuthentication]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
