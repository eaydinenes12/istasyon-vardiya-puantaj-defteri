import re
import unittest
from pathlib import Path

import reports

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def _week(start: str = "07:00", end: str = "15:00", days=range(5, 11)):
    return [{
        "employee_id": 1,
        "days": {f"2026-10-{day:02d}": {"kind": "work", "start": start, "end": end} for day in days},
    }]


class WeeklyLegalLimitTests(unittest.TestCase):
    def _weekly(self, entries, **kwargs):
        return [item for item in reports.legal_limit_items("2026-10", entries, {1: "Ayşe"}, {}, **kwargs)
                if item["type"] == "legal_weekly"]

    def test_configured_break_is_deducted_from_weekly_limit(self):
        self.assertEqual(self._weekly(_week(), break_rules=[[7.5, 60]]), [])

    def test_gross_time_still_alerts_without_break_rules(self):
        items = self._weekly(_week())
        self.assertEqual(len(items), 1)
        self.assertIn("net 48 saat", items[0]["message"])
        self.assertIn("mola kuralı tanımlı değil", items[0]["message"])

    def test_break_exempt_person_uses_gross_time(self):
        items = self._weekly(_week(), break_rules=[[7.5, 60]], person_rules={1: {"break_exempt": True}})
        self.assertEqual(len(items), 1)

    def test_net_time_over_limit_reports_gross_too(self):
        items = self._weekly(_week("07:00", "16:00"), break_rules=[[7.5, 60]])
        self.assertEqual(len(items), 1)
        self.assertIn("net 48 saat", items[0]["message"])
        self.assertIn("brüt 54 saat", items[0]["message"])


class TemplateEditorNavTests(unittest.TestCase):
    def test_template_editor_does_not_insert_nav_before_nested_guide_button(self):
        source = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"insertBefore\(\s*nav\s*,\s*el\(\s*[\"']#open-guide[\"']\s*\)\s*\)")


class MenuClarityTests(unittest.TestCase):
    def test_hub_tabs_do_not_reuse_the_icon_of_a_main_view(self):
        source = (WEB / "easy-hubs.js").read_text(encoding="utf-8")
        hub_block = source[source.index("const HUB_INFO"):source.index("const VIEW_ICONS")]
        hub_icons = set(re.findall(r'icon: "([a-z]+)"', hub_block))
        view_icons = set(re.findall(r'"([a-z]+)"', re.search(r"const VIEW_ICONS = \{([^}]*)\}", source).group(1)))
        self.assertFalse(hub_icons & view_icons)
        svg_block = source[source.index("const SVG"):source.index("const HUB_INFO")]
        for icon in hub_icons:
            self.assertRegex(svg_block, rf"\n\s+{icon}: '")

    def test_duplicate_tool_entries_are_removed(self):
        menu = "\n".join((WEB / name).read_text(encoding="utf-8")
                         for name in ("easy-mode.js", "easy-assistant.js", "data-continuity.js"))
        self.assertNotIn('["Detaylı başlangıç rehberi"', menu)
        self.assertNotIn('["Kısayol halkasını aç", ', menu)
        self.assertIn('["Kısayol halkasını aç / kapat"', menu)
        self.assertNotIn('"PDKS eşleştirme ve mutabakat"', menu)

    def test_overflowing_tabs_move_into_more_menu(self):
        source = (WEB / "easy-menu.js").read_text(encoding="utf-8")
        self.assertIn("easy-nav-overflow", source)
        self.assertIn("Daha fazla", source)


class LocalApiGuardTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        import threading
        from server import create_server
        from storage import Store

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "guard.sqlite3"
        with Store(path):
            pass
        self.server = create_server(path, WEB)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), thread.join(3)))
        self.port = self.server.server_address[1]

    def send(self, method, path, headers, body=None):
        import http.client
        import json
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
            connection.request(method, path, data, headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_same_origin_json_request_is_accepted(self):
        status, _ = self.send("POST", "/api/people", {
            "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}",
            "Sec-Fetch-Site": "same-origin"}, {"name": "Koruma Deneme"})
        self.assertEqual(status, 201)

    def test_foreign_origin_and_rebinding_host_are_rejected(self):
        status, _ = self.send("POST", "/api/people", {
            "Content-Type": "application/json", "Origin": "http://evil.example"}, {"name": "X"})
        self.assertEqual(status, 403)
        status, _ = self.send("POST", "/api/people", {
            "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port + 1}"}, {"name": "X"})
        self.assertEqual(status, 403)
        status, _ = self.send("GET", "/api/people", {"Host": f"evil.example:{self.port}"})
        self.assertEqual(status, 403)
        status, _ = self.send("POST", "/api/people", {
            "Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"}, {"name": "X"})
        self.assertEqual(status, 403)

    def test_browser_form_post_is_rejected(self):
        status, _ = self.send("POST", "/api/people", {
            "Content-Type": "text/plain", "Origin": f"http://localhost:{self.port}"}, b'{"name": "X"}')
        self.assertEqual(status, 415)

    def test_local_get_still_works(self):
        status, _ = self.send("GET", "/api/people", {"Host": f"localhost:{self.port}"})
        self.assertEqual(status, 200)


class ScheduleGridTidyTests(unittest.TestCase):
    def test_stray_dot_in_a4_is_hidden_in_ui_template_and_export(self):
        import tempfile
        from openpyxl import load_workbook
        import scheduling

        template = scheduling.workbook_template("schedule", 0)
        self.assertIsNone(template["cells"][3][0]["value"])
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "plan.xlsx"
            scheduling.export_schedule_xlsx(target, "2026-10", [], [])
            workbook = load_workbook(target)
            for sheet in workbook.worksheets:
                self.assertIsNone(sheet["A4"].value, sheet.title)

    def test_row_header_marks_hidden_rows_above(self):
        source = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("data-hidden-before", source)
        self.assertIn(".excel-row-header[data-hidden-before]", (WEB / "app.css").read_text(encoding="utf-8"))


class CloudSyncNoticeTests(unittest.TestCase):
    def test_similar_database_is_reported_not_opened_or_merged(self):
        import tempfile
        import health
        from storage import Store

        with tempfile.TemporaryDirectory() as folder, Store(Path(folder) / "OneDrive" / "db.sqlite3") as store:
            copy = store.path.with_name("db-PC2.sqlite3")
            copy.write_bytes(b"never read as a database")
            store.path.with_name("unrelated.sqlite3").touch()
            store.path.with_name("db.sqlite3-wal-copy").touch()
            alerts = [item for item in health.check(store)["issues"] if item["code"] == "possible-sync-copy"]
            self.assertEqual(len(alerts), 1)
            self.assertEqual((alerts[0]["count"], alerts[0]["repairable"]), (1, False))
            self.assertEqual(copy.read_bytes(), b"never read as a database")

    def test_locked_temp_database_and_conflict_copy_show_safe_guidance_without_merging(self):
        import os
        import sqlite3
        import tempfile
        from unittest import mock
        import health
        from storage import Store

        with tempfile.TemporaryDirectory() as folder:
            synced = Path(folder) / "OneDrive"
            database = synced / "vardiya_puantaj.sqlite3"
            copy = synced / "vardiya_puantaj-PC2.sqlite3"
            copy_bytes = b"synthetic conflict copy; do not open as SQLite"
            copy.parent.mkdir(parents=True)
            copy.write_bytes(copy_bytes)
            with mock.patch.dict(os.environ, {"OneDrive": str(synced)}), Store(database) as store:
                store.connection.execute("BEGIN IMMEDIATE")
                contender = sqlite3.connect(database, timeout=0)
                try:
                    with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                        contender.execute("BEGIN IMMEDIATE")
                finally:
                    contender.close()
                try:
                    result = health.check(store)
                finally:
                    store.connection.rollback()

            issues = {item["code"]: item for item in result["issues"]}
            self.assertIn("possible-sync-copy", issues)
            self.assertIn("Dosyaları birleştirmeyin veya silmeyin", issues["possible-sync-copy"]["message"])
            self.assertIn("normal şekilde kapattıktan sonra", issues["possible-sync-copy"]["message"])
            self.assertIn("cloud-sync", issues)
            self.assertIn("kilidi tek başına eşitleme hatası anlamına gelmez", issues["cloud-sync"]["message"])
            self.assertEqual(copy.read_bytes(), copy_bytes)

    def test_onedrive_database_gets_info_notice_until_external_copy_is_set(self):
        import os
        import tempfile
        from unittest import mock
        import health
        from storage import Store

        with tempfile.TemporaryDirectory() as folder:
            synced = Path(folder) / "OneDrive"
            outside = Path(folder) / "Yerel" / "Yedek"
            (synced / "data").mkdir(parents=True)
            with mock.patch.dict(os.environ, {"OneDrive": str(synced)}), Store(synced / "data" / "db.sqlite3") as store:
                result = health.check(store)
                notice = [item for item in result["issues"] if item["code"] == "cloud-sync"]
                self.assertEqual([item["level"] for item in notice], ["info"])
                self.assertFalse(notice[0]["repairable"])
                self.assertEqual(result["status"], "warning")  # no-backup warning only
                store.save_backup_mirror_folder(str(synced / "Yedek"))
                self.assertTrue(any(item["code"] == "cloud-sync" for item in health.check(store)["issues"]))
                store.save_backup_mirror_folder(str(outside))
                self.assertFalse(any(item["code"] == "cloud-sync" for item in health.check(store)["issues"]))
                self.assertEqual(health.cloud_sync_root(store.local_mirror_folder()), "")

    def test_info_only_health_stays_ok(self):
        import tempfile
        from unittest import mock
        import health
        from storage import Store

        with tempfile.TemporaryDirectory() as folder, Store(Path(folder) / "OneDrive" / "db.sqlite3") as store:
            store.create_backup()
            with mock.patch.dict("os.environ", {"OneDrive": str(Path(folder) / "OneDrive")}):
                result = health.check(store)
        self.assertEqual(result["status"], "ok")
        self.assertEqual([item["code"] for item in result["issues"]], ["cloud-sync"])


class AttendanceMissingPeopleTests(unittest.TestCase):
    def test_missing_people_respects_removed_and_employment_dates(self):
        import json
        import tempfile
        import threading
        import urllib.request
        from server import create_server
        from storage import Store

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing.sqlite3"
            with Store(path) as store:
                ids = {name: store.add_person(name)["id"] for name in ("Ali", "Banu", "Cem", "Derya")}
                store.save_employee_profile(ids["Cem"], {"hire_date": "2026-11-05"})
                store.save_employee_profile(ids["Derya"], {"exit_date": "2026-09-30"})
                current = store.attendance_month("2026-10")
                store.save_attendance_month("2026-10", {}, {}, current["revision"],
                                            {"added": [], "removed": [ids["Banu"]], "order": []}, {})
            server = create_server(path, WEB)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                with urllib.request.urlopen(f"{base}/api/attendance?month=2026-10", timeout=10) as response:
                    data = json.loads(response.read())
                self.assertEqual([item["name"] for item in data["missing_people"]], ["Ali"])
                body = json.dumps({"month": "2026-10", "revision": data["revision"], "overrides": {}, "deductions": {},
                                   "overtime": {}, "roster": {"added": [ids["Ali"]], "removed": [ids["Banu"]], "order": []}})
                request = urllib.request.Request(f"{base}/api/attendance", body.encode(), method="PUT",
                                                 headers={"Content-Type": "application/json"})
                urllib.request.urlopen(request, timeout=10).close()
                with urllib.request.urlopen(f"{base}/api/attendance?month=2026-10", timeout=10) as response:
                    data = json.loads(response.read())
                self.assertEqual(data["missing_people"], [])
                self.assertIn(ids["Ali"], [row["employee_id"] for row in data["attendance"]["rows"]])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(3)

    def test_attendance_legend_lists_all_standard_codes(self):
        import scheduling
        html = (WEB / "index.html").read_text(encoding="utf-8")
        footer = html[html.index('class="table-footer attendance-footer"'):html.index("attendance-calendar-footer")]
        for code in scheduling.ATTENDANCE_LEGEND_SLOTS:
            self.assertIn(f"<b>{code}</b>", footer)


class DocumentPresetSettingsTests(unittest.TestCase):
    def test_document_presets_are_validated_and_cleaned(self):
        import ui_settings
        result = ui_settings.normalize({"document_presets": [
            {"name": "  Geç  kalma ", "title": "Geç kalma tutanağı", "code": "", "section": "MARKET",
             "description": " Saat 09:20'de geldi.\nSözlü uyarı yapıldı. "},
            {"name": "Devamsızlık", "code": "G"},
        ]})
        self.assertEqual(result["document_presets"][0], {
            "name": "Geç kalma", "title": "Geç kalma tutanağı", "code": "", "section": "MARKET",
            "description": "Saat 09:20'de geldi.\nSözlü uyarı yapıldı.",
        })
        self.assertEqual(result["document_presets"][1]["title"], "")
        self.assertEqual(ui_settings.normalize({})["document_presets"], [])
        invalid = (
            [{"name": "A"}, {"name": "a"}],
            [{"name": ""}],
            [{"name": "A", "code": "X"}],
            [{"name": "A", "extra": 1}],
            [{"name": "A", "description": "x" * 501}],
            [{"name": f"P{index}"} for index in range(31)],
        )
        for presets in invalid:
            with self.subTest(presets=presets[:2]), self.assertRaises(ValueError):
                ui_settings.normalize({"document_presets": presets})

    def test_document_dialog_supports_single_record_edits_and_presets(self):
        script = (WEB / "document-workflows.js").read_text(encoding="utf-8")
        self.assertIn("async function openSelection(cells, options = {})", script)
        for marker in ('id="easy-document-date"', 'id="easy-document-code"', 'id="easy-document-title"',
                       'id="easy-document-preset-save"', "title: draft.title.trim()", "document_presets: presets"):
            self.assertIn(marker, script)
        tools = (WEB / "table-tools.js").read_text(encoding="utf-8")
        self.assertIn('{ editable: true, onBack: ttOpenPersonnel, backLabel: "← Personel listesine dön" }', tools)


class LeaveAndPersonnelFlowTests(unittest.TestCase):
    def test_leave_dialog_can_approve_immediately_and_return_to_list(self):
        script = (WEB / "linked-leave.js").read_text(encoding="utf-8")
        self.assertIn("data-linked-submit-approve", script)
        self.assertIn("context.onBack", script)
        self.assertNotIn('namedItem("employee_id")', script)
        tools = (WEB / "table-tools.js").read_text(encoding="utf-8")
        self.assertIn("onBack: ttOpenPersonnel", tools)
        self.assertGreaterEqual(tools.count("dialog.ttListeners = new AbortController()"), 3)

    def test_personnel_home_uses_data_person_and_reports_card_gaps(self):
        import json
        import tempfile
        import threading
        import urllib.request
        from server import create_server
        from storage import Store

        extras = (WEB / "extras.js").read_text(encoding="utf-8")
        self.assertIn("Number(button.dataset.person || button.dataset.personnelHomeDocument)", extras)
        self.assertIn('data-personnel-home-action="section"', extras)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "card.sqlite3"
            with Store(path) as store:
                full = store.add_person("Tam Kart")["id"]
                incomplete = store.add_person("Eksik Kart")["id"]
                store.save_employee_profile(full, {"title": "Kasiyer", "phone": "05550000000", "hire_date": "2025-01-02",
                                                   "payroll_code": "S-1", "home_section": "MARKET", "skills": ["Kasa"],
                                                   "duty_required_fields": ["skills"]})
                store.save_employee_profile(incomplete, {
                    "title": "Geçici kasiyer", "duty_required_fields": ["documents", "home_section"],
                })
                for personal_field in ("phone", "birth_date", "monthly_wage"):
                    with self.assertRaises(ValueError):
                        store.save_employee_profile(full, {"duty_required_fields": [personal_field]})
            server = create_server(path, WEB)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/api/personnel-overview"
                with urllib.request.urlopen(url, timeout=10) as response:
                    people = {item["name"]: item for item in json.loads(response.read())["people"]}
            finally:
                server.shutdown()
                server.server_close()
                thread.join(3)
        self.assertEqual(people["Tam Kart"]["card_missing"], [])
        self.assertEqual(people["Eksik Kart"]["card_missing"], ["Belge", "Bölüm"])
        self.assertEqual(people["Tam Kart"]["duty_required_fields"], ["skills"])
        self.assertEqual(people["Eksik Kart"]["duty_required_fields"], ["documents", "home_section"])
        self.assertEqual(people["Tam Kart"]["card_required_count"], 2)
        self.assertEqual(people["Eksik Kart"]["card_required_count"], 3)
        for person in people.values():
            self.assertNotIn("Telefon", person["card_missing"])
            self.assertNotIn("Doğum tarihi", person["card_missing"])
            self.assertNotIn("Ücret", person["card_missing"])


class ReadyTemplateLibraryTests(unittest.TestCase):
    def test_ready_templates_are_valid_format_only_sheets(self):
        import scheduling
        import ui_settings

        ready = ui_settings.READY_LIBRARY
        self.assertGreaterEqual(sum(item["kind"] == "schedule" for item in ready), 2)
        self.assertGreaterEqual(sum(item["kind"] == "attendance" for item in ready), 2)
        for item in ready:
            self.assertTrue(item["description"])
            for index, sheet in item["sheets"].items():
                self.assertFalse({"insert_rows", "insert_cols", "delete_rows", "delete_cols", "merges_add",
                                  "merges_remove", "extra_rows", "extra_cols"} & set(sheet), item["name"])
                self.assertFalse(any("text" in cell for cell in sheet["cells"].values()), item["name"])
                key = f"{item['kind']}:template:{index}"
                scheduling.normalize_workbook_settings({"sheets": {key: sheet}, "quick_colors": {}})
        copies = [{"name": item["name"], "kind": item["kind"], "sheets": item["sheets"],
                   "created": "2026-11-19T10:00:00"} for item in ready]
        self.assertEqual(len(ui_settings.normalize_library(copies)), len(ready))

    def test_library_endpoint_returns_ready_templates_and_ui_offers_copy(self):
        import json
        import tempfile
        import threading
        import urllib.request
        from server import create_server
        from storage import Store

        tools = (WEB / "table-tools.js").read_text(encoding="utf-8")
        self.assertIn('data-ready="apply"', tools)
        self.assertIn('data-ready="copy"', tools)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "library.sqlite3"
            with Store(path):
                pass
            server = create_server(path, WEB)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/api/template-library"
                with urllib.request.urlopen(url, timeout=10) as response:
                    payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(3)
        self.assertEqual(payload["items"], [])
        self.assertEqual([item["name"] for item in payload["ready"]],
                         [item["name"] for item in __import__("ui_settings").READY_LIBRARY])


class MonthEndCheckTests(unittest.TestCase):
    def _get(self, server, path):
        import json
        import urllib.error
        import urllib.request

        url = f"http://127.0.0.1:{server.server_address[1]}{path}"
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_month_end_check_reports_steps_and_lock_state(self):
        import json
        import tempfile
        import threading
        from server import create_server
        from storage import Store

        month = "2020-01"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "month-end.sqlite3"
            with Store(path) as store:
                ayse = store.add_person("Ayşe Kaya")["id"]
                mehmet = store.add_person("Mehmet Can")["id"]
                store.save_schedule_month(month, [{
                    "row_id": "row-a", "employee_id": ayse, "section": "MARKET", "slot": 1,
                    "days": {"2020-01-02": {"kind": "work", "start": "07:00", "end": "15:00"}},
                }], "")
                store.connection.execute(
                    "INSERT INTO linked_leave_requests (id, employee_id, pattern, start_date, end_date, weekdays, "
                    "kind, status, note, revision, created_at, updated_at) "
                    "VALUES ('req1', ?, 'range', '2020-01-10', '2020-01-12', ?, 'annual_leave', 'pending', '', 1, "
                    "'2020-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00')",
                    (mehmet, json.dumps([])),
                )
                store.connection.commit()
            server = create_server(path, WEB)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, first = self._get(server, f"/api/month-end-check?month={month}")
                with Store(path) as store:
                    store.set_period_locked(month, True)
                _, locked = self._get(server, f"/api/month-end-check?month={month}")
                bad_status, bad = self._get(server, "/api/month-end-check?month=2020-13")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(3)

        self.assertEqual(status, 200)
        steps = {item["id"]: item for item in first["steps"]}
        self.assertEqual(list(steps), ["health", "schedule", "attendance-people", "attendance-cells",
                                       "leave", "backup", "lock"])
        self.assertEqual(steps["schedule"]["status"], "warning")
        self.assertIn("30 günde", steps["schedule"]["detail"])
        self.assertLessEqual(len(steps["schedule"]["items"]), 12)
        self.assertEqual(steps["attendance-people"]["items"], ["Mehmet Can"])
        self.assertEqual(steps["leave"]["status"], "warning")
        self.assertEqual(steps["leave"]["items"], ["Mehmet Can: 10.01–12.01"])
        self.assertTrue(first["can_lock"])
        self.assertFalse(first["locked"])
        self.assertEqual(first["warnings"], sum(item["status"] == "warning" for item in first["steps"]))
        self.assertTrue(locked["locked"])
        self.assertFalse(locked["can_lock"])
        self.assertEqual({item["id"]: item["status"] for item in locked["steps"]}["lock"], "ok")
        self.assertEqual(bad_status, 400)
        self.assertIn("error", bad)

    def test_wizard_is_reachable_from_menu_with_safe_actions(self):
        extras = (WEB / "extras.js").read_text(encoding="utf-8")
        self.assertIn('["Ay sonu kontrol sihirbazı", () => openMonthEndCheck()]', extras)
        self.assertIn("/api/month-end-check?month=", extras)
        self.assertIn('resolveUnsaved(kind, "Dönem değiştirilmeden")', extras)
        self.assertIn("if (data.errors) throw new Error", extras)
        self.assertIn('"Ay sonu kontrol"', (WEB / "easy-mode.js").read_text(encoding="utf-8"))
        self.assertIn('[/ay sonu kontrol/, "check"]', (WEB / "easy-hubs.js").read_text(encoding="utf-8"))

    def test_mail_menu_opens_automation_and_history_sections(self):
        access = (WEB / "access.js").read_text(encoding="utf-8")
        self.assertIn('openMailDraft({ section: "automation" })', access)
        self.assertIn('openMailDraft({ section: "history" })', access)
        self.assertIn('"E-posta"', (WEB / "easy-mode.js").read_text(encoding="utf-8"))


class MainFlowTests(unittest.TestCase):
    """Plan → puantaj → hazır şablon → Excel → ay sonu kontrolü, tamamı HTTP üzerinden."""

    def setUp(self):
        import tempfile
        import threading
        from server import create_server
        from storage import Store

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "flow.sqlite3"
        with Store(path) as store:
            self.person = store.add_person("Akış Personeli")["id"]
        self.server = create_server(path, WEB)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), thread.join(3)))
        self.port = self.server.server_address[1]

    def send(self, method, path, body=None):
        import http.client
        import json
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            headers = {"Origin": f"http://127.0.0.1:{self.port}"}
            data = None
            if body is not None:
                headers["Content-Type"] = "application/json"
                data = json.dumps(body).encode()
            connection.request(method, path, data, headers)
            response = connection.getresponse()
            raw = response.read()
            kind = response.getheader("Content-Type") or ""
            return response.status, (json.loads(raw) if "json" in kind else raw)
        finally:
            connection.close()

    def test_plan_attendance_ready_template_export_and_month_end(self):
        from datetime import date, timedelta
        from io import BytesIO
        from openpyxl import load_workbook
        import ui_settings

        month = "2020-02"
        days = {}
        for offset in range(29):
            day = date(2020, 2, 1) + timedelta(days=offset)
            days[day.isoformat()] = ({"kind": "weekly_off"} if day.weekday() == 6
                                     else {"kind": "work", "start": "08:00", "end": "16:00"})
        status, saved = self.send("PUT", "/api/schedule", {"month": month, "revision": "", "entries": [{
            "row_id": "flow-row", "employee_id": self.person, "section": "MARKET", "slot": 1, "days": days}]})
        self.assertEqual(status, 200, saved)

        status, attendance = self.send("GET", f"/api/attendance?month={month}")
        self.assertEqual(status, 200)
        rows = attendance["attendance"]["rows"]
        self.assertEqual([row["employee_id"] for row in rows], [self.person])
        self.assertEqual(attendance["missing_people"], [])
        self.assertTrue(all(str(code).strip() for code in rows[0]["codes"].values()))

        ready = next(item for item in ui_settings.READY_LIBRARY
                     if item["kind"] == "attendance" and "A4" in item["name"])
        status, library = self.send("PUT", "/api/template-library", {"items": [{
            "name": "Benim A4 puantajım", "kind": "attendance", "sheets": ready["sheets"],
            "created": "2020-03-01T09:00:00"}]})
        self.assertEqual(status, 200, library)
        self.assertEqual([item["name"] for item in library["items"]], ["Benim A4 puantajım"])

        status, settings = self.send("GET", "/api/workbook-settings")
        self.assertEqual(status, 200)
        settings["sheets"]["attendance:template:0"] = ready["sheets"]["0"]
        status, stored = self.send("PUT", "/api/workbook-settings", settings)
        self.assertEqual(status, 200, stored)

        status, body = self.send("GET", f"/api/export?kind=attendance&month={month}&week=0")
        self.assertEqual(status, 200)
        sheet = load_workbook(BytesIO(body)).worksheets[0]
        self.assertEqual(sheet.page_setup.orientation, "landscape")
        self.assertEqual((sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight), (1, 1))
        self.assertEqual(sheet.print_title_rows.replace("$", ""), "1:3")
        self.assertIn("Akış Personeli", [cell.value for row in sheet.iter_rows() for cell in row])

        status, check = self.send("GET", f"/api/month-end-check?month={month}")
        self.assertEqual(status, 200)
        steps = {item["id"]: item["status"] for item in check["steps"]}
        self.assertEqual((steps["schedule"], steps["attendance-people"], steps["attendance-cells"]),
                         ("ok", "ok", "ok"))
        self.assertTrue(check["can_lock"])
        status, _ = self.send("POST", "/api/archive/period-lock", {"month": month, "locked": True})
        self.assertEqual(status, 200)
        status, refused = self.send("PUT", "/api/schedule", {"month": month, "revision": saved["revision"],
                                                             "entries": []})
        self.assertEqual(status, 400)
        self.assertIn("kilitli", refused["error"])
        status, check = self.send("GET", f"/api/month-end-check?month={month}")
        self.assertTrue(check["locked"])
        self.assertFalse(check["can_lock"])


if __name__ == "__main__":
    unittest.main()
