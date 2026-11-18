from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from authentication.models import User
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiExample
from rest_framework import serializers


# Wallet Login
class WalletAuthenticationView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    @extend_schema(
        tags=["User Management V1"],
        summary="Wallet login",
        description=(
            "Authenticates the wallet action using the user's secondary password.\n\n"
            "Purpose: Adds a second factor-style credential check before sensitive wallet operations.\n\n"
            "Context: User must already be authenticated via JWT; this endpoint validates secondary_password only."
        ),
        request=inline_serializer(
            name="WalletLoginRequestV1",
            fields={
                "password": serializers.CharField(
                    help_text=(
                        "Purpose: Verifies wallet-specific credential before wallet access. "
                        "Location: Request body. "
                        "Format/Type: string. "
                        "Required."
                    )
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="WalletLoginSuccessV1",
                fields={
                    "message": serializers.CharField(help_text="Success message. Value: success.")
                },
            ),
            400: inline_serializer(
                name="WalletLoginBadRequestV1",
                fields={
                    "error": serializers.CharField(help_text="Missing password or invalid credentials message.")
                },
            ),
        },
        examples=[
            OpenApiExample(
                "Wallet login request",
                value={"password": "1234"},
                request_only=True,
            )
        ],
    )
    def post(self, request):
        try:
            user = User.objects.get(id=request.user.id)
        except User.DoesNotExist:
            return Response(
                {"error": "Invalid credentials."}, status=status.HTTP_400_BAD_REQUEST
            )
        password = request.data.get("password")
        if not password:
            return Response(
                {"error": "password is required."}, status=status.HTTP_400_BAD_REQUEST
            )
        if user.secondary_password != password:
            return Response(
                {"error": "Invalid credentials."}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response({"message": "success"}, status=status.HTTP_200_OK)
