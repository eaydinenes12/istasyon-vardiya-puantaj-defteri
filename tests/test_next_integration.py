import json
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import unittest

import access_services
import scheduling
from storage import Store
from tests import test_application
from workspace_tools import PhoneViewServer


class NextIntegrationTests(unittest.TestCase):
    setUp = test_application.ApplicationTests.setUp
    tearDown = test_application.ApplicationTests.tearDown
    request = test_application.ApplicationTests.request
    json_request = test_application.ApplicationTests.json_request
    add_person = test_application.ApplicationTests.add_person

    def http(self, path, method="GET", payload=None, cookie=""):
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        data = None if payload is None else json.dumps(payload).encode()
        try:
            response = urlopen(Request(self.base_url + path, data=data, method=method, headers=headers), timeout=15)
        except HTTPError as error:
            response = error
        return response.status, response.headers, json.loads(response.read())

    def login(self, username):
        status, headers, result = self.http("/api/auth/login", "POST", {"username": username, "password": "abcdef"})
        self.assertEqual(status, 200, result)
        return headers["Set-Cookie"].split(";")[0]

    def test_real_gate_live_downgrade_and_sensitive_reads(self):
        with Store(self.server.data_path) as store:
            store.save_user("Admin", "abcdef", "admin")
            store.save_user("Viewer", "abcdef", "viewer")
            store.save_user("Operator", "abcdef", "operator")
        viewer = self.login("Viewer")
        self.assertEqual(self.http("/api/schedule?month=2026-03", cookie=viewer)[0], 200)
        for path in ("/api/backups", "/api/continuity/status", "/api/employee-profile?employee_id=1",
                     "/api/export?kind=schedule&month=2026-03&week=0", "/api/access/permissions"):
            self.assertEqual(self.http(path, cookie=viewer)[0], 403, path)
        admin = self.login("Admin")
        self.assertEqual(self.http("/api/access/permissions", cookie=admin)[0], 200)
        status, _, result = self.http("/api/access/permissions", "PUT", {
            "username": "Operator", "permissions": ["schedule.read", "schedule.write"], "confirm_sensitive": False,
        }, admin)
        self.assertEqual(status, 200, result)
        operator = self.login("Operator")
        status, _, result = self.http("/api/schedule-months", "PUT", {
            "months": {"2026-03": {"revision": "", "entries": []}},
        }, operator)
        self.assertEqual(status, 200, result)
        self.http("/api/access/permissions", "PUT", {
            "username": "Operator", "permissions": [], "confirm_sensitive": False,
        }, admin)
        self.assertEqual(self.http("/api/schedule?month=2026-03", cookie=operator)[0], 403)
        self.assertEqual(self.http("/api/unknown", cookie=operator)[0], 403)
        status, _, result = self.http("/api/users", "PUT", {
            "username": "Operator", "password": "", "role": "admin", "confirm_admin": False,
        }, admin)
        self.assertEqual(status, 400, result)

    def test_employee_portal_exact_capability_and_phone_service_post(self):
        person = self.add_person()
        status, result = self.json_request("/api/access/portal-links", "POST", {
            "employee_id": person["id"], "days": 7,
        })
        self.assertEqual(status, 200, result)
        token_path = result["path"]
        with Store(self.server.data_path) as store, store.connection:
            store._write_setting("published_plans", {"2026-11": {"entries": [{
                "employee_id": person["id"], "section": "MARKET",
                "days": {"2026-11-20": {"kind": "work", "start": "08:00", "end": "16:00"}},
            }]}})
        status, _, content = self.request(token_path)
        self.assertEqual(status, 200)
        self.assertIn("08:00".encode(), content)
        self.assertEqual(self.request(token_path + "?employee_id=999")[0], 404)
        service = PhoneViewServer(self.server.data_path, lambda: Store(self.server.data_path))
        service.host = "127.0.0.1"
        self.addCleanup(service.stop)
        phone = service.start()
        origin = f"http://127.0.0.1:{phone['port']}"
        payload = urlencode({
            "kind": "leave", "start_date": "2026-12-01", "end_date": "2026-12-02", "details": "",
        }).encode()
        with urlopen(Request(origin + token_path + "/requests", data=payload, headers={
            "Origin": origin, "Content-Type": "application/x-www-form-urlencoded",
        }), timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("Talebiniz".encode(), response.read())
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(origin + "/api/people", data=b"{}", headers={"Content-Type": "application/json"}), timeout=10)
        self.assertEqual(error.exception.code, 404)
        with Store(self.server.data_path) as store:
            row = store.connection.execute("SELECT employee_id,status FROM portal_requests").fetchone()
            self.assertEqual((row["employee_id"], row["status"]), (person["id"], "pending"))
            self.assertEqual(store.schedule_month("2026-12")["entries"], [])

    def test_data_routes_schema_and_no_implicit_migration(self):
        person = self.add_person()
        status, result = self.json_request("/api/continuity/status")
        self.assertEqual(status, 200, result)
        self.assertEqual(result["actions"], [])
        status, result = self.json_request("/api/continuity/package?month=2026-03")
        self.assertEqual(status, 200, result)
        self.assertEqual(result["package"]["people"][0]["id"], person["id"])
        status, result = self.json_request("/api/continuity/migration/apply", "POST", {
            "preview_id": "not-a-preview", "confirmed": True,
        })
        self.assertEqual(status, 400, result)
        with Store(self.server.data_path) as store:
            self.assertEqual(len(store.people()), 1)

    def test_attendance_editor_cannot_read_or_clear_hidden_money(self):
        person = self.add_person()
        with Store(self.server.data_path) as store:
            store.save_attendance_month("2026-03", {"2026-03-02": {str(person["id"]): "X"}},
                                        {str(person["id"]): 123.45}, "")
            store.save_user("Admin", "abcdef", "admin")
            store.save_user("Operator", "abcdef", "operator")
        admin = self.login("Admin")
        status, _, result = self.http("/api/access/permissions", "PUT", {
            "username": "Operator", "permissions": ["attendance.read", "attendance.write"],
            "confirm_sensitive": False,
        }, admin)
        self.assertEqual(status, 200, result)
        operator = self.login("Operator")
        status, _, result = self.http("/api/attendance?month=2026-03", cookie=operator)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["payroll_redacted"])
        self.assertEqual(result["deductions"], {})
        self.assertIsNone(result["attendance"]["rows"][0]["deduction_amount"])
        self.assertEqual(self.http("/api/attendance", "PUT", {
            "month": "2026-03", "revision": result["revision"], "overrides": result["overrides"], "deductions": {},
        }, operator)[0], 200)
        with Store(self.server.data_path) as store:
            self.assertEqual(store.attendance_month("2026-03")["deductions"], {str(person["id"]): 123.45})
            revision = store.attendance_month("2026-03")["revision"]
        self.assertEqual(self.http("/api/attendance", "PUT", {
            "month": "2026-03", "revision": revision, "overrides": {}, "deductions": {str(person["id"]): 1},
        }, operator)[0], 403)

    def test_attendance_correction_evidence_permissions_staleness_and_rejection(self):
        person = self.add_person("Puantaj Kaynağı")
        month, day, other_day = "2026-03", "2026-03-02", "2026-03-03"
        schedule = [{"row_id": "correction-row", "employee_id": person["id"], "section": "MARKET",
                     "days": {day: {"kind": "work", "start": "08:00", "end": "16:00"}}}]
        self.assertEqual(self.json_request("/api/schedule", "PUT", {
            "month": month, "entries": schedule, "revision": "",
        })[0], 200)
        overrides = {day: {str(person["id"]): "X"}, other_day: {str(person["id"]): "H"}}
        self.assertEqual(self.json_request("/api/attendance", "PUT", {
            "month": month, "revision": "", "overrides": overrides,
            "deductions": {str(person["id"]): 12},
        })[0], 200)
        with Store(self.server.data_path) as store:
            with store.connection:
                store._write_setting("pdks_records", {month: {"records": [{
                    "name": person["name"], "date": day, "in": "08:05", "out": "16:00",
                }]}})
            store.save_user("Admin", "abcdef", "admin")
            store.save_user("Viewer", "abcdef", "viewer")
            store.save_user("Operator", "abcdef", "operator")

        admin = self.login("Admin")
        self.assertEqual(self.http("/api/access/permissions", "PUT", {
            "username": "Operator", "permissions": ["attendance.read"], "confirm_sensitive": False,
        }, admin)[0], 200)
        viewer = self.login("Viewer")
        operator = self.login("Operator")
        evidence_path = f"/api/attendance/corrections?month={month}&date={day}&employee_id={person['id']}"
        self.assertEqual(self.http(f"/api/attendance/corrections?month={month}", cookie=viewer)[0], 200)
        self.assertEqual(self.http(evidence_path, cookie=viewer)[0], 403)
        self.assertEqual(self.http(evidence_path, cookie=operator)[0], 403)
        unauthorized_request = {
            "month": month, "date": day, "employee_id": person["id"], "code": "R",
            "reason": "Kaynakla eşleştirildi", "fingerprint": "unavailable-to-read-only-user",
        }
        self.assertEqual(self.http("/api/attendance/corrections", "POST", unauthorized_request, viewer)[0], 403)
        self.assertEqual(self.http("/api/attendance/corrections", "POST", unauthorized_request, operator)[0], 403)
        self.assertEqual(self.http("/api/access/permissions", "PUT", {
            "username": "Operator", "permissions": ["attendance.read", "attendance.write"],
            "confirm_sensitive": False,
        }, admin)[0], 200)

        status, _, current = self.http(evidence_path, cookie=operator)
        self.assertEqual(status, 200, current)
        self.assertEqual(current["punches"], [{"in": "08:05", "out": "16:00"}])
        request = {
            "month": month, "date": day, "employee_id": person["id"], "code": "R",
            "reason": "Kaynakla eşleştirildi", "fingerprint": current["fingerprint"],
        }
        status, _, created = self.http("/api/attendance/corrections", "POST", request, operator)
        self.assertEqual(status, 201, created)
        duplicate_status, _, duplicate = self.http("/api/attendance/corrections", "POST", request, operator)
        self.assertEqual(duplicate_status, 400, duplicate)
        self.assertEqual(self.http("/api/attendance/corrections", "PUT", {
            "month": month, "id": created["id"], "approve": True,
        }, operator)[0], 403)

        with Store(self.server.data_path) as store, store.connection:
            records = store._setting("pdks_records", {})
            records[month]["records"][0]["out"] = "16:30"
            store._write_setting("pdks_records", records)
        decision = {"month": month, "id": created["id"], "approve": True}
        self.assertEqual(self.http("/api/attendance/corrections", "PUT", decision, admin)[0], 400)
        self.assertEqual(self.http("/api/attendance/corrections", "PUT", {
            **decision, "approve": False,
        }, admin)[0], 200)
        status, _, saved = self.http(f"/api/attendance?month={month}", cookie=admin)
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["overrides"], overrides)
        self.assertEqual(saved["deductions"], {str(person["id"]): 12})
        status, _, requests = self.http(f"/api/attendance/corrections?month={month}", cookie=operator)
        self.assertEqual(status, 200, requests)
        self.assertEqual([(item["requested_by"], item["status"]) for item in requests["requests"]],
                         [("Operator", "rejected")])

    def test_configured_hard_rules_enforced_on_single_and_atomic_multimonth_save(self):
        person = self.add_person()
        status, result = self.json_request("/api/operations/availability", "PUT", {
            "employee_id": person["id"], "start_date": "2026-03-10", "end_date": "2026-03-20",
            "unavailable_weekdays": [0], "no_night": False, "no_weekend": False, "preference": False,
            "unavailable_ranges": [],
        })
        self.assertEqual(status, 200, result)
        entries = [{"row_id": "restricted", "employee_id": person["id"], "section": "MARKET",
                    "days": {"2026-03-16": {"kind": "work", "start": "08:00", "end": "16:00"}}}]
        status, result = self.json_request("/api/schedule", "PUT", {
            "month": "2026-03", "revision": "", "entries": entries,
        })
        self.assertEqual(status, 400, result)
        status, result = self.json_request("/api/schedule-months", "PUT", {"months": {
            "2026-02": {"revision": "", "entries": []},
            "2026-03": {"revision": "", "entries": entries},
        }})
        self.assertEqual(status, 400, result)
        with Store(self.server.data_path) as store:
            self.assertEqual(store.schedule_month("2026-02")["revision"], "")
            self.assertEqual(store.schedule_month("2026-03")["revision"], "")

    def test_publication_requires_preview_revision_and_notifies_only_real_changes(self):
        person = self.add_person()
        entries = [{"row_id": "published-row", "employee_id": person["id"], "section": "MARKET",
                    "days": {"2026-03-02": {"kind": "work", "start": "08:00", "end": "16:00"}}}]
        _, saved = self.json_request("/api/schedule", "PUT", {"month": "2026-03", "revision": "", "entries": entries})
        self.assertEqual(self.json_request("/api/schedule/publish", "POST", {"month": "2026-03"})[0], 400)
        self.assertEqual(self.json_request("/api/schedule/publish", "POST", {
            "month": "2026-03", "revision": "stale",
        })[0], 400)
        status, result = self.json_request("/api/schedule/publish", "POST", {
            "month": "2026-03", "revision": saved["revision"],
        })
        self.assertEqual(status, 200, result)
        with Store(self.server.data_path) as store:
            count = store.connection.execute("SELECT COUNT(*) FROM access_notifications").fetchone()[0]
            self.assertGreater(count, 0)
        self.json_request("/api/schedule/publish", "POST", {"month": "2026-03", "revision": saved["revision"]})
        with Store(self.server.data_path) as store:
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM access_notifications").fetchone()[0], count)


if __name__ == "__main__":
    unittest.main()
