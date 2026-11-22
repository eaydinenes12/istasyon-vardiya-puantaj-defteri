"""Management analytics (suggestion group E).

Assumptions (also shown in the UI):
- Absence rate = (R + G + Ü days) / (X + R + G + Ü days). Annual leave (S) and weekly off (H) are planned
  absences and are not counted.
- Hourly wage = monthly gross / 225 (30 days x 7.5 h). Base cost = monthly gross x SGK days / 30.
  Overtime (FM, the attendance daily rule) is paid at 1.5x, UBGT hours get one extra hourly wage.
  Employer SGK/unemployment shares are not included (gross estimate).
- Absence pattern: an R or G day on a Monday, a Friday, or right before/after a public holiday or the
  person's weekly off (H). A person is flagged with 3+ such days making at least half of their R/G days.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

import reports
import scheduling

ABSENCE_CODES = ("R", "G", "Ü")
MANAGER_CHART_NOTE_KEYS = frozenset({
    "worked_hours", "overtime_hours", "ubgt_hours", "sick_days", "absence_days",
    "unpaid_days", "annual_leave_days",
})
HOURS_PER_MONTH = 225
MONTHS_TR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim",
             "Kasım", "Aralık")


def manager_report_version(summary: dict[str, Any]) -> str:
    stable = dict(summary)
    stable.pop("overtime", None)
    context = dict(stable.get("report_context", {}))
    context.pop("calculated_at", None)
    context.pop("report_version", None)
    stable["report_context"] = context

    def normalize(value):
        if isinstance(value, bool):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    content = json.dumps(normalize(stable), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return "manager-v1-" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]


def month_label(month: str) -> str:
    year, number = month.split("-")
    return f"{MONTHS_TR[int(number) - 1]} {year}"


def shift_month(month: str, delta: int) -> str:
    year, number = map(int, month.split("-"))
    index = year * 12 + number - 1 + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_metrics(attendance: dict[str, Any], overtime: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {code: 0 for code in ("X", "S", "R", "G", "Ü", "H")}
    people = 0
    for row in attendance["rows"]:
        codes = [code for code in row.get("codes", {}).values() if code]
        if codes:
            people += 1
        for code in codes:
            if code in counts:
                counts[code] += 1
    absence = sum(counts[code] for code in ABSENCE_CODES)
    base = counts["X"] + absence
    return {
        "people": people,
        "calendar_days": len(attendance["dates"]),
        "normal_weekdays": sum(scheduling.attendance_day_category(date.fromisoformat(day)) == "weekday"
                               for day in attendance["dates"]),
        "work_days": counts["X"], "annual_leave_days": counts["S"], "sick_days": counts["R"],
        "absence_days": counts["G"], "unpaid_days": counts["Ü"], "weekly_off_days": counts["H"],
        "absence_rate": round(absence * 100 / base, 1) if base else 0.0,
        "worked_hours": round(sum(float(row.get("worked_hours") or 0) for row in attendance["rows"]), 2),
        "overtime_hours": round(sum(float(row.get("overtime_hours") or 0) for row in attendance["rows"]), 2),
        "ubgt_hours": round(sum(float(row.get("ubgt_hours") or 0) for row in attendance["rows"]), 2),
        "night_hours": round(sum(float(row.get("night_hours") or 0) for row in attendance["rows"]), 2),
    }


def metric_details(attendance: dict[str, Any], metric: str) -> list[dict[str, Any]]:
    code_metrics = {
        "work_days": "X",
        "annual_leave_days": "S",
        "sick_days": "R",
        "absence_days": "G",
        "unpaid_days": "Ü",
        "weekly_off_days": "H",
    }
    if metric not in (*code_metrics, "worked_hours", "overtime_hours", "ubgt_hours"):
        raise ValueError("Grafik göstergesi geçersiz.")

    details = []
    for row in attendance["rows"]:
        employee_id = int(row["employee_id"])
        if metric in code_metrics:
            code = code_metrics[metric]
            for day, value in sorted(row.get("codes", {}).items()):
                if value == code:
                    details.append({
                        "employee_id": employee_id, "name": row["name"], "date": day,
                        "value": 1, "unit": "gün", "code": code,
                        "source": row.get("sources", {}).get(day, "unknown"),
                    })
            continue

        if metric == "worked_hours":
            day_values, source = row.get("worked_hours_by_day", {}), "schedule-calculation"
            fallback_value = 0
        elif metric == "ubgt_hours":
            day_values, source = row.get("ubgt_days", {}), "holiday-calculation"
            fallback_value = 0
        elif row.get("overtime_manual"):
            day_values, source = {}, "manual-period-total"
            fallback_value = float(row.get("overtime_hours") or 0)
        elif row.get("overtime_days"):
            day_values, source = row["overtime_days"], "overtime-rule"
            fallback_value = 0
        else:
            day_values, source = {}, "weekly-rule-period-total"
            fallback_value = float(row.get("overtime_hours") or 0)

        for day, value in sorted(day_values.items()):
            if float(value):
                details.append({
                    "employee_id": employee_id, "name": row["name"], "date": day,
                    "value": float(value), "unit": "sa", "code": row.get("codes", {}).get(day, ""),
                    "source": source,
                })
        if fallback_value:
            details.append({
                "employee_id": employee_id, "name": row["name"], "date": None,
                "value": fallback_value, "unit": "sa", "code": "",
                "source": source,
            })
    return details


def compare(current: dict[str, Any], other: dict[str, Any] | None) -> dict[str, Any]:
    if not other or not other.get("people"):
        return {}
    return {
        key: round(float(current[key]) - float(other[key]), 2)
        for key in current if isinstance(current[key], (int, float))
    }


def cost_estimate(
    attendance: dict[str, Any], sgk: list[dict[str, Any]], overtime: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]], hours_per_month: float = HOURS_PER_MONTH,
) -> dict[str, Any]:
    if not 150 <= hours_per_month <= 240:
        raise ValueError("Aylık saat 150 ile 240 arasında olmalıdır.")
    sgk_by = {int(row["employee_id"]): row for row in sgk}
    extra_by = {int(row["employee_id"]): row for row in overtime}
    people, missing = [], []
    for row in attendance["rows"]:
        employee_id = int(row["employee_id"])
        if not any(row.get("codes", {}).values()):
            continue
        wage = float(profiles.get(str(employee_id), {}).get("monthly_wage") or 0)
        if wage <= 0:
            missing.append(row["name"])
            continue
        hourly = wage / hours_per_month
        days = sgk_by.get(employee_id, {}).get("sgk_days", 30)
        extra = extra_by.get(employee_id, {})
        parts = {
            "base": wage * days / 30,
            "overtime": hourly * 1.5 * float(extra.get("overtime_hours", row.get("overtime_hours")) or 0),
            "ubgt": hourly * float(row.get("ubgt_hours") or 0),
        }
        people.append({
            "employee_id": employee_id, "name": row["name"], "monthly_wage": wage,
            "hourly_wage": round(hourly, 2), "sgk_days": days,
            **{key: round(value, 2) for key, value in parts.items()},
            "total": round(sum(parts.values()), 2),
        })
    totals = {key: round(sum(item[key] for item in people), 2)
              for key in ("base", "overtime", "ubgt", "total")}
    return {"hours_per_month": hours_per_month, "people": people, "totals": totals, "missing_wage": missing}


def _bridge_day(day: date, codes: dict[str, str]) -> str | None:
    for neighbour, label in ((day - timedelta(days=1), "tatil sonrası"), (day + timedelta(days=1), "tatil öncesi")):
        if scheduling.holiday_window(neighbour) or codes.get(neighbour.isoformat()) == "H":
            return label
    return None


def absence_patterns(months: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    people: dict[int, dict[str, Any]] = {}
    for _, attendance in months:
        for row in attendance["rows"]:
            person = people.setdefault(int(row["employee_id"]), {
                "employee_id": int(row["employee_id"]), "name": row["name"], "codes": {},
            })
            person["codes"].update(row.get("codes", {}))
    result = []
    for person in people.values():
        days = sorted(day for day, code in person["codes"].items() if code in ("R", "G"))
        hits = []
        for text in days:
            day = date.fromisoformat(text)
            reason = ("Pazartesi" if day.weekday() == 0 else "Cuma" if day.weekday() == 4 else None) \
                or _bridge_day(day, person["codes"])
            if reason:
                hits.append({"date": text, "code": person["codes"][text], "reason": reason})
        flagged = len(hits) >= 3 and len(hits) * 2 >= len(days)
        if hits:
            result.append({
                "employee_id": person["employee_id"], "name": person["name"], "absence_days": len(days),
                "pattern_days": len(hits), "ratio": round(len(hits) * 100 / len(days)),
                "flagged": flagged, "hits": hits,
            })
    result.sort(key=lambda item: (not item["flagged"], -item["pattern_days"], item["name"].casefold()))
    return result


def _fmt(value: float, digits: int = 1) -> str:
    text = f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return text[:-2] if digits and text.endswith(",0") else text


def _money(value: float) -> str:
    return f"{_fmt(round(value), 0)} ₺"


def _delta(value: float | None, unit: str = "", inverse: bool = False) -> tuple[str, str]:
    if value is None:
        return "—", "#6B7785"
    if abs(value) < 0.005:
        return "değişmedi", "#6B7785"
    worse = value > 0 if not inverse else value < 0
    return f"{'+' if value > 0 else '−'}{_fmt(abs(value))}{unit}", "#B42318" if worse else "#067647"


def manager_summary_pdf(month: str, summary: dict[str, Any]) -> bytes:
    width, height, margin = 1240, 1754, 80
    page = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(page)
    title, h2, body, small = reports._font(46, True), reports._font(28, True), reports._font(22), reports._font(18)
    draw.rectangle((0, 0, width, 150), fill="#1F5F3F")
    draw.text((margin, 40), f"Yönetici özeti · {month_label(month)}", font=title, fill="white")
    draw.text((margin, 102), "Vardiya Puantaj · tek sayfalık aylık durum", font=small, fill="#D1E9DB")
    metrics, previous, last_year = summary["metrics"], summary["previous"], summary["last_year"]
    cost = summary["cost"]
    tiles = [
        ("Puantajdaki personel", str(metrics["people"]), None, ""),
        ("Devamsızlık oranı", f"%{_fmt(metrics['absence_rate'])}", summary["compare_previous"].get("absence_rate"), " puan"),
        ("Fazla mesai", f"{_fmt(metrics['overtime_hours'])} sa",
         summary["compare_previous"].get("overtime_hours") if summary["compare_previous"] else None, " sa"),
        ("Dönem kontrolü uyarısı", str(summary["warnings"]["total"]), None, ""),
        ("Tahmini brüt maliyet", _money(cost["totals"]["total"]) if cost["people"] else "Ücret girilmemiş", None, ""),
        ("Rapor (R) günü", str(metrics["sick_days"]), summary["compare_previous"].get("sick_days"), " gün"),
    ]
    tile_w, tile_h, gap = (width - 2 * margin - 2 * 24) // 3, 150, 24
    y = 190
    for index, (label, value, delta, unit) in enumerate(tiles):
        x = margin + (index % 3) * (tile_w + gap)
        top = y + (index // 3) * (tile_h + gap)
        draw.rounded_rectangle((x, top, x + tile_w, top + tile_h), radius=14, fill="#F4F7F5", outline="#D0DDD5")
        draw.text((x + 22, top + 18), label, font=small, fill="#3B4A5A")
        draw.text((x + 22, top + 52), value, font=reports._font(38, True), fill="#1F2A37")
        if delta is not None:
            text, color = _delta(delta, unit)
            draw.text((x + 22, top + 108), f"önceki aya göre {text}", font=small, fill=color)
    y += 2 * (tile_h + gap) + 20

    draw.text((margin, y), "Karşılaştırma", font=h2, fill="#1F2A37")
    y += 46
    columns = [margin, margin + 430, margin + 650, margin + 870]
    headers = ("Gösterge", month_label(month), month_label(summary["previous_month"]), month_label(summary["last_year_month"]))
    for x, text in zip(columns, headers):
        draw.text((x, y), text, font=reports._font(20, True), fill="#3B4A5A")
    y += 34
    rows = [
        ("Takvim günü / dönem", "calendar_days", "{:g}"), ("Normal iş günü (takvim)", "normal_weekdays", "{:g}"),
        ("Kod girilen personel", "people", "{:g}"),
        ("Çalışılan gün (X)", "work_days", "{:g}"), ("Toplam çalışma (sa)", "worked_hours", "{:g}"),
        ("Fazla mesai (sa)", "overtime_hours", "{:g}"),
        ("Rapor günü (R)", "sick_days", "{:g}"), ("Devamsız gün (G)", "absence_days", "{:g}"),
        ("Ücretsiz izin (Ü)", "unpaid_days", "{:g}"), ("Yıllık izin (S)", "annual_leave_days", "{:g}"),
        ("Devamsızlık oranı %", "absence_rate", "{:g}"),
    ]
    for index, (label, key, pattern) in enumerate(rows):
        if index % 2 == 0:
            draw.rectangle((margin - 10, y - 6, width - margin, y + 28), fill="#F8FAF9")
        values = [metrics, previous, last_year]
        draw.text((columns[0], y), label, font=body, fill="#1F2A37")
        for x, data in zip(columns[1:], values):
            text = pattern.format(data[key]) if data and data.get("people") else "—"
            draw.text((x, y), text.replace(".", ","), font=body, fill="#1F2A37")
        y += 36
    y += 24

    half = (width - 2 * margin - 40) // 2
    left_y = right_y = y
    draw.text((margin, left_y), "En çok fazla mesai", font=h2, fill="#1F2A37")
    left_y += 44
    top_overtime = sorted(summary["overtime"], key=lambda row: -row["overtime_hours"])[:6]
    top_overtime = [row for row in top_overtime if row["overtime_hours"] > 0]
    for row in top_overtime or [{"name": "Fazla mesai yok", "overtime_hours": 0}]:
        draw.text((margin, left_y), row["name"][:28], font=body, fill="#1F2A37")
        if row["overtime_hours"]:
            draw.text((margin + half, left_y), f"{_fmt(row['overtime_hours'])} sa", font=body,
                      fill="#1F2A37", anchor="ra")
        left_y += 34
    right_x = margin + half + 40
    draw.text((right_x, right_y), "Devamsızlık örüntüsü", font=h2, fill="#1F2A37")
    right_y += 44
    flagged = [item for item in summary["patterns"] if item["flagged"]][:6]
    for item in flagged or [{"name": "Dikkat çeken örüntü yok", "pattern_days": 0}]:
        draw.text((right_x, right_y), item["name"][:24], font=body, fill="#1F2A37")
        if item["pattern_days"]:
            draw.text((width - margin, right_y), f"{item['pattern_days']}/{item['absence_days']} gün",
                      font=body, fill="#B42318", anchor="ra")
        right_y += 34
    y = max(left_y, right_y) + 24

    warnings = summary["warnings"]
    draw.text((margin, y), f"Dikkat gerektiren uyarılar ({warnings['total']})", font=h2, fill="#1F2A37")
    y += 44
    limit_y = height - 150
    for item in warnings["items"]:
        if y > limit_y - 30:
            draw.text((margin, y), f"… ve {warnings['total'] - warnings['items'].index(item)} uyarı daha (Dönem kontrolü)",
                      font=small, fill="#6B7785")
            break
        color = "#B42318" if item["severity"] == "error" else "#B54708"
        text = f"{item['name']}{' · ' + date.fromisoformat(item['date']).strftime('%d.%m') if item.get('date') else ''} · {item['message']}"
        while draw.textlength(text, font=small) > width - 2 * margin - 30 and len(text) > 10:
            text = text[:-2]
        if text != f"{item['name']}{' · ' + date.fromisoformat(item['date']).strftime('%d.%m') if item.get('date') else ''} · {item['message']}":
            text = text.rstrip() + "…"
        draw.ellipse((margin, y + 7, margin + 12, y + 19), fill=color)
        draw.text((margin + 24, y), text, font=small, fill="#1F2A37")
        y += 30
    if not warnings["items"]:
        draw.text((margin, y), "Uyarı yok.", font=body, fill="#067647")
    note = "Devamsızlık oranı = (R+G+Ü) / (X+R+G+Ü). Maliyet brüt tahmindir: saat ücreti = aylık brüt / " \
           f"{cost['hours_per_month']:g}; işveren SGK payı dahil değildir."
    context = summary.get("report_context", {})
    calculated_at = context.get("calculated_at")
    timestamp = datetime.fromisoformat(calculated_at).astimezone().strftime("%d.%m.%Y %H:%M") \
        if calculated_at else datetime.now().astimezone().strftime("%d.%m.%Y %H:%M")
    revisions = context.get("source_revisions", {}).get(month, {})
    source_label = (
        f"Hesaplandı: {timestamp} · Plan {revisions.get('schedule')[:8] if revisions.get('schedule') else 'kayıt yok'} · "
        f"Puantaj {revisions.get('attendance')[:8] if revisions.get('attendance') else 'kayıt yok'}"
    )
    missing = ", ".join(context.get("missing_sources", [])) or "Eksik kaynak yok"
    draw.text((margin, height - 170), note, font=small, fill="#6B7785")
    draw.text((margin, height - 140), source_label, font=small, fill="#6B7785")
    draw.text((margin, height - 110), f"Kaynak durumu: {missing}", font=small, fill="#6B7785")
    draw.text((margin, height - 80), f"Rapor dönemi: {month_label(month)}", font=small, fill="#6B7785")
    buffer = BytesIO()
    page.save(buffer, "PDF", resolution=150)
    return buffer.getvalue()
