import logging
import base64
import binascii
import boto3
from django.conf import settings
from django.http import HttpResponse
from rest_framework.decorators import action
from urllib.parse import urlparse
from django.db.models import Prefetch
from booking_v2.models import BookingV2, BookedProductVariant
from whatsapp_integration.v3.services import WhatsAppService
from whatsapp_integration.v3.pdf_template import BasePDFService, BookingPDFService
from whatsapp_integration.v3.travelbot_service import resolve_whatsapp_provider, first_name
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")


def _try_cleanup_s3_pdf(pdf_url: str) -> None:
    """
    Delete an orphaned S3 PDF after a WhatsApp send failure.
    Errors are logged and swallowed — cleanup is best-effort.
    """
    try:
        bucket = settings.AWS_STORAGE_BUCKET_NAME
        expected_host = f"{bucket}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com"
        parsed = urlparse(pdf_url)
        if parsed.netloc == expected_host:
            s3_key = parsed.path.lstrip("/")
            boto3.client(
                "s3",
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=settings.AWS_S3_REGION_NAME,
            ).delete_object(Bucket=bucket, Key=s3_key)
            logger.info(f"Cleaned up orphaned S3 PDF: {s3_key}")
    except Exception as cleanup_err:
        logger.warning(f"Failed to clean up S3 PDF: {cleanup_err}")


