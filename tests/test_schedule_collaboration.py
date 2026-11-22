import copy
import http.client
import json
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import scheduling
import schedule_protection
from server import create_server
from storage import Store


MONTH = "2026-11"
FIRST = f"{MONTH}-02"
SECOND = f"{MONTH}-03"
THIRD = f"{MONTH}-04"


def shift(start="08:00", end="16:00"):
    return {"kind": "work", "start": start, "end": end}


class SharedScheduleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "isolated.sqlite3"
        with Store(self.path) as store:
            person = store.add_person("Deneme Personeli")
            self.initial = [{"row_id": "row-a", "employee_id": person["id"], "section": "MARKET",
                             "slot": 1, "days": {FIRST: shift()}}]
            self.base = store.save_schedule_month(MONTH, self.initial, "")

    def edit(self, day, value, entries=None):
        result = copy.deepcopy(self.initial if entries is None else entries)
        result[0]["days"][day] = value
        return result

    def test_large_period_sparse_changes_patch_cells_and_keep_save_guards(self):
        sections = (("MARKET", 6), ("TEMİZLİK", 1), ("ÖSSE", 11))
        entries_by_month = {}
        with Store(self.path) as store:
            for month in ("2026-11", "2026-12"):
                year, number = map(int, month.split("-"))
                days = {}
                cursor = date(year, number, 1)
                while cursor.month == number:
                    days[cursor.isoformat()] = shift()
                    cursor += timedelta(days=1)
                entries = []
                index = 0
                for section, count in sections:
                    for slot in range(1, count + 1):
                        person = store.add_person(f"Large period {month} {index}")["id"]
                        entries.append({
                            "row_id": f"large-{month}-{index}", "employee_id": person,
                            "section": section, "slot": slot, "days": copy.deepcopy(days),
                        })
                        index += 1
                entries_by_month[month] = entries
            revisions = {
                month: store.save_schedule_month(month, entries, self.base if month == MONTH else "")
                for month, entries in entries_by_month.items()
            }

        server = create_server(self.path, Path(__file__).resolve().parents[1] / "web")
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def save(months, merge=False):
            with closing(http.client.HTTPConnection(*server.server_address, timeout=10)) as client:
                client.request("PUT", "/api/schedule-months", json.dumps({
                    "months": months, "merge_independent": merge,
                }).encode(), {"Content-Type": "application/json"})
                response = client.getresponse()
                return response.status, json.loads(response.read())

        first = "2026-11-01"
        second = "2026-11-02"
        row_id = "large-2026-11-0"
        change = lambda day, value: {
            "upsert_rows": [], "remove_row_ids": [],
            "day_changes": [{"row_id": row_id, "day": day, "value": value}],
        }
        with patch.object(scheduling, "_validate_time", wraps=scheduling._validate_time) as validate_time:
            status, saved = save({"2026-11": {"revision": revisions["2026-11"],
                                              "changes": change(first, shift("09:00"))}})
            self.assertEqual(status, 200, saved)
            self.assertGreaterEqual(validate_time.call_count, 18 * 30 * 2)
            validate_time.reset_mock()
            status, next_saved = save({"2026-11": {"revision": saved["revisions"]["2026-11"],
                                                   "changes": change("2026-11-04", shift("09:00"))}})
            self.assertEqual(status, 200, next_saved)
            self.assertEqual(validate_time.call_count, 2)
        current_revision = next_saved["revisions"]["2026-11"]
        with Store(self.path) as store:
            current = store.schedule_month("2026-11")["entries"]
            self.assertEqual(current[0]["days"][first], shift("09:00"))
            self.assertEqual(current[0]["days"][second], shift())
            self.assertEqual(current[0]["days"]["2026-11-04"], shift("09:00"))
            self.assertEqual(current[1:], entries_by_month["2026-11"][1:])

        status, _ = save({"2026-11": {"revision": current_revision,
                                      "changes": change("2026-11-05", shift("25:00"))}})
        self.assertEqual(status, 400)
        with Store(self.path) as store:
            self.assertEqual(store.schedule_month("2026-11")["revision"], current_revision)

        with Store(self.path) as store:
            protected = {"month": "2026-11", "row_id": row_id, "date": "2026-11-04",
                         "revision": current_revision, "protected": True}
            columns = {column["name"] for column in store.connection.execute(
                "PRAGMA table_info(schedule_protection)")}
            if "reason" in columns:
                protected["reason"] = "Seyrek kayıt koruma testi"
            schedule_protection.set_lock(store, protected)
        status, _ = save({"2026-11": {"revision": current_revision,
                                      "changes": change("2026-11-04", shift("11:00"))}})
        self.assertEqual(status, 409)
        with Store(self.path) as store:
            locked = store.schedule_month("2026-11")
            self.assertEqual(locked["revision"], current_revision)
            self.assertEqual(locked["entries"][0]["days"]["2026-11-04"], shift("09:00"))
            protected["protected"] = False
            schedule_protection.set_lock(store, protected)

        status, _ = save({"2026-11": {"revision": revisions["2026-11"],
                                      "changes": change(second, shift("10:00"))}})
        self.assertEqual(status, 409)
        status, merged = save({"2026-11": {"revision": revisions["2026-11"],
                                            "changes": change(second, shift("10:00"))}}, merge=True)
        self.assertEqual(status, 200, merged)
        current_revision = merged["revisions"]["2026-11"]
        with Store(self.path) as store:
            current = store.schedule_month("2026-11")["entries"]
            self.assertEqual(current[0]["days"][first], shift("09:00"))
            self.assertEqual(current[0]["days"][second], shift("10:00"))

        with Store(self.path) as store:
            store.set_period_locked("2026-12", True)
        status, _ = save({
            "2026-11": {"revision": current_revision, "changes": change("2026-11-03", shift("11:00"))},
            "2026-12": {"revision": revisions["2026-12"], "changes": {
                "upsert_rows": [], "remove_row_ids": [],
                "day_changes": [{"row_id": "large-2026-12-0", "day": "2026-12-03", "value": shift("11:00")}],
            }},
        })
        self.assertEqual(status, 400)
        with Store(self.path) as store:
            self.assertEqual(store.schedule_month("2026-11")["entries"][0]["days"]["2026-11-03"], shift())
            self.assertEqual(store.schedule_month("2026-12")["entries"][0]["days"]["2026-12-03"], shift())

        with Store(self.path) as store:
            store.set_period_locked("2026-11", True)
        status, _ = save({"2026-11": {"revision": current_revision,
                                      "changes": change("2026-11-03", shift("11:00"))}})
        self.assertEqual(status, 400)
        with Store(self.path) as store:
            self.assertEqual(store.schedule_month("2026-11")["entries"][0]["days"]["2026-11-03"], shift())

    def test_sparse_row_and_day_changes_survive_reload_and_reject_invalid_batch_atomically(self):
        with Store(self.path) as store:
            second = store.add_person("İkinci personel")["id"]
        added = {"row_id": "row-b", "employee_id": second, "section": "MARKET",
                 "slot": 2, "days": {SECOND: shift("09:00")}}
        server = create_server(self.path, Path(__file__).resolve().parents[1] / "web")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def save(revision, changes):
            with closing(http.client.HTTPConnection(*server.server_address, timeout=10)) as client:
                body = {"months": {MONTH: {"revision": revision, "changes": changes}}}
                client.request("PUT", "/api/schedule-months", json.dumps(body).encode(),
                               {"Content-Type": "application/json"})
                response = client.getresponse()
                return response.status, json.loads(response.read())

        status, result = save(self.base, {
            "upsert_rows": [added], "remove_row_ids": [],
            "day_changes": [{"row_id": "row-a", "day": FIRST, "value": None},
                            {"row_id": "row-a", "day": THIRD, "value": shift("10:00")}],
        })
        self.assertEqual(status, 200, result)
        revision = result["revisions"][MONTH]
        with Store(self.path) as store:
            rows = {row["row_id"]: row for row in store.schedule_month(MONTH)["entries"]}
        self.assertEqual(rows["row-a"]["days"], {THIRD: shift("10:00")})
        self.assertEqual(rows["row-b"]["days"], {SECOND: shift("09:00")})
        status, _ = save(revision, {
            "upsert_rows": [], "remove_row_ids": ["row-b"],
            "day_changes": [{"row_id": "missing", "day": SECOND, "value": shift()}],
        })
        self.assertEqual(status, 400)
        with Store(self.path) as store:
            unchanged = store.schedule_month(MONTH)
        self.assertEqual(unchanged["revision"], revision)
        self.assertEqual({row["row_id"]: row for row in unchanged["entries"]}, rows)
        status, result = save(revision, {
            "upsert_rows": [], "remove_row_ids": ["row-b"], "day_changes": [],
        })
        self.assertEqual(status, 200, result)
        with Store(self.path) as store:
            reloaded = store.schedule_month(MONTH)
        self.assertEqual([row["row_id"] for row in reloaded["entries"]], ["row-a"])
        self.assertEqual(reloaded["entries"][0]["days"], {THIRD: shift("10:00")})

    def test_interleaved_sessions_keep_independent_days_and_report_same_day_conflict(self):
        with Store(self.path) as left, Store(self.path) as right:
            left.save_schedule_month(MONTH, self.edit(FIRST, shift("09:00")), self.base)
            months = {MONTH: {"revision": self.base, "entries": self.edit(SECOND, shift())}}
            self.assertIsNone(right.save_schedule_months(months))
            self.assertTrue(right.save_schedule_months(months, merge_independent=True))
            current = right.schedule_month(MONTH)
            self.assertEqual(right.schedule_merged_months, [MONTH])
            self.assertEqual(current["entries"][0]["days"][FIRST]["start"], "09:00")
            self.assertIn(SECOND, current["entries"][0]["days"])
            conflict = {MONTH: {"revision": self.base, "entries": self.edit(FIRST, shift("10:00"))},
                        "2026-12": {"revision": "", "entries": []}}
            self.assertIsNone(right.save_schedule_months(conflict, merge_independent=True))
            self.assertEqual(right.schedule_month("2026-12")["revision"], "")
            self.assertEqual(right.schedule_month(MONTH), current)
            self.assertEqual(right.schedule_merge_conflicts[0]["saved"]["start"], "09:00")
            self.assertEqual(right.schedule_merge_conflicts[0]["draft"]["start"], "10:00")

    def test_attendance_merges_only_codes_and_preserves_saved_deductions(self):
        employee = str(self.initial[0]["employee_id"])
        with Store(self.path) as store:
            baseline = {FIRST: {employee: "S"}}
            base = store.save_attendance_month(MONTH, baseline, {}, "")
            saved = store.save_attendance_month(MONTH, {FIRST: {employee: "HT"}}, {employee: 50}, base)
            self.assertIsNone(store.save_attendance_month(
                MONTH, {FIRST: {employee: "Yİ"}}, {}, base, merge_independent=True))
            self.assertEqual(store.attendance_merge_conflicts,
                             [{"date": FIRST, "employee_id": int(employee), "saved": "HT", "draft": "Yİ"}])
            self.assertEqual(store.attendance_month(MONTH)["revision"], saved)
            draft = {**baseline, SECOND: {employee: "S"}}
            self.assertIsNone(store.save_attendance_month(MONTH, draft, {employee: 100}, base, merge_independent=True))
            self.assertTrue(store.save_attendance_month(MONTH, draft, {}, base, merge_independent=True))
            self.assertTrue(store.attendance_merged)
            self.assertEqual(store.attendance_month(MONTH)["overrides"], {FIRST: {employee: "HT"}, SECOND: {employee: "S"}})
            self.assertEqual(store.attendance_month(MONTH)["deductions"], {employee: 50})

    def test_attendance_http_reports_conflict_and_keeps_unrelated_codes(self):
        employee = str(self.initial[0]["employee_id"])
        with Store(self.path) as store:
            baseline = {FIRST: {employee: "S"}}
            base = store.save_attendance_month(MONTH, baseline, {}, "")
        server = create_server(self.path, Path(__file__).resolve().parents[1] / "web")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with closing(http.client.HTTPConnection(*server.server_address, timeout=10)) as client:
            def save(overrides, flag=True):
                client.request("PUT", "/api/attendance", json.dumps({
                    "month": MONTH, "revision": base, "overrides": overrides, "merge_independent": flag,
                }).encode(), {"Content-Type": "application/json"})
                response = client.getresponse()
                return response.status, json.loads(response.read())

            self.assertEqual(save(baseline, "yes")[0], 400)
            self.assertEqual(save({FIRST: {employee: "HT"}})[0], 200)
            status, result = save({})
            self.assertEqual(status, 409)
            self.assertEqual(result["attendance_conflicts"][0]["saved"], "HT")
            self.assertIsNone(result["attendance_conflicts"][0]["draft"])
            status, result = save({**baseline, SECOND: {employee: "S"}})
            self.assertEqual(status, 200)
            self.assertTrue(result["merged"])
            def restore(**updates):
                client.request("POST", "/api/attendance/restore-cell", json.dumps({
                    "month": MONTH, "revision": base, "expected_revision": result["revision"],
                    "employee_id": int(employee), "date": FIRST, **updates,
                }).encode(), {"Content-Type": "application/json"})
                response = client.getresponse()
                response.read()
                return response.status

            self.assertEqual(restore(employee_id=True), 400)
            self.assertEqual(restore(expected_revision=base), 409)
            with Store(self.path) as store:
                store.set_period_locked(MONTH, True)
            self.assertEqual(restore(), 400)
            with Store(self.path) as store:
                store.set_period_locked(MONTH, False)
            self.assertEqual(restore(), 200)
        with Store(self.path) as store:
            self.assertEqual(store.attendance_month(MONTH)["overrides"],
                             {FIRST: {employee: "S"}, SECOND: {employee: "S"}})

    def test_attendance_cell_restore_rejects_repeated_changes_and_can_remove_override(self):
        employee = self.initial[0]["employee_id"]
        key = str(employee)
        with Store(self.path) as store:
            empty = store.save_attendance_month(MONTH, {}, {}, "")
            first = store.save_attendance_month(MONTH, {FIRST: {key: "S"}}, {}, empty)
            current = store.save_attendance_month(MONTH, {FIRST: {key: "R"}, SECOND: {key: "H"}},
                                                  {key: 75}, first, overtime={key: 2})
            with self.assertRaisesRegex(ValueError, "birden fazla"):
                store.restore_attendance_cell(MONTH, empty, current, employee, FIRST)
            restored = store.restore_attendance_cell(MONTH, first, current, employee, FIRST)
            self.assertTrue(store.restore_attendance_cell(MONTH, first, restored, employee, SECOND))
            actual = store.attendance_month(MONTH)
            self.assertEqual(actual["overrides"], {FIRST: {key: "S"}})
            self.assertEqual((actual["deductions"], actual["overtime"]), ({key: 75}, {key: 2}))

    def test_merged_overlap_rolls_back(self):
        with Store(self.path) as store:
            saved = self.edit(SECOND, shift())
            revision = store.save_schedule_month(MONTH, saved, self.base)
            other_section = {**self.initial[0], "section": "ÖSSE", "row_id": "row-b", "days": {SECOND: shift()}}
            months = {MONTH: {"revision": self.base, "entries": self.initial + [other_section]}}
            with self.assertRaisesRegex(ValueError, "Çift kayıt"):
                store.save_schedule_months(months, merge_independent=True)
            self.assertEqual(store.schedule_month(MONTH)["revision"], revision)
            self.assertEqual(len(store.schedule_month(MONTH)["entries"]), 1)

    def test_cell_restore_preserves_unrelated_changes_and_rejects_stale_or_locked(self):
        with Store(self.path) as store:
            changed = self.edit(SECOND, shift(), self.edit(FIRST, shift("09:00")))
            revision = store.save_schedule_month(MONTH, changed, self.base)
            self.assertIsNone(store.restore_schedule_cell(MONTH, self.base, "outdated", "row-a", FIRST))
            restored = store.restore_schedule_cell(MONTH, self.base, revision, "row-a", FIRST)
            self.assertTrue(restored)
            self.assertEqual(store.schedule_month(MONTH)["entries"][0]["days"], {FIRST: shift(), SECOND: shift()})
            store.set_period_locked(MONTH, True)
            with self.assertRaisesRegex(ValueError, "Dönem kilitli"):
                store.restore_schedule_cell(MONTH, revision, restored, "row-a", FIRST)

    def test_restore_refuses_cell_changed_twice_but_allows_newer_revision(self):
        with Store(self.path) as store:
            first = store.save_schedule_month(MONTH, self.edit(FIRST, shift("09:00")), self.base)
            second = store.save_schedule_month(MONTH, self.edit(FIRST, shift("10:00")), first)
            with self.assertRaisesRegex(ValueError, "birden fazla"):
                store.restore_schedule_cell(MONTH, self.base, second, "row-a", FIRST)
            self.assertTrue(store.restore_schedule_cell(MONTH, first, second, "row-a", FIRST))
            self.assertEqual(store.schedule_month(MONTH)["entries"][0]["days"][FIRST]["start"], "09:00")

    def test_two_http_clients_observe_canonical_merge_conflict_and_narrow_restore(self):
        server = create_server(self.path, Path(__file__).resolve().parents[1] / "web")
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def send(client, path, method, payload):
            client.request(method, path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
            response = client.getresponse()
            return response.status, json.loads(response.read())

        with closing(http.client.HTTPConnection(*server.server_address, timeout=10)) as left, \
                closing(http.client.HTTPConnection(*server.server_address, timeout=10)) as right:
            def save(client, entries):
                return send(client, "/api/schedule-months", "PUT", {
                    "months": {MONTH: {"revision": self.base, "entries": entries}}, "merge_independent": True,
                })

            self.assertEqual(save(left, self.edit(FIRST, shift("09:00")))[0], 200)
            status, data = save(right, self.edit(SECOND, shift()))
            self.assertEqual(status, 200, data)
            self.assertEqual(data["merged_months"], [MONTH])
            self.assertEqual(set(data["entries"][MONTH][0]["days"]), {FIRST, SECOND})
            status, collision = save(right, self.edit(FIRST, shift("10:00")))
            self.assertEqual(status, 409, collision)
            self.assertEqual(collision["schedule_conflicts"][0]["date"], FIRST)
            status, result = send(right, "/api/schedule/restore-cell", "POST", {
                "month": MONTH, "revision": self.base, "expected_revision": data["revisions"][MONTH],
                "row_id": "row-a", "date": FIRST,
            })
            self.assertEqual(status, 200, result)
            with Store(self.path) as store:
                self.assertEqual(store.schedule_month(MONTH)["entries"][0]["days"], {FIRST: shift(), SECOND: shift()})
