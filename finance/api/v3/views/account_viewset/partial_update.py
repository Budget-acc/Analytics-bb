import logging

from django.db import IntegrityError, transaction

from finance.api.v3.serializers import AccountSerializer
from finance.api.v3.views.account_viewset.base import VALID_ACCOUNT_TYPES
from finance.models import Account
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, OpenApiParameter, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers

logger = logging.getLogger("api_requests")


class AccountPartialUpdateMixin:

    @extend_schema(
        tags=["Finance Management V3"],
        summary="Update account",
        description=(
            "Partially updates account fields. Owner-only. Supports name, account_type, description, account_number, is_default, is_active."
        ),
        parameters=[
            OpenApiParameter(name="id", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Account id."),
        ],
        request=inline_serializer(
            name="FinanceAccountPartialUpdateV3Request",
            fields={
                "name": serializers.CharField(required=False),
                "account_type": serializers.ChoiceField(required=False, choices=["cash", "bank"]),
                "description": serializers.CharField(required=False, allow_blank=True),
                "account_number": serializers.CharField(required=False, allow_blank=True),
                "is_default": serializers.BooleanField(required=False),
                "is_active": serializers.BooleanField(required=False),
            },
        ),
        responses={
            200: OpenApiResponse(
                description=(
                    "Success messages:\n"
                    "- Account updated successfully.\n"
                    "- No changes were made."
                )
            ),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages:\n"
                    "- Account name is required.\n"
                    "- Account name must not exceed 255 characters.\n"
                    "- Invalid account type.\n"
                    "- Account number must not exceed 50 characters.\n"
                    "- is_default must be a boolean.\n"
                    "- is_active must be a boolean.\n"
                    "- At least one active account is required.\n"
                    "- An account with this name already exists."
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
            500: OpenApiResponse(description="Exact message: Unable to update account. Please try again."),
        },
        examples=[
            OpenApiExample(
                "Request example - update account",
                description="PATCH /finance/v3/accounts/5/",
                value={
                    "name": "Main Cash Counter",
                    "description": "Updated description",
                    "is_active": True,
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Account updated successfully.",
                    "data": {},
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "No change response",
                value={
                    "status": "success",
                    "message": "No changes were made.",
                    "data": {},
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Validation error response",
                value={
                    "status": "validation_error",
                    "message": "At least one active account is required.",
                    "data": {
                        "validation_errors": "At least one active account is required."
                    },
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
                    "message": "Unable to update account. Please try again.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    @transaction.atomic
    def partial_update(self, request, pk=None):
        """
        PATCH /finance/v3/accounts/{id}/
        Update account fields. OWNER only.

        Allowed fields: name, account_type, is_active
        Blocked on default accounts: name, account_type (locked by system)

        Edge cases handled:
        - Non-owner → 403
        - Account not found / wrong shop → 404
        - Editing name/account_type of a default account → 400
        - Duplicate name → 400
        - Deactivating the only active Cash in Hand → 400
        - Invalid account_type value → 400
        """
        try:
            self._require_owner(request)
        except Exception:
            raise

        try:
            # Lock ALL accounts for this shop to serialize concurrent modifications.
            # Prevents race conditions on is_default transfer and last-active guard.
            list(Account.objects.filter(shop_id=self.shop_id, deleted=None).select_for_update())
            account = Account.objects.filter(id=pk, shop_id=self.shop_id, deleted=None).first()

            if not account:
                return self.not_found_response(
                    message="Account not found.",
                    dev_message=f"Account id={pk} not found for shop {self.shop_id}.",
                )

            data = request.data
            errors = None
            changed_fields = []

            # --- name ---
            if "name" in data:
                name = str(data["name"]).strip()
                if not name:
                    errors = "Account name is required."
                elif len(name) > 255:
                    errors = "Account name must not exceed 255 characters."
                else:
                    account.name = name
                    changed_fields.append("name")

            # --- account_type ---
            if "account_type" in data:
                account_type = str(data["account_type"]).strip().lower()
                if account_type not in VALID_ACCOUNT_TYPES:
                    errors = "Invalid account type."
                else:
                    account.account_type = account_type
                    changed_fields.append("account_type")

            # --- description (allowed on all accounts, including defaults) ---
            if "description" in data:
                account.description = str(data["description"]).strip() or None
                changed_fields.append("description")

            # --- account_number (allowed on all accounts) ---
            if "account_number" in data:
                account_number = str(data["account_number"] or "").strip() or None
                if account_number and len(account_number) > 50:
                    errors = "Account number must not exceed 50 characters."
                else:
                    account.account_number = account_number
                    changed_fields.append("account_number")

            # --- is_default ---
            # 0 or 1 default account per shop is allowed at any time.
            # Setting True clears the flag on all other accounts atomically (transfer).
            # Setting False simply unsets this account's default (0 defaults is valid).
            if "is_default" in data:
                new_default = data["is_default"]
                if not isinstance(new_default, bool):
                    errors = "is_default must be a boolean."
                else:
                    if new_default:
                        # Transfer default: clear flag on every other account atomically
                        Account.objects.filter(
                            shop_id=self.shop_id, deleted=None
                        ).exclude(id=pk).update(is_default=False)
                    account.is_default = new_default
                    changed_fields.append("is_default")

            # --- is_active ---
            if "is_active" in data:
                new_active = data["is_active"]
                if not isinstance(new_active, bool):
                    errors = "is_active must be a boolean."
                else:
                    # Guard: at least one active account must always exist per shop.
                    # Only checked when actually transitioning from active → inactive.
                    if not new_active and account.is_active:
                        active_count = Account.objects.filter(
                            shop_id=self.shop_id,
                            is_active=True,
                            deleted=None,
                        ).count()
                        if active_count <= 1:
                            return self.validation_error_response(
                                message="At least one active account is required.",
                                dev_message=(
                                    f"Blocked deactivation of last active account "
                                    f"id={pk} for shop {self.shop_id}."
                                ),
                            )
                    account.is_active = new_active
                    changed_fields.append("is_active")

            if errors:
                return self.validation_error_response(
                    message=errors,
                    dev_message=f"Validation failed on account update id={pk} for shop {self.shop_id}.",
                )

            if not changed_fields:
                return self.success_response(
                    message="No changes were made.",
                    dev_message=f"PATCH account id={pk}: no updatable fields provided.",
                )

            account.save(update_fields=changed_fields)

            logger.info(
                f"Account updated: id={pk} fields={changed_fields} "
                f"shop={self.shop_id} user={request.user.id}"
            )
            return self.success_response(
                message="Account updated successfully.",
                dev_message=(
                    f"Account id={pk} updated fields={changed_fields} "
                    f"for shop {self.shop_id}."
                ),
            )

        except IntegrityError:
            logger.warning(
                f"Duplicate account name on update, account id={pk}, shop={self.shop_id}."
            )
            return self.error_response(
                message="An account with this name already exists.",
                dev_message=(
                    f"IntegrityError: unique_account_name_per_shop on update "
                    f"for account id={pk}, shop={self.shop_id}."
                ),
            )
        except Exception as e:
            logger.error(
                f"Error updating account {pk} for shop {self.shop_id}: {e}", exc_info=True
            )
            return self.error_response(
                message="Unable to update account. Please try again.",
                dev_message=f"Unhandled error in AccountPartialUpdateMixin.partial_update for account {pk}.",
            )
