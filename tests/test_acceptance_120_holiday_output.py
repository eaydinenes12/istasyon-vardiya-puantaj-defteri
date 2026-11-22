import tempfile
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook
from PIL import Image, ImageDraw
from pypdf import PdfReader

import reports
import scheduling


class HolidayOutputTests(unittest.TestCase):
    def test_export_marks_holiday_types_without_replacing_numeric_dates(self):
        holidays = {date(2026, 10, 13): "public", date(2026, 10, 14): "religious"}
        attendance = {"rows": [{"employee_id": 1, "name": "Kabul",
                                "codes": {"2026-10-13": "X", "2026-10-14": "H"}}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(
            scheduling, "_active_holiday", side_effect=lambda day: holidays.get(day)
        ):
            output = Path(folder) / "attendance.xlsx"
            scheduling.export_attendance_xlsx(output, "2026-10", attendance)
            sheet = load_workbook(output).active
            official, religious, ordinary = (sheet.cell(2, 15), sheet.cell(2, 16), sheet.cell(2, 17))
            self.assertEqual((official.value, religious.value, ordinary.value), (13, 14, 15))
            self.assertEqual((official.number_format, religious.number_format), ('0"R"', '0"D"'))
            self.assertNotIn('"R"', ordinary.number_format)
            self.assertNotIn('"D"', ordinary.number_format)
            self.assertIn("R: Resmî tatil", sheet["A1"].value)
            self.assertIn("D: Dinî bayram", sheet["A1"].value)
            self.assertEqual(sheet.cell(3, 15).value.date(), date(2026, 10, 13))
            self.assertEqual((sheet.cell(4, 15).value, sheet.cell(4, 16).value), ("X", "H"))

    def test_pdf_holiday_markers_remain_distinct_without_calendar_colors(self):
        holidays = {date(2026, 10, 13): "public", date(2026, 10, 14): "religious"}
        dates = [day.isoformat() for day in scheduling.month_dates("2026-10")]
        attendance = {"dates": dates, "rows": [{"employee_id": 1, "name": "Kabul",
                      "codes": {}, "overtime_hours": 0}]}
        colors = {category: "#FFFFFF" for category in scheduling.ATTENDANCE_CATEGORY_COLORS}
        with patch.object(scheduling, "_active_holiday", side_effect=lambda day: holidays.get(day)), \
                patch.object(scheduling, "ATTENDANCE_CATEGORY_COLORS", colors):
            output = reports.person_summary_pdf("2026-10", attendance, {})
        page = PdfReader(BytesIO(output)).pages[0].images[0].image.convert("L")
        for day_number, marker in ((13, "R"), (14, "D")):
            slot = date(2026, 10, 1).weekday() + day_number - 1
            x = 80 + (slot % 7) * ((1240 - 160) // 7) + ((1240 - 160) // 7) - 18
            y = 304 + (slot // 7) * 120 + 22
            crop = page.crop((x - 15, y - 14, x + 15, y + 14))
            actual = {index for index, value in enumerate(crop.tobytes()) if value < 160}
            scores = {}
            for candidate in ("R", "D"):
                expected_image = Image.new("L", (30, 28), 255)
                ImageDraw.Draw(expected_image).text((15, 14), candidate, font=reports._font(20, True),
                                                    fill=75, anchor="mm")
                expected = {index for index, value in enumerate(expected_image.tobytes()) if value < 160}
                scores[candidate] = len(actual & expected) / len(actual | expected)
            self.assertGreater(scores[marker], 0.7, scores)
            self.assertGreater(scores[marker], scores["D" if marker == "R" else "R"], scores)


if __name__ == "__main__":
    unittest.main()
