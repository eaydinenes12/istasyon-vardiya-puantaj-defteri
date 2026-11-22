from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from PIL import Image, ImageDraw, ImageFont

import scheduling

PAYROLL_CODES = (
    ("X", "Çalışılan gün"),
    ("G.Ç", "Geceden çıkış"),
    ("H", "Hafta tatili"),
    ("S", "Yıllık izin"),
    ("R", "Raporlu"),
    ("Ü", "Ücretsiz izin"),
    ("G", "İşe gelmedi"),
    ("E", "Evlilik izni"),
    ("K", "Karantina"),
    ("Y", "Yarım gün"),
    ("İST.", "İstifa"),
)
PAYROLL_HEADERS = (
    "Sıra", "Personel", *(f"{label} ({code})" for code, label in PAYROLL_CODES),
    "Diğer kodlar", "Boş gün", "Toplam çalışma saat", "Resmi tatil saat",
    "Fazla mesai saat", "Gece çalışma saat", "Kesinti tutarı",
)
DAY_NAMES = ("Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz")


def payroll_rows(attendance: dict[str, Any]) -> list[list[Any]]:
    known = {code for code, _ in PAYROLL_CODES}
    dates = attendance["dates"]
    result = []
    for index, row in enumerate(attendance["rows"], start=1):
        codes = [row["codes"].get(day, "") for day in dates]
        counts = {code: sum(value == code for value in codes) for code in known}
        others = defaultdict(int)
        for value in codes:
            if value and value not in known:
                others[value] += 1
        result.append([
            index,
            row["name"],
            *(counts[code] for code, _ in PAYROLL_CODES),
            ", ".join(f"{code}: {count}" for code, count in sorted(others.items())),
            sum(not value for value in codes),
            row.get("worked_hours", 0),
            row.get("holiday_hours", 0),
            row["overtime_hours"],
            row.get("night_hours", 0),
            row.get("deduction_amount") or 0,
        ])
    return result


