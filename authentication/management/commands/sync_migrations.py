"""
Self-healing migration bootstrap.

Migration files (*.py under */migrations/, except __init__.py) are
intentionally gitignored in this project and never committed. That means
every fresh clone, new EC2 instance, or `git clean` wipes them out locally,
while the shared RDS database's `django_migrations` bookkeeping table still
records old ones as applied. Without this command, that mismatch reproduces
the `NodeNotFoundError` / "Dependency on app with no migrations" errors
every single time.

Why this doesn't just call `migrate --fake`: Django's `migrate` and
`makemigrations` commands both call `check_consistent_history()` at the very
start, before touching anything. If a dependency (e.g. admin.0001_initial,
which depends on this app's user model migration) is already applied but
this app's matching row is missing, that check fails immediately — before
`--fake` ever gets a chance to insert the row. So the row has to be written
directly, with a timestamp older than everything that depends on it, and
only afterwards is the DB state consistent enough for normal Django commands
to run.

This command, for each app in SYNC_MIGRATIONS_APPS:
  1. Regenerates any missing migration file(s) locally from models.py
     (only if the app currently has zero migration files on disk at all).
  2. For any migration file on disk with no matching django_migrations row,
     inserts that row directly via SQL, backdated before every other
     recorded `applied` timestamp in the table.

This never runs CREATE TABLE / ALTER / INSERT / UPDATE / DELETE against any
real application table — only against django_migrations, Django's own
bookkeeping table. No existing data or schema is touched.

Usage (run once per environment right after cloning/pulling, before
runserver/migrate):

    python manage.py sync_migrations

It is idempotent — safe to run every time, on every machine.
"""

from datetime import timedelta
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.exceptions import InconsistentMigrationHistory


# Apps whose local migration files are gitignored and may be missing.
# Keep this list in sync with any app that historically lost migration files.
SYNC_MIGRATIONS_APPS = getattr(
    settings,
    "SYNC_MIGRATIONS_APPS",
    [
        "authentication",
        "marketplace",
        "booking_v2",
        "expenses_v2",
        "finance",
        "notifications",
        "pdf_service",
        "purchase",
        "sales",
        "services",
        "shop",
        "subscriptions",
        "tailored",
        "whatsapp_integration",
    ],
)


class Command(BaseCommand):
    help = "Regenerate any missing (gitignored) migration files and record them as applied to match the existing DB, without touching any real table's schema or data."

    def handle(self, *args, **options):
        for app_label in SYNC_MIGRATIONS_APPS:
            if app_label not in apps.app_configs:
                self.stdout.write(self.style.WARNING(f"[sync_migrations] app '{app_label}' not installed, skipping"))
                continue
            self._sync_app(app_label)
        self.stdout.write(self.style.SUCCESS("[sync_migrations] done"))

    def _sync_app(self, app_label):
        app_config = apps.get_app_config(app_label)
        migrations_dir = Path(app_config.path) / "migrations"

        disk_names = self._disk_migration_names(migrations_dir)
        applied = self._applied_migration_names(app_label)

        if not disk_names:
            # No local files at all — regenerate from current models.py.
            try:
                call_command("makemigrations", app_label, interactive=False, verbosity=1)
            except InconsistentMigrationHistory as exc:
                self.stdout.write(self.style.ERROR(
                    f"[sync_migrations] {app_label}: can't makemigrations yet ({exc}). "
                    f"Another app's history is still inconsistent — fix that app first, then re-run."
                ))
                return
            disk_names = self._disk_migration_names(migrations_dir)

        missing = disk_names - applied

        if not missing:
            self.stdout.write(f"[sync_migrations] {app_label}: already in sync")
            return

        if applied:
            # This app already has history in the DB — the schema already
            # exists. Record the missing file(s) as applied directly,
            # backdated so dependents (admin, sessions, etc.) that were
            # applied long ago don't look "applied before their dependency".
            # This writes only to django_migrations — no real table is
            # touched.
            self._insert_backdated(app_label, missing)
            self.stdout.write(self.style.SUCCESS(
                f"[sync_migrations] {app_label}: recorded {sorted(missing)} as applied (bookkeeping only, no schema/data touched)"
            ))
        else:
            # Genuinely no prior history for this app in this DB — a real
            # migrate is correct and expected here.
            call_command("migrate", app_label, interactive=False, verbosity=1)
            self.stdout.write(self.style.SUCCESS(f"[sync_migrations] {app_label}: migrated for the first time"))

    @staticmethod
    def _disk_migration_names(migrations_dir):
        return {p.stem for p in migrations_dir.glob("*.py") if p.stem != "__init__"}

    @staticmethod
    def _applied_migration_names(app_label):
        with connection.cursor() as cursor:
            cursor.execute("SELECT name FROM django_migrations WHERE app = %s", [app_label])
            return {row[0] for row in cursor.fetchall()}

    @staticmethod
    def _insert_backdated(app_label, migration_names):
        with connection.cursor() as cursor:
            cursor.execute("SELECT MIN(applied) FROM django_migrations")
            (min_applied,) = cursor.fetchone()
            new_applied = (min_applied - timedelta(days=1)) if min_applied else None
            for name in sorted(migration_names):
                cursor.execute(
                    """
                    INSERT INTO django_migrations (app, name, applied)
                    SELECT %s, %s, COALESCE(%s, NOW())
                    WHERE NOT EXISTS (
                        SELECT 1 FROM django_migrations WHERE app = %s AND name = %s
                    )
                    """,
                    [app_label, name, new_applied, app_label, name],
                )
