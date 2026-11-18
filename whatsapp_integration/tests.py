import logging
from unittest import mock

from botocore.exceptions import ClientError
from django.test import SimpleTestCase, override_settings

from whatsapp_integration.v3 import tasks as wa_tasks
from whatsapp_integration.v3.tasks import (
    WHATSAPP_INVOICE_S3_PREFIX,
    run_whatsapp_invoice_pdf_cleanup,
)

BUCKET = "test-bucket"


def _client_error(code, op="Op"):
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class FakeS3:
    """Minimal in-memory S3 (versioned or not) for the cleanup task."""

    def __init__(self, versions=(), markers=(), objects=(), versioning="Enabled", mfa=None,
                 deny_versions=False, delete_errors=(), raise_on_delete=False, put_error=False):
        self.versions = list(versions)      # [(key, version_id)]
        self.markers = list(markers)        # [(key, version_id)]
        self.objects = list(objects)        # unversioned fallback listing
        self.versioning = versioning
        self.mfa = mfa
        self.deny_versions = deny_versions
        self.delete_errors = set(delete_errors)
        self.raise_on_delete = raise_on_delete
        self.put_error = put_error
        self.delete_calls = []
        self.put_calls = []

    def head_object(self, Bucket, Key):
        if any(k == Key for k, _ in self.versions) or Key in self.objects:
            return {"ContentLength": 0}
        raise _client_error("404", "HeadObject")

    def put_object(self, Bucket, Key, Body):
        self.put_calls.append((Key, Body))
        if self.put_error:
            raise _client_error("AccessDenied", "PutObject")
        if self.versioning == "Unversioned":
            self.objects.append(Key)
        else:
            self.versions.append((Key, f"put{len(self.put_calls)}"))

    def get_bucket_versioning(self, Bucket):
        resp = {}
        if self.versioning != "Unversioned":
            resp["Status"] = self.versioning
        if self.mfa:
            resp["MFADelete"] = self.mfa
        return resp

    def get_paginator(self, name):
        fake = self

        class Paginator:
            def paginate(self, Bucket, Prefix, PaginationConfig):
                size = PaginationConfig["PageSize"]
                if name == "list_object_versions":
                    if fake.deny_versions:
                        raise _client_error("AccessDenied", "ListObjectVersions")
                    entries = [("v", k, v) for k, v in fake.versions] + [("m", k, v) for k, v in fake.markers]
                    entries = [e for e in entries if e[1].startswith(Prefix)]
                    for i in range(0, max(len(entries), 1), size):
                        chunk = entries[i:i + size]
                        yield {
                            "Versions": [{"Key": k, "VersionId": v} for t, k, v in chunk if t == "v"],
                            "DeleteMarkers": [{"Key": k, "VersionId": v} for t, k, v in chunk if t == "m"],
                        }
                else:
                    keys = [k for k in fake.objects if k.startswith(Prefix)]
                    for i in range(0, max(len(keys), 1), size):
                        yield {"Contents": [{"Key": k} for k in keys[i:i + size]]}

        return Paginator()

    def delete_objects(self, Bucket, Delete):
        objs = Delete["Objects"]
        self.delete_calls.append(objs)
        if self.raise_on_delete:
            raise _client_error("InternalError", "DeleteObjects")
        deleted, errors = [], []
        for o in objs:
            ident = (o["Key"], o.get("VersionId"))
            if o["Key"] in self.delete_errors:
                errors.append({"Key": o["Key"], "VersionId": o.get("VersionId"),
                               "Code": "AccessDenied", "Message": "Object Lock"})
                continue
            if "VersionId" in o:
                if ident in self.versions:
                    self.versions.remove(ident)
                    deleted.append({"Key": o["Key"], "VersionId": o["VersionId"]})
                elif ident in self.markers:
                    self.markers.remove(ident)
                    deleted.append({"Key": o["Key"], "VersionId": o["VersionId"], "DeleteMarker": True,
                                    "DeleteMarkerVersionId": o["VersionId"]})
            elif self.versioning in ("Enabled", "Suspended"):
                # Plain delete on a versioned bucket = soft delete (marker added).
                deleted.append({"Key": o["Key"], "DeleteMarker": True, "DeleteMarkerVersionId": "new"})
            else:
                if o["Key"] in self.objects:
                    self.objects.remove(o["Key"])
                deleted.append({"Key": o["Key"]})
        return {"Deleted": deleted, "Errors": errors}


def run(fake, **kwargs):
    return run_whatsapp_invoice_pdf_cleanup(client=fake, bucket=BUCKET, **kwargs)


