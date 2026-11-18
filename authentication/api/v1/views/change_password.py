from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from authentication.models import User
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiExample
from rest_framework import serializers


class ChangePasswordAPIView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    @extend_schema(
        tags=["User Management V1"],
        summary="Change password",
        description=(
            "Changes the authenticated user's main account password.\n\n"
            "Purpose: Allows users to rotate their own login password securely.\n\n"
            "Context: Old password verification is mandatory. Optionally, user can request logout from all devices "
            "by blacklisting all outstanding refresh tokens."
        ),
        request=inline_serializer(
            name="ChangePasswordRequestV1",
            fields={
                "old_password": serializers.CharField(
                    help_text=(
                        "Purpose: Verifies user identity before password change. "
                        "Location: Request body. "
                        "Format/Type: string. "
                        "Required."
                    )
                ),
                "new_password": serializers.CharField(
                    help_text=(
                        "Purpose: Sets new login password for future authentications. "
                        "Location: Request body. "
                        "Format/Type: string. "
                        "Required."
                    )
                ),
                "logout_from_all_devices": serializers.BooleanField(
                    required=False,
                    default=False,
                    help_text=(
                        "Purpose: If true, invalidates tokens on all active devices after password update. "
                        "Location: Request body. "
                        "Format/Type: boolean. "
                        "Optional. Default: false."
                    )
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="ChangePasswordSuccessV1",
                fields={
                    "message": serializers.CharField(help_text="Success message after password update.")
                },
            ),
            400: inline_serializer(
                name="ChangePasswordBadRequestV1",
                fields={
                    "error": serializers.CharField(help_text="Validation error or incorrect old password message.")
                },
            ),
            404: inline_serializer(
                name="ChangePasswordNotFoundV1",
                fields={
                    "error": serializers.CharField(help_text="User lookup failure message.")
                },
            ),
            500: inline_serializer(
                name="ChangePasswordServerErrorV1",
                fields={
                    "error": serializers.CharField(help_text="Token blacklist failure message when logout-all is requested.")
                },
            ),
        },
        examples=[
            OpenApiExample(
                "Change password request",
                value={
                    "old_password": "OldPass@123",
                    "new_password": "NewPass@456",
                    "logout_from_all_devices": True,
                },
                request_only=True,
            )
        ],
    )
    def post(self, request):
        try:
            user = User.objects.get(id=request.user.id)
        except User.DoesNotExist:
            return Response(
                {"error": "User not found."}, status=status.HTTP_404_NOT_FOUND
            )

        old_password = request.data.get("old_password")
        new_password = request.data.get("new_password")
        logout_all = request.data.get("logout_from_all_devices", False)

        if not old_password or not new_password:
            return Response(
                {"error": "Both old_password and new_password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not user.check_password(old_password):
            return Response(
                {"error": "Old password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save()

        if logout_all:
            try:
                tokens = OutstandingToken.objects.filter(user=user)
                for token in tokens:
                    BlacklistedToken.objects.get_or_create(token=token)
            except Exception as e:
                return Response(
                    {
                        "error": f"Password changed, but failed to logout from all devices: {str(e)}"
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        return Response(
            {"message": "Password updated successfully."},
            status=status.HTTP_200_OK,
        )
