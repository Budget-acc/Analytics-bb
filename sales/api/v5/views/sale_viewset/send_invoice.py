import base64
import binascii
import logging

from django.http import HttpResponse
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.decorators import action

from sales.models import Sale
from whatsapp_integration.v3.pdf_template import SalesPDFService
from whatsapp_integration.v3.services import WhatsAppService
from whatsapp_integration.v3.travelbot_service import resolve_whatsapp_provider, first_name

logger = logging.getLogger('api_requests')


class SaleInvoiceMixin:

    @extend_schema(
        tags=["Sales Management V5"],
        summary="Send or download sale invoice",
        description="Returns invoice PDF by default. When send_whatsapp=true, sends invoice via WhatsApp and returns JSON status.",
        parameters=[
            OpenApiParameter(name="pk", type=OpenApiTypes.STR, location=OpenApiParameter.PATH, required=True, description="Sale id."),
            OpenApiParameter(name="send_whatsapp", type=OpenApiTypes.BOOL, location=OpenApiParameter.QUERY, required=False, default=False, description="Set true to send WhatsApp template with invoice PDF."),
        ],
        responses={
            200: OpenApiResponse(description="Either binary PDF response or JSON success response."),
            404: OpenApiResponse(description="Exact message: Sale not found."),
            500: OpenApiResponse(
                description=(
                    "Error messages. Exact messages can include:\n"
                    "- Invalid PDF generated.\n"
                    "- Failed to generate sales invoice PDF.\n"
                    "- Failed to prepare PDF response.\n"
                    "- Client phone number is missing. Cannot send invoice via WhatsApp.\n"
                    "- Failed to send invoice to WhatsApp.\n"
                    "- Something went wrong while sending invoice to WhatsApp.\n"
                    "- Unexpected error occurred while processing sales invoice."
                )
            ),
        },
        examples=[
            OpenApiExample("Request example - download", value={"pk": 55, "send_whatsapp": False}, request_only=True),
            OpenApiExample("Request example - WhatsApp", value={"pk": 55, "send_whatsapp": True}, request_only=True),
            OpenApiExample("Success response", value={"status": "success", "message": "Sales invoice sent successfully to WhatsApp.", "data": {"sale_id": 55}}, response_only=True, status_codes=["200"]),
            OpenApiExample("Not found response", value={"status": "not_found", "message": "Sale not found.", "data": None}, response_only=True, status_codes=["404"]),
            OpenApiExample("WhatsApp failure response", value={"status": "error", "message": "Failed to send invoice to WhatsApp.", "data": None}, response_only=True, status_codes=["500"]),
        ],
    )
    @action(detail=False, methods=["get"], url_path="send-invoice/(?P<pk>[^/.]+)")
    def send_invoice(self, request, *args, **kwargs):
        """
        Handles sales invoice:
        - Default (`send_whatsapp=false`): returns invoice PDF as file.
        - If `send_whatsapp=true`: sends invoice PDF via WhatsApp.
        """
        try:
            pk = kwargs.get("pk")
            shop_id = self.shop_id
            send_whatsapp = request.query_params.get("send_whatsapp", "false").lower() == "true"

            # Step 1: Fetch sale
            try:
                sale = (
                    Sale.objects.select_related(
                        "client",
                        "staff",
                        "shop",
                    )
                    .prefetch_related(
                        "items__product_variant__product__shop_service__general_service__main_category",
                        "payments",
                    )
                    .get(id=pk, shop_id=shop_id)
                )
            except Sale.DoesNotExist:
                return self.not_found_response(
                    resource="Sale",
                    message="Sale not found.",
                    dev_message=f"Sale with id {pk} not found for shop {shop_id}.",
                )

            shop = sale.shop
            client = sale.client
            client_phone_e164 = str(sale.client_phone_e164) if sale.client_phone_e164 else None

            # Step 2: Generate sales invoice PDF (base64)
            try:
                pdf_base64 = SalesPDFService.generate_sale_pdf(
                    shop=shop,
                    client=client,
                    sale=sale,
                    items=sale.items.all(),
                    invoice_prefix="SAL",
                )
                # Validate PDF base64
                try:
                    pdf_bytes = base64.b64decode(pdf_base64, validate=True)
                except (binascii.Error, ValueError):
                    logger.error(f"Invalid base64 PDF generated for sale {sale.id}")
                    return self.error_response(
                        message="Invalid PDF generated.",
                        dev_message="SalesPDFService returned non-base64 output.",
                    )
            except Exception as e:
                logger.error(f"PDF generation failed for sale {sale.id}: {str(e)}", exc_info=True)
                return self.error_response(
                    message="Failed to generate sales invoice PDF.",
                    dev_message=str(e),
                )

            # Step 3a: Default → return PDF file
            if not send_whatsapp:
                try:
                    response = HttpResponse(pdf_bytes, content_type="application/pdf")
                    response["Content-Disposition"] = (
                        f'attachment; filename="Sale_{sale.shop_sale_id or sale.id}.pdf"'
                    )
                    return response
                except Exception as e:
                    logger.error(f"Failed to return PDF file for sale {sale.id}: {str(e)}", exc_info=True)
                    return self.error_response(
                        message="Failed to prepare PDF response.",
                        dev_message=str(e),
                    )

            # Step 3b: WhatsApp send
            if not client_phone_e164:
                return self.error_response(
                    message="Client phone number is missing. Cannot send invoice via WhatsApp.",
                    dev_message=f"Sale {sale.id} has no sale-level client_phone_e164.",
                )

            try:
                whatsapp_service = WhatsAppService()
                filename = f"Sale_{sale.shop_sale_id or sale.id}.pdf"
                # S3 upload is provider-agnostic — always via WhatsAppService.
                pdf_url = whatsapp_service.upload_pdf_to_s3(pdf_base64, filename)
                if not pdf_url:
                    logger.error(f"Failed to upload sale invoice PDF to S3 for sale {sale.id}.")
                    return self.error_response(
                        message="Failed to send invoice to WhatsApp.",
                        dev_message="Could not upload invoice PDF to S3."
                    )

                customer_name = client.client_name if client and client.client_name else "Customer"
                provider = resolve_whatsapp_provider(shop) if shop else {"provider": "wacto", "service": None}
                if provider["provider"] == "shop_wayon":
                    response = provider["service"].send_invoice_template(
                        phone_number=client_phone_e164,
                        customer_first_name=first_name(customer_name),
                        template_name="sale_invoice_2",
                        pdf_url=pdf_url,
                        filename=filename,
                    )
                elif provider["provider"] == "bookiebuddy_wayon":
                    formatted_shop_phone = whatsapp_service._format_phone_number(getattr(shop, "phone", ""))
                    shop_phone_display = whatsapp_service._with_plus(formatted_shop_phone)
                    response = provider["service"].send_bookiebuddy_invoice_template(
                        client_phone_e164,
                        first_name(customer_name),
                        shop.name if shop else "",
                        shop_phone_display,
                        formatted_shop_phone,
                        "sale_invoice_2",
                        pdf_url,
                        filename,
                    )
                else:
                    response = whatsapp_service.send_sale_invoice_template(
                        phone_number=client_phone_e164,
                        customer_name="Customer",
                        shop_name=sale.shop.name if sale.shop else "",
                        shop_phone=getattr(sale.shop, "phone", "") if sale.shop else "",
                        pdf_url=pdf_url,
                        filename=filename,
                    )

                if not response.get("success"):
                    logger.error(
                        f"WhatsApp send failed for sale {sale.id}: {response.get('error')}"
                    )
                    return self.error_response(
                        message="Failed to send invoice to WhatsApp.",
                        dev_message=response.get("error"),
                    )
            except Exception as e:
                logger.error(f"WhatsApp sending exception for sale {sale.id}: {str(e)}", exc_info=True)
                return self.error_response(
                    message="Something went wrong while sending invoice to WhatsApp.",
                    dev_message=str(e),
                )

            # Step 4: Success response
            return self.success_response(
                message="Sales invoice sent successfully to WhatsApp.",
                data={"sale_id": sale.id},
            )

        except Exception as e:
            logger.error(f"Unexpected error in sales send_invoice: {str(e)}", exc_info=True)
            return self.error_response(
                message="Unexpected error occurred while processing sales invoice.",
                dev_message=str(e),
            )
