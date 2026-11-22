import copy
import http.client
import json
import tempfile
import threading
from types import SimpleNamespace
import unittest
from pathlib import Path

import operations
import scheduling
import schedule_protection
import ui_settings
import usability
from storage import Store
from server import create_server


class UsabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.sqlite3")
        self.person = self.store.add_person("Deneme Personel")
        self.month = "2026-11"

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def schedule_payload(self):
        return {
            "month": self.month, "draft_revision": "",
            "revisions": usability._revisions(self.store, "schedule", self.month),
            "snapshot": {"entries": [{
                "row_id": "test", "employee_id": self.person["id"], "section": "MARKET", "slot": 1,
                "days": {"2026-11-02": {"kind": "work", "start": "08:00", "end": "16:00"}},
            }], "neighborEntries": {"2026-10": [], "2026-12": []}},
        }

    def test_anonymous_owner_cannot_collide_with_a_named_local_account(self):
        anonymous = usability._owner(SimpleNamespace(current_user=None))
        named = usability._owner(SimpleNamespace(current_user={"username": "LOCAL"}))
        self.assertNotEqual(anonymous, named)
        self.assertEqual(named, usability._owner(SimpleNamespace(current_user={"username": "local"})))

    def test_draft_persists_without_saving_plan_and_is_scoped_to_account(self):
        payload = self.schedule_payload()
        result = usability.save_draft(self.store, "owner1", "schedule", self.month, payload)
        self.assertEqual(self.store.schedule_month(self.month)["entries"], [])
        self.assertIsNone(usability.draft_record(self.store, "owner2", "schedule", self.month))
        with Store(self.store.path) as reopened:
            record = usability.draft_record(reopened, "owner1", "schedule", self.month)
        self.assertFalse(record["stale"])
        self.assertEqual(record["draft_revision"], result["draft_revision"])
        with self.assertRaisesRegex(ValueError, "başka pencerede"):
            usability.save_draft(self.store, "owner1", "schedule", self.month, payload)
        with self.assertRaises(ValueError):
            usability.discard_draft(self.store, "owner1", "schedule", self.month, "")
        usability.discard_draft(self.store, "owner1", "schedule", self.month, result["draft_revision"])
        self.assertIsNone(usability.draft_record(self.store, "owner1", "schedule", self.month))

    def test_neighbor_source_change_blocks_overwrite_and_marks_recovery_stale(self):
        payload = self.schedule_payload()
        result = usability.save_draft(self.store, "owner1", "schedule", self.month, payload)
        self.store.save_schedule_month("2026-10", [], "")
        self.assertTrue(usability.draft_record(self.store, "owner1", "schedule", self.month)["stale"])
        payload["draft_revision"] = result["draft_revision"]
        with self.assertRaisesRegex(ValueError, "Kayıtlı ay değişti"):
            usability.save_draft(self.store, "owner1", "schedule", self.month, payload)

    def test_payroll_revocation_hides_prior_draft_amounts_and_rejects_new_amounts(self):
        payload = {"month": self.month, "draft_revision": "",
                   "revisions": usability._revisions(self.store, "attendance", self.month),
                   "snapshot": {"overrides": {}, "deductions": {str(self.person["id"]): 25}, "overtime": {}}}
        usability.save_draft(self.store, "owner1", "attendance", self.month, payload)
        hidden = usability.draft_record(self.store, "owner1", "attendance", self.month, payroll=False)
        self.assertEqual(hidden["snapshot"]["deductions"], {})
        with self.assertRaises(PermissionError):
            usability.save_draft(self.store, "owner2", "attendance", self.month, payload, payroll=False)
        self.store.save_schedule_month(self.month, [], "")
        self.assertTrue(usability.draft_record(self.store, "owner1", "attendance", self.month)["stale"])

    def test_workspace_distinguishes_saved_published_changed_and_locked(self):
        self.assertFalse(usability.workspace_status(self.store, "schedule", self.month)["saved"])
        payload = self.schedule_payload()
        revision = self.store.save_schedule_month(self.month, payload["snapshot"]["entries"], "")
        operations.publish_in_transaction(self.store, self.month, revision)
        self.assertEqual(usability.workspace_status(self.store, "schedule", self.month)["published"], "current")
        entries = copy.deepcopy(payload["snapshot"]["entries"])
        entries[0]["days"]["2026-11-02"]["end"] = "15:00"
        self.store.save_schedule_month(self.month, entries, revision)
        self.store.set_period_locked(self.month, True)
        status = usability.workspace_status(self.store, "schedule", self.month)
        self.assertEqual(status["published"], "changed")
        self.assertTrue(status["locked"])

    def test_bulk_check_is_readonly_and_rejects_overlap_stale_and_hard_availability(self):
        payload = self.schedule_payload()
        entries = payload["snapshot"]["entries"]
        months = {period: {"revision": "", "entries": entries if period == self.month else []}
                  for period in scheduling.adjacent_months(self.month)}
        result = usability.bulk_check(self.store, {"month": self.month, "months": months})
        self.assertFalse(result["persisted"])
        self.assertEqual(self.store.schedule_month(self.month)["entries"], [])
        months[self.month]["entries"] = entries + [dict(entries[0], row_id="second", section="ÖSSE")]
        with self.assertRaisesRegex(ValueError, "Çift kayıt"):
            usability.bulk_check(self.store, {"month": self.month, "months": months})
        months[self.month]["entries"] = entries
        rule = operations.normalize_availability({
            "employee_id": self.person["id"], "start_date": "2026-11-02", "end_date": "2026-11-02",
            "unavailable_weekdays": [0], "preference": False,
        }, {self.person["id"]})
        with self.store.connection:
            operations.save_availability(self.store.connection, rule, self.store.people())
        with self.assertRaises(ValueError):
            usability.bulk_check(self.store, {"month": self.month, "months": months})

    def test_bulk_replan_preserves_protected_assignment_and_reports_staffing_gap(self):
        ada, bora = self.store.add_people(["Korunan Plan Ada", "Korunan Plan Bora"])
        day = "2026-11-02"
        entries = [
            {"row_id": "replan-ada", "employee_id": ada["id"], "section": "MARKET",
             "slot": 1, "days": {}},
            {"row_id": "replan-bora", "employee_id": bora["id"], "section": "MARKET",
             "slot": 2, "days": {}},
            {"row_id": "replan-protected", "employee_id": self.person["id"],
             "section": "MARKET", "slot": 3,
             "days": {day: {"kind": "work", "start": "10:00", "end": "18:00"}}},
        ]
        revision = self.store.save_schedule_month(self.month, entries, "")
        schedule_protection.set_lock(self.store, {
            "month": self.month, "row_id": "replan-protected", "date": day,
            "revision": revision, "protected": True, "reason": "Onaylı kritik görev",
        })
        self.store.save_compliance_settings({"min_staffing_windows": [{
            "section": "MARKET", "start": "00:00", "end": "06:00", "minimum": 3,
        }]})
        saved = self.store.schedule_month(self.month)
        proposed = [
            {**entries[0], "days": {day: {"kind": "work", "start": "00:00", "end": "06:00"}}},
            {**entries[1], "days": {day: {"kind": "work", "start": "00:00", "end": "06:00"}}},
            {**entries[2], "days": {day: {"kind": "work", "start": "00:00", "end": "06:00"}}},
        ]
        months = {
            period: {
                "entries": proposed if period == self.month else [],
                "revision": self.store.schedule_month(period)["revision"],
            }
            for period in scheduling.adjacent_months(self.month)
        }

        result = usability.bulk_check(self.store, {"month": self.month, "months": months})

        self.assertFalse(result["persisted"])
        self.assertEqual([(item["row_id"], item["day"]) for item in result["skipped"]],
                         [("replan-protected", day)])
        protected = next(row for row in result["months"][self.month]["entries"]
                         if row["row_id"] == "replan-protected")
        self.assertEqual(protected["days"][day], entries[2]["days"][day])
        ada_after = next(row for row in result["months"][self.month]["entries"]
                         if row["row_id"] == "replan-ada")
        self.assertEqual(ada_after["days"][day]["start"], "00:00")
        staffing = next(item for item in result["findings"]
                        if item["type"] == "min_staffing" and item["date"] == day)
        self.assertIn("00:00–06:00 aralığında en az 3 çalışan gerekir; en düşük sayı 2",
                      staffing["message"])
        self.assertEqual(self.store.schedule_month(self.month), saved)

    def test_task_progress_rejects_unknown_flows_duplicates_and_invalid_months(self):
        valid = {"flow": "closing", "month": "2026-11", "checked": [0, 3]}
        self.assertEqual(ui_settings.normalize({"easy_task": valid})["easy_task"], valid)
        for invalid in (valid | {"flow": "unknown"}, valid | {"checked": [1, 1]},
                        valid | {"checked": [True]}, valid | {"month": "2026-99"}):
            with self.assertRaises(ValueError):
                ui_settings.normalize({"easy_task": invalid})

    def test_attendance_preview_is_readonly_validated_and_source_checked(self):
        payload = {"month": self.month,
                   "revisions": usability._revisions(self.store, "attendance", self.month),
                   "snapshot": {"overrides": {"2026-11-02": {str(self.person["id"]): "R"}}, "deductions": {}, "overtime": {}}}
        self.assertFalse(usability.attendance_check(self.store, payload)["persisted"])
        self.assertEqual(self.store.attendance_month(self.month)["overrides"], {})
        invalid = copy.deepcopy(payload)
        invalid["snapshot"]["overrides"]["2026-11-02"][str(self.person["id"])] = "INVALID CODE LONG"
        with self.assertRaises(ValueError):
            usability.attendance_check(self.store, invalid)
        self.store.save_schedule_month("2026-12", [], "")
        with self.assertRaisesRegex(ValueError, "kaynak aylar değişti"):
            usability.attendance_check(self.store, payload)


class UsabilityHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "http.sqlite3"
        with Store(self.path) as store:
            store.add_person("Deneme Personel")
            for name, role in (("Admin", "admin"), ("Other", "admin"), ("Viewer", "viewer"), ("Operator", "operator")):
                store.save_user(name, "abcdef", role)
        self.server = create_server(self.path, Path(__file__).resolve().parents[1] / "web")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookies = {}
        for username in ("Admin", "Other", "Viewer", "Operator"):
            status, _, cookie = self.send("/api/auth/login", "POST", {"username": username, "password": "abcdef"}, None)
            self.assertEqual(status, 200)
            self.cookies[username] = cookie

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def send(self, path, method="GET", body=None, user="Admin"):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        try:
            headers = {"Content-Type": "application/json", "Cookie": self.cookies.get(user, "")}
            connection.request(method, path, None if body is None else json.dumps(body).encode(), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read()), (response.getheader("Set-Cookie") or "").split(";")[0]
        finally:
            connection.close()

    def payload(self):
        with Store(self.path) as store:
            revisions = usability._revisions(store, "schedule", "2026-11")
        return {"month": "2026-11", "draft_revision": "", "revisions": revisions,
                "snapshot": {"entries": [{"row_id": "one", "employee_id": 1, "section": "MARKET", "slot": 1,
                    "days": {"2026-11-02": {"kind": "work", "start": "08:00", "end": "16:00"}}}],
                    "neighborEntries": {"2026-10": [], "2026-12": []}}}

    def test_real_http_account_isolation_permissions_and_recovery_cas(self):
        payload = self.payload()
        status, result, _ = self.send("/api/schedule/draft", "PUT", payload)
        self.assertEqual(status, 200, result)
        revision = result["draft"]["draft_revision"]
        self.assertIsNone(self.send("/api/schedule/draft?month=2026-11", user="Other")[1]["draft"])
        self.assertEqual(self.send("/api/schedule/draft", "PUT", payload)[0], 400)
        self.assertEqual(self.send("/api/schedule/draft?month=2026-11", user="Viewer")[0], 403)
        self.assertEqual(self.send("/api/attendance/bulk-check", "POST", {}, "Viewer")[0], 403)
        status, _, _ = self.send("/api/access/permissions", "PUT", {
            "username": "Operator", "permissions": ["schedule.read", "schedule.write"], "confirm_sensitive": False})
        self.assertEqual(status, 200)
        self.assertEqual(self.send("/api/schedule/draft", "PUT", payload, "Operator")[0], 200)
        self.assertIsNone(self.send("/api/attendance/workspace?month=2026-11", user="Operator")[1].get("revision"))
        with Store(self.path) as store:
            store.save_schedule_month("2026-12", [], "")
            self.assertEqual(store.schedule_month("2026-11")["entries"], [])
        self.assertTrue(self.send("/api/schedule/draft?month=2026-11")[1]["draft"]["stale"])
        self.assertEqual(self.send("/api/schedule/draft/discard", "POST", {"month": "2026-11", "draft_revision": ""})[0], 400)
        self.assertEqual(self.send("/api/schedule/draft/discard", "POST", {"month": "2026-11", "draft_revision": revision})[0], 200)

    def test_http_workspace_evidence_bulk_validation_and_locked_periods(self):
        status, result, _ = self.send("/api/schedule/workspace?month=2026-11&evidence=1")
        self.assertEqual(status, 200, result)
        self.assertFalse(result["verification"]["plan"])
        self.assertFalse(result["verification"]["closing"])
        self.assertFalse(result["verification"]["approvals"])
        payload = self.payload()
        months = {period: {"entries": payload["snapshot"]["entries"] if period == "2026-11" else [], "revision": ""}
                  for period in scheduling.adjacent_months("2026-11")}
        status, result, _ = self.send("/api/schedule/bulk-check", "POST", {"month": "2026-11", "months": months})
        self.assertEqual(status, 200, result)
        self.assertFalse(result["persisted"])
        with Store(self.path) as store:
            self.assertEqual(store.schedule_month("2026-11")["entries"], [])
            store.set_period_locked("2026-11", True)
            revisions = usability._revisions(store, "attendance", "2026-11")
        self.assertEqual(self.send("/api/schedule/bulk-check", "POST", {"month": "2026-11", "months": months})[0], 400)
        self.assertEqual(self.send("/api/attendance/bulk-check", "POST", {"month": "2026-11", "revisions": revisions,
            "snapshot": {"overrides": {}, "deductions": {}, "overtime": {}}})[0], 400)
