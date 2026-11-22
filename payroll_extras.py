"""Bordro ekleri: kesinti açıklaması, avanslar, yemek/yol günü, tatil ücreti veya telafi izni,
denkleştirme raporu.

Varsayımlar (kullanıcıya bildirilir):
- Yemek ve yol günü, puantajda fiilen çalışılan günlerdir (X, G.Ç, Y); kişi bazında kapatılabilir.
- Resmî tatilde çalışılan saat varsayılan olarak ücret olarak ödenir. "Telafi izni" seçilen kişide
  bu saatler ücret sütununa yazılmaz, telafi izni bakiyesine eklenir.
- Telafi izni kullanımı puantajda "Tİ" koduyla girilir; her Tİ günü kişinin günlük sınırı
  (yoksa 8 saat) kadar bakiyeden düşer.
- Denkleştirme: dönemin her haftasında mola düşülmüş fiilî çalışma toplanır; haftalık ortalama
  45 saati aşarsa aşan toplam saat fazla çalışma olarak raporlanır (4857 md. 63).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import scheduling

COMP_LEAVE_CODE = "Tİ"
WORK_CODES = ("X", "G.Ç", "Y")
WEEKLY_LIMIT = 45
BALANCING_WEEKS = (2, 4, 8, 12, 16)
HOLIDAY_CHOICES = ("pay", "leave")
_DEFAULT = {"deduction_note": "", "advances": [], "holiday_choice": "pay", "meal": True, "transport": True}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def setting_name(month: str) -> str:
    return f"payroll_extras:{scheduling.validate_month(month)}"


def _text(value: Any, limit: int, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label} metin olmalıdır.")
    text = " ".join(_CONTROL.sub(" ", value).split())
    if len(text) > limit:
        raise ValueError(f"{label} en fazla {limit} karakter olabilir.")
    return text


def _amount(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("Avans tutarı sayı olmalıdır.")
    try:
        amount = Decimal(str(value).replace(",", ".")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Avans tutarı geçersiz.") from exc
    if not amount.is_finite() or amount <= 0 or amount > Decimal("999999999.99"):
        raise ValueError("Avans tutarı 0'dan büyük ve 999999999,99'dan küçük olmalıdır.")
    return float(amount)


def normalize(month: str, payload: Any, people_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("Bordro ekleri geçersiz.")
    month_days = {day.isoformat() for day in scheduling.month_dates(month)}
    cleaned: dict[str, dict[str, Any]] = {}
    for key, raw in payload.items():
        person = str(key)
        if person not in people_ids:
            raise ValueError("Bordro ekindeki personel bulunamadı.")
        if not isinstance(raw, dict) or not set(raw) <= set(_DEFAULT):
            raise ValueError("Bordro eki alanları geçersiz.")
        advances = raw.get("advances", [])
        if not isinstance(advances, list) or len(advances) > 31:
            raise ValueError("Avans listesi en fazla 31 kayıt olabilir.")
        clean_advances = []
        for item in advances:
            if not isinstance(item, dict) or not set(item) <= {"date", "amount", "note"}:
                raise ValueError("Avans kaydı geçersiz.")
            day = item.get("date") or ""
            if day and day not in month_days:
                raise ValueError("Avans tarihi seçilen ayın içinde olmalıdır.")
            clean_advances.append({
                "date": day, "amount": _amount(item.get("amount")),
                "note": _text(item.get("note"), 80, "Avans açıklaması"),
            })
        choice = raw.get("holiday_choice", "pay")
        if choice not in HOLIDAY_CHOICES:
            raise ValueError("Tatil çalışması seçimi 'pay' veya 'leave' olmalıdır.")
        flags = {name: raw.get(name, True) for name in ("meal", "transport")}
        if any(not isinstance(value, bool) for value in flags.values()):
            raise ValueError("Yemek / yol seçimi evet-hayır olmalıdır.")
        record = {
            "deduction_note": _text(raw.get("deduction_note"), 200, "Kesinti açıklaması"),
            "advances": sorted(clean_advances, key=lambda item: item["date"]),
            "holiday_choice": choice, **flags,
        }
        if record != _DEFAULT:
            cleaned[person] = record
    return cleaned


def load(store, month: str) -> dict[str, dict[str, Any]]:
    value = store._setting(setting_name(month), {})
    return value if isinstance(value, dict) else {}


def save(store, month: str, payload: Any, people_ids: set[str]) -> dict[str, dict[str, Any]]:
    cleaned = normalize(month, payload, people_ids)
    store.assert_periods_unlocked({month})
    store._backup_before_change()
    with store.connection:
        store._write_setting(setting_name(month), cleaned)
        store.record_change("payroll.extras", f"{month} bordro ekleri kaydedildi ({len(cleaned)} kişi)",
                            {"month": month, "people": len(cleaned)})
    return cleaned


def person_extras(extras: dict[str, Any], employee_id: Any) -> dict[str, Any]:
    return {**_DEFAULT, **(extras.get(str(employee_id)) or {})}


def month_rows(attendance: dict[str, Any], extras: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in attendance["rows"]:
        info = person_extras(extras, row["employee_id"])
        codes = row.get("codes") or {}
        work_days = sum(1 for code in codes.values() if code in WORK_CODES)
        used_days = sum(1 for code in codes.values() if code == COMP_LEAVE_CODE)
        day_hours = float(row.get("daily_limit_hours") or 0) or 8.0
        ubgt = round(float(row.get("ubgt_hours") or 0), 2)
        leave = info["holiday_choice"] == "leave"
        rows.append({
            "employee_id": row["employee_id"], "name": row["name"],
            "meal": info["meal"], "transport": info["transport"],
            "meal_days": work_days if info["meal"] else 0,
            "transport_days": work_days if info["transport"] else 0,
            "advances": info["advances"],
            "advances_total": round(sum(item["amount"] for item in info["advances"]), 2),
            "deduction_amount": row.get("deduction_amount") or 0,
            "deduction_note": info["deduction_note"],
            "holiday_choice": info["holiday_choice"],
            "ubgt_hours": ubgt,
            "paid_ubgt_hours": 0 if leave else ubgt,
            "comp_earned_hours": ubgt if leave else 0,
            "comp_used_days": used_days,
            "comp_used_hours": round(used_days * day_hours, 2),
        })
    return rows


def comp_leave_balance(months: list[tuple[str, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """Telafi izni bakiyesi: ay ay kazanılan tatil saati − kullanılan Tİ saati."""
    people: dict[int, dict[str, Any]] = {}
    for month, rows in months:
        for row in rows:
            if not (row["comp_earned_hours"] or row["comp_used_hours"]):
                continue
            item = people.setdefault(row["employee_id"], {
                "employee_id": row["employee_id"], "name": row["name"],
                "earned_hours": 0.0, "used_hours": 0.0, "months": [],
            })
            item["earned_hours"] = round(item["earned_hours"] + row["comp_earned_hours"], 2)
            item["used_hours"] = round(item["used_hours"] + row["comp_used_hours"], 2)
            item["months"].append({"month": month, "earned": row["comp_earned_hours"], "used": row["comp_used_hours"]})
    result = []
    for item in people.values():
        item["balance_hours"] = round(item["earned_hours"] - item["used_hours"], 2)
        result.append(item)
    return sorted(result, key=lambda item: item["name"])


def balancing_period(month: str, weeks: int) -> tuple[date, date]:
    if weeks not in BALANCING_WEEKS:
        raise ValueError("Denkleştirme dönemi 2, 4, 8, 12 veya 16 hafta olmalıdır.")
    last_day = scheduling.month_dates(month)[-1]
    last_week = last_day - timedelta(days=last_day.weekday())
    first_week = last_week - timedelta(days=7 * (weeks - 1))
    return first_week, last_week + timedelta(days=6)


def months_between(first: date, last: date) -> list[str]:
    months, cursor = [], first.replace(day=1)
    while cursor <= last:
        months.append(cursor.strftime("%Y-%m"))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def balancing_report(month: str, weeks: int, attendances: dict[str, dict[str, Any]]) -> dict[str, Any]:
    first, last = balancing_period(month, weeks)
    week_starts = [first + timedelta(days=7 * index) for index in range(weeks)]
    people: dict[int, dict[str, Any]] = {}
    for attendance in attendances.values():
        for row in attendance["rows"]:
            item = people.setdefault(row["employee_id"], {
                "employee_id": row["employee_id"], "name": row["name"],
                "hours": {start.isoformat(): 0.0 for start in week_starts},
            })
            for day_text, detail in (row.get("overtime_details") or {}).items():
                day = date.fromisoformat(day_text)
                if not first <= day <= last:
                    continue
                monday = (day - timedelta(days=day.weekday())).isoformat()
                net = float(detail.get("worked") or 0) - float(detail.get("break") or 0)
                item["hours"][monday] = round(item["hours"][monday] + net, 2)
    rows = []
    for item in people.values():
        total = round(sum(item["hours"].values()), 2)
        if not total:
            continue
        average = round(total / weeks, 2)
        rows.append({
            "employee_id": item["employee_id"], "name": item["name"],
            "weeks": [{"week": key, "hours": value} for key, value in item["hours"].items()],
            "total_hours": total, "average_hours": average,
            "over_weeks": sum(1 for value in item["hours"].values() if value > WEEKLY_LIMIT),
            "excess_hours": round(max(0.0, total - WEEKLY_LIMIT * weeks), 2),
            "status": "over" if average > WEEKLY_LIMIT else "ok",
        })
    rows.sort(key=lambda row: (row["status"] != "over", -row["average_hours"], row["name"]))
    return {
        "month": month, "weeks": weeks, "start": first.isoformat(), "end": last.isoformat(),
        "limit": WEEKLY_LIMIT, "week_starts": [start.isoformat() for start in week_starts], "rows": rows,
    }
