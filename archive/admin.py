from django.contrib import admin

<<<<<<< HEAD
from .models import ArchiveBatch, ArchiveRun


=======
from .models import ArchiveBatch, ArchiveRun, ImportBatch, ImportTableLog


# ---------------- live app ----------------
>>>>>>> upstream/dev-2.0
@admin.register(ArchiveRun)
class ArchiveRunAdmin(admin.ModelAdmin):
    list_display = ("id", "cutoff", "status", "dry_run", "created_at", "finished_at")
    list_filter = ("status", "dry_run")
    readonly_fields = [f.name for f in ArchiveRun._meta.fields]


@admin.register(ArchiveBatch)
class ArchiveBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "shop_id", "kind", "status", "created_at", "purged_at")
    list_filter = ("status", "kind", "shop_id")
    search_fields = ("s3_prefix", "error")
<<<<<<< HEAD
    readonly_fields = [f.name for f in ArchiveBatch._meta.fields]
=======
    readonly_fields = [f.name for f in ArchiveBatch._meta.fields]


# ---------------- analytics app ----------------
class ImportTableLogInline(admin.TabularInline):
    model = ImportTableLog
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in ImportTableLog._meta.fields]


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "run_id", "batch_id", "shop_id", "kind", "status", "data_from", "data_to",
                    "total_rows", "finished_at")
    list_filter = ("status", "kind", "shop_id")
    search_fields = ("s3_prefix", "error")
    readonly_fields = [f.name for f in ImportBatch._meta.fields]
    inlines = [ImportTableLogInline]


@admin.register(ImportTableLog)
class ImportTableLogAdmin(admin.ModelAdmin):
    list_display = ("id", "import_batch", "table_name", "rows_in_file", "rows_inserted", "rows_updated",
                    "date_from", "date_to", "total_amount", "checksum_ok")
    list_filter = ("table_name", "checksum_ok")
    readonly_fields = [f.name for f in ImportTableLog._meta.fields]
>>>>>>> upstream/dev-2.0
