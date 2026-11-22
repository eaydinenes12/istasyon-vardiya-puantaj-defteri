from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import linked_leave
import planning
from storage import Store


class Handler:
    def __init__(self, path: Path, payload=None):
        self.path = path
        self.payload = payload
        self.result = None
        self.status = 200

    def _store(self):
        store = Store(self.path)
        linked_leave.initialize(store.connection)
        return store

    def _read_json(self, maximum=1_000_000):
        return self.payload

    def _json(self, value, status=200):
        self.result = value
        self.status = status


class LinkedLeaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "linked-leave.sqlite3"
        with Store(self.path) as store:
            linked_leave.initialize(store.connection)
            self.person = store.add_person("Ayşe Yılmaz")
            self.other = store.add_person("Deniz Kaya")

    def tearDown(self):
        self.temp.cleanup()

    def store(self):
        result = Store(self.path)
        linked_leave.initialize(result.connection)
        return result

    def add_request(self, *, employee_id=None, pattern="single", start="2026-10-12",
                    end="2026-10-12", weekdays=None, kind="annual_leave"):
        handler = Handler(self.path, {
            "employee_id": employee_id or self.person["id"],
            "pattern": pattern, "start": start, "end": end, "weekdays": weekdays or [],
            "kind": kind, "note": "",
        })
        self.assertTrue(linked_leave.handle_write(handler, "POST", "/api/linked-leave/requests"))
        self.assertEqual(handler.status, 201)
        return handler.result["request"]

    def approve(self, item):
        return self.approval_result(item)["request"]

    def approval_result(self, item):
        handler = Handler(self.path, {"revision": item["revision"], "status": "approved"})
        self.assertTrue(linked_leave.handle_write(
            handler, "POST", f"/api/linked-leave/requests/{item['id']}/approve",
        ))
        return handler.result

    def put_schedule(self, month, entries):
        with self.store() as store:
            revision = store.save_schedule_month(month, entries, "")
            self.assertTrue(revision)
            return revision

    def preview(self, month, requests):
        handler = Handler(self.path, {"month": month, "request_ids": [item["id"] for item in requests]})
        linked_leave.handle_write(handler, "POST", "/api/linked-leave/preview")
        return handler.result

    def apply(self, month, requests, preview, confirm=False):
        handler = Handler(self.path, {
            "month": month, "request_ids": [item["id"] for item in requests],
            "schedule_revision": preview["schedule_revision"],
            "preview_hash": preview["preview_hash"], "confirm_conflicts": confirm,
        })
        linked_leave.handle_write(handler, "POST", "/api/linked-leave/apply")
        return handler

    def test_pending_is_not_reconciled_until_approved(self):
        request = self.add_request()
        entries = [{"row_id": "row-a", "employee_id": self.person["id"],
                    "section": "MARKET", "slot": 1, "days": {}}]
        with self.store() as store:
            pending = linked_leave.reconcile_month(store, "2026-10", entries)
            self.assertEqual(pending["applications"], [])

        approved = self.approve(request)
        with self.store() as store:
            reconciled = linked_leave.reconcile_month(store, "2026-10", entries)
        self.assertEqual(reconciled["entries"][0]["days"]["2026-10-12"]["kind"], "annual_leave")
        self.assertEqual(reconciled["applications"][0]["request_id"], approved["id"])

    def test_approval_automatically_applies_all_saved_covered_months(self):
        item = self.add_request(pattern="range", start="2026-10-31", end="2026-11-01",
                                kind="weekly_off")
        for month in ("2026-10", "2026-11"):
            self.put_schedule(month, [{
                "row_id": "row-a", "employee_id": self.person["id"],
                "section": "MARKET", "slot": 1, "days": {},
            }])
        result = self.approval_result(item)
        self.assertEqual(result["applied"], 2)
        self.assertEqual(sorted(result["months"]), ["2026-10", "2026-11"])
        self.assertEqual(result["conflicts"], [])
        with self.store() as store:
            for month, day in (("2026-10", "2026-10-31"), ("2026-11", "2026-11-01")):
                self.assertEqual(store.schedule_month(month)["entries"][0]["days"][day],
                                 {"kind": "weekly_off"})
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM linked_leave_applications WHERE undone_at IS NULL",
            ).fetchone()[0], 2)

    def test_approval_reports_exact_occupied_and_locked_days(self):
        item = self.add_request(pattern="range", start="2026-10-31", end="2026-11-01")
        self.put_schedule("2026-10", [{
            "row_id": "row-a", "employee_id": self.person["id"],
            "section": "MARKET", "slot": 1,
            "days": {"2026-10-31": {"kind": "work", "start": "09:00", "end": "17:00"}},
        }])
        revision = self.put_schedule("2026-11", [{
            "row_id": "row-a", "employee_id": self.person["id"],
            "section": "MARKET", "slot": 1, "days": {},
        }])
        with self.store() as store:
            store.set_period_locked("2026-11", True)
        result = self.approval_result(item)
        self.assertEqual(result["applied"], 0)
        self.assertEqual([conflict["date"] for conflict in result["conflicts"]],
                         ["2026-10-31", "2026-11-01"])
        self.assertIn("kilitli", result["conflicts"][1]["reason"])
        with self.store() as store:
            self.assertEqual(store.schedule_month("2026-11")["revision"], revision)
            self.assertEqual(store.schedule_month("2026-10")["entries"][0]["days"]["2026-10-31"]["kind"], "work")

    def test_approval_reports_overnight_spill_from_previous_month(self):
        item = self.add_request(start="2026-11-01", end="2026-11-01")
        self.put_schedule("2026-10", [{
            "row_id": "row-a", "employee_id": self.person["id"],
            "section": "MARKET", "slot": 1,
            "days": {"2026-10-31": {"kind": "work", "start": "22:00", "end": "06:00"}},
        }])
        self.put_schedule("2026-11", [{
            "row_id": "row-a", "employee_id": self.person["id"],
            "section": "MARKET", "slot": 1, "days": {},
        }])
        result = self.approval_result(item)
        self.assertEqual(result["applied"], 0)
        self.assertIn("gece vardiyası", result["conflicts"][0]["reason"])

    def test_overlapping_approvals_do_not_silently_select_a_leave_type(self):
        self.approve(self.add_request())
        second = self.approve(self.add_request(kind="weekly_off"))
        with self.store() as store:
            result = linked_leave.prepare_schedule_save(store, "2026-10", [{
                "row_id": "row-a", "employee_id": self.person["id"],
                "section": "MARKET", "slot": 1, "days": {},
            }])
        self.assertEqual(result["applications"], [])
        self.assertTrue(any("başka onaylı izin" in row["reason"] for row in result["conflicts"]))

    def test_approval_failure_rolls_back_status_all_months_and_audit(self):
        item = self.add_request(pattern="range", start="2026-10-31", end="2026-11-01")
        revisions = {month: self.put_schedule(month, [{
            "row_id": "row-a", "employee_id": self.person["id"],
            "section": "MARKET", "slot": 1, "days": {},
        }]) for month in ("2026-10", "2026-11")}
        original = linked_leave.record_applications_in_transaction

        def fail_second_month(store, month, applications):
            count = original(store, month, applications)
            if month == "2026-11":
                raise ValueError("Simulated failure")
            return count

        with patch.object(linked_leave, "record_applications_in_transaction", side_effect=fail_second_month):
            with self.assertRaisesRegex(ValueError, "Simulated failure"):
                self.approval_result(item)
        with self.store() as store:
            for month in revisions:
                self.assertEqual(store.schedule_month(month)["revision"], revisions[month])
                self.assertEqual(store.schedule_month(month)["entries"][0]["days"], {})
            self.assertEqual(store.connection.execute(
                "SELECT status FROM linked_leave_requests WHERE id=?", (item["id"],),
            ).fetchone()["status"], "pending")
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM linked_leave_applications",
            ).fetchone()[0], 0)
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM change_history WHERE action='linked_leave.approve_apply'",
            ).fetchone()[0], 0)

    def test_transaction_helper_is_idempotent_and_does_not_commit_its_owner(self):
        self.approve(self.add_request())
        with self.store() as store:
            store.connection.execute("BEGIN IMMEDIATE")
            prepared = linked_leave.prepare_schedule_save(store, "2026-10", [])
            store.connection.execute(
                "INSERT INTO schedule_months(period,payload,revision) VALUES (?,?,?)",
                ("2026-10", json.dumps(prepared["entries"]), "transaction-test"),
            )
            self.assertEqual(linked_leave.record_applications_in_transaction(
                store, "2026-10", prepared["applications"],
            ), 1)
            self.assertEqual(linked_leave.record_applications_in_transaction(
                store, "2026-10", prepared["applications"],
            ), 0)
            self.assertTrue(store.connection.in_transaction)
            store.connection.rollback()
            self.assertEqual(store.schedule_month("2026-10")["revision"], "")
            self.assertEqual(store.connection.execute(
                "SELECT COUNT(*) FROM linked_leave_applications",
            ).fetchone()[0], 0)
            with self.assertRaisesRegex(ValueError, "içinde"):
                linked_leave.record_applications_in_transaction(store, "2026-10", [])

    def test_undone_application_can_be_reapplied_and_retains_audit(self):
        item = self.add_request()
        self.put_schedule("2026-10", [{
            "row_id": "row-a", "employee_id": self.person["id"],
            "section": "MARKET", "slot": 1, "days": {},
        }])
        result = self.approval_result(item)
        undo = Handler(self.path, {
            "month": "2026-10", "request_ids": [item["id"]],
            "schedule_revision": result["months"]["2026-10"],
        })
        linked_leave.handle_write(undo, "POST", "/api/linked-leave/undo")
        approved = result["request"]
        preview = self.preview("2026-10", [approved])
        applied = self.apply("2026-10", [approved], preview)
        self.assertEqual(applied.result["applied"], 1)
        with self.store() as store:
            mark = store.connection.execute(
                "SELECT undone_at FROM linked_leave_applications WHERE request_id=?", (item["id"],),
            ).fetchone()
            self.assertIsNone(mark["undone_at"])
            actions = [row["action"] for row in store.connection.execute(
                "SELECT action FROM change_history WHERE action LIKE 'linked_leave.%'",
            )]
            self.assertIn("linked_leave.undo", actions)
            self.assertEqual(actions.count("linked_leave.applications"), 2)

    def test_forecast_deduplicates_approved_days_and_excludes_them_from_pending(self):
        self.approve(self.add_request(pattern="range", start="2026-10-31", end="2026-11-02"))
        self.approve(self.add_request(start="2026-11-02", end="2026-11-02"))
        self.add_request(pattern="range", start="2026-11-02", end="2026-11-03")
        self.add_request(employee_id=self.other["id"], start="2026-11-04", end="2026-11-04")
        balances = {"people": [
            {"employee_id": self.person["id"], "name": self.person["name"], "has_opening": True,
             "annual_leave_remaining_days": 7.5},
            {"employee_id": self.other["id"], "name": self.other["name"], "has_opening": False},
        ]}
        with self.store() as store:
            before = list(store.connection.iterdump())
            result = linked_leave.forecast_annual_leave(store, "2026-10", balances)
            self.assertEqual(list(store.connection.iterdump()), before)
        first, second = result["people"]
        self.assertEqual((first["approved"], first["pending"], first["projected"]), (2, 1, 5.5))
        self.assertEqual((second["approved"], second["pending"], second["projected"]), (0, 1, None))

    def test_calendar_rows_are_day_exact_and_compatible_with_legacy_reports(self):
        item = self.add_request(pattern="weekdays", start="2026-10-01", end="2026-11-01",
                                weekdays=[0], kind="sick_leave")
        with self.store() as store:
            pending = linked_leave.calendar_requests(store, "2026-10")
            self.assertEqual([row["start"] for row in pending],
                             ["2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26"])
            self.assertTrue(all(row["start"] == row["end"] and row["status"] == "pending"
                                and row["code"] == "R" for row in pending))
            calendar = planning.leave_calendar("2026-10", pending,
                                               {self.person["id"]: self.person["name"]}, 2)
            self.assertEqual(len(calendar["requests"]), 4)
        self.approve(item)
        with self.store() as store:
            records = linked_leave.calendar_requests(store, "2026-10")
            warnings = planning.leave_request_items("2026-10", [{
                "row_id": "row-a", "employee_id": self.person["id"], "section": "MARKET",
                "days": {"2026-10-12": {"kind": "work", "start": "09:00", "end": "17:00"}},
            }], {self.person["id"]: self.person["name"]}, records, 2)
            self.assertTrue(any(row["type"] == "leave_request_conflict" for row in warnings))

    def test_calendar_combines_fixed_weekly_off_with_annual_leave_and_unique_section_density(self):
        self.approve(self.add_request())
        self.approve(self.add_request(employee_id=self.other["id"]))
        linked_weekly_off = self.add_request(
            employee_id=self.other["id"], kind="weekly_off",
        )
        with self.store() as store:
            revision = store.schedule_month("2026-10")["revision"]
            store.save_schedule_month("2026-10", [{
                "row_id": "fixed-weekly-off", "employee_id": self.person["id"],
                "section": "MARKET", "slot": 1,
                "days": {"2026-10-12": {"kind": "weekly_off"}},
            }, {
                "row_id": "linked-weekly-off", "employee_id": self.other["id"],
                "section": "MARKET", "slot": 1,
                "days": {"2026-10-12": {"kind": "weekly_off"}},
            }], revision)
            saved = store.schedule_month("2026-10")
            requests = linked_leave.calendar_requests(store, "2026-10")
            fixed_rows = [row for row in requests
                          if row["employee_id"] == self.person["id"] and row["code"] == "H"]
            linked_rows = [row for row in requests
                           if row.get("linked_request_id") == linked_weekly_off["id"]]
            self.assertEqual(len(fixed_rows), 1)
            self.assertEqual(fixed_rows[0]["source"], "fixed_schedule")
            self.assertEqual(len(linked_rows), 1)
            calendar = planning.leave_calendar(
                "2026-10", requests,
                {person["id"]: person["name"] for person in (self.person, self.other)},
                2, section_for=lambda _employee_id, _day: "MARKET",
            )
            day = next(item for item in calendar["days"] if item["date"] == "2026-10-12")
            self.assertEqual(day["sections"], [{
                "section": "MARKET", "count": 2,
                "people": sorted([self.person["name"], self.other["name"]], key=str.casefold),
            }])
            fixed_overlap = next(item for item in calendar["overlaps"]
                                 if item["employee_id"] == self.person["id"])
            self.assertEqual({item["code"] for item in fixed_overlap["requests"]}, {"H", "S"})
            self.assertEqual(fixed_overlap["priority"], "not_configured")
            self.assertIn("otomatik tür seçilmedi", fixed_overlap["message"])
            self.assertEqual(store.schedule_month("2026-10"), saved)
            self.assertEqual(store.schedule_month("2026-10")["revision"], saved["revision"])

    def test_weekday_recurrence_crosses_month_boundaries(self):
        request = self.add_request(
            pattern="weekdays", start="2026-01-26", end="2026-02-10", weekdays=[0, 2],
        )
        self.approve(request)
        entries = [{"row_id": "row-a", "employee_id": self.person["id"],
                    "section": "MARKET", "slot": 1, "days": {}}]
        with self.store() as store:
            result = linked_leave.reconcile_month(store, "2026-02", entries)
        self.assertEqual(
            sorted(result["entries"][0]["days"]),
            ["2026-02-02", "2026-02-04", "2026-02-09"],
        )

    def test_future_schedule_reconciliation_creates_one_row_for_multiple_leave_days(self):
        request = self.approve(self.add_request(
            pattern="range", start="2026-10-12", end="2026-10-14",
        ))
        with self.store() as store:
            result = linked_leave.reconcile_month(store, "2026-10", [])
        self.assertEqual(len(result["entries"]), 1)
        self.assertEqual(
            sorted(result["entries"][0]["days"]),
            ["2026-10-12", "2026-10-13", "2026-10-14"],
        )
        self.assertEqual({item["request_id"] for item in result["applications"]}, {request["id"]})

    def test_future_schedule_save_records_linked_cells_atomically(self):
        request = self.approve(self.add_request())
        with self.store() as store:
            result = linked_leave.reconcile_month(store, "2026-10", [])
            saved = linked_leave.save_reconciled_month(store, "2026-10", result, "")
            self.assertEqual(saved["applied"], 1)
            self.assertTrue(saved["revision"])
            marked = store.connection.execute(
                "SELECT request_id FROM linked_leave_applications WHERE month='2026-10' AND undone_at IS NULL",
            ).fetchall()
            self.assertEqual([row["request_id"] for row in marked], [request["id"]])

    def test_conflicts_require_confirmation_and_are_never_overwritten(self):
        request = self.approve(self.add_request())
        original = {"kind": "work", "start": "09:00", "end": "17:00"}
        revision = self.put_schedule("2026-10", [{
            "row_id": "row-a", "employee_id": self.person["id"], "section": "MARKET",
            "slot": 1, "days": {"2026-10-12": original},
        }])
        preview = self.preview("2026-10", [request])
        self.assertEqual(len(preview["conflicts"]), 1)
        denied = self.apply("2026-10", [request], preview, confirm=False)
        self.assertEqual(denied.status, 409)
        self.assertEqual(revision, preview["schedule_revision"])
        applied = self.apply("2026-10", [request], preview, confirm=True)
        self.assertEqual(applied.result["applied"], 0)
        with self.store() as store:
            self.assertEqual(store.schedule_month("2026-10")["entries"][0]["days"]["2026-10-12"], original)

    def test_apply_is_idempotent_and_locked_periods_are_rejected(self):
        request = self.approve(self.add_request())
        self.put_schedule("2026-10", [{
            "row_id": "row-a", "employee_id": self.person["id"], "section": "MARKET",
            "slot": 1, "days": {},
        }])
        preview = self.preview("2026-10", [request])
        self.assertEqual(preview["placements"], [])
        self.assertEqual(len(preview["already_applied"]), 1)
        first = self.apply("2026-10", [request], preview)
        self.assertEqual(first.result["applied"], 0)
        second_preview = self.preview("2026-10", [request])
        self.assertEqual(second_preview["placements"], [])
        self.assertEqual(len(second_preview["already_applied"]), 1)
        second = self.apply("2026-10", [request], second_preview)
        self.assertEqual(second.result["applied"], 0)

        with self.store() as store:
            store.set_period_locked("2026-10", True)
            current = store.schedule_month("2026-10")
        undo = Handler(self.path, {
            "month": "2026-10", "request_ids": [request["id"]],
            "schedule_revision": current["revision"],
        })
        with self.assertRaisesRegex(ValueError, "Dönem kilitli"):
            linked_leave.handle_write(undo, "POST", "/api/linked-leave/undo")

    def test_apply_rejects_stale_schedule_after_preview(self):
        request = self.approve(self.add_request())
        self.put_schedule("2026-10", [{
            "row_id": "row-a", "employee_id": self.person["id"], "section": "MARKET",
            "slot": 1, "days": {},
        }])
        preview = self.preview("2026-10", [request])
        with self.store() as store:
            data = store.schedule_month("2026-10")
            changed = list(data["entries"])
            changed[0]["days"] = {"2026-10-20": {"kind": "weekly_off"}}
            store.save_schedule_month("2026-10", changed, data["revision"])
        with self.assertRaisesRegex(ValueError, "değişmiş"):
            self.apply("2026-10", [request], preview)

    def test_reconcile_preserves_existing_manual_cells_and_can_record_new_month(self):
        request = self.approve(self.add_request(
            pattern="range", start="2026-10-12", end="2026-10-13",
        ))
        manual = {"kind": "weekly_off"}
        entries = [{
            "row_id": "row-a", "employee_id": self.person["id"], "section": "MARKET",
            "slot": 1, "days": {"2026-10-12": manual},
        }]
        with self.store() as store:
            result = linked_leave.reconcile_month(store, "2026-10", entries)
        self.assertEqual(result["entries"][0]["days"]["2026-10-12"], manual)
        self.assertEqual(result["entries"][0]["days"]["2026-10-13"]["kind"], "annual_leave")
        revision = self.put_schedule("2026-10", result["entries"])
        with self.store() as store:
            self.assertEqual(
                linked_leave.record_reconciled_applications(
                    store, "2026-10", result["applications"], revision,
                ),
                1,
            )

    def test_stale_request_revision_is_rejected(self):
        item = self.add_request()
        self.approve(item)
        handler = Handler(self.path, {"revision": item["revision"], "status": "cancelled"})
        with self.assertRaisesRegex(ValueError, "değişmiş"):
            linked_leave.handle_write(
                handler, "POST", f"/api/linked-leave/requests/{item['id']}/approve",
            )

    def test_selective_undo_removes_only_selected_linked_cells(self):
        first = self.approve(self.add_request(start="2026-10-12", end="2026-10-12"))
        second = self.approve(self.add_request(start="2026-10-13", end="2026-10-13"))
        self.put_schedule("2026-10", [{
            "row_id": "row-a", "employee_id": self.person["id"], "section": "MARKET",
            "slot": 1, "days": {},
        }])
        preview = self.preview("2026-10", [first, second])
        result = self.apply("2026-10", [first, second], preview)
        undo = Handler(self.path, {
            "month": "2026-10", "request_ids": [first["id"]],
            "schedule_revision": result.result["schedule_revision"],
        })
        linked_leave.handle_write(undo, "POST", "/api/linked-leave/undo")
        self.assertEqual(undo.result["undone"], 1)
        with self.store() as store:
            days = store.schedule_month("2026-10")["entries"][0]["days"]
        self.assertNotIn("2026-10-12", days)
        self.assertEqual(days["2026-10-13"]["kind"], "annual_leave")

    def test_pending_leave_impact_detects_next_month_overnight_gap_without_writes(self):
        item = self.add_request(start="2026-10-31", end="2026-10-31")
        revision = self.put_schedule("2026-10", [{
            "row_id": "night-row", "employee_id": self.person["id"], "section": "MARKET",
            "slot": 1, "days": {"2026-10-31": {"kind": "work", "start": "22:00", "end": "06:00"}},
        }])
        with self.store() as store:
            store.save_compliance_settings({"min_staffing_windows": [
                {"section": "MARKET", "start": "05:00", "end": "05:30", "minimum": 2},
            ]})
        handler = Handler(self.path)
        self.assertTrue(linked_leave.handle_get(handler, "/api/linked-leave/impact", {
            "request_id": [item["id"]], "month": ["2026-10"],
        }))
        self.assertEqual(handler.result["gaps"], [
            {"date": "2026-11-01", "section": "MARKET", "start": "05:00", "end": "05:30",
             "before": 1, "after": 2},
        ])
        comparison = Handler(self.path)
        linked_leave.handle_get(comparison, "/api/linked-leave/impact", {
            "request_id": [item["id"]], "month": ["2026-10"],
            "alternative_start": ["2026-10-30"], "alternative_end": ["2026-10-30"],
        })
        self.assertEqual(comparison.result["requested_days"], 1)
        self.assertEqual(comparison.result["shifts"], handler.result["shifts"])
        self.assertEqual(comparison.result["alternatives"][0]["start"], "2026-10-30")
        self.assertEqual(comparison.result["alternatives"][0]["shifts"], [])
        self.assertEqual(comparison.result["alternatives"][0]["gaps"], [])
        self.assertEqual([person["employee_id"] for person in handler.result["shifts"][0]["candidates"]],
                         [self.other["id"]])
        with self.store() as store:
            self.assertEqual(store.schedule_month("2026-10")["revision"], revision)
        self.approve(item)
        with self.assertRaisesRegex(ValueError, "bekleyen"):
            linked_leave.handle_get(handler, "/api/linked-leave/impact", {
                "request_id": [item["id"]], "month": ["2026-10"],
            })

    def test_replacement_candidates_include_reasons_for_conflict_and_missing_skill(self):
        with self.store() as store:
            store.save_planning_settings({
                "daily_limit": 0, "skill_requirements": [{"section": "MARKET", "skill": "Forklift"}],
            })
            store.save_overtime_settings({"weekly_limit_hours": 36, "week_start": 0, "daily_limit_hours": 0})
            store.save_employee_profile(self.other["id"], {"skills": []})
            store.save_schedule_month("2026-10", [{
                "row_id": "row-b", "employee_id": self.other["id"], "section": "MARKET",
                "slot": 1, "days": {"2026-10-12": {"kind": "work", "start": "08:00", "end": "16:00"}},
            }], "")
        handler = Handler(self.path, {
            "employee_id": self.person["id"], "exclude_employee_id": self.person["id"],
            "date": "2026-10-12", "section": "MARKET", "start": "09:00", "end": "17:00",
        })
        linked_leave.handle_write(handler, "POST", "/api/linked-leave/candidates")
        candidate = next(item for item in handler.result["candidates"] if item["employee_id"] == self.other["id"])
        self.assertIn("Bölüm için gereken yetkinlikler", handler.result["checked_rules"])
        self.assertIn("Mevcut vardiya çakışmaları ve tanımlı dinlenme süresi", handler.result["checked_rules"])
        self.assertIn("Kayıtlı vardiyalarla haftalık saat toplamı", handler.result["checked_rules"])
        self.assertFalse(candidate["eligible"])
        self.assertTrue(any("yetkinlik" in reason.lower() for reason in candidate["reasons"]))
        self.assertTrue(any("dolu" in reason.lower() for reason in candidate["reasons"]))
        self.assertEqual(candidate["hour_projection"]["weeks"], [{
            "start": "2026-10-12", "end": "2026-10-18", "scheduled_hours_before": 8.0,
            "projected_hours": 16.0, "configured_limit_hours": 36.0,
            "limit_assessment": "within_configured_limit",
        }])
        self.assertEqual(len(candidate["hour_projection"]["unknowns"]), 2)

    def test_hourly_leave_preview_shows_remaining_shift_and_never_invents_attendance_rule(self):
        self.put_schedule("2026-10", [{
            "row_id": "hourly-leave-row", "employee_id": self.person["id"], "section": "MARKET",
            "slot": 1, "days": {"2026-10-12": {"kind": "work", "start": "08:00", "end": "16:00"}},
        }])
        payload = {
            "employee_id": self.person["id"], "date": "2026-10-12", "start": "10:00", "end": "12:00",
        }
        with self.store() as store:
            before = store.attendance_month("2026-10")
            preview = linked_leave.hourly_leave_preview(store, payload)
            self.assertEqual(preview["leave"]["minutes"], 120)
            self.assertEqual(preview["remaining_intervals"], [
                {"start": "2026-10-12T08:00", "end": "2026-10-12T10:00"},
                {"start": "2026-10-12T12:00", "end": "2026-10-12T16:00"},
            ])
            self.assertFalse(preview["attendance_rule"]["configured"])
            self.assertIn("yapılandırılmamış", preview["attendance_rule"]["notice"])
            self.assertFalse(preview["saved"])
            self.assertEqual(store.attendance_month("2026-10"), before)
            linked_leave.save_hourly_leave_rule(store, {
                "description": "Kurumca onaylanmış örnek kural", "effective_date": "2026-10-01",
            })
            configured = linked_leave.hourly_leave_preview(store, payload)
            self.assertTrue(configured["attendance_rule"]["configured"])
            self.assertEqual(configured["attendance_rule"]["description"], "Kurumca onaylanmış örnek kural")
            self.assertEqual(store.attendance_month("2026-10"), before)

    def test_replacement_candidate_projection_splits_overnight_hours_across_configured_weeks(self):
        handler = Handler(self.path, {
            "employee_id": self.person["id"], "date": "2026-10-18", "section": "MARKET",
            "start": "22:00", "end": "06:00",
        })
        linked_leave.handle_write(handler, "POST", "/api/linked-leave/candidates")
        candidate = next(item for item in handler.result["candidates"]
                         if item["employee_id"] == self.other["id"])
        self.assertEqual(
            [(week["start"], week["end"], week["projected_hours"], week["configured_limit_hours"],
              week["limit_assessment"]) for week in candidate["hour_projection"]["weeks"]],
            [("2026-10-12", "2026-10-18", 2.0, None, "unconfigured"),
             ("2026-10-19", "2026-10-25", 6.0, None, "unconfigured")],
        )
        self.assertTrue(any("yapılandırılmamış" in text for text in candidate["hour_projection"]["unknowns"]))

    def test_replacement_candidates_check_every_skill_and_linked_approval(self):
        self.approve(self.add_request(employee_id=self.other["id"], kind="weekly_off"))
        with self.store() as store:
            store.save_planning_settings({
                "daily_limit": 0, "skill_requirements": [
                    {"section": "MARKET", "skill": "Forklift"},
                    {"section": "MARKET", "skill": "İlk yardım"},
                ],
            })
            store.save_employee_profile(self.other["id"], {"skills": ["Forklift"]})
        handler = Handler(self.path, {
            "employee_id": self.person["id"], "date": "2026-10-12",
            "section": "MARKET", "start": "09:00", "end": "17:00",
        })
        linked_leave.handle_write(handler, "POST", "/api/linked-leave/candidates")
        candidate = next(row for row in handler.result["candidates"]
                         if row["employee_id"] == self.other["id"])
        self.assertFalse(candidate["eligible"])
        self.assertTrue(any("İlk yardım" in reason for reason in candidate["reasons"]))
        self.assertTrue(any("Onaylı bağlı izin" in reason for reason in candidate["reasons"]))

    def test_fairness_proposals_are_suggestions_and_introduce_no_new_warning(self):
        with self.store() as store:
            store.save_schedule_month("2026-10", [
                {"row_id": "row-a", "employee_id": self.person["id"], "section": "MARKET",
                 "slot": 1, "days": {
                     "2026-10-12": {"kind": "work", "start": "09:00", "end": "17:00"},
                     "2026-10-13": {"kind": "work", "start": "09:00", "end": "17:00"},
                 }},
                {"row_id": "row-b", "employee_id": self.other["id"], "section": "MARKET",
                 "slot": 2, "days": {}},
            ], "")
        handler = Handler(self.path, {"month": "2026-10"})
        linked_leave.handle_write(handler, "POST", "/api/linked-leave/fairness-proposals")
        self.assertTrue(handler.result["proposals"])
        self.assertIn("report", handler.result)
        with self.store() as store:
            self.assertEqual(store.schedule_month("2026-10")["entries"][0]["days"]["2026-10-12"]["kind"], "work")

    def test_invalid_recurring_range_is_bounded_to_one_year(self):
        with self.assertRaisesRegex(ValueError, "bir yıl"):
            linked_leave._normalize_request({
                "employee_id": self.person["id"], "pattern": "weekdays",
                "start": "2026-01-01", "end": "2027-01-02", "weekdays": [0],
                "kind": "annual_leave",
            }, {self.person["id"]}, create=True)

    def test_bulk_fixed_leave_creates_one_request_per_selected_person(self):
        handler = Handler(self.path, {
            "employee_ids": [self.person["id"], self.other["id"]],
            "pattern": "weekdays", "start": "2026-10-01", "end": "2026-10-31",
            "weekdays": [0, 2], "kind": "weekly_off", "note": "Sabit izin",
        })
        self.assertTrue(linked_leave.handle_write(handler, "POST", "/api/linked-leave/requests/bulk"))
        self.assertEqual(handler.status, 201)
        requests = handler.result["requests"]
        self.assertEqual([item["employee_id"] for item in requests],
                         [self.person["id"], self.other["id"]])
        self.assertTrue(all(item["pattern"] == "weekdays" for item in requests))
        with self.store() as store:
            count = store.connection.execute(
                "SELECT COUNT(*) FROM linked_leave_requests"
            ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_bulk_leave_rejects_invalid_person_without_partial_records(self):
        handler = Handler(self.path, {
            "employee_ids": [self.person["id"], 99999],
            "pattern": "range", "start": "2026-10-01", "end": "2026-10-03",
            "kind": "annual_leave",
        })
        with self.assertRaisesRegex(ValueError, "Personel seçimi"):
            linked_leave.handle_write(handler, "POST", "/api/linked-leave/requests/bulk")
        with self.store() as store:
            count = store.connection.execute(
                "SELECT COUNT(*) FROM linked_leave_requests"
            ).fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
