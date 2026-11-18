"""
Management command: generate_model_schema
===========================================

PURPOSE
-------
Generates a complete, structured JSON document of every Django model
registered in this project: its app, its plain fields (with key properties
like null/blank/primary_key/unique/db_index/max_length/default), its forward
relationships (ForeignKey/OneToOneField/ManyToManyField declared on the
model itself), and its reverse relationships (the accessors Django
auto-creates on the OTHER side of a FK/M2M, e.g. `Shop.bookings` for
`BookingV2.shop`).

This is the model/database-layer equivalent of what drf-spectacular's
Swagger/OpenAPI schema already provides for the HTTP API layer - Swagger
documents request/response payload shapes, this documents the underlying
table/field/relationship structure those payloads are backed by. Neither
replaces the other.

DYNAMIC MODEL DISCOVERY
------------------------
Nothing here hardcodes a list of apps or models. The command (via
`ERP.utils.model_schema.build_model_schema()`) walks Django's live model
registry at runtime:

    django.apps.apps.get_models()
        -> model._meta.get_fields()
            -> classified into fields / relationships / reverse_relationships

So when a model or field is added, removed, or changed anywhere in the
project, the NEXT run of this command reflects that automatically - there is
no separate list of models/fields to keep in sync by hand.

OUTPUT FORMAT
-------------
A single JSON file shaped as:

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
              "fields": [
                {"name", "type", "null", "blank", "primary_key", "unique",
                 "db_index", "max_length", "default"}
              ],
              "relationships": [
                {"name", "type", "related_model", "on_delete", "related_name", "null"}
              ],
              "reverse_relationships": [
                {"name", "type", "source_model"}
              ]
            }
          }
        }
      }
    }

The `relationships` vs `reverse_relationships` split matters: a forward
relationship (`field.concrete is True`) is an actual column on this model's
table (e.g. `BookingV2.staff`, a real FK column with an `on_delete`); a
reverse relationship (`field.concrete is False`) is Django's auto-generated
accessor on the model being pointed AT (e.g. `Shop.bookings`), which has no
column of its own and no `on_delete` - conflating the two would misrepresent
which side of the relationship actually owns the foreign key.

This command is read-only against the database - it never writes rows, only
introspects model metadata (`_meta`). It contains no customer/shop row data,
no credentials, no secrets - only field names, types, and relationship
structure.

USAGE
-----
    # Write docs/model_schema.json (default location):
    python manage.py generate_model_schema

    # Write to a custom path instead:
    python manage.py generate_model_schema --output docs/custom_schema.json

WHEN TO RUN
-----------
Ad hoc, any time after a model change, to refresh docs/model_schema.json.
Safe to re-run at any time - the output is fully deterministic from the
current model registry, with no side effects on the database.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from ERP.utils.model_schema import build_model_schema

DEFAULT_OUTPUT_PATH = "docs/model_schema.json"


class Command(BaseCommand):
    help = (
        "Generate a JSON document of every Django model's fields and "
        "relationships (forward and reverse), discovered dynamically from "
        "the live model registry. See module docstring for full details."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            default=DEFAULT_OUTPUT_PATH,
            help=f"Destination file path (default: {DEFAULT_OUTPUT_PATH}).",
        )

    def handle(self, *args, **options):
        output_path = Path(options["output"])
        output_path.parent.mkdir(parents=True, exist_ok=True)

        schema = build_model_schema()
        app_count = len(schema["apps"])
        model_count = sum(len(app["models"]) for app in schema["apps"].values())

        with output_path.open("w", encoding="utf-8") as f:
            json.dump(schema, f, indent=2, default=str)

        self.stdout.write(
            self.style.SUCCESS(
                f"Model schema generated successfully: {output_path} "
                f"({app_count} app(s), {model_count} model(s))."
            )
        )
