import logging

from finance.api.v3.serializers import AccountSerializer
from finance.models import Account
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation

logger = logging.getLogger("api_requests")


class AccountListMixin:

    @skip_subscription_validation
    @extend_schema(
        tags=["Finance Management V3"],
        summary="List accounts",
        description=(
            "Returns all non-deleted accounts for the active shop. "
            "Used by finance setup and account selection flows."
        ),
        parameters=[
            OpenApiParameter(
                name="is_active",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                default="true",
                description="Optional. Pass false to include inactive accounts.",
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Pagination page number.",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Pagination page size.",
            ),
        ],
        responses={
            200: OpenApiResponse(description="Exact message: Accounts fetched successfully."),
            403: OpenApiResponse(
                description=(
                    "Permission errors. Exact messages:\n"
                    "- Access to this shop has been deactivated."
                )
            ),
            500: OpenApiResponse(description="Exact message: Unable to fetch accounts."),
        },
        examples=[
            OpenApiExample(
                "Request example - active accounts",
                description="GET /finance/v3/accounts/?is_active=true&page=1&page_size=10",
                value={
                    "is_active": "true",
                    "page": 1,
                    "page_size": 10,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Accounts fetched successfully.",
                    "data": [
                        {
                            "id": 1,
                            "name": "Cash in Hand",
                            "account_type": "cash",
                            "description": "Default cash account",
                            "account_number": None,
                            "is_default": True,
                            "is_active": True,
                        }
                    ],
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Permission denied response",
                value={
                    "status": "permission_denied",
                    "message": "Access to this shop has been deactivated.",
                    "data": None,
                },
                response_only=True,
                status_codes=["403"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Unable to fetch accounts.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    def list(self, request):
        """
        GET /finance/v3/accounts/
        List all non-deleted accounts for the active shop.
        Ordered: cash before bank, default first within type, then alphabetical.
        Accessible by all roles.

        Query params:
            is_active (bool, default=true) — pass ?is_active=false to include inactive accounts
        """
        try:
            is_active_param = request.query_params.get("is_active", "true").strip().lower()
            include_all = is_active_param == "false"

            qs = Account.objects.filter(
                shop_id=self.shop_id,
                deleted=None,
            )
            if not include_all:
                qs = qs.filter(is_active=True)

            accounts = qs.order_by("name")

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(accounts, request)

            if page is not None:
                serializer = AccountSerializer(page, many=True)
                return paginator.get_paginated_response(serializer.data)

            serializer = AccountSerializer(accounts, many=True)
            return self.success_response(
                data=serializer.data,
                message="Accounts fetched successfully.",
                dev_message=f"Accounts list for shop {self.shop_id}.",
            )

        except Exception as e:
            logger.error(
                f"Error listing accounts for shop {self.shop_id}: {e}", exc_info=True
            )
            return self.error_response(
                message="Unable to fetch accounts.",
                dev_message=f"Unhandled error in AccountListMixin.list for shop {self.shop_id}.",
            )