def payroll_csv(attendance: dict[str, Any]) -> bytes:
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(PAYROLL_HEADERS)
    for row in payroll_rows(attendance):
        writer.writerow([
            str(value).replace(".", ",") if isinstance(value, float) else value for value in row
        ])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def payroll_xlsx(month: str, attendance: dict[str, Any]) -> bytes:
    year, month_number = map(int, month.split("-"))
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Bordro aktarımı"
    thin = Side(style="thin", color="9AA5B1")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    sheet["A1"] = f"{scheduling.MONTH_NAMES[month_number - 1]} {year} BORDRO AKTARIM LİSTESİ"
    sheet["A1"].font = Font(bold=True, size=14)
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(PAYROLL_HEADERS))
    sheet.append([])
    sheet.append(PAYROLL_HEADERS)
    header_fill = PatternFill("solid", fgColor="DDEBF7")
    for cell in sheet[3]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[3].height = 45
    rows = payroll_rows(attendance)
    for row in rows:
        sheet.append(row)
    last = 3 + len(rows)
    if rows:
        total_row = last + 1
        sheet.cell(total_row, 2, "TOPLAM").font = Font(bold=True)
        for column in range(3, len(PAYROLL_HEADERS) + 1):
            header = PAYROLL_HEADERS[column - 1]
            if header in ("Diğer kodlar",):
                continue
            letter = get_column_letter(column)
            cell = sheet.cell(total_row, column, f"=SUM({letter}4:{letter}{last})")
            cell.font = Font(bold=True)
        last = total_row
    for row in sheet.iter_rows(min_row=4, max_row=last, max_col=len(PAYROLL_HEADERS)):
        for cell in row:
            cell.border = border
            if cell.column != 2:
                cell.alignment = Alignment(horizontal="center")
    for column in range(1, len(PAYROLL_HEADERS) + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 28 if column == 2 else 12
    sheet.freeze_panes = "C4"
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


YEARLY_CODES = ("X", "G.Ç", "H", "S", "R", "Ü", "G", "E", "K", "Y")


def yearly_xlsx(year: int, months: list[tuple[str, dict[str, Any]]]) -> bytes:
    people: dict[int, str] = {}
    per_month: dict[int, dict[str, dict[str, float]]] = defaultdict(dict)
    for period, attendance in months:
        for row in attendance["rows"]:
            employee_id = int(row["employee_id"])
            values = list(row["codes"].values())
            if not any(values):
                continue
            people.setdefault(employee_id, row["name"])
            stats = {code: sum(value == code for value in values) for code in YEARLY_CODES}
            stats["worked_hours"] = float(row.get("worked_hours") or 0)
            stats["overtime_hours"] = float(row["overtime_hours"])
            stats["holiday_hours"] = float(row.get("holiday_hours", 0))
            per_month[employee_id][period] = stats
    order = sorted(people, key=lambda key: people[key].casefold())
    periods = [period for period, _ in months]
    thin = Side(style="thin", color="9AA5B1")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor="DDEBF7")
    workbook = Workbook()

    def write_sheet(sheet, title, headers, rows, name_width=28):
        sheet["A1"] = title
        sheet["A1"].font = Font(bold=True, size=14)
        sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
        sheet.append([])
        sheet.append(headers)
        for cell in sheet[3]:
            cell.font = Font(bold=True)
            cell.fill = header_fill
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.row_dimensions[3].height = 36
        for row in rows:
            sheet.append(row)
        last = 3 + len(rows)
        if rows:
            last += 1
            sheet.cell(last, 2, "TOPLAM").font = Font(bold=True)
            for column in range(3, len(headers) + 1):
                letter = get_column_letter(column)
                sheet.cell(last, column, f"=SUM({letter}4:{letter}{last - 1})").font = Font(bold=True)
        for line in sheet.iter_rows(min_row=4, max_row=last, max_col=len(headers)):
            for cell in line:
                cell.border = border
                if cell.column != 2:
                    cell.alignment = Alignment(horizontal="center")
        for column in range(1, len(headers) + 1):
            sheet.column_dimensions[get_column_letter(column)].width = name_width if column == 2 else 10
        sheet.freeze_panes = "C4"

    labels = dict(PAYROLL_CODES)
    total_rows = []
    for index, employee_id in enumerate(order, start=1):
        months_data = per_month[employee_id].values()
        total_rows.append([
            index, people[employee_id],
            *(sum(item[code] for item in months_data) for code in YEARLY_CODES),
            round(sum(item["worked_hours"] for item in months_data), 2),
            round(sum(item["holiday_hours"] for item in months_data), 2),
            round(sum(item["overtime_hours"] for item in months_data), 2),
        ])
    write_sheet(
        workbook.active, f"{year} YILLIK PUANTAJ ÖZETİ",
        ["Sıra", "Personel", *(f"{labels[code]} ({code})" for code in YEARLY_CODES),
         "Toplam çalışma saat", "Resmî tatil saat", "Fazla mesai saat"],
        total_rows,
    )
    workbook.active.title = "Yıllık toplam"
    month_headers = ["Sıra", "Personel", *(scheduling.MONTH_NAMES[int(p[-2:]) - 1] for p in periods)]
    for title, key in (
        ("Aylık çalışılan gün (X)", "X"), ("Aylık yıllık izin (S)", "S"),
        ("Aylık rapor (R)", "R"), ("Aylık fazla mesai saat", "overtime_hours"),
    ):
        rows = [
            [index, people[employee_id], *(per_month[employee_id].get(period, {}).get(key, 0) for period in periods)]
            for index, employee_id in enumerate(order, start=1)
        ]
        write_sheet(workbook.create_sheet(title[:31]), f"{year} – {title}", month_headers, rows)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _rest_violations(month: str, entries, staff_names: dict[int, str], minimum_hours: float):
    if not minimum_hours:
        return []
    intervals: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
    for entry in entries:
        employee_id = int(entry["employee_id"])
        if employee_id not in staff_names:
            continue
        for day_text, item in entry.get("days", {}).items():
            if item.get("kind") == "work" and item.get("start") and item.get("end"):
                intervals[employee_id].append(scheduling._time_interval(day_text, item["start"], item["end"]))
    result = []
    for employee_id, items in intervals.items():
        ordered = sorted(set(items))
        for previous, current in zip(ordered, ordered[1:]):
            if not current[0].strftime("%Y-%m") == month:
                continue
            gap = (current[0] - previous[1]).total_seconds() / 3600
            if gap < minimum_hours:
                result.append({
                    "type": "overlap" if gap < 0 else "rest",
                    "employee_id": employee_id,
                    "name": staff_names[employee_id],
                    "date": current[0].date().isoformat(),
                    "message": "Vardiya saatleri çakışıyor" if gap < 0
                    else f"{gap:.1f} saat dinlenme (en az {minimum_hours:g} saat)",
                })
    return result


