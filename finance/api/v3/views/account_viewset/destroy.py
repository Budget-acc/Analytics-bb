import logging

from django.db import transaction

from finance.models import Account
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, OpenApiParameter, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


class AccountDestroyMixin:

    @extend_schema(
        tags=["Finance Management V3"],
        summary="Delete account",
        description="Soft-deletes an account. Owner-only. Blocks default accounts and accounts with transaction history.",
        parameters=[
            OpenApiParameter(name="id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Account id."),
        ],
        responses={
            200: OpenApiResponse(description="Exact message: Account deleted successfully."),
            400: OpenApiResponse(
                description=(
                    "Business rule errors. Exact messages:\n"
                    "- Default accounts cannot be deleted.\n"
                    "- This account has transaction records and cannot be deleted."
                )
            ),
            403: OpenApiResponse(
                description=(
                    "Permission errors. Exact messages:\n"
                    "- Access to this shop has been deactivated.\n"
                    "- Unable to verify shop access.\n"
                    "- Only the shop owner can manage accounts."
                )
            ),
            404: OpenApiResponse(description="Exact message: Account not found."),
            500: OpenApiResponse(description="Exact message: Unable to delete account. Please try again."),
        },
        examples=[
            OpenApiExample(
                "Request example",
                description="DELETE /finance/v3/accounts/5/",
                value={"id": 5},
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Account deleted successfully.",
                    "data": {},
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Business rule error response",
                value={
                    "status": "error",
                    "message": "Default accounts cannot be deleted.",
                    "data": None,
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Not found response",
                value={
                    "status": "not_found",
                    "message": "Account not found.",
                    "data": None,
                },
                response_only=True,
                status_codes=["404"],
            ),
            OpenApiExample(
                "Permission denied response",
                value={
                    "status": "permission_denied",
                    "message": "Only the shop owner can manage accounts.",
                    "data": None,
                },
                response_only=True,
                status_codes=["403"],
            ),
            OpenApiExample(
                "Server error response",
                value={
                    "status": "error",
                    "message": "Unable to delete account. Please try again.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    @transaction.atomic
    def destroy(self, request, pk=None):
        """
        DELETE /finance/v3/accounts/{id}/
        Soft-delete an account. OWNER only.

        Edge cases handled:
        - Non-owner → 403
        - Account not found / wrong shop → 404
        - Default account → 400 (cannot delete system accounts)
        - Account has LedgerEntry records → 400 (financial history protected)
        """
        try:
            self._require_owner(request)
        except Exception:
            raise

        try:
            account = Account.objects.select_for_update().filter(
                id=pk, shop_id=self.shop_id, deleted=None
            ).first()

            if not account:
                return self.not_found_response(
                    message="Account not found.",
                    dev_message=f"Account id={pk} not found for shop {self.shop_id}.",
                )

            # Guard: default accounts are system-owned
            if account.is_default:
                logger.warning(
                    f"AccountDestroyMixin: attempted deletion of default account "
                    f"id={pk} shop={self.shop_id} user={request.user.id}"
                )
                return self.error_response(
                    message="Default accounts cannot be deleted.",
                    dev_message=(
                        f"Attempted deletion of is_default=True account "
                        f"id={pk} for shop {self.shop_id}."
                    ),
                )

            # Guard: account with ledger history must be preserved
            if account.ledger_entries.exists():
                logger.warning(
                    f"AccountDestroyMixin: attempted deletion of account "
                    f"id={pk} with existing ledger entries, shop={self.shop_id}."
                )
                return self.error_response(
                    message="This account has transaction records and cannot be deleted.",
                    dev_message=(
                        f"Account id={pk} has linked LedgerEntry records. "
                        f"Deletion blocked for shop {self.shop_id}."
                    ),
                )

            account.delete()  # SafeDeleteModel — soft delete

            logger.info(
                f"Account soft-deleted: id={pk} shop={self.shop_id} user={request.user.id}"
            )
            return self.success_response(
                message="Account deleted successfully.",
                dev_message=f"Account id={pk} soft-deleted for shop {self.shop_id}.",
            )

        except Exception as e:
            logger.error(
                f"Error deleting account {pk} for shop {self.shop_id}: {e}", exc_info=True
            )
            return self.error_response(
                message="Unable to delete account. Please try again.",
                dev_message=f"Unhandled error in AccountDestroyMixin.destroy for account {pk}.",
            )
