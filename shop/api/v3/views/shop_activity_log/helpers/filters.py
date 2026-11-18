from django.db.models import Q
from django.core.exceptions import ValidationError

# Upper bound on comma-separated action values accepted in one request
MAX_ACTION_FILTERS = 20


def _parse_action_filter(raw_value):
    """'deleted, Updated,,deleted' -> ['deleted', 'updated']: trimmed, lowercased
    (actions are stored lowercase), blanks dropped, de-duplicated in order, capped."""
    actions = (part.strip().lower() for part in (raw_value or "").split(","))
    return list(dict.fromkeys(a for a in actions if a))[:MAX_ACTION_FILTERS]


def _apply_filters(self, qs, request):
    """Apply all filters to the queryset"""
    filters = Q()

    q = (request.query_params.get("q") or "").strip()
    actions = _parse_action_filter(request.query_params.get("action"))
    user_name = (request.query_params.get("user") or "").strip()
    ct = (request.query_params.get("content_type") or "").strip()
    start_date = request.query_params.get("start_date")
    end_date = request.query_params.get("end_date")

    if q:
        filters &= Q(log_message__icontains=q)
    if actions:
        # One or more comma-separated action types (e.g. "deleted,updated")
        filters &= Q(action__in=actions)
    if user_name:
        filters &= (
            Q(user__full_name__icontains=user_name)
            | Q(user__phone__icontains=user_name)
        )
    if ct:
        if "." in ct:
            app_label, model = ct.split(".", 1)
            filters &= Q(content_type__app_label__iexact=app_label, content_type__model__iexact=model)
        else:
            filters &= Q(content_type__model__iexact=ct)
    if start_date:
        try:
            filters &= Q(created_at__date__gte=start_date)
        except Exception:
            raise ValidationError({"start_date": ["Invalid date format. Use YYYY-MM-DD"]})
    if end_date:
        try:
            filters &= Q(created_at__date__lte=end_date)
        except Exception:
            raise ValidationError({"end_date": ["Invalid date format. Use YYYY-MM-DD"]})

    return qs.filter(filters)
