from django.core.management.base import BaseCommand

from invoice_templates.v3.services.catalog import sync_catalog


class Command(BaseCommand):
    help = (
        "Create/update InvoiceTemplate catalog rows from the code registry "
        "(invoice_templates/v3/registry). Idempotent; never touches shop settings "
        "and never re-activates a template an admin deactivated."
    )

    def handle(self, *args, **options):
        created, updated = sync_catalog()
        self.stdout.write(self.style.SUCCESS(
            f"Invoice templates synced: {len(created)} created {created or ''}, "
            f"{len(updated)} updated {updated or ''}."
        ))
