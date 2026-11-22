"""Bordro ve SGK yardımcıları (C grubu).

Varsayımlar (kullanıcıya bildirilir):
- SGK eksik gün nedenleri: R → 01 İstirahat, Ü → 21 Diğer ücretsiz izin, G → 15 Devamsızlık;
  birden fazla neden varsa 12 Birden fazla.
- Prim günü: ayın tamamında çalışan için 30 − eksik gün; ay içinde giren/çıkan için
  istihdam edilen takvim günü (en fazla 30) − eksik gün.
- Fazla mesai puantajla birebir aynıdır: her gün günlük sınırı (varsayılan 8 saat) aşan süre, mola ve
  resmî tatil saatleri düşülerek; puantajda elle düzeltilen FM değeri esas alınır.
- Hastalık raporunda SGK geçici iş göremezlik ödeneği 3. günden başlar; iş kazası,
  meslek hastalığı ve analıkta ilk günden ödenir.
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import scheduling

SGK_REASONS = {"R": ("01", "İstirahat"), "Ü": ("21", "Diğer ücretsiz izin"), "G": ("15", "Devamsızlık")}
SGK_MULTIPLE = ("12", "Birden fazla")
WORK_DAY_CODES = {"X", "G.Ç", "Y"}
REPORT_TYPES = {
    "illness": "Hastalık", "work_accident": "İş kazası",
    "occupational": "Meslek hastalığı", "maternity": "Analık",
}
EXPORT_FORMATS = ("logo", "mikro", "netsis")
_FIELDS = (
    "payroll_code", "name", "period", "sgk_days", "missing_days", "missing_code", "work_days",
    "weekly_off", "annual_leave", "sick", "unpaid", "absent", "worked_hours", "overtime_hours",
    "ubgt_hours", "night_hours", "deduction",
    "meal_days", "transport_days", "advances", "comp_leave_hours", "deduction_note",
)
_HEADERS = {
    "logo": (
        "Sicil No", "Adı Soyadı", "Dönem", "SGK Gün", "Eksik Gün", "Eksik Gün Nedeni", "Çalışma Günü",
        "Hafta Tatili", "Yıllık İzin", "Raporlu Gün", "Ücretsiz İzin", "Devamsızlık", "Toplam Çalışma Saati",
        "Fazla Mesai Saat", "Genel Tatil Mesaisi Saat",
        "Gece Çalışma Saati", "Kesinti Tutarı",
        "Yemek Günü", "Yol Günü", "Avans Tutarı", "Telafi İzni Saat", "Kesinti Açıklaması",
    ),
    "mikro": (
        "PERSONEL KODU", "ADI SOYADI", "DÖNEM", "SGK GÜN SAYISI", "EKSİK GÜN SAYISI", "EKSİK GÜN KODU",
        "FİİLİ ÇALIŞMA GÜNÜ", "HAFTA TATİLİ GÜNÜ", "YILLIK İZİN GÜNÜ", "İSTİRAHAT GÜNÜ", "ÜCRETSİZ İZİN GÜNÜ",
        "DEVAMSIZLIK GÜNÜ", "TOPLAM ÇALIŞMA SAAT", "FAZLA MESAİ SAAT", "UBGT SAAT", "GECE SAAT", "KESİNTİ",
        "YEMEK GÜNÜ", "YOL GÜNÜ", "AVANS", "TELAFİ İZNİ SAAT", "KESİNTİ AÇIKLAMA",
    ),
    "netsis": (
        "SICIL_NO", "AD_SOYAD", "DONEM", "SGK_GUN", "EKSIK_GUN", "EKSIK_GUN_NEDEN", "CALISMA_GUN",
        "HAFTA_TATILI", "YILLIK_IZIN", "RAPOR_GUN", "UCRETSIZ_IZIN", "DEVAMSIZLIK", "CALISMA_SAAT",
        "FM_SAAT", "UBGT_SAAT", "GECE_SAAT", "KESINTI",
        "YEMEK_GUN", "YOL_GUN", "AVANS", "TELAFI_IZNI_SAAT", "KESINTI_ACIKLAMA",
    ),
}


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def employment_window(month: str, profile: dict[str, Any], codes: dict[str, str]) -> tuple[date, date] | None:
    dates = scheduling.month_dates(month)
    first, last = dates[0], dates[-1]
    hired = _date(profile.get("hire_date"))
    exit_day = _date(profile.get("exit_date"))
    resigned = next((date.fromisoformat(day) for day in sorted(codes) if codes[day] == "İST."), None)
    if hired and hired > first:
        first = hired
    if exit_day and exit_day < last:
        last = exit_day
    if resigned and resigned - timedelta(days=1) < last:
        last = resigned - timedelta(days=1)
    return (first, last) if first <= last else None


def sgk_summary(month: str, attendance: dict[str, Any], profiles: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    dates = scheduling.month_dates(month)
    rows = []
    for row in attendance["rows"]:
        profile = profiles.get(str(row["employee_id"]), {})
        window = employment_window(month, profile, row["codes"])
        reasons: dict[str, int] = defaultdict(int)
        counts: dict[str, int] = defaultdict(int)
        empty = []
        employed = 0
        if window:
            for day in dates:
                if not window[0] <= day <= window[1]:
                    continue
                employed += 1
                code = row["codes"].get(day.isoformat(), "")
                counts[code] += 1
                if code in SGK_REASONS:
                    reasons[SGK_REASONS[code][0]] += 1
                elif not code:
                    empty.append(day.isoformat())
        missing = sum(reasons.values())
        full_month = bool(window) and window == (dates[0], dates[-1])
        base = 30 if full_month else min(employed, 30)
        if len(reasons) > 1:
            code, label = SGK_MULTIPLE
        elif reasons:
            code = next(iter(reasons))
            label = next(text for value, text in SGK_REASONS.values() if value == code)
        else:
            code, label = "", ""
        rows.append({
            "employee_id": row["employee_id"], "name": row["name"],
            "payroll_code": profile.get("payroll_code", ""),
            "employed_from": window[0].isoformat() if window else None,
            "employed_to": window[1].isoformat() if window else None,
            "employed_days": employed, "full_month": full_month,
            "missing_days": missing, "missing_by_reason": dict(sorted(reasons.items())),
            "missing_code": code, "missing_label": label,
            "sgk_days": max(0, base - missing),
            "work_days": sum(counts[value] for value in WORK_DAY_CODES),
            "weekly_off": counts["H"], "annual_leave": counts["S"], "sick": counts["R"],
            "unpaid": counts["Ü"], "absent": counts["G"], "empty_days": empty,
        })
    return rows


def overtime_breakdown(month: str, attendance: dict[str, Any]) -> list[dict[str, Any]]:
    """Kişi başı fazla mesai: puantajdaki günlük FM (elle düzeltme dahil) ve hafta kırılımı."""
    dates = scheduling.month_dates(month)
    result = []
    for row in attendance["rows"]:
        days = row.get("overtime_days") or {}
        weeks = []
        cursor = dates[0] - timedelta(days=dates[0].weekday())
        while cursor <= dates[-1]:
            hours = sum(float(days.get((cursor + timedelta(days=offset)).isoformat(), 0)) for offset in range(7))
            if hours:
                weeks.append({"week": cursor.isoformat(), "hours": round(hours, 2)})
            cursor += timedelta(days=7)
        result.append({
            "employee_id": row["employee_id"], "name": row["name"], "weeks": weeks,
            "overtime_hours": round(float(row.get("overtime_hours") or 0), 2),
            "overtime_manual": bool(row.get("overtime_manual")),
            "worked_hours": round(float(row.get("worked_hours") or 0), 2),
            "ubgt_hours": row.get("ubgt_hours", 0),
            "night_hours": row.get("night_hours", 0),
        })
    return result


def export_rows(month: str, attendance, sgk: list[dict], overtime: list[dict],
                extras: list[dict] | None = None) -> list[dict[str, Any]]:
    by_sgk = {item["employee_id"]: item for item in sgk}
    by_ot = {item["employee_id"]: item for item in overtime}
    by_extra = {item["employee_id"]: item for item in extras or []}
    rows = []
    for row in attendance["rows"]:
        social, extra = by_sgk[row["employee_id"]], by_ot[row["employee_id"]]
        added = by_extra.get(row["employee_id"], {})
        rows.append({
            "payroll_code": social["payroll_code"], "name": row["name"], "period": month,
            "sgk_days": social["sgk_days"], "missing_days": social["missing_days"],
            "missing_code": social["missing_code"], "work_days": social["work_days"],
            "weekly_off": social["weekly_off"], "annual_leave": social["annual_leave"],
            "sick": social["sick"], "unpaid": social["unpaid"], "absent": social["absent"],
            "worked_hours": extra["worked_hours"], "overtime_hours": extra["overtime_hours"],
            "ubgt_hours": added.get("paid_ubgt_hours", extra["ubgt_hours"]),
            "night_hours": extra["night_hours"], "deduction": row.get("deduction_amount") or 0,
            "meal_days": added.get("meal_days", social["work_days"]),
            "transport_days": added.get("transport_days", social["work_days"]),
            "advances": added.get("advances_total", 0),
            "comp_leave_hours": added.get("comp_earned_hours", 0),
            "deduction_note": added.get("deduction_note", ""),
        })
    return rows


def reconcile_accounting_csv(month: str, attendance: dict[str, Any], profiles: dict[str, dict[str, Any]],
                             filename: str, content: str) -> dict[str, Any]:
    month = scheduling.validate_month(month)
    if not isinstance(filename, str) or not filename.strip() or len(filename) > 400:
        raise ValueError("Mutabakat dosya adı geçersiz.")
    if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > 2_000_000:
        raise ValueError("Mutabakat CSV'si boş veya izin verilen boyuttan büyük.")
    try:
        reader = csv.DictReader(StringIO(content))
        headers = reader.fieldnames or []
        identity = "employee_id" if "employee_id" in headers else "payroll_code" if "payroll_code" in headers else ""
        if not identity or set(headers) != {identity, "date", "code", "hours"} or len(headers) != 4:
            raise ValueError("CSV başlıkları employee_id/payroll_code, date, code ve hours olmalıdır.")
        if len(content.splitlines()) > 20_000:
            raise ValueError("Mutabakat dosyası en fazla 20.000 satır içerebilir.")
        external: dict[tuple[int, str], dict[str, Any]] = {}
        people = {int(row["employee_id"]): row for row in attendance.get("rows", [])}
        payroll_codes: dict[str, list[int]] = {}
        for employee_id, profile in profiles.items():
            payroll_code = str(profile.get("payroll_code") or "").strip()
            if payroll_code:
                payroll_codes.setdefault(payroll_code, []).append(int(employee_id))
        for line_number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"CSV satırı {line_number} eksik alan içeriyor.")
            if identity == "employee_id":
                raw_id = str(row[identity]).strip()
                if not raw_id.isdigit() or int(raw_id) not in people:
                    raise ValueError(f"CSV satırı {line_number}: personel kimliği bulunamadı.")
                employee_id = int(raw_id)
            else:
                matches = payroll_codes.get(str(row[identity]).strip(), [])
                if len(matches) != 1 or matches[0] not in people:
                    raise ValueError(f"CSV satırı {line_number}: bordro kodu bulunamadı veya tekil değil.")
                employee_id = matches[0]
            try:
                day = date.fromisoformat(str(row["date"]).strip()).isoformat()
            except ValueError as exc:
                raise ValueError(f"CSV satırı {line_number}: tarih YYYY-AA-GG olmalıdır.") from exc
            if day[:7] != month:
                raise ValueError(f"CSV satırı {line_number}: tarih seçilen dönemin dışında.")
            try:
                code = scheduling.normalize_attendance_code(str(row["code"]).strip())
            except ValueError as exc:
                raise ValueError(f"CSV satırı {line_number}: puantaj kodu geçersiz.") from exc
            try:
                hours = Decimal(str(row["hours"]).strip())
            except InvalidOperation as exc:
                raise ValueError(f"CSV satırı {line_number}: saat sayısal olmalıdır.") from exc
            if not hours.is_finite() or hours < 0 or hours > Decimal("24"):
                raise ValueError(f"CSV satırı {line_number}: saat 0–24 arasında olmalıdır.")
            key = (employee_id, day)
            if key in external:
                raise ValueError(f"CSV satırı {line_number}: kişi ve gün birden fazla kez var.")
            external[key] = {"code": code, "hours": hours.quantize(Decimal("0.01"))}
    except csv.Error as exc:
        raise ValueError("Mutabakat CSV'si okunamadı.") from exc

    expected: dict[tuple[int, str], dict[str, Any]] = {}
    for row in attendance.get("rows", []):
        employee_id = int(row["employee_id"])
        codes = row.get("codes") or {}
        hours_by_day = row.get("worked_hours_by_day") or {}
        for day in scheduling.month_dates(month):
            day_text = day.isoformat()
            code = str(codes.get(day_text) or "")
            hours = Decimal(str(hours_by_day.get(day_text, 0))).quantize(Decimal("0.01"))
            if code or hours:
                expected[(employee_id, day_text)] = {"code": code, "hours": hours}
    differences = []
    for employee_id, day in sorted(set(expected) | set(external), key=lambda key: (key[1], key[0])):
        before, after = expected.get((employee_id, day)), external.get((employee_id, day))
        if before == after:
            continue
        person = people[employee_id]
        fields = []
        for field in ("code", "hours"):
            expected_value = before[field] if before else ("" if field == "code" else Decimal("0.00"))
            external_value = after[field] if after else ("" if field == "code" else Decimal("0.00"))
            if expected_value != external_value:
                fields.append({
                    "field": field, "expected": str(expected_value),
                    "external": str(external_value),
                })
        if not fields:
            continue
        differences.append({
            "employee_id": employee_id, "name": person["name"], "date": day,
            "expected_present": before is not None, "external_present": after is not None,
            "fields": fields,
        })
    expected_hours = sum((item["hours"] for item in expected.values()), Decimal("0.00"))
    external_hours = sum((item["hours"] for item in external.values()), Decimal("0.00"))
    expected_code_counts = {code: sum(item["code"] == code for item in expected.values())
                            for code in sorted({item["code"] for item in expected.values()} - {""})}
    external_code_counts = {code: sum(item["code"] == code for item in external.values())
                            for code in sorted({item["code"] for item in external.values()} - {""})}
    return {
        "month": month, "file": Path(filename).name[:120],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "identity_field": identity, "expected_rows": len(expected), "external_rows": len(external),
        "expected_hours": str(expected_hours), "external_hours": str(external_hours),
        "hours_total_equal": expected_hours == external_hours,
        "expected_code_counts": expected_code_counts, "external_code_counts": external_code_counts,
        "code_totals_equal": expected_code_counts == external_code_counts,
        "matches": not differences, "difference_count": len(differences), "differences": differences,
        "scope": "Yalnızca seçilen dönemin kayıtlı puantaj kodu ve hesaplanmış günlük çalışma saati karşılaştırıldı; ücret hesabı yapılmadı.",
    }


def payroll_program_file(month: str, rows: list[dict[str, Any]], fmt: str) -> tuple[bytes, str, str]:
    if fmt not in EXPORT_FORMATS:
        raise ValueError("Bordro programı biçimi logo, mikro veya netsis olmalıdır.")
    headers = _HEADERS[fmt]
    values = [[row[field] for field in _FIELDS] for row in rows]
    if fmt == "netsis":
        buffer = StringIO()
        writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
        writer.writerow(headers)
        for line in values:
            writer.writerow([f"{value:.2f}".replace(".", ",") if isinstance(value, float) else value for value in line])
        return ("\ufeff" + buffer.getvalue()).encode("utf-8"), f"NETSIS_PUANTAJ_{month}.csv", "text/csv"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Puantaj"
    sheet.append(headers)
    thin = Side(style="thin", color="9AA5B1")
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDEBF7")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for line in values:
        sheet.append(line)
    for column in range(1, len(headers) + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 26 if column == 2 else 14
    sheet.row_dimensions[1].height = 36
    sheet.freeze_panes = "C2"
    buffer = BytesIO()
    workbook.save(buffer)
    return (
        buffer.getvalue(), f"{fmt.upper()}_PUANTAJ_{month}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def normalize_sick_report(item: Any, employee_ids: set[int]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("Rapor kaydı geçersiz.")
    employee_id = item.get("employee_id")
    if not isinstance(employee_id, int) or isinstance(employee_id, bool) or employee_id not in employee_ids:
        raise ValueError("Personel seçimi geçersiz.")
    start, end = _date(item.get("start")), _date(item.get("end"))
    if not start or not end or end < start or (end - start).days > 365:
        raise ValueError("Rapor tarihleri geçersiz (bitiş başlangıçtan önce olamaz).")
    kind = item.get("type", "illness")
    if kind not in REPORT_TYPES:
        raise ValueError("Rapor türü geçersiz.")
    text = {}
    for field, limit in (("report_no", 40), ("issuer", 80), ("notes", 200)):
        value = item.get(field, "") or ""
        if not isinstance(value, str) or len(value.strip()) > limit:
            raise ValueError(f"Rapor alanı en fazla {limit} karakter olabilir.")
        text[field] = value.strip()
    return {"employee_id": employee_id, "start": start.isoformat(), "end": end.isoformat(), "type": kind, **text}


def sick_report_summary(month: str, records: list[dict[str, Any]], attendance: dict[str, Any]) -> dict[str, Any]:
    dates = [date.fromisoformat(day) for day in attendance["dates"]]
    names = {row["employee_id"]: row["name"] for row in attendance["rows"]}
    codes = {row["employee_id"]: row["codes"] for row in attendance["rows"]}
    covered: dict[int, set[str]] = defaultdict(set)
    items = []
    for record in records:
        start, end = date.fromisoformat(record["start"]), date.fromisoformat(record["end"])
        if end < dates[0] or start > dates[-1]:
            continue
        sgk_from = start + timedelta(days=2) if record["type"] == "illness" else start
        in_month = [day for day in dates if start <= day <= end]
        for day in in_month:
            covered[record["employee_id"]].add(day.isoformat())
        items.append({
            **record, "name": names.get(record["employee_id"], "Personel"),
            "total_days": (end - start).days + 1, "days_in_month": len(in_month),
            "sgk_paid_days": sum(day >= sgk_from for day in in_month),
            "employer_days": sum(day < sgk_from for day in in_month),
            "type_label": REPORT_TYPES[record["type"]],
        })
    mismatches = []
    for employee_id, person_codes in codes.items():
        r_days = {day for day, code in person_codes.items() if code == "R"}
        for day in sorted(r_days - covered[employee_id]):
            mismatches.append({"employee_id": employee_id, "name": names[employee_id], "date": day,
                               "message": "Puantajda R var ama rapor kaydı yok"})
        for day in sorted(covered[employee_id] - r_days):
            mismatches.append({"employee_id": employee_id, "name": names[employee_id], "date": day,
                               "message": f"Rapor kaydı var ama puantaj kodu {person_codes.get(day) or 'boş'}"})
    items.sort(key=lambda item: (item["start"], item["name"]))
    return {"month": month, "reports": items, "mismatches": mismatches,
            "types": REPORT_TYPES}


def leave_pay(remaining_days: float, monthly_wage: float) -> dict[str, float]:
    if not math.isfinite(monthly_wage) or monthly_wage < 0:
        raise ValueError("Aylık ücret geçersiz.")
    daily = round(monthly_wage / 30, 2)
    days = max(0.0, float(remaining_days))
    return {"remaining_days": round(days, 2), "daily_wage": daily, "amount": round(daily * days, 2)}
