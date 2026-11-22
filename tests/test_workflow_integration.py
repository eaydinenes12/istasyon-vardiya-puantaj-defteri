import hashlib
import base64
from io import BytesIO
import unittest
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from pypdf import PdfReader, PdfWriter
from PIL import Image

import output_tracking
import scheduling
from storage import Store
from tests import test_application


class WorkflowIntegrationTests(unittest.TestCase):
    setUp = test_application.ApplicationTests.setUp
    tearDown = test_application.ApplicationTests.tearDown
    request = test_application.ApplicationTests.request
    json_request = test_application.ApplicationTests.json_request
    add_person = test_application.ApplicationTests.add_person

    def approved_recurring_leave(self):
        person = self.add_person()
        status, response = self.json_request("/api/linked-leave/requests", "POST", {
            "employee_id": person["id"], "pattern": "weekdays",
            "start": "2026-11-02", "end": "2026-11-10", "weekdays": [0],
            "kind": "weekly_off", "note": "",
        })
        self.assertEqual(status, 201, response)
        item = response["request"]
        status, response = self.json_request(
            f"/api/linked-leave/requests/{item['id']}/approve", "POST",
            {"revision": item["revision"], "status": "approved"},
        )
        self.assertEqual(status, 200, response)
        return person

    def test_future_month_save_returns_auto_leave_and_day_exact_calendar(self):
        person = self.approved_recurring_leave()
        status, response = self.json_request("/api/schedule-months", "PUT", {
            "months": {
                "2026-11": {"revision": "", "entries": []},
                "2026-12": {"revision": "", "entries": []},
            },
        })
        self.assertEqual(status, 200, response)
        self.assertEqual(response["linked_leave_conflicts"], [])
        days = {
            day: item for row in response["entries"]["2026-11"]
            if row["employee_id"] == person["id"] for day, item in row["days"].items()
        }
        self.assertEqual(days, {
            "2026-11-02": {"kind": "weekly_off"}, "2026-11-09": {"kind": "weekly_off"},
        })
        status, calendar = self.json_request("/api/leave-requests?month=2026-11")
        self.assertEqual(status, 200, calendar)
        self.assertEqual(
            [row["date"] for row in calendar["days"] if row["count"]],
            ["2026-11-02", "2026-11-09"],
        )
        with Store(self.server.data_path) as store:
            with self.assertRaisesRegex(ValueError, "pasife"):
                store.delete_person(person["id"], force=True)
            self.assertEqual(len(store.people()), 1)

    def test_linked_audit_failure_rolls_back_every_month_and_application(self):
        self.approved_recurring_leave()
        with patch("linked_leave.record_applications_in_transaction", side_effect=ValueError("test rollback")):
            status, response = self.json_request("/api/schedule-months", "PUT", {
                "months": {
                    "2026-11": {"revision": "", "entries": []},
                    "2026-12": {"revision": "", "entries": []},
                },
            })
        self.assertEqual(status, 400, response)
        with Store(self.server.data_path) as store:
            self.assertEqual(store.schedule_month("2026-11")["revision"], "")
            self.assertEqual(store.schedule_month("2026-12")["revision"], "")
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM linked_leave_applications"
            ).fetchone()[0], 0)

    def test_excel_has_printed_identity_and_registry_detects_new_source_revision(self):
        person = self.add_person()
        status, saved = self.json_request("/api/schedule-months", "PUT", {
            "months": {"2026-11": {"revision": "", "entries": [{
                "row_id": "one", "employee_id": person["id"], "section": "MARKET", "slot": 1,
                "days": {"2026-11-02": {"kind": "work", "start": "08:00", "end": "16:00"}},
            }]}},
        })
        self.assertEqual(status, 200, saved)
        status, _, content = self.request("/api/export?kind=schedule&month=2026-11&week=0")
        self.assertEqual(status, 200, content[:300])
        workbook = load_workbook(BytesIO(content))
        code = workbook.properties.identifier
        self.assertTrue(code)
        self.assertIn(code, workbook.worksheets[0].oddFooter.center.text)
        self.assertEqual(len(workbook.worksheets), len(scheduling.week_starts("2026-11")))
        self.assertEqual(workbook.worksheets[0].max_row, 35)
        workbook.close()
        status, registry = self.json_request("/api/document-workflows/exports")
        self.assertEqual(status, 200, registry)
        record = next(item for item in registry["exports"] if item["id"] == code)
        self.assertTrue(record["current"])
        self.assertEqual(record["sha256"], hashlib.sha256(content).hexdigest())
        self.json_request("/api/schedule-months", "PUT", {"months": {
            "2026-11": {"revision": saved["revisions"]["2026-11"], "entries": []},
        }})
        _, registry = self.json_request("/api/document-workflows/exports")
        self.assertFalse(next(item for item in registry["exports"] if item["id"] == code)["current"])

    def test_jpeg_of_stale_or_unsaved_view_is_explicitly_a_draft(self):
        image = BytesIO()
        Image.new("RGB", (20, 10), "red").save(image, "JPEG")
        payload = {
            "kind": "attendance", "month": "2026-11", "draft": False, "sources": None,
            "content": base64.b64encode(image.getvalue()).decode("ascii"),
        }
        status, response = self.json_request("/api/view-output", "POST", payload)
        self.assertEqual(status, 200, response)
        with Image.open(BytesIO(base64.b64decode(response["content"]))) as output:
            self.assertEqual(output.height, 58)
            self.assertGreaterEqual(output.width, 640)
            self.assertGreater(output.getpixel((4, 4))[0], 200)
        _, registry = self.json_request("/api/document-workflows/exports")
        self.assertEqual(registry["exports"][0]["type"], "draft-jpeg")
        self.assertFalse(registry["exports"][0]["current"])
        payload["draft"] = 1
        self.assertEqual(self.json_request("/api/view-output", "POST", payload)[0], 400)

    def cross_month_undo_payload(self):
        person = self.add_person()
        _, response = self.json_request("/api/linked-leave/requests", "POST", {
            "employee_id": person["id"], "pattern": "range", "start": "2026-11-30",
            "end": "2026-12-01", "weekdays": [], "kind": "annual_leave", "note": "",
        })
        item = response["request"]
        _, approved = self.json_request(f"/api/linked-leave/requests/{item['id']}/approve", "POST", {
            "revision": item["revision"], "status": "approved",
        })
        status, saved = self.json_request("/api/schedule-months", "PUT", {
            "months": {month: {"revision": "", "entries": []} for month in ("2026-11", "2026-12")},
        })
        self.assertEqual(status, 200, saved)
        return {
            "request_id": item["id"], "request_revision": approved["request"]["revision"],
            "schedule_revisions": saved["revisions"], "confirmed": True,
        }

    def test_linked_leave_move_removes_old_and_applies_new_dates_together(self):
        approval = self.cross_month_undo_payload()
        move = {
            "request_id": approval["request_id"], "request_revision": approval["request_revision"],
            "start": "2026-12-02", "end": "2026-12-03",
        }
        status, preview = self.json_request("/api/linked-leave/move-preview", "POST", move)
        self.assertEqual(status, 200, preview)
        self.assertTrue(preview["can_apply"])
        self.assertEqual(preview["old_removals"], 2)
        self.assertEqual(preview["new_applications"], 2)
        self.assertEqual(
            [(item["month"], item["old_removals"],
              [application["date"] for application in item["new_applications"]])
             for item in preview["months"]],
            [("2026-11", ["2026-11-30"], []),
             ("2026-12", ["2026-12-01"], ["2026-12-02", "2026-12-03"])],
        )
        status, result = self.json_request("/api/linked-leave/move", "POST", {
            **move, "preview_hash": preview["preview_hash"], "confirmed": True,
        })
        self.assertEqual(status, 200, result)
        self.assertEqual((result["old_removals"], result["new_applications"]), (2, 2))
        with Store(self.server.data_path) as store:
            for month, expected_days in (
                ("2026-11", set()),
                ("2026-12", {"2026-12-02", "2026-12-03"}),
            ):
                entries = store.schedule_month(month)["entries"]
                actual = {day for row in entries for day in row["days"]}
                self.assertEqual(actual, expected_days)
            request = store.connection.execute(
                "SELECT start_date,end_date,status,revision FROM linked_leave_requests WHERE id=?",
                (approval["request_id"],),
            ).fetchone()
            self.assertEqual((request["start_date"], request["end_date"], request["status"], request["revision"]),
                             ("2026-12-02", "2026-12-03", "approved", approval["request_revision"] + 1))
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM linked_leave_applications WHERE request_id=? AND undone_at IS NULL",
                (approval["request_id"],),
            ).fetchone()[0], 2)
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM linked_leave_applications WHERE request_id=? AND undone_at IS NOT NULL",
                (approval["request_id"],),
            ).fetchone()[0], 2)

    def test_linked_leave_move_keeps_everything_on_lock_or_transaction_failure(self):
        approval = self.cross_month_undo_payload()
        move = {
            "request_id": approval["request_id"], "request_revision": approval["request_revision"],
            "start": "2026-12-02", "end": "2026-12-03",
        }
        with Store(self.server.data_path) as store:
            original_revisions = {
                month: store.schedule_month(month)["revision"] for month in ("2026-11", "2026-12")
            }
            store.set_period_locked("2026-12", True)
        status, locked = self.json_request("/api/linked-leave/move-preview", "POST", move)
        self.assertEqual(status, 200, locked)
        self.assertFalse(locked["can_apply"])
        self.assertTrue(locked["blocked"])
        status, rejected = self.json_request("/api/linked-leave/move", "POST", {
            **move, "preview_hash": locked["preview_hash"], "confirmed": True,
        })
        self.assertEqual(status, 400, rejected)
        with Store(self.server.data_path) as store:
            self.assertEqual(store.schedule_month("2026-11")["revision"], original_revisions["2026-11"])
            self.assertEqual(store.schedule_month("2026-12")["revision"], original_revisions["2026-12"])
            self.assertEqual(store.connection.execute(
                "SELECT start_date,end_date,revision FROM linked_leave_requests WHERE id=?",
                (approval["request_id"],),
            ).fetchone()[:], ("2026-11-30", "2026-12-01", approval["request_revision"]))
            store.set_period_locked("2026-12", False)
        status, fresh = self.json_request("/api/linked-leave/move-preview", "POST", move)
        self.assertEqual(status, 200, fresh)
        original = Store.record_change

        def fail_final_audit(store, action, *args, **kwargs):
            if action == "linked_leave.move":
                raise ValueError("test rollback")
            return original(store, action, *args, **kwargs)

        with patch.object(Store, "record_change", fail_final_audit):
            status, failed = self.json_request("/api/linked-leave/move", "POST", {
                **move, "preview_hash": fresh["preview_hash"], "confirmed": True,
            })
        self.assertEqual(status, 400, failed)
        with Store(self.server.data_path) as store:
            for month, revision in original_revisions.items():
                self.assertEqual(store.schedule_month(month)["revision"], revision)
            request = store.connection.execute(
                "SELECT start_date,end_date,status,revision FROM linked_leave_requests WHERE id=?",
                (approval["request_id"],),
            ).fetchone()
            self.assertEqual(tuple(request), (
                "2026-11-30", "2026-12-01", "approved", approval["request_revision"],
            ))
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM linked_leave_applications WHERE request_id=? AND undone_at IS NULL",
                (approval["request_id"],),
            ).fetchone()[0], 2)
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM linked_leave_applications WHERE request_id=? AND undone_at IS NOT NULL",
                (approval["request_id"],),
            ).fetchone()[0], 0)

    def test_request_undo_reverses_approval_and_all_months_together(self):
        payload = self.cross_month_undo_payload()
        status, response = self.json_request("/api/linked-leave/undo-request", "POST", payload)
        self.assertEqual(status, 200, response)
        self.assertEqual(response["undone"], 2)
        with Store(self.server.data_path) as store:
            for month in ("2026-11", "2026-12"):
                self.assertFalse(any(row["days"] for row in store.schedule_month(month)["entries"]))
            request = store.connection.execute(
                "SELECT status,revision FROM linked_leave_requests WHERE id=?", (payload["request_id"],),
            ).fetchone()
            self.assertEqual(request["status"], "pending")
            self.assertEqual(request["revision"], payload["request_revision"] + 1)

    def test_request_undo_failure_rolls_back_approval_every_month_and_markers(self):
        payload = self.cross_month_undo_payload()
        original = Store.record_change

        def fail_final_audit(store, action, *args, **kwargs):
            if action == "linked_leave.undo_request":
                raise ValueError("test transaction failure")
            return original(store, action, *args, **kwargs)

        with patch.object(Store, "record_change", fail_final_audit):
            status, response = self.json_request("/api/linked-leave/undo-request", "POST", payload)
        self.assertEqual(status, 400, response)
        with Store(self.server.data_path) as store:
            for month, revision in payload["schedule_revisions"].items():
                self.assertEqual(store.schedule_month(month)["revision"], revision)
                self.assertEqual(sum(len(row["days"]) for row in store.schedule_month(month)["entries"]), 1)
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM linked_leave_applications WHERE undone_at IS NULL"
            ).fetchone()[0], 2)
            self.assertEqual(store.connection.execute(
                "SELECT status FROM linked_leave_requests WHERE id=?", (payload["request_id"],),
            ).fetchone()[0], "approved")

    def test_request_undo_preserves_all_months_on_lock_or_reassigned_employee(self):
        payload = self.cross_month_undo_payload()
        with Store(self.server.data_path) as store:
            store.set_period_locked("2026-12", True)
        self.assertEqual(self.json_request("/api/linked-leave/undo-request", "POST", payload)[0], 400)
        other = self.add_person("Başka Personel")
        with Store(self.server.data_path) as store:
            self.assertEqual(store.schedule_month("2026-11")["revision"], payload["schedule_revisions"]["2026-11"])
            store.set_period_locked("2026-12", False)
            december = store.schedule_month("2026-12")
            december["entries"][0]["employee_id"] = other["id"]
            payload["schedule_revisions"]["2026-12"] = store.save_schedule_month(
                "2026-12", december["entries"], december["revision"],
            )
        self.assertEqual(self.json_request("/api/linked-leave/undo-request", "POST", payload)[0], 400)
        with Store(self.server.data_path) as store:
            self.assertEqual(store.schedule_month("2026-11")["revision"], payload["schedule_revisions"]["2026-11"])
            self.assertEqual(store.schedule_month("2026-12")["entries"][0]["employee_id"], other["id"])
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM linked_leave_applications WHERE undone_at IS NULL"
            ).fetchone()[0], 2)


