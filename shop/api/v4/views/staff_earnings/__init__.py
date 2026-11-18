from .base import StaffEarningsViewSet
from .helpers.validators import _validate_staff_exists
from .helpers.time_utils import _get_target_year, _get_target_year_month
from .helpers.data_fetchers import (
    _staff_bookings_earnings_qs,
    _staff_sales_earnings_qs,
    _staff_tailor_orders_qs,
    _get_staff_monthly_transaction_rows,
    _get_staff_bookings_data,
    _get_staff_sales_data,
    _get_staff_tailor_data,
    _pending_bookings_queryset,
    _get_pending_bookings_data,
    _paginate_bookings_response,
    _get_monthly_bookings,
    _get_monthly_sales,
    _get_monthly_tailor_orders,
    _get_monthly_pending_bookings,
)
from .get_earnings_report import get_earnings_report
from .get_monthly_transactions import get_monthly_transactions
from .export_earnings_excel import export_earnings_excel

StaffEarningsViewSet._validate_staff_exists = _validate_staff_exists
StaffEarningsViewSet._get_target_year = _get_target_year
StaffEarningsViewSet._get_target_year_month = _get_target_year_month
StaffEarningsViewSet._staff_bookings_earnings_qs = _staff_bookings_earnings_qs
StaffEarningsViewSet._staff_sales_earnings_qs = _staff_sales_earnings_qs
StaffEarningsViewSet._staff_tailor_orders_qs = _staff_tailor_orders_qs
StaffEarningsViewSet._get_staff_monthly_transaction_rows = _get_staff_monthly_transaction_rows
StaffEarningsViewSet._get_staff_bookings_data = _get_staff_bookings_data
StaffEarningsViewSet._get_staff_sales_data = _get_staff_sales_data
StaffEarningsViewSet._get_staff_tailor_data = _get_staff_tailor_data
StaffEarningsViewSet._pending_bookings_queryset = _pending_bookings_queryset
StaffEarningsViewSet._get_pending_bookings_data = _get_pending_bookings_data
StaffEarningsViewSet._paginate_bookings_response = _paginate_bookings_response
StaffEarningsViewSet._get_monthly_bookings = _get_monthly_bookings
StaffEarningsViewSet._get_monthly_sales = _get_monthly_sales
StaffEarningsViewSet._get_monthly_tailor_orders = _get_monthly_tailor_orders
StaffEarningsViewSet._get_monthly_pending_bookings = _get_monthly_pending_bookings
StaffEarningsViewSet.get_earnings_report = get_earnings_report
StaffEarningsViewSet.get_monthly_transactions = get_monthly_transactions
StaffEarningsViewSet.export_earnings_excel = export_earnings_excel
