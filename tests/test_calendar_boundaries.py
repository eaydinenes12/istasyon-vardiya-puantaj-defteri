from pathlib import Path
import unittest

import scheduling


class CalendarBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.staff = [{"id": 1, "name": "Örnek"}]
        self.enterContext(scheduling.restored_workbook_settings(Path(__file__).resolve().parents[1] / "templates", None, []))

    def shifts(self, day, start, end):
        return scheduling.normalize_schedule_entries(day[:7], [{
            "row_id": "edge", "employee_id": 1, "section": "MARKET",
            "days": {day: {"kind": "work", "start": start, "end": end}},
        }], self.staff)

    def hours(self, entries, month):
        row = scheduling.calculate_attendance(month, entries, {}, self.staff)["rows"][0]
        return row["worked_hours"], row["normal_hours"], row["holiday_hours"]

    def test_leap_day_and_new_year_overnights_are_split_without_double_counting(self):
        for day, following, holiday in (("2024-02-29", "2024-03", 0), ("2026-12-31", "2027-01", 2)):
            with self.subTest(day=day):
                entries = self.shifts(day, "22:00", "02:00")
                self.assertEqual(self.hours(entries, day[:7]), (2, 2, 0))
                self.assertEqual(self.hours(entries, following), (2, 2 - holiday, holiday))
        with self.assertRaises(ValueError):
            self.shifts("2025-02-29", "22:00", "02:00")

    def test_midnight_end_at_year_boundary_does_not_add_work_to_next_year(self):
        entries = self.shifts("2026-12-31", "16:00", "24:00")
        self.assertEqual(self.hours(entries, "2026-12"), (8, 8, 0))
        self.assertEqual(self.hours(entries, "2027-01"), (0, 0, 0))

    def test_same_shift_has_one_identical_breakdown_in_each_affected_month(self):
        entries = self.shifts("2026-12-31", "22:45", "03:15")
        for month in ("2026-12", "2027-01"):
            row = scheduling.calculate_attendance(month, entries * 2, {}, self.staff)["rows"][0]
            self.assertEqual(row["cross_month_shifts"], [{
                "start": "2026-12-31T22:45", "end": "2027-01-01T03:15",
                "before_minutes": 75, "after_minutes": 195, "total_minutes": 270,
            }])
        for month, schedule in (("2027-02", entries), ("2026-12", self.shifts("2026-12-31", "15:00", "24:00"))):
            row = scheduling.calculate_attendance(month, schedule, {}, self.staff)["rows"][0]
            self.assertEqual(row["cross_month_shifts"], [])

    def test_calendar_and_calculation_agree_at_half_day_boundary(self):
        for day, category, minutes in (
            ("2026-03-19", "official", 1), ("2026-03-20", "religious", 2),
            ("2026-05-26", "official", 1), ("2026-05-27", "religious", 2),
            ("2026-10-28", "official", 1), ("2026-10-29", "official", 2),
            ("2026-10-30", "weekday", 0), ("2026-10-31", "weekend", 0),
        ):
            with self.subTest(day=day):
                month = day[:7]
                self.assertEqual(scheduling.attendance_calendar(month)[day], category)
                self.assertEqual(self.hours(self.shifts(day, "12:59", "13:01"), month),
                                 (round(2 / 60, 2), round((2 - minutes) / 60, 2), round(minutes / 60, 2)))
