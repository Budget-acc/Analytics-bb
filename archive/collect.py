"""
Step 1 and 2 of a batch: pick the main records, then collect every row that
belongs to them (moved) and every row they point to (copy only).
"""

from collections import OrderedDict, defaultdict

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db.models import F, IntegerField, Max, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.db.models import OuterRef, Subquery

from . import config

_TABLE_MODEL_CACHE = {}


def model_for_table(db_table):
    if not _TABLE_MODEL_CACHE:
        for model in apps.get_models():
            _TABLE_MODEL_CACHE[model._meta.db_table] = model
    try:
        return _TABLE_MODEL_CACHE[db_table]
    except KeyError:
        raise LookupError(f"No Django model for table {db_table}")


def all_rows(model):
    """Queryset that includes soft-deleted rows (safedelete) as well."""
    manager = getattr(model, "all_objects", None) or model._base_manager
    return manager.all()


# --------------------------------------------------------------------------- #
# Which main records are old enough
# --------------------------------------------------------------------------- #
def eligible_queryset(kind, shop_id, cutoff):
    cutoff_date = timezone.localtime(cutoff).date()
    model = model_for_table(config.MAIN_TABLE[kind])
    qs = all_rows(model).filter(shop_id=shop_id)

    if kind == "booking":
        return qs.filter(
            booking_status="completed",
            completed_at__isnull=False,
            completed_at__lt=cutoff,
        )

    if kind == "sale":
        # Fully paid, sale date before cutoff, and the LAST payment before cutoff too.
        return (
            qs.filter(sale_date__lt=cutoff_date)
            .annotate(
                archive_paid=Coalesce(Sum("payments__payment_amount"), Value(0), output_field=IntegerField()),
                archive_last_payment=Max("payments__created_at"),
            )
            .filter(archive_paid__gte=F("total_amount") - F("discount"))
            .filter(Q(archive_last_payment__isnull=True) | Q(archive_last_payment__lt=cutoff))
        )

    date_rule = Q(expense_date_time__lt=cutoff) | Q(expense_date_time__isnull=True, date__lt=cutoff_date)

    if kind == "expense":
        return qs.filter(date_rule)

    if kind == "product_expense":
        # Only product expenses NOT linked to a general expense; linked ones move with their expense.
        return qs.filter(date_rule, linked_expense__isnull=True)

    if kind == "daily_balance":
        # Keep the LAST row before the cutoff for each account: it carries the balance
        # forward (day book opening, account transfer check, update_daily_balance chain).
        last_row = (
            model.objects.filter(
                shop_id=shop_id,
                account_id=OuterRef("account_id"),
                date__lt=cutoff_date,
                deleted__isnull=True,
            )
            .order_by("-date", "-id")
            .values("id")[:1]
        )
        return qs.filter(date__lt=cutoff_date).exclude(id=Subquery(last_row))

    raise ValueError(f"Unknown kind {kind}")


def pick_ids(kind, shop_id, cutoff, after_id, size=None):
    """Next `size` eligible IDs after `after_id`, in ID order."""
    size = size or config.BATCH_SIZE
    return list(
        eligible_queryset(kind, shop_id, cutoff)
        .filter(id__gt=after_id)
        .order_by("id")
        .values_list("id", flat=True)[:size]
    )


# --------------------------------------------------------------------------- #
# Everything that belongs to a batch
# --------------------------------------------------------------------------- #
class Bundle:
    """Rows of one batch. `moved` rows are deleted from live, `refs` rows are only copied."""

    def __init__(self):
        self.moved = OrderedDict()  # {model: {pk: obj}}
        self.refs = OrderedDict()   # {model: {pk: obj}}

    @staticmethod
    def _key(obj):
        return obj._meta.concrete_model

    def add_moved(self, objs):
        for obj in objs:
            self.moved.setdefault(self._key(obj), {})[obj.pk] = obj

    def add_refs(self, objs):
        for obj in objs:
            model = self._key(obj)
            if obj.pk in self.moved.get(model, {}):
                continue
            self.refs.setdefault(model, {})[obj.pk] = obj

    def has(self, model, pk):
        return pk in self.moved.get(model, {}) or pk in self.refs.get(model, {})

    def moved_ids(self):
        return {m._meta.db_table: sorted(rows) for m, rows in self.moved.items()}

    def all_models(self):
        return set(self.moved) | set(self.refs)

    def rows_for(self, model):
        rows = dict(self.refs.get(model, {}))
        rows.update(self.moved.get(model, {}))
        return [rows[pk] for pk in sorted(rows)]

    def counts(self):
        out = {}
        for model in self.all_models():
            out[model._meta.db_table] = {
                "rows": len(self.rows_for(model)),
                "moved": len(self.moved.get(model, {})),
            }
        return out


def collect(kind, shop_id, ids):
    bundle = Bundle()

    main_model = model_for_table(config.MAIN_TABLE[kind])
    bundle.add_moved(all_rows(main_model).filter(id__in=ids))

    for table, column in config.CHILD_TABLES[kind]:
        child = model_for_table(table)
        bundle.add_moved(all_rows(child).filter(**{f"{column}__in": ids}))

    if kind != config.DAILY_BALANCE_KIND:
        _collect_ledger_rows(bundle, shop_id)
        _collect_activity_logs(bundle, shop_id)

    _collect_references(bundle)
    return bundle


def _collect_ledger_rows(bundle, shop_id):
    ledger = model_for_table("finance_ledgerentry")
    rows = []
    for model, objs in list(bundle.moved.items()):
        ref_type = config.LEDGER_REFERENCE_TYPE.get(model._meta.db_table)
        if not ref_type or not objs:
            continue
        rows.extend(
            all_rows(ledger).filter(shop_id=shop_id, reference_type=ref_type, reference_id__in=list(objs))
        )
    bundle.add_moved(rows)


def _collect_activity_logs(bundle, shop_id):
    log_model = model_for_table("shop_shopactivitylog")
    rows = []
    for model, objs in list(bundle.moved.items()):
        if model is log_model or not objs:
            continue
        ct = ContentType.objects.get_for_model(model, for_concrete_model=True)
        rows.extend(all_rows(log_model).filter(shop_id=shop_id, content_type=ct, object_id__in=list(objs)))
    bundle.add_moved(rows)


def _forward_fks(model):
    for field in model._meta.concrete_fields:
        if field.is_relation and (field.many_to_one or field.one_to_one):
            yield field


def _collect_references(bundle):
    """Follow every foreign key of the collected rows and copy what they point to."""
    pending = defaultdict(set)  # {(model, target_attname): {values}}

    def scan(objs):
        for obj in objs:
            for field in _forward_fks(obj._meta.concrete_model):
                target = field.related_model._meta.concrete_model
                if target._meta.label_lower in config.SKIP_REFERENCE_MODELS:
                    continue
                value = getattr(obj, field.attname)
                if value is None:
                    continue
                target_attname = field.target_field.attname
                if target_attname == target._meta.pk.attname and bundle.has(target, value):
                    continue
                pending[(target, target_attname)].add(value)

    for objs in list(bundle.moved.values()):
        scan(objs.values())

    while pending:
        (model, attname), values = pending.popitem()
        objs = list(all_rows(model).filter(**{f"{attname}__in": list(values)}))
        new = [o for o in objs if not bundle.has(model, o.pk)]
        bundle.add_refs(new)
        scan(new)