from datetime import datetime

from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

_DATE_FORMATS = ['%Y-%m-%d', '%d-%m-%Y']


def parse_date(value, param_name):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValidationError({param_name: ["Invalid date format. Use YYYY-MM-DD or DD-MM-YYYY."]})


def _format_datetime(value):
    if not value:
        return None
    return timezone.localtime(value).strftime('%d-%m-%Y %H:%M:%S')


def apply_common_filters(qs, request):
    """
    Applies the filters shared by Investment and Withdrawal (both models
    have identical field names for all of these) to a single queryset.
    """
    account_id = request.query_params.get('account_id')
    if account_id:
        qs = qs.filter(account_id=account_id)

    payment_method = request.query_params.get('payment_method')
    if payment_method:
        qs = qs.filter(payment_method=payment_method)

    from_date = request.query_params.get('from_date')
    if from_date:
        qs = qs.filter(transaction_date__date__gte=parse_date(from_date, 'from_date'))

    to_date = request.query_params.get('to_date')
    if to_date:
        qs = qs.filter(transaction_date__date__lte=parse_date(to_date, 'to_date'))

    start_date = request.query_params.get('start_date')
    if start_date:
        qs = qs.filter(transaction_date__date__gte=parse_date(start_date, 'start_date'))

    end_date = request.query_params.get('end_date')
    if end_date:
        qs = qs.filter(transaction_date__date__lte=parse_date(end_date, 'end_date'))

    search = request.query_params.get('search')
    if search:
        search_q = (
            Q(notes__icontains=search)
            | Q(account__name__icontains=search)
            | Q(reference_number__icontains=search)
        )
        if search.isdigit():
            search_q |= Q(id=search)
        qs = qs.filter(search_q)

    return qs


def serialize_investment(obj):
    return {
        'id': obj.id,
        'type': 'investment',
        'account_id': obj.account_id,
        'account_name': obj.account.name if obj.account_id else None,
        'payment_method': obj.payment_method,
        'amount': obj.amount,
        'transaction_date': _format_datetime(obj.transaction_date),
        'remarks': obj.notes,
        'party_name': obj.investor_display_name,
        'reference_number': obj.reference_number,
        'status': obj.status,
        'created_by': (obj.created_by.full_name or obj.created_by.phone) if obj.created_by_id else None,
        'created_at': _format_datetime(obj.created_at),
        'updated_at': _format_datetime(obj.updated_at),
    }


def serialize_withdrawal(obj):
    return {
        'id': obj.id,
        'type': 'withdrawal',
        'account_id': obj.account_id,
        'account_name': obj.account.name if obj.account_id else None,
        'payment_method': obj.payment_method,
        'amount': obj.amount,
        'transaction_date': _format_datetime(obj.transaction_date),
        'remarks': obj.notes,
        'party_name': obj.withdrawn_by_display_name,
        'reference_number': obj.reference_number,
        'status': obj.status,
        'created_by': (obj.created_by.full_name or obj.created_by.phone) if obj.created_by_id else None,
        'created_at': _format_datetime(obj.created_at),
        'updated_at': _format_datetime(obj.updated_at),
    }
