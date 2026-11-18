from django.db import transaction, IntegrityError, DatabaseError, OperationalError
from shop.models import Staff
from shop.api.v3.serializers import StaffSerializer
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logs = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Management V3"],
    summary="Update staff",
    description="Partially updates staff with validation and color uniqueness checks within active shop.",
    parameters=[OpenApiParameter("id", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Staff id.")],
    request=inline_serializer(
        name="ShopV3StaffPatchRequest",
        fields={
            "name": serializers.CharField(required=False),
            "phone": serializers.CharField(required=False),
            "color": serializers.CharField(required=False, allow_blank=True),
            "salary": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Staff member updated successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Color already in use.\n"
                "- Duplicate phone number for this shop.\n"
                "- Invalid staff name.\n"
                "- Invalid staff data provided.\n"
                "- This color is already assigned to another staff in this shop.\n"
                "- This phone number is already registered for this shop.\n"
                "- StaffSerializer field-level validation errors."
            )
        ),
        404: OpenApiResponse(description="Exact message: Staff not found."),
        500: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- An error occurred while updating staff. Please try again later.\n"
                "- An unexpected error occurred while updating staff. Please contact support."
            )
        ),
    },
    examples=[
        OpenApiExample("Request example", value={"name": "Raju Kumar", "color": "#4CAF50"}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Staff member updated successfully.", "data": {"id": 10, "name": "Raju Kumar"}}, response_only=True, status_codes=["200"]),
    ],
)
def partial_update(self, request, pk=None):
    """Partially update a staff member"""
    try:
        with transaction.atomic():
            try:
                staff = Staff.objects.select_for_update().get(pk=pk, shop_id=self.shop_id)
            except Staff.DoesNotExist:
                return self.not_found_response(
                    resource="Staff",
                    message="Staff not found.",
                    dev_message=f"Staff {pk} not found in shop {self.shop_id}"
                )

            serializer = StaffSerializer(
                staff,
                data=request.data,
                partial=True,
                context={"shop_id": self.shop_id}
            )

            if not serializer.is_valid():
                # Separate phone validation errors
                phone_errors = serializer.errors.get("phone")
                if phone_errors:
                    error_msg = str(phone_errors[0])
                    return self.validation_error_response(
                        errors={"phone": phone_errors},
                        # message="This phone number is already registered for this shop.",
                        message=error_msg,
                        dev_message=f"Phone validation failed for staff {pk}: {error_msg}"
                    )

                # Separate name validation errors
                name_errors = serializer.errors.get("name")
                if name_errors:
                    return self.validation_error_response(
                        errors={"name": name_errors},
                        message="Invalid staff name.",
                        dev_message=f"Invalid name attempted for staff {pk}"
                    )

                # Generic validation errors
                return self.validation_error_response(
                    errors=serializer.errors,
                    message="Invalid staff data provided.",
                    dev_message=f"Validation errors while updating staff {pk}"
                )

            # handle color uniqueness
            if "color" in request.data:
                color_raw = request.data.get("color", None)
                if color_raw not in (None, ""):
                    conflict_qs = Staff.objects.select_for_update().filter(
                        shop_id=self.shop_id,
                        color=color_raw,
                        is_deleted=False
                    ).exclude(pk=staff.pk)
                    if conflict_qs.exists():
                        return self.validation_error_response(
                            errors={"color": ["This color is already assigned to another staff in this shop."]},
                            message="Color already in use."
                        )
                    serializer.validated_data["color"] = color_raw
                else:
                    serializer.validated_data["color"] = None

            updated_staff = serializer.save()

        return self.success_response(
            data=StaffSerializer(updated_staff).data,
            message="Staff member updated successfully.",
            dev_message=f"Updated staff {pk} in shop {self.shop_id}"
        )

    except IntegrityError as e:
        error_message = str(e).lower()
        if "unique" in error_message or "phone" in error_message:
            return self.validation_error_response(
                errors={"phone": ["This phone number is already registered for this shop."]},
                message="Duplicate phone number for this shop.",
                dev_message=f"IntegrityError: Duplicate phone attempted for staff {pk}"
            )
        return self.error_response(
            message="Error occurred while updating staff. Please try again.",
            dev_message=str(e)
        )

    except (DatabaseError, OperationalError) as e:
        return self.error_response(
            message="An error occurred while updating staff. Please try again later.",
            dev_message=str(e)
        )

    except Exception as e:
        logs.error(f"Unexpected error updating staff {pk} for shop {self.shop_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="An unexpected error occurred while updating staff. Please contact support.",
            dev_message=str(e)
        )
