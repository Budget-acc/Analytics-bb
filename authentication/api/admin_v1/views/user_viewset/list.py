from rest_framework import status
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django.db.models import Q
from authentication.models import User
from authentication.api.admin_v1.serializers import UserSerializer
from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from rest_framework import serializers


@extend_schema(
    tags=["Authentication Admin v1"],
    summary="List admin-managed users",
    description=(
        "Returns a paginated list of user accounts visible in the admin panel, filtered by is_staff.\n\n"
        "Purpose: Powers the admin user management screen, allowing admins to browse and search all user accounts.\n\n"
        "Context: Results are ordered by most recently created first (descending ID). "
        "Superuser accounts are not excluded from this list - a superuser account also has is_staff=True, "
        "so it appears when is_staff=true is requested (superusers conventionally are also staff). "
        "Use the search parameter to filter by name, email, or phone. "
        "By default only regular (non-admin) users are returned; pass is_staff=true to list admin/staff "
        "accounts instead (this includes approved VENDOR_ADMIN accounts; use the role field to tell them apart). "
        "Authentication: JWT Bearer token required."
    ),
    parameters=[
        OpenApiParameter(
            name="search",
            type=str,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Filters the user list to accounts matching the search term. "
                "Location: Query parameter. "
                "Format: string, case-insensitive partial match — applied across full_name, last_name, email, and phone fields. "
                "Optional. Omit to return all users."
            ),
        ),
        OpenApiParameter(
            name="is_staff",
            type=bool,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Selects whether to list regular users or admin/staff users. "
                "Location: Query parameter. "
                "Format: boolean — pass 'true' to return accounts with is_staff=True (admin users). "
                "Any other value or omitting the parameter returns accounts with is_staff=False (regular users). "
                "Superuser accounts are included whenever they match the requested is_staff value "
                "(superusers conventionally have is_staff=True, so they appear under is_staff=true). "
                "Optional. Default: false (regular users)."
            ),
        ),
        OpenApiParameter(
            name="page",
            type=int,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "Purpose: Selects which page of results to return. "
                "Location: Query parameter. "
                "Format: integer, minimum 1. Default: 1. "
                "Optional."
            ),
        ),
    ],
    responses={
        200: inline_serializer(
            name="AdminUserListResponse",
            fields={
                "count": serializers.IntegerField(help_text="Total number of users matching the query (across all pages)."),
                "next": serializers.CharField(allow_null=True, help_text="URL to the next page of results. Null if on the last page."),
                "previous": serializers.CharField(allow_null=True, help_text="URL to the previous page of results. Null if on the first page."),
                "results": serializers.ListSerializer(
                    child=inline_serializer(
                        name="AdminUserListItem",
                        fields={
                            "id": serializers.IntegerField(help_text="Unique user ID."),
                            "phone": serializers.CharField(help_text="Phone number in E.164 format, e.g. +919876543210."),
                            "full_name": serializers.CharField(help_text="User's primary display name."),
                            "last_name": serializers.CharField(allow_null=True, help_text="User's last name. Null if not set."),
                            "email": serializers.EmailField(allow_null=True, help_text="Email address. Null if not set."),
                            "role": serializers.CharField(help_text="Role assigned to the user, e.g. OWNER or STAFF."),
                            "is_active": serializers.BooleanField(help_text="Whether the account is enabled for login."),
                            "is_staff": serializers.BooleanField(help_text="Whether the account is an admin/staff account."),
                        },
                    )
                ),
            },
        ),
        400: inline_serializer(
            name="AdminUserListError",
            fields={"error": serializers.CharField(help_text="Unexpected server-side error message.")},
        ),
    },
)
def list(self, request):
    try:
        # Get search parameter
        search = request.query_params.get("search", "").strip()

        # is_staff=true lists admin/staff users; anything else (including omitted) lists regular users
        is_staff = request.query_params.get("is_staff", "").strip().lower() == "true"

        # Base queryset - filtered by is_staff only. Superuser accounts are
        # NOT excluded: a superuser conventionally also has is_staff=True,
        # so it will appear here under is_staff=true (see is_staff param docs).
        users = User.objects.filter(is_staff=is_staff)

        # Apply search filtering if search parameter is provided
        if search:
            users = users.filter(
                Q(full_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(email__icontains=search) |
                Q(phone__icontains=search)
            )

        # Order by creation date (newest first)
        users = users.order_by("-id")
        paginator = PageNumberPagination()
        paginated_users = paginator.paginate_queryset(users, request)
        serializer = UserSerializer(paginated_users, many=True)
        return paginator.get_paginated_response(serializer.data)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
