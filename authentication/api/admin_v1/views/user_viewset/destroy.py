from rest_framework import status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from authentication.models import User
from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from rest_framework import serializers


@extend_schema(
    tags=["Authentication Admin v1"],
    summary="Soft delete user (admin)",
    description=(
        "Soft deletes a user account by ID. Works for both regular users and admin/staff users.\n\n"
        "Purpose: Removes an account from the system when it is no longer needed — "
        "used by admins to clean up decommissioned staff, owner, or admin accounts.\n\n"
        "Context: This is a soft delete — the record is flagged as deleted but retained in the database. "
        "Exercise caution; the operation is irreversible. "
        "Authentication: JWT Bearer token required."
    ),
    parameters=[
        OpenApiParameter(
            name="pk",
            type=int,
            location=OpenApiParameter.PATH,
            required=True,
            description=(
                "Purpose: Identifies the specific user account to soft delete. "
                "Location: Path parameter (URL segment, bound as 'pk'). "
                "Format: integer, positive — must correspond to an existing user ID. "
                "Required."
            ),
        ),
    ],
    responses={
        200: inline_serializer(
            name="AdminDeleteUserSuccess",
            fields={
                "message": serializers.CharField(help_text="Confirmation message. Value: 'User soft-deleted successfully.'")
            },
        ),
        404: inline_serializer(
            name="AdminDeleteUserNotFound",
            fields={
                "detail": serializers.CharField(help_text="Value: 'Not found.' — returned when no user matches the given ID.")
            },
        ),
        500: inline_serializer(
            name="AdminDeleteUserError",
            fields={
                "error": serializers.CharField(help_text="Unexpected server-side error message.")
            },
        ),
    },
)
def destroy(self, request, pk=None):
    try:
        user = get_object_or_404(User, pk=pk)
        user.delete()
        # if user.role == "OWNER":
        #     ShopOwners.objects.filter(phone=user.phone).update(block=True)
        return Response(
            {"message": "User soft-deleted successfully."},
            status=status.HTTP_200_OK,
        )
    except Exception as e:
        return Response(
            {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
