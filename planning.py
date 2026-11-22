"""Planlama yardımcıları (D grubu): adil dağılım, tercihler, yetkinlik, izin talepleri,
bölümler arası ödünç personel ve geçmiş puantaj Excel'lerinin içe aktarılması.

Varsayımlar (kullanıcıya bildirilir):
- Gece vardiyası: 20:00–06:00 arasında en az 3 saat süren vardiya (yasal uyum kuralıyla aynı).
- Hafta sonu vardiyası: cumartesi veya pazar başlayan vardiya.
- Bayram/tatil vardiyası: resmî/dinî tatil veya arife öğleden sonrasına denk gelen vardiya.
- Yetkinlik kontrolü gün + bölüm bazındadır: o gün o bölümde çalışan biri gerekli yetkiye sahip olmalı.
- Ödünç personel: personel kartındaki "asıl bölüm" dışındaki bir bölüm satırında çalıştığı gün.
  Asıl bölüm girilmemişse kişinin ay içinde en çok çalıştığı bölüm asıl kabul edilir.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from io import BytesIO
from typing import Any

from openpyxl import load_workbook

import compliance
import scheduling

WEEKDAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
LEAVE_REQUEST_TYPES = {"S": "Yıllık izin", "Ü": "Ücretsiz izin", "E": "Evlilik izni", "H": "Hafta tatili isteği"}
LEAVE_REQUEST_STATUSES = {"pending": "Bekliyor", "approved": "Onaylandı", "rejected": "Reddedildi"}
DEFAULT_LEAVE_SETTINGS = {"daily_limit": 2}


def _clean_text(value: Any, limit: int, label: str) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or len(value.strip()) > limit:
        raise ValueError(f"{label} en fazla {limit} karakter olabilir.")
    return " ".join(value.split())


def normalize_preferences(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        value = {}
    if not isinstance(value, dict):
        raise ValueError("Personel tercihleri geçersiz.")
    days = value.get("unavailable_weekdays", []) or []
    if not isinstance(days, list) or any(
        not isinstance(day, int) or isinstance(day, bool) or not 0 <= day <= 6 for day in days
    ):
        raise ValueError("Çalışamayacağı günler 0-6 arasında olmalıdır.")
    ranges = value.get("unavailable_ranges", []) or []
    if not isinstance(ranges, list) or len(ranges) > 20:
        raise ValueError("En fazla 20 tarih aralığı girilebilir.")
    unavailable_ranges, seen_ranges = [], set()
    for item in ranges:
        if not isinstance(item, dict):
            raise ValueError("Tarih aralığı geçersiz.")
        try:
            start = date.fromisoformat(item.get("start", ""))
            end = date.fromisoformat(item.get("end", ""))
        except (TypeError, ValueError) as exc:
            raise ValueError("Tarih aralığı başlangıcı veya bitişi geçersiz.") from exc
        if end < start or (end - start).days > 366:
            raise ValueError("Tarih aralığı en fazla 367 gün olabilir; bitiş başlangıçtan önce olamaz.")
        key = (start.isoformat(), end.isoformat())
        if key not in seen_ranges:
            seen_ranges.add(key)
            unavailable_ranges.append({"start": key[0], "end": key[1]})
    return {
        "unavailable_weekdays": sorted(set(days)),
        "unavailable_ranges": sorted(unavailable_ranges, key=lambda item: (item["start"], item["end"])),
        "no_night": bool(value.get("no_night", False)),
        "no_weekend": bool(value.get("no_weekend", False)),
        "note": _clean_text(value.get("note", ""), 120, "Tercih notu"),
    }


def normalize_skills(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [part for part in re.split(r"[,;\n]", value)]
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("En fazla 20 yetkinlik girilebilir.")
    result: list[str] = []
    for item in value:
        text = _clean_text(item, 40, "Yetkinlik adı")
        if text and text.casefold() not in {skill.casefold() for skill in result}:
            result.append(text)
    return result


def normalize_skill_requirements(value: Any) -> list[dict[str, str]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError("En fazla 50 yetkinlik kuralı girilebilir.")
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Yetkinlik kuralı geçersiz.")
        skill = _clean_text(item.get("skill", ""), 40, "Yetkinlik adı")
        section = _clean_text(item.get("section", ""), 60, "Bölüm adı")
        if not skill:
            raise ValueError("Yetkinlik kuralında yetkinlik adı zorunludur.")
        key = (skill.casefold(), section.casefold())
        if key not in seen:
            seen.add(key)
            result.append({"skill": skill, "section": section})
    return result


def normalize_leave_settings(value: Any) -> dict[str, int]:
    value = value if isinstance(value, dict) else {}
    limit = value.get("daily_limit", DEFAULT_LEAVE_SETTINGS["daily_limit"])
    if not isinstance(limit, int) or isinstance(limit, bool) or not 0 <= limit <= 50:
        raise ValueError("Günlük izin sınırı 0-50 arasında olmalıdır (0 = kapalı).")
    return {"daily_limit": limit}


def normalize_leave_request(item: Any, employee_ids: set[int]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("İzin talebi geçersiz.")
    employee_id = item.get("employee_id")
    if not isinstance(employee_id, int) or isinstance(employee_id, bool) or employee_id not in employee_ids:
        raise ValueError("Personel seçimi geçersiz.")
    try:
        start, end = date.fromisoformat(item.get("start", "")), date.fromisoformat(item.get("end", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("İzin talebi tarihleri geçersiz.") from exc
    if end < start or (end - start).days > 90:
        raise ValueError("İzin talebi en fazla 90 gün olabilir; bitiş başlangıçtan önce olamaz.")
    code = item.get("code", "S")
    if code not in LEAVE_REQUEST_TYPES:
        raise ValueError("İzin türü geçersiz.")
    status = item.get("status", "pending")
    if status not in LEAVE_REQUEST_STATUSES:
        raise ValueError("Talep durumu geçersiz.")
    return {
        "employee_id": employee_id, "start": start.isoformat(), "end": end.isoformat(),
        "code": code, "status": status, "note": _clean_text(item.get("note", ""), 120, "Talep notu"),
    }


def _work_shifts(entries, month: str | None = None):
    """(employee_id, section, start_datetime, end_datetime) for work days, optionally within a month."""
    for row in entries:
        for day_text, item in row.get("days", {}).items():
            if item.get("kind") != "work" or not item.get("start") or not item.get("end"):
                continue
            if month and not day_text.startswith(month):
                continue
            start, end = scheduling._time_interval(day_text, item["start"], item["end"])
            yield int(row["employee_id"]), (row.get("section") or "").strip(), start, end


def _is_night(start: datetime, end: datetime) -> bool:
    return compliance.night_minutes(start, end) >= compliance.NIGHT_SHIFT_MINUTES


def _is_holiday(start: datetime, end: datetime) -> bool:
    day = start.date()
    while day <= end.date():
        window = scheduling.holiday_window(day)
        if window and start < window[1] and end > window[0]:
            return True
        day += timedelta(days=1)
    return False


def _item(kind, severity, employee_id, name, day, message):
    return {
        "type": kind, "severity": severity, "employee_id": employee_id, "name": name,
        "date": day.isoformat() if isinstance(day, date) else day, "message": message,
    }


def fairness(
    entries_by_month: dict[str, list[dict]],
    names: dict[int, str],
    profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    stats: dict[int, dict[str, float]] = {
        employee_id: {"shifts": 0, "hours": 0.0, "night": 0, "weekend": 0, "holiday": 0}
        for employee_id in names
    }
    for month, entries in entries_by_month.items():
        for employee_id, _, start, end in _work_shifts(entries, month):
            if employee_id not in stats:
                continue
            row = stats[employee_id]
            row["shifts"] += 1
            row["hours"] += (end - start).total_seconds() / 3600
            row["night"] += _is_night(start, end)
            row["weekend"] += start.weekday() >= 5
            row["holiday"] += _is_holiday(start, end)
    active = [row for row in stats.values() if row["shifts"]]
    keys = ("shifts", "hours", "night", "weekend", "holiday")
    averages = {key: round(sum(row[key] for row in active) / len(active), 2) if active else 0 for key in keys}
    people = []
    for employee_id, row in stats.items():
        if not row["shifts"]:
            continue
        profile = (profiles or {}).get(str(employee_id), {})
        preferences = normalize_preferences(profile.get("preferences"))
        unavailable_weekdays = set(preferences["unavailable_weekdays"])
        unavailable_ranges = preferences["unavailable_ranges"]
        availability_periods = []
        for month in sorted(entries_by_month):
            eligible_dates = []
            night_dates = []
            weekend_dates = []
            holiday_dates = []
            for day in scheduling.month_dates(month):
                day_text = day.isoformat()
                if profile.get("hire_date") and day_text < profile["hire_date"]:
                    continue
                if profile.get("exit_date") and day_text > profile["exit_date"]:
                    continue
                if day.weekday() in unavailable_weekdays or any(
                    item["start"] <= day_text <= item["end"] for item in unavailable_ranges
                ):
                    continue
                eligible_dates.append(day)
                if not preferences["no_night"]:
                    night_dates.append(day)
                if day.weekday() >= 5 and not preferences["no_weekend"]:
                    weekend_dates.append(day)
                if scheduling.holiday_window(day):
                    holiday_dates.append(day)
            availability_periods.append({
                "month": month,
                "unrestricted_start_days": len(eligible_dates),
                "night_start_days": len(night_dates),
                "weekend_start_days": len(weekend_dates),
                "holiday_start_days": len(holiday_dates),
            })
        # Load score: how far above/below the team average this person carries the unpopular shifts.
        burden = sum(row[key] - averages[key] for key in ("night", "weekend", "holiday"))
        people.append({
            "employee_id": employee_id, "name": names[employee_id],
            **{key: round(row[key], 2) for key in keys},
            "burden": round(burden, 2),
            "flags": [key for key in ("night", "weekend", "holiday")
                      if averages[key] and row[key] >= max(averages[key] * 1.5, averages[key] + 2)],
            "availability": {
                "source": "personel kartındaki kayıtlı tercih ve görev tarihleri",
                "confirmed": False,
                "unavailable_weekdays": [WEEKDAYS[index] for index in unavailable_weekdays],
                "unavailable_ranges": unavailable_ranges,
                "no_night_preference": preferences["no_night"],
                "no_weekend_preference": preferences["no_weekend"],
                "periods": availability_periods,
            },
        })
    people.sort(key=lambda item: (-item["burden"], item["name"].casefold()))
    return {"months": sorted(entries_by_month), "averages": averages, "people": people}


def preference_items(month, entries, names, profiles) -> list[dict[str, Any]]:
    items = []
    for employee_id, _, start, end in _work_shifts(entries, month):
        if employee_id not in names:
            continue
        prefs = profiles.get(str(employee_id), {}).get("preferences") or {}
        reasons = []
        if start.weekday() in prefs.get("unavailable_weekdays", []):
            reasons.append(f"{WEEKDAYS[start.weekday()]} günü çalışamıyor")
        if prefs.get("no_night") and _is_night(start, end):
            reasons.append("gece çalışmak istemiyor")
        if prefs.get("no_weekend") and start.weekday() >= 5:
            reasons.append("hafta sonu çalışmak istemiyor")
        last_shift_day = end.date() - timedelta(days=1) if end.time() == time.min and end > start else end.date()
        shift_days = []
        current_day = start.date()
        while current_day <= last_shift_day:
            shift_days.append(current_day)
            current_day += timedelta(days=1)
        unavailable = [
            item for item in prefs.get("unavailable_ranges", [])
            if any(date.fromisoformat(item["start"]) <= day <= date.fromisoformat(item["end"]) for day in shift_days)
        ]
        if unavailable:
            reasons.append("belirtilen tarih aralığında çalışmaya uygun değil")
        if reasons:
            note = f" (not: {prefs['note']})" if prefs.get("note") else ""
            items.append(_item(
                "preference", "warning", employee_id, names[employee_id], start.date(),
                f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')} vardiyası tercihe aykırı: {', '.join(reasons)}{note}",
            ))
    return items


def skill_items(month, entries, names, profiles, requirements) -> list[dict[str, Any]]:
    if not requirements:
        return []
    working: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for employee_id, section, start, _ in _work_shifts(entries, month):
        if employee_id in names:
            working[start.date().isoformat()].append((employee_id, section))
    skills = {
        int(key): {skill.casefold() for skill in profile.get("skills") or []}
        for key, profile in profiles.items() if str(key).isdigit()
    }
    items = []
    for day in sorted(working):
        for rule in requirements:
            section = rule["section"].casefold()
            staff = [employee_id for employee_id, name in working[day] if not section or name.casefold() == section]
            if staff and not any(rule["skill"].casefold() in skills.get(employee_id, set()) for employee_id in staff):
                where = f"{rule['section']} bölümünde" if rule["section"] else "Bu gün"
                items.append(_item(
                    "skill_coverage", "warning", None, rule["skill"], day,
                    f"{where} çalışan {len(staff)} kişiden hiçbiri “{rule['skill']}” yetkisine sahip değil",
                ))
    return items


def mentorship_items(month, entries, names, profiles) -> list[dict[str, Any]]:
    """Flag declared trainee task hours not fully overlapped by a declared mentor."""
    shifts = list(_work_shifts(entries))
    trainee_skills = {
        int(key): {skill.casefold() for skill in profile.get("training_skills") or []}
        for key, profile in profiles.items() if str(key).isdigit()
    }
    experienced_skills = {
        int(key): {skill.casefold() for skill in profile.get("experienced_skills") or []}
        for key, profile in profiles.items() if str(key).isdigit()
    }
    findings = []
    for trainee_id, section, start, end in shifts:
        if start.strftime("%Y-%m") != month or trainee_id not in names:
            continue
        needs = trainee_skills.get(trainee_id, set()) - experienced_skills.get(trainee_id, set())
        for skill in sorted(needs):
            mentors = [
                (other_start, other_end) for mentor_id, mentor_section, other_start, other_end in shifts
                if mentor_id != trainee_id and mentor_section.casefold() == section.casefold()
                and skill in experienced_skills.get(mentor_id, set())
                and other_start < end and other_end > start
            ]
            boundaries = sorted({start, end} | {
                edge for mentor_start, mentor_end in mentors
                for edge in (max(start, mentor_start), min(end, mentor_end))
            })
            uncovered = [
                (left, right) for left, right in zip(boundaries, boundaries[1:])
                if left < right and not any(mentor_start <= left and mentor_end >= right
                                             for mentor_start, mentor_end in mentors)
            ]
            if uncovered:
                ranges = ", ".join(f"{left:%H:%M}–{right:%H:%M}" for left, right in uncovered)
                findings.append(_item(
                    "mentorship_gap", "warning", trainee_id, names[trainee_id], start.date(),
                    f"{names[trainee_id]} · {skill}: {section or 'bölüm belirtilmemiş'} görevinde "
                    f"deneyimli çalışanla örtüşmeyen saatler: {ranges}.",
                ) | {"skill": skill, "uncovered": [
                    {"start": left.strftime("%H:%M"), "end": right.strftime("%H:%M")}
                    for left, right in uncovered
                ]})
    return findings


def _request_days(request: dict[str, Any], dates: list[date]) -> list[date]:
    start, end = date.fromisoformat(request["start"]), date.fromisoformat(request["end"])
    return [day for day in dates if start <= day <= end]


def leave_calendar(month: str, requests: list[dict[str, Any]], names: dict[int, str], limit: int,
                   section_for=None) -> dict[str, Any]:
    dates = scheduling.month_dates(month)
    days = {day.isoformat(): [] for day in dates}
    visible = []
    for request in requests:
        covered = _request_days(request, dates)
        if not covered:
            continue
        visible.append({**request, "name": names.get(request["employee_id"], "Personel"),
                        "status_label": LEAVE_REQUEST_STATUSES[request["status"]],
                        "code_label": LEAVE_REQUEST_TYPES[request["code"]]})
        if request["status"] == "rejected":
            continue
        for day in covered:
            day_text = day.isoformat()
            section = request.get("section") or (
                section_for(request["employee_id"], day_text) if section_for else ""
            )
            days[day_text].append({
                "employee_id": request["employee_id"],
                "name": names.get(request["employee_id"], "Personel"),
                "status": request["status"], "code": request["code"],
                "kind": request.get("kind"),
                "code_label": LEAVE_REQUEST_TYPES[request["code"]],
                "section": section or "Bölümü belirsiz",
                "request_id": request.get("linked_request_id") or request.get("id", ""),
            })
    visible.sort(key=lambda item: (item["start"], item["name"].casefold()))
    overlaps = []
    calendar_days = []
    for day_text, items in days.items():
        by_person = defaultdict(list)
        by_section = defaultdict(list)
        for item in items:
            by_person[item["employee_id"]].append(item)
            by_section[item["section"]].append(item)
        for employee_id, person_items in by_person.items():
            codes = {item["code"] for item in person_items}
            if len(codes) > 1:
                overlaps.append({
                    "date": day_text, "employee_id": employee_id,
                    "name": person_items[0]["name"],
                    "requests": [{"code": item["code"], "kind": item.get("kind"), "label": item["code_label"],
                                  "status": item["status"], "request_id": item["request_id"]}
                                 for item in person_items],
                    "priority": "not_configured",
                    "message": "İzin türü önceliği yapılandırılmadı; otomatik tür seçilmedi.",
                })
        calendar_days.append({
            "date": day_text, "count": len(items), "people": items,
            "over": bool(limit) and len(items) > limit,
            "sections": [
                {"section": section,
                 "count": len({item["employee_id"] for item in section_items}),
                 "people": sorted({item["name"] for item in section_items}, key=str.casefold)}
                for section, section_items in sorted(by_section.items(), key=lambda row: row[0].casefold())
            ],
        })
    return {
        "month": month, "daily_limit": limit, "requests": visible,
        "days": calendar_days,
        "overlaps": overlaps,
        "types": LEAVE_REQUEST_TYPES, "statuses": LEAVE_REQUEST_STATUSES,
    }


def leave_request_items(month, entries, names, requests, limit) -> list[dict[str, Any]]:
    calendar = leave_calendar(month, requests, names, limit)
    items = [
        _item("leave_request_crowd", "warning", None, f"{day['count']} izin talebi", day["date"],
              f"Aynı güne {day['count']} izin talebi düştü (sınır {limit}): "
              + ", ".join(person["name"] for person in day["people"]))
        for day in calendar["days"] if day["over"]
    ]
    worked = {(employee_id, start.date().isoformat()) for employee_id, _, start, _ in _work_shifts(entries, month)}
    dates = scheduling.month_dates(month)
    for request in requests:
        if request["status"] != "approved" or request["employee_id"] not in names:
            continue
        for day in _request_days(request, dates):
            if (request["employee_id"], day.isoformat()) in worked:
                items.append(_item(
                    "leave_request_conflict", "warning", request["employee_id"], names[request["employee_id"]], day,
                    f"Onaylı {LEAVE_REQUEST_TYPES[request['code']].lower()} talebi var ama çalışma planlanmış",
                ))
    return items


def borrowed_staff(month: str, entries, names: dict[int, str], profiles) -> list[dict[str, Any]]:
    by_person: dict[int, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(lambda: {"days": set(), "hours": 0.0}))
    for employee_id, section, start, end in _work_shifts(entries, month):
        if employee_id in names:
            slot = by_person[employee_id][section or "Bölümsüz"]
            slot["days"].add(start.date().isoformat())
            slot["hours"] += (end - start).total_seconds() / 3600
    result = []
    for employee_id, sections in by_person.items():
        home = (profiles.get(str(employee_id), {}).get("home_section") or "").strip()
        declared = bool(home)
        if not home:
            home = max(sections, key=lambda key: (len(sections[key]["days"]), sections[key]["hours"], key))
        home_key = home.casefold()
        rows = [{"section": section, "days": len(value["days"]), "hours": round(value["hours"], 2),
                 "dates": sorted(value["days"]), "home": section.casefold() == home_key}
                for section, value in sorted(sections.items())]
        borrowed = [row for row in rows if not row["home"]]
        if borrowed:
            result.append({
                "employee_id": employee_id, "name": names[employee_id], "home_section": home,
                "home_declared": declared, "sections": rows,
                "borrowed_days": sum(row["days"] for row in borrowed),
                "borrowed_hours": round(sum(row["hours"] for row in borrowed), 2),
            })
    result.sort(key=lambda item: item["name"].casefold())
    return result


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").replace("ı", "i").replace("İ", "I").upper())
    return re.sub(r"[^A-Z0-9]", "", "".join(char for char in text if not unicodedata.combining(char)))


def parse_attendance_workbook(
    content: bytes,
    month: str,
    people: list[dict[str, Any]],
    include_codes: list[str] | None = None,
) -> dict[str, Any]:
    """Read a hand-filled puantaj workbook: a header row of day numbers 1..N and a name column."""
    if include_codes is None:
        selected_codes = None
    elif not isinstance(include_codes, list) or not 1 <= len(include_codes) <= 80:
        raise ValueError("Aktarılacak puantaj kodlarını seçin.")
    else:
        selected_codes = set()
        for value in include_codes:
            if not isinstance(value, str):
                raise ValueError("Aktarılacak puantaj kodu geçersiz.")
            code = scheduling.normalize_attendance_code(value)
            if not code or code in selected_codes:
                raise ValueError("Aktarılacak puantaj kodu boş veya yinelenmiş.")
            selected_codes.add(code)
    dates = scheduling.month_dates(month)
    try:
        workbook = load_workbook(BytesIO(content), data_only=True, read_only=False)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types for bad files
        raise ValueError("Excel dosyası okunamadı (.xlsx olmalı).") from exc
    known = {_key(person["name"]): person for person in people}
    best = None
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 30)):
            columns = {}
            for cell in row:
                value = cell.value
                if isinstance(value, float) and value.is_integer():
                    value = int(value)
                if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 31 and value not in columns:
                    columns[value] = cell.column
            if len(columns) >= 28 and all(day in columns for day in range(1, 29)):
                best = (sheet, row[0].row, columns)
                break
        if best:
            break
    if not best:
        raise ValueError("Gün numaralarının (1, 2, 3 … 28+) yazılı olduğu başlık satırı bulunamadı.")
    sheet, header_row, columns = best
    first_day_column = columns[1]
    name_column, best_hits = None, -1
    for column in range(1, first_day_column):
        hits = sum(
            1 for row_number in range(header_row + 1, min(sheet.max_row, header_row + 80) + 1)
            if _key(sheet.cell(row_number, column).value) in known
        )
        text_cells = sum(
            1 for row_number in range(header_row + 1, min(sheet.max_row, header_row + 80) + 1)
            if isinstance(sheet.cell(row_number, column).value, str)
        )
        score = hits * 1000 + text_cells
        if score > best_hits:
            name_column, best_hits = column, score
    if name_column is None:
        raise ValueError("Personel adlarının bulunduğu sütun bulunamadı.")
    overrides: dict[str, dict[str, str]] = defaultdict(dict)
    matched, unknown, invalid = [], [], []
    available_codes = set()
    filtered_out_count = 0
    empty_streak = 0
    for row_number in range(header_row + 1, sheet.max_row + 1):
        raw_name = sheet.cell(row_number, name_column).value
        if not isinstance(raw_name, str) or not raw_name.strip():
            empty_streak += 1
            if empty_streak >= 3 and matched:
                break
            continue
        empty_streak = 0
        person = known.get(_key(raw_name))
        codes = {}
        for day_number, column in columns.items():
            if day_number > len(dates):
                continue
            value = sheet.cell(row_number, column).value
            if value in (None, ""):
                continue
            text = str(value).strip().replace("i", "İ").replace("ı", "I").upper()
            text = {"IST.": "İST.", "IST": "İST.", "İST": "İST.", "G.C": "G.Ç", "GÇ": "G.Ç"}.get(text, text)
            try:
                code = scheduling.normalize_attendance_code(text)
            except ValueError:
                invalid.append({"name": raw_name.strip(), "day": day_number, "value": str(value)})
                continue
            if code:
                available_codes.add(code)
                if selected_codes is not None and code not in selected_codes:
                    filtered_out_count += 1
                    continue
                codes[dates[day_number - 1].isoformat()] = code
        if not codes:
            continue
        if person is None:
            unknown.append({"name": raw_name.strip(), "codes": len(codes)})
            continue
        for day_text, code in codes.items():
            overrides[day_text][str(person["id"])] = code
        matched.append({"employee_id": person["id"], "name": person["name"], "codes": len(codes)})
    if not matched and not unknown and selected_codes is None:
        raise ValueError("Dosyada içe aktarılacak puantaj kodu bulunamadı.")
    return {
        "sheet": sheet.title, "header_row": header_row, "name_column": name_column,
        "matched": matched, "unknown": unknown, "invalid": invalid[:50],
        "available_codes": sorted(available_codes),
        "filtered_out_count": filtered_out_count,
        "overrides": {day: values for day, values in sorted(overrides.items())},
        "code_count": sum(len(values) for values in overrides.values()),
    }