@override_settings(AWS_STORAGE_BUCKET_NAME=BUCKET)
class WhatsAppInvoicePdfCleanupTests(SimpleTestCase):

    def setUp(self):
        # The task logs to "api_requests", whose handlers forward errors to
        # Discord - keep the expected test errors out of real alert channels.
        quiet = logging.getLogger("whatsapp_invoice_cleanup_tests")
        quiet.propagate = False
        quiet.addHandler(logging.NullHandler())
        patcher = mock.patch.object(wa_tasks, "logger", quiet)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_versioned_bucket_deletes_every_version_and_marker_permanently(self):
        fake = FakeS3(
            versions=[(f"whatsapp-invoices/2026/10/{d:02d}/a{d}.pdf", f"v{d}") for d in range(1, 4)]
            + [("whatsapp-invoices/2026/10/01/a1.pdf", "old")],
            markers=[("whatsapp-invoices/2026/09/30/x.pdf", "m1")],
        )
        stats = run(fake)
        self.assertEqual(stats["versions_deleted"], 4)
        self.assertEqual(stats["delete_markers_deleted"], 1)
        self.assertEqual(stats["remaining_after_verify"], 0)
        self.assertEqual(stats["soft_deleted"], 0)
        for call in fake.delete_calls:
            for obj in call:
                self.assertIn("VersionId", obj)  # never a plain (soft) delete
        # Only the (re)created root folder marker is left - no PDF data.
        self.assertEqual(fake.versions, [(WHATSAPP_INVOICE_S3_PREFIX, "put1")])
        self.assertEqual(fake.markers, [])

    def test_batches_are_at_most_1000_across_pages(self):
        fake = FakeS3(versions=[(f"whatsapp-invoices/2026/10/10/{i}.pdf", "null") for i in range(2500)],
                      versioning="Unversioned")
        stats = run(fake)
        self.assertEqual(stats["versions_deleted"], 2500)
        self.assertEqual([len(c) for c in fake.delete_calls], [1000, 1000, 500])
        self.assertEqual(stats["remaining_after_verify"], 0)

    def test_only_pdfs_and_folder_markers_inside_prefix_are_deleted(self):
        fake = FakeS3(versions=[
            ("whatsapp-invoices/2026/10/10/a.pdf", "1"),
            ("whatsapp-invoices/2026/10/10/B.PDF", "2"),
            ("whatsapp-invoices/2026/10/10/", "3"),
            ("whatsapp-invoices/2026/10/10/notes.txt", "4"),
            ("pdf_reports/shop_1/x.pdf", "5"),           # outside the prefix
        ])
        stats = run(fake)
        deleted_keys = {o["Key"] for call in fake.delete_calls for o in call}
        self.assertEqual(deleted_keys, {
            "whatsapp-invoices/2026/10/10/a.pdf",
            "whatsapp-invoices/2026/10/10/B.PDF",
            "whatsapp-invoices/2026/10/10/",
        })
        self.assertEqual(stats["skipped_non_pdf"], 1)
        self.assertIn(("pdf_reports/shop_1/x.pdf", "5"), fake.versions)

    def test_dry_run_makes_no_delete_calls(self):
        fake = FakeS3(versions=[("whatsapp-invoices/2026/10/10/a.pdf", "1")],
                      markers=[("whatsapp-invoices/2026/10/09/b.pdf", "m")])
        stats = run(fake, dry_run=True)
        self.assertEqual(fake.delete_calls, [])
        self.assertEqual(stats["would_delete_versions"], 1)
        self.assertEqual(stats["would_delete_delete_markers"], 1)

    def test_versioned_bucket_without_version_listing_aborts(self):
        fake = FakeS3(versions=[("whatsapp-invoices/2026/10/10/a.pdf", "1")], deny_versions=True)
        stats = run(fake)
        self.assertIn("soft delete", stats["aborted"])
        self.assertEqual(fake.delete_calls, [])

    def test_unversioned_bucket_falls_back_to_plain_listing(self):
        fake = FakeS3(objects=["whatsapp-invoices/2026/10/10/a.pdf", "other/x.pdf"],
                      versioning="Unversioned", deny_versions=True)
        stats = run(fake)
        self.assertEqual(stats["versions_deleted"], 1)
        self.assertEqual(stats["remaining_after_verify"], 0)
        self.assertEqual(fake.objects, ["other/x.pdf", WHATSAPP_INVOICE_S3_PREFIX])

    def test_mfa_delete_aborts(self):
        fake = FakeS3(versions=[("whatsapp-invoices/2026/10/10/a.pdf", "1")], mfa="Enabled")
        stats = run(fake)
        self.assertIn("MFA", stats["aborted"])
        self.assertEqual(fake.delete_calls, [])

    def test_per_object_errors_are_counted_and_reported_as_remaining(self):
        fake = FakeS3(versions=[("whatsapp-invoices/2026/10/10/locked.pdf", "1"),
                                ("whatsapp-invoices/2026/10/10/ok.pdf", "2")],
                      delete_errors={"whatsapp-invoices/2026/10/10/locked.pdf"})
        stats = run(fake)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["versions_deleted"], 1)
        self.assertEqual(stats["remaining_after_verify"], 1)

    def test_batch_client_error_does_not_crash(self):
        fake = FakeS3(versions=[("whatsapp-invoices/2026/10/10/a.pdf", "1")], raise_on_delete=True)
        stats = run(fake)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["remaining_after_verify"], 1)

    def test_time_budget_stops_early_without_verify(self):
        fake = FakeS3(versions=[("whatsapp-invoices/2026/10/10/a.pdf", "1")])
        stats = run(fake, time_budget_seconds=-1)
        self.assertTrue(stats["stopped_early"])
        self.assertIsNone(stats["remaining_after_verify"])

    def test_empty_prefix_is_a_clean_noop(self):
        stats = run(FakeS3())
        self.assertEqual(stats["versions_deleted"], 0)
        self.assertEqual(stats["remaining_after_verify"], 0)
        self.assertIsNone(stats["aborted"])

    # --- Root folder preservation -----------------------------------------

    def test_root_marker_versions_and_markers_are_never_deleted(self):
        root = WHATSAPP_INVOICE_S3_PREFIX
        fake = FakeS3(
            versions=[(root, "r1"), (root, "r0"),
                      ("whatsapp-invoices/2026/10/", "d1"),
                      ("whatsapp-invoices/2026/10/10/a.pdf", "p1")],
            markers=[(root, "rm")],
        )
        stats = run(fake)
        deleted_keys = {o["Key"] for call in fake.delete_calls for o in call}
        self.assertNotIn(root, deleted_keys)
        self.assertEqual(deleted_keys, {"whatsapp-invoices/2026/10/", "whatsapp-invoices/2026/10/10/a.pdf"})
        self.assertEqual(stats["root_marker_entries_preserved"], 3)
        self.assertEqual(stats["root_folder"], "kept")
        self.assertTrue(stats["root_folder_present"])
        self.assertEqual(fake.put_calls, [])
        self.assertEqual(stats["remaining_after_verify"], 0)
        self.assertIn((root, "r1"), fake.versions)
        self.assertIn((root, "rm"), fake.markers)

    def test_missing_root_marker_is_created_once(self):
        fake = FakeS3(versions=[("whatsapp-invoices/2026/10/10/a.pdf", "1")])
        stats = run(fake)
        self.assertEqual(fake.put_calls, [(WHATSAPP_INVOICE_S3_PREFIX, b"")])
        self.assertEqual(stats["root_folder"], "created")
        self.assertTrue(stats["root_folder_present"])
        self.assertEqual(stats["remaining_after_verify"], 0)
        # Second run: marker now exists -> kept, no new put
        stats = run(fake)
        self.assertEqual(stats["root_folder"], "kept")
        self.assertEqual(len(fake.put_calls), 1)

    def test_dry_run_reports_would_create_without_writing(self):
        fake = FakeS3(versions=[("whatsapp-invoices/2026/10/10/a.pdf", "1")])
        stats = run(fake, dry_run=True)
        self.assertEqual(stats["root_folder"], "would create")
        self.assertEqual(fake.put_calls, [])
        self.assertEqual(fake.delete_calls, [])

    def test_root_marker_create_error_does_not_fail_cleanup(self):
        fake = FakeS3(versions=[("whatsapp-invoices/2026/10/10/a.pdf", "1")], put_error=True)
        stats = run(fake)
        self.assertEqual(stats["root_folder"], "error")
        self.assertFalse(stats["root_folder_present"])
        self.assertEqual(stats["versions_deleted"], 1)
        self.assertEqual(stats["remaining_after_verify"], 0)

    def test_unversioned_bucket_keeps_root_marker(self):
        root = WHATSAPP_INVOICE_S3_PREFIX
        fake = FakeS3(objects=[root, "whatsapp-invoices/2026/10/10/a.pdf"],
                      versioning="Unversioned", deny_versions=True)
        stats = run(fake)
        self.assertEqual(fake.objects, [root])
        self.assertEqual(stats["root_folder"], "kept")

    def test_beat_task_uses_same_logic_as_command(self):
        fake = FakeS3(versions=[(WHATSAPP_INVOICE_S3_PREFIX, "r"), ("whatsapp-invoices/2026/10/10/a.pdf", "1")])
        with mock.patch("services.s3_service.S3Service.get_client", return_value=fake):
            stats = wa_tasks.cleanup_whatsapp_invoice_pdfs.apply().get()
        self.assertEqual(stats["versions_deleted"], 1)
        self.assertEqual(stats["root_folder"], "kept")
        self.assertEqual(stats["root_marker_entries_preserved"], 1)
        self.assertIn((WHATSAPP_INVOICE_S3_PREFIX, "r"), fake.versions)

    @override_settings(AWS_S3_REGION_NAME="ap-south-1")
    def test_prefix_matches_upload_key(self):
        from whatsapp_integration.v3.services import WhatsAppService

        with mock.patch("boto3.client") as client_factory:
            url = WhatsAppService.__new__(WhatsAppService).upload_pdf_to_s3("JVBERi0=", "Inv.pdf")
        key = client_factory.return_value.put_object.call_args.kwargs["Key"]
        self.assertTrue(key.startswith(WHATSAPP_INVOICE_S3_PREFIX), key)
        self.assertTrue(wa_tasks._is_whatsapp_invoice_key(key))
        self.assertTrue(url.endswith(key))


