"""Mutabakat ve kontrol araçları: PDKS karşılaştırması, yayınlanan plan farkı,
çakışma engeli ve puantaj onay akışı."""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from io import BytesIO, StringIO
from typing import Any

from openpyxl import load_workbook

import scheduling

PDKS_MAX_RECORDS = 6000
APPROVAL_STEPS = (
    ("prepared", "Hazırlandı"),
    ("checked", "Kontrol edildi"),
    ("approved", "Onaylandı"),
)
_TR_FOLD = str.maketrans("çğıöşüâîûÇĞİIÖŞÜ", "cgiosuaiucgiiosu")


def name_key(value: Any) -> str:
    return " ".join(str(value or "").translate(_TR_FOLD).casefold().split())


def _header_key(value: Any) -> str:
    return re.sub(r"[^a-z]", "", name_key(value))


_HEADER_WORDS = {
    "name": ("personel", "adsoyad", "adisoyadi", "adsoyadi", "calisan", "isim", "adi", "ad"),
    "date": ("tarih", "gun"),
    "in": ("giris", "girissaati", "in", "ilkgiris"),
    "out": ("cikis", "cikissaati", "out", "soncikis"),
    "time": ("saat", "zaman", "okutma", "hareketsaati"),
}


def _column_roles(header: list[Any]) -> dict[str, int]:
    roles: dict[str, int] = {}
    keys = [_header_key(cell) for cell in header]
    for role, words in _HEADER_WORDS.items():
        for index, key in enumerate(keys):
            if index in roles.values() or not key:
                continue
            if key in words or any(key.startswith(word) for word in words if len(word) > 3):
                roles[role] = index
                break
    return roles


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    match = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        year, month, day = map(int, match.groups())
    else:
        match = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", text)
        if not match:
            return None
        day, month, year = map(int, match.groups())
        year += 2000 if year < 100 else 0
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, (int, float)) and 0 <= value < 1:
        minutes = round(value * 1440)
        return f"{minutes // 60 % 24:02}:{minutes % 60:02}"
    match = re.search(r"(\d{1,2})[:.](\d{2})(?::\d{2})?\s*$", str(value or "").strip())
    if not match:
        return ""
    hour, minute = map(int, match.groups())
    if hour == 24 and minute == 0:
        return "00:00"
    return f"{hour:02}:{minute:02}" if hour < 24 and minute < 60 else ""