@extend_schema(
    tags=["Booking Management V5"],
    summary="Get or send booking invoice",
    description="Generates booking invoice PDF. Returns file by default or sends invoice via WhatsApp when send_whatsapp=true.",
    parameters=[
        OpenApiParameter(name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True, description="Booking ID."),
        OpenApiParameter(name="send_whatsapp", type=OpenApiTypes.BOOL, location=OpenApiParameter.QUERY, required=False, default=False, description="If true, sends invoice over WhatsApp instead of returning file."),
    ],
    responses={
        200: OpenApiResponse(description="PDF response or exact message: Invoice sent successfully to WhatsApp."),
        404: OpenApiResponse(description="Exact message: Booking not found."),
        400: OpenApiResponse(description="Exact messages include: Invalid PDF generated.; Client phone number is missing. Cannot send invoice via WhatsApp.; Failed to upload invoice to S3.; Failed to send invoice to WhatsApp."),
        500: OpenApiResponse(description="Exact messages include: Failed to generate invoice PDF.; Failed to prepare PDF response.; Something went wrong while sending invoice to WhatsApp.; Unexpected error occurred while processing invoice."),
    },
)
@action(detail=False, methods=["get"], url_path="send-invoice/(?P<pk>[^/.]+)")
def send_invoice(self, request, *args, **kwargs):
    """
    Handles booking invoice:
    - Default (`send_whatsapp=false`): returns invoice PDF as file.
    - If `send_whatsapp=true`: sends invoice PDF via WhatsApp.
    """
    try:
        pk = kwargs.get("pk")
        shop_id = self.shop_id
        send_whatsapp = request.query_params.get("send_whatsapp", "false").lower() == "true"

        # Step 1: Fetch booking
        try:
            booking = (
                BookingV2.objects.select_related(
                    "client_identity",
                    "staff",
                    "shop",
                    "service__general_service__main_category",
                )
                .prefetch_related(
                    Prefetch(
                        "booked_items",
                        queryset=BookedProductVariant.objects.filter(
                            deleted__isnull=True
                        ).select_related(
                            "product_variant__product__shop_service__general_service__main_category"
                        ),
                    ),
                    "requirements",
                    "additional_charges",
                )
                .get(id=pk, shop_id=shop_id)
            )
        except BookingV2.DoesNotExist:
            return self.not_found_response(
                resource="Booking",
                message="Booking not found.",
                dev_message=f"Booking with id {pk} not found for shop {shop_id}.",
            )

        shop = booking.shop
        client = booking.client_identity
        customer_name = client.client_name if client and client.client_name else "Customer"
        invoice_filename = BasePDFService.build_invoice_filename(
            customer_name, booking.shop_booking_id or booking.id
        )

        # Evaluate the prefetch cache once and reuse across service-name extraction and PDF generation.
        booked_items = list(booking.booked_items.all())

        # Extract main service name from active booked items (prefetched, zero extra queries).
        # Falls back to booking.service FK (select_related) if all items are soft-deleted.
        main_service_name = ""
        for item in booked_items:
            try:
                name = (
                    item.product_variant
                    .product
                    .shop_service
                    .general_service
                    .main_category
                    .name
                )
                if name:
                    main_service_name = name
                    break
            except AttributeError:
                continue

        if not main_service_name:
            try:
                main_service_name = (
                    booking.service.general_service.main_category.name or ""
                )
            except AttributeError:
                pass

        # Step 2: Generate invoice PDF (base64)
        try:
            pdf_base64 = BookingPDFService.generate_transaction_pdf(
                shop=shop,
                client=client,
                transaction=booking,
                items=booked_items,
                invoice_prefix="BOOK",
                main_service_name=main_service_name,
            )
            # Validate PDF base64
            try:
                pdf_bytes = base64.b64decode(pdf_base64, validate=True)
            except (binascii.Error, ValueError):
                logger.error(f"Invalid base64 PDF generated for booking {booking.id}")
                return self.error_response(
                    message="Invalid PDF generated.",
                    dev_message="PDFService returned non-base64 output.",
                )
        except Exception as e:
            logger.error(f"PDF generation failed for booking {booking.id}: {str(e)}", exc_info=True)
            return self.error_response(
                message="Failed to generate invoice PDF.",
                dev_message=str(e),
            )

        # Step 3a: Default - return PDF file
        if not send_whatsapp:
            try:
                response = HttpResponse(pdf_bytes, content_type="application/pdf")
                response["Content-Disposition"] = (
                    f'attachment; filename="{invoice_filename}"'
                )
                return response
            except Exception as e:
                logger.error(f"Failed to return PDF file for booking {booking.id}: {str(e)}", exc_info=True)
                return self.error_response(
                    message="Failed to prepare PDF response.",
                    dev_message=str(e),
                )

        # Step 3b: WhatsApp send
        if not client.phone_1_e164:
            return self.error_response(
                message="Client phone number is missing. Cannot send invoice via WhatsApp.",
                dev_message=f"Booking {booking.id} has no client phone number.",
            )

        pdf_url = None
        try:
            whatsapp_service = WhatsAppService()
            filename = invoice_filename

            # S3 upload is provider-agnostic (just puts the PDF in our own
            # bucket) — always done via WhatsAppService regardless of which
            # provider ultimately sends the message.
            pdf_url = whatsapp_service.upload_pdf_to_s3(pdf_base64, filename)

            if not pdf_url:
                logger.error(f"Failed to upload PDF to S3 for booking {booking.id}")
                return self.error_response(
                    message="Failed to upload invoice to S3.",
                    dev_message="PDF upload to S3 returned no URL",
                )

            provider = resolve_whatsapp_provider(shop)
            if provider["provider"] == "shop_wayon":
                response = provider["service"].send_invoice_template(
                    phone_number=str(client.phone_1_e164),
                    customer_first_name=first_name(client.client_name),
                    template_name="rental_invoice_5",
                    pdf_url=pdf_url,
                    filename=filename,
                )
            elif provider["provider"] == "bookiebuddy_wayon":
                formatted_shop_phone = whatsapp_service._format_phone_number(shop.phone)
                shop_phone_display = whatsapp_service._with_plus(formatted_shop_phone)
                response = provider["service"].send_bookiebuddy_invoice_template(
                    str(client.phone_1_e164),
                    first_name(client.client_name),
                    shop.name,
                    shop_phone_display,
                    formatted_shop_phone,
                    "rental_invoice_5",
                    pdf_url,
                    filename,
                )
            else:
                response = whatsapp_service.send_booking_invoice_template(
                    phone_number=client.phone_1_e164,
                    customer_name=client.client_name,
                    shop_name=shop.name,
                    shop_phone=shop.phone,
                    pdf_url=pdf_url,
                    filename=filename,
                )

            if not response.get("success"):
                logger.error(
                    f"WhatsApp send failed for booking {booking.id}: {response.get('error')}"
                )
                _try_cleanup_s3_pdf(pdf_url)
                return self.error_response(
                    message="Failed to send invoice to WhatsApp.",
                    dev_message=response.get("error"),
                )
        except Exception as e:
            logger.error(f"WhatsApp sending exception for booking {booking.id}: {str(e)}", exc_info=True)
            if pdf_url:
                _try_cleanup_s3_pdf(pdf_url)
            return self.error_response(
                message="Something went wrong while sending invoice to WhatsApp.",
                dev_message=str(e),
            )

        # Step 4: Success response
        return self.success_response(
            message="Invoice sent successfully to WhatsApp.",
            data={"booking_id": booking.id},
        )

    except Exception as e:
        logger.error(f"Unexpected error in send_invoice: {str(e)}", exc_info=True)
        return self.error_response(
            message="Unexpected error occurred while processing invoice.",
            dev_message=str(e),
        )


__all__ = ["send_invoice"]