class PersonMeasurementTableTests(SimpleTestCase):
    """Booking invoice measurement page: measurements as rows, up to 4 person columns per table."""

    PEOPLE = [
        {"name": "Person 1", "value": {"chest": "36", "armhole": "16", "upper_arm": "12", "shoulder": "15"}},
        {"name": "Person 2", "value": "{Chest: 38, Armhole: 17, Upper Arm: 13, Shoulder: 16}"},
        {"name": "Person 3", "value": {"chest": "40", "armhole": "18", "upper_arm": "14", "shoulder": "17"}},
        {"name": "Person 4", "value": {"chest": "42", "armhole": "19", "upper_arm": "15", "shoulder": "18"}},
        {"name": "Rahul", "value": {"chest": "44", "armhole": "20", "shoulder": "19"}},
        {"name": "", "value": {"chest": "46", "armhole": "21", "upper_arm": "17", "shoulder": "20"}},
    ]

    def tables(self, people):
        from whatsapp_integration.v3.pdf_template import BasePDFService
        return BasePDFService._person_measurement_tables(*BasePDFService._build_person_measurement_table(people))

    def test_splits_into_tables_of_four(self):
        self.assertEqual([len(names) for names, _ in self.tables(self.PEOPLE)], [4, 2])
        self.assertEqual([len(names) for names, _ in self.tables(self.PEOPLE[:4])], [4])
        self.assertEqual([len(names) for names, _ in self.tables(self.PEOPLE[:1])], [1])

    def test_values_under_correct_person(self):
        first, second = self.tables(self.PEOPLE)
        self.assertEqual(first, (
            ["Person 1", "Person 2", "Person 3", "Person 4"],
            [["Chest", "36", "38", "40", "42"], ["Armhole", "16", "17", "18", "19"],
             ["Upper Arm", "12", "13", "14", "15"], ["Shoulder", "15", "16", "17", "18"]],
        ))
        self.assertEqual(second, (
            ["Rahul", "Person 6"],
            [["Chest", "44", "46"], ["Armhole", "20", "21"], ["Upper Arm", "—", "17"], ["Shoulder", "19", "20"]],
        ))

    def test_rows_follow_entered_order(self):
        from booking_v2.utils.measurements import to_stored_measurements
        from whatsapp_integration.v3.pdf_template import BasePDFService

        entered = {"chest": "36", "sleeve_length": "24", "armhole": "16", "inseam": "30", "thigh": "22"}
        stored = to_stored_measurements({"Person 1": entered})
        # Simulate jsonb: object keys come back sorted by length, then alphabetically.
        stored[0]["value"] = dict(sorted(entered.items(), key=lambda kv: (len(kv[0]), kv[0])))
        columns, _ = BasePDFService._build_person_measurement_table(stored)
        self.assertEqual(columns, ["Chest", "Sleeve Length", "Armhole", "Inseam", "Thigh"])
        # Rows saved without "keys" keep the stored order, as before.
        legacy = [{"name": "Person 1", "value": stored[0]["value"]}]
        self.assertEqual(BasePDFService._build_person_measurement_table(legacy)[0][0], "Chest")
        self.assertEqual(BasePDFService._build_person_measurement_table(legacy)[0][1], "Thigh")

    def test_page_html(self):
        from whatsapp_integration.v3.pdf_template import BasePDFService
        columns, rows = BasePDFService._build_person_measurement_table(self.PEOPLE)
        page = BasePDFService._build_measurements_page_html([], person_sections=[("Sherwani - XL", columns, rows)])
        self.assertEqual(page.count('<table class="person-measurements-table"'), 2)
        self.assertEqual(page.count("Sherwani - XL"), 1)
        self.assertIn('<th class="measurement-name">Measurements</th>', page)
        self.assertIn('<th class="text-right">Rahul</th>', page)
        self.assertNotIn("S. No.", page)
        # Compact width: 150px label column + 120px per person, on card and table.
        self.assertEqual(page.count('style="width:630px;"'), 2)
        self.assertEqual(page.count('style="width:390px;"'), 2)
        self.assertEqual(page.count('<col style="width:150px;">'), 2)
        self.assertEqual(page.count('<col style="width:120px;">'), 6)
