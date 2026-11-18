import logging
from datetime import datetime, timedelta, time
from django.db.models import Sum
from django.utils.timezone import make_aware
from pytz import timezone as pytz_timezone
from rest_framework.decorators import action
from booking_v2.models import BookedProductVariant
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

logger = logging.getLogger("api_requests")
india = pytz_timezone('Asia/Kolkata')


def parse_date_string(date_str):
    formats = ["%d-%m-%Y", "%d-%m-%YT%H:%M:%S"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue


@extend_schema(
    tags=["Booking Management V5"],
    summary="Check variant availability",
    description="Returns availability for a product variant in rental or sale mode using date window checks.",
    parameters=[
        OpenApiParameter(name="identifier", type=OpenApiTypes.STR, location=OpenApiParameter.PATH, required=True, description="Variant ID or external QR identifier based on identifier_type."),
        OpenApiParameter(name="mode", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, default="rental", description="Allowed values: rental, sale."),
        OpenApiParameter(name="identifier_type", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Use external_qr to resolve by external QR code."),
        OpenApiParameter(name="pickup_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Required for rental mode. Format DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS."),
        OpenApiParameter(name="return_date", type=OpenApiTypes.STR, location=OpenApiParameter.QUERY, required=False, description="Required for rental mode. Format DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS."),
    ],
    responses={
        200: OpenApiResponse(description="Success messages include: Variant availability returned for sale mode.; Variant is fully available for the selected date range.; Selected variant is not available for the chosen date range."),
        400: OpenApiResponse(description="Exact messages include: Invalid mode query param. Use 'rental' (default) or 'sale'.; Please provide both 'pickup_date' and 'return_date'.; Invalid date/time format.; 'return_date' must be after 'pickup_date'.; Invalid identifier type."),
        404: OpenApiResponse(description="Exact message: Product variant not found."),
        500: OpenApiResponse(description="Exact message: Failed to check variant availability."),
    },
)
@action(detail=False, methods=["get"], url_path=r"variant-availability/(?P<identifier>[^/.]+)")
def variant_availability(self, request, identifier=None):
    """
    GET /.../variant-availability/{identifier}/?from=<datetime>&to=<datetime>

    Query params:
      - pickup_date : start datetime or date (formats supported by parse_date_string)
      - return_date : end datetime or date (date-only is treated as inclusive)

    Returns product + single-variant availability payload. `message` is user-facing,
    `dev_message` contains diagnostics for front-end developers.
    """
    try:
        mode = (request.query_params.get("mode") or "rental").lower().strip()
        if mode not in ("rental", "sale"):
            return self.validation_error_response(
                errors={"mode": ["Invalid mode. Use 'rental' or 'sale'."]},
                message="Invalid mode query param. Use 'rental' (default) or 'sale'."
            )

        start_raw = request.query_params.get("pickup_date")
        end_raw = request.query_params.get("return_date")

        if mode == "rental" and (not start_raw or not end_raw):
            return self.validation_error_response(
                errors={"params": ["Missing required query params 'pickup_date' and 'return_date'."]},
                message="Please provide both 'pickup_date' and 'return_date'."
            )

        def looks_like_date_only(s: str) -> bool:
            return ("T" not in s) and (":" not in s)

        def to_aware(dt):
            if dt is None:
                return None
            if dt.tzinfo is None:
                return make_aware(dt, india)
            return dt.astimezone(india)

        start_dt = end_dt = None
        if start_raw or end_raw:
            try:
                parsed_start = parse_date_string(start_raw)
                parsed_end = parse_date_string(end_raw)
                if parsed_start is None or parsed_end is None:
                    raise ValueError("Could not parse dates")

                start_dt = to_aware(parsed_start)
                end_dt = to_aware(parsed_end)

                if looks_like_date_only(end_raw):
                    end_dt = end_dt + timedelta(days=1)

            except Exception:
                return self.validation_error_response(
                    errors={"date": ["Invalid datetime format. Use DD-MM-YYYY or DD-MM-YYYYTHH:MM:SS"]},
                    message="Invalid date/time format."
                )

            if end_dt <= start_dt:
                return self.validation_error_response(
                    errors={"date": ["'return_date' must be after 'pickup_date'."]},
                    message="'return_date' must be after 'pickup_date'."
                )

        identifier_type = (
            request.query_params.get("identifier_type") or ""
        ).lower().strip()

        if identifier_type and identifier_type != "external_qr":
            return self.validation_error_response(
                errors={
                    "identifier_type": [
                        "Invalid value. Use 'external_qr' or omit for variant ID."
                    ]
                },
                message="Invalid identifier type."
            )

        variant = self._get_variant(identifier, identifier_type)

        if not variant:
            return self.not_found_response(
                resource="ProductVariant",
                message="Product variant not found."
            )

        product = variant.product

        if mode == "sale":
            variant_data = {
                "id": variant.id,
                "attribute": variant.attribute,
                "price": variant.price,
                "stock": int(variant.stock or 0),
                "remaining_stock": int(variant.stock or 0),
                "fabric_length": variant.fabric_length,
                "unique_code": variant.unique_code,
                "external_qr_code": variant.external_qr_code,
            }
            product_data = {
                "id": product.id,
                "name": product.name,
                "price": product.price,
                "color": product.color,
                "category": product.category,
                "model": product.model,
                "image": product.image,
                "variants": [variant_data],
            }
            dev_message = {
                "mode": "sale",
                "variant_id": variant.id,
                "stock": int(variant.stock or 0),
                "note": "BookedProductVariant overlap checks skipped in sale mode."
            }
            return self.success_response(
                data={"product": product_data},
                message="Variant availability returned for sale mode.",
                dev_message=dev_message
            )

        booking_qs = (
            BookedProductVariant.objects
            .filter(
                product_variant=variant,
                booking__pickup_date__lt=end_dt,
                booking__return_date__gt=start_dt,
                booking__deleted__isnull=True,
                booking__booking_status="upcoming",
            )
            .values("booking_id", "booking__pickup_date", "booking__return_date")
            .annotate(total_qty=Sum("quantity"))
        )

        booking_rows = list(booking_qs)

        if not booking_rows:
            variant_data = {
                "id": variant.id,
                "attribute": variant.attribute,
                "price": variant.price,
                "stock": int(variant.stock or 0),
                "remaining_stock": int(variant.stock or 0),
                "fabric_length": variant.fabric_length,
                "unique_code": variant.unique_code,
                "external_qr_code": variant.external_qr_code,
            }
            product_data = {
                "id": product.id,
                "name": product.name,
                "price": product.price,
                "color": product.color,
                "category": product.category,
                "model": product.model,
                "image": product.image,
                "variants": [variant_data],
            }
            return self.success_response(
                data={"product": product_data},
                message="Variant is fully available for the selected date range."
            )

        bookings = [
            (r["booking__pickup_date"], r["booking__return_date"], int(r["total_qty"] or 0))
            for r in booking_rows
        ]

        start_date = start_dt.date()
        end_date = (end_dt - timedelta(seconds=1)).date()
        total_days = (end_date - start_date).days + 1
        if total_days < 1:
            total_days = 1

        per_day_max = {}
        worst_count = 0

        for i in range(total_days):
            day = start_date + timedelta(days=i)
            day_start = make_aware(datetime.combine(day, time.min), india)
            day_end = make_aware(datetime.combine(day, time.max), india)

            window_start = max(day_start, start_dt)
            window_end = min(day_end, end_dt)
            if window_end <= window_start:
                per_day_max[day.strftime("%d-%m-%Y")] = 0
                continue

            events = []
            for b_start, b_end, qty in bookings:
                clip_start = max(b_start, window_start)
                clip_end = min(b_end, window_end)
                if clip_end <= clip_start or qty <= 0:
                    continue
                events.append((clip_start, qty))
                events.append((clip_end, -qty))

            if not events:
                per_day_max[day.strftime("%d-%m-%Y")] = 0
                continue

            events.sort(key=lambda x: (x[0], -x[1]))

            concurrent = 0
            max_concurrent = 0
            for _, delta in events:
                concurrent += delta
                if concurrent > max_concurrent:
                    max_concurrent = concurrent

            per_day_max[day.strftime("%d-%m-%Y")] = max_concurrent
            if max_concurrent > worst_count:
                worst_count = max_concurrent

        remaining_stock = max(0, int(variant.stock or 0) - worst_count)

        variant_data = {
            "id": variant.id,
            "attribute": variant.attribute,
            "price": variant.price,
            "stock": int(variant.stock or 0),
            "remaining_stock": remaining_stock,
            "fabric_length": variant.fabric_length,
        }
        product_data = {
            "id": product.id,
            "name": product.name,
            "price": product.price,
            "color": product.color,
            "category": product.category,
            "model": product.model,
            "image": product.image,
            "variants": [variant_data],
        }

        dev_message = {
            "requested_from": start_dt.isoformat(),
            "requested_to": end_dt.isoformat(),
            "variant_id": variant.id,
            "stock": int(variant.stock or 0),
            "worst_day_concurrent": worst_count,
            "per_day_max_concurrent": per_day_max,
            "bookings_considered": len(bookings),
        }

        if remaining_stock <= 0:
            return self.success_response(
                data={"message": "Product is not available for chosen date range"},
                message="Selected variant is not available for the chosen date range.",
                dev_message=dev_message
            )

        return self.success_response(
            data={"product": product_data},
            message=f"Variant available. Remaining stock for requested range: {remaining_stock}.",
            dev_message=dev_message
        )

    except Exception as e:
        logger.error(f"[variant_availability] Error for variant: {str(e)}", exc_info=True)
        return self.error_response(
            message="Failed to check variant availability.",
            dev_message=str(e)
        )


__all__ = ["variant_availability"]
