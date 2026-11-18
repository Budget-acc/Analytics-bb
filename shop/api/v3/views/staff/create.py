from django.db import transaction, IntegrityError, DatabaseError, DataError, OperationalError
from shop.models import Staff
from shop.api.v3.serializers import StaffSerializer
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
import logging

logs = logging.getLogger("api_requests")


@extend_schema(
    tags=["Shop Management V3"],
    summary="Create staff",
    description="Creates staff in active shop with validation on phone/name/color and database consistency checks.",
    request=inline_serializer(
        name="ShopV3StaffCreateRequest",
        fields={
            "name": serializers.CharField(required=True),
            "phone": serializers.CharField(required=True),
            "color": serializers.CharField(required=False, allow_blank=True),
            "salary": serializers.DecimalField(required=False, max_digits=12, decimal_places=2),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Staff member created successfully."),
        400: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Color already in use.\n"
                "- Duplicate phone number for this shop.\n"
                "- Invalid staff name.\n"
                "- Please check the input fields and try again.\n"
                "- This color is already assigned to another staff in this shop.\n"
                "- This phone number is already registered for this shop.\n"
                "- Invalid or too large value for one of the fields.\n"
                "- Invalid data provided.\n"
                "- StaffSerializer field-level errors under errors object."
            )
        ),
        500: OpenApiResponse(
            description=(
                "Error messages:\n"
                "- Database operation failed. Please try again later.\n"
                "- A database error occurred. Please contact support.\n"
                "- An unexpected error occurred while creating staff. Please contact support."
            )
        ),
    },
    examples=[
        OpenApiExample("Request example", value={"name": "Raju", "phone": "9999999999", "color": "#2D89EF"}, request_only=True),
        OpenApiExample("Success response", value={"status": "success", "message": "Staff member created successfully.", "data": {"id": 10, "name": "Raju", "phone": "9999999999"}}, response_only=True, status_codes=["200"]),
    ],
)
def create(self, request):
    """Create a new staff member"""
    try:
        serializer = StaffSerializer(data=request.data, context={"shop_id": self.shop_id})

        if not serializer.is_valid():
            # phone validation errors
            phone_errors = serializer.errors.get("phone")
            if phone_errors:
                error_message = str(phone_errors[0])
                return self.validation_error_response(
                    errors={"phone": phone_errors},
                    message=error_message,
                    dev_message=f"Phone validation failed for: {request.data.get('phone')}"
                )

            # name validation errors
            name_errors = serializer.errors.get("name")
            if name_errors:
                return self.validation_error_response(
                    errors={"name": name_errors},
                    message="Invalid staff name.",
                    dev_message=f"Invalid name attempted: {request.data.get('name')}"
                )

            # Generic validation errors
            return self.validation_error_response(
                errors=serializer.errors,
                message="Please check the input fields and try again.",
                dev_message="Validation errors while creating staff."
            )

        color_raw = request.data.get("color", None)

        with transaction.atomic():
            if color_raw not in (None, ""):
                conflict = Staff.objects.select_for_update().filter(
                    shop_id=self.shop_id,
                    color=color_raw,
                    is_deleted=False
                ).exists()
                if conflict:
                    return self.validation_error_response(
                        errors={"color": ["This color is already assigned to another staff in this shop."]},
                        message="Color already in use."
                    )

            save_kwargs = {"shop_id": self.shop_id}
            if "color" in request.data:
                save_kwargs["color"] = color_raw or None

        # Save staff with shop_id
        staff = serializer.save(shop_id=self.shop_id)

        return self.success_response(
            data=StaffSerializer(staff).data,
            message="Staff member created successfully.",
            dev_message=f"Created staff {staff.id} for shop {self.shop_id}"
        )

    except IntegrityError as e:
        # Likely duplicate phone
        return self.validation_error_response(
            errors={"phone": ["This phone number is already registered for this shop."]},
            message="Duplicate phone number for this shop.",
            dev_message=f"IntegrityError on shop {self.shop_id} with phone {request.data.get('phone')} | {str(e)}"
        )

    except DataError as e:
        return self.validation_error_response(
            errors={"data": ["Invalid or too large value for one of the fields."]},
            message="Invalid data provided.",
            dev_message=str(e)
        )

    except OperationalError as e:
        return self.error_response(
            message="Database operation failed. Please try again later.",
            dev_message=f"OperationalError while creating staff for shop {self.shop_id}: {str(e)}"
        )

    except DatabaseError as e:
        # Generic database error
        return self.error_response(
            message="A database error occurred. Please contact support.",
            dev_message=f"DatabaseError while creating staff for shop {self.shop_id}: {str(e)}"
        )

    except Exception as e:
        logs.error(f"Unexpected error creating staff for shop {self.shop_id}: {str(e)}", exc_info=True)
        return self.error_response(
            message="An unexpected error occurred while creating staff. Please contact support.",
            dev_message=str(e)
        )
