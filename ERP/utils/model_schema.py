"""
Shared builder for the auto-generated Django model schema.

Used by both `manage.py generate_model_schema` (writes a JSON file) and the
admin-only `/api/internal/model-schema/` endpoint (returns the same shape
live) - keeping exactly one introspection implementation so the file and the
endpoint can never drift out of sync.

See `ERP/management/commands/generate_model_schema.py` for the full
documentation of what this produces and why.
"""
from django.apps import apps
from django.db.models.fields import NOT_PROVIDED
from django.utils.timezone import now as _now


def _serialize_default(field):
    """Stringify a field's default, skipping Django's "no default" sentinel
    and safely handling callables (e.g. `timezone.now`) by naming them
    rather than invoking them."""
    default = getattr(field, "default", NOT_PROVIDED)
    if default is NOT_PROVIDED:
        return None
    if callable(default):
        return getattr(default, "__name__", str(default))
    return str(default)


def _build_plain_field(field):
    data = {
        "name": field.name,
        "type": field.get_internal_type() if hasattr(field, "get_internal_type") else field.__class__.__name__,
    }
    if hasattr(field, "null"):
        data["null"] = field.null
    if hasattr(field, "blank"):
        data["blank"] = field.blank
    if hasattr(field, "primary_key"):
        data["primary_key"] = field.primary_key
    if hasattr(field, "unique"):
        data["unique"] = field.unique
    if hasattr(field, "db_index"):
        data["db_index"] = field.db_index
    if getattr(field, "max_length", None) is not None:
        data["max_length"] = field.max_length
    data["default"] = _serialize_default(field)
    return data


def _build_forward_relationship(field):
    """A relation field actually declared (and stored as a column, for
    ForeignKey/OneToOneField - M2M has its own through-table) on this
    model - i.e. `field.concrete` is True."""
    remote_field = getattr(field, "remote_field", None)
    on_delete = getattr(remote_field, "on_delete", None)
    return {
        "name": field.name,
        "type": field.__class__.__name__,
        "related_model": field.related_model._meta.label if field.related_model else None,
        "on_delete": on_delete.__name__ if on_delete is not None else None,
        "related_name": getattr(remote_field, "related_name", None),
        "null": getattr(field, "null", None),
    }


def _build_reverse_relationship(field):
    """Django's auto-created reverse accessor (e.g. `Shop.bookings` for
    `BookingV2.shop`) - `field.concrete` is False here; the relation is
    declared on the OTHER model, not this one."""
    accessor_name = field.get_accessor_name() if hasattr(field, "get_accessor_name") else None
    return {
        "name": accessor_name or field.name,
        "type": field.__class__.__name__,
        "source_model": field.related_model._meta.label if field.related_model else None,
    }


def build_model_schema():
    """
    Walk every registered Django model and return a dict describing every
    app's models: their plain fields, forward relationships (FK/O2O/M2M
    declared on the model itself), and reverse relationships (auto-created
    accessors from other models' FKs pointing at this one).

    Returns:
        {
          "generated_at": "<iso8601 timestamp>",
          "apps": {
            "<app_label>": {
              "app_label": "<app_label>",
              "models": {
                "<ModelName>": {
                  "model": "<ModelName>",
                  "label": "<app_label>.<ModelName>",
                  "table": "<db_table>",
                  "fields": [...],
                  "relationships": [...],
                  "reverse_relationships": [...]
                }
              }
            }
          }
        }
    """
    result = {"generated_at": _now().isoformat(), "apps": {}}

    for model in apps.get_models():
        app_label = model._meta.app_label
        app_bucket = result["apps"].setdefault(app_label, {"app_label": app_label, "models": {}})

        model_entry = {
            "model": model.__name__,
            "label": model._meta.label,
            "table": model._meta.db_table,
            "fields": [],
            "relationships": [],
            "reverse_relationships": [],
        }

        for field in model._meta.get_fields():
            if not field.is_relation:
                model_entry["fields"].append(_build_plain_field(field))
            elif field.concrete:
                model_entry["relationships"].append(_build_forward_relationship(field))
            else:
                model_entry["reverse_relationships"].append(_build_reverse_relationship(field))

        app_bucket["models"][model.__name__] = model_entry

    return result
