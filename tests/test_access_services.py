import io
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import access_services as service
from storage import Store


class Handler:
    def __init__(self, path, user=None, payload=None):
        self.server = SimpleNamespace(data_path=path, sessions={"session": user} if user else {})
        self.current_user = user
        self.payload = payload
        self.result = None
        self.headers = {}
        self.rfile = io.BytesIO()
        self.wfile = io.BytesIO()
        self.client_address = ("127.0.0.1", 1)

    def _is_internal(self):
        return False

    def _session_token(self):
        return "session"

    def _store(self):
        store = Store(self.server.data_path)
        store.actor = (self.current_user or {}).get("username", "")
        return store

    def _json(self, value, status=200):
        self.result = (status, value)

    def _read_json(self, maximum):
        return self.payload

    def _attendance_for(self, store, month):
        from server import ScheduleRequestHandler

        return ScheduleRequestHandler._attendance_for(object.__new__(ScheduleRequestHandler), store, month)

    def send_response(self, code):
        self.status = code

    def send_header(self, *args):
        pass

    def end_headers(self):
        pass


class AccessServicesTest(unittest.TestCase):
    def test_public_post_requires_exact_same_origin_or_same_origin_referer(self):
        self.assertTrue(service._same_origin_post({
            "Host": "localhost:8000", "Origin": "http://localhost:8000",
        }))
        self.assertTrue(service._same_origin_post({
            "Host": "localhost:8000", "Referer": "http://localhost:8000/employee-portal/token",
        }))
        self.assertTrue(service._same_origin_post({
            "Host": "localhost:8000", "Sec-Fetch-Site": "same-origin",
        }))
        self.assertTrue(service._same_origin_post({
            "Host": "localhost:8000", "Origin": "null", "Sec-Fetch-Site": "same-origin",
        }))
        self.assertFalse(service._same_origin_post({
            "Host": "localhost:8000", "Origin": "http://attacker.example",
        }))
        self.assertFalse(service._same_origin_post({
            "Host": "localhost:8000", "Referer": "http://attacker.example/employee-portal/token",
        }))
        self.assertFalse(service._same_origin_post({"Host": "localhost:8000"}))

    def test_attendance_evidence_keeps_month_sources_and_cell_correction_history_distinct(self):
        import attendance_corrections
        import operations
        import scheduling

        day = "2026-10-12"
        with Store(self.path) as store:
            operations.initialize(store.connection)
            attendance_corrections.initialize(store.connection)
            employee_id = store.people()[0]["id"]
            entries = scheduling.normalize_schedule_entries("2026-10", [{
                "row_id": "evidence-ada", "employee_id": employee_id, "section": "MARKET",
                "days": {day: {"kind": "work", "start": "09:00", "end": "17:00"}},
            }], store.people())
            schedule_revision = store.save_schedule_month("2026-10", entries, "")
            attendance_seed_revision = store.save_attendance_month(
                "2026-10", {day: {str(employee_id): "H"}}, {},
                store.attendance_month("2026-10")["revision"],
            )
            attendance_revision = store.save_attendance_month(
                "2026-10", {day: {str(employee_id): "X"}}, {}, attendance_seed_revision,
            )
            store._write_setting("pdks_records", {"2026-10": {
                "file": "synthetic-pdks.csv", "imported_at": "2026-10-12T08:00:00",
                "actor": "Kabul aktarıcısı",
                "records": [{"name": "Ali", "date": day, "in": "09:00", "out": "17:00"}],
            }})
            store.actor = "Kabul içe aktarıcısı"
            store.record_change("attendance.import_excel", "2026-10 puantajı Excel'den içe aktarıldı", {
                "month": "2026-10", "file": "synthetic-attendance.xlsx", "codes": 1,
            })
            with store.connection:
                store.connection.execute(
                    "INSERT INTO attendance_corrections "
                    "(id,month,day,employee_id,code,reason,evidence,requested_by,created_at,status,decided_by,decided_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,'approved',?,?)",
                    ("synthetic-correction", "2026-10", day, employee_id, "X",
                     "Kabul düzeltme gerekçesi", "fingerprint", "Kabul personeli",
                     "2026-10-12T09:00:00", "Kabul yöneticisi", "2026-10-12T10:00:00"),
                )
            result = attendance_corrections.evidence(Handler(self.path), store, "2026-10", day, employee_id)

        self.assertEqual(result["pdks_source"]["file"], "synthetic-pdks.csv")
        self.assertEqual(result["punches"], [{"in": "09:00", "out": "17:00"}])
        self.assertEqual(result["imports"], [{
            "occurred_at": result["imports"][0]["occurred_at"],
            "actor": "Kabul içe aktarıcısı", "file": "synthetic-attendance.xlsx", "codes": 1,
        }])
        self.assertEqual(result["corrections"][0]["reason"], "Kabul düzeltme gerekçesi")
        self.assertEqual(result["corrections"][0]["decided_by"], "Kabul yöneticisi")
        self.assertEqual(result["manual"], "X")
        self.assertEqual(result["schedule_revision"], schedule_revision)
        self.assertEqual(result["attendance_revision"], attendance_revision)
        self.assertEqual(len(result["attendance_revisions"]), 1)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.path = Path(self.directory.name) / "data.sqlite3"
        with Store(self.path) as store:
            service.initialize(store.connection)
            store.connection.execute("INSERT INTO employees(name) VALUES ('Ali'),('Ayşe')")
            store.save_user("Admin", "abcdef", "admin")
            store.save_user("Viewer", "abcdef", "viewer")
            store.save_user("Operator", "abcdef", "operator")

    def tearDown(self):
        self.directory.cleanup()

    def handler(self, name="Admin", payload=None):
        with Store(self.path) as store:
            user = next(u for u in store.users() if u["username"] == name)
        return Handler(self.path, user, payload)

    def write(self, route, payload, name="Admin", method="POST"):
        handler = self.handler(name, payload)
        self.assertTrue(service.handle_write(handler, method, route))
        return handler.result[1]

    def token(self, person=1):
        return self.write("/api/access/portal-links", {"employee_id": person, "days": 7})["path"].split("/")[-1]

    def test_permission_preview_lists_pages_fields_actions_and_outputs(self):
        preview = service.permission_preview(["schedule.read", "payroll"])
        self.assertIn("Vardiya tablosu", preview["pages"])
        self.assertIn("Personel adı, bölüm, vardiya türü ve saatleri", preview["fields"])
        self.assertIn("Bordro ve ücret çıktıları", preview["outputs"])
        with self.assertRaises(ValueError):
            service.permission_preview(["unknown"])
        handler = self.handler()
        self.assertTrue(service.handle_get(handler, "/api/access/permissions", {}))
        self.assertIn("effects", handler.result[1])

    def public(self, token, body=None, origin="http://localhost", suffix="/requests"):
        handler = Handler(self.path)
        path = "/employee-portal/" + token
        if body is None:
            method = "GET"
        else:
            method = "POST"
            path += suffix
            encoded = urlencode(body).encode()
            handler.rfile = io.BytesIO(encoded)
            handler.headers = {"Origin": origin, "Host": "localhost", "Content-Length": str(len(encoded)),
                               "Content-Type": "application/x-www-form-urlencoded"}
        self.assertTrue(service.handle_public(handler, path, method))
        return handler.status, handler.wfile.getvalue().decode()

    def test_permissions_live_revoke_and_fail_closed(self):
        viewer = self.handler("Viewer")
        self.assertTrue(service.authorize(viewer, "GET", "/api/schedule", {}))
        self.assertTrue(service.authorize(viewer, "GET", "/api/import-profiles", {}))
        self.assertTrue(service.authorize(viewer, "POST", "/api/import-extract", {}))
        self.assertTrue(service.authorize(viewer, "POST", "/api/import-preview", {}))
        self.assertTrue(service.authorize(viewer, "POST", "/api/import-mapped-preview", {}))
        self.assertFalse(service.authorize(viewer, "PUT", "/api/import-profiles", {}))
        self.assertTrue(service.authorize(viewer, "GET", "/api/assistant/models", {}))
        self.assertTrue(service.authorize(viewer, "POST", "/api/assistant/chat", {}))
        self.assertTrue(service.authorize(viewer, "GET", "/api/reports/check", {"month": ["2026-10"]}))
        self.assertFalse(service.authorize(viewer, "GET", "/api/reports/payroll", {}))
        self.assertFalse(service.authorize(viewer, "GET", "/api/archive", {}))
        self.assertFalse(service.authorize(viewer, "GET", "/api/personnel-assets", {"employee_id": ["1"]}))
        self.assertFalse(service.authorize(viewer, "POST", "/api/personnel-assets", {}))
        self.assertFalse(service.authorize(viewer, "POST", "/api/forms/preview", {}))
        self.assertFalse(service.authorize(viewer, "POST", "/api/schedule", {}))
        operator = self.handler("Operator")
        self.assertFalse(service.authorize(operator, "GET", "/api/schedule", {}))
        self.assertFalse(service.authorize(operator, "GET", "/api/assistant/models", {}))
        self.write("/api/access/permissions", {"username": "Operator", "permissions": ["schedule.read", "schedule.write"],
                                               "confirm_sensitive": False}, method="PUT")
        self.assertTrue(service.authorize(operator, "GET", "/api/schedule", {}))
        self.assertTrue(service.authorize(operator, "GET", "/api/import-profiles", {}))
        self.assertTrue(service.authorize(operator, "POST", "/api/import-extract", {}))
        self.assertTrue(service.authorize(operator, "POST", "/api/import-preview", {}))
        self.assertTrue(service.authorize(operator, "POST", "/api/import-mapped-preview", {}))
        self.assertTrue(service.authorize(operator, "PUT", "/api/import-profiles", {}))
        self.assertTrue(service.authorize(operator, "GET", "/api/assistant/models", {}))
        self.assertTrue(service.authorize(operator, "PUT", "/api/schedule", {}))
        self.assertFalse(service.authorize(operator, "GET", "/api/reports/person-pdf", {}))
        self.assertFalse(service.authorize(operator, "GET", "/api/unknown", {}))
        self.assertFalse(service.authorize(operator, "GET", "/api/personnel-assets", {"employee_id": ["1"]}))
        self.write("/api/access/permissions", {"username": "Operator", "permissions": ["documents"],
                                               "confirm_sensitive": True}, method="PUT")
        self.assertTrue(service.authorize(operator, "GET", "/api/personnel-assets", {"employee_id": ["1"]}))
        self.assertTrue(service.authorize(operator, "POST", "/api/personnel-assets", {}))
        self.write("/api/access/permissions", {"username": "Operator", "permissions": [], "confirm_sensitive": False}, method="PUT")
        self.assertFalse(service.authorize(operator, "GET", "/api/schedule", {}))
        self.assertTrue(service.authorize(self.handler(), "GET", "/api/archive", {}))
        with self.assertRaises(ValueError):
            self.write("/api/access/permissions", {"username": "Operator", "permissions": ["payroll"], "confirm_sensitive": False}, method="PUT")
        with self.assertRaises(ValueError):
            self.write("/api/access/permissions", {"username": "Operator", "permissions": ["bogus"], "confirm_sensitive": True}, method="PUT")
        with Store(self.path) as store:
            store.delete_user("Operator")
        self.assertFalse(service.authorize(operator, "GET", "/api/schedule", {}))
        self.assertEqual(operator.result[0], 401)

    def test_check_report_filters_sensitive_and_ignored_items(self):
        viewer = self.handler("Viewer")
        viewer._month_attendance = lambda store, month: ({}, [])
        viewer._balance_summary = lambda store, month: {}
        viewer._check_report = lambda store, month, attendance, entries, balances: {
            "month": month, "people": 1,
            "items": [
                {"type": "overtime", "severity": "info", "message": "3 saat"},
                {"type": "document_expiry", "severity": "warning", "message": "özel belge"},
                {"type": "special_night", "severity": "warning", "message": "gebelik"},
                {"type": "preference", "severity": "warning", "message": "özel not"},
                {"type": "manual_change", "severity": "info", "message": "Rapor"},
            ],
            "ignored_items": [{"type": "special_overtime", "severity": "error", "message": "sağlık"},
                              {"type": "weekly_rest", "severity": "warning", "message": "dinlenme"}],
        }
        self.assertTrue(service.handle_get(viewer, "/api/reports/check", {"month": ["2026-10"]}))
        report = viewer.result[1]
        self.assertEqual([item["type"] for item in report["items"]], ["overtime"])
        self.assertEqual([item["type"] for item in report["ignored_items"]], ["weekly_rest"])
        self.assertEqual(report["counts"], {"error": 0, "warning": 0, "info": 1})
        self.assertEqual(report["ignored"], 1)
        self.assertTrue(report["redacted"])
        self.assertFalse(service.handle_get(self.handler(), "/api/reports/check", {"month": ["2026-10"]}))

    def test_portal_isolation_expiry_revocation_and_requests(self):
        token = self.token()
        with Store(self.path) as store, store.connection:
            store._write_setting("published_plans", {"2026-10": {"entries": [
                {"employee_id": 1, "days": {"2026-10-01": {"kind": "work", "start": "09:00", "end": "17:00"},
                                               "2026-10-02": {"kind": "sick_leave", "notes": "GİZLİ"}}},
                {"employee_id": 2, "days": {"2026-10-01": {"kind": "work", "start": "12:00", "end": "20:00"}}}]}})
        status, page = self.public(token)
        self.assertEqual(status, 200)
        self.assertIn("09:00–17:00", page)
        for secret in ("Ayşe", "12:00", "GİZLİ", "sick_leave"):
            self.assertNotIn(secret, page)
        request = {"kind": "leave", "start_date": "2026-10-10", "end_date": "2026-10-11", "details": "İstek"}
        self.assertEqual(self.public(token, request, "http://evil.example")[0], 403)
        self.assertEqual(self.public(token, request)[0], 200)
        self.assertNotIn("İstek", self.public(self.token(2))[1])
        request["end_date"] = "2026-09-10"
        self.assertIn("Tarih", self.public(token, request)[1])
        request["end_date"] = "2026-10-10"
        request["details"] = "x" * 301
        self.assertIn("300", self.public(token, request)[1])
        with Store(self.path) as store, store.connection:
            store.connection.execute("UPDATE portal_tokens SET expires_at=0 WHERE token_hash=?", (service._token_hash(token),))
        self.assertEqual(self.public(token)[0], 404)
        token2 = self.token()
        unrelated = self.token(2)
        self.write("/api/access/portal-links/revoke", {"token_hash": service._token_hash(token2)})
        self.assertEqual(self.public(token2)[0], 404)
        self.assertEqual(self.public(unrelated)[0], 200)

    def test_rate_review_and_notification_semantics(self):
        token = self.token()
        request = {"kind": "availability", "start_date": "2026-10-10", "end_date": "2026-10-10", "details": ""}
        for _ in range(20):
            self.assertEqual(self.public(token, request)[0], 200)
        self.assertEqual(self.public(token, request)[0], 429)
        with Store(self.path) as store:
            first = store.connection.execute("SELECT id FROM portal_requests ORDER BY id LIMIT 1").fetchone()[0]
        result = self.write("/api/access/requests/review", {"id": first, "decision": "accepted"})
        self.assertEqual((result["status"], result["processed"]), ("accepted", False))
        with self.assertRaises(ValueError):
            self.write("/api/access/requests/review", {"id": first, "decision": "approved"})
        handler = self.handler()
        service.handle_get(handler, "/api/access/notifications", {})
        notice = handler.result[1]["notifications"][0]
        self.assertIsNotNone(notice["delivered_at"])
        self.assertIsNone(notice["read_at"])
        self.write("/api/access/notifications/read", {"id": notice["id"]})
        service.handle_get(handler, "/api/access/notifications", {})
        self.assertIsNotNone(handler.result[1]["notifications"][0]["read_at"])
        with Store(self.path) as store, store.connection:
            before = len(store.connection.execute("SELECT id FROM access_notifications").fetchall())
            service.notify_published_in_transaction(store, "2026-10", [], [])
            self.assertEqual(before, len(store.connection.execute("SELECT id FROM access_notifications").fetchall()))
            after = [{"employee_id": 1, "days": {"2026-10-01": {"kind": "work", "start": "09:00", "end": "17:00"}}}]
            service.notify_published_in_transaction(store, "2026-10", [], after)
            self.assertGreater(len(store.connection.execute("SELECT id FROM access_notifications").fetchall()), before)
        with Store(self.path) as store:
            before = store.connection.execute("SELECT count(*) FROM access_notifications").fetchone()[0]
            store.connection.execute("BEGIN")
            service.notify_published_in_transaction(store, "2026-10", [], after)
            store.connection.rollback()
            self.assertEqual(store.connection.execute("SELECT count(*) FROM access_notifications").fetchone()[0], before)

    def test_inactive_employee_and_exact_public_routes(self):
        token = self.token()
        handler = Handler(self.path)
        path = "/employee-portal/" + token
        self.assertFalse(service.handle_public(handler, path + "/requests/extra", "POST"))
        self.assertFalse(service.handle_public(handler, path + "/", "GET"))
        self.assertTrue(service.handle_public(handler, path + "?other=1", "GET"))
        self.assertEqual(handler.status, 404)
        with Store(self.path) as store, store.connection:
            store.connection.execute("UPDATE employees SET active=0 WHERE id=1")
        payload = {"kind": "leave", "start_date": "2026-10-10", "end_date": "2026-10-10", "details": ""}
        self.assertEqual(self.public(token, payload)[0], 404)
        self.assertEqual(self.public(token)[0], 404)
        with Store(self.path) as store:
            self.assertEqual(store.connection.execute("SELECT count(*) FROM portal_requests").fetchone()[0], 0)
            self.assertEqual(store.connection.execute("SELECT count(*) FROM access_notifications").fetchone()[0], 0)

    def test_portal_shows_only_latest_twelve_and_safe_weekly_off(self):
        token = self.token()
        plans = {}
        for index in range(13):
            year, month = 2025 + index // 12, 1 + index % 12
            period = f"{year:04d}-{month:02d}"
            plans[period] = {"entries": [{"employee_id": 1, "days": {
                period + "-01": {"kind": "weekly_off", "note": "PRIVATE"},
                period + "-02": {"kind": "sick_leave", "note": "MEDICAL"}}}]}
        with Store(self.path) as store, store.connection:
            store._write_setting("published_plans", plans)
        page = self.public(token)[1]
        self.assertNotIn("2025-01-01", page)
        self.assertIn("2026-01-01", page)
        published_schedule = page.split("<h2>Yayınlanan programım</h2>", 1)[1].split("</table>", 1)[0]
        schedule_rows = published_schedule.split("<tbody>", 1)[1]
        self.assertEqual(schedule_rows.count("<tr>"), 12)
        self.assertEqual(schedule_rows.count("Hafta tatili"), 12)
        self.assertNotIn("PRIVATE", page)
        self.assertNotIn("MEDICAL", page)

    def test_public_portal_requires_revision_specific_read_confirmation(self):
        token = self.token()
        revision = "a" * 32
        with Store(self.path) as store, store.connection:
            store._write_setting("published_plans", {"2026-10": {
                "source_revision": revision,
                "entries": [{"employee_id": 1, "section": "MARKET", "days": {
                    "2026-10-12": {"kind": "work", "start": "09:00", "end": "17:00"}}}],
                "changes": [
                    {"employee_id": 1, "date": "2026-10-12", "section": "MARKET",
                     "before": "08:00–16:00", "after": "09:00–17:00"},
                    {"employee_id": 2, "date": "2026-10-12", "section": "SECRET",
                     "before": "PRIVATE", "after": "PRIVATE"},
                ],
            }})
        status, page = self.public(token)
        self.assertEqual(status, 200)
        self.assertIn("<th>Önceki vardiya</th>", page)
        self.assertIn("<th>Yeni yayınlanan vardiya</th>", page)
        self.assertIn("08:00–16:00", page)
        self.assertIn("09:00–17:00", page)
        self.assertIn("Bu yayın sürümünü okudum", page)
        self.assertNotIn("SECRET", page)
        with Store(self.path) as store:
            status = service._plan_version_status(store, service._token_hash(token), 1)
            self.assertEqual(status["versions"][0]["published_revision"], revision)
            self.assertEqual(status["versions"][0]["viewed_revision"], "")
            self.assertIsNotNone(status["last_seen_at"])
        csrf = re.search(r'name="csrf" value="([A-Za-z0-9_-]+)"', page)[1]
        self.assertEqual(self.public(token, {
            "month": "2026-10", "revision": revision, "csrf": csrf,
        }, suffix="/read")[0], 303)
        with Store(self.path) as store:
            status = service._plan_version_status(store, service._token_hash(token), 1)
            self.assertEqual(status["versions"][0]["viewed_revision"], revision)
        self.assertNotIn("Bu yayın sürümünü okudum", self.public(token)[1])

    def test_published_changes_include_section_but_ignore_order_and_row_ids(self):
        day = {"kind": "work", "start": "09:00", "end": "17:00"}
        entries = [
            {"employee_id": 1, "section": "MARKET", "row_id": "a", "days": {"2026-10-01": day}},
            {"employee_id": 2, "section": "MARKET", "row_id": "b", "days": {"2026-10-01": day}},
        ]
        reordered = [{**entry, "row_id": "changed"} for entry in reversed(entries)]
        moved = [{**entry, "section": "TEMİZLİK" if entry["employee_id"] == 1 else entry["section"]}
                 for entry in reordered]
        with Store(self.path) as store, store.connection:
            service.notify_published_in_transaction(store, "2026-10", entries, reordered)
            self.assertEqual(store.connection.execute("SELECT count(*) FROM access_notifications").fetchone()[0], 0)
            service.notify_published_in_transaction(store, "2026-10", entries, moved)
            self.assertGreater(store.connection.execute("SELECT count(*) FROM access_notifications").fetchone()[0], 0)

    def test_portal_attendance_correction_is_self_scoped_and_changes_only_after_manager_approval(self):
        import attendance_corrections

        with Store(self.path) as store:
            people = store.people()
            employee_id, other_id = people[0]["id"], people[1]["id"]
            day, month = "2026-10-12", "2026-10"
            original = {day: {str(employee_id): "X", str(other_id): "H"}}
            store.save_attendance_month(
                month, original, {}, "", roster={
                    "added": [employee_id, other_id], "removed": [], "order": [employee_id, other_id],
                },
            )

        token = self.token(employee_id)
        status, page = self.public(token)
        self.assertEqual(status, 200)
        self.assertIn(f'action="/employee-portal/{token}/attendance-corrections"', page)
        self.assertIn("Yönetici kaynakları inceleyip onaylamadan puantaj değişmez", page)

        status, page = self.public(
            token, {"date": day, "code": "R", "reason": "Kaynak kayıtla eşleşti"},
            suffix="/attendance-corrections",
        )
        self.assertEqual(status, 200)
        self.assertIn("yöneticiye iletildi", page)
        self.assertIn("R · Kaynak kayıtla eşleşti · Yönetici incelemesi bekleniyor", page)

        with Store(self.path) as store:
            pending = store.connection.execute(
                "SELECT * FROM attendance_corrections WHERE month=? AND employee_id=?",
                (month, employee_id),
            ).fetchone()
            self.assertIsNotNone(pending)
            self.assertEqual(pending["requested_by"], f"Personel portalı #{employee_id}")
            self.assertEqual(pending["status"], "pending")
            self.assertEqual(store.attendance_month(month)["overrides"], original)
            self.assertEqual(
                store.connection.execute("SELECT count(*) FROM attendance_corrections WHERE employee_id=?",
                                         (other_id,)).fetchone()[0],
                0,
            )
            preview = attendance_corrections.evidence(Handler(self.path), store, month, day, employee_id)
            self.assertEqual(preview["code"], "X")
            self.assertEqual(preview["fingerprint"], pending["evidence"])
            manager = Handler(self.path)
            store.actor = "Admin"
            result = attendance_corrections.decide(
                manager, store, {"month": month, "id": pending["id"], "approve": True},
            )
            self.assertEqual(result["status"], "approved")

        with Store(self.path) as store:
            saved = store.attendance_month(month)["overrides"]
            self.assertEqual(saved, {day: {str(employee_id): "R", str(other_id): "H"}})
        self.assertIn("Yönetici onayladı", self.public(token)[1])

        status, page = self.public(
            token, {"date": day, "code": "S", "reason": "Başka gün", "employee_id": str(other_id)},
            suffix="/attendance-corrections",
        )
        self.assertIn("alanları geçersiz", page)
        with Store(self.path) as store:
            self.assertEqual(store.connection.execute(
                "SELECT count(*) FROM attendance_corrections WHERE employee_id=?", (other_id,),
            ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
