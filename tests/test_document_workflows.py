import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from io import BytesIO
from hashlib import sha256
from urllib.error import HTTPError
from urllib.request import urlopen
from datetime import datetime, timedelta, timezone
import secrets
from types import SimpleNamespace
from unittest import mock

from docx import Document
from openpyxl import Workbook, load_workbook
from pypdf import PdfReader, PdfWriter
from PIL import Image

import document_workflows
import scheduling
from workspace_tools import PhoneViewServer


class WorkflowStore:
    def __init__(self, path=None):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.people_data = [
            {"id": 1, "name": "Ayşe Yılmaz", "active": True},
            {"id": 2, "name": "Mehmet Demir", "active": True},
        ]
        self.settings = {}
        self.history = []
        self.path = Path(path or ".")
        self.connection.execute(
            "CREATE TABLE form_templates (id TEXT PRIMARY KEY, name TEXT, category TEXT, extension TEXT, content BLOB)"
        )
        self.connection.commit()
        document_workflows.initialize(self.connection)

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        return False

    def people(self):
        return list(self.people_data)

    def users_enabled(self):
        return False

    def _setting(self, name, default):
        return self.settings.get(name, default)

    def overtime_settings(self, month=None):
        return {"daily_limit_hours": 8, "break_rules": [{"after_hours": 6, "break_hours": 0.5}]}

    def employee_profiles(self):
        return {}

    def record_change(self, action, summary, details):
        self.history.append((action, summary, details))

    def attendance_month(self, month):
        overrides = {"2026-03-02": {"1": "S"}} if month == "2026-03" else {}
        return {"overrides": overrides, "revision": ""}

    def _backup_path(self, backup_id):
        if backup_id != "manual-20260301T000000Z-deadbeef.sqlite3":
            raise ValueError("Invalid backup id")
        return self.path / backup_id


class WorkflowHandler:
    def __init__(self, store):
        self.store = store
        self.command = "GET"
        self.current_user = None
        self.response = None
        self.status = None
        self.headers = {}
        self.body = BytesIO()

    def _store(self):
        return self.store

    def _month_revisions(self, store, month):
        return {"schedule": "schedule-r1", "attendance": "attendance-r1"}

    def _balance_summary(self, store, month):
        return {
            "year": 2026,
            "through_month": 3,
            "people": [
                {"employee_id": 1, "name": "Ayşe Yılmaz", "annual_leave_opening_days": 6.0,
                 "annual_leave_used_days": 1.5, "annual_leave_remaining_days": 4.5},
                {"employee_id": 2, "name": "Mehmet Demir", "annual_leave_remaining_days": 2.0},
            ],
        }

    def _attendance_for(self, store, month):
        row = {
            "employee_id": 1,
            "codes": {"2026-03-02": "S", "2026-03-03": "R"},
            "sources": {"2026-03-02": "manual", "2026-03-03": "schedule"},
            "overtime_details": {
                "2026-03-02": {"worked": 8, "break": 0.5, "holiday": 0, "limit": 8, "overtime": 0.5},
                "2026-03-03": {"worked": 9, "break": 1, "holiday": 2, "limit": 8, "overtime": 1},
            },
            "overtime_detail_minutes": {
                "2026-03-02": {"worked": 480, "break": 30, "holiday": 0, "limit": 480, "overtime": 30},
                "2026-03-03": {"worked": 540, "break": 60, "holiday": 120, "limit": 480, "overtime": 60},
            },
            "worked_hours": 17,
            "holiday_hours": 2,
            "overtime_computed_hours": 1.5,
            "overtime_hours": 2.0,
            "overtime_manual": True,
            "daily_limit_hours": 8,
        }
        return (
            {"dates": ["2026-03-02", "2026-03-03"], "rows": [row]},
            [],
            {"revision": "attendance-r1"},
        )

    def _json(self, value, status=200):
        self.response = value
        self.status = status

    def _read_json(self, maximum=1_000_000):
        return {}

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass

    def wfile_write(self, value):
        self.body.write(value)

    @property
    def wfile(self):
        return self.body


class DocumentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = WorkflowStore(self.temporary.name)
        self.addCleanup(self.store.connection.close)
        self.handler = WorkflowHandler(self.store)
        document = Document()
        document.add_paragraph("{{personel}} · {{tarih}} · {{durum}}")
        output = BytesIO()
        document.save(output)
        self.store.connection.execute(
            "INSERT INTO form_templates VALUES (?,?,?,?,?)",
            ("absence-form", "Devamsızlık tutanağı", "G", "docx", output.getvalue()),
        )
        self.store.connection.execute(
            "INSERT INTO form_templates VALUES (?,?,?,?,?)",
            ("leave-form", "İzin formu", "S", "docx", output.getvalue()),
        )
        self.store.connection.commit()

    def generate_batch(self, cells=None):
        selected_cells = cells or [
            {"employee_id": 1, "date": "2026-03-02", "code": "G"},
            {"employee_id": 1, "date": "2026-03-05", "code": "S"},
            {"employee_id": 2, "date": "2026-03-02", "code": "G"},
        ]
        return document_workflows.create_documents_batch(
            self.handler,
            self.store,
            {
                "cells": selected_cells,
                "templates": {
                    code: {"G": "absence-form", "S": "leave-form"}[code]
                    for code in {cell["code"] for cell in selected_cells}
                    if code in ("G", "S")
                },
                "section": "Satış",
                "description": "Seçilen gün",
            },
        )

    def test_selection_preserves_disjoint_days_and_separate_employees(self):
        cells = document_workflows.normalize_selection([
            {"employee_id": 1, "date": "2026-03-02", "code": "G"},
            {"employee_id": 1, "date": "2026-03-05", "code": "S"},
            {"employee_id": 2, "date": "2026-03-02", "code": "G"},
        ])
        groups = document_workflows.selection_groups(cells)
        self.assertEqual([item["date"] for item in groups[1]], ["2026-03-02", "2026-03-05"])
        self.assertEqual([item["date"] for item in groups[2]], ["2026-03-02"])
        self.assertEqual(len(self.generate_batch()), 3)
        rows = self.store.connection.execute(
            "SELECT employee_id, exact_dates, status FROM workflow_documents ORDER BY created_at, employee_id"
        ).fetchall()
        self.assertEqual({row["employee_id"] for row in rows}, {1, 2})
        self.assertTrue(all(json.loads(row["exact_dates"]) in [["2026-03-02"], ["2026-03-05"]] for row in rows))
        self.assertTrue(all(row["status"] == "draft" for row in rows))
        with self.assertRaisesRegex(ValueError, "birden fazla puantaj kodu"):
            document_workflows.normalize_selection([
                {"employee_id": 1, "date": "2026-03-02", "code": "G"},
                {"employee_id": 1, "date": "2026-03-02", "code": "S"},
            ])

    def test_invalid_batch_is_atomic_and_status_cannot_skip_printing(self):
        with self.assertRaisesRegex(ValueError, "Form şablonu seçimi"):
            self.generate_batch([
                {"employee_id": 1, "date": "2026-03-02", "code": "G"},
                {"employee_id": 2, "date": "2026-03-03", "code": "R"},
            ])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM workflow_documents").fetchone()[0], 0)
        self.assertEqual(document_workflows._advance_status("draft", "printed"), "printed")
        with self.assertRaisesRegex(ValueError, "sırası"):
            document_workflows._advance_status("draft", "signed")

    def test_batch_results_separate_failures_and_allow_retrying_only_failed_cells(self):
        payload = {
            "cells": [
                {"employee_id": 1, "date": "2026-03-02", "code": "G"},
                {"employee_id": 2, "date": "2026-03-05", "code": "G"},
            ],
            "templates": {"G": "absence-form"},
            "section": "Satış",
            "description": "Seçilen gün",
        }
        generate = document_workflows.forms.generate

        def fail_second_date(template, person, start, end, code, section, description, event_time=""):
            if start == "2026-03-05":
                raise ValueError("Sentetik üretim hatası")
            return generate(template, person, start, end, code, section, description, event_time)

        with mock.patch.object(document_workflows.forms, "generate", side_effect=fail_second_date):
            result = document_workflows.create_documents_batch_result(self.handler, self.store, payload)

        self.assertEqual(len(result["documents"]), 1)
        self.assertEqual(len(result["failures"]), 1)
        self.assertEqual(result["failures"][0]["date"], "2026-03-05")
        self.assertEqual(result["failures"][0]["person_name"], "Mehmet Demir")
        self.assertEqual(result["failures"][0]["reason"], "Sentetik üretim hatası")
        self.assertEqual(
            self.store.connection.execute("SELECT COUNT(*) FROM workflow_documents").fetchone()[0],
            1,
        )

        retried = document_workflows.create_documents_batch_result(
            self.handler,
            self.store,
            payload | {"cells": [
                {
                    "employee_id": item["employee_id"],
                    "date": item["date"],
                    "code": item["code"],
                }
                for item in result["failures"]
            ]},
        )
        self.assertEqual(len(retried["documents"]), 1)
        self.assertEqual(retried["failures"], [])
        self.assertEqual(
            self.store.connection.execute("SELECT COUNT(*) FROM workflow_documents").fetchone()[0],
            2,
        )

    def test_rapor_code_generates_neutral_form_with_exact_fields(self):
        documents = document_workflows.create_documents_batch(
            self.handler,
            self.store,
            {
                "cells": [{"employee_id": 1, "date": "2026-03-08", "code": "R"}],
                "templates": {"R": "neutral"},
                "section": "",
                "description": "Kayıt notu",
            },
        )
        self.assertEqual(len(documents), 1)
        row = self.store.connection.execute(
            "SELECT content,template_id FROM workflow_documents WHERE id=?",
            (documents[0]["id"],),
        ).fetchone()
        doc = Document(BytesIO(row["content"]))
        fields = [cell.text for table in doc.tables for table_row in table.rows for cell in table_row.cells]
        self.assertEqual(fields, [
            "Personel", "Ayşe Yılmaz", "Tarih", "2026-03-08",
            "Durum", "R", "Açıklama", "Kayıt notu",
        ])
        self.assertEqual(row["template_id"], "neutral-status")

    def test_neutral_form_accepts_optional_title_heading(self):
        payload = {
            "cells": [{"employee_id": 1, "date": "2026-03-09", "code": ""}],
            "templates": {"": "neutral"}, "section": "", "description": "Olay notu",
            "title": "  Olay tespit tutanağı ",
        }
        documents = document_workflows.create_documents_batch(self.handler, self.store, payload)
        content = self.store.connection.execute(
            "SELECT content FROM workflow_documents WHERE id=?", (documents[0]["id"],),
        ).fetchone()["content"]
        doc = Document(BytesIO(content))
        self.assertEqual(doc.paragraphs[0].text, "Olay tespit tutanağı")
        self.assertEqual(doc.tables[0].rows[0].cells[1].text, "Ayşe Yılmaz")
        with self.assertRaisesRegex(ValueError, "120 karakter"):
            document_workflows.create_documents_batch(self.handler, self.store, payload | {"title": "x" * 121})
        with self.assertRaisesRegex(ValueError, "Toplu belge bilgisi"):
            document_workflows.create_documents_batch(self.handler, self.store, payload | {"extra": 1})

    def test_event_time_placeholder_is_replaced_and_never_printed_as_helper_text(self):
        template = Document()
        template.add_paragraph("Olay saati: [Olay saati]")
        output = BytesIO()
        template.save(output)
        row = {
            "id": "event-time-form", "name": "Olay saati", "category": "G",
            "extension": "docx", "content": output.getvalue(),
        }
        rendered = document_workflows.forms.generate(
            row, "Ayşe Yılmaz", "2026-03-02", "2026-03-02", "G", event_time="14:35",
        )
        text = document_workflows.forms.preview(rendered, "docx")
        self.assertIn("14:35", text)
        self.assertNotIn("[Olay saati]", text)

        without_time = document_workflows.forms.generate(
            row, "Ayşe Yılmaz", "2026-03-02", "2026-03-02", "G",
        )
        self.assertNotIn("[Olay saati]", document_workflows.forms.preview(without_time, "docx"))
        with self.assertRaisesRegex(ValueError, "SS:DD"):
            document_workflows.forms.generate(
                row, "Ayşe Yılmaz", "2026-03-02", "2026-03-02", "G", event_time="25:99",
            )

    def test_existing_document_keeps_generated_text_after_template_is_replaced(self):
        created = self.generate_batch([{"employee_id": 1, "date": "2026-03-02", "code": "G"}])[0]
        original = self.store.connection.execute(
            "SELECT content FROM workflow_documents WHERE id=?", (created["id"],),
        ).fetchone()["content"]
        replacement = Document()
        replacement.add_paragraph("Replacement wording: {{personel}}")
        stream = BytesIO()
        replacement.save(stream)
        self.store.connection.execute(
            "UPDATE form_templates SET content=? WHERE id='absence-form'", (stream.getvalue(),),
        )
        self.store.connection.commit()
        saved = self.store.connection.execute(
            "SELECT content FROM workflow_documents WHERE id=?", (created["id"],),
        ).fetchone()["content"]
        self.assertEqual(saved, original)
        self.assertIn("Ayşe Yılmaz", document_workflows.forms.preview(saved, "docx"))
        self.assertNotIn("Replacement wording", document_workflows.forms.preview(saved, "docx"))

    def test_event_documents_are_personalized_and_single_document_text_stays_separate_from_template(self):
        template = Document()
        template.add_paragraph("{{personel}} · {{tarih}} · {{durum}} · {{bolum}} · {{aciklama}}")
        template_output = BytesIO()
        template.save(template_output)
        template_content = template_output.getvalue()
        self.store.connection.execute(
            "INSERT INTO form_templates VALUES (?,?,?,?,?)",
            ("event-form", "Olay tutanağı", "G", "docx", template_content),
        )
        self.store.connection.commit()
        payload = {
            "cells": [
                {"employee_id": 1, "date": "2026-03-02", "code": "G"},
                {"employee_id": 2, "date": "2026-03-02", "code": "G"},
            ],
            "templates": {"G": "event-form"},
            "section": "Satış",
            "description": "Ortak olay açıklaması",
        }
        documents = document_workflows.create_documents_batch(self.handler, self.store, payload)
        saved = {}
        for item in documents:
            content = self.store.connection.execute(
                "SELECT content FROM workflow_documents WHERE id=?", (item["id"],),
            ).fetchone()["content"]
            text = "\n".join(paragraph.text for paragraph in Document(BytesIO(content)).paragraphs)
            saved[item["employee_id"]] = text
        self.assertIn("Ayşe Yılmaz", saved[1])
        self.assertNotIn("Mehmet Demir", saved[1])
        self.assertIn("Ortak olay açıklaması", saved[1])
        self.assertIn("Mehmet Demir", saved[2])
        self.assertNotIn("Ayşe Yılmaz", saved[2])
        self.assertIn("Ortak olay açıklaması", saved[2])

        one_document = document_workflows.create_documents_batch(
            self.handler, self.store,
            payload | {
                "cells": [{"employee_id": 1, "date": "2026-03-02", "code": "G"}],
                "description": "Yalnız bu belgeye ait düzenleme",
            },
        )[0]
        edited_content = self.store.connection.execute(
            "SELECT content FROM workflow_documents WHERE id=?", (one_document["id"],),
        ).fetchone()["content"]
        edited_text = "\n".join(
            paragraph.text for paragraph in Document(BytesIO(edited_content)).paragraphs
        )
        self.assertIn("Yalnız bu belgeye ait düzenleme", edited_text)
        self.assertIn("Ortak olay açıklaması", saved[1])
        persisted_template = self.store.connection.execute(
            "SELECT content FROM form_templates WHERE id='event-form'",
        ).fetchone()["content"]
        self.assertEqual(persisted_template, template_content)

    def test_attachment_validates_type_and_stays_draft(self):
        document = self.generate_batch()[0]
        content = b"%PDF-1.7\nsafe test"
        attachment = document_workflows.add_attachment(
            self.handler,
            self.store,
            document["id"],
            {"filename": "kanıt.pdf", "content": base64.b64encode(content).decode("ascii")},
        )
        self.assertEqual(attachment["status"], "draft")
        self.assertEqual(attachment["sha256"], __import__("hashlib").sha256(content).hexdigest())
        changed_content = b"%PDF-1.7\nchanged test"
        changed_attachment = document_workflows.add_attachment(
            self.handler,
            self.store,
            document["id"],
            {"filename": "kanıt.pdf", "content": base64.b64encode(changed_content).decode("ascii")},
        )
        self.assertNotEqual(changed_attachment["sha256"], attachment["sha256"])
        self.assertEqual(self.store.history[-1][0], "document.attachment_changed")
        self.assertEqual(self.store.history[-1][2]["previous_sha256"], attachment["sha256"])
        self.assertTrue(document_workflows.handle_get(
            self.handler, "/api/document-workflows/documents", {"limit": ["20"]},
        ))
        listed = next(item for item in self.handler.response["documents"] if item["id"] == document["id"])
        self.assertTrue(all(item["same_name_changed"] for item in listed["attachments"]))
        self.assertTrue(all("content" not in item for item in listed["attachments"]))
        with self.assertRaisesRegex(ValueError, "eşleşmiyor"):
            document_workflows.add_attachment(
                self.handler,
                self.store,
                document["id"],
                {"filename": "fake.pdf", "content": base64.b64encode(b"not a pdf").decode("ascii")},
            )

    def test_share_contains_only_employee_published_schedule_and_revocation_works(self):
        self.store.settings["published_plans"] = {
            "2026-03": {
                "entries": [
                    {"employee_id": 1, "days": {
                        "2026-03-02": {"kind": "work", "start": "07:00", "end": "15:00", "note": "private-note"},
                        "2026-03-03": {"kind": "sick_leave"},
                    }},
                    {"employee_id": 1, "days": {"2026-03-02": {"kind": "work", "start": "16:00", "end": "20:00"}}},
                    {"employee_id": 2, "days": {"2026-03-02": {"kind": "work", "start": "15:00", "end": "23:00"}}},
                ],
            },
        }
        share = document_workflows.create_share(
            self.handler, self.store, {"employee_id": 1, "month": "2026-03", "days": 7},
        )
        self.handler.command = "GET"
        self.assertTrue(document_workflows.handle_public_share(
            self.handler, f"/document-share/{share['token']}",
        ))
        page = self.handler.body.getvalue().decode("utf-8")
        self.assertIn("Ayşe Yılmaz", page)
        self.assertIn("07:00", page)
        self.assertIn("16:00", page)
        self.assertNotIn("Mehmet Demir", page)
        self.assertNotIn("private-note", page)
        self.assertNotIn("2026-03-03", page)
        self.assertNotIn("Rapor", page)
        self.assertNotIn("attendance", page.lower())
        self.assertEqual(self.handler.headers["Cache-Control"], "no-store")
        self.assertFalse(document_workflows.handle_public_share(
            self.handler, f"/document-share/{share['token']}/extra",
        ))
        self.assertTrue(document_workflows.handle_write(
            self.handler, "POST", f"/api/document-workflows/shares/{share['id']}/revoke",
        ))
        self.assertFalse(document_workflows._share_summaries(self.store.connection)[0]["active"])

    def test_personal_calendar_uses_only_published_work_and_converts_overnight(self):
        self.store.settings["published_plans"] = {"2026-03": {
            "source_revision": "schedule-r7", "entries": [
            {"employee_id": 1, "days": {
                "2026-03-02": {"kind": "work", "start": "22:00", "end": "06:00", "note": "private-note"},
                "2026-03-03": {"kind": "sick_leave"},
                "2026-03-04": {"kind": "work", "start": "16:00", "end": "24:00"},
                "2026-04-01": {"kind": "work", "start": "08:00", "end": "12:00"},
            }},
            {"employee_id": 2, "days": {
                "2026-03-02": {"kind": "work", "start": "10:00", "end": "12:00"},
            }},
        ]}}
        calendar = document_workflows.personal_calendar(self.store, 1, "2026-03")
        self.assertEqual(calendar.count(b"BEGIN:VEVENT"), 2)
        self.assertIn(b"DTSTART:20260302T190000Z\r\nDTEND:20260303T030000Z\r\n", calendar)
        self.assertIn(b"DTEND:20260304T210000Z\r\n", calendar)
        self.assertIn(b"X-WR-CALNAME:Ay\xc5\x9fe Y\xc4\xb1lmaz - 2026-03", calendar)
        self.assertIn(b"X-VP-EMPLOYEE-ID:1\r\nX-VP-PERIOD:2026-03\r\nX-VP-PLAN-VERSION:", calendar)
        self.assertNotIn(b"private-note", calendar)
        self.assertNotIn(b"20260302T070000Z", calendar)
        self.assertTrue(calendar.endswith(b"END:VCALENDAR\r\n"))
        with self.assertRaisesRegex(ValueError, "yayımlanmış vardiya"):
            document_workflows.personal_calendar(self.store, 1, "2026-04")
        self.store.settings["published_plans"]["2026-03"]["entries"].pop(0)
        with self.assertRaisesRegex(ValueError, "yayımlanmış çalışma vardiyası yok"):
            document_workflows.personal_calendar(self.store, 1, "2026-03")

    @staticmethod
    def calendar_fields(content):
        unfolded = content.replace(b"\r\n ", b"").decode("utf-8")
        header, *blocks = unfolded.split("BEGIN:VEVENT\r\n")

        def properties(block):
            return dict(line.split(":", 1) for line in block.split("\r\n") if line)

        return properties(header), [properties(block.split("END:VEVENT\r\n")[0]) for block in blocks]

    def test_split_calendar_reexports_follow_published_rows_not_times_or_order(self):
        month, day = "2026-03", "2026-03-02"
        first, second = scheduling.section_names()[:2]
        rows = [
            {"employee_id": 1, "row_id": "private-row-a", "section": first, "slot": 1, "days": {
                day: {"kind": "work", "start": "07:00", "end": "11:00", "note": "private-note"},
                "2026-03-03": {"kind": "work", "start": "22:00", "end": "06:00"},
                "2026-03-04": {"kind": "weekly_off"},
            }},
            {"employee_id": 1, "row_id": "private-row-b", "section": second, "slot": 1, "days": {
                day: {"kind": "work", "start": "16:00", "end": "20:00", "shift_label": "private-label"},
            }},
            {"employee_id": 2, "row_id": "private-row-c", "section": first, "slot": 2, "days": {
                "2026-03-05": {"kind": "work", "start": "10:00", "end": "18:00"},
            }},
        ]
        self.handler._send = mock.Mock()

        def publish(revision, at):
            self.store.settings["published_plans"] = {month: {
                "source_revision": revision, "at": at, "actor": "private-actor",
                "changes": [{"name": "Mehmet Demir", "section": second}],
                "entries": scheduling.normalize_schedule_entries(month, rows, self.store.people()),
            }}

        def download(revision):
            self.assertTrue(document_workflows.handle_get(
                self.handler, "/api/document-workflows/calendar", {"employee_id": ["1"], "month": [month]},
            ))
            (content, mime), kwargs = self.handler._send.call_args
            self.assertEqual(mime, "text/calendar; charset=utf-8")
            self.assertEqual(kwargs["filename"], f"vardiya-1-{month}-{revision}.ics")
            header, events = self.calendar_fields(content)
            self.assertEqual(header["X-VP-PLAN-VERSION"], revision)
            self.assertEqual(len(events), 3)
            self.assertEqual(len({e["UID"] for e in events}), 3)
            for event in events:
                self.assertEqual(event["X-VP-PLAN-VERSION"], revision)
            for private in ("private-", "Mehmet Demir", first, second, "20260305", "20260304T000000Z",
                            "weekly_off", "ATTENDEE", "ORGANIZER"):
                self.assertNotIn(private.encode(), content)
            return {e["DTSTART"]: e["UID"] for e in events}, content

        rev1, rev2 = "a" * 32, "b" * 32
        publish(rev1, "2026-03-01T12:00:00+00:00")
        before, original = download(rev1)
        self.assertEqual(set(before), {"20260302T040000Z", "20260302T130000Z", "20260303T190000Z"})
        # Pending edits cannot affect the published file. Republishing reverses
        # both chronological order and the order of rows in the snapshot.
        rows[0]["days"][day].update(start="19:00", end="23:00")
        rows[1]["days"][day].update(start="08:00", end="12:00")
        rows.reverse()
        self.assertEqual(download(rev1)[1], original)
        publish(rev2, "2026-03-02T08:00:00+00:00")
        after, _ = download(rev2)
        self.assertEqual(after, {
            "20260302T160000Z": before["20260302T040000Z"],
            "20260302T050000Z": before["20260302T130000Z"],
            "20260303T190000Z": before["20260303T190000Z"],
        })
        # Distinct rows still describe two events if their intervals coincide.
        rows[1]["days"][day].update(start="19:00", end="23:00")
        publish("c" * 32, "2026-03-02T10:00:00+00:00")
        _, events = self.calendar_fields(download("c" * 32)[1])
        self.assertEqual({e["UID"] for e in events if e["DTSTART"] == "20260302T160000Z"},
                         {before["20260302T040000Z"], before["20260302T130000Z"]})

    def test_legacy_calendar_versions_and_private_metadata(self):
        day = "2026-03-02"
        entries = [{"employee_id": 1, "days": {day: {"kind": "work", "start": start, "end": end}}}
                   for start, end in [("08:00", "12:00"), ("13:00", "17:00")]]
        published = {"entries": entries}
        self.store.settings["published_plans"] = {"2026-03": published}
        self.store.people_data[0]["name"] = "Ş" * 42 + "\r\nATTENDEE:private"

        def export():
            content = document_workflows.personal_calendar(self.store, 1, "2026-03")
            self.assertNotIn(b"\r\nATTENDEE:", content)
            self.assertTrue(all(len(line) <= 75 for line in content.split(b"\r\n")))
            header, events = self.calendar_fields(content)
            revision = header["X-VP-PLAN-VERSION"]
            self.assertRegex(revision, r"^sha256:[0-9a-f]{64}$")
            self.assertEqual([e["X-VP-PLAN-VERSION"] for e in events], [revision, revision])
            return revision, [e["UID"] for e in events]

        first_rev, ids = export()
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(export(), (first_rev, ids))
        entries[0]["days"][day]["start"] = "07:00"
        second_rev, edited_ids = export()
        self.assertNotEqual(second_rev, first_rev)
        self.assertEqual(edited_ids, ids)
        published["source_revision"] = "private-payroll-note"
        revision, private_ids = export()
        self.assertEqual(private_ids, ids)
        self.handler._send = mock.Mock()
        document_workflows.handle_get(self.handler, "/api/document-workflows/calendar",
                                      {"employee_id": ["1"], "month": ["2026-03"]})
        self.assertNotIn(b"private-payroll-note", self.handler._send.call_args.args[0])
        self.assertEqual(self.handler._send.call_args.kwargs["filename"],
                         f"vardiya-1-2026-03-{revision.replace(':', '-')}.ics")

    def test_calendar_endpoints_require_admin_when_accounts_enabled(self):
        self.store.users_enabled = lambda: True
        self.handler.current_user = {"role": "viewer"}
        self.handler._send = mock.Mock()
        document_workflows.handle_get(self.handler, "/api/document-workflows/calendar",
                                      {"employee_id": ["1"], "month": ["2026-03"]})
        self.assertEqual(self.handler.status, 403)
        self.handler._send.assert_not_called()
        self.handler.status = None
        document_workflows.handle_write(self.handler, "POST", "/api/document-workflows/calendar/save")
        self.assertEqual(self.handler.status, 403)

    def test_desktop_personal_calendar_save_uses_native_save_dialog(self):
        self.store.settings["published_plans"] = {"2026-03": {
            "source_revision": "schedule-r7", "entries": [
            {"employee_id": 1, "days": {
                "2026-03-02": {"kind": "work", "start": "08:00", "end": "16:00"},
            }},
        ]}}
        saved_path = Path(self.temporary.name) / "vardiya-1-2026-03-schedule-r7.ics"

        def write_calendar(extension, content, filename):
            self.assertEqual((extension, filename), ("ics", saved_path.name))
            saved_path.write_bytes(content)
            return {"cancelled": False, "path": str(saved_path)}

        save_form = mock.Mock(side_effect=write_calendar)
        self.handler.server = SimpleNamespace(save_form=save_form)
        with mock.patch.object(self.handler, "_read_json", return_value={
            "employee_id": 1, "month": "2026-03",
        }):
            self.assertTrue(document_workflows.handle_write(
                self.handler, "POST", "/api/document-workflows/calendar/save",
            ))
        save_form.assert_called_once()
        extension, content, filename = save_form.call_args.args
        self.assertEqual((extension, filename), ("ics", saved_path.name))
        header, events = self.calendar_fields(content)
        self.assertEqual(header["X-VP-PLAN-VERSION"], "schedule-r7")
        self.assertEqual([e["X-VP-PLAN-VERSION"] for e in events], ["schedule-r7"])
        self.assertEqual(saved_path.read_bytes(), content)
        self.assertTrue(self.handler.response["verified"])
        self.assertEqual(self.handler.response["path"], str(saved_path.resolve()))
        self.assertEqual(self.handler.response["plan_version"], "schedule-r7")
        self.assertEqual(self.handler.response["sha256"], sha256(content).hexdigest())
        self.assertEqual(self.handler.response["size_bytes"], len(content))
        record = self.store.connection.execute(
            "SELECT employee_id,person_name,month,plan_version,path,filename,sha256,size_bytes "
            "FROM personal_calendar_exports WHERE id=?",
            (self.handler.response["export_id"],),
        ).fetchone()
        self.assertEqual(tuple(record), (
            1, "Ayşe Yılmaz", "2026-03", "schedule-r7", str(saved_path.resolve()),
            saved_path.name, sha256(content).hexdigest(), len(content),
        ))

    def test_calendar_save_cancellation_does_not_create_export_record(self):
        self.store.settings["published_plans"] = {"2026-03": {"entries": [
            {"employee_id": 1, "days": {
                "2026-03-02": {"kind": "work", "start": "08:00", "end": "16:00"},
            }},
        ]}}
        self.handler.server = SimpleNamespace(save_form=mock.Mock(return_value={"cancelled": True}))
        with mock.patch.object(self.handler, "_read_json", return_value={
            "employee_id": 1, "month": "2026-03",
        }):
            document_workflows.handle_write(self.handler, "POST", "/api/document-workflows/calendar/save")
        self.assertEqual(self.handler.response, {"cancelled": True})
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM personal_calendar_exports",
        ).fetchone()[0], 0)

    def test_calendar_save_rejects_missing_or_mismatched_file(self):
        self.store.settings["published_plans"] = {"2026-03": {"entries": [
            {"employee_id": 1, "days": {
                "2026-03-02": {"kind": "work", "start": "08:00", "end": "16:00"},
            }},
        ]}}
        missing_path = Path(self.temporary.name) / "missing.ics"
        self.handler.server = SimpleNamespace(save_form=mock.Mock(return_value={
            "cancelled": False, "path": str(missing_path),
        }))
        with mock.patch.object(self.handler, "_read_json", return_value={
            "employee_id": 1, "month": "2026-03",
        }):
            with self.assertRaisesRegex(ValueError, "erişilemiyor"):
                document_workflows.handle_write(
                    self.handler, "POST", "/api/document-workflows/calendar/save",
                )

        mismatched_path = Path(self.temporary.name) / "mismatched.ics"
        mismatched_path.write_bytes(b"not the exported calendar")
        self.handler.server.save_form.return_value = {
            "cancelled": False, "path": str(mismatched_path),
        }
        with mock.patch.object(self.handler, "_read_json", return_value={
            "employee_id": 1, "month": "2026-03",
        }):
            with self.assertRaisesRegex(ValueError, "içeriği doğrulanamadı"):
                document_workflows.handle_write(
                    self.handler, "POST", "/api/document-workflows/calendar/save",
                )
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM personal_calendar_exports",
        ).fetchone()[0], 0)

    def test_calculation_reuses_summary_and_marks_manual_overrides(self):
        self.store.settings["overtime_rule_versions"] = {
            "2026-03": {"weekly_limit_hours": 0, "week_start": 0, "daily_limit_hours": 8,
                        "break_rules": [[6, 30]]},
        }
        result = document_workflows.calculation_detail(self.handler, self.store, "2026-03", 1)
        self.assertEqual(result["result"]["annual_leave_remaining_days"], 4.5)
        self.assertEqual(result["annual_leave_equation"], {
            "opening_days": 6.0, "used_days": 1.5, "remaining_days": 4.5,
        })
        self.assertEqual(sum(row["overtime_hours"] for row in result["daily_rows"]), 1.5)
        self.assertEqual(result["monthly_totals"]["overtime_computed_hours"], 1.5)
        self.assertEqual(result["monthly_totals"]["overtime_hours"], 2.0)
        self.assertTrue(result["monthly_totals"]["overtime_manual"])
        self.assertEqual(result["daily_rows"][1]["holiday_hours"], 2)
        self.assertEqual(result["daily_rows"][0]["worked_minutes"], 480)
        self.assertEqual(result["daily_rows"][0]["break_minutes"], 30)
        self.assertEqual(result["daily_rows"][0]["overtime_minutes"], 30)
        self.assertEqual(result["overtime_rule_effective_month"], "2026-03")
        self.assertEqual(result["overtime_rule_effective_date"], "2026-03-01")
        self.assertIn("iki ondalık", result["rounding"]["display"])
        self.assertEqual(result["manual_overrides"], [
            {"month": "2026-03", "date": "2026-03-02", "code": "S", "source": "manual"},
        ])

    def test_related_center_flags_documents_and_approvals_after_revision_change(self):
        self.generate_batch()
        self.store.settings["approvals"] = {
            "2026-03": {
                "prepared": {
                    "name": "Yönetici",
                    "at": "2026-03-01T10:00:00Z",
                    "revisions": {"schedule": "old", "attendance": "old"},
                },
            },
        }
        self.handler._month_revisions = lambda store, month: {
            "schedule": "schedule-r2", "attendance": "attendance-r1",
        }
        result = document_workflows._related_records(self.handler, self.store, "2026-03")
        self.assertEqual(len(result["stale_approvals"]), 1)
        self.assertEqual(len(result["stale_documents"]), 3)

    def test_document_index_and_file_reads_require_admin_when_accounts_enabled(self):
        self.generate_batch()
        self.store.users_enabled = lambda: True
        self.handler.current_user = {"role": "viewer"}
        self.assertTrue(document_workflows.handle_get(
            self.handler, "/api/document-workflows/documents", {"limit": ["20"]},
        ))
        self.assertEqual(self.handler.status, 403)
        self.assertTrue(document_workflows.handle_get(
            self.handler, "/api/document-workflows/backup-inspection",
            {"id": ["missing"], "month": ["2026-03"], "kind": ["schedule"]},
        ))
        self.assertEqual(self.handler.status, 403)

    def test_retention_policy_is_optional_and_due_documents_are_reviewed_not_deleted(self):
        documents = self.generate_batch()
        rows = self.store.connection.execute(
            "SELECT id FROM workflow_documents ORDER BY id"
        ).fetchall()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with self.store.connection:
            self.store.connection.execute(
                "UPDATE workflow_documents SET created_at=? WHERE id=?",
                ((now - timedelta(days=3)).isoformat(), rows[0]["id"]),
            )
            for row in rows[1:]:
                self.store.connection.execute(
                    "UPDATE workflow_documents SET created_at=? WHERE id=?",
                    ((now - timedelta(hours=12)).isoformat(), row["id"]),
                )
        document_count = self.store.connection.execute(
            "SELECT COUNT(*) FROM workflow_documents"
        ).fetchone()[0]

        self.assertTrue(document_workflows.handle_get(
            self.handler, "/api/document-workflows/retention", {},
        ))
        self.assertIsNone(self.handler.response["policy"])
        self.assertEqual(self.handler.response["review_documents"], [])

        self.handler.command = "PUT"
        self.handler._read_json = lambda maximum=8192: {"retention_days": True}
        with self.assertRaisesRegex(ValueError, "1-365000 gün"):
            document_workflows.handle_write(
                self.handler, "PUT", "/api/document-workflows/retention",
            )
        self.handler._read_json = lambda maximum=8192: {"retention_days": 1}
        self.assertTrue(document_workflows.handle_write(
            self.handler, "PUT", "/api/document-workflows/retention",
        ))
        self.assertEqual(self.handler.response["policy"]["retention_days"], 1)
        reviewed = self.handler.response["review_documents"]
        self.assertEqual([item["id"] for item in reviewed], [rows[0]["id"]])
        self.assertEqual(
            datetime.fromisoformat(reviewed[0]["review_due_at"]),
            datetime.fromisoformat(reviewed[0]["created_at"]) + timedelta(days=1),
        )
        self.assertEqual(
            self.store.connection.execute("SELECT COUNT(*) FROM workflow_documents").fetchone()[0],
            document_count,
        )

        document_workflows.initialize(self.store.connection)
        self.assertTrue(document_workflows.handle_get(
            self.handler, "/api/document-workflows/retention", {},
        ))
        self.assertEqual(self.handler.response["policy"]["retention_days"], 1)
        self.handler._read_json = lambda maximum=8192: {"retention_days": None}
        self.assertTrue(document_workflows.handle_write(
            self.handler, "PUT", "/api/document-workflows/retention",
        ))
        self.assertIsNone(self.handler.response["policy"])
        self.assertEqual(self.handler.response["review_documents"], [])
        self.assertEqual(
            self.store.connection.execute("SELECT COUNT(*) FROM workflow_documents").fetchone()[0],
            document_count,
        )

    def test_retention_policy_requires_admin_when_accounts_are_enabled(self):
        self.store.users_enabled = lambda: True
        self.handler.current_user = {"role": "viewer"}
        self.assertTrue(document_workflows.handle_get(
            self.handler, "/api/document-workflows/retention", {},
        ))
        self.assertEqual(self.handler.status, 403)
        self.handler._read_json = lambda maximum=8192: {"retention_days": 30}
        self.assertTrue(document_workflows.handle_write(
            self.handler, "PUT", "/api/document-workflows/retention",
        ))
        self.assertEqual(self.handler.status, 403)
        self.assertIsNone(document_workflows._retention_review(self.store)["policy"])

    def test_document_preview_renders_saved_document_and_requires_admin(self):
        document = self.generate_batch()[0]
        path = f"/api/document-workflows/documents/{document['id']}/preview"
        self.assertTrue(document_workflows.handle_get(self.handler, path, {}))
        self.assertIn("Ayşe Yılmaz", self.handler.response["text"])
        self.assertIn("<main", self.handler.response["html"])
        self.assertIn("page-break-after", self.handler.response["html"])

        self.store.users_enabled = lambda: True
        self.handler.current_user = {"role": "viewer"}
        self.assertTrue(document_workflows.handle_get(self.handler, path, {}))
        self.assertEqual(self.handler.status, 403)

    def test_document_save_uses_desktop_picker_and_preserves_cancelled_draft(self):
        document = self.generate_batch()[0]
        row = self.store.connection.execute(
            "SELECT filename,content,status FROM workflow_documents WHERE id=?",
            (document["id"],),
        ).fetchone()
        save_form = mock.Mock(return_value={"cancelled": False, "path": "C:/Tutanak.docx"})
        self.handler.server = SimpleNamespace(save_form=save_form)
        self.handler.command = "POST"
        path = f"/api/document-workflows/documents/{document['id']}/save"
        self.assertTrue(document_workflows.handle_write(self.handler, "POST", path))
        save_form.assert_called_once_with("docx", row["content"], row["filename"])
        self.assertEqual(self.handler.response, {"cancelled": False, "path": "C:/Tutanak.docx"})

        self.handler.server.save_form = None
        with self.assertRaisesRegex(ValueError, "yalnız masaüstü"):
            document_workflows.handle_write(self.handler, "POST", path)
        self.handler.server.save_form = save_form
        save_form.reset_mock()
        save_form.return_value = {"cancelled": True}
        self.handler.response = None
        self.assertTrue(document_workflows.handle_write(self.handler, "POST", path))
        self.assertEqual(self.handler.response, {"cancelled": True})
        current = self.store.connection.execute(
            "SELECT status FROM workflow_documents WHERE id=?",
            (document["id"],),
        ).fetchone()
        self.assertEqual(current["status"], row["status"])

    def test_export_trace_records_stamped_bytes_and_supports_code_lookup(self):
        code = document_workflows.new_export_code()
        workbook = Workbook()
        source = BytesIO()
        workbook.save(source)
        stamped = document_workflows.stamp_export(source.getvalue(), "march.xlsx", code)
        self.assertIn(code, load_workbook(BytesIO(stamped)).active.oddFooter.center.text)
        pdf_writer = PdfWriter()
        pdf_writer.add_blank_page(width=200, height=100)
        pdf_source = BytesIO()
        pdf_writer.write(pdf_source)
        stamped_pdf = document_workflows.stamp_export(pdf_source.getvalue(), "report.pdf", code)
        self.assertIn(code, PdfReader(BytesIO(stamped_pdf)).pages[0].extract_text())
        image_source = BytesIO()
        Image.new("RGB", (80, 40), "white").save(image_source, "JPEG")
        stamped_image = Image.open(BytesIO(document_workflows.stamp_export(
            image_source.getvalue(), "photo.jpg", code,
        )))
        self.assertEqual(stamped_image.size, (80, 64))

        export_id = document_workflows.record_export(
            self.store.connection,
            "schedule",
            "2026-03",
            "march.xlsx",
            stamped,
            {"2026-03": self.handler._month_revisions(self.store, "2026-03")},
            app_version="3.1.0-test",
            code_revision="code-test",
            export_id=code,
        )
        self.assertEqual(export_id, code)
        result = document_workflows._export_summaries(self.handler, self.store)[0]
        self.assertTrue(result["current"])
        self.assertEqual(result["code_revision"], "code-test")
        self.assertEqual(len(result["sha256"]), 64)
        self.assertEqual(result["sha256"], sha256(stamped).hexdigest())
        self.assertEqual(result["code"], code)
        self.assertTrue(document_workflows.handle_get(
            self.handler, "/api/document-workflows/exports/lookup", {"code": [code]},
        ))
        self.assertEqual(self.handler.response["export"]["code"], code)
        with self.assertRaisesRegex(ValueError, "Çıktı kodu biçimi"):
            document_workflows.record_export(
                self.store.connection, "schedule", "2026-03", "bad.xlsx", b"data",
                {"2026-03": self.handler._month_revisions(self.store, "2026-03")},
                export_id="invalid",
            )
        self.handler._month_revisions = lambda store, month: {
            "schedule": "changed", "attendance": "attendance-r1",
        }
        self.assertFalse(document_workflows._export_summaries(self.handler, self.store)[0]["current"])

    def test_draft_export_is_never_reported_current(self):
        document_workflows.record_export(
            self.store.connection,
            "draft-jpeg",
            "2026-03",
            "draft.jpg",
            b"draft-snapshot",
            {"2026-03": self.handler._month_revisions(self.store, "2026-03")},
        )
        result = document_workflows._export_summaries(self.handler, self.store)[0]
        self.assertEqual(result["type"], "draft-jpeg")
        self.assertFalse(result["current"])

    def test_backup_inspection_is_readonly_and_excludes_user_credentials(self):
        backup = Path(self.temporary.name) / "manual-20260301T000000Z-deadbeef.sqlite3"
        connection = sqlite3.connect(backup)
        connection.executescript(
            "CREATE TABLE employees(id INTEGER);"
            "CREATE TABLE schedule_months(period TEXT,payload TEXT,revision TEXT);"
            "CREATE TABLE attendance_months(period TEXT,payload TEXT,revision TEXT);"
            "CREATE TABLE users(username TEXT,password_hash TEXT);"
            "INSERT INTO employees VALUES (1);"
            "INSERT INTO users VALUES ('admin','secret-hash');"
            "PRAGMA user_version=9;"
        )
        connection.execute(
            "INSERT INTO schedule_months VALUES (?,?,?)",
            ("2026-03", json.dumps({"entries": [{"employee_id": 1, "days": {"2026-03-02": {"kind": "work"}}}]}), "r1"),
        )
        connection.execute(
            "INSERT INTO attendance_months VALUES (?,?,?)",
            ("2026-03", json.dumps({"overrides": {"2026-03-02": {"1": "S"}}}), "a1"),
        )
        connection.commit()
        connection.close()
        original_hash = sha256(backup.read_bytes()).hexdigest()
        inspection = document_workflows._inspect_backup(self.store, backup.name)
        self.assertEqual(inspection["counts"]["employees"], 1)
        self.assertNotIn("users", inspection["counts"])
        detail = document_workflows._inspect_backup(self.store, backup.name, "2026-03", "schedule")
        self.assertEqual(detail["detail"]["records"]["entries"][0]["employee_id"], 1)
        attendance = document_workflows._inspect_backup(self.store, backup.name, "2026-03", "attendance")
        self.assertEqual(attendance["detail"]["records"]["overrides"]["2026-03-02"]["1"], "S")
        connection = sqlite3.connect(backup)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 9)
        connection.close()
        self.assertEqual(sha256(backup.read_bytes()).hexdigest(), original_hash)

    def test_backup_rehearsal_restores_and_opens_both_workbooks_without_live_writes(self):
        from threading import Thread
        from urllib.request import Request
        from server import create_server
        from storage import Store

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live.sqlite3"
            with Store(path) as live:
                employee = live.add_person("Prova Personeli")["id"]
                live.save_schedule_month("2026-03", [
                    {"row_id": "prova", "employee_id": employee, "section": "MARKET", "slot": 1,
                     "days": {"2026-03-02": {"kind": "work", "start": "08:00", "end": "16:00"}}},
                ], "")
                live.save_attendance_month("2026-03", {"2026-03-04": {str(employee): "S"}}, {}, "")
                live.save_attendance_month("2026-04", {"2026-04-01": {str(employee): "H"}}, {}, "")
                backup = live.create_backup("manual")
                live.add_person("Yedek Sonrası Personel")
                backup_path = live._backup_path(backup["id"])
            server = create_server(path, Path(__file__).resolve().parents[1] / "web")
            Thread(target=server.serve_forever, daemon=True).start()
            try:
                before = [sha256(file.read_bytes()).hexdigest() for file in (path, backup_path)]
                try:
                    with urlopen(Request(
                        f"http://127.0.0.1:{server.server_address[1]}/api/document-workflows/backup-rehearsal",
                        data=json.dumps({"id": backup["id"], "periods": {"attendance": "2026-03"}}).encode(),
                        headers={"Content-Type": "application/json"},
                    ), timeout=20) as response:
                        result = json.load(response)["rehearsal"]
                except HTTPError as error:
                    self.fail(f"HTTP {error.code}: {error.read().decode('utf-8')}")
                self.assertEqual(result["checks"]["schedule"]["records"], 1)
                self.assertGreaterEqual(result["checks"]["schedule"]["sheets"], 4)
                self.assertEqual(result["checks"]["attendance"], {"month": "2026-03", "records": 1, "sheets": 1})
                for periods in ({"schedule": "2026-08"}, {"attendance": "bad"}, {"extra": "2026-03"}, None):
                    with self.assertRaises(HTTPError) as refused:
                        urlopen(Request(
                            f"http://127.0.0.1:{server.server_address[1]}/api/document-workflows/backup-rehearsal",
                            data=json.dumps({"id": backup["id"], "periods": periods}).encode(),
                            headers={"Content-Type": "application/json"},
                        ), timeout=20)
                    with refused.exception as response:
                        self.assertEqual(response.code, 400)
                self.assertEqual([sha256(file.read_bytes()).hexdigest() for file in (path, backup_path)], before)
            finally:
                server.shutdown()
                server.server_close()

    def test_phone_view_serves_only_capability_route_readonly(self):
        database = Path(self.temporary.name) / "phone-share.sqlite3"
        token = secrets.token_urlsafe(32)
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        document_workflows.initialize(connection)
        connection.execute(
            """INSERT INTO workflow_shares
               (id,token_hash,employee_id,person_name,month,snapshot,expires_at,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                "share-id", __import__("hashlib").sha256(token.encode()).hexdigest(),
                1, "Ayşe", "2026-03",
                json.dumps({"person_name": "Ayşe", "month": "2026-03", "days": {}}),
                (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
        connection.close()
        original_hash = sha256(database.read_bytes()).hexdigest()
        phone = PhoneViewServer(database, lambda: self.fail("share route must not use Store"))
        self.assertFalse(phone.status()["running"])
        status = phone.start(0)
        port = phone.httpd.server_address[1]
        try:
            with urlopen(f"http://127.0.0.1:{port}/document-share/{token}", timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("Ayşe", response.read().decode("utf-8"))
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            try:
                urlopen(f"http://127.0.0.1:{port}/api/document-workflows/documents", timeout=3)
                self.fail("Phone server must not proxy API paths")
            except HTTPError as error:
                self.assertEqual(error.code, 403)
                error.close()
        finally:
            phone.stop()
        self.assertTrue(status["running"])
        self.assertEqual(sha256(database.read_bytes()).hexdigest(), original_hash)


if __name__ == "__main__":
    unittest.main()
