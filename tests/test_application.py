from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import date, timedelta
from io import BytesIO
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from unittest.mock import patch

import server as app_server

from docx import Document
from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import main
import ocr_windows
from main import DesktopAPI
from scheduling import (
    ATTENDANCE_CATEGORY_COLORS,
    _css_color,
    _theme_colors,
    calculate_attendance,
)
from server import create_server
from storage import Store


ROOT = Path(__file__).resolve().parents[1]


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = create_server(
            Path(self.temp.name) / "data.sqlite3",
            ROOT / "web",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def test_schedule_and_attendance_empty_states_offer_accessible_recovery(self):
        status, _, html = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="schedule-empty" class="empty-filter" role="status" aria-live="polite"', html)
        self.assertIn(b'id="attendance-empty" class="empty-filter" role="status" aria-live="polite"', html)

        status, _, script = self.request("/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b'data-empty-action="clear-schedule-filters"', script)
        self.assertIn(b'data-empty-action="clear-filters"', script)
        self.assertIn(b"function captureViewContext", script)
        self.assertIn(b"function restoreViewContext", script)

    def test_default_data_file_migrates_legacy_state_into_application_folder(self):
        root = Path(self.temp.name)
        legacy = root / "local" / "VardiyaPuantaj"
        legacy.mkdir(parents=True)
        source_db = legacy / "vardiya_puantaj.sqlite3"
        connection = sqlite3.connect(source_db)
        try:
            connection.execute("CREATE TABLE sample (value TEXT)")
            connection.execute("INSERT INTO sample VALUES ('preserved')")
            connection.commit()
        finally:
            connection.close()
        (legacy / "browser").mkdir()
        (legacy / "browser" / "profile.dat").write_text("browser state", encoding="utf-8")
        app_directory = root / "application"
        app_directory.mkdir()

        with patch.dict(os.environ, {"LOCALAPPDATA": str(root / "local")}, clear=True):
            with patch.object(main, "app_root", return_value=app_directory), \
                    patch.object(main.sys, "frozen", False, create=True):
                result = main.data_file()

        expected = app_directory / "data" / "vardiya_puantaj.sqlite3"
        self.assertEqual(result, expected)
        connection = sqlite3.connect(result)
        try:
            self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "preserved")
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()
        self.assertTrue(source_db.is_file())
        self.assertEqual(
            (app_directory / "data" / "browser" / "profile.dat").read_text(encoding="utf-8"),
            "browser state",
        )

    def test_frozen_data_file_uses_executable_directory_not_environment_override(self):
        root = Path(self.temp.name)
        executable = root / "application" / "VardiyaPuantaj.exe"
        executable.parent.mkdir()
        configured = root / "configured-elsewhere"
        with patch.dict(os.environ, {
            "LOCALAPPDATA": str(root / "local"),
            "VARDIYA_PUANTAJ_DATA_DIR": str(configured),
        }, clear=True):
            with patch.object(main.sys, "frozen", True, create=True), \
                    patch.object(main.sys, "executable", str(executable)):
                result = main.data_file()

        self.assertEqual(result, executable.parent / "data" / "vardiya_puantaj.sqlite3")
        self.assertFalse(configured.exists())

    def request(self, path, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            status, headers, body = error.code, error.headers, error.read()
            error.close()
            return status, headers, body

    def json_request(self, path, method="GET", body=None):
        status, _, body = self.request(path, method, body)
        return status, json.loads(body.decode("utf-8"))

    def add_person(self, name="Ayşe Yılmaz"):
        status, payload = self.json_request("/api/people", "POST", {"name": name})
        self.assertEqual(status, 201)
        return payload["person"]

    def test_hourly_day_read_endpoints_preserve_saved_schedule_revision_and_cells(self):
        person = self.add_person("Saatlik Görünüm Personeli")
        entry = {
            "row_id": "hourly-read-only",
            "employee_id": person["id"],
            "section": "MARKET",
            "slot": 1,
            "days": {"2026-11-01": {"kind": "work", "start": "22:00", "end": "02:00"}},
        }
        with Store(self.server.data_path) as store:
            store.save_schedule_month("2026-11", [entry], "")
            before = store.schedule_month("2026-11")

        for path in (
            "/api/operations/hourly-staffing?month=2026-11&date=2026-11-02",
            "/api/operations/shift-activities?month=2026-11&date=2026-11-02",
        ):
            status, _ = self.json_request(path)
            self.assertEqual(status, 200, path)

        with Store(self.server.data_path) as store:
            self.assertEqual(store.schedule_month("2026-11"), before)

    def test_assistant_staffing_answer_uses_registered_report_and_revision_sources(self):
        import scheduling
        from storage import Store

        month = date.today().strftime("%Y-%m")
        person = self.add_person("Kabul Kadro Personeli")
        dates = scheduling.month_dates(month)
        missing = {dates[2].isoformat(), dates[4].isoformat(), dates[6].isoformat()}
        with Store(self.server.data_path) as store:
            entries = scheduling.normalize_schedule_entries(month, [{
                "row_id": "assistant-staffing",
                "employee_id": person["id"],
                "section": "MARKET",
                "days": {
                    day.isoformat(): {"kind": "work", "start": "08:00", "end": "16:00"}
                    for day in dates if day.isoformat() not in missing
                },
            }], store.people())
            revision = store.save_schedule_month(month, entries, store.schedule_month(month)["revision"])
            store.save_compliance_settings({"min_staffing": {"MARKET": 1}})

        with patch("server.live_assistant.chat", side_effect=AssertionError("Local report must answer directly")):
            status, answer = self.json_request("/api/assistant/chat", "POST", {
                "model": "acceptance-local",
                "messages": [{"role": "user", "content": "4 eksik vardiya var, hangi günler?"}],
                "context": {"view": "schedule", "month": month, "people": [person["name"]]},
            })

        self.assertEqual(status, 200)
        self.assertIn("İletide 4 denmiş; kayıtlı raporda 3 açık bulundu.", answer["reply"])
        self.assertEqual({source["date"] for source in answer["sources"]}, missing)
        self.assertTrue(all(
            source["kind"] == "schedule" and source["month"] == month
            and source["section"] == "MARKET" and source["revision"] == revision
            for source in answer["sources"]
        ))

    def test_assistant_constraint_answer_groups_recorded_findings_without_claiming_impossibility(self):
        month = date.today().strftime("%Y-%m")
        day = f"{month}-12"
        report = {
            "revision": "plan-revision-42",
            "findings": [
                {"type": "leave_request_conflict", "date": day, "name": "Ayşe", "message": "Onaylı izin çakışması"},
                {"type": "min_staffing", "date": day, "section": "MARKET", "message": "Kadro açığı"},
                {"type": "overlap", "date": day, "section": "MARKET", "message": "Vardiya çakışması"},
            ],
        }
        with patch("server.operations.quality_report", return_value=report), \
                patch("schedule_protection.locks", return_value=[
                    {"day": day, "section": "MARKET"},
                ]), \
                patch("server.live_assistant.chat", side_effect=AssertionError("Recorded constraints answer directly")):
            status, answer = self.json_request("/api/assistant/chat", "POST", {
                "model": "acceptance-local",
                "messages": [{"role": "user", "content": "Plan oluşturamadı, neden?"}],
                "context": {"view": "schedule", "month": month, "people": []},
            })

        self.assertEqual(status, 200)
        for expected in ("1 izin", "1 kadro", "1 koruma", "1 vardiya çakışması"):
            self.assertIn(expected, answer["reply"])
        self.assertIn("tek başına planın matematiksel olarak olanaksız olduğunu kanıtlamaz", answer["reply"])
        self.assertEqual(len(answer["sources"]), 4)
        self.assertTrue(all(source["revision"] == "plan-revision-42" for source in answer["sources"]))

    def test_assistant_calculation_answer_uses_engine_totals_and_excludes_pay_estimates(self):
        from storage import Store

        person = self.add_person("Kabul Hesap Personeli")
        month = date.today().strftime("%Y-%m")
        detail = {
            "result": {},
            "monthly_totals": {
                "worked_hours": 8.5,
                "overtime_computed_hours": 1.25,
                "overtime_manual": 0.5,
            },
            "overtime_rule_effective_date": "2026-01-01",
        }
        with Store(self.server.data_path):
            with patch("server.document_workflows.calculation_detail", return_value=detail) as calculate, \
                    patch("server.live_assistant.chat", side_effect=AssertionError("Calculation engine must answer directly")):
                status, answer = self.json_request("/api/assistant/chat", "POST", {
                    "model": "acceptance-local",
                    "messages": [{"role": "user", "content": "Kabul Hesap Personeli çalışma saati ve fazla mesai toplamı?"}],
                    "context": {
                        "view": "attendance", "month": month, "people": [person["name"]],
                    },
                })

        self.assertEqual(status, 200)
        calculate.assert_called_once()
        self.assertIn("çalışma toplamı 8.5 saat", answer["reply"])
        self.assertIn("hesaplanan fazla mesai 1.25 saat", answer["reply"])
        self.assertIn("Ücret hesabı yapılmadı.", answer["reply"])
        self.assertIn("mesai kuralı 2026-01-01", answer["reply"])
        self.assertEqual(answer["sources"][0]["employee_id"], person["id"])
        self.assertEqual(answer["sources"][0]["month"], month)

    def test_easy_interface_is_opt_in_and_keeps_real_application_assets(self):
        status, _, normal = self.request("/")
        self.assertEqual(status, 200)
        self.assertNotIn(b"/easy-mode.js", normal)
        self.assertNotIn(b"/easy-mode.css", normal)
        self.assertNotIn(b"/easy-menu.js", normal)
        easy_scripts = (
            b"/easy-mode.js", b"/easy-hubs.js", b"/easy-menu.js", b"/easy-assistant.js", b"/linked-leave.js",
            b"/document-workflows.js", b"/data-continuity.js", b"/operations.js",
        )
        self.server.easy_mode = True
        status, _, easy = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'<script src="/easy-mode.js" defer>', easy)
        self.assertIn(b'<script src="/easy-menu.js" defer>', easy)
        self.assertLess(easy.index(b'<script src="/easy-mode.js" defer>'), easy.index(b'<script src="/easy-hubs.js" defer>'))
        self.assertLess(easy.index(b'<script src="/easy-hubs.js" defer>'), easy.index(b'<script src="/easy-menu.js" defer>'))
        self.assertIn(b'href="/easy-mode.css"', easy)
        for html in (normal, easy):
            self.assertEqual(html.count(b'<script src="/easy-usability.js" defer>'), 1)
        self.assertEqual(
            re.findall(rb'<script src="([^"]+)"', normal),
            [path for path in re.findall(rb'<script src="([^"]+)"', easy)
             if path not in easy_scripts],
        )
        for path in (*[item.decode() for item in easy_scripts], "/easy-mode.css"):
            status, _, content = self.request(path)
            self.assertEqual(status, 200)
            self.assertTrue(content)
        self.add_person("Kolay Kullanım Test")
        status, result = self.json_request("/api/people")
        self.assertEqual(status, 200)
        self.assertTrue(any(person["name"] == "Kolay Kullanım Test" for person in result["people"]))

    def test_live_assistant_assets_and_api_contract(self):
        status, _, script = self.request("/live-assistant.js")
        self.assertEqual(status, 200)
        self.assertIn(b"confirm-live-assistant-action", script)

        with patch("server.live_assistant.list_models", return_value=["qwen2.5:1.5b"]):
            status, result = self.json_request("/api/assistant/models")
        self.assertEqual(status, 200)
        self.assertEqual(result, {"models": ["qwen2.5:1.5b"]})

        with patch("server.live_assistant.chat", return_value={"reply": "Merhaba"}):
            status, result = self.json_request("/api/assistant/chat", "POST", {
                "model": "qwen2.5:1.5b",
                "messages": [{"role": "user", "content": "Merhaba"}],
                "context": {"view": "schedule", "month": "2026-11", "people": ["Ayşe Yılmaz"]},
            })
        self.assertEqual(status, 200)
        self.assertEqual(result, {"reply": "Merhaba"})

        status, result = self.json_request("/api/assistant/chat", "POST", {
            "model": "qwen2.5:1.5b",
            "messages": [{"role": "user", "content": "Merhaba"}],
            "context": {},
            "unexpected": True,
        })
        self.assertEqual(status, 400)
        self.assertIn("geçersiz", result["error"])

    def test_easy_menu_preferences_persist_and_reject_invalid_updates_atomically(self):
        import ui_settings

        defaults = {
            "order": list(ui_settings.MENU_VIEWS), "hidden": [],
            "ring_enabled": True, "ring_size": 190, "ring_items": None,
        }
        self.assertEqual(self.json_request("/api/ui-settings")[1]["settings"]["easy_menu"], defaults)
        menu = {
            "order": ["view:attendance", "group:Personel", "tool:Personel listesi (Ctrl+Shift+P)",
                      "view:schedule", "view:templates", "view:archive"],
            "hidden": ["view:archive", "group:Diğer araçlar"],
            "ring_enabled": False, "ring_size": 300,
            "ring_items": ["view:schedule", "group:Personel", "tool:Personel listesi (Ctrl+Shift+P)"],
        }
        status, result = self.json_request("/api/ui-settings", "PUT", {
            "easy_menu": menu, "density": "compact",
        })
        self.assertEqual(status, 200, result)
        self.assertEqual(self.json_request("/api/ui-settings")[1]["settings"]["easy_menu"], menu)
        invalid_menus = [
            None, {}, {"order": [], "hidden": []},
            {**menu, "order": menu["order"] + ["view:schedule"]},
            {**menu, "order": menu["order"][1:]},
            {**menu, "order": list(ui_settings.MENU_VIEWS) + ["view:unknown"]},
            {**menu, "order": list(ui_settings.MENU_VIEWS) + ["tool:"]},
            {**menu, "order": list(ui_settings.MENU_VIEWS) + ["tool: Personel"]},
            {**menu, "order": list(ui_settings.MENU_VIEWS) + ["tool:Personel\nkartı"]},
            {**menu, "order": list(ui_settings.MENU_VIEWS) + ["group:"]},
            {**menu, "order": list(ui_settings.MENU_VIEWS) + ["group: Personel"]},
            {**menu, "order": list(ui_settings.MENU_VIEWS) + ["group:Vardiya  planlama"]},
            {**menu, "order": list(ui_settings.MENU_VIEWS) + ["group:Personel\n"]},
            {**menu, "order": menu["order"] + ["group:Personel"]},
            {**menu, "order": list(ui_settings.MENU_VIEWS) + [True]},
            {**menu, "order": list(ui_settings.MENU_VIEWS) + [f"tool:{i}" for i in range(97)]},
            {**menu, "hidden": ["tool:Personel kartı"]},
            {**menu, "hidden": ["group: Personel"]},
            {**menu, "hidden": ["view:archive", "view:archive"]},
            {**menu, "extra": True},
            {**menu, "ring_enabled": "false"},
            {**menu, "ring_enabled": 1},
            {**menu, "ring_size": 280},
            {**menu, "ring_size": False},
            {**menu, "ring_items": "view:schedule"},
            {**menu, "ring_items": ["view:schedule", "view:schedule"]},
            {**menu, "ring_items": ["view:schedule", None, "view:schedule"]},
            {**menu, "ring_items": [f"tool:Araç {i}" for i in range(13)]},
            {**menu, "ring_items": ["view:unknown"]},
            {**menu, "ring_items": ["tool: Personel"]},
        ]
        for invalid in invalid_menus:
            with self.subTest(menu=invalid):
                status, result = self.json_request("/api/ui-settings", "PUT", {
                    "easy_menu": invalid, "density": "comfortable",
                })
                self.assertEqual(status, 400, result)
                stored = self.json_request("/api/ui-settings")[1]["settings"]
                self.assertEqual(stored["easy_menu"], menu)
                self.assertEqual(stored["density"], "compact")
        legacy_menu = {"order": list(ui_settings.MENU_VIEWS), "hidden": []}
        status, result = self.json_request("/api/ui-settings", "PUT", {"easy_menu": legacy_menu})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["settings"]["easy_menu"], {
            **legacy_menu, "ring_enabled": True, "ring_size": 190, "ring_items": None,
        })
        ring_with_gap = {
            **legacy_menu, "ring_enabled": True, "ring_size": 190,
            "ring_items": ["view:schedule", None, "view:attendance"],
        }
        status, result = self.json_request("/api/ui-settings", "PUT", {"easy_menu": ring_with_gap})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["settings"]["easy_menu"], ring_with_gap)
        compact_ring = {**legacy_menu, "ring_enabled": True, "ring_size": 160,
                        "ring_items": [f"tool:Araç {i}" for i in range(12)]}
        status, result = self.json_request("/api/ui-settings", "PUT", {"easy_menu": compact_ring})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["settings"]["easy_menu"], compact_ring)
        empty_ring = {**compact_ring, "ring_items": []}
        status, result = self.json_request("/api/ui-settings", "PUT", {"easy_menu": empty_ring})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["settings"]["easy_menu"], empty_ring)
        all_hidden = {**menu, "hidden": list(ui_settings.MENU_VIEWS)}
        status, result = self.json_request("/api/ui-settings", "PUT", {"easy_menu": all_hidden})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.json_request("/api/ui-settings")[1]["settings"]["easy_menu"], all_hidden)
        status, result = self.json_request("/api/ui-settings", "PUT", {"easy_menu": defaults})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["settings"]["easy_menu"], defaults)
        self.assertEqual(result["settings"]["density"], "compact")

    def test_easy_ribbon_visibility_is_independent_and_rejects_invalid_updates_atomically(self):
        defaults = {"schedule": True, "attendance": True}
        self.assertEqual(self.json_request("/api/ui-settings")[1]["settings"]["easy_ribbons"], defaults)
        for ribbons in (
            {"schedule": False, "attendance": True},
            {"schedule": False, "attendance": False},
            {"schedule": True, "attendance": False},
        ):
            status, result = self.json_request("/api/ui-settings", "PUT", {
                "easy_ribbons": ribbons, "density": "compact",
            })
            self.assertEqual(status, 200, result)
            self.assertEqual(self.json_request("/api/ui-settings")[1]["settings"]["easy_ribbons"], ribbons)
        for invalid in (
            None, [], True, {}, {"schedule": False},
            {**ribbons, "attendance": 0}, {**ribbons, "schedule": "false"},
            {**ribbons, "archive": False},
        ):
            with self.subTest(ribbons=invalid):
                status, result = self.json_request("/api/ui-settings", "PUT", {
                    "easy_ribbons": invalid, "density": "comfortable",
                })
                self.assertEqual(status, 400, result)
                stored = self.json_request("/api/ui-settings")[1]["settings"]
                self.assertEqual(stored["easy_ribbons"], ribbons)
                self.assertEqual(stored["density"], "compact")

    def test_planning_warnings_follow_tables_and_legends(self):
        class ViewParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.views = {}
                self.current_view = None

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                element_id = attrs.get("id")
                if element_id in ("schedule-view", "attendance-view"):
                    self.current_view = element_id
                    self.views[element_id] = []
                if self.current_view:
                    self.views[self.current_view].append(attrs)

            def handle_endtag(self, tag):
                if tag == "main":
                    self.current_view = None

        status, _, content = self.request("/")
        self.assertEqual(status, 200)
        parser = ViewParser()
        parser.feed(content.decode("utf-8"))
        for view, warning_ids in (
            ("schedule-view", ["schedule-validation-warnings"]),
            ("attendance-view", ["attendance-warnings", "leave-alerts"]),
        ):
            elements = parser.views[view]
            footer_positions = [
                i for i, attrs in enumerate(elements)
                if "table-footer" in attrs.get("class", "").split()
            ]
            for warning_id in warning_ids:
                matches = [(i, attrs) for i, attrs in enumerate(elements) if attrs.get("id") == warning_id]
                self.assertEqual(len(matches), 1)
                position, attrs = matches[0]
                self.assertGreater(position, max(footer_positions))
                self.assertEqual(attrs.get("role"), "status")
                self.assertIn("hidden", attrs)
            self.assertEqual(elements[-1]["id"], warning_ids[-1])

    def test_import_mapping_profiles_persist_and_reject_invalid_columns(self):
        mapping = {
            "mode": "rows", "start_row": 2, "person_col": 1, "section_col": None,
            "default_section": "MARKET", "date_col": 2, "status_col": 3,
            "start_col": None, "end_col": None, "header_row": 1, "first_day_col": 2,
        }
        signature = {
            "mode": "rows",
            "columns": [
                {"field": "person", "column": 1, "header": "PERSONEL"},
                {"field": "date", "column": 2, "header": "TARIH"},
                {"field": "status", "column": 3, "header": "VARDIYA"},
            ],
        }
        status, saved = self.json_request(
            "/api/import-profiles", "PUT",
            {"profiles": [{
                "id": "", "name": "Aylık çizelge", "mapping": mapping,
                "header_signature": signature,
            }]},
        )
        self.assertEqual(status, 200)
        profile = saved["profiles"][0]
        self.assertTrue(profile["id"])
        self.assertEqual(profile["mapping"], mapping)
        self.assertEqual(profile["header_signature"], signature)
        status, loaded = self.json_request("/api/import-profiles")
        self.assertEqual(status, 200)
        self.assertEqual(loaded["profiles"], [profile])

        invalid = dict(mapping, person_col=0)
        status, result = self.json_request(
            "/api/import-profiles", "PUT",
            {"profiles": [{"id": "", "name": "Geçersiz", "mapping": invalid}]},
        )
        self.assertEqual(status, 400)
        self.assertIn("numarası", result["error"])
        invalid_signature = {
            "mode": "rows",
            "columns": [{"field": ["person"], "column": 1, "header": "PERSONEL"}],
        }
        status, result = self.json_request(
            "/api/import-profiles", "PUT",
            {"profiles": [{
                "id": "", "name": "Geçersiz başlık", "mapping": mapping,
                "header_signature": invalid_signature,
            }]},
        )
        self.assertEqual(status, 400)
        self.assertIn("başlık imzası", result["error"])

    def test_import_profile_requires_reconfirmation_when_mapped_headers_change(self):
        mapping = {
            "mode": "rows", "start_row": 2, "person_col": 1, "section_col": None,
            "date_col": 2, "status_col": 3,
        }
        text = "Personel | Tarih | Vardiya\nAyşe Yılmaz | 01.10.2026 | H"
        status, baseline = self.json_request("/api/import-profile-headers", "POST", {
            "text": text, "mapping": mapping, "expected": None,
        })
        self.assertEqual(status, 200, baseline)
        self.assertTrue(baseline["needs_confirmation"])
        self.assertEqual(
            [column["header"] for column in baseline["actual"]["columns"]],
            ["PERSONEL", "TARIH", "VARDIYA"],
        )

        status, unchanged = self.json_request("/api/import-profile-headers", "POST", {
            "text": text, "mapping": mapping, "expected": baseline["actual"],
        })
        self.assertEqual(status, 200, unchanged)
        self.assertTrue(unchanged["matches"])
        self.assertFalse(unchanged["needs_confirmation"])

        changed_text = "Çalışan | Tarih | Vardiya\nAyşe Yılmaz | 01.10.2026 | H"
        status, changed = self.json_request("/api/import-profile-headers", "POST", {
            "text": changed_text, "mapping": mapping, "expected": baseline["actual"],
        })
        self.assertEqual(status, 200, changed)
        self.assertFalse(changed["matches"])
        self.assertTrue(changed["needs_confirmation"])
        self.assertEqual(changed["actual"]["columns"][0]["header"], "CALISAN")

        matrix_mapping = {
            "mode": "matrix", "header_row": 1, "first_day_col": 2, "person_col": 1,
        }
        status, matrix_baseline = self.json_request("/api/import-profile-headers", "POST", {
            "text": "Personel | 01.10.2026 | 02.10.2026\nAyşe | H | H",
            "mapping": matrix_mapping, "expected": None,
        })
        self.assertEqual(status, 200, matrix_baseline)
        status, matrix_next_month = self.json_request("/api/import-profile-headers", "POST", {
            "text": "Personel | 01.11.2026 | 02.11.2026\nAyşe | H | H",
            "mapping": matrix_mapping, "expected": matrix_baseline["actual"],
        })
        self.assertEqual(status, 200, matrix_next_month)
        self.assertTrue(matrix_next_month["matches"])

    def test_form_draft_round_trips_and_rejects_invalid_person_or_dates(self):
        person = self.add_person()
        draft = {
            "id": "absence", "person_id": person["id"], "category": "G",
            "start": "2026-10-12", "end": "2026-10-13",
            "section": "MARKET", "description": "Açıklama daha sonra tamamlanacak",
        }
        status, saved = self.json_request("/api/form-draft", "PUT", draft)
        self.assertEqual(status, 200)
        self.assertEqual(saved["draft"], draft)
        status, loaded = self.json_request("/api/form-draft")
        self.assertEqual(status, 200)
        self.assertEqual(loaded["draft"], draft)

        invalid_person = dict(draft, person_id=999)
        status, result = self.json_request("/api/form-draft", "PUT", invalid_person)
        self.assertEqual(status, 400)
        self.assertIn("personeli", result["error"])
        invalid_dates = dict(draft, start="2026-10-14", end="2026-10-13")
        status, result = self.json_request("/api/form-draft", "PUT", invalid_dates)
        self.assertEqual(status, 400)
        self.assertIn("önce", result["error"])

    def test_attendance_events_validate_dates_and_filter_overlapping_months(self):
        person = self.add_person()
        body = {
            "employee_id": person["id"], "kind": "annual_leave",
            "start": "2026-10-30", "end": "2026-11-02",
            "document": "izin-formu.docx", "notes": "Onaylı izin",
        }
        status, created = self.json_request("/api/attendance-events", "POST", body)
        self.assertEqual(status, 201)
        event = created["event"]
        self.assertEqual(event["notes"], "Onaylı izin")

        for month in ("2026-10", "2026-11"):
            status, result = self.json_request(f"/api/attendance-events?month={month}")
            self.assertEqual(status, 200)
            self.assertEqual([item["id"] for item in result["events"]], [event["id"]])
        status, result = self.json_request("/api/attendance-events?month=2026-12")
        self.assertEqual(status, 200)
        self.assertEqual(result["events"], [])

        invalid = dict(body, end="2026-10-29")
        status, result = self.json_request("/api/attendance-events", "POST", invalid)
        self.assertEqual(status, 400)
        self.assertIn("önce", result["error"])
        status, result = self.json_request(
            "/api/attendance-events/delete", "POST", {"id": event["id"]}
        )
        self.assertEqual(status, 200)
        status, result = self.json_request("/api/attendance-events?month=2026-10")
        self.assertEqual(result["events"], [])

    def test_form_samples_generate_for_person_and_date_range(self):
        person = self.add_person()
        status, result = self.json_request("/api/forms")
        self.assertEqual(status, 200)
        forms = {item["id"]: item for item in result["forms"]}
        self.assertEqual(forms["unpaid"]["category"], "Ü")
        body = {"id": "absence", "person_id": person["id"], "start": "2026-10-30",
                "end": "2026-11-02", "category": "G"}
        status, _, content = self.request("/api/forms/generate", "POST", body)
        self.assertEqual(status, 200)
        text = "\n".join(p.text for p in Document(BytesIO(content)).paragraphs)
        self.assertIn("Ayşe Yılmaz", text)
        self.assertIn("30.10.2026 - 02.11.2026", text)
        self.assertIn("tarihleri arasında", text)
        self.assertNotIn("{{", text)
        body["end"] = body["start"]
        status, _, content = self.request("/api/forms/generate", "POST", body)
        self.assertEqual(status, 200)
        self.assertIn("30.10.2026 tarihinde", "\n".join(p.text for p in Document(BytesIO(content)).paragraphs))
        body["end"] = "2026-11-02"
        body.update(id="annual", category="S")
        status, _, content = self.request("/api/forms/generate", "POST", body)
        self.assertEqual(status, 200)
        sheet = load_workbook(BytesIO(content)).active
        self.assertEqual(sheet["D21"].value, "Ayşe Yılmaz")
        self.assertEqual(sheet["E12"].value, "30.10.2026")
        self.assertEqual(sheet["E13"].value, "02.11.2026")
        self.assertIsNone(sheet["D23"].value)
        self.assertEqual(len(sheet.merged_cells.ranges), 4)
        body["end"] = "2026-10-01"
        self.assertEqual(self.json_request("/api/forms/preview", "POST", body)[0], 400)
        body.update(end="2026-11-02", category="G")
        self.assertEqual(self.json_request("/api/forms/preview", "POST", body)[0], 400)

    def test_form_settings_add_replace_delete_and_split_run(self):
        person = self.add_person()
        doc = Document()
        paragraph = doc.add_paragraph()
        paragraph.add_run("Ad: {{pers")
        paragraph.add_run("onel}} / {{baslangic}}")
        data = BytesIO()
        doc.save(data)
        payload = {"id": "", "name": "Yeni form", "category": "G", "filename": "test.docx",
                   "content": base64.b64encode(data.getvalue()).decode()}
        status, saved = self.json_request("/api/forms", "POST", payload)
        self.assertEqual(status, 200)
        form_id = saved["id"]
        listed = {form["id"]: form for form in self.json_request("/api/forms")[1]["forms"]}
        self.assertEqual(listed[form_id]["status"], "draft")
        body = {"id": form_id, "person_id": person["id"], "start": "2026-10-01",
                "end": "2026-10-01", "category": "G"}
        self.assertEqual(self.json_request("/api/forms/preview", "POST", body)[0], 400)
        self.assertEqual(self.json_request("/api/forms/status", "POST", {
            "id": form_id, "status": "active"})[0], 200)
        status, result = self.json_request("/api/forms/preview", "POST", body)
        self.assertEqual(status, 200)
        self.assertEqual(result["text"], "Ad: Ayşe Yılmaz / 01.10.2026")
        self.assertIn("<main", result["html"])
        self.assertIn("Ayşe Yılmaz", result["html"])
        payload.update(id=form_id, name="Güncel form")
        self.assertEqual(self.json_request("/api/forms", "POST", payload)[0], 200)
        self.assertTrue(any(f["id"] == form_id and f["name"] == "Güncel form"
                            for f in self.json_request("/api/forms")[1]["forms"]))
        self.assertEqual(self.json_request("/api/forms/status", "POST", {
            "id": form_id, "status": "retired"})[0], 200)
        self.assertEqual(self.json_request("/api/forms/preview", "POST", body)[0], 400)
        self.assertEqual(self.json_request("/api/forms/status", "POST", {
            "id": form_id, "status": "active"})[0], 400)
        payload["content"] = base64.b64encode(b"not a docx").decode()
        self.assertEqual(self.json_request("/api/forms", "POST", payload)[0], 400)
        self.assertEqual(self.json_request("/api/forms/delete", "POST", {"id": "absence"})[0], 200)
        self.assertNotIn("absence", [f["id"] for f in self.json_request("/api/forms")[1]["forms"]])

    def test_visual_form_preview_escapes_text_and_renders_excel_grid(self):
        person = self.add_person("<script>alert(1)</script>")
        body = {"id": "absence", "person_id": person["id"], "start": "2026-10-01",
                "end": "2026-10-01", "category": "G"}
        status, preview = self.json_request("/api/forms/preview", "POST", body)
        self.assertEqual(status, 200, preview)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", preview["html"])
        self.assertNotIn("<script>", preview["html"])

        body.update(id="annual", category="S")
        status, preview = self.json_request("/api/forms/preview", "POST", body)
        self.assertEqual(status, 200, preview)
        self.assertIn('class="spreadsheet"', preview["html"])

    def test_form_generated_excel_treats_person_name_as_text(self):
        person = self.add_person("=1+1")
        body = {"id": "annual", "person_id": person["id"], "start": "2026-10-01",
                "end": "2026-10-01", "category": "S"}
        status, _, content = self.request("/api/forms/generate", "POST", body)
        self.assertEqual(status, 200)
        cell = load_workbook(BytesIO(content)).active["D21"]
        self.assertEqual(cell.value, "=1+1")
        self.assertEqual(cell.data_type, "s")

    def test_form_section_and_description_tokens_require_and_fill_mapped_values(self):
        person = self.add_person()
        document = Document()
        document.add_paragraph("Bölüm: {{bolum}}")
        document.add_paragraph("Açıklama: {{aciklama}}")
        stream = BytesIO()
        document.save(stream)
        payload = {
            "id": "", "name": "Bölüm ve açıklama", "category": "G",
            "filename": "mapped-fields.docx",
            "content": base64.b64encode(stream.getvalue()).decode("ascii"),
        }
        status, saved = self.json_request("/api/forms", "POST", payload)
        self.assertEqual(status, 200)
        self.assertEqual(self.json_request("/api/forms/status", "POST", {
            "id": saved["id"], "status": "active"})[0], 200)
        form = {
            "id": saved["id"], "person_id": person["id"],
            "start": "2026-10-01", "end": "2026-10-01", "category": "G",
        }
        status, result = self.json_request("/api/forms/preview", "POST", form)
        self.assertEqual(status, 400)
        self.assertIn("bölüm", result["error"].lower())
        form["section"] = "MARKET"
        status, result = self.json_request("/api/forms/preview", "POST", form)
        self.assertEqual(status, 400)
        self.assertIn("açıklama", result["error"].lower())
        form["description"] = "Personel toplantısında bulundu."
        status, result = self.json_request("/api/forms/preview", "POST", form)
        self.assertEqual(status, 200)
        self.assertIn("Bölüm: MARKET", result["text"])
        self.assertIn("Açıklama: Personel toplantısında bulundu.", result["text"])
        status, result = self.json_request("/api/forms/history", "POST", {
            **form, "id": saved["id"], "file_name": "tutanak.docx",
        })
        self.assertEqual(status, 200)
        history = self.json_request("/api/history")[1]["history"]
        self.assertEqual(history[0]["action"], "form.generated")
        self.assertEqual(history[0]["details"]["employee_id"], person["id"])
        self.assertEqual(history[0]["details"]["start"], "2026-10-01")
        self.assertEqual(history[0]["details"]["file_name"], "tutanak.docx")

    def test_form_save_endpoint_writes_generated_file_and_respects_cancellation(self):
        person = self.add_person()
        path = Path(self.temp.name) / "izin.xlsx"

        class FakeWindow:
            selection = (str(path),)

            def create_file_dialog(self, *args, **kwargs):
                return self.selection

        api = DesktopAPI(self.base_url)
        api._window = FakeWindow()
        self.server.save_form = api.save_form
        body = {"id": "annual", "person_id": person["id"], "start": "2026-10-30",
                "end": "2026-11-02", "category": "S"}
        status, result = self.json_request("/api/forms/save", "POST", body)
        self.assertEqual(status, 200)
        self.assertEqual(result, {"cancelled": False, "path": str(path)})
        history = self.json_request("/api/history")[1]["history"]
        self.assertEqual(history[0]["action"], "form.generated")
        self.assertIn("Ayşe Yılmaz", history[0]["summary"])
        self.assertEqual(history[0]["details"]["file_name"], "izin.xlsx")
        sheet = load_workbook(path).active
        self.assertEqual(sheet["D21"].value, "Ayşe Yılmaz")
        self.assertEqual(sheet["E12"].value, "30.10.2026")
        self.assertEqual(sheet["E13"].value, "02.11.2026")
        before = path.read_bytes()
        api._window.selection = None
        status, result = self.json_request("/api/forms/save", "POST", body)
        self.assertEqual(status, 200)
        self.assertEqual(result, {"cancelled": True})
        self.assertEqual(path.read_bytes(), before)

    def test_jpeg_save_checks_image_and_saves_to_selected_path(self):
        image = BytesIO()
        Image.new("RGB", (10, 12), "white").save(image, "JPEG")
        path = Path(self.temp.name) / "tablo.jpg"

        class FakeWindow:
            def create_file_dialog(self, *args, **kwargs):
                return (str(path),)

        api = DesktopAPI(self.base_url)
        api._window = FakeWindow()
        self.server.save_form = api.save_form
        payload = {"content": base64.b64encode(image.getvalue()).decode()}
        status, result = self.json_request("/api/save-jpeg", "POST", payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["path"], str(path))
        with Image.open(path) as saved:
            self.assertEqual((saved.format, saved.size), ("JPEG", (10, 12)))
        status, _ = self.json_request("/api/save-jpeg", "POST", {"content": base64.b64encode(b"invalid").decode()})
        self.assertEqual(status, 400)

    def test_archive_delete_is_revision_checked_and_preserves_other_data(self):
        person = self.add_person()
        entry = {"row_id": "archive", "employee_id": person["id"], "section": "MARKET",
                 "slot": 1, "days": {"2026-10-31": {"kind": "work", "start": "22:00", "end": "07:00"}}}
        status, saved = self.json_request("/api/schedule", "PUT",
            {"month": "2026-10", "revision": "", "entries": [entry]})
        self.assertEqual(status, 200)
        status, manual = self.json_request("/api/attendance", "PUT", {
            "month": "2026-10", "revision": "",
            "overrides": {"2026-10-31": {str(person["id"]): "H"}},
            "deductions": {str(person["id"]): 20}})
        self.assertEqual(status, 200)
        self.assertEqual(self.json_request("/api/archive")[1]["months"], [
            {"month": "2026-11", "schedule_revision": "", "attendance_revision": "",
             "attendance_derived": True, "locked": False},
            {"month": "2026-10", "schedule_revision": saved["revision"],
             "attendance_revision": manual["revision"], "attendance_derived": True, "locked": False}])
        november = self.json_request("/api/attendance?month=2026-11")[1]["attendance"]["rows"][0]
        self.assertEqual(november["codes"]["2026-11-01"], "G.Ç")
        payload = {"kind": "schedule", "month": "2026-10", "revision": "stale"}
        self.assertEqual(self.json_request("/api/archive/delete", "POST", payload)[0], 409)
        payload["revision"] = saved["revision"]
        self.assertEqual(self.json_request("/api/archive/delete", "POST", payload)[0], 200)
        self.assertEqual(self.json_request("/api/schedule?month=2026-10")[1]["entries"], [])
        november_rows = self.json_request("/api/attendance?month=2026-11")[1]["attendance"]["rows"]
        self.assertFalse(any(row["codes"].get("2026-11-01") == "G.Ç" for row in november_rows))
        self.assertEqual(self.json_request("/api/attendance?month=2026-10")[1]["deductions"],
                         {str(person["id"]): 20})
        self.assertEqual(self.json_request("/api/people")[1]["people"][0]["id"], person["id"])
        self.assertEqual(self.json_request("/api/schedule", "PUT",
            {"month": "2026-10", "revision": saved["revision"], "entries": [entry]})[0], 409)
        self.assertEqual(self.json_request("/api/archive/delete", "POST",
            {"kind": "attendance", "month": "2026-10", "revision": manual["revision"]})[0], 200)
        self.assertEqual(self.json_request("/api/archive")[1]["months"], [])

    def test_archive_revisions_compare_restore_and_reject_stale_changes(self):
        person = self.add_person("Ayşe Yılmaz")
        first_entry = {
            "row_id": "archive-revision", "employee_id": person["id"],
            "section": "MARKET", "slot": 1,
            "days": {"2026-10-05": {"kind": "work", "start": "09:00", "end": "17:00"}},
        }
        status, first = self.json_request("/api/schedule", "PUT", {
            "month": "2026-10", "revision": "", "entries": [first_entry],
        })
        self.assertEqual(status, 200)
        second_entry = {
            **first_entry,
            "days": {"2026-10-05": {"kind": "work", "start": "10:00", "end": "18:00"}},
        }
        status, second = self.json_request("/api/schedule", "PUT", {
            "month": "2026-10", "revision": first["revision"], "entries": [second_entry],
        })
        self.assertEqual(status, 200)

        query = urlencode({"kind": "schedule", "month": "2026-10"})
        status, revisions = self.json_request(f"/api/archive/revisions?{query}")
        self.assertEqual(status, 200)
        self.assertEqual(revisions["current_revision"], second["revision"])
        self.assertEqual(revisions["revisions"][0]["revision"], first["revision"])
        compare_query = urlencode({
            "kind": "schedule", "month": "2026-10", "revision": first["revision"],
        })
        status, comparison = self.json_request(f"/api/archive/compare?{compare_query}")
        self.assertEqual(status, 200)
        self.assertEqual(comparison["previous"][0]["days"]["2026-10-05"]["start"], "09:00")
        self.assertEqual(comparison["current"][0]["days"]["2026-10-05"]["start"], "10:00")

        restore = {
            "kind": "schedule", "month": "2026-10", "revision": first["revision"],
            "expected_revision": second["revision"],
        }
        status, restored = self.json_request("/api/archive/restore", "POST", restore)
        self.assertEqual(status, 200)
        self.assertNotEqual(restored["revision"], first["revision"])
        self.assertEqual(
            self.json_request("/api/schedule?month=2026-10")[1]["entries"],
            [first_entry],
        )
        self.assertEqual(self.json_request("/api/archive/restore", "POST", restore)[0], 409)

        status, locked = self.json_request("/api/archive/period-lock", "POST", {
            "month": "2026-10", "locked": True,
        })
        self.assertEqual(status, 200)
        restore["expected_revision"] = restored["revision"]
        self.assertEqual(self.json_request("/api/archive/restore", "POST", restore)[0], 400)

    def test_locked_period_rejects_edits_and_reopens_with_confirmation(self):
        person = self.add_person()
        entry = {
            "row_id": "locked-period", "employee_id": person["id"],
            "section": "MARKET", "slot": 1,
            "days": {"2026-10-12": {"kind": "work", "start": "09:00", "end": "17:00"}},
        }
        status, saved = self.json_request("/api/schedule", "PUT", {
            "month": "2026-10", "revision": "", "entries": [entry],
        })
        self.assertEqual(status, 200)
        event_status, event = self.json_request("/api/attendance-events", "POST", {
            "employee_id": person["id"], "kind": "annual_leave",
            "start": "2026-10-15", "end": "2026-10-16",
            "document": "", "notes": "",
        })
        self.assertEqual(event_status, 201)
        status, locked = self.json_request("/api/archive/period-lock", "POST", {
            "month": "2026-10", "locked": True,
        })
        self.assertEqual(status, 200)
        self.assertTrue(locked["locked"])
        archive = self.json_request("/api/archive")[1]["months"]
        self.assertTrue(next(item for item in archive if item["month"] == "2026-10")["locked"])

        status, result = self.json_request("/api/schedule", "PUT", {
            "month": "2026-10", "revision": saved["revision"], "entries": [],
        })
        self.assertEqual(status, 400)
        self.assertIn("kilitli", result["error"])
        status, result = self.json_request("/api/attendance-events", "POST", {
            "employee_id": person["id"], "kind": "sick_leave",
            "start": "2026-10-20", "end": "2026-10-20",
            "document": "", "notes": "",
        })
        self.assertEqual(status, 400)
        self.assertIn("kilitli", result["error"])
        status, result = self.json_request("/api/attendance-events/delete", "POST", {
            "id": event["event"]["id"],
        })
        self.assertEqual(status, 400)
        self.assertIn("kilitli", result["error"])
        status, result = self.json_request("/api/archive/delete", "POST", {
            "kind": "schedule", "month": "2026-10", "revision": saved["revision"],
        })
        self.assertEqual(status, 400)
        self.assertIn("kilitli", result["error"])

        status, result = self.json_request("/api/archive/period-lock", "POST", {
            "month": "2026-10", "locked": False,
        })
        self.assertEqual(status, 400)
        self.assertIn("gerekçe", result["error"])
        status, reopened = self.json_request("/api/archive/period-lock", "POST", {
            "month": "2026-10", "locked": False, "reason": "Rapor sonradan geldi",
        })
        self.assertEqual(status, 200)
        self.assertFalse(reopened["locked"])
        history = self.json_request("/api/history")[1]
        self.assertTrue(any("Rapor sonradan geldi" in item["summary"] for item in history["history"]))
        self.assertEqual(self.json_request("/api/schedule", "PUT", {
            "month": "2026-10", "revision": saved["revision"], "entries": [],
        })[0], 200)

    def test_desktop_save_endpoint_uses_native_callback_without_js_bridge(self):
        self.assertEqual(self.json_request("/api/environment")[1], {"desktop": False})
        self.assertEqual(self.json_request("/api/save-excel", "POST",
            {"kind": "schedule", "month": "2026-10", "week": 0})[0], 400)
        path = Path(self.temp.name) / "secilen.xlsx"
        api = DesktopAPI(self.base_url)
        class FakeWindow:
            def create_file_dialog(self, *args, **kwargs):
                return (str(path),)
        api._window = FakeWindow()
        self.server.save_excel = api.save_excel
        self.assertEqual(self.json_request("/api/environment")[1], {"desktop": True})
        status, result = self.json_request("/api/save-excel", "POST",
            {"kind": "attendance", "month": "2026-10", "week": 0})
        self.assertEqual(status, 200)
        self.assertEqual(result["path"], str(path))
        self.assertTrue(load_workbook(path).worksheets)

    def test_import_combined_shift_and_common_headers(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["Adı Soyadı", "Birim", "Tarih", "Vardiya Saati"])
        ws.append(["Ayşe Yılmaz", "MARKET", "01.10.2026", "07:00–15:00"])
        output = BytesIO()
        wb.save(output)
        status, result = self.json_request("/api/import-preview", "POST", {
            "filename": "liste.xlsx", "content": base64.b64encode(output.getvalue()).decode(),
            "month": "2026-10"})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["rows"][0]["entry"], {"kind": "work", "start": "07:00", "end": "15:00"})

    def test_import_preview_compares_content_not_filename(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Personel", "Bölüm", "Tarih", "Giriş", "Çıkış", "Durum"])
        sheet.append(["Ayşe Yılmaz", "MARKET", "01.10.2026", "07:00", "15:00", ""])
        sheet.append(["Ayşe Yılmaz", "MARKET", "02.10.2026", "", "", "H"])
        sheet.append(["Ayşe Yılmaz", "MARKET", "03.10.2026", "", "", "S"])
        output = BytesIO()
        workbook.save(output)
        existing = [
            {"person": "Ayşe Yılmaz", "section": "MARKET", "date": "2026-10-01",
             "entry": {"kind": "work", "start": "07:00", "end": "15:00"}},
            {"person": "Ayşe Yılmaz", "section": "MARKET", "date": "2026-10-02",
             "entry": {"kind": "annual_leave"}},
        ]
        payload = {
            "content": base64.b64encode(output.getvalue()).decode(),
            "month": "2026-10",
            "existing": existing,
        }
        status, first = self.json_request("/api/import-preview", "POST", {
            **payload, "filename": "vardiya-ilk.xlsx",
        })
        self.assertEqual(status, 200, first)
        self.assertEqual(first["comparison"], {
            "statuses": ["same", "changed", "new"],
            "counts": {"same": 1, "new": 1, "changed": 1},
        })

        status, renamed = self.json_request("/api/import-preview", "POST", {
            **payload, "filename": "farkli-dosya-adi.xlsx",
        })
        self.assertEqual(status, 200, renamed)
        self.assertEqual(renamed["comparison"], first["comparison"])

    def test_import_manual_mapping_supports_rows_matrix_and_validation(self):
        body = {"month": "2026-10",
                "text": "İsim | Gün | Vardiya\nAyşe Yılmaz | 01.10.2026 | 07:00-15:00\nAyşe Yılmaz | 02.10.2026 | H",
                "mapping": {"mode": "rows", "start_row": 2, "person_col": 1, "date_col": 2,
                            "status_col": 3, "default_section": "MARKET"}}
        status, result = self.json_request("/api/import-mapped-preview", "POST", body)
        self.assertEqual(status, 200, result)
        self.assertEqual([row["entry"]["kind"] for row in result["rows"]], ["work", "weekly_off"])
        body["text"] = "İsim | 01.10.2026 | 02.10.2026\nAyşe Yılmaz | 07:00-15:00 | H"
        body["mapping"] = {"mode": "matrix", "start_row": 2, "header_row": 1,
                           "person_col": 1, "first_day_col": 2, "default_section": "MARKET"}
        status, result = self.json_request("/api/import-mapped-preview", "POST", body)
        self.assertEqual(status, 200, result)
        self.assertEqual([row["date"] for row in result["rows"]], ["2026-10-01", "2026-10-02"])
        body["text"] += "\nAyşe Yılmaz | H | H"
        status, result = self.json_request("/api/import-mapped-preview", "POST", body)
        self.assertEqual(status, 400)
        self.assertIn("Çelişkili", result["error"])
        body["text"] = "İsim | 01.10.2026\nAyşe Yılmaz | bilinmeyen"
        status, result = self.json_request("/api/import-mapped-preview", "POST", body)
        self.assertEqual(status, 400)
        self.assertIn("tanınmadı", result["error"])

    def test_import_preview_separates_valid_rows_from_review_rows(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Personel", "Bölüm", "Tarih", "Giriş", "Çıkış", "Durum"])
        sheet.append(["Ayşe Yılmaz", "MARKET", "01.10.2026", "07:00", "15:00", ""])
        sheet.append(["Ayşe Yılmaz", "MARKET", "02.10.2026", "", "", "BİLİNMİYOR"])
        sheet.append(["Ayşe Yılmaz", "MARKET", "03.10.2026", "", "", "H"])
        output = BytesIO()
        workbook.save(output)
        original_bytes = output.getvalue()
        document = {
            "filename": "inceleme.xlsx",
            "content": base64.b64encode(output.getvalue()).decode(),
            "month": "2026-10",
            "partial": True,
        }
        status, automatic = self.json_request("/api/import-preview", "POST", document)
        self.assertEqual(status, 200, automatic)
        self.assertEqual([row["date"] for row in automatic["rows"]], ["2026-10-01", "2026-10-03"])
        self.assertEqual(automatic["comparison"]["counts"], {"same": 0, "new": 2, "changed": 0})
        self.assertEqual(len(automatic["review"]), 1)
        self.assertIn("Tablo 1, satır 3", automatic["review"][0]["location"])
        self.assertIn("tanınmadı", automatic["review"][0]["reason"])
        self.assertEqual(automatic["review"][0]["filename"], "inceleme.xlsx")
        self.assertEqual(automatic["review"][0]["page"], 1)
        self.assertEqual(
            automatic["review"][0]["source"],
            "Ayşe Yılmaz | MARKET | 02.10.2026 |  |  | BİLİNMİYOR",
        )
        self.assertEqual(output.getvalue(), original_bytes)

        mapped = {
            "month": "2026-10",
            "filename": "monthly-list.xlsx",
            "page": 2,
            "text": "İsim | Bölüm | Gün | Giriş | Çıkış | Durum\n"
                    "Ayşe Yılmaz | MARKET | 01.10.2026 | 07:00 | 15:00 |\n"
                    "Ayşe Yılmaz | MARKET | 02.10.2026 | | | BİLİNMİYOR\n"
                    "Ayşe Yılmaz | MARKET | 03.10.2026 | | | H",
            "mapping": {"mode": "rows", "start_row": 2, "person_col": 1, "section_col": 2,
                        "date_col": 3, "start_col": 4, "end_col": 5, "status_col": 6},
            "partial": True,
        }
        status, manual = self.json_request("/api/import-mapped-preview", "POST", mapped)
        self.assertEqual(status, 200, manual)
        self.assertEqual([row["date"] for row in manual["rows"]], ["2026-10-01", "2026-10-03"])
        self.assertEqual(len(manual["review"]), 1)
        self.assertEqual(manual["review"][0]["location"], "Satır 3")
        self.assertEqual(manual["review"][0]["filename"], "monthly-list.xlsx")
        self.assertEqual(manual["review"][0]["page"], 2)
        self.assertEqual(
            manual["review"][0]["source"],
            "Ayşe Yılmaz | MARKET | 02.10.2026 | | | BİLİNMİYOR",
        )

        mapped.pop("partial")
        status, strict = self.json_request("/api/import-mapped-preview", "POST", mapped)
        self.assertEqual(status, 400)
        self.assertIn("tanınmadı", strict["error"])

    def test_import_preview_reconfirms_working_hours_or_selected_codes(self):
        body = {
            "month": "2026-10",
            "text": "İsim | Gün | Vardiya\nAyşe Yılmaz | 01.10.2026 | 07:00-15:00\nAyşe Yılmaz | 02.10.2026 | H\nAyşe Yılmaz | 03.10.2026 | S",
            "mapping": {"mode": "rows", "start_row": 2, "person_col": 1, "date_col": 2,
                        "status_col": 3, "default_section": "MARKET"},
        }
        status, all_rows = self.json_request("/api/import-mapped-preview", "POST", body)
        self.assertEqual(status, 200, all_rows)
        self.assertEqual(len(all_rows["rows"]), 3)

        status, hours = self.json_request("/api/import-mapped-preview", "POST", {
            **body, "fields": {"mode": "hours", "codes": []},
        })
        self.assertEqual(status, 200, hours)
        self.assertEqual([row["date"] for row in hours["rows"]], ["2026-10-01"])

        status, selected = self.json_request("/api/import-mapped-preview", "POST", {
            **body, "fields": {"mode": "codes", "codes": ["H"]},
        })
        self.assertEqual(status, 200, selected)
        self.assertEqual([row["date"] for row in selected["rows"]], ["2026-10-02"])
        self.assertEqual(selected["rows"][0]["entry"]["kind"], "weekly_off")

        status, invalid = self.json_request("/api/import-mapped-preview", "POST", {
            **body, "fields": {"mode": "codes", "codes": ["not-a-code"]},
        })
        self.assertEqual(status, 400)
        self.assertIn("kodlarını seçin", invalid["error"])

    def test_attendance_import_selected_codes_preserves_existing_values_in_blank_or_unselected_cells(self):
        person = self.add_person("Ayşe Yılmaz")
        status, saved = self.json_request("/api/attendance", "PUT", {
            "month": "2026-10",
            "revision": "",
            "overrides": {
                "2026-10-01": {str(person["id"]): "G"},
                "2026-10-03": {str(person["id"]): "H"},
                "2026-10-04": {str(person["id"]): "S"},
            },
            "deductions": {},
        })
        self.assertEqual(status, 200, saved)

        workbook = Workbook()
        sheet = workbook.active
        sheet["B3"] = "ADI SOYADI"
        for day in range(1, 32):
            sheet.cell(3, day + 2, day)
        sheet["B4"] = "Ayşe Yılmaz"
        sheet["C4"], sheet["D4"], sheet["F4"] = "X", "R", "H"
        content = BytesIO()
        workbook.save(content)
        payload = {
            "month": "2026-10",
            "filename": "gecmis.xlsx",
            "content": base64.b64encode(content.getvalue()).decode(),
            "apply": False,
            "include_codes": ["H"],
        }
        status, preview = self.json_request("/api/import/attendance-excel", "POST", payload)
        self.assertEqual(status, 200, preview)
        self.assertEqual(preview["available_codes"], ["H", "R", "X"])
        self.assertEqual(preview["filtered_out_count"], 2)
        self.assertEqual(preview["overrides"], {"2026-10-04": {str(person["id"]): "H"}})
        self.assertEqual(preview["code_count"], 1)

        status, saved_import = self.json_request(
            "/api/import/attendance-excel", "POST",
            payload | {"apply": True, "revision": preview["revision"]},
        )
        self.assertEqual(status, 200, saved_import)
        current = self.json_request("/api/attendance?month=2026-10")[1]["overrides"]
        self.assertEqual(current["2026-10-01"][str(person["id"])], "G")
        self.assertEqual(current["2026-10-03"][str(person["id"])], "H")
        self.assertEqual(current["2026-10-04"][str(person["id"])], "H")
        self.assertNotIn("2026-10-02", current)

    def test_scanned_pdf_requires_review_and_exposes_correctable_text(self):
        pdf = PdfWriter()
        pdf.add_blank_page(width=200, height=200)
        data = BytesIO()
        pdf.write(data)
        payload = {"filename": "scan.pdf", "content": base64.b64encode(data.getvalue()).decode()}
        ocr = {
            "tables": [[("Personel", "Tarih", "Durum"), ("DENEME", "01.10.2026", "H")]],
            "evidence": [{
                "page": 1, "row": 2, "text": "DENEME | 01.10.2026 | H",
                "image": "data:image/jpeg;base64,c2FtcGxl",
            }],
        }
        with (
            patch("document_import.ocr_windows.recognize_pdf_pages_with_evidence", return_value=ocr),
            patch("document_import.ocr_windows.recognize_pdf_pages", return_value=ocr["tables"]),
        ):
            status, extracted = self.json_request("/api/import-extract", "POST", {**payload, "force_ocr": False})
            self.assertEqual(status, 200, extracted)
            self.assertEqual(extracted["ocr_pages"], [1])
            self.assertFalse(extracted["ocr_confidence_available"])
            self.assertEqual(extracted["tables"][0][1], ["DENEME", "01.10.2026", "H"])
            self.assertEqual(extracted["ocr_evidence"][0]["text"], "DENEME | 01.10.2026 | H")
            self.assertTrue(extracted["ocr_evidence"][0]["image"].startswith("data:image/jpeg;base64,"))
            status, result = self.json_request("/api/import-preview", "POST", {**payload, "month": "2026-10"})
            self.assertEqual(status, 400)
            self.assertIn("doğrulanmalıdır", result["error"])

    def test_ocr_rows_keep_image_region_bounds_for_the_recognized_values(self):
        words = [[
            {"text": "DENEME", "x": 10, "y": 20, "width": 50, "height": 10},
            {"text": "MARKET", "x": 100, "y": 20, "width": 50, "height": 10},
        ]]
        row = ocr_windows._lines_to_rows(words, include_regions=True)[0]
        self.assertEqual(row["cells"], ("DENEME", "MARKET"))
        self.assertEqual(row["bounds"], (10, 20, 150, 30))
        image = Image.new("RGB", (300, 100), "white")
        try:
            encoded = ocr_windows._crop_ocr_region(image, row["bounds"])
        finally:
            image.close()
        self.assertTrue(encoded.startswith("data:image/jpeg;base64,"))
        crop = Image.open(BytesIO(base64.b64decode(encoded.split(",", 1)[1])))
        self.assertEqual(crop.size, (160, 30))
        crop.close()

    @unittest.skipUnless(os.environ.get("VARDIYA_TEST_OCR") != "0" and __import__("ocr_windows").turkish_ocr_available(),
                         "Requires Windows Turkish OCR")
    def test_scanned_pdf_ocr_review_save_and_export(self):
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new("RGB", (2500, 600), "white")
        painter = ImageDraw.Draw(image)
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 45)
        for y, row in ((50, ("PERSONEL", "BOLUM", "TARIH", "GIRIS", "CIKIS")),
                       (150, ("DENEME", "MARKET", "01.10.2026", "07:00", "15:00"))):
            for x, text in zip((50, 500, 950, 1450, 1900), row):
                painter.text((x, y), text, fill="black", font=font)
        scanned = BytesIO()
        image.save(scanned, format="PDF", resolution=200)
        status, extracted = self.json_request("/api/import-extract", "POST", {
            "filename": "scan.pdf", "content": base64.b64encode(scanned.getvalue()).decode(),
            "force_ocr": False})
        self.assertEqual(status, 200, extracted)
        self.assertEqual(extracted["ocr_pages"], [1])
        self.assertFalse(extracted["ocr_confidence_available"])
        self.assertTrue(extracted["ocr_evidence"])
        employee_evidence = next(item for item in extracted["ocr_evidence"] if "DENEME" in item["text"])
        self.assertEqual(employee_evidence["page"], 1)
        self.assertTrue(employee_evidence["image"].startswith("data:image/jpeg;base64,"))
        text = "\n".join(" | ".join(row) for row in extracted["tables"][0])
        status, preview = self.json_request("/api/import-mapped-preview", "POST", {
            "month": "2026-10", "text": text,
            "mapping": {"mode": "rows", "start_row": 2, "person_col": 1, "section_col": 2,
                        "date_col": 3, "start_col": 4, "end_col": 5}})
        self.assertEqual(status, 200, preview)
        self.assertEqual(preview["rows"][0]["entry"], {"kind": "work", "start": "07:00", "end": "15:00"})
        person = self.add_person("DENEME")
        row = preview["rows"][0]
        entry = {"row_id": "ocr", "employee_id": person["id"], "section": row["section"],
                 "slot": 1, "days": {row["date"]: row["entry"]}}
        status, _ = self.json_request("/api/schedule", "PUT",
            {"month": "2026-10", "revision": "", "entries": [entry]})
        self.assertEqual(status, 200)
        attendance = self.json_request("/api/attendance?month=2026-10")[1]["attendance"]["rows"][0]
        self.assertEqual(attendance["codes"]["2026-10-01"], "X")
        status, _, body = self.request("/api/export?kind=attendance&month=2026-10")
        self.assertEqual(status, 200)
        workbook = load_workbook(BytesIO(body))
        self.assertEqual(workbook.active["C4"].value, "X")

    def test_bulk_people_add_is_atomic_and_supports_template_capacity(self):
        existing = self.add_person("Mevcut Personel")
        status, result = self.json_request(
            "/api/people/bulk",
            "POST",
            {"names": ["Yeni Personel", existing["name"].lower()]},
        )
        self.assertEqual(status, 400)
        self.assertIn("Zaten kayıtlı", result["error"])
        self.assertEqual(
            [person["name"] for person in self.json_request("/api/people")[1]["people"]],
            ["Mevcut Personel"],
        )

        names = [f"Personel {index:02d}" for index in range(1, 19)]
        status, result = self.json_request(
            "/api/people/bulk", "POST", {"names": names}
        )
        self.assertEqual(status, 201)
        self.assertEqual([person["name"] for person in result["people"]], names)

        status, result = self.json_request(
            "/api/people/bulk", "POST", {"names": ["Aynı Ad", "aynı ad"]}
        )
        self.assertEqual(status, 400)
        self.assertIn("yinelenen", result["error"])

    def test_schedule_slots_are_preserved_and_duplicate_slots_are_rejected(self):
        first = self.add_person("Birinci Personel")
        second = self.add_person("İkinci Personel")
        _, schedule = self.json_request("/api/schedule?month=2026-10")
        legacy_entries = [
            {
                "row_id": "legacy-second",
                "employee_id": second["id"],
                "section": "MARKET",
                "days": {},
            },
            {
                "row_id": "legacy-first",
                "employee_id": first["id"],
                "section": "MARKET",
                "days": {},
            },
        ]
        status, _ = self.json_request(
            "/api/schedule",
            "PUT",
            {"month": "2026-10", "revision": schedule["revision"], "entries": legacy_entries},
        )
        self.assertEqual(status, 200)
        _, schedule = self.json_request("/api/schedule?month=2026-10")
        self.assertEqual(
            {entry["row_id"]: entry["slot"] for entry in schedule["entries"]},
            {"legacy-second": 2, "legacy-first": 1},
        )
        entries = [
            {
                "row_id": "second-slot",
                "employee_id": second["id"],
                "section": "MARKET",
                "slot": 2,
                "days": {},
            },
            {
                "row_id": "first-slot",
                "employee_id": first["id"],
                "section": "MARKET",
                "slot": 1,
                "days": {},
            },
        ]
        status, saved = self.json_request(
            "/api/schedule",
            "PUT",
            {"month": "2026-10", "revision": schedule["revision"], "entries": entries},
        )
        self.assertEqual(status, 200)
        _, persisted = self.json_request("/api/schedule?month=2026-10")
        self.assertEqual(
            {entry["row_id"]: entry["slot"] for entry in persisted["entries"]},
            {"second-slot": 2, "first-slot": 1},
        )
        duplicate_slots = [{**entry, "slot": 1} for entry in entries]
        status, result = self.json_request(
            "/api/schedule",
            "PUT",
            {"month": "2026-10", "revision": persisted["revision"], "entries": duplicate_slots},
        )
        self.assertEqual(status, 400)
        self.assertIn("aynı şablon satırına", result["error"])

    def test_quick_entry_presets_are_saved_and_invalid_mappings_are_rejected(self):
        status, initial = self.json_request("/api/quick-entries")
        self.assertEqual(status, 200)
        self.assertEqual(initial["entries"], [])
        presets = [{
            "id": "training-shift",
            "label": "Eğitim vardiyası",
            "code": "X",
            "start": "09:00",
            "end": "17:00",
        }, {
            "id": "special-rest",
            "label": "Özel hafta tatili",
            "code": "H",
            "start": "",
            "end": "",
        }]
        status, saved = self.json_request(
            "/api/quick-entries", "PUT", {"entries": presets}
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["entries"], presets)
        self.assertEqual(self.json_request("/api/quick-entries")[1]["entries"], presets)

        status, result = self.json_request(
            "/api/quick-entries",
            "PUT",
            {"entries": [{**presets[0], "start": "09:00", "end": ""}]},
        )
        self.assertEqual(status, 400)
        self.assertIn("birlikte", result["error"])

    def test_schedule_validation_settings_persist_and_reject_invalid_limits(self):
        status, initial = self.json_request("/api/schedule-validation-settings")
        self.assertEqual(status, 200)
        self.assertEqual(initial, {"minimum_rest_hours": 0.0})

        settings = {"minimum_rest_hours": 11.5}
        status, saved = self.json_request(
            "/api/schedule-validation-settings", "PUT", settings,
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved, settings)
        self.assertEqual(self.json_request("/api/schedule-validation-settings")[1], settings)

        for invalid_hours in (-0.5, 24.5, True, "11", float("nan")):
            status, result = self.json_request(
                "/api/schedule-validation-settings",
                "PUT",
                {"minimum_rest_hours": invalid_hours},
            )
            self.assertEqual(status, 400)
            self.assertIn("Asgari dinlenme süresi", result["error"])
        self.assertEqual(self.json_request("/api/schedule-validation-settings")[1], settings)

    def test_overtime_settings_persist_validate_and_apply_weekly_rule(self):
        status, initial = self.json_request("/api/overtime-settings")
        self.assertEqual(status, 200)
        self.assertEqual(initial, {"weekly_limit_hours": 0.0, "week_start": 0, "daily_limit_hours": 8.0, "break_rules": []})

        settings = {"weekly_limit_hours": 36, "week_start": 0}
        status, saved = self.json_request("/api/overtime-settings", "PUT", settings)
        self.assertEqual(status, 200)
        self.assertEqual(saved, {"weekly_limit_hours": 36.0, "week_start": 0, "daily_limit_hours": 8.0, "break_rules": []})
        self.assertEqual(self.json_request("/api/overtime-settings")[1], saved)
        self.assertEqual(
            self.json_request("/api/attendance?month=2026-10")[1]["overtime_settings"],
            saved,
        )

        for invalid in (
            {"weekly_limit_hours": -1, "week_start": 0},
            {"weekly_limit_hours": 169, "week_start": 0},
            {"weekly_limit_hours": True, "week_start": 0},
            {"weekly_limit_hours": 36, "week_start": 7},
            {"weekly_limit_hours": 36, "week_start": False},
            {"weekly_limit_hours": 36, "week_start": 0, "daily_limit_hours": 25},
            {"weekly_limit_hours": 36, "week_start": 0, "daily_limit_hours": True},
        ):
            self.assertEqual(
                self.json_request("/api/overtime-settings", "PUT", invalid)[0],
                400,
            )
        self.assertEqual(self.json_request("/api/overtime-settings")[1], saved)

        employee = {"id": 1, "name": "Ayşe"}
        entries = [{
            "employee_id": 1,
            "section": "MARKET",
            "days": {
                f"2026-10-{day:02d}": {"kind": "work", "start": "09:00", "end": "17:00"}
                for day in range(5, 9)
            },
        }]
        monday_week = calculate_attendance(
            "2026-10", entries, {}, [employee],
            overtime_settings={"weekly_limit_hours": 24, "week_start": 0, "daily_limit_hours": 0},
        )
        wednesday_week = calculate_attendance(
            "2026-10", entries, {}, [employee],
            overtime_settings={"weekly_limit_hours": 24, "week_start": 2, "daily_limit_hours": 0},
        )
        self.assertEqual(monday_week["rows"][0]["overtime_hours"], 8.0)
        self.assertEqual(wednesday_week["rows"][0]["overtime_hours"], 0.0)

        crossing_entries = [{
            "employee_id": 1,
            "section": "MARKET",
            "days": {
                f"2026-10-{day:02d}": {"kind": "work", "start": "09:00", "end": "17:00"}
                for day in range(26, 30)
            },
        }]
        october = calculate_attendance(
            "2026-10", crossing_entries, {}, [employee],
            overtime_settings={"weekly_limit_hours": 24, "week_start": 0, "daily_limit_hours": 0},
        )
        november = calculate_attendance(
            "2026-11", crossing_entries, {}, [employee],
            overtime_settings={"weekly_limit_hours": 24, "week_start": 0, "daily_limit_hours": 0},
        )
        self.assertEqual(october["rows"][0]["overtime_hours"], 8.0)
        self.assertEqual(november["rows"][0]["overtime_hours"], 0.0)

    def test_year_to_date_balances_store_openings_and_count_saved_codes(self):
        person = self.add_person("Bakiye Test Personeli")
        status, initial = self.json_request("/api/balances?month=2026-02")
        self.assertEqual(status, 200)
        row = next(item for item in initial["people"] if item["employee_id"] == person["id"])
        self.assertEqual(row["annual_leave_remaining_days"], 0)
        self.assertEqual(row["overtime_tracked_hours"], 0)

        status, saved = self.json_request("/api/balances", "PUT", {
            "year": 2026,
            "employee_id": person["id"],
            "annual_leave_opening_days": 7.5,
            "overtime_opening_hours": 2.25,
        })
        self.assertEqual(status, 200)
        self.assertEqual(saved["opening"], {"annual_leave_days": 7.5, "overtime_hours": 2.25})

        for month, overrides in (
            ("2026-01", {"2026-01-05": "S"}),
            ("2026-02", {"2026-02-02": "R", "2026-02-03": "Ü", "2026-02-04": "G"}),
        ):
            _, attendance = self.json_request(f"/api/attendance?month={month}")
            self.assertEqual(self.json_request("/api/attendance", "PUT", {
                "month": month,
                "revision": attendance["revision"],
                "overrides": {
                    day: {str(person["id"]): code} for day, code in overrides.items()
                },
            })[0], 200)

        _, summary = self.json_request("/api/balances?month=2026-02")
        row = next(item for item in summary["people"] if item["employee_id"] == person["id"])
        self.assertEqual(row["annual_leave_used_days"], 1)
        self.assertEqual(row["annual_leave_remaining_days"], 6.5)
        self.assertEqual(row["sick_days"], 1)
        self.assertEqual(row["unpaid_leave_days"], 1)
        self.assertEqual(row["absence_days"], 1)
        self.assertEqual(row["overtime_opening_hours"], 2.25)
        self.assertEqual(row["overtime_reported_hours"], 0)
        self.assertEqual(row["overtime_tracked_hours"], 2.25)

        self.assertEqual(self.json_request("/api/balances", "PUT", {
            "year": 2026, "employee_id": person["id"],
            "annual_leave_opening_days": 9.5, "overtime_opening_hours": 2.25,
        })[0], 200)
        status, ledger_summary = self.json_request(
            f"/api/balances?month=2026-02&employee_id={person['id']}"
        )
        self.assertEqual(status, 200)
        ledger = next(item for item in ledger_summary["people"] if item["employee_id"] == person["id"])[
            "leave_balance_ledger"
        ]
        self.assertTrue(ledger["opening_configured"])
        self.assertEqual([item["type"] for item in ledger["movements"]], ["opening", "used", "adjustment"])
        self.assertEqual([item["delta"] for item in ledger["movements"]], [7.5, -1.0, 2.0])
        self.assertEqual(ledger["movements"][1]["source"], "Elle puantaj düzeltmesi")
        self.assertEqual(ledger["closing_days"], 8.5)
        self.assertEqual(ledger["movements"][-1]["running_balance"], 8.5)

        invalid = self.json_request("/api/balances", "PUT", {
            "year": 2026,
            "employee_id": person["id"],
            "annual_leave_opening_days": float("nan"),
            "overtime_opening_hours": 0,
        })
        self.assertEqual(invalid[0], 400)

    def test_user_name_is_recorded_as_history_actor(self):
        status, user = self.json_request("/api/user-settings")
        self.assertEqual((status, user["user_name"]), (200, "Yerel kullanıcı"))
        status, user = self.json_request("/api/user-settings", "PUT", {"user_name": "  Ayşe   Yılmaz "})
        self.assertEqual((status, user["user_name"]), (200, "Ayşe Yılmaz"))
        self.add_person("Geçmiş Kişi")
        status, history = self.json_request("/api/history?limit=5")
        self.assertEqual(history["history"][0]["actor"], "Ayşe Yılmaz")
        self.assertEqual(history["history"][1]["action"], "settings.user")
        self.assertEqual(self.json_request("/api/user-settings", "PUT", {"user_name": "x" * 61})[0], 400)
        self.assertEqual(self.json_request("/api/user-settings", "PUT", {"user_name": ""})[1]["user_name"], "Yerel kullanıcı")

    def test_reports_check_payroll_and_person_pdf(self):
        from pypdf import PdfReader

        first = self.add_person("Rapor Bir")
        second = self.add_person("Rapor İki")
        self.assertEqual(self.json_request("/api/schedule-validation-settings", "PUT", {"minimum_rest_hours": 11})[0], 200)
        self.assertEqual(self.json_request("/api/balances", "PUT", {
            "year": 2026, "employee_id": first["id"],
            "annual_leave_opening_days": 0, "overtime_opening_hours": 0,
        })[0], 200)
        days = {
            f"2026-10-{day:02d}": {"kind": "work", "start": "09:00", "end": "17:00"}
            for day in range(1, 32) if day not in (4, 11, 18, 25, 20)
        }
        days["2026-10-02"] = {"kind": "work", "start": "15:00", "end": "23:00"}
        days["2026-10-03"] = {"kind": "work", "start": "07:00", "end": "15:00"}
        days["2026-10-04"] = {"kind": "weekly_off"}
        days["2026-10-11"] = {"kind": "annual_leave"}
        status, _ = self.json_request("/api/schedule", "PUT", {"month": "2026-10", "revision": "", "entries": [{
            "row_id": "report-1", "employee_id": first["id"], "section": "MARKET", "slot": 1, "days": days,
        }]})
        self.assertEqual(status, 200)

        status, report = self.json_request("/api/reports/check?month=2026-10")
        self.assertEqual(status, 200, report)
        by_type = {}
        for item in report["items"]:
            by_type.setdefault(item["type"], []).append(item)
        self.assertEqual(by_type["rest"][0]["date"], "2026-10-03")
        self.assertEqual(by_type["negative_leave"][0]["employee_id"], first["id"])
        self.assertEqual(by_type["no_records"][0]["employee_id"], second["id"])
        self.assertEqual(by_type["empty_days"][0]["dates"], ["2026-10-18", "2026-10-20", "2026-10-25"])
        self.assertTrue(by_type["holiday_work"])
        self.assertEqual(report["counts"]["error"], 2)

        status, headers, body = self.request("/api/reports/payroll?month=2026-10&format=xlsx")
        self.assertEqual(status, 200)
        self.assertIn("BORDRO_2026-10.xlsx", headers["Content-Disposition"])
        sheet = load_workbook(BytesIO(body)).active
        header = [cell.value for cell in sheet[3]]
        row = next(r for r in sheet.iter_rows(min_row=4, values_only=True) if r[1] == "Rapor Bir")
        self.assertEqual(row[header.index("Çalışılan gün (X)")], 26)
        self.assertEqual(row[header.index("Hafta tatili (H)")], 1)
        self.assertEqual(row[header.index("Yıllık izin (S)")], 1)
        self.assertEqual(row[header.index("Boş gün")], 3)
        self.assertTrue(str(sheet.cell(sheet.max_row, 3).value).startswith("=SUM("))
        status, _, body = self.request("/api/reports/payroll?month=2026-10&format=csv")
        text = body.decode("utf-8-sig")
        self.assertTrue(text.startswith("Sıra;Personel;"))
        self.assertIn("Rapor Bir;26;", text)
        self.assertEqual(self.request("/api/reports/payroll?month=2026-10&format=pdf")[0], 400)

        status, headers, body = self.request(f"/api/reports/person-pdf?month=2026-10&employee_id={first['id']}")
        self.assertEqual(status, 200)
        self.assertIn("Rapor_Bir", headers["Content-Disposition"])
        self.assertEqual(len(PdfReader(BytesIO(body)).pages), 1)
        status, _, body = self.request("/api/reports/person-pdf?month=2026-10")
        self.assertEqual(len(PdfReader(BytesIO(body)).pages), 1)
        self.assertEqual(self.request("/api/reports/person-pdf?month=2026-10&employee_id=9999")[0], 400)

    def test_weekly_rest_warning_and_yearly_report(self):
        person = self.add_person("Yıllık Kişi")
        days = {f"2026-10-{day:02d}": {"kind": "work", "start": "09:00", "end": "17:00"} for day in range(1, 32)}
        for day in (9, 16, 23, 30):
            days[f"2026-10-{day:02d}"] = {"kind": "weekly_off"}
        days["2026-10-20"] = {"kind": "sick_leave"}
        status, result = self.json_request("/api/schedule", "PUT", {"month": "2026-10", "revision": "", "entries": [{
            "row_id": "yearly-1", "employee_id": person["id"], "section": "MARKET", "slot": 1, "days": days,
        }]})
        self.assertEqual(status, 200, result)
        status, report = self.json_request("/api/reports/check?month=2026-10")
        rest = [item for item in report["items"] if item["type"] == "weekly_rest"]
        self.assertEqual([item["date"] for item in rest], ["2026-10-01"])
        self.assertIn("8 gün", rest[0]["message"])

        status, headers, body = self.request("/api/reports/yearly?year=2026")
        self.assertEqual(status, 200)
        self.assertIn("YILLIK_PUANTAJ_2026.xlsx", headers["Content-Disposition"])
        workbook = load_workbook(BytesIO(body))
        self.assertEqual(workbook.sheetnames[0], "Yıllık toplam")
        sheet = workbook["Yıllık toplam"]
        header = [cell.value for cell in sheet[3]]
        row = next(r for r in sheet.iter_rows(min_row=4, values_only=True) if r[1] == "Yıllık Kişi")
        self.assertEqual(row[header.index("Çalışılan gün (X)")], 26)
        self.assertEqual(row[header.index("Hafta tatili (H)")], 4)
        self.assertEqual(row[header.index("Raporlu (R)")], 1)
        monthly = workbook["Aylık çalışılan gün (X)"]
        month_header = [cell.value for cell in monthly[3]]
        person_row = next(r for r in monthly.iter_rows(min_row=4, values_only=True) if r[1] == "Yıllık Kişi")
        self.assertEqual(person_row[month_header.index("EKİM")], 26)
        self.assertEqual(person_row[month_header.index("EYLÜL")], 0)
        self.assertEqual(self.request("/api/reports/yearly?year=abc")[0], 400)

    def test_automatic_backup_retention_only_prunes_automatic_backups(self):
        directory = Path(self.temp.name) / "backups"
        directory.mkdir(exist_ok=True)
        for day in range(1, 10):
            (directory / f"automatic-2020-01-{day:02d}.sqlite3").write_bytes(b"x")
        (directory / "automatic-2020-01-01.sqlite3.templates").mkdir()
        status, manual = self.json_request("/api/backups", "POST", {})
        self.assertEqual(status, 201)
        self.assertEqual(self.json_request("/api/backups")[1]["settings"], {"keep_automatic": 0})
        self.assertEqual(self.json_request("/api/backup-settings", "PUT", {"keep_automatic": 3})[0], 400)
        status, result = self.json_request("/api/backup-settings", "PUT", {"keep_automatic": 7})
        self.assertEqual(status, 200, result)
        self.assertEqual(sorted(result["removed"]), ["automatic-2020-01-01.sqlite3", "automatic-2020-01-02.sqlite3"])
        self.assertFalse((directory / "automatic-2020-01-01.sqlite3.templates").exists())
        names = {item["id"] for item in self.json_request("/api/backups")[1]["backups"]}
        self.assertIn(manual["backup"]["id"], names)
        self.assertEqual(sum(name.startswith("automatic-") for name in names), 7)

    def test_workbook_formatting_and_notes_persist_and_export(self):
        settings = {"sheets": {
            "schedule:2026-10:1": {"cells": {
                "35:4": {"color": "#000000"},
                "6:4": {"color": "#ddebf7"}, "6:5": {"color": "#ddebf7"},
            }, "hide_notes": True, "zoom": 75},
            "attendance:2026-10:0": {"cells": {}, "hide_notes": True, "zoom": 90},
        }, "quick_colors": {"weekly-off": "#fff2cc"}}
        status, result = self.json_request("/api/workbook-settings", "PUT", settings)
        self.assertEqual(status, 200)
        self.assertEqual(result, settings)
        self.assertEqual(self.json_request("/api/workbook-settings")[1], settings)
        status, _, body = self.request("/api/export?kind=schedule&month=2026-10")
        self.assertEqual(status, 200)
        workbook = load_workbook(BytesIO(body))
        ws = workbook.worksheets[1]
        self.assertTrue(ws.row_dimensions[34].hidden)
        self.assertEqual(ws["N35"].value, "İSTASYON MÜDÜRÜ")
        self.assertEqual(ws.max_row, 35)
        self.assertEqual(ws.sheet_view.zoomScale, 75)
        self.assertEqual(ws["D6"].fill.fgColor.rgb[-6:], "DDEBF7")
        self.assertEqual(ws["E6"].fill.fgColor.rgb[-6:], "DDEBF7")
        self.assertEqual(ws["D35"].fill.fgColor.rgb[-6:], "000000")
        self.assertEqual(ws["D35"].font.color.rgb[-6:], "FFFFFF")
        self.assertEqual(workbook.worksheets[0]["D6"].fill.fgColor.rgb[-6:], "FFFFFF")
        status, _, body = self.request("/api/export?kind=attendance&month=2026-10")
        self.assertEqual(status, 200)
        ws = load_workbook(BytesIO(body)).active
        self.assertTrue(ws.row_dimensions[25].hidden)
        self.assertEqual(ws.sheet_view.zoomScale, 90)

        for bad_cell in [{"color": "red"}, {"text": "=1+1"}]:
            invalid = {"sheets": {"schedule:2026-10:1": {
                "cells": {"6:4": bad_cell}, "hide_notes": False, "zoom": 55,
            }}, "quick_colors": {}}
            status, _ = self.json_request("/api/workbook-settings", "PUT", invalid)
            self.assertEqual(status, 400)
        self.assertEqual(self.json_request("/api/workbook-settings")[1], settings)

    def test_health_repair_whats_new_and_version_backup(self):
        from server import record_version_start
        from storage import Store
        from version import APP_VERSION

        status, _, body = self.request("/api/health")
        health = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(health["version"], APP_VERSION)
        self.assertIn("no-backup", {item["code"] for item in health["issues"]})
        self.assertFalse(any(item["level"] == "error" for item in health["issues"]))

        data_path = Path(self.temp.name) / "data.sqlite3"
        with Store(data_path) as store:
            with store.connection:
                store._write_setting("employee_profiles", {"999999": {"phone": "x"}})
        status, _, body = self.request("/api/health")
        self.assertIn("orphan:profiles", {item["code"] for item in json.loads(body)["issues"]})
        status, _, body = self.request("/api/health/repair", "POST", {})
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(result["backup"].startswith("before-repair") or "before-repair" in result["backup"])
        codes = {item["code"] for item in result["health"]["issues"]}
        self.assertNotIn("orphan:profiles", codes)
        self.assertNotIn("no-backup", codes)

        status, _, body = self.request("/api/whats-new")
        news = json.loads(body)
        self.assertEqual(news["version"], APP_VERSION)
        self.assertTrue(news["items"])
        self.request("/api/whats-new/seen", "POST", {})
        self.assertFalse(json.loads(self.request("/api/whats-new")[2])["unseen"])

        with Store(data_path) as store:
            with store.connection:
                store._write_setting("app_version", {"current": "0.9.0"})
        self.assertEqual(record_version_start(data_path), {"changed": True, "previous": "0.9.0"})
        with Store(data_path) as store:
            info = store._setting("app_version", {})
            self.assertEqual(info["current"], APP_VERSION)
            self.assertIn("before-update", info["backup"])
            self.assertTrue(any("before-update" in item["id"] for item in store.backups()))
        self.assertEqual(record_version_start(data_path), {"changed": False})

    def test_app_info_reports_active_source_and_executable_paths(self):
        from version import APP_VERSION

        status, source_info = self.json_request("/api/app-info")
        self.assertEqual(status, 200)
        self.assertEqual(source_info["version"], APP_VERSION)
        self.assertEqual(source_info["runtime"], "source")
        self.assertEqual(source_info["executable_path"], "")
        self.assertEqual(source_info["application_path"], str(ROOT.resolve()))
        self.assertEqual(source_info["data_path"], str(self.server.data_path.resolve()))
        self.assertEqual(source_info["branch"], {"id": "ana", "name": "Ana şube"})

        executable = Path(self.temp.name) / "VardiyaPuantaj.exe"
        self.server.app_executable = executable
        status, exe_info = self.json_request("/api/app-info")
        self.assertEqual(status, 200)
        self.assertEqual(exe_info["runtime"], "exe")
        self.assertEqual(exe_info["executable_path"], str(executable.resolve()))
        self.assertEqual(exe_info["application_path"], str(executable.resolve().parent))
        self.assertEqual(exe_info["data_path"], source_info["data_path"])

    def test_app_info_identifies_running_branch_not_next_startup_selection(self):
        directory = Path(self.temp.name)
        (directory / "branches.json").write_text(json.dumps({
            "active": "ana", "branches": [{"id": "dogu", "name": "Doğu istasyonu"}],
        }), encoding="utf-8")
        branch_path = directory / "subeler" / "dogu" / "vardiya_puantaj.sqlite3"
        with patch.object(self.server, "data_path", branch_path):
            status, result = self.json_request("/api/app-info")
        self.assertEqual(status, 200)
        self.assertEqual(result["branch"], {"id": "dogu", "name": "Doğu istasyonu"})
        self.assertEqual(result["data_path"], str(branch_path.resolve()))

    def test_app_info_is_available_in_help_and_update_menu(self):
        status, _, feature_script = self.request("/features.js")
        self.assertEqual(status, 200)
        self.assertIn(b'["Uygulama bilgisi", openAppInfo]', feature_script)
        status, _, easy_mode_script = self.request("/easy-mode.js")
        self.assertEqual(status, 200)
        self.assertIn(b'"Uygulama bilgisi"', easy_mode_script)

    def test_update_refuses_incompatible_candidate_before_changing_executable(self):
        root = Path(self.temp.name)
        current = root / "VardiyaPuantaj.exe"
        candidate = root / "update" / current.name
        candidate.parent.mkdir()
        current.write_bytes(b"synthetic-current-executable")
        candidate.write_bytes(b"synthetic-incompatible-candidate")

        def write_manifest(executable, minimum, maximum):
            manifest = {
                "format": "vardiya-schema-compatibility", "version": 1,
                "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "minimum_schema": minimum, "maximum_schema": maximum,
            }
            Path(f"{executable}.schema.json").write_text(json.dumps(manifest), encoding="utf-8")

        write_manifest(current, 0, 1)
        write_manifest(candidate, 2, 4)
        self.server.app_executable = current
        with app_server.Store(self.server.data_path) as store, store.connection:
            store._write_setting("update_settings", {"folder": str(candidate.parent)})
        before_current, before_candidate = current.read_bytes(), candidate.read_bytes()

        status, info = self.json_request("/api/update")
        self.assertEqual(status, 200)
        self.assertTrue(info["available"])
        self.assertFalse(info["schema_compatibility"]["compatible"])
        self.assertIn("desteklemiyor", info["schema_compatibility"]["reason"])
        status, refusal = self.json_request("/api/update/apply", "POST", {})
        self.assertEqual(status, 400)
        self.assertIn("uyumluluk ön denetimi", refusal["error"])
        self.assertEqual(current.read_bytes(), before_current)
        self.assertEqual(candidate.read_bytes(), before_candidate)
        self.assertFalse(Path(f"{current}.onceki").exists())

    def test_update_restart_waits_for_loaded_window_and_does_not_bypass_execution_policy(self):
        import main
        import os
        import sys
        from types import SimpleNamespace

        class Event:
            def __init__(self):
                self.callbacks = []

            def __iadd__(self, callback):
                self.callbacks.append(callback)
                return self

            def fire(self):
                for callback in self.callbacks:
                    callback()

        window = SimpleNamespace(events=SimpleNamespace(loaded=Event()))
        fake_webview = SimpleNamespace(
            create_window=lambda *args, **kwargs: window,
            start=lambda **kwargs: window.events.loaded.fire(),
        )
        fake_server = SimpleNamespace(
            data_path=Path(self.temp.name) / "database.sqlite3",
            server_address=("127.0.0.1", 43127),
            internal_token="synthetic-internal-token",
            serve_forever=lambda: None,
            shutdown=lambda: None,
            server_close=lambda: None,
            lock=SimpleNamespace(release=lambda: None),
            request_exit=None,
        )
        status_path = Path(self.temp.name) / "vardiya-update-acceptance.json"
        with patch.dict(os.environ, {"VARDIYA_PUANTAJ_UPDATE_STATUS_PATH": str(status_path)}), \
                patch.dict("sys.modules", {"webview": fake_webview}), \
                patch("main.create_server", return_value=fake_server), \
                patch("mobile_service.diagnostics", return_value={"status": "ok"}), \
                patch("tempfile.gettempdir", return_value=self.temp.name), \
                patch.object(sys, "frozen", True, create=True):
            self.assertEqual(main.main(data_path=fake_server.data_path), 0)
        self.assertEqual(json.loads(status_path.read_text(encoding="utf-8"))["status"], "ready")

    def test_compatible_update_and_rollback_start_with_verified_backup(self):
        from storage import SCHEMA_VERSION, Store

        root = Path(self.temp.name) / "Güncelleme 'özel'"
        root.mkdir()
        current = root / "VardiyaPuantaj.exe"
        update_folder = root / "update"
        update_folder.mkdir()
        candidate = update_folder / current.name
        previous = Path(f"{current}.onceki")
        current.write_bytes(b"synthetic-current-executable")
        candidate.write_bytes(b"synthetic-compatible-candidate")

        def write_manifest(executable):
            manifest = {
                "format": "vardiya-schema-compatibility", "version": 1,
                "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "minimum_schema": 0, "maximum_schema": SCHEMA_VERSION,
            }
            Path(f"{executable}.schema.json").write_text(json.dumps(manifest), encoding="utf-8")

        for executable in (current, candidate):
            write_manifest(executable)
        before_current_manifest = Path(f"{current}.schema.json").read_bytes()
        before_candidate_manifest = Path(f"{candidate}.schema.json").read_bytes()
        self.server.app_executable = current
        with Store(self.server.data_path) as store, store.connection:
            store._write_setting("update_settings", {"folder": str(update_folder)})
            schema = int(store.connection.execute("PRAGMA user_version").fetchone()[0])
        before_current = current.read_bytes()
        before_candidate = candidate.read_bytes()
        script_directory = root / "scripts"
        script_directory.mkdir()
        exits = []
        self.server.request_exit = lambda: exits.append(True)

        def decoded_script(call):
            arguments = call.args[0]
            self.assertEqual(arguments[0], "powershell.exe")
            self.assertIn("-EncodedCommand", arguments)
            return base64.b64decode(arguments[-1]).decode("utf-16le")

        powershell = shutil.which("powershell.exe")

        def execute_synthetic_script(
            source, index, *, release_process=True, expected_returncode=0, signal_ready=True,
        ):
            if release_process:
                source = source.replace(f"$processId = {os.getpid()}", "$processId = -1", 1)
            source = source.replace("Show-FailureDialog $failure", "Write-Output $failure")
            launch_stub = "function Start-Process { "
            launch_stub += (
                "if ($env:PYINSTALLER_RESET_ENVIRONMENT -ne '1') "
                "{ throw 'PyInstaller reset flag missing for child process.' }; "
            )
            if signal_ready:
                launch_stub += (
                    "[IO.File]::WriteAllText($env:VARDIYA_PUANTAJ_UPDATE_STATUS_PATH, "
                    "'{\"status\":\"ready\",\"message\":\"\"}'); "
                )
            launch_stub += (
                "$process = [pscustomobject]@{ Id = 987654; HasExited = $true }; "
                "$process | Add-Member -MemberType ScriptMethod -Name Refresh -Value {}; "
                "return $process }"
            )
            source = launch_stub + "\n" + source
            encoded = base64.b64encode(source.encode("utf-16le")).decode("ascii")
            result = subprocess.run(
                [
                    powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                    "-EncodedCommand", encoded,
                ],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, expected_returncode, result.stderr or result.stdout)

        with patch("subprocess.Popen") as launch, patch(
            "tempfile.gettempdir", return_value=str(script_directory),
        ):
            status, update = self.json_request("/api/update/apply", "POST", {})
            self.assertEqual(status, 200, update)
            self.assertTrue(update["started"])
            self.assertEqual(launch.call_count, 1)
            update_script = decoded_script(launch.call_args)
            self.assertIn(str(current).replace("'", "''"), update_script)
            self.assertIn("Get-Process -Id $processId", update_script)
            self.assertIn("[IO.FileShare]::None", update_script)
            self.assertIn("AddSeconds(120)", update_script)
            self.assertIn("AddSeconds(60)", update_script)
            self.assertIn("VARDIYA_PUANTAJ_UPDATE_STATUS_PATH", update_script)
            self.assertIn("$env:PYINSTALLER_RESET_ENVIRONMENT = '1'", update_script)
            self.assertIn("Show-FailureDialog $failure", update_script)
            self.assertIn("[System.Windows.Forms.MessageBox]::Show", update_script)
            self.assertLess(update_script.index("$deadline ="), update_script.index("$commitStarted = $true"))
            self.assertLess(
                update_script.index("$commitStarted = $true"),
                update_script.index("[IO.File]::Move($current, $removedCurrent)"),
            )
            self.assertIn(
                f"$expectedCandidateHash = '{hashlib.sha256(before_candidate).hexdigest()}'",
                update_script,
            )
            self.assertIn(
                "Assert-ExecutablePair $current $currentManifest $expectedCandidateHash "
                "$expectedCandidateManifestHash",
                update_script,
            )
            self.assertIn("Assert-ExecutablePair $previous $previousManifest", update_script)
            self.assertIn("Start-Process -FilePath $current", update_script)
            self.assertIn("-WindowStyle Normal", update_script)
            self.assertLess(
                update_script.index("$env:PYINSTALLER_RESET_ENVIRONMENT = '1'"),
                update_script.index("Start-Process -FilePath $current"),
            )
            self.assertNotIn("-ExecutionPolicy", update_script)
            self.assertNotIn("-ExecutionPolicy", launch.call_args.args[0])
            self.assertLess(
                update_script.index("Assert-ExecutablePair $previous $previousManifest"),
                update_script.index("Start-Process -FilePath $current"),
            )
            self.assertEqual(current.read_bytes(), before_current)
            self.assertEqual(candidate.read_bytes(), before_candidate)
            self.assertFalse(previous.exists())
            with Store(self.server.data_path) as store:
                self.assertTrue(store.verify_backup(update["backup"])["valid"])
                self.assertEqual(
                    int(store.connection.execute("PRAGMA user_version").fetchone()[0]),
                    schema,
                )

        if powershell:
            timeout_script = update_script.replace("AddSeconds(120)", "AddSeconds(1)", 1)
            execute_synthetic_script(
                timeout_script, 0, release_process=False, expected_returncode=1,
            )
            status_file = Path(update["status_path"])
            timeout_result = json.loads(status_file.read_text(encoding="utf-8-sig"))
            self.assertEqual(timeout_result["status"], "failed")
            self.assertIn("120 saniye içinde kapanmadı", timeout_result["message"])
            self.assertEqual(current.read_bytes(), before_current)
            self.assertEqual(Path(f"{current}.schema.json").read_bytes(), before_current_manifest)
            self.assertEqual(candidate.read_bytes(), before_candidate)
            self.assertFalse(previous.exists())
            status_file.unlink()
            candidate_manifest_path = Path(f"{candidate}.schema.json")
            changed_manifest = json.loads(before_candidate_manifest)
            changed_manifest["maximum_schema"] += 1
            candidate_manifest_path.write_text(json.dumps(changed_manifest), encoding="utf-8")
            execute_synthetic_script(update_script, 0, expected_returncode=1)
            mismatch_result = json.loads(status_file.read_text(encoding="utf-8-sig"))
            self.assertEqual(mismatch_result["status"], "failed")
            self.assertIn("beklenen sürümle eşleşmiyor", mismatch_result["message"])
            self.assertEqual(current.read_bytes(), before_current)
            self.assertEqual(Path(f"{current}.schema.json").read_bytes(), before_current_manifest)
            self.assertFalse(previous.exists())
            candidate_manifest_path.write_bytes(before_candidate_manifest)
            status_file.unlink()
            failed_start_script = update_script.replace("AddSeconds(60)", "AddSeconds(1)", 1)
            failed_start_script = failed_start_script.replace("AddSeconds(20)", "AddSeconds(1)", 1)
            execute_synthetic_script(
                failed_start_script, 0, expected_returncode=1, signal_ready=False,
            )
            startup_result = json.loads(status_file.read_text(encoding="utf-8-sig"))
            self.assertEqual(startup_result["status"], "failed")
            self.assertIn("Eski sürüm geri yüklendi", startup_result["message"])
            self.assertEqual(current.read_bytes(), before_current)
            self.assertEqual(Path(f"{current}.schema.json").read_bytes(), before_current_manifest)
            self.assertFalse(previous.exists())
            status_file.unlink()
            execute_synthetic_script(update_script, 1)
        else:
            previous.write_bytes(before_current)
            Path(f"{previous}.schema.json").write_bytes(before_current_manifest)
            current.write_bytes(before_candidate)
            Path(f"{current}.schema.json").write_bytes(before_candidate_manifest)
        self.assertEqual(current.read_bytes(), before_candidate)
        self.assertEqual(previous.read_bytes(), before_current)
        self.assertEqual(
            json.loads(Path(f"{current}.schema.json").read_text(encoding="utf-8"))["executable_sha256"],
            hashlib.sha256(before_candidate).hexdigest(),
        )
        self.assertEqual(
            json.loads(Path(f"{previous}.schema.json").read_text(encoding="utf-8"))["executable_sha256"],
            hashlib.sha256(before_current).hexdigest(),
        )

        with patch("subprocess.Popen") as launch, patch(
            "tempfile.gettempdir", return_value=str(script_directory),
        ):
            status, rollback = self.json_request("/api/update/rollback", "POST", {})
            self.assertEqual(status, 200, rollback)
            self.assertTrue(rollback["started"])
            self.assertEqual(launch.call_count, 1)
            rollback_script = decoded_script(launch.call_args)
            escaped_previous = str(previous).replace("'", "''")
            self.assertIn(f"$candidate = '{escaped_previous}'", rollback_script)
            self.assertIn("Assert-ExecutablePair $candidate $candidateManifest", rollback_script)
            self.assertIn("[IO.File]::Move($removedCurrent, $previous)", rollback_script)
            self.assertIn("Start-Process -FilePath $current", rollback_script)
            self.assertIn("$env:PYINSTALLER_RESET_ENVIRONMENT = '1'", rollback_script)
            self.assertLess(
                rollback_script.index("$env:PYINSTALLER_RESET_ENVIRONMENT = '1'"),
                rollback_script.index("Start-Process -FilePath $current"),
            )
            self.assertEqual(current.read_bytes(), before_candidate)
            self.assertEqual(previous.read_bytes(), before_current)
            with Store(self.server.data_path) as store:
                self.assertTrue(store.verify_backup(rollback["backup"])["valid"])

        if powershell:
            execute_synthetic_script(rollback_script, 2)
        self.assertEqual(current.read_bytes(), before_current if powershell else before_candidate)
        self.assertEqual(previous.read_bytes(), before_candidate if powershell else before_current)
        self.assertEqual(exits, [True, True])
        self.assertEqual(candidate.read_bytes(), before_candidate)

    def test_settings_import_stages_groups_with_verified_backup_and_partial_completion(self):
        import workspace_tools
        with app_server.Store(self.server.data_path) as store:
            data = workspace_tools.export_settings(store)
            original_quick_entries = store._setting("quick_entries", [])
        data["settings"]["overtime_settings"]["daily_limit_hours"] = 11
        data["settings"]["quick_entries"] = "invalid synthetic setting"
        keys = ["overtime_settings", "quick_entries"]

        status, preview = self.json_request("/api/workspace/settings-import/preview", "POST", {
            "data": data, "keys": keys,
        })
        self.assertEqual(status, 200, preview)
        self.assertEqual(preview["changed"], 2)
        self.assertEqual([item["key"] for item in preview["groups"]], keys)

        status, prepared = self.json_request("/api/workspace/settings-import/backup", "POST", {})
        self.assertEqual(status, 200, prepared)
        backup_id = prepared["backup"]
        with app_server.Store(self.server.data_path) as store:
            self.assertTrue(store.verify_backup(backup_id)["valid"])
            self.assertNotEqual(store.overtime_settings()["daily_limit_hours"], 11)

        status, first = self.json_request("/api/workspace/settings-import", "POST", {
            "data": data, "keys": ["overtime_settings"], "backup": backup_id,
        })
        self.assertEqual(status, 200, first)
        self.assertEqual(first["applied"], ["Fazla mesai kuralları"])
        self.assertEqual(first["backup"], backup_id)
        with app_server.Store(self.server.data_path) as store:
            self.assertEqual(store.overtime_settings()["daily_limit_hours"], 11)
            self.assertEqual(store._setting("quick_entries", []), original_quick_entries)

        status, refusal = self.json_request("/api/workspace/settings-import", "POST", {
            "data": data, "keys": ["overtime_settings", "quick_entries"], "backup": backup_id,
        })
        self.assertEqual(status, 400)
        self.assertIn("tek bir grup", refusal["error"])
        with app_server.Store(self.server.data_path) as store:
            self.assertEqual(store._setting("quick_entries", []), original_quick_entries)

    def test_settings_import_ui_reports_stages_safe_cancel_and_completed_groups(self):
        status, _, script = self.request("/workspace.js")
        self.assertEqual(status, 200)
        for expected in (
            "Ayar dosyası okunuyor", "Ayar grupları mevcut değerlerle karşılaştırılıyor",
            "Güvenlik yedeği hazırlanıyor", "Devam eden tek ayar grubu tamamlanacak",
            "güvenli bir grup sınırında", "Başlatılmayanlar:",
        ):
            self.assertIn(expected.encode("utf-8"), script)

    def test_rollback_refuses_incompatible_previous_executable_without_replacing_current(self):
        root = Path(self.temp.name)
        current = root / "VardiyaPuantaj.exe"
        previous = Path(f"{current}.onceki")
        current.write_bytes(b"synthetic-current-executable")
        previous.write_bytes(b"synthetic-previous-executable")

        def write_manifest(executable, minimum, maximum):
            manifest = {
                "format": "vardiya-schema-compatibility", "version": 1,
                "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "minimum_schema": minimum, "maximum_schema": maximum,
            }
            Path(f"{executable}.schema.json").write_text(json.dumps(manifest), encoding="utf-8")

        write_manifest(current, 0, 1)
        write_manifest(previous, 0, 0)
        self.server.app_executable = current
        before_current, before_previous = current.read_bytes(), previous.read_bytes()
        status, refusal = self.json_request("/api/update/rollback", "POST", {})
        self.assertEqual(status, 400)
        self.assertIn("uyumluluk ön denetimi", refusal["error"])
        self.assertEqual(current.read_bytes(), before_current)
        self.assertEqual(previous.read_bytes(), before_previous)
        status, health = self.json_request("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["rollback_available"])
        self.assertFalse(health["rollback_compatibility"]["compatible"])
        status, _, script = self.request("/features.js")
        self.assertEqual(status, 200)
        self.assertIn(b"rollbackCompatible", script)

    def test_ui_settings_template_library_and_personnel_bulk(self):
        status, result = self.json_request("/api/ui-settings")
        self.assertEqual(status, 200)
        self.assertTrue(result["settings"]["hide_empty_rows"])
        self.assertEqual(result["settings"]["shift_types"][0]["id"], "sabah")
        status, result = self.json_request("/api/ui-settings", "PUT", {
            "shortcuts": {"zoom-in": "Ctrl+Shift+Z", "jump": ""}, "density": "compact",
        })
        self.assertEqual(status, 200, result)
        self.assertEqual(result["settings"]["shortcuts"], {"zoom-in": "Ctrl+Shift+Z", "jump": ""})
        self.assertTrue(result["settings"]["hide_empty_rows"])
        status, result = self.json_request("/api/ui-settings", "PUT", {
            "shortcuts": {"zoom-in": "Ctrl+M", "zoom-out": "Ctrl+M"},
        })
        self.assertEqual(status, 400)
        self.assertIn("iki işleme", result["error"])
        self.assertEqual(self.json_request("/api/ui-settings", "PUT", {"bogus": 1})[0], 400)
        self.assertEqual(self.json_request("/api/ui-settings")[1]["settings"]["density"], "compact")

        import ui_settings
        settings = ui_settings.merged({})
        self.assertEqual(ui_settings.classify_shift("07:00", "15:00", settings), "sabah")
        self.assertEqual(ui_settings.classify_shift("16:00", "00:00", settings), "aksam")
        self.assertEqual(ui_settings.classify_shift("23:00", "07:00", settings), "gece")
        self.assertEqual(ui_settings.classify_shift("09:00", "13:00", settings), "yarim")

        item = {"name": "Kış düzeni", "kind": "schedule", "sheets": {"0": {"cells": {}, "hide_notes": False, "zoom": 70}}}
        status, result = self.json_request("/api/template-library", "PUT", {"items": [item]})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.json_request("/api/template-library")[1]["items"][0]["name"], "Kış düzeni")
        status, result = self.json_request("/api/template-library", "PUT", {"items": [item, item]})
        self.assertEqual(status, 400)

        first, second = self.add_person("Liste Bir"), self.add_person("Liste İki")
        status, result = self.json_request("/api/personnel/bulk", "POST", {
            "ids": [first["id"], second["id"]], "action": "section", "value": "MARKET",
        })
        self.assertEqual((status, result["done"]), (200, [first["id"], second["id"]]))
        self.json_request("/api/personnel/bulk", "POST", {"ids": [second["id"]], "action": "deactivate"})
        overview = {item["name"]: item for item in self.json_request("/api/personnel-overview")[1]["people"]}
        self.assertEqual(overview["Liste Bir"]["home_section"], "MARKET")
        self.assertFalse(overview["Liste İki"]["active"])
        self.json_request("/api/schedule", "PUT", {"month": "2026-10", "revision": "", "entries": [{
            "row_id": "bulk-del", "employee_id": first["id"], "section": "MARKET", "slot": 1,
            "days": {"2026-10-12": {"kind": "work", "start": "09:00", "end": "17:00"}},
        }]})
        status, result = self.json_request("/api/personnel/bulk", "POST", {
            "ids": [first["id"], second["id"]], "action": "delete",
        })
        self.assertEqual((result["done"], result["blocked"]), ([second["id"]], [first["id"]]))
        self.assertEqual(self.json_request("/api/personnel/bulk", "POST", {"ids": [], "action": "delete"})[0], 400)

    def test_midnight_end_accepts_24_00_and_exports_as_24_hours(self):
        person = self.add_person("Gece Yarısı")
        status, saved = self.json_request("/api/schedule", "PUT", {"month": "2026-10", "revision": "", "entries": [{
            "row_id": "midnight", "employee_id": person["id"], "section": "MARKET", "slot": 1,
            "days": {"2026-10-12": {"kind": "work", "start": "16:00", "end": "24:00", "shift_type": "aksam"}},
        }]})
        self.assertEqual(status, 200, saved)
        day = self.json_request("/api/schedule?month=2026-10")[1]["entries"][0]["days"]["2026-10-12"]
        self.assertEqual((day["end"], day["shift_type"]), ("00:00", "aksam"))
        status, _, body = self.request("/api/export?kind=schedule&month=2026-10")
        self.assertEqual(status, 200)
        from datetime import datetime, timedelta
        workbook = load_workbook(BytesIO(body))
        midnight = [cell for ws in workbook.worksheets for row in ws.iter_rows() for cell in row
                    if cell.number_format == "[hh]:mm" and cell.value in (1, timedelta(days=1), datetime(1899, 12, 31))]
        self.assertTrue(midnight, "24:00 bitiş hücresi [hh]:mm biçiminde 1 gün olarak yazılmalı")
        status, entries = self.json_request("/api/quick-entries", "PUT", {"entries": [
            {"id": "preset-weekly-off", "preset": "weekly-off", "label": "Hafta tatili", "code": "H",
             "start": "", "end": "", "hidden": True},
            {"id": "evening", "label": "Akşam", "code": "X", "start": "16:00", "end": "24:00", "shift_type": "aksam"},
        ]})
        self.assertEqual(status, 200, entries)
        self.assertTrue(entries["entries"][0]["hidden"])
        self.assertEqual(entries["entries"][1]["end"], "00:00")

    def test_min_staffing_and_warning_rules_hide_without_blocking(self):
        person = self.add_person("Kadro Bir")
        days = {f"2026-10-{day:02d}": {"kind": "work", "start": "09:00", "end": "17:00"} for day in range(1, 32)}
        status, _ = self.json_request("/api/schedule", "PUT", {"month": "2026-10", "revision": "", "entries": [{
            "row_id": "staff-1", "employee_id": person["id"], "section": "MARKET", "slot": 1, "days": days,
        }]})
        self.assertEqual(status, 200)
        status, settings = self.json_request("/api/compliance-settings", "PUT", {"min_staffing": {"MARKET": 2, "TEMİZLİK": 0}})
        self.assertEqual(status, 200, settings)
        self.assertEqual(settings["min_staffing"], {"MARKET": 2})
        self.assertEqual(settings["legal_rest_hours"], 11.0)
        status, settings = self.json_request("/api/compliance-settings", "PUT", {"legal_rest_hours": 10})
        self.assertEqual(settings["min_staffing"], {"MARKET": 2}, "diğer ayarı kaydetmek asgari kadroyu silmemeli")
        self.assertEqual(self.json_request("/api/compliance-settings", "PUT", {"min_staffing": {"MARKET": 120}})[0], 400)

        report = self.json_request("/api/reports/check?month=2026-10")[1]
        staffing = [item for item in report["items"] if item["type"] == "min_staffing"]
        self.assertEqual(len(staffing), 31)
        self.assertEqual(staffing[0]["name"], "MARKET")
        self.assertIn("1 çalışan; asgari kadro 2", staffing[0]["message"])
        self.assertEqual(report["ignored"], 0)
        before = report["counts"]["warning"]

        status, rules = self.json_request("/api/warning-rules/ignore", "POST", {"scope": "once", "item": staffing[0]})
        self.assertEqual(status, 200, rules)
        report = self.json_request("/api/reports/check?month=2026-10")[1]
        self.assertEqual((report["ignored"], report["counts"]["warning"]), (1, before - 1))
        self.json_request("/api/warning-rules/ignore", "POST", {"scope": "type", "item": staffing[1]})
        report = self.json_request("/api/reports/check?month=2026-10")[1]
        self.assertFalse([item for item in report["items"] if item["type"] == "min_staffing"])
        self.assertEqual(report["ignored"], 31)
        self.assertEqual(report["counts"]["warning"], before - 31)
        self.assertEqual(self.json_request("/api/warning-rules/ignore", "POST", {"scope": "forever", "item": staffing[0]})[0], 400)
        self.assertEqual(self.json_request("/api/warning-rules/ignore", "POST", {"scope": "once", "item": {}})[0], 400)
        # Uyarı kaydı engellemez: aynı plan yeniden kaydedilebilir.
        revision = self.json_request("/api/schedule?month=2026-10")[1]["revision"]
        status, _ = self.json_request("/api/schedule", "PUT", {"month": "2026-10", "revision": revision, "entries": [{
            "row_id": "staff-1", "employee_id": person["id"], "section": "MARKET", "slot": 1, "days": days,
        }]})
        self.assertEqual(status, 200)
        status, rules = self.json_request("/api/warning-rules", "PUT", {"once": [], "person": [], "type": []})
        self.assertEqual((status, rules["rules"]["type"]), (200, []))
        self.assertEqual(self.json_request("/api/reports/check?month=2026-10")[1]["ignored"], 0)

    def test_time_window_min_staffing_counts_concurrent_workers_and_overnight_shifts(self):
        first, second, third = (
            self.add_person("Saatli Kadro 1"),
            self.add_person("Saatli Kadro 2"),
            self.add_person("Saatli Kadro 3"),
        )
        work = lambda start, end: {"kind": "work", "start": start, "end": end}
        status, _ = self.json_request("/api/schedule", "PUT", {"month": "2026-09", "revision": "", "entries": [{
            "row_id": "night-before", "employee_id": first["id"], "section": "MARKET", "slot": 1,
            "days": {"2026-09-30": work("22:00", "02:00")},
        }]})
        self.assertEqual(status, 200)
        status, _ = self.json_request("/api/schedule", "PUT", {"month": "2026-10", "revision": "", "entries": [
            {"row_id": "early", "employee_id": first["id"], "section": "MARKET", "slot": 1,
             "days": {"2026-10-05": work("08:00", "16:00")}},
            {"row_id": "mid", "employee_id": second["id"], "section": "MARKET", "slot": 2,
             "days": {"2026-10-05": work("09:00", "12:00"), "2026-10-01": work("00:00", "04:00")}},
            {"row_id": "late", "employee_id": third["id"], "section": "MARKET", "slot": 3,
             "days": {"2026-10-05": work("13:00", "16:00")}},
        ]})
        self.assertEqual(status, 200)
        status, settings = self.json_request("/api/compliance-settings", "PUT", {"min_staffing_windows": [
            {"section": "MARKET", "start": "00:00", "end": "02:00", "minimum": 2},
            {"section": "MARKET", "start": "09:00", "end": "12:00", "minimum": 2},
            {"section": "MARKET", "start": "12:00", "end": "16:00", "minimum": 2},
        ]})
        self.assertEqual(status, 200, settings)
        self.assertEqual(settings["min_staffing_windows"][0]["end"], "02:00")
        self.assertEqual(self.json_request("/api/compliance-settings", "PUT", {"min_staffing_windows": [
            {"section": "MARKET", "start": "09:00", "end": "13:00", "minimum": 2},
            {"section": "MARKET", "start": "12:00", "end": "16:00", "minimum": 2},
        ]})[0], 400)
        report = self.json_request("/api/reports/check?month=2026-10")[1]
        staffing = [item for item in report["items"] if item["type"] == "min_staffing"]
        overnight = [item for item in staffing if item["date"] == "2026-10-01" and "00:00–02:00" in item["message"]]
        self.assertEqual(overnight, [], "the prior day's overnight shift covers the 00:00-02:00 window")
        gap = [item for item in staffing if item["date"] == "2026-10-05" and "12:00–13:00" in item["message"]]
        self.assertEqual(len(gap), 1)
        self.assertIn("en düşük sayı 1", gap[0]["message"])

    def test_attendance_correction_approves_only_one_cell_and_respects_period_lock(self):
        employee = self.add_person("Düzeltme Personeli")["id"]
        month, day, second = "2026-10", "2026-10-12", "2026-10-13"
        route = "/api/attendance/corrections"
        attendance = f"/api/attendance?month={month}"
        initial = {day: {str(employee): "X"}, second: {str(employee): "H"}}
        status, saved = self.json_request("/api/attendance", "PUT", {
            "month": month, "revision": "", "overrides": initial, "deductions": {str(employee): 12},
        })
        self.assertEqual(status, 200, saved)
        status, preview = self.json_request(f"{route}?month={month}&date={day}&employee_id={employee}")
        self.assertEqual(status, 200, preview)
        self.assertEqual(preview["code"], "X")
        status, created = self.json_request(route, "POST", {
            "month": month, "date": day, "employee_id": employee, "code": "R",
            "reason": "Kaynak karşılaştırıldı", "fingerprint": preview["fingerprint"],
        })
        self.assertEqual(status, 201, created)
        self.assertEqual(self.json_request(attendance)[1]["overrides"], initial)
        decision = {"month": month, "id": created["id"], "approve": True}
        from storage import Store
        with Store(self.server.data_path) as store:
            store.set_period_locked(month, True)
        self.assertEqual(self.json_request(route, "PUT", decision)[0], 400)
        with Store(self.server.data_path) as store:
            store.set_period_locked(month, False)
        self.assertEqual(self.json_request(route, "PUT", decision)[0], 200)
        actual = self.json_request(attendance)[1]
        self.assertEqual(actual["overrides"], {**initial, day: {str(employee): "R"}})
        self.assertEqual(actual["deductions"], {str(employee): 12})
        self.assertEqual(self.json_request(route, "PUT", decision)[0], 400)

    def test_attendance_correction_marks_ambiguous_pdks_names_without_assigning_punches(self):
        from storage import Store

        first = self.add_person("Ipek Kaya")["id"]
        self.add_person("İpek Kaya")
        month, day = "2026-10", "2026-10-12"
        self.assertEqual(self.json_request("/api/attendance", "PUT", {
            "month": month, "revision": "", "overrides": {day: {str(first): "X"}},
        })[0], 200)
        with Store(self.server.data_path) as store, store.connection:
            store._write_setting("pdks_records", {month: {"records": [{
                "name": "Ipek Kaya", "date": day, "in": "08:00", "out": "16:00",
            }]}})
        status, evidence = self.json_request(
            f"/api/attendance/corrections?month={month}&date={day}&employee_id={first}",
        )
        self.assertEqual(status, 200, evidence)
        self.assertTrue(evidence["ambiguous"])
        self.assertEqual(evidence["punches"], [])

    def test_dated_overtime_rule_preserves_prior_month_and_requires_fresh_preview(self):
        from datetime import date
        from analytics import shift_month

        old_month = date.today().strftime("%Y-%m")
        new_month = shift_month(old_month, 1)
        route = "/api/overtime-settings/transition"
        baseline = self.json_request(f"/api/overtime-settings?month={old_month}")[1]
        payload = {"month": new_month, "settings": {**baseline, "daily_limit_hours": 7}}
        self.assertEqual(self.json_request(route, "POST", {**payload, "month": old_month})[0], 400)
        status, preview = self.json_request(route, "POST", payload)
        self.assertEqual(status, 200, preview)
        self.assertEqual(self.json_request(f"/api/overtime-settings?month={new_month}")[1], baseline)
        self.add_person("Geçiş Personeli")
        self.assertEqual(self.json_request(route, "PUT", {**payload, "preview": preview["preview"]})[0], 400)
        status, fresh = self.json_request(route, "POST", payload)
        self.assertEqual(status, 200, fresh)
        self.assertEqual(self.json_request(route, "PUT", {**payload, "preview": fresh["preview"]})[0], 200)
        for month, hours in ((old_month, baseline["daily_limit_hours"]), (new_month, 7)):
            self.assertEqual(self.json_request(f"/api/attendance?month={month}")[1]["overtime_settings"]["daily_limit_hours"], hours)
        self.assertEqual(self.json_request("/api/overtime-settings", "PUT", baseline)[0], 400)

    def test_protected_schedule_cell_rejects_direct_write_and_is_skipped_in_bulk(self):
        employee = self.add_person("Korunan Personel")["id"]
        month, day, other_day = "2026-10", "2026-10-12", "2026-10-13"
        row = {"row_id": "protected-row", "employee_id": employee, "section": "MARKET", "slot": 1}
        work = {"kind": "work", "start": "08:00", "end": "16:00"}
        status, saved = self.json_request("/api/schedule", "PUT", {
            "month": month, "entries": [{**row, "days": {day: work}}], "revision": "",
        })
        self.assertEqual(status, 200, saved)
        route = "/api/schedule/protection"
        protection = {"month": month, "row_id": row["row_id"], "date": day,
                      "revision": saved["revision"], "protected": True,
                      "reason": "Personel talebiyle kesinleşti"}
        self.assertEqual(self.json_request(route, "PUT", protection)[0], 200)
        _, protection_list = self.json_request(f"{route}?month={month}")
        self.assertEqual([(item["reason"], item["protected_by"]) for item in protection_list["cells"]],
                         [("Personel talebiyle kesinleşti", "Yerel kullanıcı")])
        proposed = [{**row, "days": {other_day: work}}]
        self.assertEqual(self.json_request("/api/schedule", "PUT", {
            "month": month, "entries": proposed, "revision": saved["revision"],
        })[0], 409)
        status, preview = self.json_request("/api/schedule/bulk-check", "POST", {
            "month": month, "months": {
                "2026-09": {"revision": "", "entries": []},
                month: {"revision": saved["revision"], "entries": proposed},
                "2026-11": {"revision": "", "entries": []},
            },
        })
        self.assertEqual(status, 200, preview)
        self.assertEqual([item["day"] for item in preview["skipped"]], [day])
        self.assertEqual(set(preview["months"][month]["entries"][0]["days"]), {day, other_day})
        self.assertEqual(self.json_request("/api/schedule", "PUT", {
            "month": month, **preview["months"][month],
        })[0], 200)
        revision = self.json_request(f"/api/schedule?month={month}")[1]["revision"]
        self.assertEqual(self.json_request(route, "PUT", {**protection, "protected": False})[0], 400)
        self.assertEqual(self.json_request(route, "PUT", {
            **protection, "protected": False, "revision": revision,
        })[0], 200)
        self.assertEqual(self.json_request("/api/schedule", "PUT", {
            "month": month, "entries": proposed, "revision": revision,
        })[0], 200)

    def test_existing_schedule_protection_rows_migrate_with_explicit_unknown_metadata(self):
        import schedule_protection
        from storage import Store

        path = Path(self.temp.name) / "legacy-protection.sqlite3"
        connection = sqlite3.connect(path)
        connection.execute(
            "CREATE TABLE schedule_protection (month TEXT NOT NULL, row_id TEXT NOT NULL, day TEXT NOT NULL, "
            "employee_id INTEGER NOT NULL, section TEXT NOT NULL, protected_at TEXT NOT NULL, "
            "PRIMARY KEY(month,row_id,day))"
        )
        connection.execute(
            "INSERT INTO schedule_protection VALUES ('2026-10','legacy-row','2026-10-12',1,'MARKET','2026-10-01')"
        )
        connection.commit()
        connection.close()

        with Store(path) as store:
            cell = schedule_protection.locks(store, "2026-10")[0]
            self.assertEqual((cell["reason"], cell["protected_by"]), ("", ""))

    def test_mutual_swap_preview_reports_attendance_and_rejects_stale_preview(self):
        ada = self.add_person("Takas Personeli Ada")["id"]
        bora = self.add_person("Takas Personeli Bora")["id"]
        month, day = "2026-10", "2026-10-12"
        entries = [
            {"row_id": f"swap-{employee_id}", "employee_id": employee_id, "section": "MARKET", "slot": slot,
             "days": {day: {"kind": "work", "start": start, "end": end}}}
            for slot, (employee_id, start, end) in enumerate(
                ((ada, "08:00", "16:00"), (bora, "15:00", "24:00")), start=1,
            )
        ]
        status, saved = self.json_request("/api/schedule", "PUT", {
            "month": month, "entries": entries, "revision": "",
        })
        self.assertEqual(status, 200, saved)
        status, published = self.json_request("/api/operations/publish", "POST", {
            "month": month, "revision": saved["revision"],
        })
        self.assertEqual(status, 200, published)

        route = "/api/operations/mutual-swaps"
        request = {"month": month, "action": "preview", "date": day, "first_id": ada, "second_id": bora}
        status, preview = self.json_request(route, "POST", request)
        self.assertEqual(status, 200, preview)
        self.assertTrue(preview["can_create"])
        impacts = {item["employee_id"]: item for item in preview["attendance_impacts"]}
        self.assertLess(impacts[ada]["worked_hours_before"], impacts[ada]["worked_hours_after"])
        self.assertGreater(impacts[bora]["worked_hours_before"], impacts[bora]["worked_hours_after"])
        self.assertEqual(self.json_request(f"/api/schedule?month={month}")[1]["revision"], saved["revision"])
        self.assertEqual(self.json_request(f"{route}?month={month}")[1]["items"], [])

        attendance = self.json_request(f"/api/attendance?month={month}")[1]
        status, _ = self.json_request("/api/attendance", "PUT", {
            "month": month, "revision": attendance["revision"],
            "overrides": {day: {str(ada): "X"}}, "deductions": {}, "overtime": {},
        })
        self.assertEqual(status, 200)
        stale = {**request, "action": "create", "fingerprint": preview["fingerprint"]}
        self.assertEqual(self.json_request(route, "POST", stale)[0], 400)

        status, fresh = self.json_request(route, "POST", request)
        self.assertEqual(status, 200, fresh)
        status, created = self.json_request(route, "POST", {
            **request, "action": "create", "fingerprint": fresh["fingerprint"],
        })
        self.assertEqual(status, 200, created)
        self.assertEqual(created["items"][0]["status"], "pending")

    def test_payroll_delivery_rejects_stale_and_duplicate_and_shows_field_changes(self):
        employee = str(self.add_person("Teslim Personeli")["id"])
        month = "2026-10"
        route = "/api/operations/payroll-delivery"
        status_path = f"{route}?month={month}"
        initial = self.json_request(status_path)[1]
        payload = {"month": month, "fingerprint": initial["fingerprint"], "reference": "Muhasebeye teslim", "confirmed": True}
        self.assertEqual(self.json_request(route, "POST", payload)[0], 400)
        status, saved = self.json_request("/api/attendance", "PUT", {
            "month": month, "revision": "", "overrides": {"2026-10-12": {employee: "X"}},
        })
        self.assertEqual(status, 200, saved)
        current = self.json_request(status_path)[1]
        self.assertEqual(self.json_request(route, "POST", payload)[0], 400)
        payload["fingerprint"] = current["fingerprint"]
        self.assertEqual(self.json_request(route, "POST", {**payload, "confirmed": False})[0], 400)
        delivery_status, delivery = self.json_request(route, "POST", payload)
        self.assertEqual(delivery_status, 201)
        self.assertEqual(self.json_request(route, "POST", payload)[0], 400)
        self.assertEqual(self.json_request("/api/attendance", "PUT", {
            "month": month, "revision": saved["revision"], "overrides": {"2026-10-12": {employee: "X"}},
            "deductions": {employee: 50},
        })[0], 200)
        result = self.json_request(status_path)[1]
        self.assertFalse(result["duplicate"])
        self.assertEqual(len(result["versions"]), 1)
        self.assertEqual(result["changes"], [{"name": "Teslim Personeli", "fields": [
            {"name": "Kesinti tutarı", "before": 0, "after": 50},
        ]}])
        correction = {
            "month": month, "fingerprint": result["fingerprint"],
            "delivery_id": delivery["id"], "reason": "Kilitli dönem bildirilen kesinti düzeltmesi",
        }
        request_route = "/api/operations/payroll-adjustments/request"
        self.assertEqual(self.json_request(request_route, "POST", correction)[0], 400)
        from storage import Store

        with Store(Path(self.temp.name) / "data.sqlite3") as store, store.connection:
            store._write_setting("locked_periods", [month])
        self.assertTrue(self.json_request(status_path)[1]["locked"])
        self.assertEqual(self.json_request(request_route, "POST", correction)[0], 201)
        pending = self.json_request(status_path)[1]
        self.assertEqual(len(pending["versions"]), 1)
        self.assertEqual(pending["adjustments"][0]["status"], "pending")
        self.assertTrue(pending["adjustments"][0]["locked"])
        self.assertEqual(pending["adjustments"][0]["reason"], correction["reason"])
        review_route = "/api/operations/payroll-adjustments/review"
        self.assertEqual(self.json_request(review_route, "POST", {
            "month": month, "id": pending["adjustments"][0]["id"], "approve": True,
        })[0], 200)
        approved = self.json_request(status_path)[1]
        self.assertEqual(approved["adjustments"][0]["status"], "approved")
        self.assertEqual(len(approved["versions"]), 1)
        with Store(Path(self.temp.name) / "data.sqlite3") as store:
            saved_delivery = store.connection.execute(
                "SELECT fingerprint,payload FROM operations_payroll_deliveries WHERE id=?", (delivery["id"],),
            ).fetchone()
            self.assertEqual(saved_delivery["fingerprint"], current["fingerprint"])
            self.assertEqual(json.loads(saved_delivery["payload"])["1"]["Kesinti tutarı"], 0)
        self.assertEqual(self.json_request(route, "POST", {**payload, "fingerprint": result["fingerprint"]})[0], 201)

    def test_overtime_budget_separates_forecast_and_recorded_hours(self):
        person = self.add_person("Bütçe Personeli")
        month = "2026-10"
        schedule = f"/api/schedule?month={month}"
        entries = [{"row_id": "budget-row", "employee_id": person["id"], "section": "MARKET", "slot": 1,
                    "days": {"2026-10-03": {"kind": "work", "start": "08:00", "end": "18:00"}}}]
        self.assertEqual(self.json_request("/api/schedule", "PUT",
                                          {"month": month, "entries": entries, "revision": ""})[0], 200)
        route = "/api/operations/overtime-budget"
        target = {"month": month, "section": "MARKET", "target": 0}
        self.assertEqual(self.json_request(route, "PUT", {**target, "target": True})[0], 400)
        self.assertEqual(self.json_request(route, "PUT", target)[0], 200)

        def market():
            status, report = self.json_request(f"{route}?month={month}")
            self.assertEqual(status, 200, report)
            return next(row for row in report["rows"] if row["section"] == "MARKET")

        before = market()
        self.assertEqual((before["target"], before["actual"]), (0, None))
        self.assertGreater(before["forecast"], 0)
        attendance = f"/api/attendance?month={month}"
        source = self.json_request(schedule)[1]["revision"]
        self.assertEqual(self.json_request("/api/attendance", "PUT", {
            "month": month, "revision": "", "overrides": {}, "overtime": {str(person["id"]): 7},
        })[0], 200)
        saved = self.json_request(attendance)[1]["revision"]
        self.assertEqual(market(), {**before, "actual": 7})
        self.assertEqual(self.json_request(route, "PUT", {**target, "target": None})[0], 200)
        self.assertIsNone(market()["target"])
        self.assertEqual(self.json_request(schedule)[1]["revision"], source)
        self.assertEqual(self.json_request(attendance)[1]["revision"], saved)

    def test_compare_plans_reports_weekend_staffing_and_overtime_without_saving(self):
        person = self.add_person("Senaryo Personeli")
        month = "2026-10"
        self.assertEqual(self.json_request("/api/compliance-settings", "PUT", {
            "min_staffing_windows": [{"section": "MARKET", "start": "08:00", "end": "10:00", "minimum": 1}],
        })[0], 200)
        path = f"/api/schedule?month={month}"
        baseline = self.json_request(path)[1]
        entries = [{"row_id": "scenario-row", "employee_id": person["id"], "section": "MARKET", "slot": 1,
                    "days": {"2026-10-03": {"kind": "work", "start": "08:00", "end": "18:00"}}}]
        route = "/api/schedule/compare-plans"
        status, result = self.json_request(route, "POST", {"month": month, "a": None, "b": entries})
        self.assertEqual(status, 200, result)
        self.assertGreater(result["a"]["staffing_findings"], result["b"]["staffing_findings"])
        self.assertEqual(result["a"]["overtime_hours"], 0)
        self.assertGreater(result["b"]["overtime_hours"], 0)
        self.assertEqual(result["b"]["weekend"], [{"employee_id": person["id"], "name": person["name"], "shifts": 1}])
        self.assertFalse(result["b"]["published_plan_available"])
        self.assertEqual((result["b"]["published_change_count"], result["b"]["published_affected_people"]), (0, 0))
        self.assertEqual(self.json_request(route, "POST", {"month": month, "a": {}, "b": entries})[0], 400)
        self.assertEqual(self.json_request(path)[1], baseline)

    def test_compare_plans_reports_published_people_and_change_notice_dates(self):
        person = self.add_person("Yayımlı Plan Çalışanı")
        month = "2026-10"
        initial = [{"row_id": "published-row", "employee_id": person["id"], "section": "MARKET", "slot": 1,
                    "days": {"2026-10-12": {"kind": "work", "start": "08:00", "end": "16:00"}}}]
        status, saved = self.json_request("/api/schedule", "PUT", {
            "month": month, "revision": "", "entries": initial,
        })
        self.assertEqual(status, 200, saved)
        status, published = self.json_request("/api/operations/publish", "POST", {
            "month": month, "revision": saved["revision"],
        })
        self.assertEqual(status, 200, published)
        changed = [{"row_id": "scenario-row", "employee_id": person["id"], "section": "MARKET", "slot": 1,
                    "days": {"2026-10-13": {"kind": "work", "start": "08:00", "end": "16:00"}}}]
        status, comparison = self.json_request("/api/schedule/compare-plans", "POST", {
            "month": month, "a": None, "b": changed,
        })
        self.assertEqual(status, 200, comparison)
        self.assertTrue(comparison["a"]["published_plan_available"])
        self.assertEqual(comparison["a"]["published_change_count"], 0)
        self.assertEqual(comparison["b"]["published_affected_people"], 1)
        self.assertEqual(comparison["b"]["published_change_count"], 2)
        self.assertEqual(comparison["b"]["earliest_change_date"], "2026-10-12")
        self.assertIsInstance(comparison["b"]["days_until_earliest_change"], int)
        self.assertEqual({item["type"] for item in comparison["b"]["published_changes"]}, {"removed", "added"})
        self.assertEqual(self.json_request(f"/api/schedule?month={month}")[1]["revision"], saved["revision"])

    def test_schedule_suggestion_check_compares_staffing_and_skill_coverage_without_saving(self):
        person = self.add_person("Öneri Denetim")
        self.assertEqual(self.json_request("/api/compliance-settings", "PUT", {
            "min_staffing_windows": [
                {"section": "MARKET", "start": "08:00", "end": "10:00", "minimum": 1},
            ],
        })[0], 200)
        self.assertEqual(self.json_request("/api/planning-settings", "PUT", {
            "daily_limit": 8,
            "skill_requirements": [{"section": "MARKET", "skill": "Forklift"}],
        })[0], 200)
        entries = [{
            "row_id": "suggested-worker",
            "employee_id": person["id"],
            "section": "MARKET",
            "slot": 1,
            "days": {"2026-10-01": {"kind": "work", "start": "08:00", "end": "10:00"}},
        }]

        status, result = self.json_request("/api/schedule/suggestion-check", "POST", {
            "month": "2026-10", "entries": entries,
        })

        self.assertEqual(status, 200, result)
        self.assertEqual(result["baseline"]["skill_coverage"], 0)
        self.assertEqual(result["proposed"]["skill_coverage"], 1)
        self.assertGreater(result["baseline"]["staffing"], result["proposed"]["staffing"])
        self.assertEqual(self.json_request("/api/schedule?month=2026-10")[1]["entries"], [])

    def test_payroll_extras_comp_leave_balancing_exit_close_and_future_holidays(self):
        from datetime import date as day_type

        import scheduling
        from storage import Store

        first, second = self.add_person("Ece Ekler"), self.add_person("Ferhat Çıkan")
        shift = {"kind": "work", "start": "08:00", "end": "20:00"}
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {
            "employee_id": second["id"], "profile": {"exit_date": "2026-10-15"}})[0], 200)
        self.assertEqual(self.json_request("/api/schedule", "PUT", {"month": "2026-10", "revision": "", "entries": [
            {"row_id": "a", "employee_id": first["id"], "section": "MARKET", "slot": 1,
             "days": {f"2026-10-{day}": shift for day in range(26, 32)}},
            {"row_id": "b", "employee_id": second["id"], "section": "MARKET", "slot": 2,
             "days": {"2026-10-12": shift, "2026-10-20": shift}},
        ]})[0], 200)
        status, data = self.json_request("/api/payroll-extras?month=2026-10")
        self.assertEqual(status, 200)
        row = next(item for item in data["rows"] if item["employee_id"] == first["id"])
        # 28 Ekim 13:00 sonrası 7 saat + 29 Ekim 12 saat resmî tatil çalışması.
        self.assertEqual((row["meal_days"], row["transport_days"], row["ubgt_hours"], row["paid_ubgt_hours"]), (6, 6, 19.0, 19.0))

        bad = [
            {str(first["id"]): {"advances": [{"date": "2026-10-10", "amount": 0}]}},
            {str(first["id"]): {"advances": [{"date": "2026-11-01", "amount": 10}]}},
            {str(first["id"]): {"holiday_choice": "maybe"}},
            {"999999": {"meal": False}},
        ]
        for people in bad:
            self.assertEqual(self.json_request("/api/payroll-extras", "PUT", {"month": "2026-10", "people": people})[0], 400)
        status, data = self.json_request("/api/payroll-extras", "PUT", {"month": "2026-10", "people": {
            str(first["id"]): {"holiday_choice": "leave", "meal": False, "deduction_note": "  Telefon\nborcu ",
                               "advances": [{"date": "2026-10-10", "amount": "1500,50", "note": "Ekim"}]},
            str(second["id"]): {"meal": True, "transport": True},
        }})
        self.assertEqual(status, 200)
        self.assertEqual(set(data["people"]), {str(first["id"])})
        row = next(item for item in data["rows"] if item["employee_id"] == first["id"])
        self.assertEqual((row["meal_days"], row["transport_days"], row["advances_total"], row["deduction_note"]),
                         (0, 6, 1500.5, "Telefon borcu"))
        self.assertEqual((row["paid_ubgt_hours"], row["comp_earned_hours"]), (0, 19.0))

        status, _, content = self.request("/api/reports/payroll-program?month=2026-10&format=logo")
        self.assertEqual(status, 200)
        sheet = load_workbook(BytesIO(content)).active
        headers = [cell.value for cell in sheet[1]]
        self.assertEqual(headers[-5:], ["Yemek Günü", "Yol Günü", "Avans Tutarı", "Telafi İzni Saat", "Kesinti Açıklaması"])
        line = next(item for item in sheet.iter_rows(min_row=2, values_only=True) if item[1] == "Ece Ekler")
        self.assertEqual(tuple(line[-5:]), (0, 6, 1500.5, 19, "Telefon borcu"))

        _, attendance = self.json_request("/api/attendance?month=2026-11")
        self.assertEqual(self.json_request("/api/attendance", "PUT", {
            "month": "2026-11", "revision": attendance["revision"],
            "overrides": {"2026-11-02": {str(first["id"]): "Tİ"}}})[0], 200)
        _, data = self.json_request("/api/payroll-extras?month=2026-11")
        balance = next(item for item in data["comp_balance"] if item["employee_id"] == first["id"])
        self.assertEqual((balance["earned_hours"], balance["used_hours"], balance["balance_hours"]), (19.0, 8.0, 11.0))

        self.assertEqual(self.json_request("/api/payroll/balancing?month=2026-10&weeks=3")[0], 400)
        status, report = self.json_request("/api/payroll/balancing?month=2026-10&weeks=2")
        self.assertEqual((status, report["start"], report["end"]), (200, "2026-10-19", "2026-11-01"))
        item = next(row for row in report["rows"] if row["employee_id"] == first["id"])
        self.assertEqual([week["hours"] for week in item["weeks"]], [0.0, 72.0])
        self.assertEqual((item["total_hours"], item["average_hours"], item["over_weeks"], item["excess_hours"], item["status"]),
                         (72.0, 36.0, 1, 0.0, "ok"))

        items = self.json_request("/api/reports/check?month=2026-10")[1]["items"]
        late = [item for item in items if item["type"] == "after_exit"]
        self.assertEqual([(item["employee_id"], item["date"]) for item in late], [(second["id"], "2026-10-20")])
        with Store(Path(self.temp.name) / "data.sqlite3") as store:
            self.assertEqual(store.close_exited_people(day_type(2026, 10, 10)), [])
            self.assertEqual(store.close_exited_people(day_type(2026, 11, 1)), [second["id"]])
            self.assertFalse(next(person for person in store.people() if person["id"] == second["id"])["active"])

        _, holidays = self.json_request("/api/holidays?year=2027")
        found = {(item["date"], item["kind"]) for item in holidays["builtin"]}
        self.assertTrue({("2027-03-08", "eve"), ("2027-03-09", "religious"), ("2027-05-19", "religious")} <= found)
        self.assertTrue(holidays["religious_known"])
        self.assertFalse(self.json_request("/api/holidays?year=2031")[1]["religious_known"])
        self.assertEqual(scheduling.holiday_window(day_type(2029, 4, 23))[0].hour, 0)
        self.assertEqual(scheduling.holiday_window(day_type(2028, 5, 4))[0].hour, 13)

    def test_fixed_pattern_exception_applies_to_one_week_then_returns_to_base_pattern(self):
        person = self.add_person("Sabit Düzen")
        status, _ = self.json_request("/api/employee-profile", "PUT", {
            "employee_id": person["id"],
            "profile": {
                "staff_type": "nonshift",
                "fixed_pattern": ["X", "X", "X", "X", "X", "X", "H"],
                "fixed_pattern_exceptions": [{
                    "start_date": "2026-11-02",
                    "pattern": ["X", "X", "H", "X", "X", "X", "H"],
                }],
            },
        })
        self.assertEqual(status, 200)
        status, attendance = self.json_request("/api/attendance?month=2026-11")
        self.assertEqual(status, 200)
        row = next(item for item in attendance["attendance"]["rows"] if item["employee_id"] == person["id"])
        self.assertEqual(row["codes"]["2026-11-04"], "H")
        self.assertEqual(row["codes"]["2026-11-11"], "X")
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {
            "employee_id": person["id"], "profile": {
                "fixed_pattern_exceptions": [{"start_date": "2026-11-03", "pattern": ["X"] * 7}],
            },
        })[0], 400)

    def test_future_fixed_pattern_change_previews_periods_and_preserves_prior_codes(self):
        person = self.add_person("Tarihli Sabit Düzen")
        status, saved_profile = self.json_request("/api/employee-profile", "PUT", {
            "employee_id": person["id"], "profile": {
                "staff_type": "nonshift", "fixed_pattern": ["X"] * 7,
                "fixed_start": "08:00", "fixed_end": "16:00",
            },
        })
        self.assertEqual(status, 200, saved_profile)
        effective = date.today() + timedelta(days=10)
        before = effective - timedelta(days=1)
        periods = {effective.strftime("%Y-%m"), before.strftime("%Y-%m")}
        for period in periods:
            status, saved = self.json_request("/api/schedule", "PUT", {
                "month": period, "revision": "", "entries": [],
            })
            self.assertEqual(status, 200, saved)
        pattern = ["X"] * 7
        pattern[effective.weekday()] = "H"
        request = {
            "employee_id": person["id"], "action": "fixed_pattern",
            "effective_date": effective.isoformat(), "pattern": pattern,
        }
        status, preview = self.json_request("/api/operations/lifecycle/preview", "POST", request)
        self.assertEqual(status, 200, preview)
        effect = next(item for item in preview["future_work"] if item["date"] == effective.isoformat())
        self.assertEqual((effect["old_code"], effect["new_code"]), ("X", "H"))
        self.assertEqual(self.json_request("/api/employee-profile?employee_id=" + str(person["id"]))[1]
                         ["profile"]["fixed_pattern_changes"], [])
        status, before_attendance = self.json_request(f"/api/attendance?month={before:%Y-%m}")
        self.assertEqual(status, 200)
        before_row = next(row for row in before_attendance["attendance"]["rows"]
                          if row["employee_id"] == person["id"])
        self.assertEqual(before_row["codes"][before.isoformat()], "X")

        status, applied = self.json_request("/api/operations/lifecycle/apply", "POST", {
            **request, "revision": preview["revision"], "confirm": True,
        })
        self.assertEqual(status, 200, applied)
        self.assertTrue(applied["pending"])
        profile = self.json_request("/api/employee-profile?employee_id=" + str(person["id"]))[1]["profile"]
        self.assertEqual(profile["fixed_pattern_changes"], [{
            "effective_date": effective.isoformat(), "pattern": pattern,
        }])
        status, future_attendance = self.json_request(f"/api/attendance?month={effective:%Y-%m}")
        self.assertEqual(status, 200)
        future_row = next(row for row in future_attendance["attendance"]["rows"]
                          if row["employee_id"] == person["id"])
        self.assertEqual(future_row["codes"][effective.isoformat()], "H")
        self.assertIn("fixed_pattern", [
            item["action"] for item in self.json_request(f"/api/operations/lifecycle?month={effective:%Y-%m}")[1]
            ["pending_transitions"]
        ])

    def test_document_renewal_stages_keep_expired_and_new_versions_distinct(self):
        person = self.add_person("Belge Yenileme")
        original = {
            "id": "certificate-old", "name": "Hijyen Belgesi", "expires": "2026-10-15",
            "note": "Eski nüsha", "renewal_stage": "", "supersedes": "",
        }
        status, saved = self.json_request("/api/employee-profile", "PUT", {
            "employee_id": person["id"], "profile": {"documents": [original]}})
        self.assertEqual(status, 200)
        document_id = saved["profile"]["documents"][0]["id"]

        requested = {**original, "id": document_id, "renewal_stage": "requested"}
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {
            "employee_id": person["id"], "profile": {"documents": [requested]}})[0], 200)
        skipped = {**requested, "renewal_stage": "checked"}
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {
            "employee_id": person["id"], "profile": {"documents": [skipped]}})[0], 400)
        received = {**requested, "renewal_stage": "received"}
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {
            "employee_id": person["id"], "profile": {"documents": [received]}})[0], 200)
        checked = {**received, "renewal_stage": "checked"}
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {
            "employee_id": person["id"], "profile": {"documents": [checked]}})[0], 200)

        renewed_without_copy = {**checked, "renewal_stage": "renewed"}
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {
            "employee_id": person["id"], "profile": {"documents": [renewed_without_copy]}})[0], 400)
        replacement = {
            "id": "certificate-new", "name": "Hijyen Belgesi", "expires": "2027-10-15",
            "note": "", "renewal_stage": "", "supersedes": document_id,
        }
        status, saved = self.json_request("/api/employee-profile", "PUT", {
            "employee_id": person["id"],
            "profile": {"documents": [renewed_without_copy, replacement]},
        })
        self.assertEqual(status, 200)
        documents = {item["id"]: item for item in saved["profile"]["documents"]}
        self.assertEqual(documents[document_id]["expires"], "2026-10-15")
        self.assertEqual(documents[document_id]["renewal_stage"], "renewed")
        self.assertEqual(documents["certificate-new"]["supersedes"], document_id)
        self.assertEqual(documents["certificate-new"]["expires"], "2027-10-15")

    def test_documents_expiry_person_history_handover_notes_and_duty_board(self):
        person, other = self.add_person("Gül Belge"), self.add_person("Hakan Gece")
        for documents in ([{"name": "", "expires": "2026-10-01"}], [{"name": "Rapor", "expires": "01.10.2026"}],
                          [{"name": "x"}] * 21, "liste"):
            self.assertEqual(self.json_request("/api/employee-profile", "PUT", {
                "employee_id": person["id"], "profile": {"documents": documents}})[0], 400)
        status, saved = self.json_request("/api/employee-profile", "PUT", {"employee_id": person["id"], "profile": {
            "documents": [{"name": " Hijyen  eğitimi ", "expires": "2026-11-20", "note": ""},
                          {"name": "Sağlık raporu", "expires": "2026-09-30"},
                          {"name": "Forklift", "expires": "2027-06-01"},
                          {"name": "", "expires": "", "note": ""}]}})
        self.assertEqual(status, 200)
        self.assertEqual([item["name"] for item in saved["profile"]["documents"]],
                         ["Sağlık raporu", "Hijyen eğitimi", "Forklift"])
        self.json_request("/api/employee-profile", "PUT", {"employee_id": other["id"], "profile": {"title": "Kasiyer"}})
        items = self.json_request("/api/reports/check?month=2026-10")[1]["items"]
        found = sorted((item["employee_id"], item["date"], item["message"]) for item in items if item["type"] == "document_expiry")
        self.assertEqual(found, [
            (person["id"], None, "Hijyen eğitimi süresi 2026-11-20 tarihinde doluyor"),
            (person["id"], None, "Sağlık raporu süresi 2026-09-30 tarihinde dolmuş"),
        ])
        _, card = self.json_request(f"/api/employee-profile?employee_id={person['id']}")
        self.assertTrue(card["history"])
        self.assertTrue(all("Gül Belge" in item["summary"] for item in card["history"]))
        self.assertFalse(any("Hakan" in item["summary"] for item in card["history"]))

        self.assertEqual(self.json_request("/api/handover", "POST", {"date": "2026-10-05", "text": "  "})[0], 400)
        self.assertEqual(self.json_request("/api/handover", "POST", {"date": "05.10.2026", "text": "x"})[0], 400)
        status, created = self.json_request("/api/handover", "POST", {
            "date": "2026-10-05", "section": "MARKET", "shift": "Akşam", "text": "Kasa 2 arızalı"})
        self.assertEqual((status, created["note"]["done"]), (200, False))
        self.json_request("/api/handover", "POST", {"date": "2026-10-06", "text": "Ertesi gün"})
        self.assertEqual(self.json_request("/api/handover", "POST", {"id": created["note"]["id"], "done": "evet"})[0], 400)
        self.assertEqual(self.json_request("/api/handover", "POST", {"id": "yok", "done": True})[0], 400)
        self.assertEqual(self.json_request("/api/handover", "POST", {"id": created["note"]["id"], "done": True})[0], 200)
        _, notes = self.json_request("/api/handover?from=2026-10-05&to=2026-10-05")
        self.assertEqual([(note["text"], note["done"]) for note in notes["notes"]], [("Kasa 2 arızalı", True)])
        self.assertEqual(self.json_request("/api/handover?from=2026-10-06&to=2026-10-05")[0], 400)
        _, open_task = self.json_request("/api/handover", "POST", {
            "date": "2026-10-05", "text": "Sonraki ekibe taşınacak iş"})
        task_id = open_task["note"]["id"]
        self.assertEqual(open_task["note"]["status"], "open")
        self.assertEqual(self.json_request("/api/handover", "POST", {
            "id": task_id, "status": "completed"})[0], 400)
        self.assertEqual(self.json_request("/api/handover", "POST", {
            "id": task_id, "status": "claimed"})[0], 200)
        _, next_day_notes = self.json_request("/api/handover?from=2026-10-06&to=2026-10-06")
        carried = next(note for note in next_day_notes["notes"] if note["id"] == task_id)
        self.assertEqual(carried["status"], "claimed")
        self.assertTrue(carried["claimed_by"])
        self.assertEqual(self.json_request("/api/handover", "POST", {
            "id": task_id, "status": "completed"})[0], 200)
        _, historical_notes = self.json_request("/api/handover?from=2026-10-05&to=2026-10-05")
        self.assertEqual(
            {note["text"]: note["status"] for note in historical_notes["notes"]},
            {"Kasa 2 arızalı": "completed", "Sonraki ekibe taşınacak iş": "completed"},
        )
        self.assertFalse(any(note["id"] == task_id for note in self.json_request(
            "/api/duty-board?date=2026-10-06")[1]["notes"]))

        self.assertEqual(self.json_request("/api/schedule", "PUT", {"month": "2026-10", "revision": "", "entries": [
            {"row_id": "a", "employee_id": person["id"], "section": "MARKET", "slot": 1,
             "days": {"2026-10-05": {"kind": "work", "start": "08:00", "end": "16:00"},
                      "2026-10-04": {"kind": "work", "start": "09:00", "end": "17:00"}}},
            {"row_id": "b", "employee_id": other["id"], "section": "TEMİZLİK", "slot": 1,
             "days": {"2026-10-04": {"kind": "work", "start": "22:00", "end": "06:00"},
                      "2026-10-05": {"kind": "annual_leave"}}},
        ]})[0], 200)
        status, board = self.json_request("/api/duty-board?date=2026-10-05")
        self.assertEqual(status, 200)
        self.assertEqual([(section["name"], [(item["name"], item["from_previous"]) for item in section["people"]])
                          for section in board["sections"]],
                         [("MARKET", [("Gül Belge", False)]), ("TEMİZLİK", [("Hakan Gece", True)])])
        self.assertEqual([(item["name"], item["status"]) for item in board["off"]], [("Hakan Gece", "Yıllık izin")])
        self.assertEqual(
            {note["text"]: note["status"] for note in board["notes"]},
            {"Kasa 2 arızalı": "completed", "Sonraki ekibe taşınacak iş": "completed"},
        )
        self.assertEqual(self.json_request("/api/handover", "POST", {"id": created["note"]["id"], "delete": True})[0], 200)
        self.assertEqual(self.json_request("/api/handover", "POST", {"id": task_id, "delete": True})[0], 200)
        self.assertEqual(self.json_request("/api/duty-board?date=2026-10-05")[1]["notes"], [])
        self.assertEqual(self.json_request("/api/duty-board?date=today")[0], 400)

    def test_personnel_asset_issue_return_history_and_person_preservation(self):
        from storage import Store

        person = self.add_person("Deneme Personel Zimmet")
        status, empty = self.json_request(f"/api/personnel-assets?employee_id={person['id']}")
        self.assertEqual((status, empty["assets"]), (200, []))
        invalid = self.json_request("/api/personnel-assets", "POST", {
            "action": "issue", "employee_id": person["id"], "category": "unknown", "item": "Kart", "note": "",
        })
        self.assertEqual(invalid[0], 400)
        status, issued = self.json_request("/api/personnel-assets", "POST", {
            "action": "issue", "employee_id": person["id"], "category": "card", "item": "Giriş kartı 12", "note": "Geçici kart",
        })
        self.assertEqual(status, 200)
        self.assertEqual((issued["asset"]["item"], issued["asset"]["note"], issued["asset"]["returned_at"]),
                         ("Giriş kartı 12", "Geçici kart", ""))
        with Store(Path(self.temp.name) / "data.sqlite3") as store:
            store.set_person_active(person["id"], False)
        self.assertEqual(self.json_request("/api/personnel-assets", "POST", {
            "action": "issue", "employee_id": person["id"], "category": "key", "item": "Anahtar", "note": "",
        })[0], 400)
        status, returned = self.json_request("/api/personnel-assets", "POST", "INVALID")
        self.assertEqual(status, 400)
        status, returned = self.json_request("/api/personnel-assets", "POST", {
            "action": "return", "asset_id": issued["asset"]["id"],
        })
        self.assertEqual(status, 200)
        self.assertTrue(returned["asset"]["returned_at"])
        self.assertEqual(returned["asset"]["returned_by"], "Yerel kullanıcı")
        self.assertEqual(self.json_request("/api/personnel-assets", "POST", {
            "action": "return", "asset_id": issued["asset"]["id"],
        })[0], 400)
        _, history = self.json_request(f"/api/personnel-assets?employee_id={person['id']}")
        self.assertEqual(len(history["assets"]), 1)
        with Store(Path(self.temp.name) / "data.sqlite3") as store:
            with self.assertRaisesRegex(ValueError, "zimmet kayıtları"):
                store.delete_person(person["id"], force=True)
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM employee_assets WHERE employee_id=?", (person["id"],),
            ).fetchone()[0], 1)
            actions = [item["action"] for item in store.change_history(action="employee_asset.")]
            self.assertEqual(actions, ["employee_asset.return", "employee_asset.issue"])

    def test_handover_read_acknowledgment_persists_without_claiming_and_carries_forward(self):
        _, created = self.json_request("/api/handover", "POST", {
            "date": "2026-10-05", "section": "MARKET", "shift": "Sabah",
            "text": "Okunma durumunu sınayan görev",
        })
        note_id = created["note"]["id"]
        self.assertEqual((created["note"]["status"], created["note"]["done"]), ("open", False))
        self.assertEqual(self.json_request("/api/handover", "POST", {
            "id": note_id, "done": False,
        })[0], 200)
        _, legacy_open = self.json_request("/api/handover?from=2026-10-05&to=2026-10-05")
        self.assertEqual(
            (legacy_open["notes"][0]["status"], legacy_open["notes"][0]["done"]),
            ("open", False),
        )

        self.assertEqual(self.json_request("/api/handover", "POST", {
            "id": note_id, "status": "read",
        })[0], 200)
        _, read_note = self.json_request("/api/handover?from=2026-10-05&to=2026-10-05")
        read = read_note["notes"][0]
        self.assertEqual((read["status"], read["done"], read["claimed_by"]), ("read", False, ""))
        self.assertTrue(read["read_by"])
        self.assertTrue(read["read_at"])
        self.assertEqual(self.json_request("/api/handover", "POST", {
            "id": note_id, "status": "completed",
        })[0], 400)

        _, next_day_notes = self.json_request("/api/handover?from=2026-10-06&to=2026-10-06")
        carried = next(note for note in next_day_notes["notes"] if note["id"] == note_id)
        self.assertEqual(carried["status"], "read")
        self.assertEqual(carried["claimed_by"], "")
        self.assertEqual(self.json_request("/api/handover", "POST", {
            "id": note_id, "status": "claimed",
        })[0], 200)
        _, claimed_notes = self.json_request("/api/handover?from=2026-10-06&to=2026-10-06")
        claimed = next(note for note in claimed_notes["notes"] if note["id"] == note_id)
        self.assertEqual(claimed["status"], "claimed")
        self.assertTrue(claimed["claimed_by"])
        self.assertEqual(claimed["read_by"], read["read_by"])

        self.assertEqual(self.json_request("/api/handover", "POST", {
            "id": note_id, "status": "completed",
        })[0], 200)
        _, completed_notes = self.json_request("/api/handover?from=2026-10-05&to=2026-10-05")
        completed = next(note for note in completed_notes["notes"] if note["id"] == note_id)
        self.assertEqual((completed["status"], completed["done"]), ("completed", True))
        self.assertFalse(any(note["id"] == note_id for note in self.json_request(
            "/api/handover?from=2026-10-06&to=2026-10-06")[1]["notes"]))

    def test_workspace_settings_transfer_problem_report_sample_new_month_branches_and_phone(self):
        import io as _io
        import zipfile as _zipfile
        from urllib.request import urlopen as _urlopen
        import workspace_tools
        # Örnek veri yalnız boş veri dosyasına yüklenir.
        status, sample = self.json_request("/api/workspace/sample-data", "POST", {"month": "2026-10"})
        self.assertEqual(status, 200, sample)
        self.assertEqual(sample["people"], 6)
        status, error = self.json_request("/api/workspace/sample-data", "POST", {"month": "2026-10"})
        self.assertEqual(status, 400)
        status, schedule = self.json_request("/api/schedule?month=2026-10")
        self.assertEqual(len(schedule["entries"]), 6)
        night = [row for row in schedule["entries"] if any(
            cell.get("start") == "00:00" and cell.get("end") == "08:00" for cell in row["days"].values())]
        self.assertTrue(night, "24:00 girişi gece yarısı (00:00) olarak saklanmalı")

        # Ayar dışa aktarımı: biçim + kişisel veri yok.
        status, headers, body = self.request("/api/reports/settings-export")
        self.assertEqual(status, 200)
        exported = json.loads(body.decode("utf-8"))
        self.assertEqual(exported["format"], "vardiya-puantaj-ayarlar")
        self.assertIn("quick_entries", exported["settings"])
        self.assertNotIn("Örnek Ayşe", body.decode("utf-8"))
        # İçe aktarma: geçerli grup uygulanır, bozuk grup atlanır, yedek alınır.
        exported["settings"]["overtime_settings"] = {**exported["settings"]["overtime_settings"], "daily_limit_hours": 9}
        exported["settings"]["quick_entries"] = "bozuk"
        status, result = self.json_request("/api/workspace/settings-import", "POST", {
            "data": exported, "keys": ["overtime_settings", "quick_entries"]})
        self.assertEqual(status, 200, result)
        self.assertEqual(result["applied"], ["Fazla mesai kuralları"])
        self.assertEqual([item["key"] for item in result["skipped"]], ["quick_entries"])
        self.assertTrue(result["backup"])
        status, overtime = self.json_request("/api/overtime-settings")
        self.assertEqual(overtime["daily_limit_hours"], 9)
        status, error = self.json_request("/api/workspace/settings-import", "POST", {"data": {"format": "x"}})
        self.assertEqual(status, 400)

        # Sorun raporu: zip içinde isim veya veritabanı yok.
        status, headers, body = self.request("/api/reports/problem-report")
        self.assertEqual(status, 200)
        with _zipfile.ZipFile(_io.BytesIO(body)) as archive:
            names = set(archive.namelist())
            self.assertEqual(names, {"README.txt", "sistem.json", "saglik_kontrolu.json", "sayilar.json", "ayarlar.json"})
            joined = "".join(archive.read(name).decode("utf-8") for name in names)
        self.assertNotIn("Örnek", joined)

        # Yeni ay sihirbazı: dizilim + son hafta deseni + önceki ayı kilitleme.
        status, wizard = self.json_request("/api/workspace/new-month", "POST", {
            "month": "2026-11", "copy_roster": True, "fill_pattern": True, "lock_previous": True})
        self.assertEqual(status, 200, wizard)
        self.assertEqual(wizard["rows"], 6)
        self.assertTrue(wizard["locked"])
        status, november = self.json_request("/api/schedule?month=2026-11")
        october_last = {}
        for row in schedule["entries"]:
            october_last[(row["employee_id"], row["section"])] = row["days"]
        filled = [row for row in november["entries"] if row["days"]]
        self.assertFalse(filled, "Ekim son haftası boş olduğu için desen gün eklememeli")
        status, locked = self.json_request("/api/workspace/new-month", "POST", {
            "month": "2026-11", "copy_roster": True, "fill_pattern": False, "lock_previous": False})
        self.assertEqual(status, 400, "Dolu ayın üzerine yazılmamalı")
        status, error = self.json_request("/api/schedule", "PUT", {
            "month": "2026-10", "revision": schedule["revision"], "entries": []})
        self.assertEqual(status, 400, "Önceki ay kilitlenmiş olmalı")

        # Şubeler: ekle, seç (yeniden başlatma gerekir), ana şube kaldırılamaz.
        status, branches = self.json_request("/api/workspace/branches")
        self.assertEqual(branches["running"], "ana")
        status, branches = self.json_request("/api/workspace/branches", "POST", {"action": "add", "name": "Merkez İstasyon"})
        self.assertEqual(status, 200, branches)
        new_id = [item["id"] for item in branches["branches"] if item["name"] == "Merkez İstasyon"][0]
        self.assertEqual(new_id, "merkez-istasyon")
        status, branches = self.json_request("/api/workspace/branches", "POST", {"action": "switch", "id": new_id, "restart": True})
        self.assertTrue(branches["restart_required"])
        self.assertFalse(branches.get("restarting"), "Kaynak koddan çalışırken uygulama kapatılmamalı")
        status, error = self.json_request("/api/workspace/branches", "POST", {"action": "remove", "id": "ana"})
        self.assertEqual(status, 400)
        root = Path(self.temp.name)
        self.assertEqual(workspace_tools.resolve_data_file(root / "vardiya_puantaj.sqlite3"),
                         root / "subeler" / "merkez-istasyon" / "vardiya_puantaj.sqlite3")

        # Telefon görünümü: varsayılan kapalı, belirteçsiz erişim reddedilir, salt okunur hafta tablosu.
        status, phone = self.json_request("/api/workspace/phone")
        self.assertFalse(phone["running"])
        workspace_tools.PhoneViewServer.host = "127.0.0.1"
        try:
            status, phone = self.json_request("/api/workspace/phone", "POST", {"action": "start"})
        finally:
            workspace_tools.PhoneViewServer.host = "0.0.0.0"
        self.assertTrue(phone["running"], phone)
        port = phone["port"]
        token = phone["urls"][0].split("t=")[1]
        try:
            with self.assertRaises(HTTPError):
                _urlopen(f"http://127.0.0.1:{port}/m?t=yanlis")
            with _urlopen(f"http://127.0.0.1:{port}/m?t={token}&d=2026-10-05") as response:
                page = response.read().decode("utf-8")
            self.assertIn("05.10 – 11.10.2026", page)
            self.assertIn("Örnek Ayşe", page)
            self.assertIn("08:00–16:00", page)
            self.assertNotIn("{token}", page)
        finally:
            status, phone = self.json_request("/api/workspace/phone", "POST", {"action": "stop"})
        self.assertFalse(phone["running"])

    def test_mobile_diagnostics_route_and_private_technical_error_trace(self):
        self.server.mobile_diagnostics = {
            "service": {"status": "ok", "message": "Sentetik HTTPS hizmeti çalışıyor."},
            "network": {"status": "warning", "message": "Ağ ayrıca doğrulanmalı."},
            "address": {"status": "ok", "message": "HTTPS adresi geçerli."},
            "certificate": {"status": "ok", "message": "Sertifika geçerli."},
            "addresses": [],
        }
        status, diagnostics = self.json_request("/api/workspace/mobile-diagnostics")
        self.assertEqual(status, 200)
        self.assertEqual(diagnostics, self.server.mobile_diagnostics)

        with patch.object(app_server.ScheduleRequestHandler, "_dashboard",
                          side_effect=RuntimeError("Kişi adı ve gizli hata metni")):
            status, failed = self.json_request("/api/dashboard?date=2026-10-01")
        self.assertEqual(status, 500)
        self.assertNotIn("Kişi adı", repr(failed))
        self.assertRegex(failed["error_code"], r"^VP-[A-F0-9]{10}$")
        self.assertRegex(failed["operation_id"], r"^[a-f0-9]{16}$")

        status, records = self.json_request("/api/diagnostics/errors")
        self.assertEqual(status, 200)
        self.assertEqual(len(records["events"]), 1)
        event = records["events"][0]
        self.assertEqual((event["app_version"], event["stage"], event["error_type"]),
                         (app_server.version.APP_VERSION, "GET /api/dashboard", "RuntimeError"))
        self.assertEqual(event["operation_id"], failed["operation_id"])
        self.assertEqual(event["error_code"], failed["error_code"])
        self.assertEqual(event["steps"], [
            "request_received", "route_resolved", "authorization_passed", "operation_started",
            "operation_failed", "error_code_assigned", "diagnostic_record_prepared",
        ])
        self.assertNotIn("Kişi adı", repr(event))
        self.assertNotIn("gizli hata metni", repr(event))

    def test_technical_error_steps_migrate_legacy_database_schema(self):
        from storage import SCHEMA_VERSION, Store

        legacy_path = Path(self.temp.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                "CREATE TABLE form_templates (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                "category TEXT NOT NULL, extension TEXT NOT NULL, content BLOB NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE technical_error_events (id INTEGER PRIMARY KEY, operation_id TEXT NOT NULL, "
                "occurred_at TEXT NOT NULL, app_version TEXT NOT NULL, stage TEXT NOT NULL, "
                "error_code TEXT NOT NULL, error_type TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE portal_plan_views (token_hash TEXT NOT NULL, month TEXT NOT NULL, "
                "revision TEXT NOT NULL, viewed_at INTEGER NOT NULL, PRIMARY KEY(token_hash, month))"
            )
            connection.execute(
                "INSERT INTO portal_plan_views VALUES ('legacy-token', '2026-03', ?, 1)",
                ("a" * 32,),
            )
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        finally:
            connection.close()
        with Store(legacy_path) as store:
            columns = {row[1] for row in store.connection.execute(
                "PRAGMA table_info(technical_error_events)"
            )}
            self.assertIn("steps_json", columns)
            view_columns = {row[1] for row in store.connection.execute(
                "PRAGMA table_info(portal_plan_views)"
            )}
            self.assertIn("confirmed", view_columns)
            self.assertEqual(store.connection.execute(
                "SELECT confirmed FROM portal_plan_views WHERE token_hash='legacy-token'"
            ).fetchone()[0], 0)
            self.assertEqual(store.connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            store.record_technical_error(
                "0123456789abcdef", "test-version", "GET /api/test", "VP-ABCDEF0123",
                "RuntimeError", ["request_received", "operation_failed"],
            )
            event = store.technical_error_history()[0]
            self.assertEqual(event["steps"], ["request_received", "operation_failed"])
    def test_support_package_preview_matches_zip_and_excludes_free_text(self):
        import zipfile
        from storage import Store
        path = "/api/reports/problem-report"
        person = self.add_person("Destek Gizlilik Denemesi")
        with Store(self.server.data_path) as store, store.connection:
            store._write_setting("test.private", {"smtp_subject": "Ozel-Baslik", "password": "test-parolasi"})
            store.record_change("ornek@example.test", "Ozel-Baslik", {"person": person["name"]})
        status, preview = self.json_request(path + "?preview=1")
        self.assertEqual(status, 200, preview)
        reviewed_path = path + "?review=" + preview["review"]
        status, _, binary = self.request(reviewed_path)
        self.assertEqual(status, 200)
        with zipfile.ZipFile(BytesIO(binary)) as archive:
            contents = {n: archive.read(n).decode("utf-8") for n in archive.namelist()}
        self.assertEqual(contents, preview["files"])
        for private in (person["name"], "Ozel-Baslik", "test-parolasi", "ornek@example.test", str(self.server.data_path)):
            self.assertNotIn(private, str(contents))
        self.add_person("Onizlemeden Sonra")
        self.assertEqual(self.request(reviewed_path)[0], 400)

    def test_print_settings_helpers_filename_template_and_preview(self):
        person = self.add_person("Çıktı Kişi")
        days = {f"2026-10-{day:02d}": {"kind": "work", "start": "09:00", "end": "17:00"} for day in range(1, 32)}
        days["2026-10-05"] = {"kind": "sick_leave"}
        status, _ = self.json_request("/api/schedule", "PUT", {"month": "2026-10", "revision": "", "entries": [{
            "row_id": "out-1", "employee_id": person["id"], "section": "MARKET", "slot": 1, "days": days,
        }]})
        self.assertEqual(status, 200)
        # Şablon katmanına kaydedilen yazdırma ayarı, kendi biçimi olan ayda da hatırlanır.
        page_setup = {"orientation": "landscape", "fit_width": 1, "fit_height": 0, "header_text": "İSTASYON & İSTASYON",
                      "footer_text": "Hazırlayan: ____", "page_numbers": True, "code_dropdown": True, "highlight_codes": True}
        settings = {"sheets": {
            "attendance:template:0": {"cells": {}, "hide_notes": False, "zoom": 100, "page_setup": page_setup},
            "attendance:2026-10:0": {"cells": {"2:2": {"color": "#ddebf7"}}, "hide_notes": False, "zoom": 90},
        }, "quick_colors": {}}
        status, result = self.json_request("/api/workbook-settings", "PUT", settings)
        self.assertEqual(status, 200, result)
        status, _, body = self.request("/api/export?kind=attendance&month=2026-10")
        self.assertEqual(status, 200)
        ws = load_workbook(BytesIO(body)).active
        self.assertEqual(ws.page_setup.orientation, "landscape")
        self.assertEqual(ws.oddHeader.center.text, "İSTASYON && İSTASYON")
        self.assertEqual(ws.oddFooter.left.text, "Hazırlayan: ____")
        self.assertEqual(ws.oddFooter.right.text, "Sayfa &P / &N")
        validations = ws.data_validations.dataValidation
        self.assertEqual(len(validations), 1)
        self.assertIn("G.Ç", validations[0].formula1)
        self.assertFalse(validations[0].showErrorMessage, "açılır liste özel kodları engellememeli")
        self.assertIn(str(validations[0].sqref).split()[0], ("C4", "C4:C4"))
        self.assertEqual(len(str(validations[0].sqref).split()), 31)
        rules = [rule for ranges in ws.conditional_formatting for rule in ranges.rules]
        self.assertIn('EXACT(C4,"R")', [formula for rule in rules for formula in rule.formula])
        self.assertEqual(ws["G4"].value, "R")
        self.assertEqual(ws["C4"].value, "X")
        # Yardımcılar kapalıyken çıktı şablondaki gibi kalır.
        settings["sheets"]["attendance:template:0"]["page_setup"] = {"orientation": "landscape"}
        self.json_request("/api/workbook-settings", "PUT", settings)
        ws = load_workbook(BytesIO(self.request("/api/export?kind=attendance&month=2026-10")[2])).active
        self.assertFalse(ws.data_validations.dataValidation)
        self.assertFalse(ws.oddFooter.right.text)
        bad = json.loads(json.dumps(settings))
        bad["sheets"]["attendance:template:0"]["page_setup"] = {"footer_text": "x" * 121}
        self.assertEqual(self.json_request("/api/workbook-settings", "PUT", bad)[0], 400)

        status, naming = self.json_request("/api/export-name?kind=attendance&month=2026-10")
        self.assertEqual((status, naming["filename"]), (200, "PUANTAJ_2026-10.xlsx"))
        status, result = self.json_request("/api/ui-settings", "PUT", {"export": {
            "filename_template": "{AYADI} {YIL}/{TUR} {HAFTA}", "last_folder": "C:\\Çıktılar"}})
        self.assertEqual(status, 200, result)
        naming = self.json_request("/api/export-name?kind=schedule&month=2026-10&week=1")[1]
        self.assertEqual(naming, {"filename": "EKİM 2026_VARDIYA_LISTESI 2.HAFTA.xlsx", "folder": "C:\\Çıktılar"})
        self.assertEqual(self.json_request("/api/export-name?kind=attendance&month=2026-10")[1]["filename"],
                         "EKİM 2026_PUANTAJ.xlsx")
        self.assertEqual(self.json_request("/api/ui-settings", "PUT", {"export": {"filename_template": "{}", "last_folder": ""}})[0], 400)

        status, preview = self.json_request("/api/export-preview?kind=attendance&month=2026-10")
        self.assertEqual(status, 200, preview)
        html = preview["html"]
        self.assertTrue(html.startswith('<table class="xl-preview"'))
        self.assertIn("İSTASYON EKİM 2026 PUANTAJ", html)
        self.assertIn("Çıktı Kişi", html)
        self.assertIn('colspan="', html, "birleştirilmiş hücreler önizlemede korunmalı")
        status, preview = self.json_request("/api/export-preview?kind=schedule&month=2026-10&week=0")
        self.assertEqual(status, 200, preview)
        self.assertIn("Çıktı Kişi", preview["html"])

    def test_exports_match_template_structure_cell_by_cell(self):
        def signature(cell):
            border = cell.border
            return (
                cell.font.name, cell.font.sz, bool(cell.font.b),
                border.left.style, border.right.style, border.top.style, border.bottom.style,
                cell.alignment.horizontal, cell.alignment.vertical, bool(cell.alignment.wrap_text),
                cell.number_format,
            )

        status, _, body = self.request("/api/export?kind=schedule&month=2026-10")
        self.assertEqual(status, 200)
        exported = load_workbook(BytesIO(body))
        template = load_workbook(ROOT / "templates" / "weekly_schedule.xlsx")
        self.assertEqual(exported.sheetnames, template.sheetnames)
        for out, ref in zip(exported.worksheets, template.worksheets):
            self.assertEqual(out.dimensions, ref.dimensions)
            self.assertEqual(out.page_setup.orientation, ref.page_setup.orientation)
            for letter, dimension in ref.column_dimensions.items():
                self.assertEqual(out.column_dimensions[letter].width, dimension.width, letter)
            for row, dimension in ref.row_dimensions.items():
                self.assertEqual(out.row_dimensions[row].height, dimension.height, f"{out.title} row {row}")
            out_merges = {str(item) for item in out.merged_cells.ranges}
            ref_merges = {str(item) for item in ref.merged_cells.ranges}
            self.assertLessEqual(out_merges, ref_merges)
            self.assertTrue({item for item in ref_merges if int(re.sub(r"\D", "", item.split(":")[0])) <= 3} <= out_merges)
            for row in range(1, 4):
                for column in range(1, ref.max_column + 1):
                    expected, actual = ref.cell(row, column), out.cell(row, column)
                    self.assertEqual(signature(actual), signature(expected), f"{out.title}!{actual.coordinate}")
                    if not isinstance(expected.value, str) or expected.value.strip():
                        self.assertEqual(actual.value, expected.value, f"{out.title}!{actual.coordinate}")

        status, _, body = self.request("/api/export?kind=attendance&month=2026-10")
        self.assertEqual(status, 200)
        out = load_workbook(BytesIO(body)).active
        ref = load_workbook(ROOT / "templates" / "attendance.xlsx").active
        dropped = 34  # NORMAL MESAİ sütunu şablondan çıkarıldı; sonrakiler bir sola kayar.
        source = lambda column: column if column < dropped else column + 1
        self.assertEqual(out.max_column, ref.max_column - 1)
        self.assertEqual(out.max_row, ref.max_row)
        self.assertEqual(len(out.merged_cells.ranges), len(ref.merged_cells.ranges))
        for column in range(1, out.max_column + 1):
            self.assertEqual(out.column_dimensions[get_column_letter(column)].width,
                             ref.column_dimensions[get_column_letter(source(column))].width, column)
        for row, dimension in ref.row_dimensions.items():
            self.assertEqual(out.row_dimensions[row].height, dimension.height, f"row {row}")
        for row in list(range(2, 6)) + list(range(37, ref.max_row + 1)):
            for column in range(1, out.max_column + 1):
                expected, actual = ref.cell(row, source(column)), out.cell(row, column)
                if column == dropped - 1 and row < 6:
                    continue  # sağ kenarlık, silinen sütunun yerine bitişik sütuna taşınır
                self.assertEqual(signature(actual)[:3] + signature(actual)[7:10], signature(expected)[:3] + signature(expected)[7:10],
                                 f"{actual.coordinate}")
        headers = [out.cell(3, column).value for column in range(34, out.max_column + 1)]
        self.assertNotIn("NORMAL MESAİ", headers)
        self.assertIn("KESİNTİ", headers)

    def upload_table_template(self, kind, workbook):
        buffer = BytesIO()
        workbook.save(buffer)
        return self.json_request("/api/table-templates/upload", "POST", {
            "kind": kind, "content": base64.b64encode(buffer.getvalue()).decode("ascii"),
        })

    def test_table_template_upload_validates_layout_and_drives_exports(self):
        status, templates = self.json_request("/api/table-templates")
        self.assertEqual(status, 200)
        self.assertFalse(templates["schedule"]["custom"])
        self.assertEqual(len(templates["schedule"]["sheets"]), 5)
        status, headers, original = self.request("/api/table-templates/download?kind=schedule")
        self.assertEqual(status, 200)
        self.assertIn("VARDIYA_SABLONU.xlsx", headers["Content-Disposition"])

        inserted = load_workbook(BytesIO(original))
        inserted.worksheets[2].insert_rows(10)
        status, result = self.upload_table_template("schedule", inserted)
        self.assertEqual(status, 400)
        self.assertIn("Satır/sütun eklemeyin", result["error"])
        unmerged = load_workbook(BytesIO(original))
        unmerged.worksheets[0].unmerge_cells(str(next(iter(unmerged.worksheets[0].merged_cells.ranges))))
        status, result = self.upload_table_template("schedule", unmerged)
        self.assertEqual(status, 400)
        self.assertIn("birleştirilmiş", result["error"])
        self.assertFalse(self.json_request("/api/table-templates")[1]["schedule"]["custom"])

        edited = load_workbook(BytesIO(original))
        for ws in edited.worksheets:
            ws["N35"] = "İSTASYON ŞEFİ"
        edited.worksheets[0]["V40"].value = None  # empty styled cell beyond the layout is trimmed
        status, result = self.upload_table_template("schedule", edited)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["custom"])
        self.assertIn("İSTASYON ŞEFİ", self.request("/api/template?kind=schedule&sheet=0")[2].decode("utf-8"))
        status, _, body = self.request("/api/export?kind=schedule&month=2026-10")
        self.assertEqual(status, 200)
        workbook = load_workbook(BytesIO(body))
        self.assertEqual(workbook.worksheets[0]["N35"].value, "İSTASYON ŞEFİ")
        self.assertEqual(workbook.worksheets[0].max_row, 35)

        status, created = self.json_request("/api/backups", "POST", {})
        self.assertEqual(status, 201)
        self.assertEqual(created["backup"]["template_count"], 1)
        status, result = self.json_request("/api/table-templates/reset", "POST", {"kind": "schedule"})
        self.assertFalse(result["custom"])
        status, restored = self.json_request("/api/backups/restore", "POST", {"id": created["backup"]["id"]})
        self.assertEqual(status, 200, restored)
        self.assertEqual(restored["safety_backup"]["template_count"], 0)
        self.assertTrue(self.json_request("/api/table-templates")[1]["schedule"]["custom"])
        status, _, body = self.request("/api/export?kind=schedule&month=2026-10")
        self.assertEqual(load_workbook(BytesIO(body)).worksheets[0]["N35"].value, "İSTASYON ŞEFİ")
        status, result = self.json_request("/api/table-templates/reset", "POST", {"kind": "schedule"})
        self.assertEqual(status, 200)
        self.assertFalse(result["custom"])
        status, _, body = self.request("/api/export?kind=schedule&month=2026-10")
        self.assertEqual(load_workbook(BytesIO(body)).worksheets[0]["N35"].value, "İSTASYON MÜDÜRÜ")

    def test_template_format_layer_applies_until_page_has_own_settings(self):
        settings = {"sheets": {
            "schedule:template:0": {"cells": {"6:4": {"color": "#ddebf7"}}, "hide_notes": False, "zoom": 70},
            "attendance:template:0": {"cells": {}, "hide_notes": False, "zoom": 85},
        }, "quick_colors": {}}
        status, result = self.json_request("/api/workbook-settings", "PUT", settings)
        self.assertEqual(status, 200, result)
        for month in ("2026-10", "2027-02"):
            status, _, body = self.request(f"/api/export?kind=schedule&month={month}")
            ws = load_workbook(BytesIO(body)).worksheets[0]
            self.assertEqual(ws["D6"].fill.fgColor.rgb[-6:], "DDEBF7")
            self.assertEqual(ws.sheet_view.zoomScale, 70)
        status, _, body = self.request("/api/export?kind=attendance&month=2026-10")
        self.assertEqual(load_workbook(BytesIO(body)).active.sheet_view.zoomScale, 85)

        settings["sheets"]["schedule:2026-10:0"] = {
            "cells": {"6:4": {"color": "#fff2cc"}}, "hide_notes": False, "zoom": 60,
        }
        self.assertEqual(self.json_request("/api/workbook-settings", "PUT", settings)[0], 200)
        status, _, body = self.request("/api/export?kind=schedule&month=2026-10")
        ws = load_workbook(BytesIO(body)).worksheets[0]
        self.assertEqual(ws["D6"].fill.fgColor.rgb[-6:], "FFF2CC")
        self.assertEqual(ws.sheet_view.zoomScale, 60)

        for invalid_key in ("attendance:template:1", "schedule:templat:0"):
            invalid = {"sheets": {invalid_key: {"cells": {}, "hide_notes": False, "zoom": 55}}, "quick_colors": {}}
            self.assertEqual(self.json_request("/api/workbook-settings", "PUT", invalid)[0], 400)

    def test_schedule_row_and_column_deletion_preserves_staff_and_manager_layout(self):
        person = self.add_person("Silme Sonrası Personel")
        additional_person = self.add_person("Ek Vardiya Satırı")
        _, schedule = self.json_request("/api/schedule?month=2026-10")
        status, _ = self.json_request("/api/schedule-months", "PUT", {
            "months": {"2026-10": {
                "revision": schedule["revision"],
                "entries": [{
                    "row_id": "delete-layout-person",
                    "employee_id": person["id"],
                    "section": "MARKET",
                    "slot": 1,
                    "days": {"2026-10-12": {"kind": "work", "start": "09:00", "end": "18:00"}},
                }, {
                    "row_id": "delete-layout-extra-person",
                    "employee_id": additional_person["id"],
                    "section": "MARKET",
                    "slot": 7,
                    "days": {"2026-10-12": {"kind": "weekly_off"}},
                }],
            }},
        })
        self.assertEqual(status, 200)

        settings = {"sheets": {
            "schedule:2026-10:1": {
                "cells": {}, "hide_notes": False, "zoom": 55,
                "extra_cols": 1, "delete_rows": [34], "delete_cols": [23],
            },
        }, "quick_colors": {}}
        status, saved = self.json_request("/api/workbook-settings", "PUT", settings)
        self.assertEqual(status, 200)
        self.assertEqual(saved["sheets"]["schedule:2026-10:1"]["delete_rows"], [34])
        self.assertEqual(saved["sheets"]["schedule:2026-10:1"]["delete_cols"], [23])

        status, _, body = self.request("/api/export?kind=schedule&month=2026-10&week=1")
        self.assertEqual(status, 200)
        worksheet = load_workbook(BytesIO(body)).worksheets[1]
        self.assertEqual(worksheet["C6"].value, person["name"])
        self.assertEqual(worksheet["C12"].value, additional_person["name"])
        self.assertEqual(worksheet["C14"].value, "TEMİZLİK")
        self.assertEqual(worksheet["N35"].value, "İSTASYON MÜDÜRÜ")
        self.assertEqual(worksheet.max_row, 35)
        self.assertEqual(worksheet.max_column, 22)

        for field, position in (("delete_rows", 35), ("delete_rows", 6), ("delete_cols", 21)):
            invalid = {"sheets": {
                "schedule:2026-10:1": {
                    "cells": {}, "hide_notes": False, "zoom": 55,
                    field: [position],
                },
            }, "quick_colors": {}}
            status, result = self.json_request("/api/workbook-settings", "PUT", invalid)
            self.assertEqual(status, 400, (field, position))
            self.assertIn("silinemez", result["error"])

    def test_attendance_calendar_categories_match_ui_and_excel_colors(self):
        _, october = self.json_request("/api/attendance?month=2026-10")
        self.assertEqual(october["calendar"]["2026-10-01"], "weekday")
        self.assertEqual(october["calendar"]["2026-10-03"], "weekend")
        self.assertEqual(october["calendar"]["2026-10-28"], "official")
        self.assertEqual(october["calendar"]["2026-10-29"], "official")

        status, _, body = self.request("/api/export?kind=attendance&month=2026-10")
        self.assertEqual(status, 200)
        worksheet = load_workbook(BytesIO(body)).active
        for day, category in (
            (1, "weekday"), (3, "weekend"), (28, "official"), (29, "official"),
        ):
            expected = ATTENDANCE_CATEGORY_COLORS[category].lstrip("#")
            self.assertEqual(worksheet.cell(3, 2 + day).fill.fgColor.rgb[-6:], expected)
            self.assertEqual(worksheet.cell(2, 2 + day).fill.fgColor.rgb[-6:], expected)

        for month, eve, holiday_days in (
            ("2026-03", 19, (20, 21, 22)),
            ("2026-05", 26, (27, 28, 29, 30)),
        ):
            _, calendar_data = self.json_request(f"/api/attendance?month={month}")
            self.assertEqual(calendar_data["calendar"][f"{month}-{eve:02d}"], "official")
            for day in holiday_days:
                self.assertEqual(calendar_data["calendar"][f"{month}-{day:02d}"], "religious")
            status, _, body = self.request(f"/api/export?kind=attendance&month={month}")
            self.assertEqual(status, 200)
            worksheet = load_workbook(BytesIO(body)).active
            self.assertEqual(
                worksheet.cell(3, 2 + eve).fill.fgColor.rgb[-6:],
                ATTENDANCE_CATEGORY_COLORS["official"].lstrip("#"),
            )
            for day in holiday_days:
                self.assertEqual(
                    worksheet.cell(3, 2 + day).fill.fgColor.rgb[-6:],
                    ATTENDANCE_CATEGORY_COLORS["religious"].lstrip("#"),
                )

    def test_multiple_months_save_atomically_and_export_selected_month_rows(self):
        first = self.add_person("Birinci Personel")
        second = self.add_person("İkinci Personel")
        october = [{"row_id": "oct-a", "employee_id": first["id"], "section": "MARKET",
                    "slot": 2, "days": {"2026-10-31": {"kind": "weekly_off"}}},
                   {"row_id": "oct-b", "employee_id": second["id"], "section": "MARKET",
                    "slot": 1, "days": {}}]
        november = [{"row_id": "nov-a", "employee_id": first["id"], "section": "MARKET",
                     "slot": 1, "days": {"2026-11-01": {"kind": "work", "start": "09:00", "end": "18:00"}}}]
        payload = {"months": {"2026-10": {"revision": "", "entries": october},
                              "2026-11": {"revision": "", "entries": november}}}
        status, saved = self.json_request("/api/schedule-months", "PUT", payload)
        self.assertEqual(status, 200)
        self.assertEqual(set(saved["revisions"]), {"2026-10", "2026-11"})
        october_rows = self.json_request("/api/attendance?month=2026-10")[1]["attendance"]["rows"]
        self.assertEqual([row["employee_id"] for row in october_rows], [second["id"], first["id"]])
        self.assertEqual(october_rows[1]["codes"]["2026-10-31"], "H")
        self.assertEqual(self.json_request("/api/attendance?month=2026-11")[1]["attendance"]["rows"][0]["codes"]["2026-11-01"], "X")
        status, _, body = self.request("/api/export?kind=schedule&month=2026-11&week=0")
        self.assertEqual(status, 200)
        sheet = load_workbook(BytesIO(body)).worksheets[0]
        self.assertEqual(sheet["C6"].value, first["name"])
        self.assertEqual(sheet["N6"].value, "HAFTALIK İZİNLİ")
        self.assertIsNone(sheet["C7"].value)
        self.assertEqual(sheet["P6"].value.hour, 9)

        payload["months"]["2026-10"]["revision"] = saved["revisions"]["2026-10"]
        payload["months"]["2026-10"]["entries"][0]["days"] = {}
        status, _ = self.json_request("/api/schedule-months", "PUT", payload)
        self.assertEqual(status, 409)
        self.assertEqual(self.json_request("/api/schedule?month=2026-10")[1]["entries"][0]["days"]["2026-10-31"]["kind"], "weekly_off")
        self.assertEqual(self.json_request("/api/schedule?month=2026-10")[1]["revision"], saved["revisions"]["2026-10"])

    def test_font_settings_export_and_validate(self):
        font = {"font_name": "Arial", "font_size": 18, "font_color": "#123456",
                "font_bold": True, "font_italic": True}
        settings = {"sheets": {"schedule:2026-10:0": {
            "cells": {"6:4": font, "6:5": font}, "hide_notes": False, "zoom": 55,
        }}, "quick_colors": {}}
        self.assertEqual(self.json_request("/api/workbook-settings", "PUT", settings)[0], 200)
        status, _, body = self.request("/api/export?kind=schedule&month=2026-10")
        self.assertEqual(status, 200)
        cell = load_workbook(BytesIO(body)).worksheets[0]["D6"]
        self.assertEqual((cell.font.name, cell.font.sz, cell.font.bold, cell.font.italic), ("Arial", 18, True, True))
        self.assertEqual(cell.font.color.rgb[-6:], "123456")
        settings["sheets"]["schedule:2026-10:0"]["cells"]["6:4"]["font_size"] = 100
        self.assertEqual(self.json_request("/api/workbook-settings", "PUT", settings)[0], 400)

    def test_dimensions_and_alignment_round_trip_and_export(self):
        settings = {"sheets": {
            "schedule:2026-10:0": {
                "cells": {"7:4": {"horizontal": "right", "vertical": "top",
                                    "shrink_to_fit": True, "wrap_text": False}},
                "row_heights": {"7": 35, "45": 29},
                "column_widths": {"4": 24.5},
                "insert_rows": [6], "hide_notes": False, "zoom": 55,
            },
            "attendance:2026-10:0": {
                "cells": {"4:2": {"horizontal": "left", "vertical": "bottom",
                                   "shrink_to_fit": False, "wrap_text": True}},
                "row_heights": {"4": 32}, "column_widths": {"2": 28},
                "hide_notes": False, "zoom": 100,
            },
        }, "quick_colors": {}}
        status, saved = self.json_request("/api/workbook-settings", "PUT", settings)
        self.assertEqual(status, 200)
        self.assertEqual(saved, settings)
        status, _, body = self.request("/api/export?kind=schedule&month=2026-10")
        self.assertEqual(status, 200)
        ws = load_workbook(BytesIO(body)).worksheets[0]
        self.assertEqual(ws.row_dimensions[7].height, 35)
        self.assertEqual(ws.row_dimensions[45].height, 29)
        self.assertEqual(ws.column_dimensions["D"].width, 24.5)
        self.assertEqual((ws["D7"].alignment.horizontal, ws["D7"].alignment.vertical), ("right", "top"))
        self.assertTrue(ws["D7"].alignment.shrinkToFit)
        status, _, body = self.request("/api/export?kind=attendance&month=2026-10")
        self.assertEqual(status, 200)
        ws = load_workbook(BytesIO(body)).active
        self.assertEqual(ws.row_dimensions[4].height, 32)
        self.assertEqual(ws.column_dimensions["B"].width, 28)
        self.assertEqual((ws["B4"].alignment.horizontal, ws["B4"].alignment.vertical), ("left", "bottom"))
        self.assertTrue(ws["B4"].alignment.wrapText)
        invalid = json.loads(json.dumps(settings))
        invalid["sheets"]["attendance:2026-10:0"]["column_widths"]["2"] = 256
        self.assertEqual(self.json_request("/api/workbook-settings", "PUT", invalid)[0], 400)
        invalid = json.loads(json.dumps(settings))
        invalid["sheets"]["attendance:2026-10:0"]["cells"]["4:2"]["shrink_to_fit"] = True
        self.assertEqual(self.json_request("/api/workbook-settings", "PUT", invalid)[0], 400)
        self.assertEqual(self.json_request("/api/workbook-settings")[1], settings)

    def test_excel_style_merges_borders_text_effects_and_page_setup_export(self):
        from openpyxl.utils import get_column_letter
        from scheduling import workbook_template
        template = workbook_template("attendance", 0)
        note_merge = next(merge for merge in template["merges"] if merge["row"] >= 25)
        anchor = f"{note_merge['row']}:{note_merge['column']}"
        width = len(template["columns"])
        border = {side: {"style": "thick", "color": "#FF0000"} for side in ("top", "bottom", "left", "right")}
        sheet = {
            "cells": {
                f"{note_merge['row']}:{note_merge['column']}": {"text": "Not"},
                f"1:{width + 1}": {"text": "Ek başlık", "font_underline": True, "font_strike": True,
                                    "text_rotation": 90, "border": border, "font_name": "Georgia"},
            },
            "extra_cols": 3, "hide_notes": False, "zoom": 100,
            "merges_remove": [anchor],
            "merges_add": [{"row": 1, "column": width + 1, "rowspan": 2, "colspan": 3}],
            "page_setup": {"print_area": "A1:AK40", "orientation": "landscape", "paper": "A3",
                           "fit_width": 1, "fit_height": 0, "margins": {"top": 2.54, "left": 1},
                           "print_title_rows": "1:3", "center_horizontally": True},
        }
        settings = {"sheets": {"attendance:2026-10:0": sheet}, "quick_colors": {}}
        status, saved = self.json_request("/api/workbook-settings", "PUT", settings)
        self.assertEqual(status, 200, saved)
        status, _, body = self.request("/api/export?kind=attendance&month=2026-10")
        self.assertEqual(status, 200)
        ws = load_workbook(BytesIO(body)).active
        ranges = {str(item) for item in ws.merged_cells.ranges}
        first = get_column_letter(width + 1)
        self.assertIn(f"{first}1:{get_column_letter(width + 3)}2", ranges)
        self.assertFalse(any(item.min_row == note_merge["row"] and item.min_col == note_merge["column"]
                             for item in ws.merged_cells.ranges))
        cell = ws[f"{first}1"]
        self.assertEqual(cell.value, "Ek başlık")
        self.assertEqual((cell.font.u, cell.font.strike, cell.font.name), ("single", True, "Georgia"))
        self.assertEqual(cell.alignment.textRotation, 90)
        self.assertEqual((cell.border.top.style, cell.border.left.style), ("thick", "thick"))
        self.assertEqual(cell.border.top.color.rgb[-6:], "FF0000")
        # The bottom/right edges of a merged range carry the border, not the anchor alone.
        self.assertEqual(ws[f"{get_column_letter(width + 3)}2"].border.right.style, "thick")
        self.assertTrue(ws.print_area.endswith("$A$1:$AK$40"))
        self.assertEqual((ws.page_setup.orientation, str(ws.page_setup.paperSize)), ("landscape", "8"))
        self.assertTrue(ws.sheet_properties.pageSetUpPr.fitToPage)
        self.assertEqual((ws.page_setup.fitToWidth, ws.page_setup.fitToHeight), (1, 0))
        self.assertAlmostEqual(ws.page_margins.top, 1.0, places=2)
        self.assertEqual(ws.print_title_rows, "$1:$3")
        self.assertTrue(ws.print_options.horizontalCentered)

        for invalid_sheet in (
            {**sheet, "merges_add": [{"row": 5, "column": 3, "rowspan": 1, "colspan": 2}]},
            {**sheet, "merges_add": [{"row": 1, "column": width + 1, "rowspan": 1, "colspan": 1}]},
            {**sheet, "merges_remove": ["999:1"]},
            {**sheet, "page_setup": {"print_area": "Z9:A1"}},
            {**sheet, "page_setup": {"paper": "B5"}},
            {**sheet, "cells": {f"1:{width + 1}": {"border": {"top": {"style": "wavy"}}}}},
            {**sheet, "cells": {f"1:{width + 1}": {"font_name": "Comic Sans"}}},
        ):
            status, _ = self.json_request("/api/workbook-settings", "PUT",
                                          {"sheets": {"attendance:2026-10:0": invalid_sheet}, "quick_colors": {}})
            self.assertEqual(status, 400, invalid_sheet)

    def test_template_versions_keep_previous_snapshots(self):
        key = "schedule:template:0"
        for zoom in (60, 70, 80):
            settings = {"sheets": {key: {"cells": {}, "hide_notes": False, "zoom": zoom}}, "quick_colors": {}}
            self.assertEqual(self.json_request("/api/workbook-settings", "PUT", settings)[0], 200)
        status, payload = self.json_request("/api/template-versions")
        self.assertEqual(status, 200)
        self.assertEqual([item["sheet"]["zoom"] for item in payload["versions"][key]], [70, 60])

    def test_monthly_exports_record_the_template_revision_used(self):
        import hashlib

        key = "schedule:template:0"
        for zoom in (60, 80):
            settings = {
                "sheets": {key: {"cells": {}, "hide_notes": False, "zoom": zoom}},
                "quick_colors": {},
            }
            self.assertEqual(self.json_request("/api/workbook-settings", "PUT", settings)[0], 200)
            status, _, content = self.request("/api/export?kind=schedule&month=2026-10&week=0")
            self.assertEqual(status, 200)
            if zoom == 60:
                first_content = content
        _, payload = self.json_request("/api/document-workflows/exports")
        records = {item["sha256"]: item for item in payload["exports"]}
        first_record = records[hashlib.sha256(first_content).hexdigest()]
        later_record = next(
            item for item in payload["exports"]
            if item["sha256"] != hashlib.sha256(first_content).hexdigest()
        )
        self.assertRegex(first_record["template_revision"], r"^[0-9a-f]{64}$")
        self.assertRegex(later_record["template_revision"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(first_record["template_revision"], later_record["template_revision"])
        self.assertEqual(first_record["sha256"], hashlib.sha256(first_content).hexdigest())

    def test_stale_out_of_layout_settings_are_repaired_so_saving_still_works(self):
        import sqlite3

        stale = {"sheets": {
            "schedule:2026-10:0": {"cells": {"6:4": {"color": "#FFEEAA"}, "80:11": {"color": "#FFEEAA"}},
                                   "hide_notes": False, "zoom": 55, "row_heights": {"90": 20}},
            "attendance:2026-10:0": {"cells": {}, "hide_notes": False, "zoom": 500},
        }, "quick_colors": {}}
        self.assertEqual(self.json_request("/api/workbook-settings")[0], 200)
        connection = sqlite3.connect(Path(self.temp.name) / "data.sqlite3")
        try:
            with connection:
                connection.execute("INSERT INTO app_settings(name, payload) VALUES ('workbook_settings', ?) "
                                   "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload", (json.dumps(stale),))
        finally:
            connection.close()
        status, settings = self.json_request("/api/workbook-settings")
        self.assertEqual(status, 200)
        sheet = settings["sheets"]["schedule:2026-10:0"]
        self.assertEqual(list(sheet["cells"]), ["6:4"])
        self.assertFalse(sheet.get("row_heights"))
        self.assertNotIn("attendance:2026-10:0", settings["sheets"])
        sheet["cells"]["7:4"] = {"color": "#AACCEE"}
        self.assertEqual(self.json_request("/api/workbook-settings", "PUT", settings)[0], 200)

    def test_desktop_save_dialog_writes_to_selected_path_and_cancellation_does_not_write(self):
        path = Path(self.temp.name) / "secilen-cikti.xlsx"

        class FakeWindow:
            def __init__(self, selection):
                self.selection = selection

            def create_file_dialog(self, *args, **kwargs):
                return self.selection

        api = DesktopAPI(self.base_url)
        api._window = FakeWindow((str(path),))
        result = api.save_excel("schedule", "2026-10", 0)
        self.assertEqual(result, {"cancelled": False, "path": str(path)})
        self.assertGreater(len(load_workbook(path).worksheets), 1)
        api._window = FakeWindow(None)
        self.assertEqual(api.save_excel("attendance", "2026-10"), {"cancelled": True})
        with self.assertRaises(ValueError):
            api.save_excel("other", "2026-10")

    def test_import_previews_excel_and_word_without_saving(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["Personel", "Bölüm", "Tarih", "Giriş", "Çıkış", "Durum"])
        ws.append(["Ayşe Yılmaz", "MARKET", "01.10.2026", "09:00", "18:00", "X"])
        ws.append(["Ayşe Yılmaz", "MARKET", "02.10.2026", "", "", "H"])
        stream = BytesIO()
        wb.save(stream)
        document = Document()
        table = document.add_table(rows=0, cols=6)
        for values in (["Personel", "Bölüm", "Tarih", "Giriş", "Çıkış", "Durum"],
                       ["Ayşe Yılmaz", "MARKET", "01.10.2026", "09:00", "18:00", "X"],
                       ["Ayşe Yılmaz", "MARKET", "02.10.2026", "", "", "H"]):
            for cell, value in zip(table.add_row().cells, values):
                cell.text = value
        word = BytesIO()
        document.save(word)
        for filename, contents in (("liste.xlsx", stream.getvalue()), ("liste.docx", word.getvalue())):
            status, result = self.json_request("/api/import-preview", "POST", {
                "filename": filename, "content": base64.b64encode(contents).decode(), "month": "2026-10",
            })
            self.assertEqual(status, 200, result)
            self.assertEqual(len(result["rows"]), 2)
            self.assertEqual(result["rows"][0]["entry"], {"kind": "work", "start": "09:00", "end": "18:00"})
            self.assertEqual(result["rows"][1]["entry"], {"kind": "weekly_off"})
        self.assertEqual(self.json_request("/api/schedule?month=2026-10")[1]["entries"], [])
        self.assertEqual(self.json_request("/api/people")[1]["people"], [])

    def test_import_existing_schedule_export_and_reject_scanned_pdf(self):
        person = self.add_person("Ayşe Yılmaz")
        entry = {"row_id": "import-test", "employee_id": person["id"], "section": "MARKET", "slot": 1,
                 "days": {"2026-10-01": {"kind": "work", "start": "09:00", "end": "18:00"},
                          "2026-10-02": {"kind": "weekly_off"}}}
        self.assertEqual(self.json_request("/api/schedule", "PUT",
            {"month": "2026-10", "revision": "", "entries": [entry]})[0], 200)
        _, _, workbook = self.request("/api/export?kind=schedule&month=2026-10")
        status, result = self.json_request("/api/import-preview", "POST", {
            "filename": "vardiya.xlsx", "content": base64.b64encode(workbook).decode(), "month": "2026-10",
        })
        self.assertEqual(status, 200, result)
        self.assertEqual({row["date"] for row in result["rows"]}, {"2026-10-01", "2026-10-02"})
        pdf = PdfWriter()
        pdf.add_blank_page(width=200, height=200)
        stream = BytesIO()
        pdf.write(stream)
        with patch("document_import.ocr_windows.recognize_pdf_pages", return_value=[[]]):
            status, result = self.json_request("/api/import-preview", "POST", {
                "filename": "taranmis.pdf", "content": base64.b64encode(stream.getvalue()).decode(), "month": "2026-10",
            })
        self.assertEqual(status, 400)
        self.assertIn("doğrulanmalıdır", result["error"])

    def test_import_text_pdf_table(self):
        pdf = PdfWriter()
        page = pdf.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): pdf._add_object(font)})})
        text = DecodedStreamObject()
        text.set_data(b"BT /F1 11 Tf 30 750 Td "
                      b"(Personel  |  Bolum  |  Tarih  |  Giris  |  Cikis  |  Durum) Tj "
                      b"0 -18 Td (Ayse Yilmaz  |  MARKET  |  01.10.2026  |  09:00  |  18:00  |  X) Tj ET")
        page[NameObject("/Contents")] = pdf._add_object(text)
        stream = BytesIO()
        pdf.write(stream)
        status, result = self.json_request("/api/import-preview", "POST", {
            "filename": "liste.pdf", "content": base64.b64encode(stream.getvalue()).decode(), "month": "2026-10",
        })
        self.assertEqual(status, 200, result)
        self.assertEqual(result["rows"][0]["entry"], {"kind": "work", "start": "09:00", "end": "18:00"})

    def test_append_note_rows_and_columns_preserves_template_and_exports_content(self):
        settings = {"sheets": {
            "schedule:2026-11:0": {"cells": {"45:3": {"text": "Ek satır"},
                                               "6:23": {"text": "Ek sütun", "color": "#ddebf7"}},
                                     "hide_notes": False, "zoom": 55, "extra_rows": 1, "extra_cols": 1},
            "attendance:2026-11:0": {"cells": {"44:2": {"text": "Puantaj notu"},
                                                 "4:37": {"text": "Ek bilgi"}},
                                       "hide_notes": False, "zoom": 100, "extra_rows": 1, "extra_cols": 1},
        }, "quick_colors": {}}
        self.assertEqual(self.json_request("/api/workbook-settings", "PUT", settings)[0], 200)
        status, _, body = self.request("/api/export?kind=schedule&month=2026-11")
        self.assertEqual(status, 200)
        sheet = load_workbook(BytesIO(body)).worksheets[0]
        self.assertEqual(sheet["C45"].value, "Ek satır")
        self.assertEqual(sheet["W6"].value, "Ek sütun")
        self.assertEqual(sheet["W6"].fill.fgColor.rgb[-6:], "DDEBF7")
        self.assertEqual(sheet["U6"].value, "=SUM(R6:T6)")
        self.assertEqual(sheet.column_dimensions["W"].width, 12)
        status, _, body = self.request("/api/export?kind=attendance&month=2026-11")
        self.assertEqual(status, 200)
        sheet = load_workbook(BytesIO(body)).active
        self.assertEqual(sheet["B44"].value, "Puantaj notu")
        self.assertEqual(sheet["AK4"].value, "Ek bilgi")
        invalid = json.loads(json.dumps(settings))
        invalid["sheets"]["schedule:2026-11:0"]["extra_cols"] = 0
        self.assertEqual(self.json_request("/api/workbook-settings", "PUT", invalid)[0], 400)

    def test_insert_rows_and_columns_preserves_data_merges_and_formulas(self):
        person = self.add_person("Deneme Personeli")
        entry = {"row_id": "entry", "employee_id": person["id"], "section": "MARKET",
                 "slot": 1, "days": {"2026-11-02": {"kind": "work", "start": "09:00", "end": "18:00"}}}
        self.assertEqual(self.json_request("/api/schedule", "PUT",
                         {"month": "2026-11", "revision": "", "entries": [entry]})[0], 200)
        settings = {"sheets": {"schedule:2026-11:1": {
            "cells": {"6:4": {"text": "Ek satır"}, "7:6": {"text": "Ek sütun", "color": "#ddebf7"}},
            "hide_notes": False, "zoom": 55, "insert_rows": [6], "insert_cols": [6],
        }}, "quick_colors": {}}
        self.assertEqual(self.json_request("/api/workbook-settings", "PUT", settings)[0], 200)
        status, _, body = self.request("/api/export?kind=schedule&month=2026-11&week=1")
        self.assertEqual(status, 200)
        sheet = load_workbook(BytesIO(body)).worksheets[1]
        self.assertEqual(sheet["C7"].value, person["name"])
        self.assertEqual(sheet["D7"].value.hour, 9)
        self.assertEqual(sheet["E7"].value.hour, 18)
        self.assertEqual(sheet["D6"].value, "Ek satır")
        self.assertEqual(sheet["F7"].value, "Ek sütun")
        self.assertEqual(sheet["F7"].fill.fgColor.rgb[-6:], "DDEBF7")
        self.assertEqual(sheet["V7"].value, "=SUM(S7:U7)")
        self.assertEqual(sheet["D14"].value, "=D2")
        self.assertIn("D2:E3", {str(merge) for merge in sheet.merged_cells.ranges})
        self.assertIn("G2:H3", {str(merge) for merge in sheet.merged_cells.ranges})
        invalid = json.loads(json.dumps(settings))
        invalid["sheets"]["schedule:2026-11:1"]["insert_cols"] = [5]
        self.assertEqual(self.json_request("/api/workbook-settings", "PUT", invalid)[0], 400)

    def test_attendance_insertion_keeps_codes_and_expands_merged_ranges(self):
        person = self.add_person("Deneme Personeli")
        entry = {"row_id": "entry", "employee_id": person["id"], "section": "MARKET",
                 "slot": 1, "days": {"2026-11-01": {"kind": "weekly_off"}}}
        self.assertEqual(self.json_request("/api/schedule", "PUT",
                         {"month": "2026-11", "revision": "", "entries": [entry]})[0], 200)
        settings = {"sheets": {"attendance:2026-11:0": {
            "cells": {"4:3": {"text": "Ek bilgi"}}, "hide_notes": False, "zoom": 100,
            "insert_rows": [4], "insert_cols": [3],
        }}, "quick_colors": {}}
        self.assertEqual(self.json_request("/api/workbook-settings", "PUT", settings)[0], 200)
        status, _, body = self.request("/api/export?kind=attendance&month=2026-11")
        self.assertEqual(status, 200)
        sheet = load_workbook(BytesIO(body)).active
        self.assertEqual(sheet["B5"].value, person["name"])
        self.assertEqual(sheet["D5"].value, "H")
        self.assertEqual(sheet["C4"].value, "Ek bilgi")
        original = load_workbook(ROOT / "templates" / "attendance.xlsx").active
        containing = [merge for merge in original.merged_cells.ranges if merge.min_col < 3 <= merge.max_col]
        self.assertTrue(containing)
        for merge in containing:
            expected = (merge.min_row + (merge.min_row >= 4), merge.min_col,
                        merge.max_row + (merge.max_row >= 4), merge.max_col + 1)
            self.assertIn(expected, {(m.min_row, m.min_col, m.max_row, m.max_col) for m in sheet.merged_cells.ranges})

    def test_inserted_first_column_is_in_print_area(self):
        settings = {"sheets": {"schedule:2026-11:0": {
            "cells": {"6:1": {"text": "Ek alan"}}, "hide_notes": False, "zoom": 55,
            "insert_cols": [1],
        }}, "quick_colors": {}}
        self.assertEqual(self.json_request("/api/workbook-settings", "PUT", settings)[0], 200)
        status, _, body = self.request("/api/export?kind=schedule&month=2026-11")
        self.assertEqual(status, 200)
        sheet = load_workbook(BytesIO(body)).worksheets[0]
        self.assertEqual(sheet["A6"].value, "Ek alan")
        self.assertIn("$A$1:", str(sheet.print_area))

    def test_twenty_personnel_and_extra_rows_export_with_shifted_columns(self):
        names = [f"Personel {index:02d}" for index in range(1, 21)]
        status, result = self.json_request("/api/people/bulk", "POST", {"names": names})
        self.assertEqual(status, 201)
        people = result["people"]
        slots = [("MARKET", slot) for slot in range(1, 10)]
        slots += [("TEMİZLİK", slot) for slot in range(1, 5)]
        slots += [("ÖSSE", slot) for slot in range(1, 8)]
        entries = [
            {"row_id": f"row-{index}", "employee_id": person["id"], "section": section,
             "slot": slot, "days": {"2026-11-01": {"kind": "work", "start": "09:00", "end": "18:00"}}}
            for index, (person, (section, slot)) in enumerate(zip(people, slots))
        ]
        status, _ = self.json_request("/api/schedule", "PUT",
                                      {"month": "2026-11", "revision": "", "entries": entries})
        self.assertEqual(status, 200)
        _, data = self.json_request("/api/attendance?month=2026-11")
        self.assertEqual(len(data["attendance"]["rows"]), 20)
        self.assertEqual(data["attendance"]["rows"][6]["codes"]["2026-11-01"], "X")
        settings = {"sheets": {"schedule:2026-11:0": {
            "cells": {"13:3": {"font_bold": True, "font_italic": True}}, "hide_notes": False, "zoom": 55,
            "insert_rows": [6], "insert_cols": [6],
        }}, "quick_colors": {}}
        self.assertEqual(self.json_request("/api/workbook-settings", "PUT", settings)[0], 200)
        status, _, body = self.request("/api/export?kind=schedule&month=2026-11")
        self.assertEqual(status, 200)
        sheet = load_workbook(BytesIO(body)).worksheets[0]
        # MARKET's extra slots 7-9 stay inside the MARKET block (template rows 6-11, shifted by one inserted row).
        self.assertEqual([sheet[f"C{row}"].value for row in range(7, 16)],
                         [person["name"] for person in people[:9]])
        self.assertEqual(sheet["B13"].value, 7)
        self.assertTrue(sheet["C13"].font.italic)
        self.assertEqual(sheet["Q13"].value.hour, 9)
        self.assertEqual(sheet["R13"].value.hour, 18)
        self.assertEqual(sheet["V13"].value, "=SUM(S13:U13)")
        self.assertEqual(sheet["C17"].value, "TEMİZLİK")
        status, _, body = self.request("/api/export?kind=attendance&month=2026-11")
        self.assertEqual(status, 200)
        attendance = load_workbook(BytesIO(body)).active
        self.assertEqual(attendance["B10"].value, people[6]["name"])
        self.assertEqual(attendance["C10"].value, "X")
        status, additional = self.json_request("/api/people/bulk", "POST",
                                               {"names": ["Personel 21", "Personel 22"]})
        self.assertEqual(status, 201)
        current = self.json_request("/api/schedule?month=2026-11")[1]
        extra_entries = [
            {"row_id": f"extra-{slot}", "employee_id": person["id"],
             "section": "ÖSSE", "slot": slot, "days": {}}
            for person, slot in zip(additional["people"], (8, 9))
        ]
        payload = {"month": "2026-11", "revision": current["revision"],
                   "entries": current["entries"] + extra_entries}
        self.assertEqual(self.json_request("/api/schedule", "PUT", payload)[0], 200)
        status, _, body = self.request("/api/export?kind=attendance&month=2026-11")
        self.assertEqual(status, 200)
        attendance = load_workbook(BytesIO(body)).active
        names = [attendance[f"B{row}"].value for row in range(4, 26)]
        self.assertEqual(len(names), 22)
        self.assertIn("Personel 22", names)
        self.assertEqual(attendance["A25"].value, 22)
        self.assertEqual(attendance.row_dimensions[25].height, attendance.row_dimensions[24].height)
        self.assertEqual(attendance["B25"].border.left.style, attendance["B24"].border.left.style)

    def test_quick_entry_colors_follow_schedule_slot_and_attendance_code(self):
        person = self.add_person()
        row = {"row_id": "colored", "employee_id": person["id"], "section": "MARKET",
               "slot": 3, "days": {
                   "2026-10-05": {"kind": "work", "start": "07:00", "end": "15:00", "fill_color": "#e2efda"},
                   "2026-10-06": {"kind": "weekly_off", "fill_color": "#fff2cc"},
               }}
        status, _ = self.json_request("/api/schedule", "PUT",
            {"month": "2026-10", "revision": "", "entries": [row]})
        self.assertEqual(status, 200)
        saved = self.json_request("/api/schedule?month=2026-10")[1]
        self.assertEqual(saved["entries"][0]["days"], row["days"])
        attendance = self.json_request("/api/attendance?month=2026-10")[1]["attendance"]["rows"][0]
        self.assertEqual(attendance["colors"]["2026-10-06"], "#fff2cc")
        status, _, body = self.request("/api/export?kind=schedule&month=2026-10")
        self.assertEqual(status, 200)
        ws = load_workbook(BytesIO(body)).worksheets[1]
        self.assertEqual(ws["C8"].value, person["name"])
        self.assertEqual(ws["D8"].fill.fgColor.rgb[-6:], "E2EFDA")
        self.assertEqual(ws["E8"].fill.fgColor.rgb[-6:], "E2EFDA")
        self.assertEqual(ws["F8"].value, "HAFTALIK İZİNLİ")
        self.assertEqual(ws["F8"].fill.fgColor.rgb[-6:], "FFF2CC")
        status, _, body = self.request("/api/export?kind=attendance&month=2026-10")
        self.assertEqual(status, 200)
        ws = load_workbook(BytesIO(body)).active
        self.assertEqual(ws["H4"].value, "H")
        self.assertEqual(ws["H4"].fill.fgColor.rgb[-6:], "FFF2CC")

    def test_custom_quick_entry_code_synchronizes_to_attendance(self):
        person = self.add_person()
        _, schedule = self.json_request("/api/schedule?month=2026-10")
        entry = {
            "row_id": "custom-shift-code",
            "employee_id": person["id"],
            "section": "MARKET",
            "days": {
                "2026-10-05": {
                    "kind": "work",
                    "start": "09:00",
                    "end": "17:00",
                    "attendance_code": "H",
                    "shift_label": "Gündüz görevi",
                },
                "2026-10-06": {
                    "kind": "custom",
                    "custom_code": "R",
                    "custom_label": "Rapor",
                },
            },
        }
        status, _ = self.json_request(
            "/api/schedule",
            "PUT",
            {"month": "2026-10", "revision": schedule["revision"], "entries": [entry]},
        )
        self.assertEqual(status, 200)
        _, current = self.json_request("/api/schedule?month=2026-10")
        self.assertEqual(current["entries"][0]["days"]["2026-10-05"]["attendance_code"], "H")
        self.assertEqual(current["entries"][0]["days"]["2026-10-06"]["custom_label"], "Rapor")

        _, attendance = self.json_request("/api/attendance?month=2026-10")
        codes = attendance["attendance"]["rows"][0]["codes"]
        self.assertEqual(codes["2026-10-05"], "H")
        self.assertEqual(codes["2026-10-06"], "R")

    def test_schedule_templates_validate_and_persist_weekly_entries(self):
        person = self.add_person()
        template = {
            "id": "weekday-template",
            "name": "  Normal   hafta ",
            "entries": [{
                "employee_id": person["id"],
                "section": "MARKET",
                "slot": 1,
                "days": {"0": {
                    "kind": "work",
                    "start": "07:00",
                    "end": "15:00",
                    "fill_color": "#123456",
                }},
            }],
        }
        status, saved = self.json_request(
            "/api/schedule-templates", "PUT", {"templates": [template]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["templates"][0]["name"], "Normal hafta")
        self.assertEqual(
            saved["templates"][0]["entries"][0]["days"]["0"]["fill_color"],
            "#123456",
        )
        self.assertEqual(
            self.json_request("/api/schedule-templates")[1]["templates"],
            saved["templates"],
        )

        invalid = json.loads(json.dumps(template))
        invalid["entries"][0]["days"] = {"7": {"kind": "weekly_off"}}
        self.assertEqual(
            self.json_request("/api/schedule-templates", "PUT", {"templates": [invalid]})[0],
            400,
        )
        invalid["entries"][0]["days"] = {
            "0": {"kind": "work", "start": "07:00", "end": "15:00"},
        }
        invalid["entries"][0]["employee_id"] = person["id"] + 999
        self.assertEqual(
            self.json_request("/api/schedule-templates", "PUT", {"templates": [invalid]})[0],
            400,
        )

    def test_schedule_save_synchronizes_shift_and_holiday_hours(self):
        person = self.add_person()
        status, data = self.json_request("/api/schedule?month=2026-10")
        self.assertEqual(status, 200)
        entry = {
            "row_id": "october-shift",
            "employee_id": person["id"],
            "section": "MARKET",
            "days": {
                "2026-10-28": {"kind": "work", "start": "12:00", "end": "02:00"},
            },
        }
        status, result = self.json_request(
            "/api/schedule",
            "PUT",
            {"month": "2026-10", "revision": data["revision"], "entries": [entry]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["revision"])

        status, attendance = self.json_request("/api/attendance?month=2026-10")
        self.assertEqual(status, 200)
        row = attendance["attendance"]["rows"][0]
        self.assertEqual(row["codes"]["2026-10-28"], "X")
        self.assertEqual(row["codes"]["2026-10-29"], "G.Ç")
        self.assertEqual(row["normal_hours"], 1.0)
        self.assertEqual(row["holiday_28_hours"], 11.0)
        self.assertEqual(row["holiday_29_hours"], 2.0)

    def test_manual_attendance_code_overrides_schedule_and_stale_write_is_rejected(self):
        person = self.add_person()
        _, month = self.json_request("/api/schedule?month=2026-10")
        entry = {
            "row_id": "work-row",
            "employee_id": person["id"],
            "section": "MARKET",
            "days": {"2026-10-05": {"kind": "work", "start": "09:00", "end": "17:00"}},
        }
        self.assertEqual(
            self.json_request(
                "/api/schedule",
                "PUT",
                {"month": "2026-10", "revision": month["revision"], "entries": [entry]},
            )[0],
            200,
        )
        _, attendance = self.json_request("/api/attendance?month=2026-10")
        overrides = {"2026-10-05": {str(person["id"]): "R"}}
        status, saved = self.json_request(
            "/api/attendance",
            "PUT",
            {"month": "2026-10", "revision": attendance["revision"], "overrides": overrides},
        )
        self.assertEqual(status, 200)
        _, current = self.json_request("/api/attendance?month=2026-10")
        row = current["attendance"]["rows"][0]
        self.assertEqual(row["codes"]["2026-10-05"], "R")
        self.assertEqual(row["sources"]["2026-10-05"], "manual")
        self.assertEqual(row["automatic_codes"]["2026-10-05"], "X")

        stale_status, stale = self.json_request(
            "/api/attendance",
            "PUT",
            {"month": "2026-10", "revision": attendance["revision"], "overrides": {}},
        )
        self.assertEqual(stale_status, 409)
        self.assertIn("başka bir pencerede", stale["error"])
        self.assertTrue(saved["revision"])

    def test_manual_deduction_is_persisted_and_exported_to_template_currency_cell(self):
        person = self.add_person()
        _, attendance = self.json_request("/api/attendance?month=2026-10")
        status, saved = self.json_request(
            "/api/attendance",
            "PUT",
            {
                "month": "2026-10",
                "revision": attendance["revision"],
                "overrides": {},
                "deductions": {str(person["id"]): 125.5},
            },
        )
        self.assertEqual(status, 200)

        _, current = self.json_request("/api/attendance?month=2026-10")
        row = current["attendance"]["rows"][0]
        self.assertEqual(row["deduction_amount"], 125.5)
        self.assertEqual(current["deductions"], {str(person["id"]): 125.5})

        status, _, body = self.request("/api/export?kind=attendance&month=2026-10")
        self.assertEqual(status, 200)
        worksheet = load_workbook(BytesIO(body)).active
        self.assertEqual(worksheet["AJ4"].value, 125.5)
        template = load_workbook(ROOT / "templates" / "attendance.xlsx").active
        self.assertEqual(worksheet["AJ4"].number_format, template["AK4"].number_format)

        status, legacy_save = self.json_request(
            "/api/attendance",
            "PUT",
            {
                "month": "2026-10",
                "revision": saved["revision"],
                "overrides": {"2026-10-05": {str(person["id"]): "R"}},
            },
        )
        self.assertEqual(status, 200)
        _, after_legacy_save = self.json_request("/api/attendance?month=2026-10")
        self.assertEqual(after_legacy_save["deductions"], {str(person["id"]): 125.5})

    def test_invalid_manual_deduction_is_rejected(self):
        person = self.add_person()
        _, attendance = self.json_request("/api/attendance?month=2026-10")
        status, result = self.json_request(
            "/api/attendance",
            "PUT",
            {
                "month": "2026-10",
                "revision": attendance["revision"],
                "overrides": {},
                "deductions": {str(person["id"]): -1},
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("0 ile", result["error"])

    def test_adjacent_month_schedule_is_used_for_overnight_exit(self):
        person = self.add_person()
        _, november = self.json_request("/api/schedule?month=2026-11")
        entry = {
            "row_id": "october-night",
            "employee_id": person["id"],
            "section": "ÖSSE",
            "days": {"2026-10-31": {"kind": "work", "start": "22:00", "end": "02:00"}},
        }
        status, _ = self.json_request(
            "/api/schedule",
            "PUT",
            {"month": "2026-10", "revision": "", "entries": [entry]},
        )
        self.assertEqual(status, 200)
        status, data = self.json_request("/api/attendance?month=2026-11")
        self.assertEqual(status, 200)
        row = data["attendance"]["rows"][0]
        self.assertEqual(row["codes"]["2026-11-01"], "G.Ç")
        self.assertEqual(row["normal_hours"], 2.0)

    def test_same_employee_can_work_in_multiple_sections_without_double_counting(self):
        person = self.add_person()
        _, schedule = self.json_request("/api/schedule?month=2026-10")
        entries = [
            {
                "row_id": "market-shift",
                "employee_id": person["id"],
                "section": "MARKET",
                "days": {"2026-10-05": {"kind": "work", "start": "09:00", "end": "13:00"}},
            },
            {
                "row_id": "osse-shift",
                "employee_id": person["id"],
                "section": "ÖSSE",
                "days": {"2026-10-05": {"kind": "work", "start": "13:00", "end": "17:00"}},
            },
        ]
        status, _ = self.json_request(
            "/api/schedule",
            "PUT",
            {"month": "2026-10", "revision": schedule["revision"], "entries": entries},
        )
        self.assertEqual(status, 200)
        _, data = self.json_request("/api/attendance?month=2026-10")
        row = data["attendance"]["rows"][0]
        self.assertEqual(row["codes"]["2026-10-05"], "X")
        self.assertEqual(row["normal_hours"], 8.0)

    def test_schedule_events_map_to_attendance_codes_in_the_source_workbook(self):
        person = self.add_person()
        _, schedule = self.json_request("/api/schedule?month=2026-10")
        entry = {
            "row_id": "event-codes",
            "employee_id": person["id"],
            "section": "MARKET",
            "days": {
                "2026-10-05": {"kind": "marriage_leave"},
                "2026-10-06": {"kind": "quarantine"},
                "2026-10-07": {"kind": "half_day"},
                "2026-10-08": {"kind": "absent"},
                "2026-10-09": {"kind": "resignation"},
            },
        }
        status, _ = self.json_request(
            "/api/schedule",
            "PUT",
            {"month": "2026-10", "revision": schedule["revision"], "entries": [entry]},
        )
        self.assertEqual(status, 200)

        _, data = self.json_request("/api/attendance?month=2026-10")
        codes = data["attendance"]["rows"][0]["codes"]
        self.assertEqual(
            [codes[f"2026-10-{day:02d}"] for day in range(5, 10)],
            ["E", "K", "Y", "G", "İST."],
        )

        status, _, body = self.request("/api/export?kind=attendance&month=2026-10")
        self.assertEqual(status, 200)
        workbook = load_workbook(BytesIO(body))
        worksheet = workbook.active
        self.assertEqual([worksheet.cell(4, column).value for column in range(7, 12)], ["E", "K", "Y", "G", "İST."])

    def test_exports_retain_template_workbook_layout_and_styles(self):
        person = self.add_person()
        _, schedule = self.json_request("/api/schedule?month=2026-10")
        entry = {
            "row_id": "export-row",
            "employee_id": person["id"],
            "section": "MARKET",
            "days": {"2026-10-05": {"kind": "work", "start": "09:00", "end": "17:00"}},
        }
        self.assertEqual(
            self.json_request(
                "/api/schedule",
                "PUT",
                {"month": "2026-10", "revision": schedule["revision"], "entries": [entry]},
            )[0],
            200,
        )

        for kind in ("schedule", "attendance"):
            status, headers, body = self.request(f"/api/export?kind={kind}&month=2026-10")
            self.assertEqual(status, 200)
            self.assertIn("spreadsheetml", headers.get("Content-Type", ""))
            workbook = load_workbook(BytesIO(body), data_only=False)
            self.assertGreaterEqual(len(workbook.worksheets), 1)
            self.assertFalse(workbook.active.sheet_view.showGridLines)
            self.assertGreater(workbook.active.column_dimensions["B"].width, 0)
            theme = _theme_colors(workbook)
            black_fills = [
                (sheet.title, cell.coordinate)
                for sheet in workbook.worksheets
                for row in sheet.iter_rows()
                for cell in row
                if cell.fill.fill_type == "solid"
                and _css_color(cell.fill.fgColor, theme) == "#000000"
            ]
            self.assertEqual(black_fills, [])
            if kind == "schedule":
                schedule_sheet = workbook["EKİM 1"]
                self.assertIn("09:00", str(schedule_sheet["D6"].value))
                self.assertEqual(schedule_sheet["U6"].value, "=SUM(R6:T6)")
                self.assertGreater(schedule_sheet.row_dimensions[6].height, 0)
                self.assertGreater(len(workbook.worksheets), 1)
                self.assertGreater(len(workbook.active.merged_cells.ranges), 0)
            else:
                self.assertIn("PUANTAJ", workbook.active["A1"].value)
                self.assertEqual(workbook.active["G4"].value, "X")
                self.assertGreater(workbook.active["G4"].style_id, 0)

    def test_attendance_export_colors_weekdays_weekends_and_public_holidays(self):
        status, _, body = self.request("/api/export?kind=attendance&month=2026-10")
        self.assertEqual(status, 200)
        workbook = load_workbook(BytesIO(body), data_only=False)
        exported = workbook.active
        for day, category in ((1, "weekday"), (3, "weekend"), (28, "official"), (29, "official")):
            cell = exported.cell(3, 2 + day)
            self.assertEqual(cell.value.strftime("%Y-%m-%d"), f"2026-10-{day:02d}")
            self.assertEqual(
                cell.fill.fgColor.rgb[-6:],
                ATTENDANCE_CATEGORY_COLORS[category].lstrip("#"),
            )

    def test_attendance_lower_tables_sync_movements_and_legend(self):
        import scheduling

        def days(*items):
            return {day: {"kind": "work", "start": "07:00", "end": "15:00", "fill_color": "#FFC000"} for day in items}

        staff = [
            {"id": 1, "name": "ALİ", "active": True},
            {"id": 2, "name": "AYŞE", "active": True},
            {"id": 3, "name": "VELİ", "active": False},
        ]
        entries = [
            {"employee_id": 1, "days": {**days("2026-09-30", "2026-10-01", "2026-10-02"), "2026-10-03": {"kind": "annual_leave"}}},
            {"employee_id": 2, "days": {**days("2026-10-10", "2026-10-11"), "2026-10-12": {"kind": "custom", "custom_code": "EĞT"}}},
            {"employee_id": 3, "days": {**days("2026-09-29", "2026-10-05"), "2026-10-06": {"kind": "resignation"}}},
        ]
        attendance = scheduling.calculate_attendance("2026-10", entries, {}, staff)
        movements = {item["name"]: item for item in attendance["movements"]}
        self.assertEqual(set(movements), {"AYŞE", "VELİ"})
        self.assertEqual(movements["AYŞE"]["hire_date"], "2026-10-10")
        self.assertIsNone(movements["AYŞE"]["exit_date"])
        self.assertEqual(movements["VELİ"]["exit_date"], "2026-10-05")
        self.assertEqual(movements["VELİ"]["resignation_date"], "2026-10-06")
        legend = {item["code"]: item for item in attendance["legend"]}
        self.assertEqual(legend["X"]["count"], 5)
        self.assertEqual(legend["X"]["color"], "#FFC000")
        self.assertFalse(legend["EĞT"]["standard"])

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "puantaj.xlsx"
            scheduling.export_attendance_xlsx(output, "2026-10", attendance)
            ws = load_workbook(output).active
        rows = {ws.cell(row, 2).value: row for row in range(26, 36) if ws.cell(row, 2).value}
        self.assertEqual(ws.cell(rows["AYŞE"], 3).value.date().isoformat(), "2026-10-10")
        self.assertEqual(ws.cell(rows["VELİ"], 9).value.date().isoformat(), "2026-10-05")
        self.assertEqual(ws.cell(rows["VELİ"], 13).value.date().isoformat(), "2026-10-06")
        self.assertEqual(ws["E37"].value, "X")
        self.assertEqual(ws["F37"].value, 5)
        self.assertEqual(ws["E37"].fill.fgColor.rgb[-6:], "FFC000")
        self.assertEqual(ws["F38"].value, 1)
        self.assertEqual(ws["M38"].value, 1)
        self.assertEqual((ws["G42"].value, ws["L42"].value, ws["M42"].value), ("ÖZEL KODLAR", "EĞT", 1))
        self.assertEqual(ws["B42"].value, "GECEDEN ÇIKIŞ")
        self.assertIn("M37:N37", {str(item) for item in ws.merged_cells.ranges})

    def test_schedule_export_names_and_opens_to_selected_week(self):
        status, _, body = self.request("/api/export?kind=schedule&month=2026-11&week=3")
        self.assertEqual(status, 200)
        workbook = load_workbook(BytesIO(body), data_only=False)
        self.assertEqual(
            workbook.sheetnames,
            ["EKİM 5", "KASIM 1", "KASIM 2", "KASIM 3", "KASIM 4", "KASIM 5"],
        )
        self.assertEqual(workbook.active.title, "KASIM 3")

        invalid_status, _, _ = self.request("/api/export?kind=schedule&month=2026-11&week=6")
        self.assertEqual(invalid_status, 400)

    def test_weekly_source_template_cleanup_preserves_manager_row_on_all_sheets(self):
        workbook = load_workbook(ROOT / "templates" / "weekly_schedule.xlsx", data_only=False)
        self.assertEqual(len(workbook.worksheets), 5)
        for worksheet in workbook.worksheets:
            with self.subTest(sheet=worksheet.title):
                self.assertEqual(worksheet.max_row, 35)
                self.assertEqual(worksheet["N35"].value, "İSTASYON MÜDÜRÜ")

    def test_template_endpoint_exposes_workbook_geometry_and_formatting(self):
        status, schedule = self.json_request("/api/template?kind=schedule&sheet=1")
        self.assertEqual(status, 200)
        self.assertEqual(schedule["sheet_name"], "EKİM 1")
        self.assertEqual((len(schedule["rows"]), len(schedule["columns"])), (35, 22))
        self.assertEqual(schedule["cells"][34][13]["value"], "İSTASYON MÜDÜRÜ")
        self.assertEqual(schedule["zoom"], 55)
        self.assertEqual(schedule["print_scale"], 52)
        self.assertIn({"row": 2, "column": 4, "rowspan": 2, "colspan": 2}, schedule["merges"])
        self.assertTrue(schedule["styles"]["150"])
        self.assertEqual(schedule["cells"][1][3]["value"], "2026-10-05T00:00:00")
        self.assertIn("background-color:#FFFFFF", schedule["styles"]["26"])
        self.assertIn("color:#000000", schedule["styles"]["26"])
        self.assertFalse(any("background-color:#000000" in style for style in schedule["styles"].values()))

        status, attendance = self.json_request("/api/template?kind=attendance")
        self.assertEqual(status, 200)
        self.assertEqual(attendance["sheet_name"], "PUANTAJ EKİM 2026")
        self.assertEqual((len(attendance["rows"]), len(attendance["columns"])), (43, 36))
        self.assertEqual(attendance["zoom"], 100)
        self.assertIn({"row": 1, "column": 1, "rowspan": 1, "colspan": 34}, attendance["merges"])
        self.assertEqual(attendance["cells"][2][2]["rotation"], 90)
        self.assertIn("background-color:#FFFFFF", attendance["styles"]["10"])
        self.assertFalse(any("background-color:#000000" in style for style in attendance["styles"].values()))


    def test_backup_creation_verification_restore_and_safety_copy(self):
        first = self.add_person("İlk Personel")
        status, created = self.json_request("/api/backups", "POST", {})
        self.assertEqual(status, 201)
        backup_id = created["backup"]["id"]
        self.assertTrue(backup_id.startswith("manual-"))
        self.add_person("Sonradan Eklenen")

        status, verified = self.json_request(
            "/api/backups/verify", "POST", {"id": backup_id},
        )
        self.assertEqual(status, 200)
        self.assertTrue(verified["backup"]["valid"])
        self.assertEqual(verified["backup"]["contents"]["attachment_count"], 0)

        # Verification describes the selected snapshot, not today's settings.
        self.assertEqual(self.json_request("/api/ui-settings", "PUT", {"density": "compact"})[0], 200)
        _, latest = self.json_request("/api/backups", "POST", {})
        _, latest_info = self.json_request("/api/backups/verify", "POST", {"id": latest["backup"]["id"]})
        self.assertTrue(latest_info["backup"]["contents"]["ui_settings"])
        _, original_info = self.json_request("/api/backups/verify", "POST", {"id": backup_id})
        self.assertEqual(original_info["backup"]["contents"], verified["backup"]["contents"])

        status, restored = self.json_request(
            "/api/backups/restore", "POST", {"id": backup_id},
        )
        self.assertEqual(status, 200)
        self.assertEqual(restored["restored"]["id"], backup_id)
        self.assertTrue(restored["safety_backup"]["id"].startswith("before-restore-"))
        history = self.json_request("/api/history")[1]["history"]
        self.assertEqual(history[0]["action"], "backup.restore")
        self.assertEqual(history[0]["details"]["backup_id"], backup_id)
        people = self.json_request("/api/people")[1]["people"]
        self.assertEqual([(person["id"], person["name"]) for person in people],
                         [(first["id"], "İlk Personel")])
        backups = self.json_request("/api/backups")[1]["backups"]
        self.assertIn(restored["safety_backup"]["id"], [item["id"] for item in backups])

    def test_selected_backup_restores_nonempty_template_and_document_attachment(self):
        import document_workflows

        person = self.add_person("Sentetik belge personeli")
        template_directory = Path(self.temp.name) / "table_templates"
        template_directory.mkdir()
        template_path = template_directory / "custom-synthetic.xlsx"
        workbook = Workbook()
        workbook.active["A1"] = "Seçili yedek özel şablonu"
        workbook.save(template_path)
        template_bytes = template_path.read_bytes()
        attachment_bytes = b"%PDF-1.4\nsynthetic document attachment\n"
        document_id = "synthetic-document-98"
        attachment_id = "synthetic-attachment-98"
        timestamp = "2026-10-12T09:00:00+00:00"
        with app_server.Store(self.server.data_path) as store:
            document_workflows.initialize(store.connection)
            store.connection.execute(
                """INSERT INTO workflow_documents
                   (id,employee_id,person_name,exact_dates,code,template_id,filename,content_type,content,
                    status,source_revisions,app_version,code_revision,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,'draft','{}','synthetic','synthetic',?,?)""",
                (document_id, person["id"], "Sentetik belge personeli", "2026-10-12", "annual_leave",
                 "synthetic-template", "sentetik-izin.pdf", "application/pdf", attachment_bytes,
                 timestamp, timestamp),
            )
            store.connection.execute(
                """INSERT INTO workflow_attachments
                   (id,document_id,filename,content_type,content,sha256,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,'draft',?,?)""",
                (attachment_id, document_id, "ek-belge.pdf", "application/pdf", attachment_bytes,
                 hashlib.sha256(attachment_bytes).hexdigest(), timestamp, timestamp),
            )
            store.connection.commit()

        status, created = self.json_request("/api/backups", "POST", {})
        self.assertEqual(status, 201)
        selected_id = created["backup"]["id"]
        status, selected = self.json_request("/api/backups/verify", "POST", {"id": selected_id})
        self.assertEqual(status, 200)
        self.assertEqual(selected["backup"]["contents"]["attachment_count"], 1)
        self.assertEqual(selected["backup"]["template_count"], 1)

        with app_server.Store(self.server.data_path) as store, store.connection:
            store.connection.execute("DELETE FROM workflow_attachments WHERE id=?", (attachment_id,))
        template_path.write_bytes(b"later template version")
        status, later = self.json_request("/api/backups", "POST", {})
        self.assertEqual(status, 201)
        status, later_info = self.json_request("/api/backups/verify", "POST", {"id": later["backup"]["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(later_info["backup"]["contents"]["attachment_count"], 0)
        self.assertEqual(later_info["backup"]["template_count"], 1)

        status, restored = self.json_request("/api/backups/restore", "POST", {"id": selected_id})
        self.assertEqual(status, 200, restored)
        with app_server.Store(self.server.data_path) as store:
            attachment = store.connection.execute(
                "SELECT filename,content,sha256 FROM workflow_attachments WHERE id=?", (attachment_id,),
            ).fetchone()
        self.assertEqual(attachment["filename"], "ek-belge.pdf")
        self.assertEqual(bytes(attachment["content"]), attachment_bytes)
        self.assertEqual(attachment["sha256"], hashlib.sha256(attachment_bytes).hexdigest())
        self.assertEqual(template_path.read_bytes(), template_bytes)
        safety_status, safety_info = self.json_request(
            "/api/backups/verify", "POST", {"id": restored["safety_backup"]["id"]},
        )
        self.assertEqual(safety_status, 200)
        self.assertEqual(safety_info["backup"]["contents"]["attachment_count"], 0)
        self.assertEqual(safety_info["backup"]["template_count"], 1)

    def test_corrupt_backup_is_rejected_without_changing_live_data(self):
        person = self.add_person("Korunacak Personel")
        status, created = self.json_request("/api/backups", "POST", {})
        self.assertEqual(status, 201)
        backup_id = created["backup"]["id"]
        backup_path = Path(self.temp.name) / "backups" / backup_id
        backup_path.write_bytes(b"not a sqlite database")

        status, result = self.json_request(
            "/api/backups/restore", "POST", {"id": backup_id},
        )
        self.assertEqual(status, 400)
        self.assertIn("bozuk", result["error"])
        self.assertEqual(self.json_request("/api/people")[1]["people"][0]["id"],
                         person["id"])
        self.assertEqual(
            self.json_request("/api/backups/restore", "POST", {"id": "../data.sqlite3"})[0],
            400,
        )

    def test_first_daily_data_change_creates_automatic_backup(self):
        self.add_person("Otomatik yedek öncesi")
        backups = self.json_request("/api/backups")[1]["backups"]
        self.assertTrue(any(item["id"].startswith("automatic-") for item in backups))
        history = self.json_request("/api/history")[1]["history"]
        self.assertEqual(history[0]["action"], "employee.create")
        self.assertEqual(history[0]["actor"], "Yerel kullanıcı")
        self.assertEqual(history[0]["details"]["names"], ["Otomatik yedek öncesi"])
        self.assertEqual(self.json_request("/api/history?limit=501")[0], 400)

    def test_archive_filters_by_month_employee_section_and_record_type(self):
        person = self.add_person("Ayşe Yılmaz")
        for month, shift_day, shift_kind in (
            ("2026-10", "2026-10-05", "weekly_off"),
            ("2026-11", "2026-11-02", "annual_leave"),
        ):
            revision = self.json_request(f"/api/schedule?month={month}")[1]["revision"]
            status, _ = self.json_request(
                "/api/schedule", "PUT",
                {"month": month, "revision": revision, "entries": [{
                    "row_id": f"archive-{month}",
                    "employee_id": person["id"],
                    "section": "MARKET",
                    "slot": 1,
                    "days": {shift_day: {"kind": shift_kind}},
                }]},
            )
            self.assertEqual(status, 200)

        query = urlencode({
            "kind": "schedule",
            "month": "2026-10",
            "employee": "AYŞE",
            "section": "market",
            "type": "hafta tatili",
        })
        self.assertEqual(
            self.json_request(f"/api/archive/search?{query}")[1]["months"],
            ["2026-10"],
        )
        query = urlencode({"kind": "attendance", "employee": "Ayşe", "type": "izin"})
        self.assertEqual(
            self.json_request(f"/api/archive/search?{query}")[1]["months"],
            ["2026-11"],
        )
        query = urlencode({"kind": "schedule", "month": "2026-99"})
        self.assertEqual(self.json_request(f"/api/archive/search?{query}")[0], 400)

    def test_archive_export_filters_by_selected_employee_and_preserves_template(self):
        first = self.add_person("Ayşe Yılmaz")
        second = self.add_person("Mehmet Kaya")
        entries = [
            {
                "row_id": f"archive-export-{person['id']}",
                "employee_id": person["id"],
                "section": "MARKET",
                "slot": index,
                "days": {
                    f"2026-10-{index + 4:02d}": {
                        "kind": "work", "start": "09:00", "end": "17:00",
                    },
                },
            }
            for index, person in enumerate((first, second), 1)
        ]
        status, _ = self.json_request("/api/schedule", "PUT", {
            "month": "2026-10", "revision": "", "entries": entries,
        })
        self.assertEqual(status, 200)

        query = urlencode({
            "kind": "schedule", "month": "2026-10", "employee": "AYŞE YILMAZ",
        })
        status, _, body = self.request(f"/api/export?{query}")
        self.assertEqual(status, 200, body.decode("utf-8", errors="replace"))
        schedule = load_workbook(BytesIO(body))
        self.assertEqual(schedule.active["C6"].value, "Ayşe Yılmaz")
        self.assertIsNone(schedule.active["C7"].value)

        query = urlencode({
            "kind": "attendance", "month": "2026-10", "employee": "Ayşe Yılmaz",
        })
        status, _, body = self.request(f"/api/export?{query}")
        self.assertEqual(status, 200)
        attendance = load_workbook(BytesIO(body))
        self.assertEqual(attendance.active["B4"].value, "Ayşe Yılmaz")
        self.assertIsNone(attendance.active["B5"].value)
        self.assertEqual(
            self.request(f"/api/export?{urlencode({'kind': 'schedule', 'month': '2026-10', 'employee': 'Ayşe'})}")[0],
            400,
        )

        selected_path = Path(self.temp.name) / "secilen-personel.xlsx"
        api = DesktopAPI(self.base_url)

        class FakeWindow:
            def create_file_dialog(self, *args, **kwargs):
                return (str(selected_path),)

        api._window = FakeWindow()
        self.server.save_excel = api.save_excel
        status, result = self.json_request("/api/save-excel", "POST", {
            "kind": "schedule", "month": "2026-10", "week": 0, "employee": "Ayşe Yılmaz",
        })
        self.assertEqual(status, 200)
        self.assertEqual(result["path"], str(selected_path))
        self.assertEqual(load_workbook(selected_path).active["C6"].value, "Ayşe Yılmaz")

    def test_desktop_api_does_not_expose_window_to_pywebview(self):
        # pywebview walks every public attribute of js_api; a public native
        # window reference makes that walk hang and freezes the app on startup.
        api = DesktopAPI(self.base_url)
        api._window = object()
        public_objects = [
            name for name, value in vars(api).items()
            if not name.startswith("_") and not isinstance(value, (str, int, float, bool, type(None)))
        ]
        self.assertEqual(public_objects, [])

    def test_leave_entitlement_follows_seniority_and_age(self):
        from datetime import date

        from reports import leave_entitlement

        on = date(2026, 10, 1)
        self.assertEqual(leave_entitlement("2026-01-01", "", on)["days"], 0)
        self.assertEqual(leave_entitlement("2023-01-01", "", on)["days"], 14)
        self.assertEqual(leave_entitlement("2023-01-01", "1970-01-01", on)["days"], 20)
        self.assertEqual(leave_entitlement("2018-01-01", "", on)["days"], 20)
        self.assertEqual(leave_entitlement("2010-01-01", "", on)["days"], 26)
        self.assertIsNone(leave_entitlement("", "", on))

    def test_extra_tools_cover_limits_notes_profiles_swaps_and_month_package(self):
        import zipfile

        import scheduling

        first, second = self.add_person("Ayşe Yılmaz"), self.add_person("Mehmet Kaya")
        work = {"kind": "work", "start": "09:00", "end": "18:00"}
        days = {f"2026-10-{day:02d}": dict(work) for day in range(5, 11)}
        days["2026-10-05"]["note"] = "rapor getirecek"
        days["2026-10-14"] = {"kind": "work", "start": "22:00", "end": "08:00"}
        status, saved = self.json_request("/api/schedule", "PUT", {"month": "2026-10", "revision": "", "entries": [
            {"row_id": "a", "employee_id": first["id"], "section": "MARKET", "slot": 1, "days": days},
            {"row_id": "b", "employee_id": second["id"], "section": "MARKET", "slot": 2,
             "days": {"2026-10-05": {"kind": "weekly_off"}}},
        ]})
        self.assertEqual(status, 200)

        check = self.json_request("/api/reports/check?month=2026-10")[1]
        types = {item["type"] for item in check["items"] if item["employee_id"] == first["id"]}
        self.assertIn("legal_weekly", types)
        self.assertIn("legal_night", types)
        attendance = self.json_request("/api/attendance?month=2026-10")[1]["attendance"]
        row = next(item for item in attendance["rows"] if item["employee_id"] == first["id"])
        self.assertGreater(row["night_hours"], 0)

        status, _, content = self.request("/api/export?kind=schedule&month=2026-10")
        self.assertEqual(status, 200)
        comments = [cell.comment.text for sheet in load_workbook(BytesIO(content)).worksheets
                    for line in sheet.iter_rows() for cell in line if cell.comment]
        self.assertIn("rapor getirecek", comments)

        dashboard = self.json_request("/api/dashboard?date=2026-10-05")[1]
        self.assertEqual([item["name"] for item in dashboard["working"]], ["Ayşe Yılmaz"])
        self.assertEqual([item["name"] for item in dashboard["off"]], ["Mehmet Kaya"])

        status, _ = self.json_request("/api/employee-profile", "PUT", {"employee_id": first["id"], "profile": {
            "title": "Kasiyer", "phone": "0555", "hire_date": "2018-03-01", "birth_date": "",
            "notes": "", "rotation": ["weekly_off"],
        }})
        self.assertEqual(status, 200)
        card = self.json_request(f"/api/employee-profile?employee_id={first['id']}")[1]
        self.assertEqual(card["profile"]["title"], "Kasiyer")
        self.assertEqual(card["profile"]["rotation"], ["weekly_off"])
        self.assertEqual(card["balance"]["entitlement"]["days"], 20)
        self.assertIn("2026-10", card["months"])

        results = self.json_request("/api/search?q=ayse")[1]["results"]
        self.assertEqual([item["name"] for item in results], ["Ayşe Yılmaz"])

        swap = {"date": "2026-10-05", "first_id": first["id"], "second_id": second["id"],
                "reason": "Karşılıklı", "first_before": "09:00–18:00", "second_before": "Hafta tatili"}
        self.assertEqual(self.json_request("/api/swaps", "POST", swap)[0], 200)
        self.assertEqual(self.json_request("/api/swaps", "POST", dict(swap, second_id=first["id"]))[0], 400)
        self.assertEqual(self.json_request("/api/swaps")[1]["swaps"][0]["second_name"], "Mehmet Kaya")
        history = self.json_request("/api/history?limit=50&action=schedule.swap")[1]["history"]
        self.assertTrue(history)
        self.assertTrue(all(item["action"].startswith("schedule.swap") for item in history))

        try:
            status, saved_holidays = self.json_request("/api/holidays", "PUT", {"holidays": [
                {"date": "2026-10-13", "kind": "public", "name": "Yerel tatil"},
            ]})
            self.assertEqual(status, 200)
            self.assertEqual(scheduling.attendance_day_category(scheduling.date(2026, 10, 13)), "official")
            self.assertEqual(self.json_request("/api/holidays?year=2026")[1]["custom"], saved_holidays["custom"])
        finally:
            self.json_request("/api/holidays", "PUT", {"holidays": []})
        self.assertEqual(scheduling.attendance_day_category(scheduling.date(2026, 10, 13)), "weekday")

        mirror = Path(self.temp.name) / "mirror"
        status, result = self.json_request("/api/backup-mirror", "PUT", {"folder": str(mirror)})
        self.assertEqual(status, 200)
        self.assertEqual(self.json_request("/api/backup-mirror")[1]["folder"], str(mirror))

        status, event = self.json_request("/api/attendance-events", "POST", {
            "employee_id": second["id"], "kind": "annual_leave", "start": "2026-10-20", "end": "2026-10-21",
            "document": "", "notes": "",
        })
        self.assertEqual(status, 201)
        status, applied = self.json_request("/api/attendance-events/apply", "POST", {"id": event["event"]["id"]})
        self.assertEqual(status, 200)
        self.assertEqual(applied["applied_days"], 2)
        entries = self.json_request("/api/schedule?month=2026-10")[1]["entries"]
        second_row = next(item for item in entries if item["employee_id"] == second["id"])
        self.assertEqual(second_row["days"]["2026-10-20"]["kind"], "annual_leave")

        status, _, content = self.request("/api/reports/person-program?month=2026-10")
        self.assertEqual(status, 200)
        self.assertTrue(content.startswith(b"%PDF"))
        status, _, personal_program = self.request(
            f"/api/reports/person-program?month=2026-10&employee_id={first['id']}"
        )
        self.assertEqual(status, 200)
        self.assertTrue(personal_program.startswith(b"%PDF"))
        export_history = self.json_request("/api/history?limit=20&action=schedule.program_export")[1]["history"]
        self.assertTrue(export_history)
        self.assertEqual(export_history[0]["details"]["employee_id"], first["id"])
        self.assertIn("schedule_revision", export_history[0]["details"])
        status, headers, content = self.request("/api/reports/month-package?month=2026-10")
        self.assertEqual(status, 200)
        names = zipfile.ZipFile(BytesIO(content)).namelist()
        self.assertTrue(any(name.endswith(".xlsx") for name in names))
        self.assertTrue(any(name.endswith(".pdf") for name in names))
        self.assertIn("DONEM_KONTROLU_2026-10.txt", names)
        self.assertEqual(self.request("/api/reports/month-package?month=2027-03")[0], 400)

    def test_group_a_pdks_reconciliation_late_early_and_unknown_names(self):
        first, second = self.add_person("Ayşe Yılmaz"), self.add_person("Mehmet Can")
        self.assertEqual(self.json_request("/api/schedule", "PUT", {"month": "2026-10", "revision": "", "entries": [
            {"row_id": "a", "employee_id": first["id"], "section": "MARKET", "slot": 1, "days": {
                "2026-10-05": {"kind": "work", "start": "08:00", "end": "16:00"},
                "2026-10-06": {"kind": "work", "start": "22:00", "end": "06:00"},
                "2026-10-07": {"kind": "overnight_exit"},
                "2026-10-08": {"kind": "work", "start": "08:00", "end": "16:00"},
            }},
            {"row_id": "b", "employee_id": second["id"], "section": "MARKET", "slot": 2, "days": {
                "2026-10-05": {"kind": "weekly_off"},
            }},
        ]})[0], 200)
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["PDKS Raporu"])
        sheet.append(["Sicil", "Adı Soyadı", "Tarih", "Giriş Saati", "Çıkış Saati"])
        sheet.append([1, "AYSE YILMAZ", "05.10.2026", "08:17", "15:40"])
        sheet.append([1, "Ayşe Yılmaz", "06.10.2026", "21:58", "06:02"])
        sheet.append([2, "Mehmet Can", "05.10.2026", "09:00", "17:00"])
        sheet.append([3, "Bilinmeyen Kişi", "05.10.2026", "09:00", "17:00"])
        buffer = BytesIO()
        workbook.save(buffer)
        status, result = self.json_request("/api/pdks/import", "POST", {
            "month": "2026-10", "filename": "pdks.xlsx", "tolerance": 5,
            "content": base64.b64encode(buffer.getvalue()).decode(),
        })
        self.assertEqual(status, 200, result)
        kinds = {(item["type"], item["date"], item["employee_id"]) for item in result["items"]}
        self.assertIn(("late", "2026-10-05", first["id"]), kinds)
        self.assertIn(("early", "2026-10-05", first["id"]), kinds)
        self.assertIn(("no_show", "2026-10-08", first["id"]), kinds)
        self.assertIn(("unplanned", "2026-10-05", second["id"]), kinds)
        self.assertFalse(any(item["date"] in ("2026-10-06", "2026-10-07") for item in result["items"]))
        person = next(item for item in result["people"] if item["employee_id"] == first["id"])
        self.assertEqual((person["late_minutes"], person["early_minutes"]), (17, 20))
        self.assertEqual(result["unknown_names"], [{"name": "Bilinmeyen Kişi", "count": 1}])
        first_record = next(item for item in result["punch_records"] if item["date"] == "2026-10-05")
        self.assertEqual([item["time"] for item in first_record["punches"]], ["08:17", "15:40"])
        self.assertEqual(first_record["pairings"], [{"in": "08:17", "out": "15:40", "rows": [3]}])
        refreshed = self.json_request("/api/pdks?month=2026-10&tolerance=30")[1]
        self.assertEqual(refreshed["counts"]["late"], 0)
        self.assertEqual(refreshed["punch_records"], result["punch_records"])

        csv_body = "Personel;Tarih;Saat\nAyşe Yılmaz;2026-10-05;08:00\nAyşe Yılmaz;2026-10-05;16:00\n"
        status, result = self.json_request("/api/pdks/import", "POST", {
            "month": "2026-10", "filename": "pdks.csv", "tolerance": 5,
            "content": base64.b64encode(csv_body.encode("utf-8")).decode(),
        })
        self.assertEqual(status, 200, result)
        self.assertFalse(any(item["date"] == "2026-10-05" and item["employee_id"] == first["id"]
                             for item in result["items"]))
        self.assertEqual(
            [item["time"] for item in result["punch_records"][0]["punches"]],
            ["08:00", "16:00"],
        )
        self.assertEqual(result["punch_records"][0]["pairings"][0]["rows"], [2, 3])
        status, result = self.json_request("/api/pdks/import", "POST", {
            "month": "2026-10", "filename": "x.csv", "tolerance": 5,
            "content": base64.b64encode(b"a;b\n1;2\n").decode(),
        })
        self.assertEqual(status, 400)
        self.assertIn("başlık", result["error"])

    def test_group_a_published_plan_diff_overlap_block_and_approval_flow(self):
        first, second = self.add_person("Ayşe Yılmaz"), self.add_person("Mehmet Can")
        entries = [{"row_id": "a", "employee_id": first["id"], "section": "MARKET", "slot": 1,
                    "days": {"2026-10-05": {"kind": "work", "start": "08:00", "end": "16:00"}}}]
        revision = self.json_request("/api/schedule", "PUT",
                                     {"month": "2026-10", "revision": "", "entries": entries})[1]["revision"]
        self.assertIsNone(self.json_request("/api/schedule/plan-diff?month=2026-10")[1]["published"])
        _, preview = self.json_request("/api/schedule/plan-diff?month=2026-10")
        status, published = self.json_request("/api/schedule/publish", "POST", {
            "month": "2026-10", "revision": preview["revision"],
        })
        self.assertEqual(status, 200)
        self.assertTrue(published["published"]["at"])

        clash = entries + [{"row_id": "b", "employee_id": first["id"], "section": "ÖSSE", "slot": 1,
                            "days": {"2026-10-05": {"kind": "work", "start": "12:00", "end": "20:00"}}}]
        status, result = self.json_request("/api/schedule", "PUT",
                                           {"month": "2026-10", "revision": revision, "entries": clash})
        self.assertEqual(status, 400)
        self.assertIn("Çift kayıt", result["error"])
        self.assertIn("ÖSSE", result["error"])

        changed = [
            {**entries[0], "days": {"2026-10-05": {"kind": "work", "start": "10:00", "end": "18:00"},
                                    "2026-10-06": {"kind": "annual_leave"}}},
            {"row_id": "b", "employee_id": second["id"], "section": "ÖSSE", "slot": 1,
             "days": {"2026-10-05": {"kind": "work", "start": "12:00", "end": "20:00"}}},
        ]
        status, saved = self.json_request("/api/schedule", "PUT",
                                          {"month": "2026-10", "revision": revision, "entries": changed})
        self.assertEqual(status, 200, saved)
        diff = self.json_request("/api/schedule/plan-diff?month=2026-10")[1]
        changes = {(item["name"], item["date"]): (item["type"], item["before"], item["after"]) for item in diff["changes"]}
        self.assertEqual(changes[("Ayşe Yılmaz", "2026-10-05")], ("changed", "08:00–16:00", "10:00–18:00"))
        self.assertEqual(changes[("Ayşe Yılmaz", "2026-10-06")], ("added", "", "S"))
        self.assertEqual(changes[("Mehmet Can", "2026-10-05")][0], "added")

        status, result = self.json_request("/api/approvals", "POST",
                                           {"month": "2026-10", "step": "approved", "name": "Ali Veli"})
        self.assertEqual(status, 400)
        self.assertIn("Hazırlandı", result["error"])
        for step in ("prepared", "checked", "approved"):
            status, approval = self.json_request("/api/approvals", "POST",
                                                 {"month": "2026-10", "step": step, "name": "Ali Veli"})
            self.assertEqual(status, 200, approval)
        self.assertEqual(approval["current"], "approved")
        self.assertFalse(approval["stale"])
        self.assertTrue(all(step["name"] == "Ali Veli" and step["at"] for step in approval["steps"]))
        self.assertEqual(self.json_request("/api/attendance", "PUT", {
            "month": "2026-10", "revision": "", "overrides": {"2026-10-07": {str(first["id"]): "R"}},
        })[0], 200)
        approval = self.json_request("/api/approvals?month=2026-10")[1]
        self.assertTrue(approval["stale"])
        self.assertIn("sonradan değişti", approval["status"])
        status, approval = self.json_request("/api/approvals", "POST",
                                             {"month": "2026-10", "step": "prepared", "name": "Ali Veli"})
        self.assertEqual(status, 200)
        self.assertEqual(approval["current"], "prepared")
        self.assertFalse(approval["stale"])

    def test_group_b_labour_law_checks_and_ubgt_hours(self):
        first, second = self.add_person("Ayşe Yılmaz"), self.add_person("Mehmet Can")
        night = {"kind": "work", "start": "22:00", "end": "06:00"}
        day_shift = {"kind": "work", "start": "08:00", "end": "17:00"}
        self.assertEqual(self.json_request("/api/schedule", "PUT", {"month": "2026-05", "revision": "", "entries": [
            {"row_id": "a", "employee_id": first["id"], "section": "MARKET", "slot": 1, "days": {
                "2026-05-01": {"kind": "work", "start": "08:00", "end": "16:00"},
                "2026-05-04": night, "2026-05-11": night, "2026-05-18": night,
                "2026-05-19": {"kind": "work", "start": "14:00", "end": "22:00"},
                "2026-05-26": {"kind": "work", "start": "08:00", "end": "18:00"},
            }},
            {"row_id": "b", "employee_id": second["id"], "section": "MARKET", "slot": 2, "days": {
                f"2026-05-{day:02d}": day_shift for day in range(18, 32)
            }},
        ]})[0], 200)
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {
            "employee_id": first["id"], "profile": {"special_status": ["pregnant", "bogus"]},
        })[0], 200)
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {
            "employee_id": second["id"], "profile": {"birth_date": "2010-01-01"},
        })[0], 200)
        self.assertEqual(self.json_request("/api/compliance-settings", "PUT", {"balancing_weeks": 5})[0], 400)
        status, settings = self.json_request("/api/compliance-settings", "PUT", {"balancing_weeks": 2})
        self.assertEqual((status, settings["legal_rest_hours"], settings["max_night_weeks"]), (200, 11.0, 2))

        row = next(item for item in self.json_request("/api/attendance?month=2026-05")[1]["attendance"]["rows"]
                   if item["employee_id"] == first["id"])
        self.assertEqual(row["ubgt_days"], {"2026-05-01": 8.0, "2026-05-19": 14.0, "2026-05-26": 5.0})
        self.assertEqual(row["ubgt_hours"], 27.0)

        items = self.json_request("/api/reports/check?month=2026-05")[1]["items"]
        found = {(item["type"], item["employee_id"], item["date"]) for item in items}
        self.assertIn(("legal_rest", first["id"], "2026-05-19"), found)
        self.assertIn(("night_rotation", first["id"], "2026-05-18"), found)
        self.assertNotIn(("night_rotation", first["id"], "2026-05-11"), found)
        self.assertIn(("special_night", first["id"], "2026-05-04"), found)
        self.assertIn(("legal_weekly_rest", second["id"], "2026-05-18"), found)
        self.assertIn(("special_daily", second["id"], "2026-05-18"), found)
        self.assertIn(("special_overtime", second["id"], "2026-05-18"), found)
        self.assertIn(("balancing", second["id"], None), found)
        self.assertNotIn(("legal_weekly_rest", first["id"], "2026-05-18"), found)
        holiday = next(item for item in items if item["type"] == "holiday_work")
        self.assertIn("27 saat", holiday["message"])
        self.assertEqual(self.json_request(f"/api/employee-profile?employee_id={first['id']}")[1]["profile"]["special_status"],
                         ["pregnant"])


    def test_group_c_sgk_days_payroll_export_sick_reports_and_leave(self):
        first, second = self.add_person("Ayşe Bordro"), self.add_person("Can Yeni")
        shift = {"kind": "work", "start": "08:00", "end": "18:00"}
        self.assertEqual(self.json_request("/api/schedule", "PUT", {"month": "2026-06", "revision": "", "entries": [
            {"row_id": "a", "employee_id": first["id"], "section": "MARKET", "slot": 1, "days": {
                f"2026-06-{day:02d}": shift for day in range(8, 14)
            }},
        ]})[0], 200)
        _, attendance = self.json_request("/api/attendance?month=2026-06")
        self.assertEqual(self.json_request("/api/attendance", "PUT", {
            "month": "2026-06", "revision": attendance["revision"], "overrides": {
                "2026-06-02": {str(first["id"]): "R"}, "2026-06-03": {str(first["id"]): "R"},
                "2026-06-20": {str(first["id"]): "Ü"},
            }, "roster": {"added": [second["id"]]},
        })[0], 200)
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {"employee_id": first["id"], "profile": {
            "hire_date": "2020-01-01", "exit_date": "2019-01-01"}})[0], 400)
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {"employee_id": first["id"], "profile": {
            "hire_date": "2020-01-01", "payroll_code": "P001", "monthly_wage": 30000}})[0], 200)
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {"employee_id": second["id"], "profile": {
            "hire_date": "2026-06-15"}})[0], 200)

        status, data = self.json_request("/api/payroll/sgk?month=2026-06")
        self.assertEqual(status, 200)
        sgk = {row["employee_id"]: row for row in data["sgk"]}
        self.assertEqual((sgk[first["id"]]["sgk_days"], sgk[first["id"]]["missing_days"]), (27, 3))
        self.assertEqual(sgk[first["id"]]["missing_by_reason"], {"01": 2, "21": 1})
        self.assertEqual(sgk[first["id"]]["missing_code"], "12")
        self.assertEqual((sgk[second["id"]]["employed_days"], sgk[second["id"]]["sgk_days"]), (16, 16))
        overtime = next(row for row in data["overtime"] if row["employee_id"] == first["id"])
        # Bordro FM = puantajın günlük kuralı: 6 gün x (10 - 8) saat.
        self.assertEqual((overtime["overtime_hours"], overtime["worked_hours"]), (12.0, 60.0))
        self.assertEqual(sum(week["hours"] for week in overtime["weeks"]), 12.0)
        self.assertEqual(data["daily_limit_hours"], 8)

        status, _, content = self.request("/api/reports/payroll-program?month=2026-06&format=logo")
        self.assertEqual(status, 200)
        sheet = load_workbook(BytesIO(content)).active
        self.assertEqual(sheet["A1"].value, "Sicil No")
        line = next(row for row in sheet.iter_rows(min_row=2, values_only=True) if row[1] == "Ayşe Bordro")
        self.assertEqual((line[0], line[3], line[5], line[12], line[13]), ("P001", 27, "12", 60, 12))
        self.assertNotIn("%25", " ".join(str(cell.value) for cell in sheet[1]))

        # Mola kuralı ve kişiye özel günlük sınır FM'yi puantaj ve bordroda birlikte değiştirir.
        self.assertEqual(self.json_request("/api/overtime-settings", "PUT", {
            "weekly_limit_hours": 0, "week_start": 0, "daily_limit_hours": 8,
            "break_rules": [[7.5, 60], [4, 30], [4, 15]]})[0], 400)
        self.assertEqual(self.json_request("/api/overtime-settings", "PUT", {
            "weekly_limit_hours": 0, "week_start": 0, "daily_limit_hours": 8,
            "break_rules": [[7.5, 60], [4, 30]]})[0], 200)
        _, attendance = self.json_request("/api/attendance?month=2026-06")
        row = next(item for item in attendance["attendance"]["rows"] if item["employee_id"] == first["id"])
        self.assertEqual((row["overtime_hours"], row["break_hours"]), (6.0, 6.0))
        self.assertEqual(row["overtime_details"]["2026-06-08"],
                         {"worked": 10.0, "break": 1.0, "holiday": 0.0, "limit": 8.0, "overtime": 1.0})
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {"employee_id": first["id"], "profile": {
            "daily_limit_hours": 7.5, "break_exempt": True}})[0], 200)
        _, data = self.json_request("/api/payroll/sgk?month=2026-06")
        overtime = next(row for row in data["overtime"] if row["employee_id"] == first["id"])
        self.assertEqual(overtime["overtime_hours"], 15.0)
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {"employee_id": first["id"], "profile": {
            "daily_limit_hours": 30}})[0], 400)
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {"employee_id": first["id"], "profile": {
            "daily_limit_hours": 0, "break_exempt": False}})[0], 200)
        self.assertEqual(self.json_request("/api/overtime-settings", "PUT", {
            "weekly_limit_hours": 0, "week_start": 0, "daily_limit_hours": 8, "break_rules": []})[0], 200)
        status, _, content = self.request("/api/reports/payroll-program?month=2026-06&format=netsis")
        self.assertEqual(status, 200)
        text = content.decode("utf-8-sig")
        self.assertTrue(text.startswith("SICIL_NO;AD_SOYAD"))
        self.assertIn("P001;Ayşe Bordro;2026-06;27;3;12;", text)
        self.assertEqual(self.request("/api/reports/payroll-program?month=2026-06&format=bogus")[0], 400)

        self.assertEqual(self.json_request("/api/sick-reports", "POST", {"action": "add", "report": {
            "employee_id": first["id"], "start": "2026-06-03", "end": "2026-06-01"}})[0], 400)
        self.assertEqual(self.json_request("/api/sick-reports", "POST", {"action": "add", "report": {
            "employee_id": first["id"], "start": "2026-06-01", "end": "2026-06-03",
            "type": "illness", "report_no": "E-123"}})[0], 200)
        _, reports_data = self.json_request("/api/sick-reports?month=2026-06")
        report = reports_data["reports"][0]
        self.assertEqual((report["days_in_month"], report["sgk_paid_days"], report["employer_days"]), (3, 1, 2))
        self.assertEqual([(item["date"], item["employee_id"]) for item in reports_data["mismatches"]],
                         [("2026-06-01", first["id"])])
        self.assertEqual(self.json_request("/api/sick-reports", "POST", {"action": "delete", "id": report["id"]})[0], 200)
        self.assertEqual(self.json_request("/api/sick-reports?month=2026-06")[1]["reports"], [])

        self.assertEqual(self.json_request("/api/balances", "PUT", {
            "year": 2025, "employee_id": first["id"], "annual_leave_opening_days": 10, "overtime_opening_hours": 0,
        })[0], 200)
        status, carried = self.json_request("/api/leave/carry-over", "POST", {"year": 2025, "include_entitlement": True})
        self.assertEqual(status, 200)
        self.assertEqual(carried["done"], [{"name": "Ayşe Bordro", "carried": 10.0, "earned": 20, "opening": 30.0}])
        again = self.json_request("/api/leave/carry-over", "POST", {"year": 2025, "include_entitlement": True})[1]
        self.assertEqual(again["done"], [])
        status, pay = self.json_request(f"/api/leave/termination?employee_id={first['id']}&exit_date=2026-06-30")
        self.assertEqual(status, 200)
        self.assertEqual((pay["remaining_days"], pay["daily_wage"], pay["amount"]), (30.0, 1000.0, 30000.0))

    def test_group_d_fairness_preferences_skills_leave_requests_borrowed_and_import(self):
        first, second, third = self.add_person("Ayşe Plan"), self.add_person("Bora Gece"), self.add_person("Cem Kasa")
        night = {"kind": "work", "start": "22:00", "end": "06:00"}
        day_shift = {"kind": "work", "start": "08:00", "end": "16:00"}
        self.assertEqual(self.json_request("/api/schedule", "PUT", {"month": "2026-06", "revision": "", "entries": [
            {"row_id": "a", "employee_id": first["id"], "section": "MARKET", "slot": 1, "days": {
                "2026-06-01": day_shift, "2026-06-02": day_shift, "2026-06-06": day_shift}},
            {"row_id": "b", "employee_id": second["id"], "section": "MARKET", "slot": 2, "days": {
                f"2026-06-{day:02d}": night for day in range(1, 8)}},
            {"row_id": "c", "employee_id": first["id"], "section": "ÖSSE", "slot": 1, "days": {
                "2026-06-03": day_shift}},
            {"row_id": "d", "employee_id": third["id"], "section": "ÖSSE", "slot": 2, "days": {
                "2026-06-04": day_shift}},
        ]})[0], 200)
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {"employee_id": first["id"], "profile": {
            "preferences": {"unavailable_weekdays": [9]}}})[0], 400)
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {"employee_id": first["id"], "profile": {
            "home_section": "MARKET", "skills": "Kasa, Forklift, kasa",
            "preferences": {"unavailable_weekdays": [1], "no_weekend": True, "note": "Okul"}}})[0], 200)
        profile = self.json_request(f"/api/employee-profile?employee_id={first['id']}")[1]["profile"]
        self.assertEqual(profile["skills"], ["Kasa", "Forklift"])
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {"employee_id": first["id"], "profile": {
            "phone": "555"}})[0], 200)
        profile = self.json_request(f"/api/employee-profile?employee_id={first['id']}")[1]["profile"]
        self.assertEqual((profile["phone"], profile["home_section"]), ("555", "MARKET"))
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {"employee_id": third["id"], "profile": {
            "preferences": {"unavailable_ranges": [{"start": "2026-06-04", "end": "2026-06-04"}]}}})[0], 200)
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {"employee_id": third["id"], "profile": {
            "preferences": {"unavailable_ranges": [{"start": "2026-06-05", "end": "2026-06-04"}]}}})[0], 400)
        preference_report = self.json_request("/api/reports/check?month=2026-06")[1]
        date_conflict = next(item for item in preference_report["items"]
                             if item["type"] == "preference" and item["employee_id"] == third["id"])
        self.assertIn("tarih aralığında çalışmaya uygun değil", date_conflict["message"])

        fair = self.json_request("/api/planning/fairness?from=2026-06&to=2026-06")[1]
        people = {row["employee_id"]: row for row in fair["people"]}
        self.assertEqual((people[second["id"]]["shifts"], people[second["id"]]["night"], people[second["id"]]["weekend"]),
                         (7, 7, 2))
        self.assertEqual(fair["people"][0]["employee_id"], second["id"])
        self.assertIn("night", people[second["id"]]["flags"])
        self.assertEqual(self.json_request("/api/planning/fairness?from=2026-01&to=2027-06")[0], 400)

        self.assertEqual(self.json_request("/api/planning-settings", "PUT", {"daily_limit": 1, "skill_requirements": [
            {"skill": "Kasa", "section": "ÖSSE"}]})[0], 200)
        _, settings = self.json_request("/api/planning-settings")
        self.assertEqual(settings["daily_limit"], 1)
        self.assertIn("Forklift", settings["known_skills"])

        for employee, status_value in ((second, "pending"), (third, "approved")):
            self.assertEqual(self.json_request("/api/leave-requests", "POST", {"action": "add", "request": {
                "employee_id": employee["id"], "start": "2026-06-04", "end": "2026-06-05",
                "code": "S", "status": status_value}})[0], 200)
        self.assertEqual(self.json_request("/api/leave-requests", "POST", {"action": "add", "request": {
            "employee_id": first["id"], "start": "2026-06-05", "end": "2026-06-01"}})[0], 400)
        calendar_data = self.json_request("/api/leave-requests?month=2026-06")[1]
        crowded = {day["date"]: day["count"] for day in calendar_data["days"] if day["over"]}
        self.assertEqual(crowded, {"2026-06-04": 2, "2026-06-05": 2})

        items = self.json_request("/api/reports/check?month=2026-06")[1]["items"]
        found = {(item["type"], item["employee_id"], item["date"]) for item in items}
        self.assertIn(("preference", first["id"], "2026-06-02"), found)
        self.assertIn(("preference", first["id"], "2026-06-06"), found)
        self.assertNotIn(("preference", first["id"], "2026-06-01"), found)
        self.assertIn(("skill_coverage", None, "2026-06-04"), found)
        self.assertNotIn(("skill_coverage", None, "2026-06-03"), found)
        self.assertIn(("leave_request_crowd", None, "2026-06-04"), found)
        self.assertIn(("leave_request_conflict", third["id"], "2026-06-04"), found)
        self.assertNotIn(("leave_request_conflict", second["id"], "2026-06-04"), found)

        pending = next(item for item in calendar_data["requests"] if item["employee_id"] == second["id"])
        self.assertEqual(self.json_request("/api/leave-requests", "POST", {
            "action": "status", "id": pending["id"], "status": "rejected"})[0], 200)
        calendar_data = self.json_request("/api/leave-requests?month=2026-06")[1]
        self.assertFalse(any(day["over"] for day in calendar_data["days"]))

        borrowed = self.json_request("/api/planning/borrowed?month=2026-06")[1]["people"]
        self.assertEqual([(row["employee_id"], row["home_section"], row["borrowed_days"]) for row in borrowed],
                         [(first["id"], "MARKET", 1)])

        workbook = Workbook()
        sheet = workbook.active
        sheet["B3"] = "ADI SOYADI"
        for day in range(1, 31):
            sheet.cell(3, day + 2, day)
        sheet["B4"], sheet["C4"], sheet["D4"], sheet["E4"] = "AYŞE PLAN", "x", "S", "zz!"
        sheet["B5"], sheet["C5"] = "Bilinmeyen Kişi", "X"
        buffer = BytesIO()
        workbook.save(buffer)
        payload = {"month": "2026-06", "filename": "eski.xlsx",
                   "content": base64.b64encode(buffer.getvalue()).decode(), "apply": False}
        status, preview = self.json_request("/api/import/attendance-excel", "POST", payload)
        self.assertEqual(status, 200, preview)
        self.assertEqual((preview["header_row"], preview["name_column"], preview["code_count"]), (3, 2, 2))
        self.assertEqual(preview["overrides"], {"2026-06-01": {str(first["id"]): "X"},
                                                "2026-06-02": {str(first["id"]): "S"}})
        self.assertEqual([item["name"] for item in preview["unknown"]], ["Bilinmeyen Kişi"])
        self.assertEqual([item["day"] for item in preview["invalid"]], [3])
        self.assertFalse(preview["applied"])
        status, applied = self.json_request("/api/import/attendance-excel", "POST",
                                            payload | {"apply": True, "revision": preview["revision"]})
        self.assertEqual(status, 200, applied)
        overrides = self.json_request("/api/attendance?month=2026-06")[1]["overrides"]
        self.assertEqual(overrides["2026-06-02"][str(first["id"])], "S")
        self.assertEqual(self.json_request("/api/import/attendance-excel", "POST",
                                           payload | {"apply": True, "revision": "stale"})[0], 409)

    def test_group_e_manager_summary_absence_patterns_cost_and_year_comparison(self):
        first, second = self.add_person("Ayşe Analiz"), self.add_person("Bora Maliyet")
        a, b = str(first["id"]), str(second["id"])
        overrides = {}
        for day, code in (("01", "R"), ("02", "X"), ("03", "X"), ("04", "X"), ("05", "G"), ("08", "R"), ("10", "R")):
            overrides.setdefault(f"2026-06-{day}", {})[a] = code
        for day, code in (("01", "X"), ("02", "X"), ("03", "X"), ("17", "Ü")):
            overrides.setdefault(f"2026-06-{day}", {})[b] = code
        for month, values in (("2026-06", overrides), ("2025-06", {"2025-06-02": {a: "X"}})):
            status, body = self.json_request("/api/attendance", "PUT", {
                "month": month, "revision": "", "overrides": values, "deductions": {}})
            self.assertEqual(status, 200, body)
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {"employee_id": first["id"], "profile": {
            "monthly_wage": 30000}})[0], 200)

        status, data = self.json_request("/api/analytics?month=2026-06")
        self.assertEqual(status, 200, data)
        metrics = data["metrics"]
        self.assertEqual((metrics["people"], metrics["work_days"], metrics["sick_days"], metrics["absence_days"],
                          metrics["unpaid_days"]), (2, 6, 3, 1, 1))
        self.assertEqual(metrics["absence_rate"], 45.5)
        self.assertEqual((metrics["calendar_days"], metrics["normal_weekdays"]), (30, 22))
        self.assertEqual(data["previous"]["calendar_days"], 31)
        self.assertLess(data["last_year"]["normal_weekdays"], metrics["normal_weekdays"])
        self.assertEqual((data["previous_month"], data["last_year_month"]), ("2026-05", "2025-06"))
        self.assertEqual(data["compare_previous"], {})
        self.assertEqual((data["last_year"]["work_days"], data["compare_last_year"]["work_days"]), (1, 5))
        report_context = data["report_context"]
        self.assertTrue(report_context["calculated_at"])
        self.assertIn("2026-06", report_context["source_revisions"])
        self.assertTrue(report_context["has_attendance_records"])
        self.assertIn("Vardiya planı kaydı yok", report_context["missing_sources"])
        self.assertIn("PDKS aktarımı yok (isteğe bağlı kaynak)", report_context["missing_sources"])

        pattern = next(item for item in data["patterns"] if item["employee_id"] == first["id"])
        self.assertEqual((pattern["absence_days"], pattern["pattern_days"], pattern["flagged"]), (4, 3, True))
        self.assertEqual([hit["reason"] for hit in pattern["hits"]], ["Pazartesi", "Cuma", "Pazartesi"])
        self.assertFalse(any(item["employee_id"] == second["id"] for item in data["patterns"]))

        cost = data["cost"]
        self.assertEqual(cost["missing_wage"], ["Bora Maliyet"])
        person = cost["people"][0]
        self.assertEqual(person["hourly_wage"], round(30000 / 225, 2))
        self.assertEqual(person["base"], round(30000 * person["sgk_days"] / 30, 2))
        self.assertAlmostEqual(person["total"], person["base"] + person["overtime"] + person["ubgt"],
                               places=1)
        self.assertEqual(self.json_request("/api/analytics?month=2026-06&hours=100")[0], 400)
        self.assertEqual(self.json_request("/api/analytics?month=2026-06&hours=x")[0], 400)

        status, headers, content = self.request("/api/reports/manager-summary?month=2026-06")
        self.assertEqual(status, 200)
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertEqual(len(re.findall(rb"/Type\s*/Page(?!s)", content)), 1)

    def test_manager_analytics_chart_details_link_to_person_day_and_period_revisions(self):
        person = self.add_person("Grafik Kaynak Personeli")
        entry = {
            "row_id": "analytics-source",
            "employee_id": person["id"],
            "section": "MARKET",
            "slot": 1,
            "days": {"2026-06-02": {"kind": "work", "start": "09:00", "end": "17:00"}},
        }
        status, saved_schedule = self.json_request("/api/schedule", "PUT", {
            "month": "2026-06", "revision": "", "entries": [entry],
        })
        self.assertEqual(status, 200, saved_schedule)
        status, saved_attendance = self.json_request("/api/attendance", "PUT", {
            "month": "2026-06", "revision": "", "overrides": {
                "2026-06-03": {str(person["id"]): "G"},
            }, "deductions": {},
        })
        self.assertEqual(status, 200, saved_attendance)

        status, worked = self.json_request(
            "/api/analytics/details?month=2026-06&metric=worked_hours",
        )
        self.assertEqual(status, 200, worked)
        self.assertEqual(worked["revisions"], {
            "schedule": saved_schedule["revision"], "attendance": saved_attendance["revision"],
        })
        self.assertEqual([(item["name"], item["date"], item["value"], item["source"])
                          for item in worked["items"]],
                         [("Grafik Kaynak Personeli", "2026-06-02", 8.0, "schedule-calculation")])

        status, absence = self.json_request(
            "/api/analytics/details?month=2026-06&metric=absence_days",
        )
        self.assertEqual(status, 200, absence)
        self.assertEqual([(item["employee_id"], item["date"], item["code"], item["source"])
                          for item in absence["items"]],
                         [(person["id"], "2026-06-03", "G", "manual")])

        self.assertEqual(self.json_request(
            "/api/analytics/details?month=2026-06&metric=monthly_wage",
        )[0], 400)
        status, _, script = self.request("/controls.js")
        self.assertEqual(status, 200)
        self.assertIn(b"data-analytics-metric", script)
        self.assertIn(b"/api/analytics/details", script)

    def test_manager_manual_overtime_chart_detail_keeps_unknown_date_period_wide(self):
        person = self.add_person("Dönem Mesaisi Personeli")
        month = date.today().strftime("%Y-%m")
        day = date.today().isoformat()
        status, saved = self.json_request("/api/attendance", "PUT", {
            "month": month, "revision": "", "overrides": {
                day: {str(person["id"]): "G"},
            }, "deductions": {}, "overtime": {str(person["id"]): 3.5},
        })
        self.assertEqual(status, 200, saved)

        status, details = self.json_request(
            f"/api/analytics/details?month={month}&metric=overtime_hours",
        )
        self.assertEqual(status, 200, details)
        self.assertEqual(
            [(item["employee_id"], item["date"], item["value"], item["source"])
             for item in details["items"]],
            [(person["id"], None, 3.5, "manual-period-total")],
        )

    def test_manager_chart_notes_reopen_only_for_their_report_version(self):
        from storage import Store

        person = self.add_person("Grafik Açıklama Personeli")
        status, saved = self.json_request("/api/attendance", "PUT", {
            "month": "2026-06", "revision": "", "overrides": {
                "2026-06-02": {str(person["id"]): "X"},
            }, "deductions": {},
        })
        self.assertEqual(status, 200, saved)

        status, report = self.json_request("/api/analytics?month=2026-06")
        self.assertEqual(status, 200, report)
        first_version = report["report_context"]["report_version"]
        self.assertRegex(first_version, r"^manager-v1-[0-9a-f]{24}$")
        self.assertEqual(report["chart_notes"], {})

        note_text = "Bu hafta bakım nedeniyle ek vardiya vardı."
        status, saved_note = self.json_request("/api/analytics/chart-note", "POST", {
            "month": "2026-06", "report_version": first_version,
            "chart_key": "worked_hours", "text": note_text,
        })
        self.assertEqual(status, 200, (first_version, saved_note))
        self.assertEqual(saved_note["note"]["text"], note_text)

        status, reopened = self.json_request("/api/analytics?month=2026-06")
        self.assertEqual(status, 200, reopened)
        self.assertEqual(reopened["report_context"]["report_version"], first_version)
        self.assertEqual(reopened["chart_notes"]["worked_hours"]["text"], note_text)

        status, changed = self.json_request("/api/attendance", "PUT", {
            "month": "2026-06", "revision": saved["revision"], "overrides": {
                "2026-06-02": {str(person["id"]): "X"},
                "2026-06-03": {str(person["id"]): "G"},
            }, "deductions": {},
        })
        self.assertEqual(status, 200, changed)
        status, newer_report = self.json_request("/api/analytics?month=2026-06")
        self.assertEqual(status, 200, newer_report)
        newer_version = newer_report["report_context"]["report_version"]
        self.assertNotEqual(newer_version, first_version)
        self.assertEqual(newer_report["chart_notes"], {})

        status, stale = self.json_request("/api/analytics/chart-note", "POST", {
            "month": "2026-06", "report_version": first_version,
            "chart_key": "worked_hours", "text": "Eski rapora ait açıklama.",
        })
        self.assertEqual(status, 409, stale)
        self.assertEqual(stale["report_version"], newer_version)
        with Store(self.server.data_path) as store:
            self.assertEqual(
                store.manager_chart_notes("2026-06", first_version)["worked_hours"]["text"],
                note_text,
            )

        self.assertEqual(self.json_request("/api/analytics/chart-note", "POST", {
            "month": "2026-06", "report_version": newer_version,
            "chart_key": "monthly_wage", "text": "Geçersiz grafik",
        })[0], 400)

    def test_group_f_person_color_tag_and_photo(self):
        person = self.add_person("Renkli Personel")
        other = self.add_person("Etiketsiz Personel")
        buffer = BytesIO()
        Image.new("RGB", (4, 4), "#12b76a").save(buffer, "PNG")
        photo = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

        def save(profile):
            return self.json_request("/api/employee-profile", "PUT", {"employee_id": person["id"], "profile": profile})

        self.assertEqual(save({"color_tag": "red"})[0], 400)
        self.assertEqual(save({"color_tag": "#12345"})[0], 400)
        self.assertEqual(save({"photo": "data:image/gif;base64,R0lGOD"})[0], 400)
        self.assertEqual(save({"photo": "data:image/png;base64," + base64.b64encode(b"not an image").decode()})[0], 400)
        self.assertEqual(save({"photo": "data:image/png;base64," + "A" * 200_004})[0], 400)

        status, body = save({"title": "Kasiyer", "color_tag": "#12B76A", "photo": photo})
        self.assertEqual(status, 200, body)
        self.assertEqual((body["profile"]["color_tag"], body["profile"]["photo"]), ("#12b76a", photo))
        status, body = save({"phone": "555"})
        self.assertEqual((body["profile"]["color_tag"], body["profile"]["photo"], body["profile"]["title"]),
                         ("#12b76a", photo, "Kasiyer"))

        status, data = self.json_request("/api/person-tags")
        self.assertEqual(status, 200)
        self.assertEqual(data["tags"], {str(person["id"]): {"color": "#12b76a", "photo": photo}})
        self.assertNotIn(str(other["id"]), data["tags"])

        self.assertEqual(save({"color_tag": "", "photo": ""})[0], 200)
        self.assertEqual(self.json_request("/api/person-tags")[1]["tags"], {})

    def cookie_request(self, path, method="GET", body=None, cookie="", headers=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(self.base_url + path, data=data, method=method,
                          headers={"Content-Type": "application/json", "Cookie": cookie, **(headers or {})})
        try:
            with urlopen(request) as response:
                raw, status, set_cookie = response.read(), response.status, response.headers.get("Set-Cookie", "")
        except HTTPError as error:
            raw, status, set_cookie = error.read(), error.code, error.headers.get("Set-Cookie", "")
            error.close()
        token = set_cookie.split(";", 1)[0] if set_cookie else ""
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except ValueError:
            parsed = raw
        return status, parsed, token

    def test_group_g_users_roles_and_session_gate(self):
        self.add_person("Kapı Personeli")
        status, data, _ = self.cookie_request("/api/auth/status")
        self.assertEqual((status, data["enabled"], data["user"]), (200, False, None))
        self.assertEqual(self.cookie_request("/api/users", "PUT", {"username": "izleyici", "password": "123456", "role": "viewer"})[0], 400)
        self.assertEqual(self.cookie_request("/api/users", "PUT", {"username": "yonetici", "password": "123", "role": "admin"})[0], 400)
        status, data, admin_cookie = self.cookie_request("/api/users", "PUT", {"username": "Yönetici", "password": "gizli123", "role": "admin"})
        self.assertEqual(status, 200, data)
        self.assertTrue(admin_cookie.startswith("vp_session="))

        # Oturumsuz istekler kapıdan geçemez; sayfa ve ortam bilgisi açık kalır.
        status, data, _ = self.cookie_request("/api/people")
        self.assertEqual((status, data["auth_required"]), (401, True))
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.cookie_request("/api/environment")[0], 200)
        self.assertEqual(self.cookie_request("/api/auth/login", "POST", {"username": "Yönetici", "password": "yanlis"})[0], 400)

        self.assertEqual(self.cookie_request("/api/people", cookie=admin_cookie)[0], 200)
        status, data, _ = self.cookie_request("/api/users", "PUT", {"username": "izleyici", "password": "okur123", "role": "viewer"}, cookie=admin_cookie)
        self.assertEqual([item["username"] for item in data["users"]], ["Yönetici", "izleyici"])
        self.assertNotIn("hash", json.dumps(data))

        status, data, viewer_cookie = self.cookie_request("/api/auth/login", "POST", {"username": "IZLEYICI".lower(), "password": "okur123"})
        self.assertEqual((status, data["user"]["role"]), (200, "viewer"))
        self.assertEqual(self.cookie_request("/api/people", cookie=viewer_cookie)[0], 200)
        status, data, _ = self.cookie_request("/api/people", "POST", {"name": "Yeni"}, cookie=viewer_cookie)
        self.assertEqual(status, 403, data)
        self.assertEqual(self.cookie_request("/api/users", cookie=viewer_cookie)[0], 403)
        status, _, _ = self.cookie_request("/api/reports/check?month=2026-10", cookie=viewer_cookie)
        self.assertEqual(status, 200)

        # Yönetici değişikliği geçmişe kendi adıyla düşer.
        status, data, _ = self.cookie_request("/api/people", "POST", {"name": "Yönetici Ekledi"}, cookie=admin_cookie)
        self.assertEqual(status, 201, data)
        history = self.cookie_request("/api/history", cookie=admin_cookie)[1]
        entries = history.get("history", history.get("entries", history)) if isinstance(history, dict) else history
        self.assertIn("Yönetici", json.dumps(entries, ensure_ascii=False))

        # Masaüstü Excel kaydı iç anahtarla kapıyı geçer.
        token = {"X-VP-Internal": self.server.internal_token}
        self.assertEqual(self.cookie_request("/api/export?kind=attendance&month=2026-10&week=0", headers=token)[0], 200)
        self.assertEqual(self.cookie_request("/api/export?kind=attendance&month=2026-10&week=0", headers={"X-VP-Internal": "yanlis"})[0], 401)

        self.assertEqual(self.cookie_request("/api/users/delete", "POST", {"username": "Yönetici"}, cookie=admin_cookie)[0], 400)
        status, data, _ = self.cookie_request("/api/users/delete", "POST", {"username": "izleyici"}, cookie=admin_cookie)
        self.assertEqual((status, len(data["users"])), (200, 1))
        self.assertEqual(self.cookie_request("/api/people", cookie=viewer_cookie)[0], 401)
        status, data, cleared = self.cookie_request("/api/users/delete", "POST", {"username": "Yönetici"}, cookie=admin_cookie)
        self.assertEqual((status, data["users"]), (200, []))
        self.assertIn("vp_session=", cleared)
        self.assertEqual(self.cookie_request("/api/people")[0], 200)

    def test_group_g_reset_users_file_disables_passwords(self):
        self.assertEqual(self.cookie_request("/api/users", "PUT", {"username": "admin", "password": "parola1", "role": "admin"})[0], 200)
        data_path = Path(self.temp.name) / "data.sqlite3"
        (data_path.parent / "reset_users.txt").write_text("", encoding="utf-8")
        second = create_server(data_path, ROOT / "web")
        second.server_close()
        self.assertFalse((data_path.parent / "reset_users.txt").exists())
        self.server.users_cache = None
        self.assertEqual(self.cookie_request("/api/auth/status")[1]["enabled"], False)

    def test_group_g_shared_folder_lock_blocks_foreign_writer(self):
        import time as _time
        lock_path = Path(self.temp.name) / "data.lock"
        status, data, _ = self.cookie_request("/api/lock")
        self.assertEqual((status, data["read_only"]), (200, False))
        self.assertTrue(lock_path.exists())
        lock_path.write_text(json.dumps({"host": "BASKA-PC", "user": "mehmet", "pid": 1, "heartbeat": _time.time()}), encoding="utf-8")
        status, data, _ = self.cookie_request("/api/people", "POST", {"name": "Kilitli"})
        self.assertEqual(status, 423)
        self.assertEqual(data["holder"]["user"], "mehmet")
        self.assertIn("mehmet", data["error"])
        self.assertEqual(self.cookie_request("/api/people")[0], 200)
        self.assertTrue(self.cookie_request("/api/lock")[1]["read_only"])

        # Süresi dolmuş kilit kendiliğinden devralınır.
        lock_path.write_text(json.dumps({"host": "BASKA-PC", "user": "mehmet", "pid": 1, "heartbeat": _time.time() - 600}), encoding="utf-8")
        self.assertEqual(self.cookie_request("/api/people", "POST", {"name": "Serbest"})[0], 201)
        lock_path.write_text(json.dumps({"host": "BASKA-PC", "user": "mehmet", "pid": 1, "heartbeat": _time.time()}), encoding="utf-8")
        status, data, _ = self.cookie_request("/api/lock/take-over", "POST", {})
        self.assertEqual((status, data.get("read_only")), (200, False), data)
        self.assertEqual(self.cookie_request("/api/people", "POST", {"name": "Devraldı"})[0], 201)
        # Aynı bilgisayarda kapanmış bir kopyanın taze kilidi beklemeden bırakılır.
        import socket as _socket
        lock_path.write_text(json.dumps({"host": _socket.gethostname(), "user": "eski", "pid": 2_000_000_000, "heartbeat": _time.time()}), encoding="utf-8")
        self.assertFalse(self.cookie_request("/api/lock")[1]["read_only"])
        self.server.lock.release()
        self.assertFalse(lock_path.exists())

    def test_group_g_mail_draft_builds_eml_with_attachment(self):
        from email import policy
        from email.parser import BytesParser
        self.add_person("Posta Personeli")
        opened = []
        self.server.open_file = opened.append
        self.assertEqual(self.cookie_request("/api/mail-draft", "POST", {"kind": "virus", "month": "2026-10"})[0], 400)
        self.assertEqual(self.cookie_request("/api/mail-draft", "POST", {"kind": "attendance", "month": "2026-10", "to": "yanlis-adres"})[0], 400)
        status, data, _ = self.cookie_request("/api/mail-draft", "POST", {
            "kind": "attendance", "month": "2026-10", "to": "ik@ornek.com; muhasebe@ornek.com", "subject": "", "body": "",
        })
        self.assertEqual(status, 200, data)
        self.assertEqual(len(opened), 1)
        path = Path(data["path"])
        try:
            message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
            self.assertEqual(message["X-Unsent"], "1")
            self.assertEqual(message["To"], "ik@ornek.com, muhasebe@ornek.com")
            self.assertIn("EKİM 2026", message["Subject"])
            attachments = list(message.iter_attachments())
            self.assertEqual(attachments[0].get_filename(), "PUANTAJ_2026-10.xlsx")
            workbook = load_workbook(BytesIO(attachments[0].get_content()))
            self.assertTrue(workbook.sheetnames)
        finally:
            path.unlink(missing_ok=True)


    def test_schedule_sections_grow_rows_and_add_custom_block_to_export(self):
        status, data = self.json_request("/api/schedule-sections")
        self.assertEqual(status, 200)
        self.assertEqual([(item["name"], item["rows"], item["capacity"]) for item in data["sections"]],
                         [("MARKET", 6, 9), ("TEMİZLİK", 1, 4), ("ÖSSE", 11, 14)])
        bad = [{"name": "TEMİZLİK", "rows": 1}, {"name": "MARKET", "rows": 6}, {"name": "ÖSSE", "rows": 11}]
        self.assertEqual(self.json_request("/api/schedule-sections", "PUT", {"sections": bad})[0], 400)
        too_few = [{"name": "MARKET", "rows": 5}, {"name": "TEMİZLİK", "rows": 1}, {"name": "ÖSSE", "rows": 11}]
        self.assertEqual(self.json_request("/api/schedule-sections", "PUT", {"sections": too_few})[0], 400)
        sections = [{"name": "MARKET", "rows": 8}, {"name": "TEMİZLİK", "rows": 1},
                    {"name": "ÖSSE", "rows": 11}, {"name": "kasa", "rows": 2}]
        status, data = self.json_request("/api/schedule-sections", "PUT", {"sections": sections})
        self.assertEqual(status, 200)
        self.assertEqual(data["sections"][3]["name"], "KASA")
        self.assertEqual(data["sections"][0]["capacity"], 11)
        self.assertTrue(data["sections"][3]["custom"])

        status, result = self.json_request("/api/people/bulk", "POST",
                                           {"names": [f"Marketçi {index}" for index in range(1, 9)] + ["Kasiyer"]})
        self.assertEqual(status, 201)
        people = result["people"]
        day = {"2026-11-01": {"kind": "work", "start": "08:00", "end": "16:00"}}
        entries = [{"row_id": f"m{slot}", "employee_id": person["id"], "section": "MARKET", "slot": slot, "days": day}
                   for slot, person in enumerate(people[:8], start=1)]
        entries.append({"row_id": "k1", "employee_id": people[8]["id"], "section": "KASA", "slot": 1, "days": day})
        status, _ = self.json_request("/api/schedule", "PUT", {"month": "2026-11", "revision": "", "entries": entries})
        self.assertEqual(status, 200)

        # Shrinking MARKET or deleting KASA would orphan saved rows, so it is refused.
        status, result = self.json_request("/api/schedule-sections", "PUT", {"sections": sections[:3]})
        self.assertEqual(status, 400)
        self.assertIn("KASA", result["error"])

        status, _, body = self.request("/api/export?kind=schedule&month=2026-11")
        self.assertEqual(status, 200)
        sheet = load_workbook(BytesIO(body)).worksheets[0]
        self.assertEqual([sheet[f"C{row}"].value for row in range(6, 14)], [person["name"] for person in people[:8]])
        self.assertEqual(sheet["B13"].value, 8)
        self.assertEqual(sheet["C15"].value, "TEMİZLİK")
        kasa_header = next(row for row in range(30, sheet.max_row + 1) if sheet[f"C{row}"].value == "KASA")
        header_merges = {str(item) for item in sheet.merged_cells.ranges if item.min_row == kasa_header}
        self.assertTrue(header_merges)
        data_row = kasa_header + 4
        self.assertEqual(sheet[f"B{data_row}"].value, 1)
        self.assertEqual(sheet[f"C{data_row}"].value, "Kasiyer")
        self.assertEqual(sheet[f"P{data_row}"].value.hour, 8)
        self.assertEqual(sheet[f"U{data_row}"].value, f"=SUM(R{data_row}:T{data_row})")
        self.assertIsNone(sheet[f"B{data_row + 1}"].value)
        self.assertIsNone(sheet[f"C{data_row + 1}"].value)
        self.assertEqual(sheet[f"C{data_row + 1}"].border.bottom.style, sheet["C19"].border.bottom.style)
        self.assertEqual(sheet.row_dimensions[data_row].height, sheet.row_dimensions[19].height)
        print_area = sheet.print_area if isinstance(sheet.print_area, str) else ",".join(sheet.print_area)
        self.assertGreater(int(print_area.rsplit("$", 1)[-1]), data_row + 1)

        _, attendance = self.json_request("/api/attendance?month=2026-11")
        codes = {row["name"]: row["codes"].get("2026-11-01") for row in attendance["attendance"]["rows"]}
        self.assertEqual(codes["Kasiyer"], "X")
        self.assertEqual(codes["Marketçi 8"], "X")

    def test_daily_overtime_holiday_column_and_manual_overtime_any_month(self):
        person = self.add_person("Mesai Personeli")
        entry = {
            "row_id": "fm-row", "employee_id": person["id"], "section": "MARKET", "slot": 1,
            "days": {
                "2026-05-01": {"kind": "work", "start": "08:00", "end": "16:00"},
                "2026-05-04": {"kind": "work", "start": "08:00", "end": "18:00"},
                "2026-05-05": {"kind": "work", "start": "08:00", "end": "16:00"},
            },
        }
        status, _ = self.json_request("/api/schedule", "PUT", {"month": "2026-05", "revision": "", "entries": [entry]})
        self.assertEqual(status, 200)
        _, data = self.json_request("/api/attendance?month=2026-05")
        row = next(item for item in data["attendance"]["rows"] if item["employee_id"] == person["id"])
        self.assertEqual(row["holiday_hours"], 8)
        self.assertEqual(row["overtime_hours"], 2)
        self.assertEqual(row["overtime_computed_hours"], 2)
        self.assertFalse(row["overtime_manual"])

        _, schedule = self.json_request("/api/schedule?month=2026-05")
        weeks = schedule["week_totals"][str(person["id"])]
        self.assertEqual(weeks["2026-05-04"][0], 2)

        status, _ = self.json_request("/api/attendance", "PUT", {
            "month": "2026-05", "revision": data["revision"], "overrides": {},
            "overtime": {str(person["id"]): 3.5}, "deductions": {str(person["id"]): 150},
        })
        self.assertEqual(status, 200)
        _, data = self.json_request("/api/attendance?month=2026-05")
        row = next(item for item in data["attendance"]["rows"] if item["employee_id"] == person["id"])
        self.assertEqual(row["overtime_hours"], 3.5)
        self.assertEqual(row["overtime_computed_hours"], 2)
        self.assertTrue(row["overtime_manual"])
        for invalid in ({str(person["id"]): -1}, {str(person["id"]): True}, {"99999": 1}):
            status, _ = self.json_request("/api/attendance", "PUT", {
                "month": "2026-05", "revision": data["revision"], "overrides": {}, "overtime": invalid})
            self.assertEqual(status, 400)

        status, _, body = self.request("/api/export?kind=attendance&month=2026-05")
        self.assertEqual(status, 200)
        sheet = load_workbook(BytesIO(body)).worksheets[0]
        headers = [str(sheet.cell(3, col).value or "") for col in range(1, sheet.max_column + 1)]
        self.assertFalse(any("EKİM" in header.upper() for header in headers))
        self.assertIn("RESMİ TATİL MESAİ", headers)
        self.assertIn("FAZLA MESAİ", headers)
        person_row = next(r for r in range(4, sheet.max_row + 1)
                          if any(sheet.cell(r, c).value == "Mesai Personeli" for c in range(1, 4)))
        values = {headers[c - 1]: sheet.cell(person_row, c).value for c in range(1, sheet.max_column + 1)}
        self.assertEqual(values["RESMİ TATİL MESAİ"], 8)
        self.assertEqual(values["FAZLA MESAİ"], 3.5)

    def test_attendance_has_no_normal_hours_column(self):
        person = self.add_person("Kolon Personeli")
        _, data = self.json_request("/api/attendance?month=2026-10")
        self.json_request("/api/attendance", "PUT", {
            "month": "2026-10", "revision": data["revision"], "overrides": {},
            "deductions": {str(person["id"]): 50}})
        status, _, body = self.request("/api/export?kind=attendance&month=2026-10")
        self.assertEqual(status, 200)
        sheet = load_workbook(BytesIO(body)).active
        original = load_workbook(ROOT / "templates" / "attendance.xlsx").active
        self.assertEqual(sheet.max_column, 36)
        self.assertEqual([sheet.cell(3, col).value for col in range(34, 37)],
                         ["RESMİ TATİL MESAİ", "FAZLA MESAİ", "KESİNTİ"])
        self.assertFalse(any("NORMAL" in str(sheet.cell(3, col).value or "").upper()
                             for col in range(1, sheet.max_column + 1)))
        self.assertEqual(sheet["AJ4"].value, 50)
        for letter, source in (("AH", "AI"), ("AI", "AJ"), ("AJ", "AK")):
            self.assertAlmostEqual(sheet.column_dimensions[letter].width,
                                   original.column_dimensions[source].width)
            self.assertEqual((sheet[f"{letter}3"].border.left.style, sheet[f"{letter}3"].font.b), (original[f"{source}3"].border.left.style, original[f"{source}3"].font.b))
        self.assertIn("A1:AH1", {str(item) for item in sheet.merged_cells.ranges})
        template = self.json_request("/api/template?kind=attendance")[1]
        self.assertEqual(len(template["columns"]), 36)

        status, _, content = self.request("/api/table-templates/download?kind=attendance")
        self.assertEqual(status, 200)
        downloaded = load_workbook(BytesIO(content))
        self.assertEqual(downloaded.active.max_column, 36)
        downloaded.active["B44"] = None
        status, _ = self.upload_table_template("attendance", downloaded)
        self.assertEqual(status, 200)
        status, _ = self.upload_table_template("attendance", load_workbook(ROOT / "templates" / "attendance.xlsx"))
        self.assertEqual(status, 200)
        status, _, body = self.request("/api/export?kind=attendance&month=2026-10")
        self.assertEqual(load_workbook(BytesIO(body)).active.max_column, 36)

    def test_attendance_roster_nonshift_added_removed_and_passive(self):
        scheduled = self.add_person("Vardiyalı Kişi")
        manager = self.add_person("Müdür Bey")
        driver = self.add_person("Tanker Şoförü")
        passive = self.add_person("Pasif Kişi")
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {"employee_id": manager["id"], "profile": {
            "staff_type": "nonshift", "fixed_pattern": ["X", "X", "X", "X", "X", "H", "H"],
            "fixed_start": "09:00", "fixed_end": "18:00"}})[0], 200)
        self.assertEqual(self.json_request("/api/employee-profile", "PUT", {"employee_id": manager["id"], "profile": {
            "staff_type": "nonshift", "fixed_pattern": ["Z"] * 7}})[0], 400)
        self.assertEqual(self.json_request(f"/api/people/{passive['id']}/active", "POST", {"active": False})[0], 200)
        status, _ = self.json_request("/api/schedule", "PUT", {"month": "2026-11", "revision": "", "entries": [{
            "row_id": "r1", "employee_id": scheduled["id"], "section": "MARKET", "slot": 1,
            "days": {"2026-11-02": {"kind": "work", "start": "08:00", "end": "16:00"}}}]})
        self.assertEqual(status, 200)

        _, data = self.json_request("/api/attendance?month=2026-11")
        names = [row["name"] for row in data["attendance"]["rows"]]
        self.assertIn("Vardiyalı Kişi", names)
        self.assertIn("Müdür Bey", names)
        self.assertNotIn("Tanker Şoförü", names)
        self.assertNotIn("Pasif Kişi", names)
        manager_row = next(row for row in data["attendance"]["rows"] if row["name"] == "Müdür Bey")
        self.assertEqual(manager_row["codes"]["2026-11-02"], "X")
        self.assertEqual(manager_row["codes"]["2026-11-07"], "H")
        self.assertEqual(data["roster_sources"][str(scheduled["id"])], "schedule")

        status, _ = self.json_request("/api/attendance", "PUT", {
            "month": "2026-11", "revision": data["revision"], "overrides": {},
            "roster": {"added": [driver["id"]], "removed": [manager["id"], scheduled["id"]]}})
        self.assertEqual(status, 200)
        _, data = self.json_request("/api/attendance?month=2026-11")
        names = [row["name"] for row in data["attendance"]["rows"]]
        self.assertIn("Tanker Şoförü", names)
        self.assertIn("Vardiyalı Kişi", names)
        self.assertNotIn("Müdür Bey", names)
        self.assertEqual(self.json_request("/api/attendance", "PUT", {
            "month": "2026-11", "revision": data["revision"], "overrides": {}, "roster": {"added": [123456]}})[0], 400)
        self.assertEqual(self.json_request("/api/attendance", "PUT", {
            "month": "2026-11", "revision": data["revision"], "overrides": {}, "roster": {"bogus": []}})[0], 400)

    def test_delete_person_requires_force_when_records_exist(self):
        empty = self.add_person("Kayıtsız Kişi")
        busy = self.add_person("Kayıtlı Kişi")
        status, _ = self.json_request(f"/api/people/{empty['id']}/delete", "POST", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.json_request("/api/schedule", "PUT", {"month": "2026-11", "revision": "", "entries": [{
            "row_id": "r1", "employee_id": busy["id"], "section": "MARKET", "slot": 1,
            "days": {"2026-11-02": {"kind": "work", "start": "08:00", "end": "16:00"}}}]})[0], 200)
        status, result = self.json_request(f"/api/people/{busy['id']}/delete", "POST", {})
        self.assertEqual(status, 409)
        self.assertTrue(result["has_records"])
        self.assertEqual(result["records"]["schedule_months"], 1)
        self.assertEqual(self.json_request(f"/api/people/{busy['id']}/delete", "POST", {"force": "yes"})[0], 400)
        status, _ = self.json_request(f"/api/people/{busy['id']}/delete", "POST", {"force": True})
        self.assertEqual(status, 200)
        _, schedule = self.json_request("/api/schedule?month=2026-11")
        self.assertEqual(schedule["entries"], [])
        _, people = self.json_request("/api/people")
        names = {person["name"] for person in people["people"]}
        self.assertFalse({"Kayıtsız Kişi", "Kayıtlı Kişi"} & names)

    def test_schedule_export_writes_weekly_overtime_totals(self):
        person = self.add_person("Uzun Vardiya")
        days = {f"2026-11-0{day}": {"kind": "work", "start": "08:00", "end": "18:00"} for day in range(2, 5)}
        self.assertEqual(self.json_request("/api/schedule", "PUT", {"month": "2026-11", "revision": "", "entries": [{
            "row_id": "r1", "employee_id": person["id"], "section": "MARKET", "slot": 1, "days": days}]})[0], 200)
        status, _, body = self.request("/api/export?kind=schedule&month=2026-11")
        self.assertEqual(status, 200)
        workbook = load_workbook(BytesIO(body))
        week_sheet, next_sheet = workbook.worksheets[1], workbook.worksheets[2]
        row = next(r for r in range(6, 12) if week_sheet.cell(r, 3).value == "Uzun Vardiya")
        self.assertEqual(week_sheet[f"R{row}"].value, 6)
        self.assertEqual(week_sheet[f"T{row}"].value, 0)
        self.assertEqual(week_sheet[f"U{row}"].value, f"=SUM(R{row}:T{row})")
        next_row = next(r for r in range(6, 12) if next_sheet.cell(r, 3).value == "Uzun Vardiya")
        self.assertEqual(next_sheet[f"R{next_row}"].value, 0)
        self.assertEqual(next_sheet[f"T{next_row}"].value, 6)

    def test_temporary_assignments_preview_conflicts_and_recheck_before_atomic_save(self):
        person = self.add_person("Geçici Görev Personeli")
        route = "/api/operations/temporary-assignments/preview"
        schedule_route = "/api/schedule"
        month = "2026-11"
        entry = {
            "row_id": "temporary-assignment-test",
            "employee_id": person["id"],
            "section": "MARKET",
            "slot": 1,
            "days": {"2026-11-02": {"kind": "work", "start": "09:00", "end": "17:00"}},
        }
        status, saved_schedule = self.json_request(schedule_route, "PUT", {
            "month": month, "revision": "", "entries": [entry],
        })
        self.assertEqual(status, 200, saved_schedule)
        assignment = {
            "employee_id": person["id"], "location_type": "section", "location": "TEMİZLİK",
            "start_date": "2026-11-02", "end_date": "2026-11-02",
            "start_time": "10:00", "end_time": "12:00", "note": "",
        }
        status, conflict = self.json_request(route, "POST", {"action": "preview", "assignment": assignment})
        self.assertEqual(status, 200, conflict)
        self.assertEqual(len(conflict["conflicts"]), 1)
        self.assertEqual((conflict["conflicts"][0]["scheduled_section"],
                          conflict["conflicts"][0]["start"], conflict["conflicts"][0]["end"]),
                         ("MARKET", "10:00", "12:00"))
        status, rejected = self.json_request(route, "POST", {
            "action": "save", "assignment": assignment, "preview": conflict["preview"],
        })
        self.assertEqual(status, 400, rejected)
        self.assertEqual(self.json_request("/api/operations/temporary-assignments?month=2026-11")[1]["items"], [])

        valid = {**assignment, "start_date": "2026-11-03", "end_date": "2026-11-03",
                 "start_time": "20:00", "end_time": "21:00"}
        status, preview = self.json_request(route, "POST", {"action": "preview", "assignment": valid})
        self.assertEqual(status, 200, preview)
        self.assertEqual(preview["conflicts"], [])
        changed_entry = {**entry, "days": {
            **entry["days"], "2026-11-03": {"kind": "work", "start": "20:30", "end": "22:00"},
        }}
        status, changed = self.json_request(schedule_route, "PUT", {
            "month": month, "revision": saved_schedule["revision"], "entries": [changed_entry],
        })
        self.assertEqual(status, 200, changed)
        status, stale = self.json_request(route, "POST", {
            "action": "save", "assignment": valid, "preview": preview["preview"],
        })
        self.assertEqual(status, 400, stale)
        self.assertIn("eskidi", stale["error"])
        status, current = self.json_request(route, "POST", {"action": "preview", "assignment": valid})
        self.assertEqual(status, 200, current)
        self.assertEqual(current["conflicts"][0]["start"], "20:30")

        valid["start_time"], valid["end_time"] = "23:00", "23:30"
        status, clear = self.json_request(route, "POST", {"action": "preview", "assignment": valid})
        self.assertEqual(status, 200, clear)
        self.assertEqual(clear["conflicts"], [])
        status, created = self.json_request(route, "POST", {
            "action": "save", "assignment": valid, "preview": clear["preview"],
        })
        self.assertEqual(status, 201, created)
        competing = {**valid, "location_type": "branch", "location": "Şube A",
                     "start_time": "23:15", "end_time": "23:45"}
        status, duplicate = self.json_request(route, "POST", {"action": "preview", "assignment": competing})
        self.assertEqual(status, 200, duplicate)
        self.assertEqual(len(duplicate["conflicts"]), 1)
        self.assertEqual(duplicate["conflicts"][0]["start"], "23:15")
        listed = self.json_request("/api/operations/temporary-assignments?month=2026-11")[1]["items"]
        self.assertEqual(len(listed), 1)
        self.assertEqual((listed[0]["employee_name"], listed[0]["location"], listed[0]["start_time"]),
                         ("Geçici Görev Personeli", "TEMİZLİK", "23:00"))


if __name__ == "__main__":
    unittest.main()
