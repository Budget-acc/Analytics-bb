import base64
import binascii
import logging

from django.db.models import Prefetch
from django.http import HttpResponse
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework.decorators import action

from tailored.models import TailorOrder, TailorOrderItem
from whatsapp_integration.v3.pdf_template import BasePDFService, TailorOrderPDFService
from whatsapp_integration.v3.services import WhatsAppService
from whatsapp_integration.v3.travelbot_service import resolve_whatsapp_provider, first_name

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Generate or send tailor order invoice",
    description="Returns the tailor order invoice PDF by default. When send_whatsapp=true, uploads the PDF and sends it to the client over WhatsApp.",
    parameters=[
        OpenApiParameter("pk", OpenApiTypes.INT, OpenApiParameter.PATH, required=True, description="Tailor order id."),
        OpenApiParameter("send_whatsapp", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False, description="Defaults to false. When true, sends invoice over WhatsApp instead of returning the PDF file."),
    ],
    responses={
        200: OpenApiResponse(description="Either a PDF file response or the exact success message: Tailor order invoice sent successfully to WhatsApp."),
        404: OpenApiResponse(description="Exact error message: Tailor order not found."),
        500: OpenApiResponse(description="Exact error messages:\n- Invalid PDF generated.\n- Failed to generate tailor order invoice PDF.\n- Failed to prepare PDF response.\n- Client phone number is missing. Cannot send invoice via WhatsApp.\n- Failed to upload invoice PDF. Please try again.\n- Failed to send invoice to WhatsApp.\n- Something went wrong while sending invoice to WhatsApp.\n- Unexpected error occurred while processing tailor order invoice."),
    },
    examples=[OpenApiExample("WhatsApp success", value={"status": "success", "message": "Tailor order invoice sent successfully to WhatsApp.", "data": {"order_id": 8, "shop_tailor_id": "TO-0008"}}, response_only=True, status_codes=["200"])],
)
@action(detail=False, methods=["get"], url_path="send-invoice/(?P<pk>[^/.]+)")
def send_invoice(self, request, *args, **kwargs):
    """
    Handles tailor order invoice:
    - Default (`send_whatsapp=false`): returns invoice PDF as file.
    - If `send_whatsapp=true`: sends invoice PDF via WhatsApp.
    """
    try:
        pk = kwargs.get("pk")
        shop_id = self.shop_id
        send_whatsapp = request.query_params.get("send_whatsapp", "false").lower() == "true"

        # Step 1: Fetch tailor order
        try:
            tailor_order = (
                TailorOrder.objects.select_related(
                    "shop",
                    "client",
                    "created_by",
                    "staff"
                )
                .prefetch_related(
                    Prefetch(
                        "items",
                        queryset=TailorOrderItem.objects.select_related("assigned_tailor")
                    ),
                    "additional_charges",
                    "payments",
                    "security_payments",
                    "security_adjustments",
                    "refund_entries",
                )
                .get(id=pk, shop_id=shop_id, deleted__isnull=True)
            )
        except TailorOrder.DoesNotExist:
            return self.not_found_response(
                resource="Tailor Order",
                message="Tailor order not found.",
                dev_message=f"Tailor order with id {pk} not found for shop {shop_id}.",
            )

        shop = tailor_order.shop
        client = tailor_order.client
        invoice_filename = BasePDFService.build_invoice_filename(
            client.client_name if client and client.client_name else "Customer",
            tailor_order.shop_tailor_id or tailor_order.id,
        )

        # Step 2: Generate tailor order invoice PDF (base64)
        try:
            pdf_base64 = TailorOrderPDFService.generate_tailor_order_pdf(
                shop=shop,
                client=client,
                tailor_order=tailor_order,
                items=tailor_order.items.all(),
            )

            # Validate PDF base64
            try:
                pdf_bytes = base64.b64decode(pdf_base64, validate=True)
            except (binascii.Error, ValueError):
                logger.error(f"Invalid base64 PDF generated for tailor order {tailor_order.id}")
                return self.error_response(
                    message="Invalid PDF generated.",
                    dev_message="PDFService returned non-base64 output.",
                )
        except Exception as e:
            logger.error(f"PDF generation failed for tailor order {tailor_order.id}: {str(e)}", exc_info=True)
            return self.error_response(
                message="Failed to generate tailor order invoice PDF.",
                dev_message=str(e),
            )

        # Step 3a: Default → return PDF file
        if not send_whatsapp:
            try:
                response = HttpResponse(pdf_bytes, content_type="application/pdf")
                response["Content-Disposition"] = (
                    f'attachment; filename="{invoice_filename}"'
                )
                return response
            except Exception as e:
                logger.error(f"Failed to return PDF file for tailor order {tailor_order.id}: {str(e)}", exc_info=True)
                return self.error_response(
                    message="Failed to prepare PDF response.",
                    dev_message=str(e),
                )

        # Step 3b: WhatsApp send
        # Determine client phone and name
        client_phone = None
        customer_name = "Customer"

        if client and client.phone_1_e164:
            client_phone = str(client.phone_1_e164)
            customer_name = client.client_name or "Customer"
        elif tailor_order.client_phone_number:
            client_phone = tailor_order.client_phone_number

        if not client_phone:
            return self.error_response(
                message="Client phone number is missing. Cannot send invoice via WhatsApp.",
                dev_message=f"Tailor order {tailor_order.id} has no client phone number.",
            )

        try:
            filename = invoice_filename
            whatsapp_service = WhatsAppService()

            # S3 upload is provider-agnostic — always via WhatsAppService.
            pdf_url = whatsapp_service.upload_pdf_to_s3(pdf_base64, filename)
            if not pdf_url:
                return self.error_response(
                    message="Failed to upload invoice PDF. Please try again.",
                    dev_message=f"S3 upload returned no URL for tailor order {tailor_order.id}.",
                )

            provider = resolve_whatsapp_provider(shop)
            if provider["provider"] == "shop_wayon":
                response = provider["service"].send_invoice_template(
                    phone_number=client_phone,
                    customer_first_name=first_name(customer_name),
                    template_name="custom_work_2",
                    pdf_url=pdf_url,
                    filename=filename,
                )
            elif provider["provider"] == "bookiebuddy_wayon":
                formatted_shop_phone = whatsapp_service._format_phone_number(shop.phone)
                shop_phone_display = whatsapp_service._with_plus(formatted_shop_phone)
                response = provider["service"].send_bookiebuddy_invoice_template(
                    client_phone,
                    first_name(customer_name),
                    shop.name,
                    shop_phone_display,
                    formatted_shop_phone,
                    "custom_work_2",
                    pdf_url,
                    filename,
                )
            else:
                response = whatsapp_service.send_tailor_order_invoice_template(
                    phone_number=client_phone,
                    customer_name=customer_name,
                    shop_name=shop.name,
                    shop_phone=shop.phone,
                    pdf_url=pdf_url,
                    filename=filename,
                )

            if not response.get("success"):
                logger.error(
                    f"WhatsApp send failed for tailor order {tailor_order.id}: {response.get('error')}"
                )
                return self.error_response(
                    message="Failed to send invoice to WhatsApp.",
                    dev_message=response.get("error"),
                )
        except Exception as e:
            logger.error(f"WhatsApp sending exception for tailor order {tailor_order.id}: {str(e)}", exc_info=True)
            return self.error_response(
                message="Something went wrong while sending invoice to WhatsApp.",
                dev_message=str(e),
            )

        # Step 4: Success response
        return self.success_response(
            message="Tailor order invoice sent successfully to WhatsApp.",
            data={
                "order_id": tailor_order.id,
                "shop_tailor_id": tailor_order.shop_tailor_id
            },
        )

    except Exception as e:
        logger.error(f"Unexpected error in tailor order send_invoice: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unexpected error occurred while processing tailor order invoice.",
            dev_message=str(e),
        )
