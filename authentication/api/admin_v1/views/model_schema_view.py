"""
Admin-only, read-only endpoint exposing the same model schema produced by
`manage.py generate_model_schema` (see `ERP/management/commands/generate_model_schema.py`
for the full shape/rationale) - except computed live on every request, via
the shared `ERP.utils.model_schema.build_model_schema()` builder, so it is
always current with whatever models/fields exist at request time.

Restricted to ADMIN staff users only (`IsAdminUserOnly` = role ADMIN + `is_staff`;
VENDOR_ADMIN users are excluded),
matching the existing admin-only pattern used elsewhere in this package
(`AdminLoginView`, `AdminViewSet`, `UserViewSet`) and in
`shop/api/admin_v1/views/organization_viewset/base.py`. GET-only - there is
no write path, so this endpoint can never be used to alter model structure.
Response contains model/field/relationship metadata only - no row data, no
credentials, no secrets.
"""
from authentication.permissions import IsAdminUserOnly
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from ERP.utils.model_schema import build_model_schema


class ModelSchemaView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUserOnly]

    def get(self, request):
        return Response(build_model_schema())


__all__ = ["ModelSchemaView"]
