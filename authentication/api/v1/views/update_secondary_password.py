from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from authentication.api.v1.serializers import UpdateSecondaryPasswordSerializer
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiExample
from rest_framework import serializers


class UpdateSecondaryPasswordView(APIView):
    permission_classes = [IsAuthenticated]
    authentication_classes = [JWTAuthentication]

    @extend_schema(
        tags=["User Management V1"],
        summary="Update secondary password",
        description=(
            "Updates the authenticated user's wallet/secondary password.\n\n"
            "Purpose: Lets users rotate wallet credential used for wallet-level authentication flows.\n\n"
            "Context: Validates old secondary password, prevents reuse of current wallet password, "
            "and disallows wallet password equal to main login password."
        ),
        request=inline_serializer(
            name="UpdateSecondaryPasswordRequestV1",
            fields={
                "old_secondary_password": serializers.CharField(
                    help_text=(
                        "Purpose: Confirms the existing wallet password before update. "
                        "Location: Request body. "
                        "Format/Type: string. "
                        "Required."
                    )
                ),
                "new_secondary_password": serializers.CharField(
                    help_text=(
                        "Purpose: Sets the new wallet password used by wallet-login checks. "
                        "Location: Request body. "
                        "Format/Type: string. "
                        "Required."
                    )
                ),
            },
        ),
        responses={
            200: inline_serializer(
                name="UpdateSecondaryPasswordSuccessV1",
                fields={
                    "message": serializers.CharField(help_text="Success message after secondary password update.")
                },
            ),
            400: inline_serializer(
                name="UpdateSecondaryPasswordBadRequestV1",
                fields={
                    "error": serializers.CharField(help_text="Validation failure message from serializer rules.")
                },
            ),
        },
        examples=[
            OpenApiExample(
                "Update secondary password request",
                value={
                    "old_secondary_password": "1234",
                    "new_secondary_password": "5678",
                },
                request_only=True,
            )
        ],
    )
    def post(self, request):
        serializer = UpdateSecondaryPasswordSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            serializer.save()
            return Response({"message": "Secondary password updated successfully."}, status=status.HTTP_200_OK)

        # Customize error response key
        errors = serializer.errors
        custom_errors = {"error": list(errors.values())[0][0]} if errors else {"error": "Invalid input."}
        return Response(custom_errors, status=status.HTTP_400_BAD_REQUEST)
