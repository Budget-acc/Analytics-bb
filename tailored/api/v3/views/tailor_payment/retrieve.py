import logging

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema

from subscriptions.api.admin_v3.utils.permissions import skip_subscription_validation
from tailored.api.v3.serializers.tailor_payment import TailorPaymentReadSerializer
from tailored.models import TailorPayment

logger = logging.getLogger("api_requests")


@extend_schema(
    tags=["Custom Work Management V3"],
    summary="Retrieve a tailor payment",
    description=(
        "Fetches the full details of a single tailor payment by id.\n\n"
        "Purpose: Lets shop staff view or verify a specific payment record (e.g. before "
        "editing or deleting it, or when reconciling accounts).\n\n"
        "Context: Used when a user opens a specific payment entry from the tailor payment "
        "list or history screen. The payment must belong to the active shop.\n\n"
        "Accepted inputs:\n"
        "Path parameters — Required:\n"
        "- pk (integer): the tailor payment id to fetch."
    ),
    parameters=[
        OpenApiParameter(
            name="pk", type=OpenApiTypes.INT, location=OpenApiParameter.PATH, required=True,
            description=(
                "Purpose: Identifies which tailor payment record to fetch. "
                "Location: URL path parameter. "
                "Format/Type: integer (TailorPayment id). "
                "Required."
            ),
        ),
    ],
    responses={
        200: OpenApiResponse(
            description=(
                "Success. Exact message: Tailor payment retrieved successfully. "
                "Data includes id, tailor_id, tailor_name, transaction_date_time, amount, "
                "payment_method, account_id, account_name, category_id, category_name, "
                "notes, created_at."
            )
        ),
        404: OpenApiResponse(description="Exact message: Tailor payment not found."),
        403: OpenApiResponse(description="Exact message: Access to this shop has been deactivated."),
        500: OpenApiResponse(description="Exact message: Something went wrong while fetching the tailor payment."),
    },
)
@skip_subscription_validation
def retrieve(self, request, pk=None):
    try:
        payment = (
            TailorPayment.objects
            .select_related('tailor', 'account', 'category')
            .get(pk=pk, shop_id=self.shop_id)
        )
        return self.success_response(
            data=TailorPaymentReadSerializer(payment).data,
            message="Tailor payment retrieved successfully.",
        )
    except TailorPayment.DoesNotExist:
        return self.not_found_response(
            resource="TailorPayment",
            message="Tailor payment not found.",
            dev_message=f"TailorPayment with id {pk} not found for shop {self.shop_id}.",
        )
    except Exception as e:
        logger.error(f"TailorPayment retrieve error: {e}", exc_info=True)
        return self.error_response(
            message="Something went wrong while fetching the tailor payment.",
            dev_message=str(e),
        )
