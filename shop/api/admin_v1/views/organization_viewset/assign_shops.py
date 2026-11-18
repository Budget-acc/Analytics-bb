from django.db.models import Count
from django.db import transaction
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status

from shop.models import Organization, Shop
from shop.api.admin_v1.serializers import OrganizationSerializer


@extend_schema(
    tags=["Organization Admin Management V1"],
    summary="Assign / remove shops from organization",
    description=(
        "Bulk-assigns and/or removes shops from an organization in a single atomic operation.\n\n"
        "Purpose: Links or unlinks existing shops to/from an organization (business group). "
        "A shop can belong to only one organization at a time — adding a shop here overwrites "
        "any previous organization link it had.\n\n"
        "Authentication: JWT — admin users only.\n\n"
        "Path parameters — Required:\n"
        "- id (integer): The database ID of the organization.\n\n"
        "Body (JSON) — At least one of 'add' or 'remove' is required:\n"
        "- add (list of integers): Shop IDs to link to this organization.\n"
        "- remove (list of integers): Shop IDs to unlink from this organization. "
        "Shop IDs that are not currently linked to this org are silently ignored."
    ),
    parameters=[
        OpenApiParameter(
            name="id",
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            required=True,
            description="Database ID of the organization.",
        ),
    ],
    request=inline_serializer(
        name="OrganizationAssignShopsRequest",
        fields={
            "add": serializers.ListField(
                child=serializers.IntegerField(),
                required=False,
                help_text="Shop IDs to link to this organization.",
            ),
            "remove": serializers.ListField(
                child=serializers.IntegerField(),
                required=False,
                help_text="Shop IDs to unlink from this organization.",
            ),
        },
    ),
    responses={
        200: OpenApiResponse(description="Exact message: Shop assignments updated successfully."),
        400: OpenApiResponse(
            description=(
                "Validation error messages:\n"
                "- At least one of 'add' or 'remove' must be provided.\n"
                "- 'add' must be a list of integers.\n"
                "- 'remove' must be a list of integers.\n"
                "- The following shop IDs do not exist: {ids}."
            )
        ),
        404: OpenApiResponse(description="Exact message: Organization not found."),
    },
    examples=[
        OpenApiExample(
            "Add and remove shops",
            value={"add": [1, 2, 3], "remove": [4]},
            request_only=True,
        ),
        OpenApiExample(
            "Add only",
            value={"add": [5, 6]},
            request_only=True,
        ),
        OpenApiExample(
            "Remove only",
            value={"remove": [7]},
            request_only=True,
        ),
        OpenApiExample(
            "Success response",
            value={
                "status": "success",
                "message": "Shop assignments updated successfully.",
                "data": {
                    "id": 1,
                    "name": "Royal Wedding Rentals",
                    "shop_count": 3,
                    "shops": [
                        {"id": 1, "name": "Wedding Charms", "place": "Ernakulam"},
                        {"id": 2, "name": "Wedding Charms Thrissur", "place": "Thrissur"},
                        {"id": 3, "name": "Wedding Charms Kochi", "place": "Kochi"},
                    ],
                },
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
@action(detail=False, methods=["post"], url_path=r"assign-shops/(?P<pk>\d+)")
def assign_shops(self, request, pk=None):
    try:
        org = Organization.objects.get(pk=pk)
    except Organization.DoesNotExist:
        return self.not_found_response(resource="Organization")

    data = request.data
    add_ids = data.get("add", None)
    remove_ids = data.get("remove", None)

    # At least one must be provided
    if add_ids is None and remove_ids is None:
        return self.validation_error_response(
            errors={"non_field_errors": ["At least one of 'add' or 'remove' must be provided."]},
            message="At least one of 'add' or 'remove' must be provided.",
        )

    # Validate types
    if add_ids is not None:
        if not isinstance(add_ids, list) or not all(isinstance(i, int) for i in add_ids):
            return self.validation_error_response(
                errors={"add": ["'add' must be a list of integers."]},
                message="'add' must be a list of integers.",
            )

    if remove_ids is not None:
        if not isinstance(remove_ids, list) or not all(isinstance(i, int) for i in remove_ids):
            return self.validation_error_response(
                errors={"remove": ["'remove' must be a list of integers."]},
                message="'remove' must be a list of integers.",
            )

    # Validate that all shop IDs in 'add' actually exist (exclude soft-deleted)
    if add_ids:
        existing_ids = set(
            Shop.objects.filter(id__in=add_ids, deleted__isnull=True).values_list("id", flat=True)
        )
        missing_ids = set(add_ids) - existing_ids
        if missing_ids:
            return self.validation_error_response(
                errors={"add": [f"The following shop IDs do not exist: {sorted(missing_ids)}."]},
                message=f"The following shop IDs do not exist: {sorted(missing_ids)}.",
            )

        # Guard against silently stealing shops from another organization
        conflicts = (
            Shop.objects.filter(id__in=add_ids, deleted__isnull=True)
            .exclude(organization__isnull=True)
            .exclude(organization=org)
        )
        if conflicts.exists():
            names = list(conflicts.values_list("name", flat=True))
            return self.validation_error_response(
                errors={"add": [f"The following shops are already assigned to another organization: {names}."]},
                message="One or more shops belong to a different organization.",
            )

    with transaction.atomic():
        if add_ids:
            Shop.objects.filter(id__in=add_ids, deleted__isnull=True).update(organization=org)

        if remove_ids:
            # Only unlink shops that are actually linked to this org
            Shop.objects.filter(id__in=remove_ids, organization=org).update(organization=None)

    # Return updated org with shop list
    updated_org = (
        Organization.objects
        .prefetch_related("shops")
        .annotate(shop_count=Count("shops"))
        .get(pk=org.pk)
    )
    return self.success_response(
        data=OrganizationSerializer(updated_org).data,
        message="Shop assignments updated successfully.",
    )