def _table_rows(filename: str, content: bytes) -> list[list[Any]]:
    lower = filename.casefold()
    if lower.endswith((".xlsx", ".xlsm")):
        workbook = load_workbook(BytesIO(content), data_only=True, read_only=True)
        try:
            sheet = workbook.worksheets[0]
            return [list(row) for row in sheet.iter_rows(values_only=True)][: PDKS_MAX_RECORDS + 20]
        finally:
            workbook.close()
    if not lower.endswith((".csv", ".txt")):
        raise ValueError("PDKS dosyası .xlsx veya .csv olmalıdır.")
    for encoding in ("utf-8-sig", "cp1254"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    delimiter = max(";,\t", key=sample.count)
    return list(csv.reader(StringIO(text), delimiter=delimiter))[: PDKS_MAX_RECORDS + 20]


def parse_pdks(filename: str, content: bytes, *, health: dict | None = None,
               month: str | None = None) -> list[dict[str, str]]:
    """Read a generic PDKS export. Rows need a name and date plus either
    entry/exit columns or a single punch-time column (first/last punch used)."""
    rows = _table_rows(filename, content)
    header_index, roles = None, {}
    for index, row in enumerate(rows[:15]):
        roles = _column_roles(row)
        if {"name", "date"} <= set(roles) and ({"in", "out"} & set(roles) or "time" in roles):
            header_index = index
            break
    if header_index is None:
        raise ValueError(
            "PDKS dosyasında başlık satırı bulunamadı. Sütunlar: Personel, Tarih, Giriş, Çıkış "
            "(veya tek bir Saat sütunu) olmalıdır."
        )
    punches: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    records: dict[tuple[str, str], dict[str, Any]] = {}
    names: dict[str, str] = {}
    seen = set()
    findings = []
    counts = {"duplicate": 0, "missing": 0, "midnight": 0}
    missing_by_day: dict[str, set[str]] = defaultdict(set)
    first_punch_row: dict[tuple[str, str], int] = {}

    def flag(kind, name, day, line):
        counts[kind] += 1
        if kind == "missing":
            missing_by_day[day].add(name_key(name))
        if len(findings) < 100:
            findings.append({"kind": kind, "name": name, "date": day, "row": line})

    for line, row in enumerate(rows[header_index + 1:], header_index + 2):
        cell = lambda role: row[roles[role]] if role in roles and roles[role] < len(row) else None  # noqa: E731
        name = " ".join(str(cell("name") or "").split())
        raw_date = cell("date")
        day = _parse_date(raw_date)
        if not name or day is None:
            continue
        key = (name_key(name), day.isoformat())
        names[key[0]] = name
        inspect = health is not None and (month is None or key[1][:7] == month)
        if "in" in roles or "out" in roles:
            entry, leave = _parse_time(cell("in")), _parse_time(cell("out"))
            if inspect:
                signature = (*key, entry, leave)
                if (entry or leave) and signature in seen:
                    flag("duplicate", name, key[1], line)
                seen.add(signature)
                if not entry or not leave:
                    flag("missing", name, key[1], line)
                if "00:00" in (entry, leave) or (entry and leave and leave < entry):
                    flag("midnight", name, key[1], line)
            current = records.setdefault(key, {"in": "", "out": ""})
            current.setdefault("punches", [])
            current.setdefault("pairings", [])
            current["pairings"].append({
                "in": entry or "", "out": leave or "", "rows": [line],
            })
            if entry:
                current["punches"].append({"time": entry, "row": line, "direction": "in"})
            if leave:
                current["punches"].append({"time": leave, "row": line, "direction": "out"})
            current["in"] = min(filter(None, (current["in"], entry)), default="")
            current["out"] = max(filter(None, (current["out"], leave)), default="")
        else:
            punch = _parse_time(cell("time")) or _parse_time(raw_date)
            if inspect:
                signature = (*key, punch)
                if punch and signature in seen:
                    flag("duplicate", name, key[1], line)
                seen.add(signature)
                if not punch:
                    flag("missing", name, key[1], line)
                else:
                    first_punch_row.setdefault(key, line)
                if punch == "00:00":
                    flag("midnight", name, key[1], line)
            if punch:
                punches[key].append({"time": punch, "row": line, "direction": "punch"})
    for key, values in punches.items():
        values.sort(key=lambda item: item["row"])
        current = records.setdefault(key, {"in": "", "out": "", "punches": [], "pairings": []})
        current["in"] = values[0]["time"]
        current["out"] = values[-1]["time"] if len(values) > 1 else ""
        current["punches"] = values
        current["pairings"] = [
            {
                "in": values[index]["time"],
                "out": values[index + 1]["time"] if index + 1 < len(values) else "",
                "rows": [values[index]["row"]]
                    + ([values[index + 1]["row"]] if index + 1 < len(values) else []),
            }
            for index in range(0, len(values), 2)
        ]
        if key in first_punch_row and (
            len(values) % 2 == 1 or values[0]["time"] == values[-1]["time"]
        ):
            flag("missing", names[key[0]], key[1], values[-1]["row"])
    result = [
        {
            "name": names[key[0]], "date": key[1], "in": value["in"], "out": value["out"],
            "punches": value.get("punches", []), "pairings": value.get("pairings", []),
        }
        for key, value in sorted(records.items(), key=lambda item: (item[0][1], item[0][0]))
    ]
    if not result:
        raise ValueError("PDKS dosyasında okunabilir kayıt bulunamadı.")
    if len(result) > PDKS_MAX_RECORDS:
        raise ValueError(f"En fazla {PDKS_MAX_RECORDS} PDKS kaydı alınabilir.")
    if health is not None:
        health.update({
            "counts": counts, "findings": findings,
            "missing_clusters": [{"date": day, "people": len(people)}
                                 for day, people in sorted(missing_by_day.items()) if len(people) >= 2],
        })
    return result


def normalize_pdks_records(records: Any) -> list[dict[str, str]]:
    if not isinstance(records, list) or len(records) > PDKS_MAX_RECORDS:
        raise ValueError("PDKS kayıtları geçersiz.")
    clean = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("PDKS kaydı geçersiz.")
        day = _parse_date(record.get("date"))
        name = " ".join(str(record.get("name") or "").split())[:80]
        if not name or day is None:
            raise ValueError("PDKS kaydında personel ve tarih zorunludur.")
        clean.append({
            "name": name, "date": day.isoformat(),
            "in": _parse_time(record.get("in")), "out": _parse_time(record.get("out")),
        })
    return clean


def _minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _clock_drift_review(offsets: list[int]) -> dict[str, Any]:
    ordered = sorted(offsets)
    best = []
    for start in range(len(ordered)):
        end = start
        while end < len(ordered) and ordered[end] - ordered[start] <= 5:
            end += 1
        cluster = ordered[start:end]
        if len(cluster) > len(best):
            best = cluster
    recommended = len(best) >= 3 and abs(sum(best) / len(best)) >= 10
    return {
        "recommended": recommended,
        "matching_records": len(best) if recommended else 0,
        "approximate_offset_minutes": round(sum(best) / len(best)) if recommended else None,
        "range_minutes": [best[0], best[-1]] if recommended else [],
    }


def pdks_compare(
    month: str,
    entries: list[dict[str, Any]],
    people: list[dict[str, Any]],
    records: list[dict[str, str]],
    tolerance_minutes: int = 5,
) -> dict[str, Any]:
    """Compare planned shifts with PDKS punches for the month.

    Each PDKS record belongs to the shift that starts on its date; an exit time
    earlier than the entry time is treated as the next morning (overnight shift)."""
    month_days = {day.isoformat() for day in scheduling.month_dates(month)}
    by_key = {name_key(person["name"]): person for person in people}
    plans: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in entries:
        for day, item in row.get("days", {}).items():
            if day in month_days and item.get("kind") in ("work", "custom", *scheduling.KIND_CODES):
                plans[(int(row["employee_id"]), day)].append(item)
    actual: dict[tuple[int, str], dict[str, str]] = {}
    unknown: dict[str, int] = defaultdict(int)
    for record in records:
        if record["date"] not in month_days:
            continue
        person = by_key.get(name_key(record["name"]))
        if person is None:
            unknown[record["name"]] += 1
            continue
        actual[(int(person["id"]), record["date"])] = record
    names = {int(person["id"]): person["name"] for person in people}
    planned_people = {employee_id for employee_id, _ in plans} | {employee_id for employee_id, _ in actual}
    items: list[dict[str, Any]] = []
    clock_offsets = []
    summary: dict[int, dict[str, Any]] = {
        employee_id: {"employee_id": employee_id, "name": names.get(employee_id, "Personel"),
                      "late_minutes": 0, "early_minutes": 0, "late_count": 0, "early_count": 0,
                      "no_show": 0, "unplanned": 0, "missing_punch": 0}
        for employee_id in planned_people
    }

    def add(kind, severity, employee_id, day, message, minutes=0):
        items.append({"type": kind, "severity": severity, "employee_id": employee_id,
                      "name": names.get(employee_id, "Personel"), "date": day,
                      "minutes": minutes, "message": message})

    for key in sorted(set(plans) | set(actual), key=lambda item: (item[1], names.get(item[0], ""))):
        employee_id, day = key
        works = [item for item in plans.get(key, []) if item.get("kind") == "work"]
        others = [item for item in plans.get(key, []) if item.get("kind") != "work"]
        record = actual.get(key)
        stats = summary[employee_id]
        if works and record is None:
            if any(item.get("kind") == "overnight_exit" for item in others):
                continue
            stats["no_show"] += 1
            add("no_show", "error", employee_id, day,
                f"Planda {works[0]['start']}–{works[0]['end']} var, PDKS kaydı yok")
            continue
        if record is None:
            continue
        if not works:
            if others and all(item.get("kind") == "overnight_exit" for item in others):
                continue
            stats["unplanned"] += 1
            label = (scheduling.KIND_CODES.get(others[0]["kind"]) or others[0].get("custom_code") or "")\
                if others else ""
            add("unplanned", "warning", employee_id, day,
                f"Plansız çalışma {record['in'] or '?'}–{record['out'] or '?'}"
                + (f" (planda {label})" if label else ""))
            continue
        if not record["in"] or not record["out"]:
            stats["missing_punch"] += 1
            add("missing_punch", "warning", employee_id, day,
                f"Eksik okutma: giriş {record['in'] or 'yok'}, çıkış {record['out'] or 'yok'}")
        spans = []
        for item in works:
            begin = _minutes(item["start"])
            spans.append((begin, begin + ((_minutes(item["end"]) - begin) % 1440 or 1440), item))
        start, _, plan = min(spans, key=lambda span: span[0])
        _, end, plan_end = max(spans, key=lambda span: span[1])
        if record["in"] and record["out"]:
            arrival_offset = _minutes(record["in"]) - start
            if arrival_offset < -720:
                arrival_offset += 1440
            actual_end = _minutes(record["out"])
            if end >= 1440 and actual_end < start:
                actual_end += 1440
            departure_offset = actual_end - end
            if departure_offset > 720:
                departure_offset -= 1440
            if abs(arrival_offset - departure_offset) <= 5:
                clock_offsets.append(round((arrival_offset + departure_offset) / 2))
        if record["in"]:
            arrived = _minutes(record["in"])
            late = arrived - start
            if late < -720:
                late += 1440
            if late > tolerance_minutes:
                stats["late_minutes"] += late
                stats["late_count"] += 1
                add("late", "warning", employee_id, day,
                    f"{late} dk geç geldi (plan {plan['start']}, giriş {record['in']})", late)
        if record["out"]:
            left = _minutes(record["out"])
            base = _minutes(record["in"]) if record["in"] else start
            if left < base or (not record["in"] and end >= 1440 and left < start):
                left += 1440
            early = end - left
            if early > tolerance_minutes:
                stats["early_minutes"] += early
                stats["early_count"] += 1
                add("early", "warning", employee_id, day,
                    f"{early} dk erken çıktı (plan {plan_end['end']}, çıkış {record['out']})", early)
    order = {"error": 0, "warning": 1, "info": 2}
    items.sort(key=lambda item: (item["date"], order[item["severity"]], item["name"].casefold()))
    return {
        "month": month,
        "tolerance_minutes": tolerance_minutes,
        "records": sum(1 for record in records if record["date"] in month_days),
        "items": items,
        "people": sorted(summary.values(), key=lambda item: item["name"].casefold()),
        "unknown_names": [{"name": name, "count": count} for name, count in sorted(unknown.items())],
        "counts": {kind: sum(item["type"] == kind for item in items)
                   for kind in ("no_show", "unplanned", "late", "early", "missing_punch")},
        "clock_drift_review": _clock_drift_review(clock_offsets),
    }


def day_label(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    kind = item.get("kind")
    if kind == "work":
        code = item.get("attendance_code") or ""
        return f"{item['start']}–{item['end']}" + (f" {code}" if code and code != "X" else "")
    if kind == "custom":
        return item.get("custom_code", "")
    return scheduling.KIND_CODES.get(kind, kind or "")


def _plan_labels(entries: list[dict[str, Any]], month: str) -> dict[tuple[int, str], str]:
    month_days = {day.isoformat() for day in scheduling.month_dates(month)}
    labels: dict[tuple[int, str], list[str]] = defaultdict(list)
    for row in entries:
        for day, item in row.get("days", {}).items():
            if day in month_days and day_label(item):
                labels[(int(row["employee_id"]), day)].append(day_label(item))
    return {key: " + ".join(sorted(values)) for key, values in labels.items()}


def plan_diff(month: str, published: list[dict[str, Any]], current: list[dict[str, Any]],
              names: dict[int, str]) -> list[dict[str, Any]]:
    before, after = _plan_labels(published, month), _plan_labels(current, month)
    changes = []
    for key in sorted(set(before) | set(after), key=lambda item: (item[1], names.get(item[0], "").casefold())):
        if before.get(key, "") != after.get(key, ""):
            employee_id, day = key
            old, new = before.get(key, ""), after.get(key, "")
            changes.append({
                "employee_id": employee_id, "name": names.get(employee_id, "Personel"), "date": day,
                "before": old, "after": new,
                "type": "added" if not old else "removed" if not new else "changed",
            })
    return changes


def work_overlaps(entries: list[dict[str, Any]], names: dict[int, str] | None = None) -> list[dict[str, Any]]:
    """Return overlapping work intervals of the same person (also across sections)."""
    intervals: dict[int, list[tuple[datetime, datetime, str, str]]] = defaultdict(list)
    for row in entries:
        for day, item in row.get("days", {}).items():
            if item.get("kind") == "work" and item.get("start") and item.get("end"):
                start, end = scheduling._time_interval(day, item["start"], item["end"])
                intervals[int(row["employee_id"])].append((start, end, row.get("section", ""), day))
    result = []
    for employee_id, items in intervals.items():
        items.sort()
        for index, (start, end, section, day) in enumerate(items):
            for other_start, other_end, other_section, other_day in items[index + 1:]:
                if other_start >= end:
                    break
                result.append({
                    "employee_id": employee_id,
                    "name": (names or {}).get(employee_id, "Personel"),
                    "date": other_day,
                    "sections": sorted({section, other_section}),
                    "key": f"{employee_id}|{day}|{other_day}|{start:%H%M}|{other_start:%H%M}",
                    "message": f"{start:%d.%m %H:%M}–{end:%H:%M} ile {other_start:%d.%m %H:%M}–{other_end:%H:%M} çakışıyor",
                })
    return result


def assert_no_new_overlaps(previous: list[dict[str, Any]], entries: list[dict[str, Any]],
                           names: dict[int, str]) -> None:
    old = {item["key"] for item in work_overlaps(previous)}
    new = [item for item in work_overlaps(entries, names) if item["key"] not in old]
    if new:
        first = new[0]
        where = " / ".join(section for section in first["sections"] if section) or "çizelge"
        more = f" (+{len(new) - 1} çakışma daha)" if len(new) > 1 else ""
        raise ValueError(
            f"Çift kayıt engellendi: {first['name']} {where} bölümlerinde aynı saatte yazılmış — "
            f"{first['message']}{more}. Saatleri düzeltip tekrar kaydedin."
        )


def approval_status(month: str, record: dict[str, Any] | None, revisions: dict[str, str]) -> dict[str, Any]:
    record = record or {}
    steps = []
    stale = False
    for key, label in APPROVAL_STEPS:
        item = record.get(key)
        if item and item.get("revisions") != revisions:
            stale = True
        steps.append({"step": key, "label": label, **({k: item[k] for k in ("name", "at")} if item else {})})
    done = [step for step in steps if step.get("name")]
    return {
        "month": month,
        "steps": steps,
        "current": done[-1]["step"] if done else "",
        "next": next((step["step"] for step in steps if not step.get("name")), ""),
        "stale": stale,
        "status": (done[-1]["label"] if done else "Hazırlanmadı") + (" (sonradan değişti)" if stale else ""),
    }


def advance_approval(record: dict[str, Any] | None, step: str, name: str,
                     revisions: dict[str, str]) -> dict[str, Any]:
    keys = [key for key, _ in APPROVAL_STEPS]
    record = dict(record or {})
    if step == "reset":
        return {}
    if step not in keys:
        raise ValueError("Onay adımı geçersiz.")
    name = " ".join(str(name or "").split())
    if not 2 <= len(name) <= 60:
        raise ValueError("Onaylayan kişinin adını yazın (2–60 karakter).")
    if any(item.get("revisions") != revisions for item in record.values() if isinstance(item, dict)):
        record = {}
    index = keys.index(step)
    missing = [label for key, label in APPROVAL_STEPS[:index] if key not in record]
    if missing:
        raise ValueError(f"Önce “{missing[0]}” adımı tamamlanmalı.")
    for key in keys[index:]:
        record.pop(key, None)
    record[step] = {"name": name, "at": datetime.now().isoformat(timespec="minutes"), "revisions": revisions}
    return record
