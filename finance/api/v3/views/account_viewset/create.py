import logging

from django.db import IntegrityError, transaction

from finance.api.v3.serializers import AccountSerializer
from finance.api.v3.views.account_viewset.base import VALID_ACCOUNT_TYPES
from finance.models import Account
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

logger = logging.getLogger("api_requests")


class AccountCreateMixin:

    @extend_schema(
        tags=["Finance Management V3"],
        summary="Create account",
        description=(
            "Creates a new shop account. Owner-only endpoint used in account setup and finance configuration flows."
        ),
        request=inline_serializer(
            name="FinanceAccountCreateV3Request",
            fields={
                "name": serializers.CharField(required=True, help_text="Account name. Max length 255."),
                "account_type": serializers.ChoiceField(required=True, choices=["cash", "bank"], help_text="Account type."),
                "description": serializers.CharField(required=False, allow_blank=True, help_text="Optional description."),
                "account_number": serializers.CharField(required=False, allow_blank=True, help_text="Optional account number. Max length 50."),
            },
        ),
        responses={
            200: OpenApiResponse(description="Exact message: Account created successfully."),
            400: OpenApiResponse(
                description=(
                    "Validation errors. Exact messages:\n"
                    "- Account name is required.\n"
                    "- Account name must not exceed 255 characters.\n"
                    "- Account type is required.\n"
                    "- Invalid account type.\n"
                    "- Account number must not exceed 50 characters.\n"
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
            500: OpenApiResponse(description="Exact message: Unable to create account. Please try again."),
        },
        examples=[
            OpenApiExample(
                "Create bank account",
                value={
                    "name": "HDFC Current Account",
                    "account_type": "bank",
                    "description": "Primary settlement account",
                    "account_number": "1234567890",
                },
                request_only=True,
            ),
            OpenApiExample(
                "Success response",
                value={
                    "status": "success",
                    "message": "Account created successfully.",
                    "data": {},
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Invalid input response",
                value={
                    "status": "validation_error",
                    "message": "Invalid account type.",
                    "data": {
                        "validation_errors": "Invalid account type."
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
            OpenApiExample(
                "Duplicate name response",
                value={
                    "status": "error",
                    "message": "An account with this name already exists.",
                    "data": None,
                },
                response_only=True,
                status_codes=["400"],
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
                    "message": "Unable to create account. Please try again.",
                    "data": None,
                },
                response_only=True,
                status_codes=["500"],
            ),
        ],
    )
    @transaction.atomic
    def create(self, request):
        """
        POST /finance/v3/accounts/
        Create a new shop account. OWNER only.

        Payload: { "name": str, "account_type": "cash" | "bank" }

        Edge cases handled:
        - Non-owner → 403
        - Missing / blank name → 400
        - name > 255 chars → 400
        - Invalid account_type → 400
        - Duplicate name (race-safe via DB unique constraint) → 400
        """
        try:
            self._require_owner(request)  # raises PermissionDenied if not OWNER
        except Exception:
            raise  # let DRF handle the PermissionDenied

        try:
            data = request.data
            name = str(data.get("name", "")).strip()
            account_type = str(data.get("account_type", "bank")).strip().lower()

            # --- Validation ---
            errors = {}

            if not name:
                errors = "Account name is required."
            elif len(name) > 255:
                errors = "Account name must not exceed 255 characters."

            if not account_type:
                errors = "Account type is required."
            elif account_type not in VALID_ACCOUNT_TYPES:
                errors = "Invalid account type."

            # Optional fields
            description = str(data.get("description", "") or "").strip() or None
            account_number = str(data.get("account_number", "") or "").strip() or None

            if account_number and len(account_number) > 50:
                errors = "Account number must not exceed 50 characters."

            if errors:
                return self.validation_error_response(
                    errors=errors,
                    message= errors,
                    dev_message=f"Validation failed on account create for shop {self.shop_id}: {errors}",
                )

            # --- Create (race-condition-safe via DB unique constraint) ---
            account = Account.objects.create(
                shop_id=self.shop_id,
                name=name,
                account_type=account_type,
                description=description,
                account_number=account_number,
                is_default=False,
                is_active=True,
            )

            logger.info(
                f"Account created: id={account.id} name='{name}' "
                f"type={account_type} shop={self.shop_id} user={request.user.id}"
            )
            return self.success_response(
                message="Account created successfully.",
                dev_message=f"Account '{name}' ({account_type}) created for shop {self.shop_id}.",
            )

        except IntegrityError:
            # unique_account_name_per_shop constraint triggered
            logger.error(
                f"Duplicate account name '{data.get('name')}' for shop {self.shop_id}."
            )
            return self.error_response(
                message="An account with this name already exists.",
                dev_message=(
                    f"IntegrityError: unique_account_name_per_shop constraint "
                    f"on (shop={self.shop_id}, name='{data.get('name')}')."
                ),
            )
        except Exception as e:
            logger.error(
                f"Error creating account for shop {self.shop_id}: {e}", exc_info=True
            )
            return self.error_response(
                message="Unable to create account. Please try again.",
                dev_message=f"Unhandled error in AccountCreateMixin.create for shop {self.shop_id}.",
            )
