import base64
import copy
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import data_continuity as continuity
import personnel_assets
import controls
import schedule_protection
import scheduling
from server import create_server
from storage import SCHEMA_VERSION, Store


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = Store(self.root / "source" / "data.sqlite3")
        self.target = Store(self.root / "target" / "data.sqlite3")
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.source.close)
        self.addCleanup(self.target.close)
        self.person = self.source.add_person("Test Personel")
        self.month = "2026-03"

    def schedule(self, person, code="work", start="08:00", end="16:00"):
        return [{
            "row_id": "original-row", "employee_id": person, "section": scheduling.section_names()[0],
            "slot": 1, "days": {"2026-03-02": {"kind": code, "start": start, "end": end}},
        }]

    def save(self, store, entries):
        clean = scheduling.normalize_schedule_entries(self.month, entries, store.people())
        return store.save_schedule_month(self.month, clean, store.schedule_month(self.month)["revision"])

    def test_migration_preview_source_unchanged_apply_and_undo(self):
        self.save(self.source, self.schedule(self.person["id"]))
        with self.source.connection:
            self.source._write_setting("api_token", "never-transfer")
            self.source._write_setting("users", [{"username": "not-imported", "password_hash": "private"}])
            self.source.connection.execute(
                """INSERT INTO personal_calendar_exports
                   (id,employee_id,person_name,month,plan_version,path,filename,sha256,size_bytes,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                ("export-1", self.person["id"], "Test Personel", self.month, "schedule-r1",
                 "C:/exports/person.ics", "person.ics", "a" * 64, 128, "2026-03-01T10:00:00+00:00"),
            )
        before = hashlib.sha256(self.source.path.read_bytes()).hexdigest()
        preview = continuity.migration_preview(self.target, str(self.source.path))
        self.assertTrue(preview["can_apply"])
        self.assertNotIn("api_token", preview["settings"])
        self.assertEqual(self.target.people(), [])
        result = continuity.apply_migration(self.target, preview["preview_id"], True)
        self.assertEqual(self.target.people()[0]["name"], "Test Personel")
        self.assertEqual(self.target.schedule_month(self.month)["entries"][0]["days"]["2026-03-02"]["kind"], "work")
        self.assertEqual(self.target._setting("users", []), [])
        self.assertEqual(self.target._setting("api_token", ""), "")
        self.assertEqual(tuple(self.target.connection.execute(
            "SELECT employee_id,month,plan_version,filename,sha256 FROM personal_calendar_exports",
        ).fetchone()), (self.person["id"], self.month, "schedule-r1", "person.ics", "a" * 64))
        self.assertEqual(hashlib.sha256(self.source.path.read_bytes()).hexdigest(), before)
        continuity.undo_migration(self.target, result["action_id"], True)
        self.assertEqual(self.target.people(), [])
        self.assertEqual(self.target.schedule_month(self.month)["revision"], "")
        self.assertEqual(self.target.connection.execute(
            "SELECT COUNT(*) FROM personal_calendar_exports",
        ).fetchone()[0], 0)
        self.assertEqual(hashlib.sha256(self.source.path.read_bytes()).hexdigest(), before)

    def test_migration_preserves_balance_openings_history_person_links_and_undo(self):
        second = self.source.add_person("İkinci Bakiye Personeli")
        self.source.actor = "Test yöneticisi"
        self.source.save_balance_opening(self.person["id"], 2025, 8, 2)
        self.source.save_balance_opening(self.person["id"], 2025, 12, 4)
        self.source.save_balance_opening(second["id"], 2026, 3, 1)
        self.source.save_balance_opening(second["id"], 2026, 5.5, 2)
        source_hash = hashlib.sha256(self.source.path.read_bytes()).hexdigest()

        preview = continuity.migration_preview(self.target, str(self.source.path))
        self.assertEqual(preview["counts"]["employee_balance_openings"], 2)
        self.assertEqual(preview["counts"]["balance_opening_history"], 2)
        result = continuity.apply_migration(self.target, preview["preview_id"], True)

        target_people = {person["name"]: person["id"] for person in self.target.people()}
        self.assertEqual(target_people["Test Personel"], self.person["id"])
        self.assertEqual(target_people["İkinci Bakiye Personeli"], second["id"])
        self.assertEqual(self.target.balance_openings(2025)[target_people["Test Personel"]], {
            "annual_leave_days": 12.0, "overtime_hours": 4.0,
        })
        self.assertEqual(self.target.balance_openings(2026)[target_people["İkinci Bakiye Personeli"]], {
            "annual_leave_days": 5.5, "overtime_hours": 2.0,
        })
        expected_history = [tuple(row) for row in self.source.connection.execute(
            "SELECT id,employee_id,year,previous_annual_leave_days,new_annual_leave_days,actor,occurred_at "
            "FROM balance_opening_history ORDER BY id",
        )]
        actual_history = [tuple(row) for row in self.target.connection.execute(
            "SELECT id,employee_id,year,previous_annual_leave_days,new_annual_leave_days,actor,occurred_at "
            "FROM balance_opening_history ORDER BY id",
        )]
        self.assertEqual(actual_history, expected_history)
        self.assertEqual(self.target.connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(hashlib.sha256(self.source.path.read_bytes()).hexdigest(), source_hash)

        continuity.undo_migration(self.target, result["action_id"], True)
        self.assertEqual(self.target.people(), [])
        self.assertEqual(self.target.connection.execute(
            "SELECT COUNT(*) FROM employee_balance_openings",
        ).fetchone()[0], 0)
        self.assertEqual(self.target.connection.execute(
            "SELECT COUNT(*) FROM balance_opening_history",
        ).fetchone()[0], 0)
        self.assertEqual(self.target.connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(hashlib.sha256(self.source.path.read_bytes()).hexdigest(), source_hash)

    def test_migration_rejects_balance_history_without_matching_employee_year_opening(self):
        with self.source.connection:
            self.source.connection.execute(
                "INSERT INTO balance_opening_history "
                "(employee_id,year,previous_annual_leave_days,new_annual_leave_days,actor,occurred_at) "
                "VALUES (?,?,?,?,?,?)",
                (self.person["id"], 2025, 8, 10, "Test", "2026-01-01T00:00:00+00:00"),
            )
        with self.assertRaisesRegex(ValueError, "bakiye hareketi"):
            continuity.migration_preview(self.target, str(self.source.path))
        self.assertEqual(self.target.people(), [])

    def test_migration_preserves_temporary_assignment_and_employee_link_on_undo(self):
        assignment = (
            "assignment-test", self.person["id"], "section", scheduling.section_names()[0],
            "2026-03-10", "2026-03-12", "09:00", "17:00", "Geçici destek",
            "2026-03-01T10:00:00+00:00", "Test yöneticisi",
        )
        with self.source.connection:
            self.source.connection.execute(
                "INSERT INTO operations_temporary_assignments "
                "(id,employee_id,location_type,location,start_date,end_date,start_time,end_time,note,created_at,actor) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                assignment,
            )
        preview = continuity.migration_preview(self.target, str(self.source.path))
        self.assertEqual(preview["counts"]["operations_temporary_assignments"], 1)
        result = continuity.apply_migration(self.target, preview["preview_id"], True)
        self.assertEqual(tuple(self.target.connection.execute(
            "SELECT id,employee_id,location_type,location,start_date,end_date,start_time,end_time,note,created_at,actor "
            "FROM operations_temporary_assignments",
        ).fetchone()), assignment)
        self.assertEqual(self.target.people()[0]["id"], assignment[1])

        continuity.undo_migration(self.target, result["action_id"], True)
        self.assertEqual(self.target.connection.execute(
            "SELECT COUNT(*) FROM operations_temporary_assignments",
        ).fetchone()[0], 0)
        self.assertEqual(self.target.people(), [])

    def test_migration_preserves_user_defined_document_retention_policy(self):
        self.save(self.source, self.schedule(self.person["id"]))
        with self.source.connection:
            self.source.connection.execute(
                "INSERT INTO workflow_retention_policy VALUES (1,?,?)",
                (180, "2026-03-01T10:00:00+00:00"),
            )
        preview = continuity.migration_preview(self.target, str(self.source.path))
        self.assertEqual(preview["counts"]["workflow_retention_policy"], 1)
        continuity.apply_migration(self.target, preview["preview_id"], True)
        policy = self.target.connection.execute(
            "SELECT retention_days FROM workflow_retention_policy WHERE id=1",
        ).fetchone()
        self.assertEqual(policy["retention_days"], 180)

    def test_migration_preserves_version_bound_chart_notes_and_undo_keeps_source(self):
        report_version = "manager-v1-0123456789abcdef01234567"
        note_text = "Bu hafta bakım nedeniyle ek vardiya vardı."
        self.source.save_manager_chart_note(self.month, report_version, "worked_hours", note_text)

        preview = continuity.migration_preview(self.target, str(self.source.path))
        self.assertEqual(preview["counts"]["manager_chart_notes"], 1)
        result = continuity.apply_migration(self.target, preview["preview_id"], True)
        expected = {"worked_hours": {
            "text": note_text,
            "updated_at": self.source.manager_chart_notes(self.month, report_version)["worked_hours"]["updated_at"],
        }}
        self.assertEqual(self.target.manager_chart_notes(self.month, report_version), expected)
        self.assertEqual(self.source.manager_chart_notes(self.month, report_version), expected)

        continuity.undo_migration(self.target, result["action_id"], True)
        self.assertEqual(self.target.manager_chart_notes(self.month, report_version), {})
        self.assertEqual(self.source.manager_chart_notes(self.month, report_version), expected)

    def test_migration_needs_empty_target_confirmation_and_fresh_preview(self):
        preview = continuity.migration_preview(self.target, str(self.source.path))
        with self.assertRaises(ValueError):
            continuity.apply_migration(self.target, preview["preview_id"], False)
        self.target.add_person("Mevcut Kayıt")
        with self.assertRaises(ValueError):
            continuity.apply_migration(self.target, preview["preview_id"], True)
        preview = continuity.migration_preview(self.target, str(self.source.path))
        self.assertFalse(preview["can_apply"])
        with self.assertRaises(ValueError):
            continuity.apply_migration(self.target, preview["preview_id"], True)
        self.assertEqual(self.target.people()[0]["name"], "Mevcut Kayıt")

    def test_migration_undo_rejects_later_edits(self):
        preview = continuity.migration_preview(self.target, str(self.source.path))
        result = continuity.apply_migration(self.target, preview["preview_id"], True)
        self.target.add_person("Daha Sonra")
        with self.assertRaises(ValueError):
            continuity.undo_migration(self.target, result["action_id"], True)
        self.assertEqual(len(self.target.people()), 2)

    def test_source_changes_after_preview_and_new_schema_are_not_silently_copied(self):
        preview = continuity.migration_preview(self.target, str(self.source.path))
        self.source.add_person("Yeni Kaynak Personel")
        with self.assertRaisesRegex(ValueError, "Kaynak"):
            continuity.apply_migration(self.target, preview["preview_id"], True)
        self.assertEqual(self.target.people(), [])
        self.source.connection.execute("ALTER TABLE employees ADD COLUMN future_data TEXT")
        self.source.connection.commit()
        with self.assertRaisesRegex(ValueError, "desteklenmeyen"):
            continuity.migration_preview(self.target, str(self.source.path))

    def test_migration_invalid_relationship_rolls_back(self):
        preview = continuity.migration_preview(self.target, str(self.source.path))
        row = self.target.connection.execute(
            "SELECT payload FROM continuity_previews WHERE id=?", (preview["preview_id"],),
        ).fetchone()
        data = json.loads(row["payload"])
        data["tables"]["employee_balance_openings"] = [
            {"employee_id": 9999, "year": 2026, "annual_leave_days": 10, "overtime_hours": 0}
        ]
        with self.target.connection:
            self.target.connection.execute(
                "UPDATE continuity_previews SET payload=? WHERE id=?", (json.dumps(data), preview["preview_id"]),
            )
        with self.assertRaises((ValueError, __import__("sqlite3").IntegrityError)):
            continuity.apply_migration(self.target, preview["preview_id"], True)
        self.assertEqual(self.target.people(), [])

    def test_migration_preserves_protected_cells_and_safe_undo_restores_empty_target(self):
        self.save(self.source, self.schedule(self.person["id"]))
        revision = self.source.schedule_month(self.month)["revision"]
        schedule_protection.set_lock(self.source, {
            "month": self.month, "row_id": "original-row", "date": "2026-03-02",
            "revision": revision, "protected": True, "reason": "Yayımlanmış vardiya",
        })
        personnel_assets.issue_asset(self.source, {
            "action": "issue", "employee_id": self.person["id"], "category": "key",
            "item": "Depo anahtarı", "note": "",
        })
        source_hash = hashlib.sha256(self.source.path.read_bytes()).hexdigest()

        preview = continuity.migration_preview(self.target, str(self.source.path))
        self.assertEqual(preview["counts"]["schedule_protection"], 1)
        self.assertEqual(preview["counts"]["employee_assets"], 1)
        result = continuity.apply_migration(self.target, preview["preview_id"], True)
        self.assertEqual(len(schedule_protection.locks(self.target, self.month)), 1)
        assets = personnel_assets.list_assets(self.target, self.person["id"])
        self.assertEqual([(item["category"], item["item"]) for item in assets], [("key", "Depo anahtarı")])
        with self.assertRaises(sqlite3.IntegrityError):
            self.save(self.target, self.schedule(self.person["id"], start="09:00", end="17:00"))

        continuity.undo_migration(self.target, result["action_id"], True)
        self.assertEqual(self.target.people(), [])
        self.assertEqual(schedule_protection.locks(self.target, self.month), [])
        self.assertEqual(hashlib.sha256(self.source.path.read_bytes()).hexdigest(), source_hash)

    def test_migration_rejects_protection_not_attached_to_a_saved_shift(self):
        self.save(self.source, self.schedule(self.person["id"]))
        with self.source.connection:
            self.source.connection.execute(
                "INSERT INTO schedule_protection "
                "(month,row_id,day,employee_id,section,protected_at,reason,protected_by) VALUES (?,?,?,?,?,?,?,?)",
                (self.month, "original-row", "2026-03-03", self.person["id"],
                 scheduling.section_names()[0], "2026-03-01T00:00:00+00:00", "Aktif", "Yerel kullanıcı"),
            )
        with self.assertRaisesRegex(ValueError, "koruması"):
            continuity.migration_preview(self.target, str(self.source.path))

    def test_template_install_failure_rolls_back_database_and_keeps_source(self):
        preview = continuity.migration_preview(self.target, str(self.source.path))
        source_hash = hashlib.sha256(self.source.path.read_bytes()).hexdigest()
        with patch("data_continuity._write_templates", side_effect=OSError("test file failure")):
            with self.assertRaises(OSError):
                continuity.apply_migration(self.target, preview["preview_id"], True)
        self.assertEqual(self.target.people(), [])
        self.assertEqual(hashlib.sha256(self.source.path.read_bytes()).hexdigest(), source_hash)
        self.assertEqual(self.target.connection.execute("SELECT COUNT(*) FROM continuity_actions").fetchone()[0], 0)

    def test_selected_backup_recovery_identity_lock_and_stale(self):
        person = self.target.add_person("Test Personel")
        self.save(self.target, self.schedule(person["id"]))
        backup = self.target.create_backup("manual")
        self.save(self.target, self.schedule(person["id"], start="09:00", end="17:00"))
        preview = continuity.backup_preview(
            self.target, backup["id"], [{"kind": "schedule", "month": self.month}],
        )
        self.assertTrue(preview["selections"][0]["changed"])
        continuity.backup_apply(self.target, preview["preview_id"], True)
        self.assertEqual(self.target.schedule_month(self.month)["entries"][0]["days"]["2026-03-02"]["start"], "08:00")
        self.target.set_period_locked(self.month, True)
        preview = continuity.backup_preview(
            self.target, backup["id"], [{"kind": "schedule", "month": self.month}],
        )
        with self.assertRaises(ValueError):
            continuity.backup_apply(self.target, preview["preview_id"], True)

    def test_packages_explicit_mapping_idempotency_and_conflict_decision(self):
        person = self.target.add_person("Test Personel")
        self.save(self.source, self.schedule(self.person["id"]))
        package = continuity.export_package(self.source, self.month)
        matching = continuity.matching(self.target, package)
        self.assertEqual(matching["matching"][0]["candidates"][0]["id"], person["id"])
        with self.assertRaises(ValueError):
            continuity.package_preview(self.target, package, {})
        preview = continuity.package_preview(self.target, package, {str(self.person["id"]): person["id"]})
        continuity.package_apply(self.target, preview["preview_id"], {}, True)
        self.assertEqual(self.target.schedule_month(self.month)["entries"][0]["days"]["2026-03-02"]["start"], "08:00")
        with self.assertRaises(ValueError):
            continuity.package_preview(self.target, package, {str(self.person["id"]): person["id"]})
        returned = continuity.export_package(self.target, self.month, package["id"])
        returned["current"]["schedule"][0]["days"]["2026-03-02"]["start"] = "10:00"
        self.save(self.source, self.schedule(self.person["id"], start="09:00", end="17:00"))
        preview = continuity.package_preview(self.source, returned, {str(person["id"]): self.person["id"]})
        self.assertEqual(preview["conflicts"], 1)
        with self.assertRaises(ValueError):
            continuity.package_apply(self.source, preview["preview_id"], {}, True)
        choices = {item["key"]: "local" for item in preview["changes"] if item["conflict"]}
        continuity.package_apply(self.source, preview["preview_id"], choices, True)
        self.assertEqual(self.source.schedule_month(self.month)["entries"][0]["days"]["2026-03-02"]["start"], "09:00")

    def test_encrypted_package_round_trip_wrong_passphrase_and_tampering(self):
        package = continuity.export_package(self.source, self.month)
        encrypted = continuity.encrypt_package(package, "correct horse battery")
        self.assertNotIn(package["people"][0]["name"], repr(encrypted))
        self.assertEqual(continuity.decrypt_package(encrypted, "correct horse battery"), package)
        with self.assertRaisesRegex(ValueError, "Parola yanlış"):
            continuity.decrypt_package(encrypted, "incorrect horse battery")
        damaged = dict(encrypted)
        ciphertext = bytearray(base64.b64decode(damaged["ciphertext"]))
        ciphertext[-1] ^= 1
        damaged["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
        with self.assertRaisesRegex(ValueError, "Parola yanlış"):
            continuity.decrypt_package(damaged, "correct horse battery")
        self.assertEqual(self.source.people()[0]["name"], "Test Personel")
        with self.assertRaisesRegex(ValueError, "12–1024"):
            continuity.encrypt_package(package, "short")

    def test_encrypted_package_http_api_preserves_legacy_package_input(self):
        package = continuity.export_package(self.source, self.month)
        server = create_server(self.source.path, Path(__file__).resolve().parents[1] / "web")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(3)))
        origin = f"http://127.0.0.1:{server.server_address[1]}"

        def post(path, payload):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            try:
                body = json.dumps(payload).encode("utf-8")
                connection.request("POST", path, body, {
                    "Content-Type": "application/json", "Origin": origin, "Sec-Fetch-Site": "same-origin",
                })
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        status, protected = post("/api/continuity/package/encrypt", {
            "package": package, "passphrase": "correct horse battery",
        })
        self.assertEqual(status, 200, protected)
        status, opened = post("/api/continuity/package/decrypt", {
            "envelope": protected["envelope"], "passphrase": "correct horse battery",
        })
        self.assertEqual(status, 200, opened)
        self.assertEqual(opened["package"], package)
        status, failure = post("/api/continuity/package/decrypt", {
            "envelope": protected["envelope"], "passphrase": "incorrect horse battery",
        })
        self.assertEqual(status, 400)
        self.assertIn("Parola yanlış", failure["error"])
        self.assertEqual(continuity.matching(self.target, package)["month"], self.month)
        self.assertEqual(self.target.people(), [])

    def test_encrypted_full_database_backup_http_round_trip_and_refusal(self):
        self.source.add_person("Snapshot Personel")
        server = create_server(self.source.path, Path(__file__).resolve().parents[1] / "web")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(3)))
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        password = "synthetic-safe-backup-passphrase"

        def post_json(path, payload):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            try:
                body = json.dumps(payload).encode("utf-8")
                connection.request("POST", path, body, {
                    "Content-Type": "application/json", "Origin": origin, "Sec-Fetch-Site": "same-origin",
                })
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()

        def post_backup(path, content, preview_id="", passphrase=password, confirm=False):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            headers = {
                "Content-Type": "application/octet-stream", "Origin": origin,
                "Sec-Fetch-Site": "same-origin",
                "X-VP-Backup-Passphrase": base64.b64encode(passphrase.encode("utf-8")).decode("ascii"),
            }
            if preview_id:
                headers["X-VP-Backup-Preview"] = preview_id
            if confirm:
                headers["X-VP-Confirm-Restore"] = "yes"
            try:
                connection.request("POST", path, content, headers)
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()

        status, envelope = post_json("/api/continuity/database-backup/export", {"passphrase": password})
        self.assertEqual(status, 200)
        self.assertTrue(envelope.startswith(continuity._DATABASE_BACKUP_MAGIC))
        before_bad_restore = self.source.people()
        status, failure = post_backup(
            "/api/continuity/database-backup/preview", envelope, passphrase="incorrect-synthetic-password",
        )
        self.assertEqual(status, 400)
        self.assertIn("Parola yanlış", json.loads(failure)["error"])
        self.assertEqual(self.source.people(), before_bad_restore)

        status, preview_body = post_backup(
            "/api/continuity/database-backup/preview", envelope[:-1] + bytes([envelope[-1] ^ 1]),
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.source.people(), before_bad_restore)

        status, preview_body = post_backup("/api/continuity/database-backup/preview", envelope)
        self.assertEqual(status, 200, preview_body)
        preview = json.loads(preview_body)
        self.assertEqual(preview["metadata"]["row_counts"]["employees"], 2)
        self.source.add_person("Daha Sonra Eklenen")
        current_people = self.source.people()
        status, failure = post_backup(
            "/api/continuity/database-backup/restore", envelope, preview["preview_id"], confirm=True,
        )
        self.assertEqual(status, 400)
        self.assertIn("değişti", json.loads(failure)["error"])
        self.assertEqual(self.source.people(), current_people)

        status, preview_body = post_backup("/api/continuity/database-backup/preview", envelope)
        self.assertEqual(status, 200)
        preview = json.loads(preview_body)
        status, restored_body = post_backup(
            "/api/continuity/database-backup/restore", envelope, preview["preview_id"], confirm=True,
        )
        self.assertEqual(status, 200, restored_body)
        restored = json.loads(restored_body)
        self.assertEqual(restored["restored"]["row_counts"]["employees"], 2)
        self.assertEqual([person["name"] for person in self.source.people()], ["Snapshot Personel", "Test Personel"])
        self.assertTrue(restored["safety_backup"]["id"])

    def test_newer_database_schema_is_refused_without_modifying_database(self):
        path = self.root / "future-schema.sqlite3"
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE future_schema_marker (value TEXT)")
            connection.execute("INSERT INTO future_schema_marker VALUES ('preserve')")
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, "daha yenidir"):
            Store(path)
        connection = sqlite3.connect(path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION + 1)
            self.assertEqual(connection.execute("SELECT value FROM future_schema_marker").fetchone()[0], "preserve")
        finally:
            connection.close()

    def test_encrypted_legacy_schema_backup_restores_and_migrates_forward(self):
        legacy_path = self.root / "legacy-schema.sqlite3"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript("""
                CREATE TABLE employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE schedule_months (
                    period TEXT PRIMARY KEY, payload TEXT NOT NULL, revision TEXT NOT NULL
                );
                CREATE TABLE attendance_months (
                    period TEXT PRIMARY KEY, payload TEXT NOT NULL, revision TEXT NOT NULL
                );
                CREATE TABLE app_settings (name TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE attendance_events (
                    id TEXT PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
                    kind TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    document TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                INSERT INTO employees(name, active) VALUES ('Test Personel', 1);
                PRAGMA user_version=0;
            """)
        finally:
            connection.close()
        envelope = continuity.encrypt_database_file(
            legacy_path, "synthetic-legacy-backup-passphrase",
        )
        snapshot = self.root / "legacy-decrypted.sqlite3"
        restored = continuity.decrypt_database_file(
            envelope, "synthetic-legacy-backup-passphrase", snapshot,
        )
        self.assertEqual(restored["schema_version"], 0)

        backup_id = "manual-20260410T080000Z-abcdef12.sqlite3"
        self.target.backup_directory.mkdir(parents=True, exist_ok=True)
        backup_path = self.target._backup_path(backup_id)
        backup_path.write_bytes(snapshot.read_bytes())
        try:
            result = self.target.restore_backup(backup_id)
        finally:
            backup_path.unlink(missing_ok=True)
        self.assertTrue(result["safety_backup"]["id"])
        self.assertEqual([person["name"] for person in self.target.people()], ["Test Personel"])
        self.assertEqual(
            self.target.connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )

    def test_package_attendance_remap_and_unknown_person_rejected(self):
        unused = self.target.add_person("Başka Personel")
        person = self.target.add_person("Test Personel")
        with self.source.connection:
            self.source.connection.execute(
                "INSERT INTO attendance_months VALUES (?,?,?)", (
                    self.month, json.dumps({"overrides": {"2026-03-02": {str(self.person["id"]): "R"}},
                                            "deductions": {}, "overtime": {}, "roster": {"added": [self.person["id"]]}}),
                    "attendance-source",
                ),
            )
        package = continuity.export_package(self.source, self.month)
        preview = continuity.package_preview(self.target, package, {str(self.person["id"]): person["id"]})
        continuity.package_apply(self.target, preview["preview_id"], {}, True)
        self.assertEqual(self.target.attendance_month(self.month)["overrides"], {"2026-03-02": {str(person["id"]): "R"}})
        self.assertEqual(self.target.attendance_month(self.month)["roster"]["added"], [person["id"]])
        self.assertNotEqual(person["id"], unused["id"])
        corrupt = copy.deepcopy(package)
        corrupt["current"]["attendance"]["overrides"]["2026-03-02"]["99999"] = "X"
        with self.assertRaises(ValueError):
            continuity.matching(self.target, corrupt)

    def test_package_row_deletion_is_reviewed_and_newer_target_is_preserved(self):
        person = self.target.add_person("Test Personel")
        self.save(self.source, self.schedule(self.person["id"]))
        package = continuity.export_package(self.source, self.month)
        preview = continuity.package_preview(self.target, package, {str(self.person["id"]): person["id"]})
        continuity.package_apply(self.target, preview["preview_id"], {}, True)
        returned = continuity.export_package(self.target, self.month, package["id"])
        returned["current"]["schedule"] = []
        preview = continuity.package_preview(self.source, returned, {str(person["id"]): self.person["id"]})
        self.assertTrue(any(item["cell"][0] == "row" and item["after"] is None for item in preview["changes"]))
        continuity.package_apply(self.source, preview["preview_id"], {}, True)
        self.assertEqual(self.source.schedule_month(self.month)["entries"], [])

    def test_duplicate_registry_review_never_merges_people(self):
        first = self.target.add_person("Birinci Kişi")
        second = self.target.add_person("İkinci Kişi")
        self.target.save_employee_profile(first["id"], {"payroll_code": "S-001"})
        self.target.save_employee_profile(first["id"], {"home_section": "MARKET", "skills": ["Kasa"]})
        self.target.save_employee_profile(second["id"], {
            "payroll_code": "S-001", "home_section": "MARKET", "skills": ["Kasa"],
        })
        review = continuity.duplicate_review(self.target)
        self.assertEqual(len(review["groups"]), 1)
        self.assertEqual(review["groups"][0]["kind"], "registry")
        self.assertEqual(review["groups"][0]["people"][0]["section"], "MARKET")
        decision = continuity.record_identity_decision(self.target, {
            "employee_id": first["id"], "candidate_id": second["id"],
            "decision": "same", "reason": "Sicil ve görev bilgisi doğrulandı.",
        })
        self.assertEqual(decision["decision"], "same")
        self.assertEqual(decision["employee_registry"], "S-001")
        self.assertEqual(continuity.duplicate_review(self.target)["history"][0]["reason"],
                         "Sicil ve görev bilgisi doğrulandı.")
        self.assertEqual(len(self.target.people()), 2)

    def test_pdks_requires_explicit_matching_and_never_changes_payroll(self):
        person = self.target.add_person("Test Personel")
        content = base64.b64encode(
            "Ad Soyad;Tarih;Giriş;Çıkış\nCihaz Personeli;02.03.2026;08:00;16:00\n".encode("utf-8-sig")
        ).decode("ascii")
        matching = continuity.pdks_preview(self.target, self.month, "pdks.csv", content, {})
        self.assertEqual(len(matching["matching"]), 1)
        preview = continuity.pdks_preview(
            self.target, self.month, "pdks.csv", content, {"Cihaz Personeli": person["id"]},
        )
        before = self.target.attendance_month(self.month)
        continuity.pdks_apply(self.target, preview["preview_id"], True)
        self.assertEqual(self.target.attendance_month(self.month), before)
        self.assertEqual(self.target._setting("pdks_records", {})[self.month]["records"][0]["name"], "Test Personel")

    def test_pdks_health_reads_raw_rows_before_grouping_without_changing_registers(self):
        ada = self.target.add_person("Ada Deneme")
        bora = self.target.add_person("Bora Deneme")
        content = base64.b64encode((
            "Ad Soyad;Tarih;Giriş;Çıkış\n"
            "Cihaz Ada;02.03.2026;22:00;00:00\n"
            "Cihaz Ada;02.03.2026;22:00;00:00\n"
            "Cihaz Ada;03.03.2026;08:00;\n"
            "Cihaz Bora;03.03.2026;;17:00\n"
            "Cihaz Bora;01.04.2026;08:00;\n"
        ).encode("utf-8-sig")).decode("ascii")
        before = (self.target.attendance_month(self.month), self.target.schedule_month(self.month))
        matching = continuity.pdks_preview(self.target, self.month, "pdks.csv", content, {})
        health = matching["source_health"]
        self.assertEqual(health["counts"], {"duplicate": 1, "missing": 2, "midnight": 2})
        self.assertEqual(health["missing_clusters"], [{"date": "2026-03-03", "people": 2}])
        self.assertEqual(next(row for row in health["findings"] if row["kind"] == "duplicate")["row"], 3)
        self.assertEqual(self.target._setting("pdks_records", {}), {})
        preview = continuity.pdks_preview(self.target, self.month, "pdks.csv", content,
                                          {"Cihaz Ada": ada["id"], "Cihaz Bora": bora["id"]})
        self.assertEqual(len(preview["records"]), 3)
        self.assertEqual(next(row for row in preview["records"] if row["name"] == "Ada Deneme"
                              and row["date"] == "2026-03-03")["out"], "")
        continuity.pdks_apply(self.target, preview["preview_id"], True)
        self.assertEqual(self.target._setting("pdks_records", {})[self.month]["source_health"], health)
        self.assertEqual((self.target.attendance_month(self.month), self.target.schedule_month(self.month)), before)

    def test_pdks_single_punch_health_does_not_invent_an_exit(self):
        content = base64.b64encode((
            "Ad Soyad;Tarih;Saat\n"
            "Cihaz Ada;02.03.2026;00:00\n"
            "Cihaz Bora;02.03.2026;08:00\n"
        ).encode("utf-8-sig")).decode("ascii")
        people = self.target.add_people(["Ada Deneme", "Bora Deneme"])
        mapping = {"Cihaz Ada": people[0]["id"], "Cihaz Bora": people[1]["id"]}
        result = continuity.pdks_preview(self.target, self.month, "pdks.csv", content, mapping)
        self.assertEqual(result["source_health"]["counts"], {"duplicate": 0, "missing": 2, "midnight": 1})
        self.assertEqual([row["out"] for row in result["records"]], ["", ""])

    def test_pdks_multi_punch_timeline_keeps_all_readings_and_leaves_unpaired_exit_empty(self):
        person = self.target.add_person("Ada Deneme")
        content = base64.b64encode((
            "Ad Soyad;Tarih;Saat\n"
            "Cihaz Ada;02.03.2026;08:00\n"
            "Cihaz Ada;02.03.2026;12:00\n"
            "Cihaz Ada;02.03.2026;13:00\n"
            "Cihaz Ada;02.03.2026;17:00\n"
            "Cihaz Ada;02.03.2026;18:00\n"
        ).encode("utf-8-sig")).decode("ascii")
        before = (self.target.attendance_month(self.month), self.target.schedule_month(self.month))
        preview = continuity.pdks_preview(
            self.target, self.month, "pdks.csv", content, {"Cihaz Ada": person["id"]},
        )
        record = preview["records"][0]
        self.assertEqual(
            [item["time"] for item in record["punches"]],
            ["08:00", "12:00", "13:00", "17:00", "18:00"],
        )
        self.assertEqual([item["row"] for item in record["punches"]], [2, 3, 4, 5, 6])
        self.assertEqual(
            [(item["in"], item["out"]) for item in record["pairings"]],
            [("08:00", "12:00"), ("13:00", "17:00"), ("18:00", "")],
        )
        self.assertEqual(record["pairings"][-1]["rows"], [6])
        self.assertEqual((self.target.attendance_month(self.month), self.target.schedule_month(self.month)), before)
        continuity.pdks_apply(self.target, preview["preview_id"], True)
        saved = self.target._setting("pdks_records", {})[self.month]["records"][0]
        self.assertEqual(saved["punches"], record["punches"])
        self.assertEqual(saved["pairings"], record["pairings"])

    def test_pdks_similar_schedule_offsets_suggest_clock_review_without_changing_punches(self):
        people = [{"id": employee_id, "name": f"Person {employee_id}"} for employee_id in range(1, 4)]
        days = ["2026-03-02", "2026-03-03", "2026-03-04"]
        entries = [
            {"employee_id": employee_id, "days": {
                day: {"kind": "work", "start": "08:00", "end": "16:00"},
            }}
            for employee_id, day in zip(range(1, 4), days)
        ]
        records = [
            {"name": f"Person {employee_id}", "date": day, "in": "08:15", "out": "16:15"}
            for employee_id, day in zip(range(1, 4), days)
        ]
        original_records = [dict(record) for record in records]
        result = controls.pdks_compare(self.month, entries, people, records)
        self.assertEqual(result["clock_drift_review"], {
            "recommended": True,
            "matching_records": 3,
            "approximate_offset_minutes": 15,
            "range_minutes": [15, 15],
        })
        self.assertEqual(records, original_records)

        mixed = [dict(record, **{"in": start, "out": end}) for record, start, end in zip(
            records, ("08:15", "08:45", "09:30"), ("16:15", "16:45", "17:30"),
        )]
        self.assertFalse(controls.pdks_compare(
            self.month, entries, people, mixed,
        )["clock_drift_review"]["recommended"])

        self.assertFalse(controls.pdks_compare(
            self.month, entries[:2], people[:2], records[:2],
        )["clock_drift_review"]["recommended"])


if __name__ == "__main__":
    unittest.main()