class OutputIdentityTests(unittest.TestCase):
    identity = {
        "id": "ABCDEF0123456789", "created_at": "2026-11-02T10:20:30+00:00",
        "app_version": "3.2.0", "source_code": "SOURCE123456",
    }

    def test_workbook_footer_preserves_rows_merges_and_existing_footer(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet["B35"] = "İSTASYON MÜDÜRÜ"
        sheet.merge_cells("B35:E35")
        sheet.oddFooter.center.text = "Mevcut not"
        original = BytesIO()
        workbook.save(original)
        content = output_tracking.stamp_output(original.getvalue(), "xlsx", self.identity)
        result = load_workbook(BytesIO(content))
        self.assertEqual(result.active.max_row, 35)
        self.assertEqual(result.active["B35"].value, "İSTASYON MÜDÜRÜ")
        self.assertEqual(str(result.active.merged_cells), "B35:E35")
        self.assertIn("Mevcut not", result.active.oddFooter.center.text)
        self.assertIn(self.identity["id"], result.active.oddFooter.center.text)
        result.close()

    def test_pdf_identity_does_not_remove_pages_or_cover_original_area(self):
        writer = PdfWriter()
        writer.add_blank_page(width=400, height=600)
        writer.add_blank_page(width=600, height=400)
        source = BytesIO()
        writer.write(source)
        result = PdfReader(BytesIO(output_tracking.stamp_output(
            source.getvalue(), "pdf", self.identity,
        )))
        self.assertEqual(len(result.pages), 2)
        self.assertEqual(result.metadata.title, self.identity["id"])
        self.assertEqual(float(result.pages[0].mediabox.height), 624)
        self.assertEqual(float(result.pages[1].mediabox.width), 600)
