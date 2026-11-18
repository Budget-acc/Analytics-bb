"""
Purchase module specific response mixins.
Extends StandardResponseMixin with vendor and purchase-specific response methods.
"""

from ERP.utils.mixins import StandardResponseMixin
from rest_framework import status


class VendorResponseMixin(StandardResponseMixin):
    """
    Mixin providing vendor-specific standardized response methods.
    Extends the core StandardResponseMixin with domain-specific helpers.
    """

    def vendor_created_response(self, vendor_data, **kwargs):
        """Vendor created successfully"""
        return self.success_response(
            data=vendor_data,
            message="Vendor created successfully.",
            http_status=status.HTTP_201_CREATED,
            **kwargs,
        )

    def vendor_updated_response(self, vendor_data, **kwargs):
        """Vendor updated successfully"""
        return self.success_response(
            data=vendor_data,
            message="Vendor updated successfully.",
            **kwargs,
        )

    def vendor_retrieved_response(self, vendor_data, **kwargs):
        """Vendor retrieved successfully"""
        return self.success_response(
            data=vendor_data,
            message="Vendor retrieved successfully.",
            **kwargs,
        )

    def vendor_deleted_response(self, **kwargs):
        """Vendor deleted successfully"""
        return self.success_response(
            message="Vendor deleted successfully.",
            http_status=status.HTTP_204_NO_CONTENT,
            **kwargs,
        )

    def vendor_not_found_response(self, vendor_id=None, **kwargs):
        """Vendor not found"""
        return self.not_found_response(
            message="Vendor not found.",
            dev_message=f"Vendor '{vendor_id}' not found." if vendor_id else None,
            **kwargs,
        )

    def duplicate_vendor_response(self, vendor_name=None, **kwargs):
        """Duplicate vendor"""
        return self.validation_error_response(
            errors={
                "name": [
                    "Vendor with this name already exists."
                ]
            },
            message="Vendor with this name already exists.",
            dev_message=f"Duplicate vendor: {vendor_name}" if vendor_name else None,
            **kwargs,
        )

    def vendor_has_purchases_response(self, **kwargs):
        """Vendor cannot be deleted"""
        return self.error_response(
            message="Vendor has existing purchases and cannot be deleted.",
            http_status=status.HTTP_400_BAD_REQUEST,
            **kwargs,
        )

    def vendor_purchase_history_response(self, data, **kwargs):
        """Vendor purchase history"""
        return self.success_response(
            data=data,
            message="Vendor purchase history retrieved successfully.",
            **kwargs,
        )