WORK_CODES = {"X", "G.Ç", "Y"}
MAX_CONSECUTIVE_WORK_DAYS = 6


def _work_streaks(codes: list[str]) -> list[tuple[int, int]]:
    """Return (start, length) for runs longer than six working days without a rest day."""
    streaks = []
    start = None
    for index, code in enumerate([*codes, ""]):
        if code in WORK_CODES:
            if start is None:
                start = index
            continue
        if start is not None and index - start > MAX_CONSECUTIVE_WORK_DAYS:
            streaks.append((start, index - start))
        start = None
    return streaks


def check_report(
    month: str,
    attendance: dict[str, Any],
    entries: list[dict[str, Any]],
    balances: dict[str, Any],
    minimum_rest_hours: float,
    yearly_overtime: dict[int, float] | None = None,
    extra_items: list[dict[str, Any]] | None = None,
    break_rules: Any = (),
    person_rules: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    dates = attendance["dates"]
    items: list[dict[str, Any]] = []
    names = {int(row["employee_id"]): row["name"] for row in attendance["rows"]}
    for row in attendance["rows"]:
        employee_id = int(row["employee_id"])
        codes = [row["codes"].get(day, "") for day in dates]
        if not any(codes):
            items.append({
                "type": "no_records", "severity": "warning", "employee_id": employee_id,
                "name": row["name"], "date": None,
                "message": "Bu ay için hiç puantaj kodu yok",
            })
            continue
        resigned = next((index for index, code in enumerate(codes) if code == "İST."), None)
        empty = [
            dates[index] for index, code in enumerate(codes)
            if not code and (resigned is None or index < resigned)
        ]
        if empty:
            items.append({
                "type": "empty_days", "severity": "warning", "employee_id": employee_id,
                "name": row["name"], "date": empty[0], "dates": empty,
                "message": f"{len(empty)} gün boş: " + ", ".join(day[-2:] for day in empty),
            })
        for day in dates:
            manual = row["codes"].get(day, "")
            automatic = row["automatic_codes"].get(day, "")
            if row["sources"].get(day) == "manual" and manual != automatic:
                items.append({
                    "type": "manual_change", "severity": "info", "employee_id": employee_id,
                    "name": row["name"], "date": day,
                    "message": f"Plan {automatic or 'boş'}, puantaj {manual or 'boş'}",
                })
        if row["overtime_hours"]:
            items.append({
                "type": "overtime", "severity": "info", "employee_id": employee_id,
                "name": row["name"], "date": None,
                "message": f"{row['overtime_hours']:g} saat fazla mesai",
            })
        holiday = row.get("ubgt_hours", row.get("holiday_hours", 0))
        if holiday:
            detail = ", ".join(f"{day[-2:]}.{day[5:7]} {hours:g} sa" for day, hours in row.get("ubgt_days", {}).items())
            items.append({
                "type": "holiday_work", "severity": "info", "employee_id": employee_id,
                "name": row["name"], "date": None,
                "message": f"Resmî tatilde (UBGT) {holiday:g} saat çalışma" + (f" ({detail})" if detail else ""),
            })
        for start, length in _work_streaks(codes):
            items.append({
                "type": "weekly_rest", "severity": "warning", "employee_id": employee_id,
                "name": row["name"], "date": dates[start],
                "message": f"{dates[start][-2:]}–{dates[start + length - 1][-2:]} arası {length} gün hafta tatilsiz çalışma",
            })
    for warning in attendance["warnings"]:
        employee_id = int(warning["employee_id"])
        items.append({
            "type": "conflict", "severity": "error", "employee_id": employee_id,
            "name": names.get(employee_id, "Personel"), "date": warning["date"],
            "message": "Çakışan kodlar: " + ", ".join(warning["codes"]),
        })
    for violation in _rest_violations(month, entries, names, minimum_rest_hours):
        items.append({**violation, "severity": "error"})
    items += legal_limit_items(month, entries, names, yearly_overtime or {}, break_rules, person_rules)
    items += [item for item in extra_items or []
              if item.get("employee_id") in names or item.get("employee_id") is None
              or item.get("type") in ("no_records", "document_expiry")]
    for person in balances.get("people", []):
        if (int(person["employee_id"]) in names and person.get("has_opening")
                and person["annual_leave_remaining_days"] < 0):
            items.append({
                "type": "negative_leave", "severity": "error",
                "employee_id": int(person["employee_id"]), "name": person["name"], "date": None,
                "message": f"Yıllık izin bakiyesi {person['annual_leave_remaining_days']:g} gün",
            })
    order = {"error": 0, "warning": 1, "info": 2}
    items.sort(key=lambda item: (order[item["severity"]], item["type"], item["name"].casefold(), item["date"] or ""))
    counts = {severity: sum(item["severity"] == severity for item in items) for severity in order}
    return {"month": month, "people": len(attendance["rows"]), "counts": counts, "items": items}


def _font(size: int, bold: bool = False):
    fonts = Path("C:/Windows/Fonts")
    for name in (("arialbd.ttf", "segoeuib.ttf") if bold else ("arial.ttf", "segoeui.ttf")):
        if (fonts / name).is_file():
            return ImageFont.truetype(str(fonts / name), size)
    for name in ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


CODE_COLORS = {
    "X": "#DCEFD9", "G.Ç": "#D7E8F7", "H": "#FFF1C2", "S": "#FAD9C8", "R": "#F6D0D6",
    "Ü": "#E6DDF3", "G": "#F4B6B6", "İST.": "#D9D9D9",
}


def _person_page(month: str, row: dict[str, Any], dates: list[str], balance: dict[str, Any] | None):
    width, height = 1240, 1754
    page = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(page)
    year, month_number = map(int, month.split("-"))
    title, heading, body, small = _font(40, True), _font(30, True), _font(24), _font(20)
    margin = 80
    draw.text((margin, 70), "PERSONEL AYLIK PUANTAJ ÖZETİ", font=title, fill="#1F2A37")
    draw.text((margin, 128), f"{scheduling.MONTH_NAMES[month_number - 1]} {year}", font=heading, fill="#3B4A5A")
    draw.text((margin, 175), row["name"], font=_font(34, True), fill="#0B5394")
    draw.line((margin, 230, width - margin, 230), fill="#9AA5B1", width=2)

    cell_w = (width - 2 * margin) // 7
    cell_h = 120
    top = 260
    for index, name in enumerate(DAY_NAMES):
        x = margin + index * cell_w
        draw.rectangle((x, top, x + cell_w, top + 44), fill="#E8EEF4", outline="#9AA5B1")
        draw.text((x + cell_w / 2, top + 22), name, font=_font(22, True), fill="#1F2A37", anchor="mm")
    top += 44
    first = date.fromisoformat(dates[0])
    offset = first.weekday()
    for index, day_text in enumerate(dates):
        slot = offset + index
        x = margin + (slot % 7) * cell_w
        y = top + (slot // 7) * cell_h
        day = date.fromisoformat(day_text)
        category = scheduling.attendance_day_category(day)
        fill = scheduling.ATTENDANCE_CATEGORY_COLORS[category]
        draw.rectangle((x, y, x + cell_w, y + cell_h), fill=fill, outline="#9AA5B1")
        draw.text((x + 10, y + 8), str(day.day), font=_font(22, True), fill="#3B4A5A")
        if category in ("official", "religious"):
            marker = "R" if category == "official" else "D"
            draw.text((x + cell_w - 18, y + 22), marker, font=_font(20, True), fill="#3B4A5A", anchor="mm")
        code = row["codes"].get(day_text, "")
        if code:
            badge = CODE_COLORS.get(code, "#E2E8F0")
            draw.rounded_rectangle((x + 22, y + 46, x + cell_w - 22, y + cell_h - 14), radius=10, fill=badge)
            draw.text((x + cell_w / 2, y + 46 + (cell_h - 60) / 2), code, font=_font(32, True), fill="#1F2A37", anchor="mm")
    weeks = (offset + len(dates) + 6) // 7
    y = top + weeks * cell_h + 50

    counts = defaultdict(int)
    for day_text in dates:
        code = row["codes"].get(day_text, "")
        counts[code or "Boş"] += 1
    draw.text((margin, y), "Gün dağılımı", font=heading, fill="#1F2A37")
    y += 50
    labels = dict(PAYROLL_CODES)
    order = {code: index for index, (code, _) in enumerate(PAYROLL_CODES)}
    items = [
        (labels.get(code, "Boş gün" if code == "Boş" else "Özel kod"), code, value)
        for code, value in sorted(counts.items(), key=lambda item: (order.get(item[0], 99), item[0]))
    ]
    column_width = (width - 2 * margin) // 2
    for index, (label, code, value) in enumerate(items):
        x = margin + (index % 2) * column_width
        row_y = y + (index // 2) * 38
        draw.text((x, row_y), f"{label} ({code})", font=body, fill="#3B4A5A")
        draw.text((x + column_width - 40, row_y), str(value), font=_font(24, True), fill="#1F2A37", anchor="ra")
    y += ((len(items) + 1) // 2) * 38 + 30

    draw.text((margin, y), "Saatler ve bakiyeler", font=heading, fill="#1F2A37")
    y += 50
    facts = [("Toplam çalışma saati", f"{row.get('worked_hours', 0):g}")]
    if row.get("holiday_hours"):
        facts.append(("Resmi tatil çalışma saati", f"{row['holiday_hours']:g}"))
    facts += [
        ("Fazla mesai saati", f"{row['overtime_hours']:g}"),
        ("Kesinti tutarı", f"{row.get('deduction_amount') or 0:g}"),
    ]
    if balance:
        facts += [
            ("Yıllık izin devri (gün)", f"{balance['annual_leave_opening_days']:g}"),
            ("Yıl içinde kullanılan izin", f"{balance['annual_leave_used_days']:g}"),
            ("Kalan yıllık izin", f"{balance['annual_leave_remaining_days']:g}"),
            ("Yıl içi rapor günü", f"{balance['sick_days']:g}"),
            ("Yıl içi ücretsiz izin", f"{balance['unpaid_leave_days']:g}"),
        ]
    for index, (label, value) in enumerate(facts):
        x = margin + (index % 2) * column_width
        row_y = y + (index // 2) * 38
        color = "#B42318" if label == "Kalan yıllık izin" and value.startswith("-") else "#1F2A37"
        draw.text((x, row_y), label, font=body, fill="#3B4A5A")
        draw.text((x + column_width - 40, row_y), value, font=_font(24, True), fill=color, anchor="ra")
    y += ((len(facts) + 1) // 2) * 38 + 30

    legend_y = max(y, height - 330)
    x = margin
    for category, label in (("weekday", "İş günü"), ("weekend", "Hafta sonu"), ("official", "Resmî tatil (R)"), ("religious", "Dinî bayram (D)")):
        draw.rectangle((x, legend_y, x + 28, legend_y + 28), fill=scheduling.ATTENDANCE_CATEGORY_COLORS[category], outline="#9AA5B1")
        draw.text((x + 38, legend_y + 2), label, font=small, fill="#3B4A5A")
        x += 250
    sign_y = height - 220
    for index, label in enumerate(("Personel imza", "Yetkili imza")):
        x = margin + index * column_width
        draw.line((x, sign_y + 60, x + column_width - 80, sign_y + 60), fill="#1F2A37", width=2)
        draw.text((x, sign_y + 72), label, font=body, fill="#3B4A5A")
    draw.text((margin, height - 70), f"Oluşturma: {datetime.now().strftime('%d.%m.%Y %H:%M')} · Vardiya Puantaj", font=small, fill="#6B7785")
    return page


def person_summary_pdf(
    month: str, attendance: dict[str, Any], balances: dict[str, Any], employee_id: int | None = None,
) -> bytes:
    rows = attendance["rows"]
    if employee_id is not None:
        rows = [row for row in rows if int(row["employee_id"]) == employee_id]
        if not rows:
            raise ValueError("Seçilen personel bu ayın puantajında yok.")
    if not rows:
        raise ValueError("Bu ay için puantajda personel yok.")
    by_id = {int(item["employee_id"]): item for item in balances.get("people", [])}
    pages = [_person_page(month, row, attendance["dates"], by_id.get(int(row["employee_id"]))) for row in rows]
    buffer = BytesIO()
    pages[0].save(buffer, "PDF", save_all=True, append_images=pages[1:], resolution=150)
    return buffer.getvalue()


WEEKLY_LEGAL_LIMIT_HOURS = 45
YEARLY_OVERTIME_LIMIT_HOURS = 270
NIGHT_SHIFT_LIMIT_HOURS = 7.5


def _work_intervals(entries) -> dict[int, list[tuple[datetime, datetime]]]:
    intervals: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
    for row in entries:
        for day_text, item in row.get("days", {}).items():
            if item.get("kind") == "work":
                intervals[int(row["employee_id"])].append(
                    scheduling._time_interval(day_text, item["start"], item["end"])
                )
    return intervals


def legal_limit_items(
    month: str, entries, names: dict[int, str], yearly_overtime: dict[int, float],
    break_rules: Any = (), person_rules: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Law 4857 checks: 45 h/week (art. 63), 7.5 h night work (art. 69), 270 h/yr overtime (art. 41).

    The weekly check uses net working time: configured breaks (mola kademeleri) are deducted per
    shift day, unless the person card is break-exempt.
    """
    dates = scheduling.month_dates(month)
    items: list[dict[str, Any]] = []
    for employee_id, intervals in _work_intervals(entries).items():
        if employee_id not in names:
            continue
        _, rules = scheduling.person_overtime_rule((person_rules or {}).get(employee_id), 0, break_rules)
        daily = scheduling.daily_overtime_details(sorted(intervals), 0, rules) if rules else {}
        cursor = dates[0] - timedelta(days=dates[0].weekday())
        while cursor <= dates[-1]:
            begin = datetime.combine(cursor, datetime.min.time())
            gross = scheduling._union_minutes(intervals, begin, begin + timedelta(days=7))
            breaks = sum(
                daily.get((cursor + timedelta(days=offset)).isoformat(), {}).get("break", 0) for offset in range(7)
            )
            minutes = max(0, gross - breaks)
            if minutes > WEEKLY_LEGAL_LIMIT_HOURS * 60:
                note = (f"molalar düşüldü, brüt {gross / 60:g} saat" if breaks
                        else "mola kuralı tanımlı değil; brüt süre")
                items.append({
                    "type": "legal_weekly", "severity": "warning", "employee_id": employee_id,
                    "name": names[employee_id], "date": max(cursor, dates[0]).isoformat(),
                    "message": f"{cursor.strftime('%d.%m')} haftası net {minutes / 60:g} saat; "
                               f"yasal haftalık {WEEKLY_LEGAL_LIMIT_HOURS} saat aşılıyor ({note})",
                })
            cursor += timedelta(days=7)
        for start, end in intervals:
            if start.strftime("%Y-%m") != month:
                continue
            night = 0
            day = start.date() - timedelta(days=1)
            while day <= end.date():
                night_begin = max(start, datetime.combine(day, scheduling.NIGHT_START))
                night_end = min(end, datetime.combine(day + timedelta(days=1), scheduling.NIGHT_END))
                if night_begin < night_end:
                    night += (night_end - night_begin).total_seconds() / 60
                day += timedelta(days=1)
            if night >= 180 and (end - start).total_seconds() / 3600 > NIGHT_SHIFT_LIMIT_HOURS:
                items.append({
                    "type": "legal_night", "severity": "warning", "employee_id": employee_id,
                    "name": names[employee_id], "date": start.date().isoformat(),
                    "message": f"{start.strftime('%d.%m %H:%M')} gece vardiyası {(end - start).total_seconds() / 3600:g} saat; "
                               f"gece çalışması {NIGHT_SHIFT_LIMIT_HOURS:g} saati aşamaz",
                })
    for employee_id, hours in yearly_overtime.items():
        if employee_id in names and hours > YEARLY_OVERTIME_LIMIT_HOURS:
            items.append({
                "type": "legal_yearly_overtime", "severity": "error", "employee_id": employee_id,
                "name": names[employee_id], "date": None,
                "message": f"Yıl içi fazla mesai {hours:g} saat; yasal sınır {YEARLY_OVERTIME_LIMIT_HOURS} saat",
            })
        elif employee_id in names and hours > YEARLY_OVERTIME_LIMIT_HOURS * 0.85:
            items.append({
                "type": "legal_yearly_overtime", "severity": "info", "employee_id": employee_id,
                "name": names[employee_id], "date": None,
                "message": f"Yıl içi fazla mesai {hours:g} saat; {YEARLY_OVERTIME_LIMIT_HOURS} saat sınırına yaklaşıyor",
            })
    return items


def leave_entitlement(hire_date: str, birth_date: str, on_date: date) -> dict[str, Any] | None:
    """Annual paid leave per Law 4857 art. 53 (14 / 20 / 26 days; min 20 under 18 or 50+)."""
    if not hire_date:
        return None
    hired = date.fromisoformat(hire_date)
    years = on_date.year - hired.year - ((on_date.month, on_date.day) < (hired.month, hired.day))
    if years < 1:
        return {"years": max(years, 0), "days": 0, "reason": "1 yıl dolmadı"}
    days = 14 if years < 5 else 20 if years < 15 else 26
    reason = f"{years} yıl kıdem"
    if birth_date:
        born = date.fromisoformat(birth_date)
        age = on_date.year - born.year - ((on_date.month, on_date.day) < (born.month, born.day))
        if (age < 18 or age >= 50) and days < 20:
            days, reason = 20, f"{reason}, {age} yaş (en az 20 gün)"
    return {"years": years, "days": days, "reason": reason}


SCHEDULE_KIND_LABELS = {
    "weekly_off": "Hafta tatili", "overnight_exit": "Geceden çıkış", "annual_leave": "Yıllık izin",
    "sick_leave": "Raporlu", "unpaid_leave": "Ücretsiz izin", "marriage_leave": "Evlilik izni",
    "quarantine": "Karantina", "half_day": "Yarım gün", "resignation": "İstifa", "absent": "İşe gelmedi",
}
FULL_DAY_NAMES = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")


def program_days(month: str, entries, employee_id: int) -> list[dict[str, str]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in entries:
        if int(row["employee_id"]) == employee_id:
            for day_text, item in row.get("days", {}).items():
                by_day[day_text].append({**item, "section": row.get("section", "")})
    result = []
    for day in scheduling.month_dates(month):
        items = by_day.get(day.isoformat(), [])
        texts = []
        for item in items:
            if item["kind"] == "work":
                text = f"{item['start']}–{item['end']}"
                if item.get("shift_label"):
                    text += f" ({item['shift_label']})"
            elif item["kind"] == "custom":
                text = item.get("custom_label") or item.get("custom_code", "")
            else:
                text = SCHEDULE_KIND_LABELS.get(item["kind"], item["kind"])
            if item.get("section"):
                text += f" · {item['section']}"
            texts.append(text)
        result.append({
            "date": day.isoformat(), "weekday": FULL_DAY_NAMES[day.weekday()],
            "text": " / ".join(texts), "note": " ".join(item.get("note", "") for item in items).strip(),
            "category": scheduling.attendance_day_category(day),
        })
    return result


def _program_page(month: str, name: str, days: list[dict[str, str]]):
    width, height = 1240, 1754
    page = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(page)
    year, month_number = map(int, month.split("-"))
    margin = 80
    draw.text((margin, 60), "KİŞİSEL VARDİYA PROGRAMI", font=_font(38, True), fill="#1F2A37")
    draw.text((margin, 112), f"{scheduling.MONTH_NAMES[month_number - 1]} {year}", font=_font(28, True), fill="#3B4A5A")
    draw.text((margin, 156), name, font=_font(32, True), fill="#0B5394")
    top, row_h = 215, 44
    widths = (110, 190, 520, width - 2 * margin - 820)
    headers = ("Tarih", "Gün", "Vardiya", "Not")
    x = margin
    for header, column in zip(headers, widths):
        draw.rectangle((x, top, x + column, top + row_h), fill="#DDEBF7", outline="#9AA5B1")
        draw.text((x + 10, top + row_h / 2), header, font=_font(22, True), fill="#1F2A37", anchor="lm")
        x += column
    body = _font(21)
    for index, item in enumerate(days):
        y = top + row_h * (index + 1)
        fill = scheduling.ATTENDANCE_CATEGORY_COLORS[item["category"]]
        values = (item["date"][8:] + "." + item["date"][5:7], item["weekday"], item["text"] or "—", item["note"])
        x = margin
        for value, column in zip(values, widths):
            draw.rectangle((x, y, x + column, y + row_h), fill=fill, outline="#C3CCD5")
            text = value
            while text and draw.textlength(text, font=body) > column - 16:
                text = text[:-2] + "…"
            draw.text((x + 8, y + row_h / 2), text, font=body, fill="#1F2A37", anchor="lm")
            x += column
    draw.text((margin, height - 70), f"Oluşturma: {datetime.now().strftime('%d.%m.%Y %H:%M')} · Vardiya Puantaj", font=_font(20), fill="#6B7785")
    return page


def person_program_pdf(month: str, entries, staff: list[dict[str, Any]], employee_id: int | None = None) -> bytes:
    people = [person for person in staff if employee_id is None or int(person["id"]) == employee_id]
    planned = {int(row["employee_id"]) for row in entries}
    people = [person for person in people if int(person["id"]) in planned]
    if not people:
        raise ValueError("Bu ay için vardiya planında seçilen personel yok.")
    pages = [_program_page(month, person["name"], program_days(month, entries, int(person["id"]))) for person in people]
    buffer = BytesIO()
    pages[0].save(buffer, "PDF", save_all=True, append_images=pages[1:], resolution=150)
    return buffer.getvalue()