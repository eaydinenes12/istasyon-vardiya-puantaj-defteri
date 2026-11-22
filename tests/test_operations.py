import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

import linked_leave
import compliance
import document_workflows
import operations
import planning
import personnel_assets
import payroll
import scheduling
from storage import Store


class OperationsTests(unittest.TestCase):
    def test_hourly_staffing_counts_unique_people_and_partial_hour_gaps(self):
        night = {"kind": "work", "start": "22:00", "end": "01:15"}
        relief = {"kind": "work", "start": "00:30", "end": "02:00"}
        entries = [
            {"employee_id": 1, "section": "MARKET", "days": {"2026-09-30": night}},
            {"employee_id": 1, "section": "MARKET", "days": {"2026-09-30": night}},
            {"employee_id": 2, "section": "MARKET", "days": {"2026-10-01": relief}},
        ]
        rows = compliance.hourly_staffing(date(2026, 10, 1), entries, [
            {"section": "MARKET", "start": "00:15", "end": "01:30", "minimum": 2},
        ])
        self.assertEqual([(row["start"], row["end"], row["lowest"]) for row in rows],
                         [("00:15", "01:00", 1), ("01:00", "01:30", 1)])
        self.assertEqual([row["gaps"] for row in rows], [
            [{"start": "00:15", "end": "00:30", "shortfall": 1}],
            [{"start": "01:15", "end": "01:30", "shortfall": 1}],
        ])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "vardiya.sqlite3"
        self.store = Store(self.path)
        operations.initialize(self.store.connection)
        self.people = self.store.add_people(["Ada Operasyon", "Bora Yedek"])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def save_schedule(self, entries, month="2026-10"):
        normalized = scheduling.normalize_schedule_entries(month, entries, self.store.people())
        revision = self.store.schedule_month(month)["revision"]
        return self.store.save_schedule_month(month, normalized, revision)

    def test_trends_include_previous_months_not_future_months(self):
        handler = SimpleNamespace(_month_attendance=lambda store, month: (
            scheduling.calculate_attendance(month, [], {}, []), []
        ))
        result = operations.trends(handler, self.store, "2026-02", 3)
        self.assertEqual([item["month"] for item in result["months"]], ["2025-12", "2026-01", "2026-02"])
        self.assertEqual([(item["metrics"]["calendar_days"], item["metrics"]["normal_weekdays"],
                           item["metrics"]["work_days"]) for item in result["months"]],
                         [(31, 23, 0), (31, 21, 0), (28, 20, 0)])

    def test_staffing_trends_group_recurring_daily_gaps_with_source_revisions(self):
        ada = self.people[0]["id"]
        self.store.save_compliance_settings({"min_staffing": {"MARKET": 1}})
        self.save_schedule([{"row_id": "ada", "employee_id": ada, "section": "MARKET", "days": {
            "2026-10-05": {"kind": "work", "start": "09:00", "end": "17:00"},
            "2026-10-12": {"kind": "work", "start": "09:00", "end": "17:00"},
        }}])

        report = operations.staffing_patterns(self.store, ["2026-10"])
        monday = next(item for item in report["patterns"]
                      if item["weekday"] == "Pazartesi")
        self.assertEqual((monday["kind"], monday["section"], monday["pattern"]), (
            "daily", "MARKET", "repeated",
        ))
        self.assertEqual(monday["dates"], ["2026-10-19", "2026-10-26"])
        self.assertEqual((monday["occurrence_count"], monday["week_count"]), (2, 2))
        self.assertEqual(monday["source_revisions"], [{
            "month": "2026-10", "revision": self.store.schedule_month("2026-10")["revision"],
        }])

    def test_publish_diff_groups_by_date_and_section_and_uses_only_recorded_emergency_reason(self):
        ada = self.people[0]["id"]
        before = [{"row_id": "ada", "employee_id": ada, "section": "MARKET", "days": {
            "2026-10-12": {"kind": "work", "start": "09:00", "end": "17:00"},
        }}]
        with self.store.connection:
            self.store._write_setting("published_plans", {"2026-10": {
                "entries": before, "source_revision": "published-rev", "at": "2026-10-01T10:00:00+00:00",
            }})
        self.save_schedule([{"row_id": "ada", "employee_id": ada, "section": "MARKET", "days": {
            "2026-10-12": {"kind": "work", "start": "10:00", "end": "18:00"},
        }}])
        diff = operations.publish_diff(self.store, "2026-10")
        self.assertEqual(len(diff["change_groups"]), 1)
        group = diff["change_groups"][0]
        self.assertEqual((group["date"], group["section"]), ("2026-10-12", "MARKET"))
        self.assertEqual(group["recorded_reasons"], [])

        with self.store.connection:
            self.store.connection.execute(
                "INSERT INTO operations_emergency_audit "
                "(id,month,employee_id,replacement_id,date,reason,source_revision,before_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("emergency-1", "2026-10", ada, self.people[1]["id"], "2026-10-12",
                 "Acil sağlık izni", "source-rev", "{}", "2026-10-12T08:00:00+00:00"),
            )
        with_reason = operations.publish_diff(self.store, "2026-10")["change_groups"][0]
        self.assertEqual(with_reason["recorded_reasons"], [{
            "id": "emergency-1", "reason": "Acil sağlık izni",
        }])

    def test_accounting_reconciliation_finds_person_day_mismatch_when_totals_match(self):
        attendance = {"rows": [
            {"employee_id": 1, "name": "Ada", "codes": {"2026-10-12": "X"},
             "worked_hours_by_day": {"2026-10-12": 8.0}},
            {"employee_id": 2, "name": "Bora", "codes": {"2026-10-12": "H"},
             "worked_hours_by_day": {}},
        ]}
        content = (
            "employee_id,date,code,hours\n"
            "1,2026-10-12,H,0\n"
            "2,2026-10-12,X,8\n"
        )
        report = payroll.reconcile_accounting_csv(
            "2026-10", attendance, {}, "..\\synthetic-accounting.csv", content,
        )
        self.assertEqual(report["file"], "synthetic-accounting.csv")
        self.assertTrue(report["hours_total_equal"])
        self.assertTrue(report["code_totals_equal"])
        self.assertFalse(report["matches"])
        self.assertEqual(report["difference_count"], 2)
        self.assertEqual([item["name"] for item in report["differences"]], ["Ada", "Bora"])
        self.assertEqual([field["field"] for field in report["differences"][0]["fields"]], ["code", "hours"])
        self.assertIn("ücret hesabı yapılmadı", report["scope"])
        with self.assertRaisesRegex(ValueError, "birden fazla kez"):
            payroll.reconcile_accounting_csv(
                "2026-10", attendance, {}, "dupe.csv",
                "employee_id,date,code,hours\n1,2026-10-12,X,8\n1,2026-10-12,X,8\n",
            )

    def test_fairness_keeps_shift_counts_and_adds_availability_period_context(self):
        ada, bora = [person["id"] for person in self.people]
        entries = {"2026-10": [
            {"employee_id": ada, "section": "MARKET", "days": {
                "2026-10-12": {"kind": "work", "start": "22:00", "end": "06:00"},
            }},
            {"employee_id": bora, "section": "MARKET", "days": {
                "2026-10-11": {"kind": "work", "start": "09:00", "end": "17:00"},
            }},
        ]}
        profiles = {str(ada): {
            "hire_date": "2026-10-01", "exit_date": "2026-10-31",
            "preferences": {
                "unavailable_weekdays": [0],
                "unavailable_ranges": [{"start": "2026-10-14", "end": "2026-10-15"}],
                "no_night": True, "no_weekend": True,
            },
        }}
        report = planning.fairness(entries, {ada: "Ada Operasyon", bora: "Bora Yedek"}, profiles)
        ada_row = next(row for row in report["people"] if row["employee_id"] == ada)
        self.assertEqual((ada_row["night"], ada_row["weekend"]), (1, 0))
        availability = ada_row["availability"]
        self.assertFalse(availability["confirmed"])
        self.assertEqual(availability["unavailable_weekdays"], ["Pazartesi"])
        self.assertEqual(availability["unavailable_ranges"], [
            {"start": "2026-10-14", "end": "2026-10-15"},
        ])
        self.assertTrue(availability["no_night_preference"])
        self.assertTrue(availability["no_weekend_preference"])
        self.assertEqual(availability["periods"], [{
            "month": "2026-10", "unrestricted_start_days": 25,
            "night_start_days": 0, "weekend_start_days": 0, "holiday_start_days": 2,
        }])

    def test_shift_activity_checks_night_shift_overlaps_and_lock_without_changing_schedule(self):
        ada, bora = [person["id"] for person in self.people]
        day = "2026-10-01"
        self.save_schedule([{"row_id": "night-ada", "employee_id": ada, "section": "MARKET", "days": {
            "2026-09-30": {"kind": "work", "start": "22:00", "end": "02:00"}}}], "2026-09")
        self.save_schedule([{"row_id": "relief-bora", "employee_id": bora, "section": "MARKET", "days": {
            day: {"kind": "work", "start": "00:00", "end": "03:00"}}}])
        before = self.store.schedule_month("2026-10")
        base = {"month": "2026-10", "date": day, "section": "MARKET", "kind": "break",
                "start": "00:20", "end": "00:40", "employee_id": ada, "recipient_id": None}
        first = operations.save_shift_activity(self.store, base)["items"][0]
        with self.assertRaises(ValueError):
            operations.save_shift_activity(self.store, base)
        report = operations.save_shift_activity(self.store, {**base, "employee_id": bora})
        self.assertEqual([item["kind"] for item in report["findings"]], ["parallel_break"])
        handover = {**base, "kind": "handover", "recipient_id": bora, "start": "00:35", "end": "01:00"}
        self.assertEqual([item["kind"] for item in operations.save_shift_activity(self.store, handover)["findings"]],
                         ["parallel_break", "person_overlap", "person_overlap"])
        with self.assertRaises(ValueError):
            operations.save_shift_activity(self.store, {**handover, "end": "02:30"})
        self.assertEqual(len(operations.shift_activity_report(self.store, date.fromisoformat(day))["items"]), 3)
        removal = {"month": "2026-10", "date": day, "remove_id": first["id"]}
        self.store.set_period_locked("2026-10", True)
        with self.assertRaises(ValueError):
            operations.save_shift_activity(self.store, removal)
        self.store.set_period_locked("2026-10", False)
        self.assertEqual(len(operations.save_shift_activity(self.store, removal)["items"]), 2)
        self.assertEqual(self.store.schedule_month("2026-10"), before)

    def test_mutual_swap_requires_separate_consents_and_applies_both_shifts(self):
        ada, bora = [person["id"] for person in self.people]
        month, day = "2026-10", "2026-10-12"
        entries = [
            {"row_id": f"worker-{person}", "employee_id": person, "section": "MARKET",
             "days": {day: {"kind": "work", "start": start, "end": end}}}
            for person, start, end in [(ada, "08:00", "16:00"), (bora, "16:00", "24:00")]
        ]
        revision = self.save_schedule(entries)
        operations.publish_in_transaction(self.store, month, revision)
        handler = SimpleNamespace(_attendance_for=lambda store, selected_month, entries=None:
                                  ({"rows": []}, entries, store.attendance_month(selected_month)))
        preview = operations.preview_mutual_swap(handler, self.store, month, {
            "action": "preview", "date": day, "first_id": ada, "second_id": bora,
        })
        self.assertTrue(preview["can_create"])
        self.assertEqual(preview["revision"], revision)
        item = operations.manage_mutual_swap(self.store, month, {
            "action": "create", "date": day, "first_id": ada, "second_id": bora,
            "fingerprint": preview["fingerprint"],
        })["items"][0]
        approve = {"action": "approve", "id": item["id"]}
        with self.store.connection:
            with self.assertRaises(ValueError):
                operations.respond_mutual_swap(self.store, 999, item["id"], "accept")
            operations.respond_mutual_swap(self.store, ada, item["id"], "accept")
            operations.respond_mutual_swap(self.store, ada, item["id"], "accept")
        with self.assertRaises(ValueError):
            operations.manage_mutual_swap(self.store, month, approve)
        self.assertEqual(self.store.schedule_month(month)["revision"], revision)
        with self.store.connection:
            operations.respond_mutual_swap(self.store, bora, item["id"], "accept")
        self.store.set_period_locked(month, True)
        with self.assertRaises(ValueError):
            operations.manage_mutual_swap(self.store, month, approve)
        self.store.set_period_locked(month, False)
        result = operations.manage_mutual_swap(self.store, month, approve)
        starts = {row["employee_id"]: row["days"][day]["start"] for row in self.store.schedule_month(month)["entries"]}
        self.assertEqual(starts, {ada: "16:00", bora: "08:00"})
        self.assertEqual(result["items"][0]["status"], "approved")
        self.assertNotEqual(result["revision"], revision)
        with self.assertRaises(ValueError):
            operations.manage_mutual_swap(self.store, month, approve)

    def test_single_skill_warning_uses_active_cards_before_any_absence(self):
        first, second = (p["id"] for p in self.people)
        self.store.save_planning_settings({"daily_limit": 2, "skill_requirements": [{"section": "MARKET", "skill": "Kasa"}]})
        def warned():
            return [f["employee_id"] for f in operations.quality_report(self.store, "2026-11")["findings"]
                    if f["type"] == "single_skill_holder"]
        self.assertEqual(warned(), [])
        self.store.save_employee_profile(first, {"skills": ["Kasa"]})
        self.assertEqual(warned(), [first])
        self.store.save_employee_profile(second, {"skills": ["kasa"]})
        self.assertEqual(warned(), [])
        with self.store.connection:
            self.store.connection.execute("UPDATE employees SET active=0 WHERE id=?", (second,))
        self.assertEqual(warned(), [first])
        self.assertEqual(self.store.schedule_month("2026-11")["revision"], "")

    def test_quality_hub_counts_pending_work_for_selected_month(self):
        employee_id = self.people[0]["id"]
        with self.store.connection:
            self.store.connection.execute(
                "INSERT INTO portal_requests(employee_id,kind,start_date,end_date,details,status,created_at) "
                "VALUES (?,?,?,?,?,?,?)", (employee_id, "leave", "2026-10-01", "2026-10-03", "", "pending", 1),
            )
        report = operations.quality_report(self.store, "2026-10")
        self.assertEqual(report["pending_work"][0]["count"], 1)
        self.assertEqual(operations.quality_report(self.store, "2026-11")["pending_work"][0]["count"], 0)

    def test_rotation_anchor_survives_partial_profile_updates(self):
        employee_id = self.people[0]["id"]
        self.store.save_employee_profile(employee_id, {
            "rotation": ["weekly-off"], "rotation_anchor": "2026-09-29",
            "rotation_resume_mode": "continue",
        })
        self.store.save_employee_profile(employee_id, {"title": "Görevli"})
        for invalid in ("20260929", "2026-02-29", False):
            with self.assertRaises(ValueError):
                self.store.save_employee_profile(employee_id, {"rotation_anchor": invalid})
        with self.assertRaises(ValueError):
            self.store.save_employee_profile(employee_id, {"rotation_resume_mode": "guess"})
        profile = self.store.employee_profile(employee_id)
        self.assertEqual(
            (profile["rotation"], profile["rotation_anchor"], profile["rotation_resume_mode"]),
            (["weekly-off"], "2026-09-29", "continue"),
        )

    def test_mentoring_requires_real_same_section_hour_overlap(self):
        ada, bora = [person["id"] for person in self.people]
        self.store.save_employee_profile(ada, {"training_skills": ["Kasa"]})
        self.store.save_employee_profile(bora, {"experienced_skills": ["kasa"]})
        self.save_schedule([
            {"row_id": "trainee", "employee_id": ada, "section": "MARKET",
             "days": {"2026-10-05": {"kind": "work", "start": "08:00", "end": "16:00"}}},
            {"row_id": "mentor", "employee_id": bora, "section": "MARKET",
             "days": {"2026-10-05": {"kind": "work", "start": "12:00", "end": "20:00"}}},
        ])
        findings = [item for item in operations.quality_report(self.store, "2026-10")["findings"]
                    if item["type"] == "mentorship_gap"]
        self.assertEqual(findings[0]["uncovered"], [{"start": "08:00", "end": "12:00"}])
        self.assertIn("örtüşmeyen saatler: 08:00–12:00", findings[0]["message"])

    def test_availability_findings_explain_preference_vs_hard_constraint(self):
        employee_id = self.people[0]["id"]
        self.save_schedule([{"row_id": "ada", "employee_id": employee_id, "section": "MARKET",
                             "days": {"2026-10-05": {"kind": "work", "start": "08:00", "end": "16:00"}}}])
        base = {"employee_id": employee_id, "start_date": "2026-10-01", "end_date": "2026-10-31",
                "unavailable_weekdays": [0]}
        for preference, expected_kind, expected_severity in (
            (True, "preference", "info"), (False, "hard_constraint", "error"),
        ):
            operations.save_availability(self.store.connection, {**base, "preference": preference}, self.people)
            self.store.connection.commit()
            item = next(item for item in operations.quality_report(self.store, "2026-10")["findings"]
                        if item["type"] == "availability")
            self.assertEqual((item["constraint_kind"], item["severity"]),
                             (expected_kind, expected_severity))
            self.assertIn("Karşılanamayan tercih" if preference else "Kesin uygunluk kısıtı",
                          item["message"])

    def test_leave_calendar_reports_section_density_and_unconfigured_type_priority(self):
        employee_id = self.people[0]["id"]
        requests = [
            {"id": "annual", "employee_id": employee_id, "start": "2026-10-05", "end": "2026-10-05",
             "code": "S", "status": "approved"},
            {"id": "sick", "employee_id": employee_id, "start": "2026-10-05", "end": "2026-10-05",
             "code": "R", "status": "approved"},
        ]
        result = planning.leave_calendar(
            "2026-10", requests, {employee_id: self.people[0]["name"]}, 2,
            section_for=lambda _employee_id, _day: "MARKET",
        )
        day = next(item for item in result["days"] if item["date"] == "2026-10-05")
        self.assertEqual(day["sections"], [{
            "section": "MARKET", "count": 1, "people": [self.people[0]["name"]],
        }])
        self.assertEqual(result["overlaps"][0]["priority"], "not_configured")
        self.assertIn("otomatik tür seçilmedi", result["overlaps"][0]["message"])

    def test_special_day_warns_only_for_selected_date_and_does_not_block_saving(self):
        rule = {"id": "inventory", "type": "staffing", "section": "MARKET",
                "start_date": "2026-10-04", "end_date": "2026-10-04",
                "start": "08:00", "end": "09:00", "minimum": 2, "severity": "error",
                "special_day": "Sayım"}
        operations.save_rules(self.store.connection, [rule])
        self.store.connection.commit()
        self.assertTrue(self.save_schedule([]))
        findings = [item for item in operations.quality_report(self.store, "2026-10")["findings"]
                    if item.get("rule_id") == "inventory"]
        self.assertEqual([(item["date"], item["severity"], item["expected"]) for item in findings],
                         [("2026-10-04", "warning", 2)])
        self.assertIn("Sayım", findings[0]["message"])
        with self.assertRaisesRegex(ValueError, "tek tarihli"):
            operations.normalize_rule({**rule, "end_date": "2026-10-05"})

    def test_rules_validate_date_time_weekday_and_bounds(self):
        valid = {
            "id": "market-day", "type": "staffing", "section": "MARKET",
            "start_date": "2026-10-01", "end_date": "2026-10-31",
            "weekdays": [0, 4], "start": "08:00", "end": "16:00",
            "minimum": 2, "skill": "Kasa", "severity": "error",
            "exception_reason": "Onaylı geçici istisna",
        }
        self.assertEqual(operations.normalize_rule(valid)["weekdays"], [0, 4])
        for invalid in (
            valid | {"end_date": "2026-09-30"},
            valid | {"start": "25:00"},
            valid | {"start": "-1:00"},
            valid | {"end": "08:+1"},
            valid | {"weekdays": [7]},
            valid | {"minimum": 100},
            valid | {"section": "NOT-A-SECTION"},
        ):
            with self.assertRaises(ValueError):
                operations.normalize_rule(invalid)
        self.assertEqual(
            operations.normalize_availability(
                {"employee_id": 1001, "start_date": "2026-10-01", "end_date": "2026-10-01"},
                {1001},
            )["employee_id"],
            1001,
        )

    def test_time_window_finds_exact_skilled_coverage_gap(self):
        day = "2026-10-05"
        self.store.save_employee_profile(self.people[0]["id"], {"skills": ["Kasa"]})
        entries = [
            {"row_id": "ada", "employee_id": self.people[0]["id"], "section": "MARKET",
             "days": {day: {"kind": "work", "start": "08:00", "end": "12:00"}}},
        ]
        rule = operations.normalize_rule({
            "id": "staff", "type": "staffing", "section": "MARKET", "start_date": day,
            "end_date": day, "weekdays": [0], "start": "08:00", "end": "16:00",
            "minimum": 1, "skill": "Kasa",
        })
        findings = operations._custom_rule_findings(
            "2026-10", entries, [rule], self.store.employee_profiles(),
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual((findings[0]["start"], findings[0]["end"]), ("12:00", "16:00"))

    def test_emergency_apply_rechecks_stale_revision_and_lock_without_mutation(self):
        day = "2026-10-12"
        original = {"kind": "work", "start": "08:00", "end": "16:00"}
        self.save_schedule([{
            "row_id": "ada", "employee_id": self.people[0]["id"], "section": "MARKET",
            "days": {day: original},
        }])
        preview = operations.emergency_preview(self.store, {
            "employee_id": self.people[0]["id"], "date": day, "reason": "Acil devamsızlık",
        })
        self.assertTrue(any(item["eligible"] for item in preview["alternatives"]))
        self.store._write_setting("locked_periods", ["2026-10"])
        self.store.connection.commit()
        payload = {
            "employee_id": self.people[0]["id"], "date": day, "reason": "Acil devamsızlık",
            "status": "absent", "replacement_id": self.people[1]["id"],
            "revision": preview["revision"], "confirm": True,
        }
        with self.assertRaises(ValueError):
            operations.apply_emergency(self.store, payload)
        saved = self.store.schedule_month("2026-10")
        self.assertEqual(saved["entries"][0]["days"][day], original)
        self.store._write_setting("locked_periods", [])
        self.store.connection.commit()
        self.save_schedule([{
            "row_id": "ada", "employee_id": self.people[0]["id"], "section": "MARKET",
            "days": {day: original, "2026-10-13": {"kind": "weekly_off"}},
        }])
        with self.assertRaisesRegex(ValueError, "değişti"):
            operations.apply_emergency(self.store, payload)
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM operations_emergency_audit"
        ).fetchone()[0], 0)

    def test_emergency_apply_rechecks_hard_availability_and_records_linked_leave(self):
        day = "2026-10-12"
        original = {"kind": "work", "start": "08:00", "end": "16:00"}
        self.save_schedule([{
            "row_id": "ada", "employee_id": self.people[0]["id"], "section": "MARKET",
            "days": {day: original},
        }])
        preview = operations.emergency_preview(self.store, {
            "employee_id": self.people[0]["id"], "date": day, "reason": "Acil devamsızlık",
        })
        self.assertTrue(any(item["eligible"] for item in preview["alternatives"]))
        payload = {
            "employee_id": self.people[0]["id"], "date": day, "reason": "Acil devamsızlık",
            "status": "absent", "replacement_id": self.people[1]["id"],
            "revision": preview["revision"], "confirm": True,
        }
        with self.store.connection:
            operations.save_availability(self.store.connection, {
                "employee_id": self.people[1]["id"], "start_date": day, "end_date": day,
                "unavailable_weekdays": [date.fromisoformat(day).weekday()], "preference": False,
            }, self.store.people())
        with self.assertRaisesRegex(ValueError, "operasyon kuralı"):
            operations.apply_emergency(self.store, payload)
        self.assertEqual(self.store.schedule_month("2026-10")["entries"][0]["days"][day], original)
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM operations_emergency_audit"
        ).fetchone()[0], 0)

        with self.store.connection:
            self.store.connection.execute(
                "DELETE FROM operations_availability WHERE employee_id=?", (self.people[1]["id"],)
            )
        with mock.patch.object(
            linked_leave, "prepare_schedule_save", wraps=linked_leave.prepare_schedule_save,
        ) as prepare, mock.patch.object(
            linked_leave, "record_applications_in_transaction",
            wraps=linked_leave.record_applications_in_transaction,
        ) as record:
            operations.apply_emergency(self.store, payload)
        prepare.assert_called_once()
        record.assert_called_once()
        self.assertEqual(self.store.schedule_month("2026-10")["entries"][0]["days"][day]["kind"], "absent")

    def test_emergency_rechecks_approved_leave_added_after_preview(self):
        day = "2026-10-12"
        original = {"kind": "work", "start": "08:00", "end": "16:00"}
        self.save_schedule([{
            "row_id": "ada", "employee_id": self.people[0]["id"], "section": "MARKET",
            "days": {day: original},
        }])
        preview = operations.emergency_preview(self.store, {
            "employee_id": self.people[0]["id"], "date": day, "reason": "Acil devamsızlık",
        })
        replacement_id = next(item["employee_id"] for item in preview["alternatives"] if item["eligible"])
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.store.connection:
            self.store.connection.execute(
                "INSERT INTO linked_leave_requests"
                "(id,employee_id,pattern,start_date,end_date,weekdays,kind,status,note,revision,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("approved-leave", replacement_id, "single", day, day, "[]", "annual_leave",
                 "approved", "", 1, now, now),
            )
        with self.assertRaisesRegex(ValueError, "artık uygun değil"):
            operations.apply_emergency(self.store, {
                "employee_id": self.people[0]["id"], "date": day, "reason": "Acil devamsızlık",
                "status": "absent", "replacement_id": replacement_id,
                "revision": preview["revision"], "confirm": True,
            })
        self.assertEqual(self.store.schedule_month("2026-10")["entries"][0]["days"][day], original)
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM operations_emergency_audit"
        ).fetchone()[0], 0)

    def test_hard_availability_only_applies_within_validity_range(self):
        employee_id = self.people[0]["id"]
        with self.store.connection:
            operations.save_availability(self.store.connection, {
                "employee_id": employee_id, "start_date": "2026-10-06", "end_date": "2026-10-06",
                "unavailable_weekdays": [1], "preference": False,
            }, self.store.people())
        entries = [{
            "row_id": "ada", "employee_id": employee_id, "section": "MARKET",
            "days": {
                "2026-10-05": {"kind": "work", "start": "08:00", "end": "16:00"},
                "2026-10-06": {"kind": "work", "start": "08:00", "end": "16:00"},
            },
        }]
        findings = operations._availability_findings(
            "2026-10", entries, self.store, {employee_id: self.people[0]["name"]},
        )
        self.assertEqual([item["date"] for item in findings], ["2026-10-06"])
        self.assertEqual(findings[0]["severity"], "error")
        with self.assertRaises(ValueError):
            operations.validate_schedule_in_transaction(self.store, "2026-10", entries)
        self.assertNotIn(employee_id, operations._effective_availability(self.store, "2026-10-05"))
        self.assertIn(str(employee_id), operations._effective_availability(self.store, "2026-10-06"))

    def test_closing_fingerprint_invalidates_step_after_upstream_change(self):
        data = operations.closing_checklist(self.store, "2026-10")
        fingerprint = operations._step_fingerprint(data["source"], "quality")
        self.assertEqual(data["steps"][0]["fingerprint"], fingerprint)
        self.assertTrue(all(step["fingerprint"] for step in data["steps"]))
        operations.mark_closing_step(self.store, "2026-10", "quality", "Ada Kontrol", fingerprint)
        self.assertTrue(operations.closing_checklist(self.store, "2026-10")["steps"][0]["done"])
        self.save_schedule([{
            "row_id": "ada", "employee_id": self.people[0]["id"], "section": "MARKET",
            "days": {"2026-10-01": {"kind": "work", "start": "08:00", "end": "16:00"}},
        }])
        refreshed = operations.closing_checklist(self.store, "2026-10")
        self.assertTrue(refreshed["steps"][0]["stale"])
        with self.assertRaises(ValueError):
            operations.mark_closing_step(self.store, "2026-10", "attendance", "Ada Kontrol", fingerprint)

    def test_closing_attendance_fingerprint_tracks_related_sources(self):
        baseline = operations.current_snapshot(self.store, "2026-10")["attendance_fingerprint"]
        self.save_schedule([{
            "row_id": "neighbor", "employee_id": self.people[0]["id"], "section": "MARKET",
            "days": {"2026-09-30": {"kind": "work", "start": "22:00", "end": "06:00"}},
        }], month="2026-09")
        after_schedule = operations.current_snapshot(self.store, "2026-10")["attendance_fingerprint"]
        self.assertNotEqual(baseline, after_schedule)
        self.store.save_employee_profile(self.people[0]["id"], {"home_section": "MARKET"})
        after_profile = operations.current_snapshot(self.store, "2026-10")["attendance_fingerprint"]
        self.assertNotEqual(after_schedule, after_profile)
        self.store.save_custom_holidays([{"date": "2026-10-12", "kind": "public", "name": "Test"}])
        after_holiday = operations.current_snapshot(self.store, "2026-10")["attendance_fingerprint"]
        self.assertNotEqual(after_profile, after_holiday)
        operations.save_rules(self.store.connection, [{
            "id": "staff", "type": "staffing", "section": "MARKET",
            "start_date": "2026-10-01", "end_date": "2026-10-31",
            "weekdays": [0], "start": "08:00", "end": "16:00", "minimum": 1,
        }])
        self.assertNotEqual(after_holiday,
                            operations.current_snapshot(self.store, "2026-10")["attendance_fingerprint"])

    def test_publication_diff_keys_section_and_includes_hours_and_type(self):
        employee_id = self.people[0]["id"]
        before = [{
            "row_id": "old", "employee_id": employee_id, "section": "MARKET",
            "days": {"2026-10-01": {"kind": "work", "start": "08:00", "end": "16:00"}},
        }]
        after = [{
            "row_id": "new", "employee_id": employee_id, "section": "ÖSSE",
            "days": {"2026-10-01": {"kind": "work", "start": "10:00", "end": "18:00"}},
        }]
        diff = operations._publication_changes(
            "2026-10", before, after, {employee_id: self.people[0]["name"]},
        )
        self.assertEqual(len(diff), 2)
        self.assertEqual({item["section"] for item in diff}, {"MARKET", "ÖSSE"})
        self.assertEqual({item["type"] for item in diff}, {"added", "removed"})
        self.assertTrue(all(item["before_hours"] or item["after_hours"] for item in diff))

    def test_publish_requires_exact_revision_and_records_section_aware_diff(self):
        day = "2026-10-02"
        self.save_schedule([{
            "row_id": "ada", "employee_id": self.people[0]["id"], "section": "MARKET",
            "days": {day: {"kind": "work", "start": "08:00", "end": "16:00"}},
        }])
        revision = self.store.schedule_month("2026-10")["revision"]
        with self.assertRaisesRegex(ValueError, "önizlemesinden"):
            operations.publish_in_transaction(self.store, "2026-10", "stale")
        published = operations.publish_in_transaction(self.store, "2026-10", revision)
        self.assertEqual(published["revision"], revision)
        self.assertEqual(published["changes"][0]["type"], "added")
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM access_notifications WHERE category='published'"
        ).fetchone()[0], 1)

    def test_publish_rejects_empty_plan_and_caps_history(self):
        self.save_schedule([], month="2026-10")
        revision = self.store.schedule_month("2026-10")["revision"]
        with self.assertRaises(ValueError):
            operations.publish_in_transaction(self.store, "2026-10", revision)
        with self.assertRaises(ValueError):
            operations.publish_in_transaction(self.store, "2026-10", None)
        plans = {
            f"{year}-{month:02d}": {"entries": [], "at": f"{year}-{month:02d}-01T00:00:00+00:00"}
            for year in (2023, 2024) for month in range(1, 13)
        }
        self.store._write_setting("published_plans", plans)
        self.store.connection.commit()
        self.save_schedule([{
            "row_id": "ada", "employee_id": self.people[0]["id"], "section": "MARKET",
            "days": {"2026-10-02": {"kind": "work", "start": "08:00", "end": "16:00"}},
        }])
        revision = self.store.schedule_month("2026-10")["revision"]
        operations.publish_in_transaction(self.store, "2026-10", revision)
        retained = self.store._setting("published_plans", {})
        self.assertEqual(len(retained), 24)
        self.assertIn("2026-10", retained)

    def test_lifecycle_exit_keeps_schedule_cells_unchanged(self):
        day = "2026-10-02"
        original = {"kind": "work", "start": "08:00", "end": "16:00"}
        self.save_schedule([{
            "row_id": "ada", "employee_id": self.people[0]["id"], "section": "MARKET",
            "days": {day: original},
        }])
        preview = operations.lifecycle_preview(self.store, {
            "employee_id": self.people[0]["id"], "action": "exit", "effective_date": day,
        })
        result = operations.apply_lifecycle(self.store, {
            "employee_id": self.people[0]["id"], "action": "exit", "effective_date": day,
            "revision": preview["revision"], "confirm": True,
        })
        self.assertTrue(result["applied"])
        self.assertEqual(result["future_work"][0]["date"], day)
        self.assertEqual(self.store.schedule_month("2026-10")["entries"][0]["days"][day], original)
        self.assertFalse(next(person for person in self.store.people() if person["id"] == self.people[0]["id"])["active"])

    def test_lifecycle_transfer_keeps_dated_home_section_history(self):
        employee_id = self.people[0]["id"]
        self.store.save_employee_profile(employee_id, {"home_section": "MARKET"})
        effective = (date.today() + timedelta(days=4)).isoformat()
        preview_payload = {
            "employee_id": employee_id, "action": "transfer", "effective_date": effective, "section": "ÖSSE",
        }
        with self.assertRaises(ValueError):
            operations.lifecycle_preview(self.store, {
                "employee_id": employee_id, "action": "transfer", "value": "ÖSSE",
            })
        preview = operations.lifecycle_preview(self.store, preview_payload)
        self.assertEqual(preview["changes"]["from_section"], "MARKET")
        result = operations.apply_lifecycle(self.store, {
            **preview_payload, "revision": preview["revision"], "confirm": True,
        })
        self.assertTrue(result["pending"])
        self.assertEqual(self.store.employee_profile(employee_id)["home_section"], "MARKET")
        self.assertEqual(operations.effective_home_section(
            self.store, employee_id, (date.fromisoformat(effective) - timedelta(days=1)).isoformat(),
        ), "MARKET")
        self.assertEqual(operations.effective_home_section(self.store, employee_id, effective), "ÖSSE")
        row, created = linked_leave._slot_and_section(self.store, [], employee_id, effective)
        self.assertTrue(created)
        self.assertEqual(row["section"], "ÖSSE")
        self.assertEqual(operations.lifecycle_pending(self.store)[0]["section"], "ÖSSE")

    def test_future_exit_keeps_access_until_due_then_revokes_atomically(self):
        employee_id = self.people[0]["id"]
        effective = (date.today() + timedelta(days=3)).isoformat()
        now = int(datetime.now(timezone.utc).timestamp())
        with self.store.connection:
            self.store.connection.execute(
                "INSERT INTO portal_tokens(token_hash,employee_id,expires_at,revoked_at,created_at) "
                "VALUES (?,?,?,?,?)",
                ("a" * 64, employee_id, now + 86400 * 30, None, now),
            )
            self.store.connection.execute(
                "INSERT INTO workflow_shares"
                "(id,token_hash,employee_id,person_name,month,snapshot,expires_at,revoked_at,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("share", "b" * 64, employee_id, self.people[0]["name"], "2026-10", "{}",
                 "2099-01-01T00:00:00+00:00", "", "2026-10-01T00:00:00+00:00"),
            )
        payload = {"employee_id": employee_id, "action": "exit", "effective_date": effective}
        preview = operations.lifecycle_preview(self.store, payload)
        operations.apply_lifecycle(self.store, {
            **payload, "revision": preview["revision"], "confirm": True,
        })
        self.assertTrue(next(person for person in self.store.people() if person["id"] == employee_id)["active"])
        self.assertIsNone(self.store.connection.execute(
            "SELECT revoked_at FROM portal_tokens WHERE token_hash=?", ("a" * 64,)
        ).fetchone()["revoked_at"])
        self.assertEqual(self.store.connection.execute(
            "SELECT revoked_at FROM workflow_shares WHERE id='share'"
        ).fetchone()["revoked_at"], "")
        self.assertEqual(operations.process_due_lifecycle(
            self.store, (date.fromisoformat(effective) - timedelta(days=1)).isoformat(),
        ), [])
        due = operations.process_due_lifecycle(self.store, effective)
        self.assertEqual(due[0]["action"], "exit")
        self.assertFalse(next(person for person in self.store.people() if person["id"] == employee_id)["active"])
        self.assertIsInstance(self.store.connection.execute(
            "SELECT revoked_at FROM portal_tokens WHERE token_hash=?", ("a" * 64,)
        ).fetchone()["revoked_at"], int)
        self.assertNotEqual(self.store.connection.execute(
            "SELECT revoked_at FROM workflow_shares WHERE id='share'"
        ).fetchone()["revoked_at"], "")

    def test_exit_preview_lists_future_work_assets_pending_documents_and_access(self):
        employee_id = self.people[0]["id"]
        effective = (date.today() + timedelta(days=10)).isoformat()
        personnel_assets.initialize(self.store.connection)
        document_workflows.initialize(self.store.connection)
        personnel_assets.issue_asset(self.store, {
            "action": "issue", "employee_id": employee_id, "category": "card",
            "item": "Giriş kartı", "note": "",
        })
        created_at = datetime.now(timezone.utc).isoformat()
        with self.store.connection:
            self.store.connection.execute(
                "INSERT INTO workflow_documents "
                "(id,employee_id,person_name,exact_dates,code,template_id,filename,content_type,content,"
                "status,source_revisions,app_version,code_revision,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("pending-doc", employee_id, self.people[0]["name"], "[]", "S", "test",
                 "teslim.pdf", "application/pdf", b"%PDF-test", "draft", "{}", "test", "test",
                 created_at, created_at),
            )
            now = int(datetime.now(timezone.utc).timestamp())
            self.store.connection.execute(
                "INSERT INTO portal_tokens(token_hash,employee_id,expires_at,revoked_at,created_at) "
                "VALUES (?,?,?,?,?)",
                ("c" * 64, employee_id, now + 86400 * 30, None, now),
            )
            share_expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
            self.store.connection.execute(
                "INSERT INTO workflow_shares"
                "(id,token_hash,employee_id,person_name,month,snapshot,expires_at,revoked_at,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("exit-share", "d" * 64, employee_id, self.people[0]["name"], effective[:7], "{}",
                 share_expiry, "", created_at),
            )
        self.save_schedule([{
            "row_id": "exit-future", "employee_id": employee_id, "section": "MARKET",
            "days": {effective: {"kind": "work", "start": "08:00", "end": "16:00"}},
        }], effective[:7])

        preview = operations.lifecycle_preview(self.store, {
            "employee_id": employee_id, "action": "exit", "effective_date": effective,
        })
        self.assertEqual(preview["future_work"][0]["date"], effective)
        checklist = preview["offboarding_checklist"]
        self.assertEqual([item["item"] for item in checklist["open_assets"]], ["Giriş kartı"])
        self.assertEqual([item["filename"] for item in checklist["pending_documents"]], ["teslim.pdf"])
        self.assertEqual(len(checklist["active_portal_tokens"]), 1)
        self.assertEqual(len(checklist["active_workflow_shares"]), 1)

    def test_linked_leave_failures_are_not_silently_discarded(self):
        with mock.patch.object(linked_leave, "calendar_requests", side_effect=ValueError("broken leave source")):
            with self.assertRaisesRegex(ValueError, "broken leave source"):
                operations.quality_report(self.store, "2026-10")
            with self.assertRaisesRegex(ValueError, "broken leave source"):
                operations.leave_conflicts(self.store, "2026-10")

    def test_cost_never_fills_an_unprovided_rate(self):
        attendance = {
            "month": "2026-10",
            "rows": [{
                "employee_id": self.people[0]["id"], "name": self.people[0]["name"],
                "codes": {"2026-10-01": "X"}, "worked_hours": 8, "overtime_hours": 0, "ubgt_hours": 0,
            }],
        }
        with self.assertRaises(ValueError):
            operations.cost_simulation(attendance, {"hours_per_month": 225})
        result = operations.cost_simulation(attendance, {
            "rates": {}, "hours_per_month": 225, "overtime_multiplier": 1.5, "holiday_multiplier": 1,
        })
        self.assertEqual(result["totals"]["total"], 0)
        self.assertEqual(result["missing_rates"], [self.people[0]["name"]])
        self.assertTrue(result["estimate_only"])

    def test_branch_compare_reads_configured_file_readonly_without_migration(self):
        root = Path(self.temp.name) / "workspace"
        branch_path = root / "subeler" / "sube-b" / "vardiya_puantaj.sqlite3"
        branch_path.parent.mkdir(parents=True)
        (root / "branches.json").write_text(json.dumps({
            "active": "ana", "branches": [{"id": "ana", "name": "Ana"},
                                            {"id": "sube-b", "name": "Şube B"}],
        }), encoding="utf-8")
        connection = sqlite3.connect(branch_path)
        connection.execute("CREATE TABLE schedule_months(period TEXT PRIMARY KEY,payload TEXT,revision TEXT)")
        connection.execute(
            "INSERT INTO schedule_months VALUES (?,?,?)",
            ("2026-10", json.dumps([{"employee_id": 1, "days": {
                "2026-10-01": {"kind": "work", "start": "22:00", "end": "06:00"},
            }}]), "branch-revision"),
        )
        connection.commit()
        connection.close()
        result = operations.branch_comparison(root / "vardiya_puantaj.sqlite3", "sube-b", "2026-10")
        self.assertEqual(result["work_hours"], 8)
        reopened = sqlite3.connect(branch_path.as_uri() + "?mode=ro", uri=True)
        try:
            tables = {row[0] for row in reopened.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            self.assertEqual(tables, {"schedule_months"})
        finally:
            reopened.close()
        with self.assertRaises(ValueError):
            operations.branch_comparison(root / "vardiya_puantaj.sqlite3", "..", "2026-10")


if __name__ == "__main__":
    unittest.main()
