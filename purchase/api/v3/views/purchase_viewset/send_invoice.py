import logging

from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.decorators import action

from purchase.models import Purchase

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Purchase Management V3"],
    summary="Download purchase invoice PDF",
    description=(
        "Generates and returns the purchase record as a PDF file download. "
        "This is an internal record of an external purchase from a vendor - "
        "there is no WhatsApp send option (unlike Booking/Sale invoices), "
        "since the vendor already provides their own invoice."
    ),
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Purchase ID."),
    ],
    responses={
        200: OpenApiResponse(description="PDF file download."),
        404: OpenApiResponse(description="Exact message: Purchase not found."),
        500: OpenApiResponse(description="Exact messages include: Failed to generate purchase PDF.; Failed to prepare PDF response."),
    },
)
@action(detail=False, methods=["get"], url_path="send-invoice/(?P<pk>[^/.]+)")
def send_invoice(self, request, *args, **kwargs):
    pk = kwargs.get("pk")
    try:
        purchase = (
            Purchase.objects.select_related("vendor", "shop")
            .prefetch_related("items", "additional_charges__account", "payments__account")
            .filter(id=pk, shop_id=self.shop_id, deleted__isnull=True)
            .first()
        )
        if not purchase:
            return self.not_found_response(resource="Purchase", message="Purchase not found.")

        try:
            from pdf_service.api.v3.utils.purchase_pdf import PurchasePDFService

            pdf_bytes = PurchasePDFService.generate_purchase_pdf(
                shop=purchase.shop,
                vendor=purchase.vendor,
                purchase=purchase,
                items=list(purchase.items.all()),
                additional_charges=list(purchase.additional_charges.all()),
                payments=list(purchase.payments.all()),
            )
        except Exception as e:
            logger.error(f"PDF generation failed for purchase {purchase.id}: {str(e)}", exc_info=True)
            return self.error_response(
                message="Failed to generate purchase PDF.",
                dev_message=str(e),
            )

        try:
            filename = f"purchase_{purchase.purchase_code or purchase.id}.pdf"
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            logger.error(f"Failed to return PDF file for purchase {purchase.id}: {str(e)}", exc_info=True)
            return self.error_response(
                message="Failed to prepare PDF response.",
                dev_message=str(e),
            )

    except Exception as e:
        logger.error(f"Purchase send_invoice error: {str(e)}", exc_info=True)
        return self.error_response(
            message="Something went wrong while generating the purchase PDF.",
            dev_message=str(e),
        )


__all__ = ["send_invoice"]
