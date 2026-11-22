from __future__ import annotations

import base64
import io
import smtplib
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openpyxl import Workbook, load_workbook

import access_services
import mail_automation
import server as app_server
from storage import Store


SENT = mail_automation._DeliveryResult("sent")


def at(*parts):
    return datetime(*parts).astimezone()


class MailAutomationTests(unittest.TestCase):
    def setUp(self):
        live_socket = patch("socket.create_connection", side_effect=AssertionError("Live SMTP is forbidden"))
        self.addCleanup(live_socket.stop)
        self.addCleanup(live_socket.start().assert_not_called)
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "mail.sqlite3"
        self.backup_patch = patch.object(Store, "_backup_before_change", lambda store: None)
        self.backup_patch.start()
        with Store(self.path) as store:
            mail_automation.initialize(store.connection)
            store.connection.execute("INSERT INTO employees(name,active) VALUES('Synthetic Recipient',1)")
            store.connection.commit()
            self.synthetic_employee_id = int(store.connection.execute(
                "SELECT id FROM employees WHERE name='Synthetic Recipient'",
            ).fetchone()["id"])

    def tearDown(self):
        self.backup_patch.stop()
        self.temp.cleanup()

    def settings(self, password="secret", enabled=True):
        return {
            "host": "smtp.example.com", "port": 587, "username": "reporter",
            "from_address": "reports@example.com", "use_ssl": False,
            "enabled": enabled, "password": password,
        }

    def job(self, **overrides):
        value = {
            "id": "", "kind": "attendance", "cadence": "weekly",
            "weekday": 0, "month_day": None, "hour": 9, "minute": 0,
            "to": "manager@example.com; manager@example.com;", "subject": "",
            "body": "", "enabled": True,
        }
        value.update(overrides)
        return value

    def save(self, settings=None, jobs=None):
        with Store(self.path) as store:
            result = mail_automation.save_configuration(
                store, {"settings": settings or self.settings(), "jobs": jobs or []},
            )
            for job in result["jobs"]:
                for recipient in mail_automation._emails(job["to"]):
                    mail_automation.save_recipient_permission(store, {
                        "email": recipient, "employee_id": self.synthetic_employee_id,
                        "section_scope": "", "all_sections": True,
                    })
            return result

    def prepare(self, job, active_since, settings=None):
        """Save one enabled plan whose current schedule became active at ``active_since``."""
        with patch.object(mail_automation, "_protect_password", return_value=b"protected"):
            job_id = self.save(settings=settings, jobs=[job])["jobs"][0]["id"]
        with Store(self.path) as store:
            store.connection.execute(
                "UPDATE mail_automation_jobs SET active_since=? WHERE id=?",
                (mail_automation._stamp(active_since), job_id),
            )
            store.connection.commit()
        return job_id

    def job_row(self, job_id):
        with Store(self.path) as store:
            return dict(store.connection.execute(
                "SELECT * FROM mail_automation_jobs WHERE id=?", (job_id,),
            ).fetchone())

    def history(self):
        with Store(self.path) as store:
            return mail_automation.get_configuration(store)["history"]

    def scoped_recipients(self, email="market@example.com"):
        with Store(self.path) as store:
            market_id = store.connection.execute(
                "INSERT INTO employees(name,active) VALUES('Synthetic Market Recipient',1)",
            ).lastrowid
            store.save_employee_profile(market_id, {"home_section": "MARKET"})
            cleaning_id = store.connection.execute(
                "INSERT INTO employees(name,active) VALUES('Synthetic Cleaning Recipient',1)",
            ).lastrowid
            store.save_employee_profile(cleaning_id, {"home_section": "TEMİZLİK"})
            mail_automation.save_recipient_permission(store, {
                "email": email, "employee_id": market_id,
                "section_scope": "MARKET", "all_sections": False,
            })
        return market_id, cleaning_id

    def test_password_is_protected_and_never_returned(self):
        with patch.object(mail_automation, "_protect_password", return_value=b"protected"):
            result = self.save(jobs=[self.job()])
        self.assertTrue(result["settings"]["configured"])
        self.assertNotIn("password", result["settings"])
        self.assertNotIn("secret", repr(result))
        with Store(self.path) as store:
            row = store.connection.execute(
                "SELECT password_cipher FROM mail_delivery_settings WHERE id=1",
            ).fetchone()
            self.assertEqual(bytes(row["password_cipher"]), b"protected")

    def test_recipient_preview_contains_real_section_filtered_attachment(self):
        job_id = self.prepare(
            self.job(kind="schedule", cadence="daily", weekday=None, to="market@example.com"),
            at(2026, 10, 12, 8),
        )
        self.scoped_recipients()
        handler = object.__new__(app_server.ScheduleRequestHandler)
        handler.server = SimpleNamespace(data_path=self.path, open_file=None)
        handler._read_json = lambda maximum: {
            "job_id": job_id, "recipient": "market@example.com", "month": "2026-10", "week": 0,
        }
        response = {}
        handler._json = lambda value, *args, **kwargs: response.update(value)

        def write_synthetic_workbook(output, month, entries, people, *args, **kwargs):
            workbook = Workbook()
            sheet = workbook.active
            for person in people:
                sheet.append([person["name"]])
            workbook.save(output)

        with patch.object(app_server.scheduling, "export_schedule_xlsx", side_effect=write_synthetic_workbook):
            self.assertTrue(mail_automation.handle_request(handler, "POST", "/api/mail-automation/preview-recipient"))
        self.assertEqual(response["recipient"], "market@example.com")
        self.assertEqual(len(response["attachments"]), 1)
        attachment = response["attachments"][0]
        workbook = load_workbook(io.BytesIO(base64.b64decode(attachment["content_base64"], validate=True)),
                                 read_only=True, data_only=True)
        try:
            cells = [str(value) for sheet in workbook.worksheets
                     for row in sheet.iter_rows(values_only=True) for value in row if value is not None]
        finally:
            workbook.close()
        content = "\n".join(cells)
        self.assertIn("Synthetic Market Recipient", content)
        self.assertNotIn("Synthetic Cleaning Recipient", content)
        self.assertIn("Synthetic Market Recipient", attachment["content_preview"])
        self.assertEqual(len(attachment["sha256"]), 64)

    def test_server_rejects_section_scope_escalation_and_inactive_recipients(self):
        job_id = self.prepare(
            self.job(kind="schedule", cadence="daily", weekday=None, to="market@example.com"),
            at(2026, 10, 12, 8),
        )
        market_id, _ = self.scoped_recipients()
        with Store(self.path) as store:
            with self.assertRaisesRegex(ValueError, "bölümüne yetkili"):
                mail_automation._recipient_scopes(store, ["market@example.com"], ["month-package"])
            store.connection.execute("UPDATE employees SET active=0 WHERE id=?", (market_id,))
            store.connection.commit()
            with self.assertRaisesRegex(ValueError, "pasif personele"):
                mail_automation._recipient_scopes(store, ["market@example.com"], ["schedule"])
        handler = object.__new__(app_server.ScheduleRequestHandler)
        handler.server = SimpleNamespace(data_path=self.path, open_file=None)
        handler._read_json = lambda maximum: {
            "job_id": job_id, "recipient": "market@example.com", "month": "2026-10", "week": 0,
        }
        handler._json = MagicMock()
        with self.assertRaisesRegex(ValueError, "pasif personele"):
            mail_automation.handle_request(handler, "POST", "/api/mail-automation/preview-recipient")

    def test_invalid_job_does_not_partially_update_smtp_settings(self):
        with patch.object(mail_automation, "_protect_password", return_value=b"protected"):
            self.save()
            invalid = self.job(hour=24)
            changed_settings = self.settings(password="", enabled=False)
            changed_settings["host"] = "changed.example.com"
            with Store(self.path) as store, self.assertRaises(ValueError):
                mail_automation.save_configuration(
                    store, {"settings": changed_settings, "jobs": [invalid]},
                )
        with Store(self.path) as store:
            row = store.connection.execute(
                "SELECT host FROM mail_delivery_settings WHERE id=1",
            ).fetchone()
            self.assertEqual(row["host"], "smtp.example.com")
            self.assertEqual(
                store.connection.execute("SELECT count(*) FROM mail_automation_jobs").fetchone()[0],
                0,
            )

    def test_due_job_is_sent_once_per_occurrence(self):
        with patch.object(mail_automation, "_protect_password", return_value=b"protected"):
            saved = self.save(jobs=[self.job()])
        moment = datetime(2026, 10, 12, 10, 0).astimezone()
        server = SimpleNamespace(data_path=self.path)
        with patch.object(mail_automation, "_deliver", return_value=SENT) as deliver:
            self.assertEqual(mail_automation.process_due(server, moment), 1)
            self.assertEqual(mail_automation.process_due(server, moment), 0)
        deliver.assert_called_once()
        self.assertEqual(deliver.call_args.args[2], "2026-10")
        with Store(self.path) as store:
            row = store.connection.execute(
                "SELECT last_status, last_occurrence FROM mail_automation_jobs WHERE id=?",
                (saved["jobs"][0]["id"],),
            ).fetchone()
        self.assertEqual(row["last_status"], "sent")
        self.assertEqual(row["last_occurrence"], "weekly:2026-42")

    def test_retry_is_refused_after_scheduler_restart_when_source_revision_changed(self):
        now = at(2026, 10, 12, 9)
        job_id = self.prepare(self.job(cadence="daily", weekday=None), now)
        server = SimpleNamespace(data_path=self.path)
        failure = mail_automation._DeliveryResult("error", "synthetic failure", retryable=True)
        with patch.object(mail_automation, "_deliver", return_value=failure) as deliver:
            self.assertEqual(mail_automation.process_due(server, now), 1)
        deliver.assert_called_once()
        original_revisions = self.job_row(job_id)["source_revisions"]
        with Store(self.path) as store:
            store.connection.execute(
                "INSERT INTO schedule_months(period,payload,revision) VALUES('2026-10','[]','new-synthetic-revision')",
            )
            store.connection.commit()
        restarted_server = SimpleNamespace(data_path=self.path)
        with patch.object(mail_automation, "_deliver") as deliver:
            self.assertEqual(mail_automation.process_due(restarted_server, now + timedelta(minutes=5)), 0)
        deliver.assert_not_called()
        row = self.job_row(job_id)
        self.assertEqual(row["source_revisions"], original_revisions)
        self.assertEqual((row["last_status"], row["pending_month"]), ("revised", ""))
        self.assertIn("Kaynak plan veya puantaj değişti", row["last_error"])
        self.assertEqual(self.history()[0]["status"], "revised")

    def test_previous_period_retry_expires_without_blocking_new_scheduled_period(self):
        first_attempt = at(2026, 10, 31, 9, 0)
        reopened = at(2026, 11, 1, 9, 5)
        job_id = self.prepare(
            self.job(cadence="daily", weekday=None, hour=9, minute=0), first_attempt,
        )
        original_server = SimpleNamespace(data_path=self.path)
        stable_revision = '{"synthetic":"unchanged"}'
        failure = mail_automation._DeliveryResult("error", "synthetic SMTP interruption", retryable=True)
        with patch.object(mail_automation, "_source_revision_snapshot", return_value=stable_revision), \
                patch.object(mail_automation, "_deliver", return_value=failure) as first_delivery:
            self.assertEqual(mail_automation.process_due(original_server, first_attempt), 1)
        first_delivery.assert_called_once()
        pending = self.job_row(job_id)
        self.assertEqual((pending["last_status"], pending["pending_month"]), ("retry", "2026-10"))
        self.assertEqual(pending["source_revisions"], stable_revision)

        restarted_server = SimpleNamespace(data_path=self.path)
        with patch.object(mail_automation, "_source_revision_snapshot", return_value=stable_revision), \
                patch.object(mail_automation, "_deliver", return_value=SENT) as new_delivery:
            self.assertEqual(mail_automation.process_due(restarted_server, reopened), 1)
        new_delivery.assert_called_once()
        self.assertEqual(new_delivery.call_args.args[2], "2026-11")

        current = self.job_row(job_id)
        self.assertEqual(current["last_status"], "sent")
        self.assertEqual(current["last_occurrence"], "daily:2026-11-01")
        self.assertEqual(current["source_revisions"], stable_revision)
        self.assertEqual((current["pending_occurrence"], current["pending_month"], current["next_retry_at"]),
                         ("", "", ""))
        history = self.history()
        self.assertEqual(
            [(entry["source"], entry["status"], entry["month"]) for entry in history[:2]],
            [("scheduled", "sent", "2026-11"), ("retry-expired", "expired", "2026-10")],
        )
        self.assertIn("Önceki dönemin (2026-10)", history[1]["error"])
        self.assertIn("yeni dönem planı engellenmedi", history[1]["error"])

    def test_monthly_report_covers_previous_month_and_requires_due_time(self):
        job = self.job(cadence="monthly", weekday=None, month_day=1)
        before_send_time = datetime(2026, 11, 1, 8, 59).astimezone()
        due_time = datetime(2026, 11, 1, 9, 0).astimezone()
        self.assertIsNone(mail_automation._due_occurrence(job, before_send_time))
        occurrence, month, _ = mail_automation._due_occurrence(job, due_time)
        self.assertEqual((occurrence, month), ("monthly:2026-11", "2026-10"))

    def test_hourly_report_is_due_once_per_hour_at_the_selected_minute(self):
        job = self.job(cadence="hourly", weekday=None, month_day=None, hour=0, minute=15)
        with patch.object(mail_automation, "_protect_password", return_value=b"protected"):
            saved = self.save(jobs=[job])
        server = SimpleNamespace(data_path=self.path)
        with patch.object(mail_automation, "_deliver", return_value=SENT) as deliver:
            self.assertEqual(
                mail_automation.process_due(server, datetime(2026, 10, 12, 10, 14).astimezone()), 0,
            )
            self.assertEqual(
                mail_automation.process_due(server, datetime(2026, 10, 12, 10, 15).astimezone()), 1,
            )
            self.assertEqual(
                mail_automation.process_due(server, datetime(2026, 10, 12, 10, 59).astimezone()), 0,
            )
            self.assertEqual(
                mail_automation.process_due(server, datetime(2026, 10, 12, 11, 15).astimezone()), 1,
            )
        self.assertEqual(deliver.call_count, 2)
        with Store(self.path) as store:
            row = store.connection.execute(
                "SELECT cadence, hour, minute FROM mail_automation_jobs WHERE id=?",
                (saved["jobs"][0]["id"],),
            ).fetchone()
        self.assertEqual((row["cadence"], row["hour"], row["minute"]), ("hourly", 0, 15))

    @contextmanager
    def smtp_replies(self, *replies, use_ssl=False, connect_error=None):
        """Use the real SMTP transaction over an in-memory wire, never a network socket."""
        cls = mail_automation._MailSMTPSSL if use_ssl else mail_automation._MailSMTP
        client = cls(local_hostname="test.invalid")
        wire = client.sock = MagicMock()
        wire.makefile.return_value.readline.side_effect = replies
        client.ehlo_resp = b"test"
        client.login = MagicMock()
        client.starttls = MagicMock()
        handler = app_server.ScheduleRequestHandler
        server = SimpleNamespace(RequestHandlerClass=handler, data_path=self.path, open_file=MagicMock())
        with patch.object(mail_automation, cls.__name__, return_value=client, side_effect=connect_error), \
                patch.object(mail_automation.ssl, "create_default_context"), \
                patch.object(handler, "_export_workbook", return_value=(b"xlsx", "example.xlsx")), \
                patch.object(app_server.tempfile, "gettempdir", return_value=self.temp.name), \
                patch.object(mail_automation, "_unprotect_password", return_value="secret"):
            yield server, wire, client
        server.open_file.assert_not_called()
        self.assertFalse(list(Path(self.temp.name).rglob("*.eml")))

    def test_confirmed_data_is_sent_even_if_quit_disconnects(self):
        now = at(2026, 10, 12, 9)
        job_id = self.prepare(self.job(cadence="daily", body=".first\n.\nlast"), now)
        with self.smtp_replies(b"250 ok", b"250 ok", b"354 go", b"250 queued", b"") as (server, wire, client):
            self.assertEqual(mail_automation.process_due(server, now), 1)
            self.assertEqual(mail_automation.process_due(server, now + timedelta(minutes=6)), 0)
        sent = [call.args[0] for call in wire.sendall.call_args_list]
        self.assertEqual(sent[-1].strip().lower(), b"quit")
        self.assertIn(b"\r\n..first\r\n..\r\nlast\r\n", sent[-2])
        self.assertTrue(sent[-2].endswith(b"\r\n.\r\n"))
        message = BytesParser(policy=policy.default).parsebytes(sent[-2])
        self.assertEqual(message["From"], "reports@example.com")
        self.assertIsNone(message.get("X-Unsent"))
        client.starttls.assert_called_once()
        client.login.assert_called_once_with("reporter", "secret")
        self.assertEqual(self.job_row(job_id)["last_sent_at"], mail_automation._stamp(now))
        self.assertEqual([item["status"] for item in self.history()], ["sent"])

    def test_pre_body_failures_and_explicit_rejections_keep_backoff(self):
        now = at(2026, 10, 12, 9)
        for phase, replies, body_sent in (
            ("connect", [], False),
            ("TLS", [], False),
            ("login", [], False),
            ("MAIL disconnect", [b""], False),
            ("MAIL rejected", [b"550 no", b"250 reset"], False),
            ("RCPT disconnect", [b"250 ok", b""], False),
            ("all RCPT rejected", [b"250 ok", b"550 no", b"250 reset"], False),
            ("no DATA go-ahead", [b"250 ok", b"250 ok", TimeoutError()], False),
            ("DATA refused", [b"250 ok", b"250 ok", b"554 no"], False),
            ("body rejected", [b"250 ok", b"250 ok", b"354 go", b"451 no", b"250 reset"], True),
        ):
            connect_error = ConnectionRefusedError() if phase == "connect" else None
            with self.subTest(phase=phase), self.smtp_replies(*replies, connect_error=connect_error) as (server, wire, client):
                job_id = self.prepare(self.job(cadence="daily"), now)
                if phase == "TLS":
                    client.starttls.side_effect = TimeoutError()
                if phase == "login":
                    client.login.side_effect = smtplib.SMTPAuthenticationError(535, b"test")
                self.assertEqual(mail_automation.process_due(server, now), 1)
                self.assertEqual(any(c.args[0].endswith(b"\r\n.\r\n") for c in wire.sendall.call_args_list), body_sent)
                row = self.job_row(job_id)
                self.assertEqual((row["last_status"], row["next_retry_at"], row["last_sent_at"]),
                                 ("retry", mail_automation._stamp(now + timedelta(minutes=5)), ""))
                self.assertEqual(self.history()[0]["status"], "error")
                self.assertEqual(self.rehearse(now)["plans"][job_id]["retry"]["status"], "retry")

    def test_no_definitive_reply_after_354_is_unknown_not_retried(self):
        now = at(2026, 10, 12, 9)
        for reply in (b"", TimeoutError(), b"malformed", b"354 unexpected", "sendall failed"):
            with self.subTest(reply=reply), self.smtp_replies(b"250 ok", b"250 ok", b"354 go", reply) as (server, wire, _):
                job_id = self.prepare(self.job(cadence="daily"), now)
                if reply == "sendall failed":
                    wire.sendall.side_effect = [None, None, None, ConnectionResetError()]
                self.assertEqual(mail_automation.process_due(server, now), 1)
                self.assertEqual(mail_automation.process_due(server, now + timedelta(hours=2)), 0)
                with Store(self.path) as store:
                    config = mail_automation.get_configuration(store, now)
                job, = config["jobs"]
                entry = config["history"][0]
                review = self.rehearse(now)["plans"][job_id]["retry"]
                self.assertEqual((job["last_status"], entry["status"], review["status"]), ("unknown",) * 3)
                self.assertEqual((job["next_retry_at"], review["next_retry_at"], job["last_sent_at"]), ("", None, ""))
                self.assertTrue(job["can_resend"])
                self.assertIn("mükerrer", entry["error"])
                self.assertEqual((job["last_error"], review["reason"]), (entry["error"],) * 2)

    def test_partial_manual_resend_records_acceptance_and_stops_pending_retry(self):
        now = at(2026, 10, 12, 9)
        job_id = self.prepare(self.job(cadence="daily", to="a@example.com; b@example.com"), now,
                              {**self.settings(), "use_ssl": True})
        with self.smtp_replies(b"421 unavailable", use_ssl=True) as (server, _, _):
            self.assertEqual(mail_automation.process_due(server, now), 1)
        with self.smtp_replies(b"250 ok", b"250 ok", b"354 go", b"250 queued",
                               b"250 ok", b"550 refused", b"250 reset", b"500 quit failed",
                               use_ssl=True) as (server, _, client):
            result = mail_automation.send_now(server, job_id, now + timedelta(minutes=1))
            self.assertEqual(mail_automation.process_due(server, now + timedelta(minutes=5)), 0)
        client.starttls.assert_not_called()
        delivery, job, entry = result["delivery"], result["jobs"][0], result["history"][0]
        review = self.rehearse(now)["plans"][job_id]["retry"]
        self.assertEqual((delivery["status"], job["last_status"], entry["status"], review["status"]), ("partial",) * 4)
        self.assertEqual((entry["source"], job["last_sent_at"], job["next_retry_at"], review["next_retry_at"]),
                         ("manual", "", "", None))
        self.assertTrue(job["can_resend"])
        self.assertIn("2 alıcıdan 1", delivery["error"])
        self.assertIn("mükerrer", delivery["error"])
        with self.smtp_replies(b"250 ok", b"250 ok", b"354 go", b"250 queued",
                               b"250 ok", b"250 ok", b"354 go", b"250 queued", b"221 bye",
                               use_ssl=True) as (server, _, _):
            final = mail_automation.send_now(server, job_id, now + timedelta(minutes=10))
            self.assertEqual(mail_automation.process_due(server, now + timedelta(hours=1)), 0)
        self.assertEqual((final["delivery"]["resend"], final["history"][0]["occurrence"]),
                         (True, "daily:2026-10-12"))
        self.assertEqual([h["status"] for h in final["history"]], ["sent", "partial", "error"])

    def test_partial_scheduled_and_unknown_manual_repeat_leave_next_slot_intact(self):
        now = at(2026, 10, 12, 9)
        job_id = self.prepare(self.job(cadence="daily", to="a@example.com; b@example.com"), now)
        with self.smtp_replies(b"250 ok", b"250 ok", b"354 go", b"250 ok",
                               b"250 ok", b"550 refused", b"250 reset", b"221 bye") as (server, _, _):
            self.assertEqual(mail_automation.process_due(server, now), 1)
            self.assertEqual(mail_automation.process_due(server, now + timedelta(minutes=5)), 0)
        with self.smtp_replies(b"250 ok", b"250 ok", b"354 go", b"250 ok",
                               b"250 ok", b"250 ok", b"354 go", TimeoutError()) as (server, _, _):
            result = mail_automation.send_now(server, job_id, now + timedelta(minutes=6))
            self.assertEqual(mail_automation.process_due(server, now + timedelta(hours=1)), 0)
        self.assertEqual((result["delivery"]["resend"], result["jobs"][0]["last_status"]), (True, "unknown"))
        self.assertEqual([h["status"] for h in result["history"]], ["unknown", "partial"])
        with self.smtp_replies(b"250 ok", b"250 ok", b"354 go", b"250 ok",
                               b"250 ok", b"250 ok", b"354 go", b"250 ok", b"500 bad QUIT") as (server, _, _):
            tomorrow = now + timedelta(days=1)
            self.assertEqual(mail_automation.process_due(server, tomorrow), 1)
            self.assertEqual(mail_automation.process_due(server, tomorrow), 0)
        self.assertEqual((self.history()[0]["status"], self.job_row(job_id)["last_occurrence"]), ("sent", "daily:2026-10-13"))

    def test_manual_unknown_outside_schedule_repeats_original_period_only_by_hand(self):
        now = at(2026, 10, 31, 8)
        job_id = self.prepare(self.job(cadence="monthly", month_day=1), now)
        with self.smtp_replies(b"250 ok", b"250 ok", b"354 go", b"") as (server, _, _):
            result = mail_automation.send_now(server, job_id, now)
            self.assertEqual(mail_automation.process_due(server, now), 0)
        self.assertEqual((result["delivery"]["status"], result["jobs"][0]["last_status"],
                          result["history"][0]["status"]), ("unknown",) * 3)
        self.assertIn("mükerrer", result["delivery"]["error"])
        with self.smtp_replies(b"250 ok", b"250 ok", b"354 go", b"250 ok", b"221 bye") as (server, _, _):
            repeat = mail_automation.send_now(server, job_id, at(2026, 11, 1, 8))
        self.assertEqual((repeat["delivery"]["resend"], repeat["delivery"]["month"]), (True, "2026-09"))
        self.assertEqual(self.job_row(job_id)["last_occurrence"], "")

    def rehearse(self, now):
        return mail_automation.schedule_rehearsal(self.path, app_server.ScheduleRequestHandler.MAIL_KINDS, now)

    def test_rehearsal_is_read_only_sorted_and_uses_scheduler_periods(self):
        now = at(2026, 2, 27, 9)
        with patch.object(mail_automation, "_protect_password", return_value=b"protected"):
            self.save(jobs=[
                self.job(cadence="monthly", month_day=31, kinds=["attendance", "schedule"]),
                self.job(cadence="daily", to="b@example.com; A@example.com", subject="Günlük", body="Metin"),
                self.job(cadence="hourly", minute=15, to="c@example.com"),
                self.job(cadence="weekly", weekday=0),
                self.job(cadence="daily", enabled=False, to="disabled@example.com"),
            ])
        before = self.path.read_bytes()
        with patch.object(mail_automation, "_deliver") as deliver, \
                patch.object(mail_automation, "_unprotect_password") as decrypt, \
                patch.object(mail_automation, "initialize") as initialize:
            result = self.rehearse(now)
            self.assertEqual(result, self.rehearse(now))
        deliver.assert_not_called()
        decrypt.assert_not_called()
        initialize.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(result["until_exclusive"], "2026-03-06")
        self.assertEqual(result["disabled_plans"], 1)
        self.assertNotIn("disabled@example.com", repr(result))
        self.assertNotIn("protected", repr(result))
        slots, plans = result["slots"], result["plans"]
        self.assertEqual([s["local_time"] for s in slots], sorted(s["local_time"] for s in slots))
        self.assertEqual(len(slots), 7 + (7 * 24 - 9) + 1 + 1)
        self.assertEqual(slots[0]["local_time"], "2026-02-27T09:00")
        daily = plans[slots[0]["job_id"]]
        self.assertEqual(daily["recipients"], ["b@example.com", "A@example.com"])
        self.assertEqual(daily["messages"]["2026-02"], {"subject": "Günlük", "body": "Metin"})
        monthly, = [s for s in slots if "schedule" in plans[s["job_id"]]["kinds"]]
        self.assertEqual((monthly["local_time"], monthly["month"], monthly["week"]), ("2026-02-28T09:00", "2026-01", 0))
        weekly, = [s for s in slots if self.job_row(s["job_id"])["cadence"] == "weekly"] if len(slots) < 5 else [
            s for s in slots if plans[s["job_id"]]["recipients"] == ["manager@example.com"] and s != monthly]
        self.assertEqual((weekly["local_time"], weekly["week"]), ("2026-03-02T09:00", 1))
        self.assertEqual(slots[-1]["local_time"], "2026-03-05T23:15")

    def test_rehearsal_message_matches_draft_mime_without_exporting_in_the_preview(self):
        self.prepare(self.job(cadence="daily", kinds=["attendance", "manager-summary"]), at(2026, 10, 1))
        handler = object.__new__(app_server.ScheduleRequestHandler)
        handler.server = SimpleNamespace(open_file=MagicMock())
        handler._export_workbook = MagicMock(return_value=(b"x", "attendance.xlsx"))
        handler._report_file = MagicMock(return_value=(b"p", None, "summary.pdf", "application/pdf"))
        result = self.rehearse(at(2026, 10, 12, 8))
        plan, = result["plans"].values()
        with patch.object(app_server.tempfile, "gettempdir", return_value=self.temp.name):
            draft = handler._mail_draft({"month": "2026-10", "kinds": plan["kinds"],
                                         "to": "; ".join(plan["recipients"])}, open_draft=False)
        message = BytesParser(policy=policy.default).parsebytes(Path(draft["path"]).read_bytes())
        self.assertEqual(plan["messages"]["2026-10"]["subject"], message["Subject"])
        self.assertEqual(plan["messages"]["2026-10"]["body"], message.get_body().get_content().rstrip("\n"))
        self.assertEqual([part.get_filename() for part in message.iter_attachments()], ["attendance.xlsx", "summary.pdf"])
        handler.server.open_file.assert_not_called()

    def test_rehearsal_no_schema_creation_and_no_slots_when_paused(self):
        missing = Path(self.temp.name) / "absent.sqlite3"
        labels = app_server.ScheduleRequestHandler.MAIL_KINDS
        self.assertEqual(mail_automation.schedule_rehearsal(missing, labels)["slots"], [])
        self.assertFalse(missing.exists())
        with closing(sqlite3.connect(missing)) as conn:
            conn.execute("CREATE TABLE unrelated(id INTEGER)")
        before = missing.read_bytes()
        self.assertFalse(mail_automation.schedule_rehearsal(missing, labels)["ready"])
        self.assertEqual(missing.read_bytes(), before)
        job_id = self.prepare(self.job(cadence="daily"), at(2026, 10, 1))
        with Store(self.path) as store:
            store.connection.execute("UPDATE mail_delivery_settings SET enabled=0")
            store.connection.commit()
        result = self.rehearse(at(2026, 10, 2))
        self.assertEqual(result["slots"], [])
        self.assertIn(job_id, result["plans"])
        self.assertFalse(result["ready"])

    def test_rehearsal_route_uses_existing_share_gate_and_only_get(self):
        handler = SimpleNamespace(server=SimpleNamespace(data_path=self.path, sessions={"s": {"username": "Operator"}}),
                                  _is_internal=lambda: False, _session_token=lambda: "s", _json=MagicMock(),
                                  path="/api/mail-automation?view=rehearsal", MAIL_KINDS=app_server.ScheduleRequestHandler.MAIL_KINDS)
        for permissions in ([], ["attendance.read"], ["share"]):
            with patch.object(Store, "users", return_value=[{"username": "Operator", "role": "operator", "permissions": permissions}]):
                self.assertEqual(access_services.authorize(handler, "GET", "/api/mail-automation", {"view": ["rehearsal"]}),
                                 permissions == ["share"])
        self.assertTrue(mail_automation.handle_request(handler, "GET", "/api/mail-automation"))
        self.assertIn("slots", handler._json.call_args.args[0])
        with self.assertRaises(ValueError):
            mail_automation.handle_request(handler, "PUT", "/api/mail-automation")
        handler.path += "&view=other"
        with self.assertRaises(ValueError):
            mail_automation.handle_request(handler, "GET", "/api/mail-automation")

    def test_review_explains_retries_and_a_manual_resend_supersedes_stale_claim(self):
        now = at(2026, 10, 12, 9)
        job_id = self.prepare(self.job(cadence="daily"), now - timedelta(days=1))
        server = SimpleNamespace(data_path=self.path)
        with patch.object(mail_automation, "_deliver", side_effect=smtplib.SMTPAuthenticationError(535, b"bad")):
            mail_automation.process_due(server, now)
            retry_time = now + timedelta(minutes=5)
            stale_plan = mail_automation._plan_attempt(self.job_row(job_id), retry_time)
            result = self.rehearse(now)
            review = result["plans"][job_id]["retry"]
            self.assertIn("kimlik", review["reason"])
            self.assertEqual(review["automatic_attempts"], 1)
            self.assertEqual(review["next_retry_at"], mail_automation._stamp(retry_time))
            self.assertTrue(all(s["local_time"] > "2026-10-12T09:00" for s in result["slots"]))
            mail_automation.send_now(server, job_id, retry_time)
            self.assertIsNone(mail_automation._claim(self.path, job_id, stale_plan, retry_time))
            self.assertEqual(self.rehearse(retry_time)["plans"][job_id]["retry"]["next_retry_at"],
                             mail_automation._stamp(now + timedelta(minutes=10)))

    def test_pending_old_scope_is_not_retried_with_new_recipients(self):
        now = at(2026, 10, 12, 9)
        job_id = self.prepare(self.job(cadence="daily"), now - timedelta(days=1))
        server = SimpleNamespace(data_path=self.path)
        with patch.object(mail_automation, "_deliver", side_effect=OSError("offline")):
            mail_automation.process_due(server, now)
        later = now + timedelta(minutes=5)
        old_claim = mail_automation._plan_attempt(self.job_row(job_id), later)
        self.save(settings=self.settings(password=""), jobs=[self.job(id=job_id, cadence="daily", to="new@example.com")])
        self.assertIsNone(mail_automation._claim(self.path, job_id, old_claim, later))
        self.assertEqual(self.job_row(job_id)["pending_occurrence"], "")
        self.assertIn("iptal", self.rehearse(later)["plans"][job_id]["retry"]["reason"])
        with patch.object(mail_automation, "_deliver") as deliver:
            self.assertEqual(mail_automation.process_due(server, later), 0)
        deliver.assert_not_called()

    def test_oversized_combined_attachments_block_before_smtp_and_do_not_retry(self):
        now = at(2026, 10, 12, 9)
        job_id = self.prepare(self.job(cadence="daily", kinds=["attendance", "manager-summary"]), now - timedelta(days=1))
        handler = app_server.ScheduleRequestHandler
        server = SimpleNamespace(RequestHandlerClass=handler, data_path=self.path, open_file=MagicMock())
        with patch.object(app_server.tempfile, "gettempdir", return_value=self.temp.name), \
                patch.object(handler, "_export_workbook", return_value=(b"123", "a.xlsx")), \
                patch.object(handler, "_report_file", return_value=(b"456", None, "b.pdf", "application/pdf")), \
                patch.object(mail_automation, "MAX_ATTACHMENT_BYTES", 5), \
                patch.object(mail_automation, "_unprotect_password") as decrypt, \
                patch.object(mail_automation, "_MailSMTP") as smtp, patch.object(mail_automation, "_MailSMTPSSL") as smtp_ssl:
            self.assertEqual(mail_automation.process_due(server, now), 1)
            self.assertEqual(mail_automation.process_due(server, now + timedelta(minutes=10)), 0)
        decrypt.assert_not_called()
        smtp.assert_not_called()
        smtp_ssl.assert_not_called()
        server.open_file.assert_not_called()
        row = self.job_row(job_id)
        self.assertEqual((row["last_status"], row["next_retry_at"]), ("error", ""))
        self.assertIn("ayrı planlara", row["last_error"])
        self.assertFalse(list(Path(self.temp.name).rglob("*.eml")))

    def test_mail_automation_routes_require_share_permission(self):
        with Store(self.path) as store:
            access_services.initialize(store.connection)
            store.save_user("Admin", "abcdef", "admin")
            store.save_user("Operator", "abcdef", "operator")
        server = SimpleNamespace(
            data_path=self.path,
            sessions={"session": {"username": "Operator", "role": "operator", "permissions": []}},
        )
        handler = SimpleNamespace(
            server=server, current_user=None, _is_internal=lambda: False,
            _session_token=lambda: "session", _json=lambda result, status=200: None,
        )
        self.assertFalse(access_services.authorize(handler, "GET", "/api/mail-automation", {}))
        self.assertFalse(access_services.authorize(handler, "PUT", "/api/mail-automation", {}))
        server.sessions["session"] = {"username": "Admin", "role": "admin", "permissions": []}
        self.assertTrue(access_services.authorize(handler, "GET", "/api/mail-automation", {}))
        self.assertTrue(access_services.authorize(handler, "PUT", "/api/mail-automation", {}))

    def test_send_now_route_requires_share_permission(self):
        server = SimpleNamespace(data_path=self.path, sessions={"session": {"username": "Operator"}})
        handler = SimpleNamespace(
            server=server, current_user=None, _is_internal=lambda: False,
            _session_token=lambda: "session", _json=lambda result, status=200: None,
        )
        for permissions, allowed in (([], False), (["share"], True)):
            user = {"username": "Operator", "role": "operator", "permissions": permissions}
            with self.subTest(permissions=permissions), patch.object(Store, "users", return_value=[user]):
                self.assertEqual(
                    access_services.authorize(handler, "POST", "/api/mail-automation/send-now", {}), allowed,
                )

    def test_failed_delivery_is_retried_with_backoff_then_marked_error(self):
        job_id = self.prepare(self.job(cadence="daily", weekday=None), at(2026, 10, 12, 8, 0))
        server = SimpleNamespace(data_path=self.path)
        start = at(2026, 10, 12, 9, 0)
        with patch.object(mail_automation, "_deliver", side_effect=smtplib.SMTPException("down")) as deliver:
            self.assertEqual(mail_automation.process_due(server, start), 1)
            row = self.job_row(job_id)
            self.assertEqual((row["last_status"], row["attempts"]), ("retry", 1))
            self.assertEqual(row["next_retry_at"], mail_automation._stamp(start + timedelta(minutes=5)))
            # Retries follow 5, 15 and 60 minute delays; nothing more is sent that day.
            for minutes, expected in ((4, 0), (5, 1), (19, 0), (20, 1), (79, 0), (80, 1), (200, 0), (840, 0)):
                with self.subTest(minutes=minutes):
                    self.assertEqual(
                        mail_automation.process_due(server, start + timedelta(minutes=minutes)), expected,
                    )
        self.assertEqual(deliver.call_count, 4)
        row = self.job_row(job_id)
        self.assertEqual((row["last_status"], row["attempts"], row["next_retry_at"]), ("error", 4, ""))
        self.assertIn("SMTP", row["last_error"])
        history = list(reversed(self.history()))
        self.assertEqual(
            [(entry["source"], entry["attempt"], entry["status"]) for entry in history],
            [("scheduled", 1, "error"), ("retry", 2, "error"), ("retry", 3, "error"), ("retry", 4, "error")],
        )
        self.assertTrue(all(entry["occurrence"] == "daily:2026-10-12" for entry in history))

    def test_successful_retry_is_not_sent_twice(self):
        job_id = self.prepare(self.job(cadence="daily", weekday=None), at(2026, 10, 12, 8, 0))
        server = SimpleNamespace(data_path=self.path)
        start = at(2026, 10, 12, 9, 0)
        with patch.object(mail_automation, "_deliver", side_effect=[OSError("offline"), SENT]) as deliver:
            for minutes, expected in ((0, 1), (5, 1), (6, 0), (90, 0)):
                self.assertEqual(mail_automation.process_due(server, start + timedelta(minutes=minutes)), expected)
        self.assertEqual(deliver.call_count, 2)
        self.assertEqual(deliver.call_args.args[2], "2026-10")
        row = self.job_row(job_id)
        self.assertEqual(
            (row["last_status"], row["pending_occurrence"], row["last_occurrence"]),
            ("sent", "", "daily:2026-10-12"),
        )
        self.assertEqual(row["last_sent_at"], mail_automation._stamp(start + timedelta(minutes=5)))
        self.assertEqual(
            [(entry["source"], entry["status"]) for entry in self.history()],
            [("retry", "sent"), ("scheduled", "error")],
        )

    def test_resend_now_delivers_the_failed_occurrence_once(self):
        job_id = self.prepare(self.job(cadence="daily", weekday=None), at(2026, 10, 12, 8, 0))
        server = SimpleNamespace(data_path=self.path)
        start = at(2026, 10, 12, 9, 0)
        later = start + timedelta(hours=2)
        with patch.object(mail_automation, "_deliver", side_effect=OSError("offline")):
            for minutes in (0, 5, 20, 80):
                mail_automation.process_due(server, start + timedelta(minutes=minutes))
        with Store(self.path) as store:
            view = mail_automation.get_configuration(store, later)["jobs"][0]
        self.assertEqual((view["last_status"], view["can_resend"]), ("error", True))
        exhausted = self.rehearse(later)["plans"][job_id]["retry"]
        self.assertEqual((exhausted["status"], exhausted["next_retry_at"]), ("error", None))
        with patch.object(mail_automation, "_deliver", return_value=SENT) as deliver:
            result = mail_automation.send_now(server, job_id, later)
            self.assertEqual(mail_automation.process_due(server, later + timedelta(hours=1)), 0)
        deliver.assert_called_once()
        self.assertEqual(deliver.call_args.args[2], "2026-10")
        self.assertEqual(result["delivery"], {"status": "sent", "error": "", "resend": True, "month": "2026-10"})
        self.assertEqual((result["jobs"][0]["last_status"], result["jobs"][0]["can_resend"]), ("sent", False))
        entry = result["history"][0]
        self.assertEqual(
            (entry["source"], entry["occurrence"], entry["status"]), ("manual", "daily:2026-10-12", "sent"),
        )

    def test_failed_manual_resend_keeps_the_automatic_retries(self):
        job_id = self.prepare(self.job(cadence="daily", weekday=None), at(2026, 10, 12, 8, 0))
        server = SimpleNamespace(data_path=self.path)
        start = at(2026, 10, 12, 9, 0)
        with patch.object(mail_automation, "_deliver", side_effect=OSError("offline")) as deliver:
            mail_automation.process_due(server, start)
            result = mail_automation.send_now(server, job_id, start + timedelta(minutes=3))
            self.assertEqual(mail_automation.process_due(server, start + timedelta(minutes=7)), 0)
            self.assertEqual(mail_automation.process_due(server, start + timedelta(minutes=8)), 1)
        self.assertEqual(deliver.call_count, 3)
        self.assertEqual(result["delivery"]["status"], "error")
        self.assertEqual(result["jobs"][0]["last_status"], "retry")
        row = self.job_row(job_id)
        self.assertEqual((row["last_status"], row["attempts"]), ("retry", 2))

    def test_send_now_counts_a_due_occurrence_but_not_an_extra_send(self):
        job_id = self.prepare(self.job(cadence="daily", weekday=None), at(2026, 10, 12, 12, 0))
        server = SimpleNamespace(data_path=self.path)
        with patch.object(mail_automation, "_deliver", return_value=SENT) as deliver:
            extra = mail_automation.send_now(server, job_id, at(2026, 10, 13, 8, 0))
            self.assertEqual(self.job_row(job_id)["last_occurrence"], "")
            mail_automation.send_now(server, job_id, at(2026, 10, 13, 9, 30))
            self.assertEqual(mail_automation.process_due(server, at(2026, 10, 13, 9, 31)), 0)
        self.assertEqual(deliver.call_count, 2)
        self.assertEqual(extra["delivery"], {"status": "sent", "error": "", "resend": False, "month": "2026-10"})
        self.assertEqual(
            [(entry["source"], entry["occurrence"]) for entry in self.history()],
            [("manual", "daily:2026-10-13"), ("manual", "")],
        )

    def test_send_now_requires_an_enabled_idle_plan_and_active_smtp(self):
        server = SimpleNamespace(data_path=self.path)
        with patch.object(mail_automation, "_protect_password", return_value=b"protected"):
            job_id = self.save(
                settings=self.settings(enabled=False), jobs=[self.job(enabled=False)],
            )["jobs"][0]["id"]
        with patch.object(mail_automation, "_deliver") as deliver:
            with self.assertRaisesRegex(ValueError, "SMTP"):
                mail_automation.send_now(server, job_id)
            with patch.object(mail_automation, "_protect_password", return_value=b"protected"):
                self.save(jobs=[self.job(id=job_id, enabled=False)])
            for value, message in (("../x", "bulunamadı"), ("a" * 24, "bulunamadı"), (job_id, "Pasif")):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                    mail_automation.send_now(server, value)
            with patch.object(mail_automation, "_protect_password", return_value=b"protected"):
                self.save(jobs=[self.job(id=job_id)])
            now = at(2026, 10, 12, 9, 0)
            with Store(self.path) as store:
                store.connection.execute(
                    "UPDATE mail_automation_jobs SET last_status='sending', last_attempt_at=?",
                    (mail_automation._stamp(now),),
                )
                store.connection.commit()
            with self.assertRaisesRegex(ValueError, "gönderiliyor"):
                mail_automation.send_now(server, job_id, now + timedelta(minutes=1))
            handler = SimpleNamespace(
                server=server, _read_json=lambda maximum: {"job_id": job_id, "force": True}, _json=MagicMock(),
            )
            with self.assertRaisesRegex(ValueError, "Gönderim isteği"):
                mail_automation.handle_request(handler, "POST", "/api/mail-automation/send-now")
        deliver.assert_not_called()

    def test_interrupted_delivery_is_not_resent_automatically_but_can_be_resent_by_hand(self):
        job_id = self.prepare(self.job(cadence="daily", weekday=None), at(2026, 10, 12, 8, 0))
        server = SimpleNamespace(data_path=self.path)
        start = at(2026, 10, 12, 9, 0)
        with Store(self.path) as store:
            store.connection.execute(
                "UPDATE mail_automation_jobs SET last_status='sending', last_occurrence=?, "
                "pending_occurrence=?, pending_month='2026-10', source_revisions=?, attempts=1, last_attempt_at=?",
                ("daily:2026-10-12", "daily:2026-10-12",
                 mail_automation._source_revision_snapshot(store, "2026-10"), mail_automation._stamp(start)),
            )
            store.connection.commit()
        later = start + timedelta(minutes=30)
        with patch.object(mail_automation, "_deliver", return_value=SENT) as deliver:
            self.assertEqual(mail_automation.process_due(server, later), 0)
            with Store(self.path) as store:
                view = mail_automation.get_configuration(store, later)["jobs"][0]
            self.assertEqual((view["last_status"], view["can_resend"]), ("unknown", True))
            interrupted = self.rehearse(later)["plans"][job_id]["retry"]
            self.assertEqual((interrupted["status"], interrupted["next_retry_at"]), ("unknown", None))
            self.assertIn("mükerrer", interrupted["reason"])
            result = mail_automation.send_now(server, job_id, later)
        deliver.assert_called_once()
        self.assertTrue(result["delivery"]["resend"])
        self.assertEqual(self.job_row(job_id)["last_status"], "sent")

    def test_missed_daily_occurrence_is_caught_up_once_after_the_app_starts(self):
        self.prepare(self.job(cadence="daily", weekday=None), at(2026, 10, 1, 8, 0))
        server = SimpleNamespace(data_path=self.path)
        with patch.object(mail_automation, "_deliver", return_value=SENT) as deliver:
            # The app was closed from Oct 9 until Oct 13 08:00: only the latest missed day is sent.
            self.assertEqual(mail_automation.process_due(server, at(2026, 10, 13, 8, 0)), 1)
            self.assertEqual(mail_automation.process_due(server, at(2026, 10, 13, 8, 30)), 0)
            self.assertEqual(mail_automation.process_due(server, at(2026, 10, 13, 9, 0)), 1)
        self.assertEqual(deliver.call_count, 2)
        self.assertEqual(
            [(entry["source"], entry["occurrence"]) for entry in self.history()],
            [("scheduled", "daily:2026-10-13"), ("catch-up", "daily:2026-10-12")],
        )

    def test_occurrences_before_activation_or_in_past_hours_are_not_caught_up(self):
        server = SimpleNamespace(data_path=self.path)
        with patch.object(mail_automation, "_deliver", return_value=SENT) as deliver:
            self.prepare(self.job(cadence="daily", weekday=None), at(2026, 10, 12, 12, 0))
            self.assertEqual(mail_automation.process_due(server, at(2026, 10, 13, 8, 0)), 0)
            self.prepare(self.job(cadence="hourly", weekday=None, minute=15), at(2026, 10, 1, 0, 0))
            self.assertEqual(mail_automation.process_due(server, at(2026, 10, 13, 11, 5)), 0)
            self.assertEqual(mail_automation.process_due(server, at(2026, 10, 13, 11, 15)), 1)
        deliver.assert_called_once()

    def test_missed_weekly_occurrence_is_caught_up_later_in_the_week(self):
        self.prepare(self.job(), at(2026, 10, 5, 12, 0))
        server = SimpleNamespace(data_path=self.path)
        with patch.object(mail_automation, "_deliver", return_value=SENT) as deliver:
            self.assertEqual(mail_automation.process_due(server, at(2026, 10, 17, 10, 0)), 1)
            self.assertEqual(mail_automation.process_due(server, at(2026, 10, 18, 10, 0)), 0)
            self.assertEqual(mail_automation.process_due(server, at(2026, 10, 19, 9, 0)), 1)
        self.assertEqual(deliver.call_count, 2)
        self.assertEqual(
            [(entry["source"], entry["occurrence"]) for entry in self.history()],
            [("scheduled", "weekly:2026-43"), ("catch-up", "weekly:2026-42")],
        )

    def test_monthly_day_31_runs_on_the_last_day_and_is_caught_up(self):
        job = self.job(cadence="monthly", weekday=None, month_day=31)
        self.assertEqual(
            mail_automation._latest_occurrence(job, datetime(2026, 11, 30, 10, 0)), datetime(2026, 11, 30, 9, 0),
        )
        self.assertEqual(
            mail_automation._latest_occurrence(job, datetime(2026, 3, 1, 8, 0)), datetime(2026, 2, 28, 9, 0),
        )
        self.assertEqual(
            mail_automation._next_occurrence(job, datetime(2026, 11, 30, 10, 0)), datetime(2026, 12, 31, 9, 0),
        )
        self.assertEqual(
            mail_automation._due_occurrence(job, at(2026, 11, 30, 9, 0))[:2], ("monthly:2026-11", "2026-10"),
        )
        self.prepare(job, at(2026, 11, 2, 12, 0))
        server = SimpleNamespace(data_path=self.path)
        with patch.object(mail_automation, "_deliver", return_value=SENT) as deliver:
            # Closed for all of Nov 30, opened on Dec 1.
            self.assertEqual(mail_automation.process_due(server, at(2026, 12, 1, 8, 0)), 1)
            self.assertEqual(mail_automation.process_due(server, at(2026, 12, 5, 8, 0)), 0)
        deliver.assert_called_once()
        self.assertEqual(deliver.call_args.args[2], "2026-10")
        self.assertEqual(self.history()[0]["occurrence"], "monthly:2026-11")

    def test_history_keeps_only_the_latest_200_entries(self):
        job = {"id": "a" * 24, "kind": "attendance", "recipients": "a@example.com; b@example.com"}
        now = at(2026, 10, 12, 9, 0)
        with Store(self.path) as store:
            for index in range(205):
                mail_automation._add_history(
                    store.connection, now, job, "scheduled", f"daily:{index}", "2026-10", 1,
                    mail_automation._DeliveryResult("error", "Hata") if index % 2 else SENT,
                )
            store.connection.commit()
            count = store.connection.execute("SELECT count(*) FROM mail_delivery_history").fetchone()[0]
            configuration = mail_automation.get_configuration(store, now)
        self.assertEqual(count, 200)
        history = configuration["history"]
        self.assertEqual((len(history), history[0]["occurrence"], history[-1]["occurrence"]),
                         (200, "daily:204", "daily:5"))
        self.assertEqual(
            (history[0]["recipients"], history[0]["kinds"], history[0]["status"], history[1]["status"]),
            (2, ["attendance"], "sent", "error"),
        )
        self.assertEqual(configuration["retry_policy"], {"delays_minutes": [5, 15, 60], "max_retries": 3})

    def test_plan_with_several_attachments_sends_all_of_them(self):
        kinds = ["attendance", "month-package"]
        self.prepare(self.job(cadence="daily", weekday=None, kinds=kinds), at(2026, 10, 12, 8, 0))
        with Store(self.path) as store:
            view = mail_automation.get_configuration(store)["jobs"][0]
        self.assertEqual((view["kind"], view["kinds"]), ("attendance", kinds))
        with patch.object(mail_automation, "_deliver", return_value=SENT) as deliver:
            mail_automation.process_due(SimpleNamespace(data_path=self.path), at(2026, 10, 12, 9, 0))
        self.assertEqual(mail_automation._job_kinds(deliver.call_args.args[1]), kinds)
        self.assertEqual(self.history()[0]["kinds"], kinds)
        for invalid in (["schedule"], ["attendance", "attendance"], []):
            with self.subTest(kinds=invalid), self.assertRaises(ValueError), \
                    patch.object(mail_automation, "_protect_password", return_value=b"protected"):
                self.save(jobs=[self.job(kinds=invalid)])

    def test_draft_kinds_validation(self):
        self.assertEqual(
            mail_automation.draft_kinds({"kinds": ["attendance", "month-package"]}),
            ["attendance", "month-package"],
        )
        self.assertEqual(mail_automation.draft_kinds({"kind": "schedule"}), ["schedule"])
        for payload, message in (
            ({"kinds": []}, "En az bir"),
            ({"kinds": "attendance"}, "En az bir"),
            ({"kinds": ["attendance", "attendance"]}, "Aynı"),
            ({"kinds": ["attendance", "payroll"]}, "geçersiz"),
            ({"kinds": ["attendance", "schedule", "manager-summary", "month-package", "attendance"]}, "En fazla 4"),
            ({"kind": "attendance", "kinds": ["schedule"]}, "taslağı"),
            ({}, "taslağı"),
            ({"kind": "payroll"}, "geçersiz"),
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, message):
                mail_automation.draft_kinds(payload)

    def test_mail_draft_attaches_every_selected_report(self):
        handler = object.__new__(app_server.ScheduleRequestHandler)
        handler.server = SimpleNamespace(open_file=None)
        handler._export_workbook = lambda query: (b"xlsx", f"{query['kind'][0].upper()}_2026-10.xlsx")
        handler._report_file = lambda path, query: (
            b"%PDF-1.4", None, "YONETICI_OZETI_2026-10.pdf", "application/pdf",
        )
        with patch.object(app_server.tempfile, "gettempdir", return_value=self.temp.name):
            result = handler._mail_draft(
                {"kinds": ["attendance", "manager-summary"], "month": "2026-10", "to": "ik@example.com"},
                open_draft=False,
            )
            legacy = handler._mail_draft({"kind": "attendance", "month": "2026-10"}, open_draft=False)
            with self.assertRaisesRegex(ValueError, "Aynı"):
                handler._mail_draft({"kinds": ["attendance", "attendance"], "month": "2026-10"}, open_draft=False)
        message = BytesParser(policy=policy.default).parsebytes(Path(result["path"]).read_bytes())
        self.assertEqual(result["attachments"], ["ATTENDANCE_2026-10.xlsx", "YONETICI_OZETI_2026-10.pdf"])
        self.assertEqual([part.get_filename() for part in message.iter_attachments()], result["attachments"])
        self.assertEqual(message["Subject"], "Puantaj, Yönetici özeti - EKİM 2026")
        self.assertIn("- Yönetici özeti (PDF)", message.get_body(("plain",)).get_content())
        self.assertEqual(legacy["attachments"], ["ATTENDANCE_2026-10.xlsx"])

    def test_initialize_upgrades_an_existing_jobs_table(self):
        with closing(sqlite3.connect(Path(self.temp.name) / "old.sqlite3")) as connection:
            connection.row_factory = sqlite3.Row
            connection.executescript(
                """
                CREATE TABLE mail_automation_jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, cadence TEXT NOT NULL, weekday INTEGER,
                    month_day INTEGER, hour INTEGER NOT NULL, minute INTEGER NOT NULL,
                    recipients TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
                    enabled INTEGER NOT NULL, last_occurrence TEXT NOT NULL DEFAULT '',
                    last_status TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                INSERT INTO mail_automation_jobs VALUES ('aaaaaaaaaaaaaaaaaaaaaaaa', 'schedule', 'daily',
                    NULL, NULL, 9, 0, 'a@example.com', '', '', 1, 'daily:2026-10-11', 'sent', '',
                    '2026-10-01T08:00:00+03:00');
                """
            )
            mail_automation.initialize(connection)
            mail_automation.initialize(connection)
            row = connection.execute("SELECT * FROM mail_automation_jobs").fetchone()
            history_rows = connection.execute("SELECT count(*) FROM mail_delivery_history").fetchone()[0]
        self.assertEqual(row["active_since"], "2026-10-01T08:00:00+03:00")
        self.assertEqual((row["attempts"], row["pending_occurrence"], row["last_status"]), (0, "", "sent"))
        self.assertEqual(mail_automation._job_kinds(row), ["schedule"])
        self.assertEqual(history_rows, 0)


if __name__ == "__main__":
    unittest.main()
