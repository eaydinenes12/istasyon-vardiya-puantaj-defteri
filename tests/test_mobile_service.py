import http.client
import json
from pathlib import Path
import re
import secrets
import tempfile
import threading
from types import SimpleNamespace
import unittest
from urllib.parse import urlencode

import access_services
import mobile_service
import operations
import scheduling
from storage import Store


class MobileServiceTests(unittest.TestCase):
    month = "2026-10"
    day = "2026-10-12"
    password = "Test-Only-Vardiya-613!"

    def setUp(self):
        data = tempfile.TemporaryDirectory()
        self.addCleanup(data.cleanup)
        self.path = Path(data.name) / "test.sqlite3"
        self.token = secrets.token_urlsafe(32)
        with Store(self.path) as store:
            worker, colleague = store.add_people(["Mobil Deneme", "Başka Personel"])
            self.employee_id = worker["id"]
            entries = scheduling.normalize_schedule_entries(self.month, [
                {"row_id": "mobile-first", "employee_id": worker["id"], "section": "MARKET",
                 "days": {self.day: {"kind": "work", "start": "08:00", "end": "16:00"}}},
                {"row_id": "mobile-second", "employee_id": colleague["id"], "section": "MARKET",
                 "days": {self.day: {"kind": "work", "start": "17:00", "end": "24:00"}}},
            ], store.people())
            self.revision = store.save_schedule_month(self.month, entries, "")
            operations.publish_in_transaction(store, self.month, self.revision)
            store.save_user("test-admin", self.password, "admin")
            store.save_user("test-viewer", self.password, "viewer")
            now = access_services._now()
            with store.connection:
                store.connection.execute("INSERT INTO portal_tokens VALUES (?,?,?,?,?)",
                    (access_services._token_hash(self.token), worker["id"], now + 3600, None, now))
        stub_tls = SimpleNamespace(wrap_socket=lambda request, **kwargs: request)
        self.server = mobile_service.MobileServer(("127.0.0.1", 0), self.path, "", stub_tls)
        self.port = self.server.server_port
        self.server.origin = f"https://127.0.0.1:{self.port}"
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def exchange(self, path, data=None, cookie="", origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Cookie": cookie}
        try:
            if data is not None:
                headers.update({"Origin": self.server.origin if origin is None else origin,
                                "Content-Type": "application/x-www-form-urlencoded"})
            conn.request("GET" if data is None else "POST", path,
                         None if data is None else urlencode(data), headers)
            reply = conn.getresponse()
            return reply.status, reply.read().decode("utf-8"), reply.getheader("Set-Cookie", "")
        finally:
            conn.close()

    def login(self, route, fields):
        status, page, header = self.exchange(route, fields)
        self.assertEqual(status, 200, page)
        for attribute in ("Secure", "HttpOnly", "SameSite=Strict"):
            self.assertIn(attribute, header)
        return page, header.split(";")[0], re.search(r'name="csrf" value="([^"]+)', page)[1]

    def test_employee_only_sees_own_publication_and_revocation_applies_to_session(self):
        self.assertEqual(self.exchange("/api/people")[0], 404)
        page, cookie, csrf = self.login("/employee-login?mode=phone", {"code": self.token})
        self.assertIn('body class="phone"', page)
        self.assertIn('script nonce=', page)
        self.assertIn("08:00–16:00", page)
        self.assertNotIn("17:00–24:00", page)
        self.assertNotIn("Başka Personel", page)
        self.assertEqual(self.exchange("/session", cookie=cookie)[0], 204)
        self.assertEqual(self.exchange("/request", {"csrf": csrf}, cookie, origin="https://other.invalid")[0], 403)
        self.assertEqual(self.exchange("/edit", {"csrf": csrf}, cookie)[0], 400)
        data = {"csrf": csrf, "kind": "leave", "start_date": self.day, "end_date": self.day, "details": "Deneme"}
        self.assertEqual(self.exchange("/request", data, cookie)[0], 200)
        with Store(self.path) as store, store.connection:
            row = store.connection.execute("SELECT employee_id FROM portal_requests").fetchone()
            self.assertEqual(row[0], self.employee_id)
            store.connection.execute("UPDATE portal_tokens SET revoked_at=1")
        self.assertEqual(self.exchange("/session", cookie=cookie)[0], 401)
        self.assertEqual(self.exchange("/request", data, cookie)[0], 403)
        self.assertNotIn("08:00–16:00", self.exchange("/", cookie=cookie)[1])

    def test_device_sessions_are_per_login_and_revocation_is_isolated(self):
        _, first_cookie, _ = self.login("/employee-login?mode=phone", {"code": self.token})
        _, second_cookie, _ = self.login("/employee-login?mode=phone", {"code": self.token})
        self.assertNotEqual(first_cookie, second_cookie)
        admin = SimpleNamespace(
            current_user={"username": "test-admin"},
            _store=lambda: Store(self.path),
            result=None,
            _json=lambda value, status=200: setattr(admin, "result", (status, value)),
        )
        self.assertTrue(access_services.handle_get(admin, "/api/access/device-sessions", {}))
        sessions = admin.result[1]["sessions"]
        active = [session for session in sessions if session["active"]]
        self.assertEqual(len(active), 2)
        self.assertEqual(len({session["session_id"] for session in active}), 2)
        with Store(self.path) as store:
            revoked_id = store.connection.execute(
                "SELECT session_id FROM portal_device_sessions WHERE session_hash=?",
                (access_services._token_hash(first_cookie.split("=", 1)[1]),),
            ).fetchone()["session_id"]
        revoke = SimpleNamespace(
            _read_json=lambda maximum: {"session_id": revoked_id},
            _store=lambda: Store(self.path),
            _json=lambda value, status=200: setattr(revoke, "result", (status, value)),
            result=None,
        )
        self.assertTrue(access_services.handle_write(revoke, "POST", "/api/access/device-sessions/revoke"))
        self.assertEqual(self.exchange("/session", cookie=first_cookie)[0], 401)
        self.assertEqual(self.exchange("/session", cookie=second_cookie)[0], 204)
        self.assertTrue(access_services.handle_get(admin, "/api/access/device-sessions", {}))
        refreshed = admin.result[1]["sessions"]
        self.assertFalse(next(item for item in refreshed if item["session_id"] == revoked_id)["active"])
        self.assertTrue(next(item for item in refreshed if item["session_id"] != revoked_id)["active"])

    def test_only_admin_edits_one_cell_and_stale_revision_is_refused(self):
        self.assertEqual(self.exchange("/admin-login", {"username": "test-viewer", "password": self.password})[0], 400)
        _, cookie, csrf = self.login("/admin-login?mode=tablet", {"username": "test-admin", "password": self.password})
        status, page, _ = self.exchange(f"/?mode=tablet&day={self.day}", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn('body class="tablet"', page)
        fields = {"csrf": csrf, "day": self.day, "row_id": "mobile-first",
                  "revision": self.revision, "start": "09:00", "end": "16:00"}
        self.assertEqual(self.exchange("/edit", {**fields, "csrf": "wrong"}, cookie)[0], 403)
        status, page, _ = self.exchange("/edit", fields, cookie)
        self.assertEqual(status, 200, page)
        self.assertEqual(self.exchange("/edit", {**fields, "start": "10:00"}, cookie)[0], 400)
        with Store(self.path) as store:
            rows = store.schedule_month(self.month)["entries"]
            self.assertEqual([row["days"][self.day]["start"] for row in rows], ["09:00", "17:00"])
            published = store._setting("published_plans", {})[self.month]["entries"]
            self.assertEqual(published[0]["days"][self.day]["start"], "08:00")
            current = store.schedule_month(self.month)["revision"]
            store.set_period_locked(self.month, True)
        self.assertEqual(self.exchange("/edit", {**fields, "revision": current}, cookie)[0], 400)

    def test_employee_sees_old_and_new_published_hours_and_revision_read_state(self):
        _, cookie, csrf = self.login("/employee-login?mode=phone", {"code": self.token})
        with Store(self.path) as store:
            previous_revision = store._setting("published_plans", {})[self.month]["source_revision"]
            status = access_services._plan_version_status(
                store, access_services._token_hash(self.token), self.employee_id,
            )
        self.assertEqual(status["versions"][0]["viewed_revision"], "")
        self.assertEqual(self.exchange("/plan-read", {
            "csrf": csrf, "month": self.month, "revision": previous_revision,
        }, cookie)[0], 200)
        with Store(self.path) as store, store.connection:
            current = store.schedule_month(self.month)
            entries = current["entries"]
            entries[0]["days"][self.day]["start"] = "09:00"
            new_revision = store.save_schedule_month(self.month, entries, current["revision"])
            operations.publish_in_transaction(store, self.month, new_revision)
            status = access_services._plan_version_status(
                store, access_services._token_hash(self.token), self.employee_id,
            )
        self.assertNotEqual(previous_revision, new_revision)
        self.assertEqual(status["versions"][0]["published_revision"], new_revision)
        self.assertEqual(status["versions"][0]["viewed_revision"], previous_revision)
        page = self.exchange("/", cookie=cookie)[1]
        self.assertIn("Programımdaki son değişiklikler", page)
        self.assertIn("<th>Önceki vardiya</th>", page)
        self.assertIn("<th>Yeni yayınlanan vardiya</th>", page)
        self.assertIn("08:00–16:00", page)
        self.assertIn("09:00–16:00", page)
        self.assertIn("Bu yayın sürümünü okudum", page)
        with Store(self.path) as store:
            status = access_services._plan_version_status(
                store, access_services._token_hash(self.token), self.employee_id,
            )
        self.assertEqual(status["versions"][0]["viewed_revision"], previous_revision)
        self.assertEqual(self.exchange("/plan-read", {
            "csrf": csrf, "month": self.month, "revision": previous_revision,
        }, cookie)[0], 400)
        with Store(self.path) as store:
            status = access_services._plan_version_status(
                store, access_services._token_hash(self.token), self.employee_id,
            )
        self.assertEqual(status["versions"][0]["viewed_revision"], previous_revision)
        self.assertEqual(self.exchange("/plan-read", {
            "csrf": csrf, "month": self.month, "revision": new_revision,
        }, cookie)[0], 200)
        with Store(self.path) as store:
            status = access_services._plan_version_status(
                store, access_services._token_hash(self.token), self.employee_id,
            )
        self.assertEqual(status["versions"][0]["viewed_revision"], new_revision)
        self.assertIsNotNone(status["last_seen_at"])

    def test_mobile_diagnostics_separate_service_address_network_and_certificate(self):
        import ipaddress
        from datetime import datetime, timedelta, timezone
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_path = root / "mobile-https.json"
            self.assertEqual(mobile_service.diagnostics(config_path)["service"]["status"], "info")
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                               critical=False).sign(key, hashes.SHA256()))
            (root / "cert.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            (root / "key.pem").write_bytes(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
            ))
            config_path.write_text(json.dumps({
                "bind": "127.0.0.1", "port": 4443, "origin": "https://127.0.0.1:4443",
                "certificate": "cert.pem", "key": "key.pem",
            }), encoding="utf-8")
            good = mobile_service.diagnostics(config_path)
            self.assertEqual(good["address"]["status"], "ok")
            self.assertEqual(good["certificate"]["status"], "ok")
            self.assertIn("güvenlik duvarı", good["network"]["message"])
            self.assertEqual(good["service"]["status"], "warning")

            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["origin"] = "http://127.0.0.1:4443"
            config["certificate"] = "missing.pem"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            bad = mobile_service.diagnostics(config_path, startup_error="failed")
            self.assertEqual(bad["service"]["status"], "error")
            self.assertEqual(bad["address"]["status"], "error")
            self.assertEqual(bad["certificate"]["status"], "error")
