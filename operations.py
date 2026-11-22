"""Release operations: schedule quality, guarded changes, lifecycle and close-out."""
from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import analytics
import access_services
import compliance
import controls
import linked_leave
import payroll
import planning
import reports
import scheduling
import workspace_tools


ADMIN_ONLY = {
    "GET /api/operations/cost": "admin",
    "POST /api/operations/cost": "admin",
    "GET /api/operations/lifecycle": "admin",
    "GET /api/operations/temporary-assignments": "admin",
    "GET /api/operations/availability": "admin",
    "POST /api/operations/lifecycle/preview": "admin",
    "POST /api/operations/lifecycle/apply": "admin",
    "POST /api/operations/temporary-assignments/preview": "admin",
    "POST /api/operations/emergency/apply": "admin",
    "POST /api/operations/publish": "admin",
    "PUT /api/operations/rules": "admin",
    "PUT /api/operations/availability": "admin",
}

_RULE_TYPES = {"staffing", "leave_limit"}
_MAX_EMPLOYEE_ID = (1 << 63) - 1
_CLOSE_STEPS = (
    ("quality", "Planı ve bulguları incele", "schedule"),
    ("attendance", "Puantajı ve kaynakları doğrula", "attendance"),
    ("reports", "Dönem raporlarını kontrol et", "schedule"),
    ("backup", "Yedeği doğrula", "schedule"),
    ("approval", "Son onayı kaydet", "schedule"),
)
_LEAVE_KINDS = {"annual_leave", "sick_leave", "unpaid_leave", "marriage_leave", "quarantine"}
_GET_ROUTES = {
    "/api/operations/shift-activities",
    "/api/operations/mutual-swaps",
    "/api/operations/payroll-delivery",
    "/api/operations/hourly-staffing",
    "/api/operations/overtime-budget",
    "/api/operations/quality", "/api/operations/rules", "/api/operations/availability",
    "/api/operations/emergency", "/api/operations/publish-diff", "/api/operations/closing",
    "/api/operations/fatigue", "/api/operations/leave-conflicts", "/api/operations/trends",
    "/api/operations/branch-compare", "/api/operations/cost", "/api/operations/lifecycle",
    "/api/operations/temporary-assignments",
}
_WRITE_ROUTES = {
    ("POST", "/api/operations/shift-activities"),
    ("POST", "/api/operations/mutual-swaps"),
    ("POST", "/api/operations/payroll-delivery"),
    ("POST", "/api/operations/payroll-reconcile/preview"),
    ("POST", "/api/operations/payroll-adjustments/request"),
    ("POST", "/api/operations/payroll-adjustments/review"),
    ("PUT", "/api/operations/overtime-budget"),
    ("PUT", "/api/operations/rules"), ("PUT", "/api/operations/availability"),
    ("POST", "/api/operations/emergency/preview"), ("POST", "/api/operations/emergency/apply"),
    ("POST", "/api/operations/publish"), ("POST", "/api/operations/closing"),
    ("POST", "/api/operations/cost"), ("POST", "/api/operations/lifecycle/preview"),
    ("POST", "/api/operations/lifecycle/apply"),
    ("POST", "/api/operations/temporary-assignments/preview"),
}


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS operations_rules (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS operations_availability (
            employee_id INTEGER PRIMARY KEY,
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS operations_closings (
            month TEXT NOT NULL,
            step TEXT NOT NULL,
            name TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            PRIMARY KEY(month, step)
        );
        CREATE TABLE IF NOT EXISTS operations_emergency_audit (
            id TEXT PRIMARY KEY,
            month TEXT NOT NULL,
            employee_id INTEGER NOT NULL,
            replacement_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            reason TEXT NOT NULL,
            source_revision TEXT NOT NULL,
            before_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS operations_cost_scenarios (
            id TEXT PRIMARY KEY,
            month TEXT NOT NULL,
            name TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS operations_payroll_deliveries (
            id INTEGER PRIMARY KEY,
            month TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            delivered_at TEXT NOT NULL,
            reference TEXT NOT NULL,
            payload TEXT NOT NULL,
            UNIQUE(month, fingerprint)
        );
        CREATE TABLE IF NOT EXISTS operations_payroll_adjustments (
            id TEXT PRIMARY KEY,
            month TEXT NOT NULL,
            delivery_id INTEGER NOT NULL,
            base_fingerprint TEXT NOT NULL,
            current_fingerprint TEXT NOT NULL,
            locked INTEGER NOT NULL,
            reason TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
            requested_by TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            decided_by TEXT,
            decided_at TEXT
        );
        CREATE TABLE IF NOT EXISTS operations_lifecycle_events (
            id TEXT PRIMARY KEY,
            employee_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            effective_date TEXT NOT NULL,
            from_section TEXT NOT NULL DEFAULT '',
            section TEXT NOT NULL DEFAULT '',
            pattern TEXT NOT NULL DEFAULT '',
            source_revision TEXT NOT NULL,
            created_at TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS operations_lifecycle_due
            ON operations_lifecycle_events(applied_at,effective_date,employee_id);
        CREATE TABLE IF NOT EXISTS operations_temporary_assignments (
            id TEXT PRIMARY KEY,
            employee_id INTEGER NOT NULL,
            location_type TEXT NOT NULL,
            location TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS operations_temporary_assignment_dates
            ON operations_temporary_assignments(employee_id,start_date,end_date);
        """
    )
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(operations_lifecycle_events)")}
    if "pattern" not in columns:
        connection.execute(
            "ALTER TABLE operations_lifecycle_events ADD COLUMN pattern TEXT NOT NULL DEFAULT ''"
        )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _date(value: Any, label: str = "Tarih") -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} YYYY-AA-GG biçiminde olmalıdır.") from exc


def _strict_int(value: Any, label: str, minimum: int = 0, maximum: int | None = None) -> int:
    if (not isinstance(value, int) or isinstance(value, bool) or value < minimum
            or maximum is not None and value > maximum):
        raise ValueError(f"{label} geçersiz.")
    return value


def _minutes(value: Any, allow_24: bool = False) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
        raise ValueError("Saat HH:MM biçiminde olmalıdır.")
    try:
        hour, minute = int(value[:2]), int(value[3:])
    except ValueError as exc:
        raise ValueError("Saat HH:MM biçiminde olmalıdır.") from exc
    if allow_24 and value == "24:00":
        return 1440
    if hour > 23 or minute > 59:
        raise ValueError("Saat 00:00-23:59 arasında olmalıdır.")
    return hour * 60 + minute


def normalize_rule(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Operasyon kuralı geçersiz.")
    rule_id = value.get("id")
    if not isinstance(rule_id, str) or not 1 <= len(rule_id) <= 48 \
            or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in rule_id):
        raise ValueError("Kural kimliği geçersiz.")
    kind = value.get("type")
    if kind not in _RULE_TYPES:
        raise ValueError("Kural türü staffing veya leave_limit olmalıdır.")
    section = " ".join(str(value.get("section") or "").split())
    if section not in scheduling.section_names():
        raise ValueError("Bölüm adı tanımlı bölümlerden biri olmalıdır.")
    start_date = _date(value.get("start_date"), "Başlangıç tarihi")
    end_date = _date(value.get("end_date"), "Bitiş tarihi")
    if end_date < start_date or (end_date - start_date).days > 370:
        raise ValueError("Kural tarih aralığı en fazla 371 gün olmalıdır.")
    weekdays = value.get("weekdays", list(range(7)))
    if (not isinstance(weekdays, list) or not weekdays
            or any(not isinstance(day, int) or isinstance(day, bool) or not 0 <= day <= 6 for day in weekdays)):
        raise ValueError("Hafta günleri 0 (Pazartesi)-6 (Pazar) arasında seçilmelidir.")
    result: dict[str, Any] = {
        "id": rule_id, "type": kind, "section": section,
        "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        "weekdays": sorted(set(weekdays)),
        "start": str(value.get("start") or "00:00"),
        "end": str(value.get("end") or "24:00"),
        "severity": value.get("severity", "warning"),
        "exception_reason": " ".join(str(value.get("exception_reason") or "").split())[:300],
    }
    begin, finish = _minutes(result["start"]), _minutes(result["end"], allow_24=True)
    if finish <= begin:
        raise ValueError("Kural saat aralığı aynı gün içinde ve başlangıçtan sonra bitmelidir.")
    if result["severity"] not in ("info", "warning", "error"):
        raise ValueError("Bulgu önem derecesi info, warning veya error olmalıdır.")
    if kind == "staffing":
        result["minimum"] = _strict_int(value.get("minimum"), "Asgari çalışan sayısı", 1, 99)
        skill = " ".join(str(value.get("skill") or "").split())
        if len(skill) > 60:
            raise ValueError("Yetkinlik en fazla 60 karakter olabilir.")
        result["skill"] = skill
    else:
        result["maximum"] = _strict_int(value.get("maximum"), "Eşzamanlı izin üst sınırı", 0, 99)
    if "special_day" in value:
        title = value["special_day"]
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
            raise ValueError("Özel gün adı 1–80 karakter olmalıdır.")
        if kind != "staffing" or start_date != end_date:
            raise ValueError("Özel gün düzeni tek tarihli bir asgari kadro kuralı olmalıdır.")
        result.update(special_day=title.strip(), severity="warning", weekdays=[start_date.weekday()])
    return result


def normalize_rules(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 200:
        raise ValueError("En fazla 200 operasyon kuralı kaydedilebilir.")
    result, seen = [], set()
    for raw in value:
        item = normalize_rule(raw)
        if item["id"] in seen:
            raise ValueError("Kural kimlikleri yinelenemez.")
        seen.add(item["id"])
        result.append(item)
    return result


def normalize_availability(value: Any, valid_employee_ids: set[int] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Uygunluk bilgisi geçersiz.")
    employee_id = _strict_int(value.get("employee_id"), "Personel seçimi", 1, _MAX_EMPLOYEE_ID)
    if valid_employee_ids is not None and employee_id not in valid_employee_ids:
        raise ValueError("Personel bulunamadı.")
    start, end = _date(value.get("start_date"), "Geçerlilik başlangıcı"), _date(value.get("end_date"), "Geçerlilik bitişi")
    if end < start or (end - start).days > 1095:
        raise ValueError("Uygunluk dönemi en fazla üç yıl olabilir.")
    weekdays = value.get("unavailable_weekdays", [])
    if (not isinstance(weekdays, list)
            or any(not isinstance(day, int) or isinstance(day, bool) or not 0 <= day <= 6 for day in weekdays)):
        raise ValueError("Uygun olmadığı hafta günleri geçersiz.")
    ranges = value.get("unavailable_ranges", [])
    if not isinstance(ranges, list) or len(ranges) > 100:
        raise ValueError("En fazla 100 uygun olunmayan tarih aralığı girilebilir.")
    clean_ranges = []
    for item in ranges:
        if not isinstance(item, dict):
            raise ValueError("Uygun olunmayan tarih aralığı geçersiz.")
        begin, finish = _date(item.get("start")), _date(item.get("end"))
        if finish < begin:
            raise ValueError("Uygun olunmayan tarih aralığı ters olamaz.")
        clean_ranges.append({"start": begin.isoformat(), "end": finish.isoformat()})
    for field in ("no_night", "no_weekend", "preference"):
        if field in value and not isinstance(value[field], bool):
            raise ValueError("Uygunluk tercihleri true/false olmalıdır.")
    return {
        "employee_id": employee_id, "start_date": start.isoformat(), "end_date": end.isoformat(),
        "unavailable_weekdays": sorted(set(weekdays)), "unavailable_ranges": clean_ranges,
        "no_night": bool(value.get("no_night", False)),
        "no_weekend": bool(value.get("no_weekend", False)),
        "preference": bool(value.get("preference", True)),
    }


def _rules(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute("SELECT payload FROM operations_rules ORDER BY id").fetchall()
    return [json.loads(row["payload"]) for row in rows]


def save_rules(connection: sqlite3.Connection, rules: Any) -> list[dict[str, Any]]:
    normalized = normalize_rules(rules)
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("DELETE FROM operations_rules")
        connection.executemany(
            "INSERT INTO operations_rules(id,payload) VALUES (?,?)",
            [(item["id"], _json(item)) for item in normalized],
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return normalized


def save_availability(connection: sqlite3.Connection, value: Any, people: list[dict[str, Any]]) -> dict[str, Any]:
    valid_ids = {int(item["id"]) for item in people}
    item = normalize_availability(value, valid_ids)
    connection.execute(
        "INSERT INTO operations_availability(employee_id,payload) VALUES (?,?) "
        "ON CONFLICT(employee_id) DO UPDATE SET payload=excluded.payload",
        (item["employee_id"], _json(item)),
    )
    return item


def _effective_availability(store, day_text: str) -> dict[str, dict[str, Any]]:
    day_text = _date(day_text).isoformat()
    rows = store.connection.execute("SELECT employee_id,payload FROM operations_availability")
    result = {}
    for row in rows:
        item = json.loads(row["payload"])
        if item["start_date"] <= day_text <= item["end_date"]:
            result[str(row["employee_id"])] = item
    return result


def _minutes_interval(day_text: str, item: dict[str, Any]) -> tuple[datetime, datetime] | None:
    if item.get("kind") != "work" or not item.get("start") or not item.get("end"):
        return None
    try:
        begin = datetime.combine(date.fromisoformat(day_text), time.fromisoformat(item["start"]))
        finish = datetime.combine(date.fromisoformat(day_text), time.fromisoformat(item["end"]))
    except (TypeError, ValueError):
        return None
    if finish <= begin:
        finish += timedelta(days=1)
    return begin, finish


_SHIFT_ACTIVITIES_KEY = "shift_activities"


def _activity_day(month, value):
    day = date.fromisoformat(value) if isinstance(value, str) else None
    if not day or day.isoformat() != value or day.strftime("%Y-%m") != month:
        raise ValueError("Mola veya devir tarihi seçilen ayda olmalıdır.")
    return day


def shift_activity_report(store, day):
    previous = day - timedelta(days=1)
    periods = sorted({value.strftime("%Y-%m") for value in (previous, day)})
    entries = [row for period in periods for row in store.schedule_month(period)["entries"]]
    midnight = datetime.combine(day, time.min)
    shifts = {}
    for row in entries:
        for value in (previous, day):
            shift = row["days"].get(value.isoformat(), {})
            if shift.get("kind") == "work":
                start, end = scheduling._time_interval(value.isoformat(), shift["start"], shift["end"])
                if end > midnight:
                    shifts.setdefault((row["section"], row["employee_id"]), []).append((start, end))
    records = store._setting(_SHIFT_ACTIVITIES_KEY, {}).get(day.isoformat(), [])
    findings = []
    ranges = []
    for item in records:
        start = midnight + timedelta(minutes=_minutes(item["start"]))
        end = midnight + timedelta(minutes=_minutes(item["end"], True))
        ids = {item["employee_id"]}
        if item["kind"] == "handover":
            ids.add(item["recipient_id"])
        if any(not any(a <= start < end <= b for a, b in shifts.get((item["section"], person), ()))
               for person in ids):
            findings.append({"kind": "outside_shift", "ids": [item["id"]],
                             "message": "Kayıt, bölümdeki çalışma vardiyasının dışında."})
        for other, left, right, members in ranges:
            if start >= right or end <= left:
                continue
            if members & ids:
                kind, message = "person_overlap", "Aynı kişinin mola veya devir kayıtları çakışıyor."
            elif other["section"] == item["section"] and other["kind"] == item["kind"] == "break":
                kind, message = "parallel_break", "Bu bölümde eşzamanlı mola planlandı."
            else:
                continue
            findings.append({"kind": kind, "ids": [other["id"], item["id"]], "message": message})
        ranges.append((item, start, end, ids))
    return {"date": day.isoformat(), "items": records, "findings": findings,
            "people": [{"id": person["id"], "name": person["name"]} for person in store.people()],
            "sections": scheduling.section_names(), "locked": day.strftime("%Y-%m") in store.locked_periods()}


def save_shift_activity(store, payload):
    month = scheduling.validate_month(payload.get("month", ""))
    day = _activity_day(month, payload.get("date"))
    key = day.isoformat()
    store.connection.execute("BEGIN IMMEDIATE")
    with store.connection:
        store.assert_periods_unlocked([month])
        all_days = store._setting(_SHIFT_ACTIVITIES_KEY, {})
        items = all_days.get(key, [])
        if set(payload) == {"month", "date", "remove_id"}:
            updated = [item for item in items if item["id"] != payload["remove_id"]]
            if len(updated) == len(items):
                raise ValueError("Silinecek mola veya devir bulunamadı.")
        else:
            if set(payload) != {"month", "date", "section", "kind", "start", "end", "employee_id", "recipient_id"}:
                raise ValueError("Mola veya devir kaydı geçersiz.")
            if len(items) >= 80:
                raise ValueError("Bir güne en fazla 80 mola veya devir eklenebilir.")
            section, kind, worker, recipient = (payload[field] for field in
                                                ("section", "kind", "employee_id", "recipient_id"))
            people = {person["id"] for person in store.people()}
            if section not in scheduling.section_names() or kind not in ("break", "handover"):
                raise ValueError("Bölüm veya kayıt türü geçersiz.")
            if type(worker) is not int or worker not in people or (kind == "break" and recipient is not None) or (
                    kind == "handover" and (type(recipient) is not int or recipient not in people or worker == recipient)):
                raise ValueError("Mola için bir kişi; devir için iki farklı kişi seçin.")
            if _minutes(payload["start"]) >= _minutes(payload["end"], True):
                raise ValueError("Bitiş başlangıçtan sonra olmalıdır; gece yarısı için 24:00 kullanın.")
            item = {field: payload[field] for field in
                    ("section", "kind", "start", "end", "employee_id", "recipient_id")}
            if any(all(existing[field] == value for field, value in item.items()) for existing in items):
                raise ValueError("Bu mola veya devir zaten kayıtlı.")
            item["id"] = secrets.token_hex(12)
            updated = [*items, item]
        if updated:
            all_days[key] = updated
        else:
            all_days.pop(key, None)
        store._write_setting(_SHIFT_ACTIVITIES_KEY, all_days)
        report = shift_activity_report(store, day)
        if "remove_id" not in payload and any(finding["kind"] == "outside_shift" and item["id"] in finding["ids"]
                                              for finding in report["findings"]):
            raise ValueError("Mola veya devir, seçilen kişilerin aynı bölümdeki kayıtlı çalışma saatleri içinde olmalıdır.")
        store.record_change("schedule.activities", f"Mola ve görev devri planı: {key}")
        return report


_SWAPS_KEY = "mutual_shift_swaps"


def _swap_pair(entries, day, ids):
    pair = []
    for employee_id in ids:
        rows = [row for row in entries if row["employee_id"] == employee_id and row["days"].get(day)]
        if len(rows) != 1 or rows[0]["days"][day].get("kind") != "work":
            raise ValueError("Her iki kişinin de o gün tek bir çalışma vardiyası olmalıdır.")
        row = rows[0]
        pair.append({"row_id": row["row_id"], "section": row["section"], "shift": row["days"][day]})
    if pair[0]["section"] != pair[1]["section"] or all(
            pair[0]["shift"][key] == pair[1]["shift"][key] for key in ("start", "end")):
        raise ValueError("Aynı bölümden, saatleri farklı iki vardiya seçin.")
    return pair


def mutual_swaps(store, month=None, employee_id=None):
    items = store._setting(_SWAPS_KEY, {}).values()
    return [item for item in reversed(list(items))
            if (month is None or item["date"][:7] == month)
            and (employee_id is None or employee_id in item["employees"])][:100]


def _check_swap_source(store, item):
    day, ids = item["date"], item["employees"]
    month = day[:7]
    store.assert_periods_unlocked([month])
    people = {}
    for person in store.people(active_only=True):
        profile = store.employee_profile(person["id"])
        if (profile.get("hire_date") or "0001-01-01") <= day <= (profile.get("exit_date") or "9999-12-31"):
            people[person["id"]] = person["name"]
    if not set(ids) <= people.keys():
        raise ValueError("Personel artık takas günü için etkin değil.")
    current = store.schedule_month(month)
    published = store._setting("published_plans", {}).get(month, {}).get("entries", [])
    if _swap_pair(current["entries"], day, ids) != item["pair"] or _swap_pair(published, day, ids) != item["pair"]:
        raise ValueError("Kayıtlı vardiyalar değişti; yeni bir takas oluşturun.")
    if store.connection.execute(
        "SELECT 1 FROM schedule_protection WHERE month=? AND day=? AND employee_id IN (?,?)",
        (month, day, *ids),
    ).fetchone():
        raise ValueError("Korumalı vardiya takas edilemez.")
    return current, people


def _swap_source_fingerprint(store, month, day, ids, pair, current, published, neighbors):
    availability = [dict(row) for row in store.connection.execute(
        "SELECT employee_id,payload FROM operations_availability ORDER BY employee_id"
    )]
    protections = [dict(row) for row in store.connection.execute(
        "SELECT row_id,day,employee_id,section FROM schedule_protection WHERE month=? ORDER BY row_id,day",
        (month,),
    )]
    return _fingerprint({
        "month": month, "date": day, "employees": ids, "pair": pair,
        "revision": current["revision"], "entries": current["entries"], "published": published,
        "neighbors": neighbors, "people": store.people(), "profiles": store.employee_profiles(),
        "availability": availability, "rules": _rules(store.connection),
        "compliance": store.compliance_settings(), "validation": store.schedule_validation_settings(),
        "planning": store.planning_settings(), "leave": store.leave_requests(),
        "linked_leave": linked_leave.calendar_requests(store, month),
        "attendance": store.attendance_month(month), "overtime": store.overtime_settings(month),
        "holidays": store.custom_holidays(), "protections": protections,
    })


def _prepare_mutual_swap(store, month, day, ids):
    current = store.schedule_month(month)
    published = store._setting("published_plans", {}).get(month, {}).get("entries", [])
    pair = _swap_pair(current["entries"], day, ids)
    if pair != _swap_pair(published, day, ids):
        raise ValueError("Takas için iki vardiyanın da güncel hali yayımlanmış olmalıdır.")
    item = {"date": day, "employees": ids, "pair": pair}
    current, people = _check_swap_source(store, item)
    entries = _shift_copy(current["entries"])
    by_row = {row["row_id"]: row for row in entries}
    for index in range(2):
        by_row[pair[index]["row_id"]]["days"][day] = pair[1 - index]["shift"]
    neighbors = [row for period in scheduling.adjacent_months(month) if period != month
                 for row in store.schedule_month(period)["entries"]]
    fingerprint = _swap_source_fingerprint(store, month, day, ids, pair, current, published, neighbors)
    return {
        "current": current, "pair": pair, "entries": entries, "neighbors": neighbors,
        "people": people, "fingerprint": fingerprint,
    }


def _mutual_swap_findings(store, month, current, candidate, neighbors, names):
    blockers, seen_blockers = [], set()

    def block(kind, message):
        if message not in seen_blockers:
            blockers.append({"kind": kind, "severity": "error", "message": message})
            seen_blockers.add(message)

    try:
        controls.assert_no_new_overlaps(current["entries"] + neighbors, candidate + neighbors, names)
    except ValueError as exc:
        block("rest", str(exc))
    try:
        prepared = linked_leave.prepare_schedule_save(store, month, candidate)
        if prepared["conflicts"] or prepared["entries"] != candidate:
            block("leave", "Bağlı izin nedeniyle takas uygulanamaz.")
    except ValueError as exc:
        block("leave", str(exc))
    try:
        validate_schedule_in_transaction(store, month, candidate, before_entries=current["entries"])
    except ValueError as exc:
        block("rule", str(exc))

    before = quality_report(store, month, current["entries"])["findings"]
    after = quality_report(store, month, candidate)["findings"]
    existing = {_fingerprint(item) for item in before}
    findings = [item for item in after if _fingerprint(item) not in existing]
    for item in findings:
        if item.get("severity") == "error":
            block("quality", item["message"])
    return findings, blockers


def preview_mutual_swap(handler, store, month, payload):
    if set(payload) != {"action", "date", "first_id", "second_id"} or payload.get("action") != "preview":
        raise ValueError("Takas önizlemesi geçersiz.")
    month = scheduling.validate_month(month)
    day = _activity_day(month, payload["date"]).isoformat()
    ids = [payload["first_id"], payload["second_id"]]
    if any(type(value) is not int or not 1 <= value <= _MAX_EMPLOYEE_ID for value in ids) or ids[0] == ids[1]:
        raise ValueError("İki farklı personel seçin.")
    prepared = _prepare_mutual_swap(store, month, day, ids)
    current, candidate = prepared["current"], prepared["entries"]
    names = prepared["people"]
    findings, blockers = _mutual_swap_findings(
        store, month, current, candidate, prepared["neighbors"], names,
    )
    swaps = []
    for index, employee_id in enumerate(ids):
        swaps.append({
            "employee_id": employee_id, "name": names[employee_id],
            "from": {key: prepared["pair"][index]["shift"][key] for key in ("start", "end")},
            "to": {key: prepared["pair"][1 - index]["shift"][key] for key in ("start", "end")},
        })
    periods = prepared["neighbors"] + current["entries"]
    proposed = prepared["neighbors"] + candidate
    before_attendance, _, _ = handler._attendance_for(store, month, entries=periods)
    after_attendance, _, _ = handler._attendance_for(store, month, entries=proposed)
    before_rows = {row["employee_id"]: row for row in before_attendance["rows"]}
    after_rows = {row["employee_id"]: row for row in after_attendance["rows"]}
    attendance_impacts = []
    for employee_id in ids:
        before = before_rows.get(employee_id, {})
        after = after_rows.get(employee_id, {})
        attendance_impacts.append({
            "employee_id": employee_id, "name": names[employee_id],
            "code_before": before.get("codes", {}).get(day, ""),
            "code_after": after.get("codes", {}).get(day, ""),
            "worked_hours_before": before.get("worked_hours", 0),
            "worked_hours_after": after.get("worked_hours", 0),
            "overtime_hours_before": before.get("overtime_hours", 0),
            "overtime_hours_after": after.get("overtime_hours", 0),
        })
    pending = store._setting(_SWAPS_KEY, {}).values()
    if any(item["date"] == day and item["status"] == "pending" and set(ids) & set(item["employees"])
           for item in pending):
        blockers.append({"kind": "pending", "severity": "error",
                         "message": "Bu gün için personelin bekleyen bir takası var."})
    return {
        "month": month, "date": day, "revision": current["revision"],
        "fingerprint": prepared["fingerprint"], "can_create": not blockers,
        "swaps": swaps, "attendance_impacts": attendance_impacts,
        "findings": findings[:80], "blockers": blockers,
        "notice": "Önizleme kayıt yapmaz; puantaj değerleri yalnızca mevcut kurallarla hesaplanmıştır.",
    }


def manage_mutual_swap(store, month, payload):
    action = payload.get("action")
    if action == "approve":
        if set(payload) != {"action", "id"} or not isinstance(payload["id"], str):
            raise ValueError("Takas onayı geçersiz.")
        identifier = payload["id"]
        expected = store.schedule_month(month)["revision"]

        def apply():
            swaps = store._setting(_SWAPS_KEY, {})
            item = swaps.get(identifier)
            if not item or item["date"][:7] != month or item["status"] != "pending" \
                    or set(item["accepted"]) != set(item["employees"]):
                raise ValueError("Takas için iki ayrı personel onayı gereklidir.")
            current, names = _check_swap_source(store, item)
            day, ids = item["date"], item["employees"]
            entries = _shift_copy(current["entries"])
            by_row = {row["row_id"]: row for row in entries}
            pair = item["pair"]
            for index in range(2):
                by_row[pair[index]["row_id"]]["days"][day] = pair[1 - index]["shift"]
            neighbors = [row for period in scheduling.adjacent_months(month) if period != month
                         for row in store.schedule_month(period)["entries"]]
            controls.assert_no_new_overlaps(current["entries"] + neighbors, entries + neighbors, names)
            prepared = linked_leave.prepare_schedule_save(store, month, entries)
            if prepared["entries"] != entries or any(
                conflict.get("employee_id") in ids and conflict.get("date") == day
                for conflict in prepared["conflicts"]
            ):
                raise ValueError("Bağlı izin nedeniyle takas uygulanamadı.")
            validate_schedule_in_transaction(store, month, entries, before_entries=current["entries"])
            item["status"] = "approved"
            store._write_setting(_SWAPS_KEY, swaps)
            return entries

        revision = store._save_month("schedule_months", month, None, expected, "schedule.mutual_swap",
                                     f"Karşılıklı vardiya takası uygulandı: {month}",
                                     {"swap_id": identifier}, prepare_payload=apply)
        if not revision:
            raise ValueError("Vardiya kaydı eş zamanlı değişti; yeniden deneyin.")
        return {"items": mutual_swaps(store, month), "revision": revision}

    store.connection.execute("BEGIN IMMEDIATE")
    with store.connection:
        swaps = store._setting(_SWAPS_KEY, {})
        if action == "cancel" and set(payload) == {"action", "id"} and isinstance(payload["id"], str):
            item = swaps.get(payload["id"])
            if not item or item["date"][:7] != month or item["status"] != "pending":
                raise ValueError("Bekleyen takas bulunamadı.")
            item["status"] = "cancelled"
        elif action == "create" and set(payload) == {
            "action", "date", "first_id", "second_id", "fingerprint",
        }:
            day = _activity_day(month, payload["date"]).isoformat()
            ids = [payload["first_id"], payload["second_id"]]
            if any(type(value) is not int or not 1 <= value <= _MAX_EMPLOYEE_ID for value in ids) or ids[0] == ids[1]:
                raise ValueError("İki farklı personel seçin.")
            if any(item["date"] == day and item["status"] == "pending" and set(ids) & set(item["employees"])
                   for item in swaps.values()):
                raise ValueError("Bu gün için personelin bekleyen bir takası var.")
            prepared = _prepare_mutual_swap(store, month, day, ids)
            if payload["fingerprint"] != prepared["fingerprint"]:
                raise ValueError("Önizlemeden sonra vardiya veya kurallar değişti; yeniden önizleyin.")
            findings, blockers = _mutual_swap_findings(
                store, month, prepared["current"], prepared["entries"], prepared["neighbors"], prepared["people"],
            )
            if blockers:
                raise ValueError(blockers[0]["message"])
            identifier = secrets.token_hex(12)
            item = {"id": identifier, "date": day, "employees": ids, "pair": prepared["pair"],
                    "accepted": [], "status": "pending"}
            swaps[identifier] = item
        else:
            raise ValueError("Takas isteği geçersiz.")
        store._write_setting(_SWAPS_KEY, swaps)
        store.record_change(f"schedule.mutual_swap.{action}", f"Karşılıklı vardiya takası: {item['date']}",
                            {"swap_id": item["id"]})
    return {"items": mutual_swaps(store, month)}


def respond_mutual_swap(store, employee_id, identifier, decision):
    swaps = store._setting(_SWAPS_KEY, {})
    item = swaps.get(identifier)
    if not item or item["status"] != "pending" or employee_id not in item["employees"]:
        raise ValueError("Bu takas yanıtlanamıyor.")
    if decision == "accept":
        _check_swap_source(store, item)
        if employee_id not in item["accepted"]:
            item["accepted"].append(employee_id)
    elif decision == "decline":
        item["status"] = "declined"
    else:
        raise ValueError("Takas yanıtı geçersiz.")
    store._write_setting(_SWAPS_KEY, swaps)
    store.record_change("schedule.mutual_swap.response", f"Personel takas yanıtı: {item['date']}",
                        {"swap_id": identifier, "employee_id": employee_id, "decision": decision})


def _custom_rule_findings(month: str, entries: list[dict[str, Any]], rules: list[dict[str, Any]],
                          profiles: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    month_days = scheduling.month_dates(month)
    for rule in rules:
        begin, finish = date.fromisoformat(rule["start_date"]), date.fromisoformat(rule["end_date"])
        dates = [day for day in month_days if begin <= day <= finish and day.weekday() in rule["weekdays"]]
        for day in dates:
            window_start = datetime.combine(day, time.fromisoformat(rule["start"]))
            window_end = datetime.combine(day, time.min) + timedelta(minutes=_minutes(rule["end"], allow_24=True))
            matching, leaves, boundaries = [], [], {window_start, window_end}
            for row in entries:
                if row.get("section") != rule["section"]:
                    continue
                item = (row.get("days") or {}).get(day.isoformat())
                if not item:
                    continue
                if rule["type"] == "leave_limit":
                    if item.get("kind") in _LEAVE_KINDS:
                        leaves.append(int(row["employee_id"]))
                    continue
                interval = _minutes_interval(day.isoformat(), item)
                if interval:
                    s, e = interval
                    if s < window_end and e > window_start:
                        matching.append((int(row["employee_id"]), s, e))
                        boundaries.update((max(s, window_start), min(e, window_end)))
            if rule["type"] == "leave_limit":
                actual = len(set(leaves))
                if actual > rule["maximum"]:
                    findings.append({
                        "type": "leave_limit", "severity": rule["severity"], "rule_id": rule["id"],
                        "date": day.isoformat(), "section": rule["section"], "actual": actual,
                        "expected": rule["maximum"],
                        "message": f"{rule['section']}: {day:%d.%m} günü eşzamanlı izin {actual}; üst sınır {rule['maximum']}.",
                        "exception_reason": rule["exception_reason"],
                    })
                continue
            skill_ids = {
                int(person_id) for person_id, profile in profiles.items()
                if rule["skill"] and rule["skill"].casefold()
                in {str(skill).casefold() for skill in profile.get("skills", [])}
            }
            segments = sorted(boundaries)
            worst = None
            for left, right in zip(segments, segments[1:]):
                active = {person_id for person_id, s, e in matching if s < right and e > left}
                skilled = active & skill_ids
                current = (len(active), len(skilled), left, right)
                if worst is None or (current[0] < worst[0] or current[0] == worst[0] and current[1] < worst[1]):
                    worst = current
            if worst is None:
                worst = (0, 0, window_start, window_end)
            actual = worst[1] if rule["skill"] else worst[0]
            if actual < rule["minimum"]:
                what = f"{rule['minimum']} yetkinlikli çalışan ({rule['skill']})" if rule["skill"] else f"{rule['minimum']} çalışan"
                findings.append({
                    "type": "staffing", "severity": rule["severity"], "rule_id": rule["id"],
                    "date": day.isoformat(), "section": rule["section"], "actual": actual,
                    "expected": rule["minimum"], "start": worst[2].strftime("%H:%M"),
                    "end": worst[3].strftime("%H:%M"), "exception_reason": rule["exception_reason"],
                    "message": f"{rule['section']}: {day:%d.%m} {worst[2]:%H:%M}–{worst[3]:%H:%M} "
                               f"aralığında {actual}; en az {what} gerekir.",
                })
    return findings


def _availability_findings(month: str, entries: list[dict[str, Any]], store,
                           names: dict[int, str]) -> list[dict[str, Any]]:
    findings = []
    availability_by_date = {}
    for row in entries:
        employee_id = int(row["employee_id"])
        for day_text, item in (row.get("days") or {}).items():
            if not day_text.startswith(month) or item.get("kind") != "work":
                continue
            if day_text not in availability_by_date:
                availability_by_date[day_text] = _effective_availability(store, day_text)
            availability = availability_by_date[day_text]
            rule = availability.get(str(employee_id))
            if not rule:
                continue
            day = date.fromisoformat(day_text)
            reasons = []
            if day.weekday() in rule["unavailable_weekdays"]:
                reasons.append("haftalık gün kısıtı")
            if any(period["start"] <= day_text <= period["end"] for period in rule["unavailable_ranges"]):
                reasons.append("tarih aralığı kısıtı")
            interval = _minutes_interval(day_text, item)
            if interval:
                if rule["no_night"] and compliance.night_minutes(*interval) > 0:
                    reasons.append("gece çalışmama kısıtı")
                if rule["no_weekend"] and day.weekday() >= 5:
                    reasons.append("hafta sonu kısıtı")
            if reasons:
                kind = "preference" if rule["preference"] else "hard_constraint"
                explanation = (
                    "Karşılanamayan tercih; planı kaydetmeyi engellemez."
                    if rule["preference"] else
                    "Kesin uygunluk kısıtı; yeni çakışma çizelge kaydında engellenir."
                )
                findings.append({
                    "type": "availability", "severity": "info" if rule["preference"] else "error",
                    "constraint_kind": kind,
                    "employee_id": employee_id, "name": names.get(employee_id, "Personel"),
                    "date": day_text, "message": f"{names.get(employee_id, 'Personel')}: {', '.join(reasons)} "
                                                   f"({rule['start_date']}–{rule['end_date']}). {explanation}",
                })
    return findings


def fatigue_findings(month: str, entries: list[dict[str, Any]], names: dict[int, str],
                     consecutive_nights: int = 3, long_streak: int = 7,
                     minimum_rest_hours: float = 11) -> list[dict[str, Any]]:
    """Inspect patterns only; these are operational flags, not medical conclusions."""
    month = scheduling.validate_month(month)
    shifts: dict[int, list[tuple[datetime, datetime, str, dict[str, Any]]]] = {}
    for row in entries:
        employee_id = int(row["employee_id"])
        for day_text, item in (row.get("days") or {}).items():
            interval = _minutes_interval(day_text, item)
            if interval:
                shifts.setdefault(employee_id, []).append((*interval, day_text, item))
    findings = []
    month_start = date.fromisoformat(month + "-01")
    month_end = scheduling.month_dates(month)[-1]
    for employee_id, items in shifts.items():
        items.sort(key=lambda shift: shift[0])
        night_dates = sorted({
            start.date() for start, end, _, _ in items
            if compliance.night_minutes(start, end) >= compliance.NIGHT_SHIFT_MINUTES
        })
        runs: list[list[date]] = []
        for day in night_dates:
            if not runs or day != runs[-1][-1] + timedelta(days=1):
                runs.append([day])
            else:
                runs[-1].append(day)
        for run in runs:
            if len(run) >= consecutive_nights and run[-1] >= month_start and run[0] <= month_end:
                findings.append({
                    "type": "consecutive_nights", "severity": "warning", "employee_id": employee_id,
                    "name": names.get(employee_id, "Personel"),
                    "date": [item.isoformat() for item in run],
                    "message": f"{names.get(employee_id, 'Personel')}: {run[0]:%d.%m}–{run[-1]:%d.%m} "
                               f"({len(run)} gün) art arda gece vardiyası.",
                })
        work_dates = sorted({start.date() for start, _, _, _ in items})
        runs = []
        for day in work_dates:
            if not runs or day != runs[-1][-1] + timedelta(days=1):
                runs.append([day])
            else:
                runs[-1].append(day)
        for run in runs:
            if len(run) >= long_streak and run[-1] >= month_start and run[0] <= month_end:
                findings.append({
                    "type": "long_streak", "severity": "warning", "employee_id": employee_id,
                    "name": names.get(employee_id, "Personel"),
                    "date": [item.isoformat() for item in run],
                    "message": f"{names.get(employee_id, 'Personel')}: {run[0]:%d.%m}–{run[-1]:%d.%m} "
                               f"({len(run)} gün) aralıksız çalışma.",
                })
        for previous, current in zip(items, items[1:]):
            rest = (current[0] - previous[1]).total_seconds() / 3600
            if minimum_rest_hours and 0 <= rest < minimum_rest_hours and current[0].strftime("%Y-%m") == month:
                findings.append({
                    "type": "short_transition", "severity": "warning", "employee_id": employee_id,
                    "name": names.get(employee_id, "Personel"),
                    "date": [previous[2], current[2]],
                    "message": f"{names.get(employee_id, 'Personel')}: {previous[1]:%d.%m %H:%M} çıkış "
                               f"ile {current[0]:%d.%m %H:%M} giriş arasında {rest:g} saat.",
                })
    return findings


def _snapshot_table_rows(connection: sqlite3.Connection, table_names: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    existing = {row["name"] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    result = {}
    for table in table_names:
        if table in existing:
            result[table] = sorted(
                (dict(row) for row in connection.execute(f"SELECT * FROM {table}")),
                key=_json,
            )
    return result


def current_snapshot(store, month: str) -> dict[str, Any]:
    month = scheduling.validate_month(month)
    periods = scheduling.adjacent_months(month)
    schedules = {
        period: store.schedule_month(period)
        for period in periods
    }
    attendance = store.attendance_month(month)
    people = store.people()
    profiles = store.employee_profiles()
    settings = [dict(row) for row in store.connection.execute(
        "SELECT name,payload FROM app_settings ORDER BY name"
    )]
    operations_data = _snapshot_table_rows(store.connection, (
        "operations_rules", "operations_availability", "operations_lifecycle_events",
        "linked_leave_requests", "linked_leave_applications",
    ))
    leave_requests = store.leave_requests()
    current_schedule = schedules[month]
    shared = {
        "people": people, "profiles": profiles, "settings": settings,
        "operations": operations_data, "leave_requests": leave_requests,
    }
    schedule_fingerprint = _fingerprint(schedules)
    attendance_fingerprint = _fingerprint({
        "attendance": attendance, "schedules": schedules, "shared": shared,
    })
    fingerprints = {
        "schedule": schedule_fingerprint,
        "attendance": attendance_fingerprint,
        "quality": _fingerprint({"schedules": schedules, "shared": shared}),
        "reports": attendance_fingerprint,
        "backup": _fingerprint({
            "schedule_months": _snapshot_table_rows(store.connection, ("schedule_months",)),
            "attendance_months": _snapshot_table_rows(store.connection, ("attendance_months",)),
            "shared": shared,
        }),
        "approval": attendance_fingerprint,
    }
    return {
        "month": month,
        "revisions": {"schedule": current_schedule["revision"], "attendance": attendance["revision"]},
        "schedule_fingerprint": schedule_fingerprint,
        "attendance_fingerprint": attendance_fingerprint,
        "fingerprints": fingerprints,
    }


def _hard_rule_signature(item: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(item.get(key) for key in (
        "type", "employee_id", "date", "rule_id", "section", "start", "end", "actual", "expected", "message",
    ))


def _configured_rule_findings(store, month: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = {int(person["id"]): person["name"] for person in store.people()}
    profiles = store.employee_profiles()
    findings = _custom_rule_findings(month, entries,
                                     [rule for rule in _rules(store.connection) if "special_day" not in rule],
                                     profiles)
    findings += _availability_findings(month, entries, store, names)
    return findings


def validate_schedule_in_transaction(
    store, month: str, entries: list[dict[str, Any]], before_entries: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Parent save hook: call only for configured error-severity operational rules."""
    month = scheduling.validate_month(month)
    findings = _configured_rule_findings(store, month, entries)
    previous = set()
    if before_entries is not None:
        previous = {
            _hard_rule_signature(item)
            for item in _configured_rule_findings(store, month, before_entries)
            if item["severity"] == "error" and not item.get("exception_reason")
        }
    hard = [
        item for item in findings
        if item["severity"] == "error" and not item.get("exception_reason")
        and _hard_rule_signature(item) not in previous
    ]
    if hard:
        raise ValueError("Yapılandırılmış operasyon kuralı: " + hard[0]["message"])
    return findings


def quality_report(store, month: str, entries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    month = scheduling.validate_month(month)
    entries = entries if entries is not None else store.schedule_month(month)["entries"]
    periods = scheduling.adjacent_months(month)
    all_entries = []
    for period in periods:
        all_entries.extend(entries if period == month else store.schedule_month(period)["entries"])
    people = store.people()
    names = {int(person["id"]): person["name"] for person in people}
    profiles = store.employee_profiles()
    settings = store.compliance_settings()
    requirements = store.planning_settings()["skill_requirements"]
    findings = planning.skill_items(month, entries, names, profiles, requirements)
    findings += planning.mentorship_items(month, all_entries, names, profiles)
    for skill in sorted({rule["skill"].casefold() for rule in requirements}):
        owners = [person for person in people if person["active"] and skill in {
            value.casefold() for value in profiles.get(str(person["id"]), {}).get("skills", [])
        }]
        if len(owners) == 1:
            owner = owners[0]
            label = next(rule["skill"] for rule in requirements if rule["skill"].casefold() == skill)
            findings.append({
                "severity": "warning", "type": "single_skill_holder", "skill": label,
                "name": owner["name"], "employee_id": owner["id"],
                "message": f"Güncel aktif personel kartlarında “{label}” yetkinliği yalnız bir kişide kayıtlı. "
                           "Kartları kontrol edin; ikinci bir çalışan yetiştirmeyi değerlendirin.",
            })
    findings += planning.preference_items(month, entries, names, profiles)
    findings += compliance.compliance_items(
        month, all_entries, names, profiles, settings,
        store.schedule_validation_settings().get("minimum_rest_hours", 0),
    )
    findings += compliance.staffing_items(
        month, all_entries, settings.get("min_staffing", {}),
        settings.get("min_staffing_windows", []),
    )
    findings += [
        {"severity": "error", **item} for item in controls.work_overlaps(entries, names)
    ]
    rules = _rules(store.connection)
    findings += _custom_rule_findings(month, entries, [rule for rule in rules if "special_day" not in rule], profiles)
    special_days = [rule for rule in rules if "special_day" in rule and rule["start_date"].startswith(month + "-")]
    for rule in special_days:
        day = date.fromisoformat(rule["start_date"])
        if rule["skill"]:
            staffing_entries = [row for row in all_entries if rule["skill"].casefold() in
                                {skill.casefold() for skill in profiles.get(str(row["employee_id"]), {}).get("skills", [])}]
        else:
            staffing_entries = all_entries
        staffing = compliance.hourly_staffing(day, staffing_entries, [rule])
        for row in staffing:
            for gap in row["gaps"]:
                findings.append({
                    "type": "staffing", "severity": "warning", "rule_id": rule["id"], "date": day.isoformat(),
                    "section": rule["section"], "actual": rule["minimum"] - gap["shortfall"], "expected": rule["minimum"],
                    "message": f"{rule['special_day']} · {rule['section']} · {gap['start']}–{gap['end']}: {gap['shortfall']} eksik.",
                })
    findings += _availability_findings(month, entries, store, names)
    findings += fatigue_findings(
        month, all_entries, names,
        minimum_rest_hours=settings.get("legal_rest_hours", 11),
    )
    leave = linked_leave.calendar_requests(store, month)
    findings += planning.leave_request_items(
        month, entries, names, store.leave_requests() + leave, store.planning_settings()["daily_limit"],
    )
    counts = {level: sum(item.get("severity") == level for item in findings) for level in ("error", "warning", "info")}
    first_day, last_day = scheduling.month_dates(month)[0].isoformat(), scheduling.month_dates(month)[-1].isoformat()
    pending_requests = store.connection.execute(
        "SELECT COUNT(*) FROM portal_requests WHERE status='pending' AND start_date<=? AND end_date>=?",
        (last_day, first_day),
    ).fetchone()[0]
    pending_leave = store.connection.execute(
        "SELECT COUNT(*) FROM linked_leave_requests WHERE status='pending' AND start_date<=? AND end_date>=?",
        (last_day, first_day),
    ).fetchone()[0]
    unsigned_documents = sum(
        any(str(day).startswith(month + "-") for day in json.loads(row["exact_dates"]))
        for row in store.connection.execute("SELECT exact_dates FROM workflow_documents WHERE status!='signed'")
    )
    pending_work = [
        {"label": "İncelenecek personel talebi", "count": pending_requests, "tool": "Personel talepleri"},
        {"label": "Bekleyen bağlantılı izin", "count": pending_leave, "tool": "Bağlantılı izinler"},
        {"label": "İmza takibi açık belge", "count": unsigned_documents, "tool": "Belge ve tutanak dosyası"},
        {"label": "Sicil/bordro kodu eksik etkin personel",
         "count": sum(person["active"] and not profiles.get(str(person["id"]), {}).get("payroll_code")
                      for person in people), "tool": "Personel kimlik incelemesi"},
    ]
    return {
        "month": month, "revision": store.schedule_month(month)["revision"],
        "counts": counts, "findings": findings,
        "leave_requests": leave,
        "pending_work": pending_work,
        "next_action": "Günlük plan tablosunu açıp bulguları inceleyin." if findings else "Yayın farkını kontrol edin.",
    }


def leave_conflicts(store, month: str) -> dict[str, Any]:
    month = scheduling.validate_month(month)
    entries = store.schedule_month(month)["entries"]
    people = {int(item["id"]): item["name"] for item in store.people()}
    requests = store.leave_requests()
    requests += linked_leave.calendar_requests(store, month)
    items = planning.leave_request_items(
        month, entries, people, requests, store.planning_settings()["daily_limit"],
    )
    return {"month": month, "findings": items, "count": len(items)}


def publish_diff(store, month: str) -> dict[str, Any]:
    month = scheduling.validate_month(month)
    current = store.schedule_month(month)
    published = store._setting("published_plans", {}).get(month)
    names = {int(person["id"]): person["name"] for person in store.people()}
    if not isinstance(published, dict) or not isinstance(published.get("entries"), list):
        published = None
    changes = _publication_changes(
        month, published["entries"] if published else [], current["entries"], names,
    )
    emergency_reasons: dict[tuple[int, str], list[dict[str, str]]] = {}
    for row in store.connection.execute(
        "SELECT id,employee_id,replacement_id,date,reason FROM operations_emergency_audit "
        "WHERE substr(date,1,7)=? ORDER BY created_at,id", (month,),
    ):
        for employee_id in (int(row["employee_id"]), int(row["replacement_id"])):
            emergency_reasons.setdefault((employee_id, row["date"]), []).append({
                "id": row["id"], "reason": row["reason"],
            })
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for change in changes:
        key = (change["date"], change["section"])
        group = groups.setdefault(key, {
            "date": change["date"], "section": change["section"],
            "changes": [], "recorded_reasons": [],
        })
        group["changes"].append({
            "employee_id": change["employee_id"], "name": change["name"],
            "type": change["type"], "before": change["before"], "after": change["after"],
            "before_hours": change["before_hours"], "after_hours": change["after_hours"],
        })
        for reason in emergency_reasons.get((change["employee_id"], change["date"]), []):
            if reason not in group["recorded_reasons"]:
                group["recorded_reasons"].append(reason)
    return {
        "month": month, "revision": current["revision"],
        "published_revision": (published or {}).get("source_revision", ""),
        "published_at": (published or {}).get("at", ""),
        "changes": changes,
        "change_groups": sorted(groups.values(), key=lambda item: (item["date"], item["section"].casefold())),
    }


def _publication_cells(month: str, entries: list[dict[str, Any]]) -> dict[tuple[int, str, str], dict[str, Any]]:
    result = {}
    allowed = {day.isoformat() for day in scheduling.month_dates(month)}
    for row in entries:
        employee_id = int(row["employee_id"])
        section = str(row.get("section") or "")
        for day_text, item in (row.get("days") or {}).items():
            if day_text not in allowed or not item or not item.get("kind"):
                continue
            interval = _minutes_interval(day_text, item)
            hours = round((interval[1] - interval[0]).total_seconds() / 3600, 2) if interval else 0.0
            result[(employee_id, section, day_text)] = {
                "label": controls.day_label(item), "kind": item.get("kind", ""),
                "hours": hours,
            }
    return result


def _publication_changes(month: str, before_entries: list[dict[str, Any]],
                         after_entries: list[dict[str, Any]], names: dict[int, str]) -> list[dict[str, Any]]:
    before = _publication_cells(month, before_entries)
    after = _publication_cells(month, after_entries)
    changes = []
    for employee_id, section, day in sorted(
        set(before) | set(after), key=lambda key: (key[2], names.get(key[0], "").casefold(), key[1].casefold()),
    ):
        old, new = before.get((employee_id, section, day)), after.get((employee_id, section, day))
        if old == new:
            continue
        changes.append({
            "employee_id": employee_id, "name": names.get(employee_id, "Personel"),
            "section": section, "date": day,
            "before": old["label"] if old else "", "after": new["label"] if new else "",
            "before_type": old["kind"] if old else "", "after_type": new["kind"] if new else "",
            "before_hours": old["hours"] if old else 0.0, "after_hours": new["hours"] if new else 0.0,
            "type": "added" if old is None else "removed" if new is None else "changed",
        })
    return changes


def publish_in_transaction(store, month: str, expected_revision: str, actor: str = "") -> dict[str, Any]:
    """Publish at the parent server's real hook after rechecking the submitted source revision."""
    if not isinstance(expected_revision, str) or not expected_revision.strip():
        raise ValueError("Yayın için geçerli kaynak revision gereklidir.")
    month = scheduling.validate_month(month)
    connection = store.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        store.assert_periods_unlocked([month])
        current = store.schedule_month(month)
        if current["revision"] != expected_revision:
            raise ValueError("Yayın önizlemesinden sonra vardiya değişti; farkı yenileyin.")
        if not any(
            item
            for entry in current["entries"]
            for item in (entry.get("days") or {}).values()
            if item
        ):
            raise ValueError("Boş vardiya planı yayınlanamaz.")
        plans = store._setting("published_plans", {})
        if not isinstance(plans, dict):
            plans = {}
        previous = plans.get(month)
        names = {int(person["id"]): person["name"] for person in store.people()}
        changes = _publication_changes(
            month, previous["entries"] if previous else [], current["entries"], names,
        )
        published = {
            "entries": current["entries"], "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "actor": actor or getattr(store, "actor", "") or store.user_name(),
            "source_revision": current["revision"], "changes": changes,
        }
        plans[month] = published
        if len(plans) > 24:
            retained = sorted(
                plans.items(),
                key=lambda item: (
                    str(item[1].get("at", "")) if isinstance(item[1], dict) else "",
                    item[0],
                ),
            )[-24:]
            plans = dict(retained)
        store._write_setting("published_plans", plans)
        access_services.notify_published_in_transaction(store, month, previous, published)
        store.record_change("schedule.publish", f"{month} vardiya planı yayınlandı", {
            "month": month, "source_revision": current["revision"], "changes": len(changes),
        })
        connection.commit()
        return {"month": month, "revision": current["revision"], "at": published["at"], "changes": changes}
    except Exception:
        connection.rollback()
        raise


def _shift_copy(item: dict[str, Any]) -> dict[str, Any]:
    return json.loads(_json(item))


class _CandidateStore:
    def __init__(self, base, replaced_month: str, entries: list[dict[str, Any]], target_date: str = ""):
        self._base, self._month, self._entries, self._target_date = base, replaced_month, entries, target_date

    def __getattr__(self, name):
        return getattr(self._base, name)

    def schedule_month(self, month):
        if month == self._month:
            return {"entries": self._entries, "revision": self._base.schedule_month(month)["revision"]}
        return self._base.schedule_month(month)

    def employee_profile(self, employee_id):
        profile = self._base.employee_profile(employee_id)
        if self._target_date:
            profile["home_section"] = effective_home_section(self._base, employee_id, self._target_date)
        return profile


def emergency_preview(store, payload: Any) -> dict[str, Any]:
    required = {"employee_id", "date", "reason"}
    allowed = required | {"status"}
    if not isinstance(payload, dict) or not required <= payload.keys() or not set(payload) <= allowed:
        raise ValueError("Acil devamsızlık önizleme alanları eksik.")
    employee_id = _strict_int(payload["employee_id"], "Personel seçimi", 1, _MAX_EMPLOYEE_ID)
    day = _date(payload["date"]).isoformat()
    reason = " ".join(str(payload["reason"] or "").split())
    status = payload.get("status", "absent")
    if not 3 <= len(reason) <= 240 or status not in ("absent", "sick_leave", "annual_leave", "unpaid_leave"):
        raise ValueError("Durum ve gerekçe seçin (gerekçe 3-240 karakter).")
    month = day[:7]
    current = store.schedule_month(month)
    selected_row = next(
        (row for row in current["entries"]
         if int(row.get("employee_id", -1)) == employee_id and (row.get("days") or {}).get(day, {}).get("kind") == "work"),
        None,
    )
    if selected_row is None:
        raise ValueError("Seçilen personel/tarih için çalışma vardiyası yok.")
    original = _shift_copy(selected_row["days"][day])
    remaining = _shift_copy(current["entries"])
    for row in remaining:
        if row.get("row_id") == selected_row.get("row_id"):
            row.setdefault("days", {}).pop(day, None)
    candidate_store = _CandidateStore(store, month, remaining, day)
    alternatives = linked_leave._replacement_candidates(candidate_store, {
        "employee_id": employee_id, "exclude_employee_id": employee_id,
        "date": day, "section": selected_row.get("section", ""),
        "start": original["start"], "end": original["end"],
    })
    begin, finish = scheduling._time_interval(day, original["start"], original["end"])
    present = []
    for row in current["entries"]:
        if row.get("section") != selected_row.get("section"):
            continue
        item = (row.get("days") or {}).get(day, {})
        interval = _minutes_interval(day, item)
        if interval and interval[0] < finish and interval[1] > begin:
            present.append((max(begin, interval[0]), min(finish, interval[1])))
    cursor, gaps = begin, []
    for left, right in sorted(present):
        if left > cursor:
            gaps.append({"start": cursor.strftime("%H:%M"), "end": left.strftime("%H:%M")})
        cursor = max(cursor, right)
    if cursor < finish:
        gaps.append({"start": cursor.strftime("%H:%M"), "end": finish.strftime("%H:%M")})
    return {
        "month": month, "revision": current["revision"], "employee_id": employee_id,
        "date": day, "section": selected_row.get("section", ""), "row_id": selected_row["row_id"],
        "original": original, "status": status, "reason": reason, "coverage_gaps": gaps,
        "alternatives": alternatives["candidates"],
        "warning": "Önizleme yalnızca öneridir. Uygulama için uygun yedeği açıkça seçin ve yeniden onaylayın.",
    }


def apply_emergency(store, payload: Any) -> dict[str, Any]:
    required = {"employee_id", "date", "reason", "status", "replacement_id", "revision", "confirm"}
    if not isinstance(payload, dict) or set(payload) != required or payload.get("confirm") is not True:
        raise ValueError("Acil değişiklik için açık onay ve bütün alanlar gereklidir.")
    if not isinstance(payload["revision"], str) or not payload["revision"]:
        raise ValueError("Önizleme revision bilgisi geçersiz.")
    preview = emergency_preview(store, {key: payload[key] for key in ("employee_id", "date", "reason", "status")})
    if preview["revision"] != payload["revision"]:
        raise ValueError("Önizlemeden sonra vardiya değişti; yeniden önizleyin.")
    replacement_id = _strict_int(payload["replacement_id"], "Yedek personel", 1, _MAX_EMPLOYEE_ID)
    month, day, employee_id = preview["month"], preview["date"], preview["employee_id"]
    connection = store.connection
    if hasattr(store, "_backup_before_change"):
        store._backup_before_change()
    connection.execute("BEGIN IMMEDIATE")
    try:
        store.assert_periods_unlocked([month])
        current = store.schedule_month(month)
        if current["revision"] != payload["revision"]:
            raise ValueError("Önizlemeden sonra vardiya değişti; değişiklik uygulanmadı.")
        fresh = emergency_preview(store, {key: payload[key] for key in ("employee_id", "date", "reason", "status")})
        if fresh["revision"] != payload["revision"]:
            raise ValueError("Önizlemeden sonra vardiya değişti; değişiklik uygulanmadı.")
        candidate = next(
            (item for item in fresh["alternatives"] if item["employee_id"] == replacement_id), None,
        )
        if not candidate or not candidate["eligible"]:
            raise ValueError("Seçilen yedek artık uygun değil; önizlemeyi yenileyin.")
        preview = fresh
        entries = _shift_copy(current["entries"])
        source = next(
            (row for row in entries if row.get("row_id") == preview["row_id"]
             and int(row.get("employee_id", -1)) == employee_id),
            None,
        )
        if source is None or (source.get("days") or {}).get(day) != preview["original"]:
            raise ValueError("Kaynak vardiya değişti; değişiklik uygulanmadı.")
        source["days"][day] = {"kind": preview["status"]}
        target = next(
            (row for row in entries if int(row.get("employee_id", -1)) == replacement_id
             and row.get("section") == preview["section"]),
            None,
        )
        if target is None:
            target = {
                "row_id": f"op-{secrets.token_hex(8)}", "employee_id": replacement_id,
                "section": preview["section"], "days": {},
            }
            entries.append(target)
        if (target.get("days") or {}).get(day):
            raise ValueError("Yedek personelin vardiya hücresi artık boş değil.")
        target.setdefault("days", {})[day] = _shift_copy(preview["original"])
        names = {int(person["id"]): person["name"] for person in store.people()}
        all_entries = []
        for period in scheduling.adjacent_months(month):
            all_entries += entries if period == month else store.schedule_month(period)["entries"]
        before_all = []
        for period in scheduling.adjacent_months(month):
            before_all += current["entries"] if period == month else store.schedule_month(period)["entries"]
        controls.assert_no_new_overlaps(before_all, all_entries, names)
        entries = scheduling.normalize_schedule_entries(month, entries, store.people())
        prepared = linked_leave.prepare_schedule_save(store, month, entries)
        entries = scheduling.normalize_schedule_entries(month, prepared["entries"], store.people())
        validate_schedule_in_transaction(store, month, entries, before_entries=current["entries"])
        all_entries = []
        for period in scheduling.adjacent_months(month):
            all_entries += entries if period == month else store.schedule_month(period)["entries"]
        controls.assert_no_new_overlaps(before_all, all_entries, names)
        # The absent employee's original assignment remains in the audit record and schedule archive.
        revision = secrets.token_hex(16)
        current_row = connection.execute(
            "SELECT payload,revision FROM schedule_months WHERE period=?", (month,),
        ).fetchone()
        if current_row and current_row["revision"] != payload["revision"]:
            raise ValueError("Vardiya kaydı eş zamanlı değişti; tekrar deneyin.")
        if current_row:
            connection.execute(
                "INSERT INTO archive_revisions(kind,period,payload,revision,recorded_at) VALUES ('schedule',?,?,?,?)",
                (month, current_row["payload"], current_row["revision"], datetime.now(timezone.utc).isoformat()),
            )
        connection.execute(
            "INSERT INTO schedule_months(period,payload,revision) VALUES (?,?,?) "
            "ON CONFLICT(period) DO UPDATE SET payload=excluded.payload,revision=excluded.revision",
            (month, _json(entries), revision),
        )
        linked_leave.record_applications_in_transaction(store, month, prepared["applications"])
        audit_id = secrets.token_hex(12)
        connection.execute(
            "INSERT INTO operations_emergency_audit"
            "(id,month,employee_id,replacement_id,date,reason,source_revision,before_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (audit_id, month, employee_id, replacement_id, day,
             f"{preview['status']}: {preview['reason']}", payload["revision"], _json(preview["original"]),
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        store.record_change("operations.emergency", f"{day} acil vardiya değişikliği uygulandı", {
            "employee_id": employee_id, "replacement_id": replacement_id, "date": day,
            "status": preview["status"], "reason": preview["reason"],
            "original_shift": preview["original"], "audit_id": audit_id,
        })
        connection.commit()
        return {"month": month, "revision": revision, "audit_id": audit_id, "status": preview["status"]}
    except Exception:
        connection.rollback()
        raise


def _finite_rate(value: Any, label: str, allow_zero: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} sayısal olmalıdır.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} sayısal olmalıdır.") from exc
    if not math.isfinite(number) or number < (0 if allow_zero else 0.000001) or number > 10_000_000:
        raise ValueError(f"{label} geçerli bir pozitif tutar olmalıdır.")
    return number


def payroll_delivery_status(handler, store, month):
    attendance, _, saved = handler._attendance_for(store, month)
    headers = list(reports.PAYROLL_HEADERS)
    current = {str(person["employee_id"]): dict(zip(headers, row))
               for person, row in zip(attendance["rows"], reports.payroll_rows(attendance))}
    fingerprint = _fingerprint(current)
    versions = list(store.connection.execute(
        "SELECT id,fingerprint,delivered_at,reference FROM operations_payroll_deliveries "
        "WHERE month=? ORDER BY id DESC", (month,),
    ))
    previous = {}
    if versions:
        record = store.connection.execute(
            "SELECT payload FROM operations_payroll_deliveries WHERE id=?", (versions[0]["id"],),
        ).fetchone()
        previous = json.loads(record["payload"])
    changes = []
    if versions:
        for employee_id in sorted(previous.keys() | current.keys(), key=int):
            before, after = previous.get(employee_id, {}), current.get(employee_id, {})
            if before != after:
                changes.append({"name": (after or before)[headers[1]],
                                "fields": [{"name": field, "before": before.get(field), "after": after.get(field)}
                                           for field in headers if before.get(field) != after.get(field)]})
    adjustments = []
    for row in store.connection.execute(
            "SELECT id,delivery_id,base_fingerprint,current_fingerprint,locked,reason,payload,status,"
            "requested_by,requested_at,decided_by,decided_at FROM operations_payroll_adjustments "
            "WHERE month=? ORDER BY requested_at DESC", (month,)):
        adjustment = dict(row)
        snapshot = json.loads(adjustment.pop("payload"))
        adjustment["changes"] = snapshot["changes"]
        adjustments.append(adjustment)
    return {"month": month, "fingerprint": fingerprint, "recorded": bool(saved["revision"]),
            "person_count": len(current), "versions": [dict(row) for row in versions],
            "duplicate": any(row["fingerprint"] == fingerprint for row in versions),
            "locked": month in store.locked_periods(), "changes": changes,
            "adjustments": adjustments}, current


def register_payroll_delivery(handler, store, payload):
    if not isinstance(payload, dict) or set(payload) != {"month", "fingerprint", "reference", "confirmed"}:
        raise ValueError("Bordro teslim bilgileri geçersiz.")
    month = scheduling.validate_month(payload["month"])
    reference = payload["reference"]
    if not isinstance(reference, str) or not 1 <= len(reference.strip()) <= 120 or payload["confirmed"] is not True:
        raise ValueError("Teslimi doğrulayın ve 1–120 karakterlik bir teslim açıklaması girin.")
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        status, rows = payroll_delivery_status(handler, store, month)
        if not status["recorded"] or not rows:
            raise ValueError("Teslim için personel içeren kayıtlı bir puantaj gerekir.")
        if payload["fingerprint"] != status["fingerprint"]:
            raise ValueError("Bordro verileri değişti; teslim görünümünü yenileyin.")
        if status["duplicate"]:
            raise ValueError("Bu bordro sürümü daha önce teslim edilmiş.")
        cursor = store.connection.execute(
            "INSERT INTO operations_payroll_deliveries(month,fingerprint,delivered_at,reference,payload) "
            "VALUES (?,?,?,?,?)", (month, status["fingerprint"], datetime.now(timezone.utc).isoformat(),
                                    reference.strip(), _json(rows)),
        )
        store.record_change("operations.payroll_delivery", "Bordro teslimi kaydedildi",
                            {"month": month, "delivery_id": cursor.lastrowid})
        store.connection.commit()
        return {"id": cursor.lastrowid, "month": month}
    except Exception:
        store.connection.rollback()
        raise


def overtime_budget(store, month, attendance, planned, reported):
    targets = store._setting(f"overtime_budget:{month}", {})
    profiles = store.employee_profiles()
    sections = set(scheduling.section_names())
    assignments = {}
    for row in planned:
        if any(shift["kind"] == "work" for shift in row.get("days", {}).values()):
            assignments.setdefault(row["employee_id"], set()).add(row["section"])
    totals = {section: [0.0, 0.0] for section in sections | targets.keys()}
    for row in attendance["rows"]:
        employee_id = row["employee_id"]
        home = profiles.get(str(employee_id), {}).get("home_section")
        worked = assignments.get(employee_id, set())
        section = home if home in sections else next(iter(worked)) if len(worked) == 1 else None
        amount = totals.setdefault(section, [0.0, 0.0])
        amount[0] += row["overtime_computed_hours"]
        amount[1] += row["overtime_hours"]
    return {"month": month, "reported": reported,
            "rows": [{"section": section, "target": targets.get(section),
                      "forecast": round(totals[section][0], 2),
                      "actual": round(totals[section][1], 2) if reported else None}
                     for section in sorted(totals, key=lambda section: (section is None, section or ""))]}


def save_overtime_budget(store, payload):
    if not isinstance(payload, dict) or set(payload) != {"month", "section", "target"}:
        raise ValueError("Mesai bütçesi bilgileri geçersiz.")
    month = scheduling.validate_month(payload["month"])
    section = payload["section"]
    if not isinstance(section, str) or section not in scheduling.section_names():
        raise ValueError("Bütçe için geçerli bir bölüm seçin.")
    target = payload["target"]
    if target is not None and (type(target) not in (int, float) or not 0 <= target <= 10000):
        raise ValueError("Mesai hedefi 0–10.000 saat arasında olmalıdır.")
    key = f"overtime_budget:{month}"
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        targets = store._setting(key, {})
        if target is None:
            targets.pop(section, None)
        else:
            targets[section] = round(target, 2)
        store._write_setting(key, targets)
        store.record_change("operations.overtime_budget", "Bölüm mesai hedefi güncellendi",
                            {"month": month, "section": section, "target": targets.get(section)})
        store.connection.commit()
    except Exception:
        store.connection.rollback()
        raise
    return {"month": month, "section": section, "target": targets.get(section)}


def cost_simulation(attendance: dict[str, Any], payload: Any) -> dict[str, Any]:
    """Explicit user rates only. This estimate is not payroll or a legal calculation."""
    if not isinstance(payload, dict) or not isinstance(payload.get("rates"), dict):
        raise ValueError("Maliyet simülasyonu için kullanıcı tarafından girilmiş oranlar gereklidir.")
    hours_per_month = _finite_rate(payload.get("hours_per_month"), "Aylık saat")
    if not 150 <= hours_per_month <= 240:
        raise ValueError("Aylık saat 150-240 arasında olmalıdır.")
    overtime_multiplier = _finite_rate(payload.get("overtime_multiplier"), "Fazla mesai çarpanı")
    holiday_multiplier = _finite_rate(payload.get("holiday_multiplier"), "Tatil çarpanı")
    if overtime_multiplier > 5 or holiday_multiplier > 5:
        raise ValueError("Çarpanlar 5'i aşamaz.")
    rates = payload["rates"]
    rows = []
    missing = []
    for row in attendance["rows"]:
        if not any(row.get("codes", {}).values()):
            continue
        rate = rates.get(str(row["employee_id"]), rates.get(row["employee_id"]))
        if rate is None:
            missing.append(row["name"])
            continue
        if not isinstance(rate, dict) or set(rate) - {"basis", "amount"} or rate.get("basis") not in ("monthly", "hourly"):
            raise ValueError(f"{row['name']} ücret bilgisi aylık veya saatlik tutar olmalıdır.")
        amount = _finite_rate(rate.get("amount"), f"{row['name']} ücreti")
        hourly = amount / hours_per_month if rate["basis"] == "monthly" else amount
        worked = float(row.get("worked_hours") or 0)
        overtime = float(row.get("overtime_hours") or 0)
        holiday = float(row.get("ubgt_hours") or 0)
        parts = {
            "base": hourly * max(0, worked - overtime - holiday),
            "overtime": hourly * overtime * overtime_multiplier,
            "holiday": hourly * holiday * holiday_multiplier,
        }
        rows.append({
            "employee_id": int(row["employee_id"]), "name": row["name"], "basis": rate["basis"],
            "entered_rate": amount, "hourly_rate": round(hourly, 2),
            "worked_hours": round(worked, 2), "overtime_hours": round(overtime, 2),
            "holiday_hours": round(holiday, 2), **{key: round(value, 2) for key, value in parts.items()},
            "total": round(sum(parts.values()), 2),
        })
    totals = {key: round(sum(row[key] for row in rows), 2) for key in ("base", "overtime", "holiday", "total")}
    return {
        "month": attendance.get("month", ""), "estimate_only": True, "payroll": False,
        "notice": "Yalnızca bu ekranda girilen oranlarla kaba tahmindir; bordro, vergi veya hukuk hesabı değildir.",
        "hours_per_month": hours_per_month, "overtime_multiplier": overtime_multiplier,
        "holiday_multiplier": holiday_multiplier, "missing_rates": missing, "people": rows, "totals": totals,
    }


def staffing_patterns(store, months: list[str]) -> dict[str, Any]:
    checked_months = [scheduling.validate_month(period) for period in months]
    settings = store.compliance_settings()
    minimums = settings.get("min_staffing", {})
    windows = settings.get("min_staffing_windows", [])
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    source_revisions: dict[str, str] = {}
    for period in checked_months:
        current = store.schedule_month(period)
        previous = store.schedule_month(analytics.shift_month(period, -1))
        source_revisions[period] = current["revision"]
        findings = compliance.staffing_items(
            period, previous["entries"] + current["entries"], minimums, windows,
        )
        for finding in findings:
            day = date.fromisoformat(finding["date"])
            hourly = re.search(
                r": (?P<start>\d{2}:\d{2})–(?P<end>\d{2}:\d{2}) aralığında "
                r"en az (?P<required>\d+) çalışan gerekir; en düşük sayı (?P<count>\d+)$",
                finding["message"],
            )
            daily = re.search(
                r": \d{2}\.\d{2} günü (?P<count>\d+) çalışan; asgari kadro (?P<required>\d+)$",
                finding["message"],
            )
            match = hourly or daily
            if match is None:
                raise RuntimeError("Kadro bulgusu beklenen kayıt biçiminde değil.")
            kind = "hourly" if hourly else "daily"
            start = match.groupdict().get("start", "00:00")
            end = match.groupdict().get("end", "24:00")
            required = int(match["required"])
            key = (kind, finding["name"], day.weekday(), start, end, required)
            group = groups.setdefault(key, {
                "kind": kind, "section": finding["name"],
                "weekday": planning.WEEKDAYS[day.weekday()],
                "start": start, "end": end, "required": required,
                "occurrences": [], "weeks": set(), "periods": set(),
            })
            group["occurrences"].append({
                "date": day.isoformat(), "lowest": int(match["count"]),
            })
            group["weeks"].add((day - timedelta(days=day.weekday())).isoformat())
            group["periods"].add(period)

    patterns = []
    for group in groups.values():
        occurrences = sorted(group["occurrences"], key=lambda item: item["date"])
        periods = sorted(group["periods"])
        patterns.append({
            "kind": group["kind"], "section": group["section"],
            "weekday": group["weekday"], "start": group["start"], "end": group["end"],
            "required": group["required"], "occurrence_count": len(occurrences),
            "week_count": len(group["weeks"]), "pattern": "repeated" if len(group["weeks"]) >= 2 else "single_week",
            "dates": [item["date"] for item in occurrences],
            "lowest_observed": min(item["lowest"] for item in occurrences),
            "periods": periods,
            "source_revisions": [
                {"month": period, "revision": source_revisions[period]} for period in periods
            ],
        })
    patterns.sort(key=lambda item: (
        item["pattern"] != "repeated", item["weekday"], item["section"].casefold(),
        item["start"], item["end"], item["kind"],
    ))
    return {
        "checked_months": checked_months,
        "has_thresholds": bool(minimums or windows),
        "source_basis": "Kayıtlı asgari kadro hedefleri ve ilgili çizelge revizyonları",
        "patterns": patterns,
    }


def trends(handler, store, month: str, months: int = 6) -> dict[str, Any]:
    month = scheduling.validate_month(month)
    months = _strict_int(months, "Ay sayısı", 1, 12)
    periods = [analytics.shift_month(month, -delta) for delta in range(months - 1, -1, -1)]
    output = []
    for period in periods:
        attendance, entries = handler._month_attendance(store, period)
        overtime = payroll.overtime_breakdown(period, attendance)
        output.append({
            "month": period, "metrics": analytics.month_metrics(attendance, overtime),
            "source": current_snapshot(store, period),
            "drilldown": [
                {"employee_id": int(row["employee_id"]), "name": row["name"],
                 "codes": row.get("codes", {}), "worked_hours": row.get("worked_hours", 0),
                 "overtime_hours": row.get("overtime_hours", 0), "ubgt_hours": row.get("ubgt_hours", 0)}
                for row in attendance.get("rows", [])
            ],
        })
    return {
        "month": month, "months": output,
        "staffing_patterns": staffing_patterns(store, periods),
    }


def _step_fingerprint(snapshot: dict[str, Any], step: str) -> str:
    return snapshot["fingerprints"][step]


def closing_checklist(store, month: str) -> dict[str, Any]:
    month = scheduling.validate_month(month)
    snapshot = current_snapshot(store, month)
    rows = {
        row["step"]: dict(row) for row in store.connection.execute(
            "SELECT step,name,checked_at,fingerprint FROM operations_closings WHERE month=?", (month,)
        )
    }
    steps = []
    for key, label, _source in _CLOSE_STEPS:
        saved = rows.get(key)
        current_fingerprint = _step_fingerprint(snapshot, key)
        valid = bool(saved and saved["fingerprint"] == current_fingerprint)
        steps.append({
            "step": key, "label": label, "done": valid,
            "stale": bool(saved and not valid),
            "name": saved["name"] if saved else "",
            "checked_at": saved["checked_at"] if saved else "",
            "fingerprint": current_fingerprint,
            "next_action": {
                "quality": "/api/operations/quality", "attendance": "/api/operations/trends",
                "reports": "/api/reports/check", "backup": "/api/backups", "approval": "/api/approvals",
            }[key],
        })
    next_step = next((item["step"] for item in steps if not item["done"]), "")
    return {"month": month, "steps": steps, "next": next_step, "complete": not next_step, "source": snapshot}


def mark_closing_step(store, month: str, step: str, name: Any, expected_fingerprint: str) -> dict[str, Any]:
    month = scheduling.validate_month(month)
    if step not in {item[0] for item in _CLOSE_STEPS}:
        raise ValueError("Kapanış kontrol adımı geçersiz.")
    name = " ".join(str(name or "").split())
    if not 2 <= len(name) <= 60:
        raise ValueError("Sorumlu kişinin adını girin (2-60 karakter).")
    connection = store.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        current = closing_checklist(store, month)
        snapshot = current["source"]
        fingerprint = _step_fingerprint(snapshot, step)
        if fingerprint != expected_fingerprint:
            raise ValueError("Kaynak kayıt değişti; kontrol adımını yenileyip tekrar işaretleyin.")
        if current["next"] != step and not next((item for item in current["steps"] if item["step"] == step), {}).get("done"):
            raise ValueError("Kapanış adımları sırayla tamamlanmalıdır.")
        connection.execute(
            "INSERT INTO operations_closings(month,step,name,checked_at,fingerprint) VALUES (?,?,?,?,?) "
            "ON CONFLICT(month,step) DO UPDATE SET name=excluded.name,checked_at=excluded.checked_at,"
            "fingerprint=excluded.fingerprint",
            (month, step, name, datetime.now(timezone.utc).isoformat(timespec="seconds"), fingerprint),
        )
        store.record_change("operations.close_step", f"{month} kapanış adımı tamamlandı: {step}",
                            {"month": month, "step": step, "name": name, "fingerprint": fingerprint})
        result = closing_checklist(store, month)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise


def register_payroll_adjustment(handler, store, payload):
    required = {"month", "fingerprint", "delivery_id", "reason"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("Bordro düzeltme fişi bilgileri geçersiz.")
    month = scheduling.validate_month(payload["month"])
    delivery_id = payload["delivery_id"]
    reason = payload["reason"]
    if (not isinstance(delivery_id, int) or isinstance(delivery_id, bool) or delivery_id < 1
            or not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 500):
        raise ValueError("Düzeltme fişi için teslim ve 3–500 karakterlik gerekçe gereklidir.")
    if month not in store.locked_periods():
        raise ValueError("Düzeltme fişi yalnızca kilitli dönem için oluşturulabilir.")
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        status, current = payroll_delivery_status(handler, store, month)
        versions = status["versions"]
        if not versions or versions[0]["id"] != delivery_id:
            raise ValueError("İlk/son teslim sürümü değişti; düzeltme fişini yenileyin.")
        if status["fingerprint"] != payload["fingerprint"]:
            raise ValueError("Bordro verileri değişti; düzeltme farkını yenileyin.")
        if status["duplicate"] or not status["changes"]:
            raise ValueError("Kaydedilmiş teslimden farklı güncel bir bordro yok.")
        existing = store.connection.execute(
            "SELECT status FROM operations_payroll_adjustments "
            "WHERE month=? AND delivery_id=? AND current_fingerprint=? "
            "AND status IN ('pending','approved')",
            (month, delivery_id, status["fingerprint"]),
        ).fetchone()
        if existing:
            raise ValueError("Bu bordro farkı için bekleyen veya onaylanmış bir fiş zaten var.")
        delivery = store.connection.execute(
            "SELECT fingerprint FROM operations_payroll_deliveries WHERE id=? AND month=?",
            (delivery_id, month),
        ).fetchone()
        if delivery is None:
            raise ValueError("İlk bordro teslimi bulunamadı.")
        identifier = secrets.token_hex(16)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        snapshot = {"changes": status["changes"], "current": current}
        store.connection.execute(
            "INSERT INTO operations_payroll_adjustments "
            "(id,month,delivery_id,base_fingerprint,current_fingerprint,locked,reason,payload,status,"
            "requested_by,requested_at,decided_by,decided_at) "
            "VALUES (?,?,?,?,?,?,?,?,'pending',?,?,NULL,NULL)",
            (identifier, month, delivery_id, delivery["fingerprint"], status["fingerprint"], 1,
             reason.strip(), _json(snapshot), store.actor or store.user_name(), now),
        )
        store.record_change("operations.payroll_adjustment_request", "Kilitli dönem bordro düzeltme fişi oluşturuldu",
                            {"month": month, "id": identifier, "delivery_id": delivery_id})
        store.connection.commit()
        return {"id": identifier, "month": month, "delivery_id": delivery_id}
    except Exception:
        store.connection.rollback()
        raise


def review_payroll_adjustment(handler, store, payload):
    if (not isinstance(payload, dict) or set(payload) != {"month", "id", "approve"}
            or not isinstance(payload.get("id"), str)
            or not isinstance(payload.get("approve"), bool)):
        raise ValueError("Bordro düzeltme onayı geçersiz.")
    month = scheduling.validate_month(payload["month"])
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        adjustment = store.connection.execute(
            "SELECT * FROM operations_payroll_adjustments "
            "WHERE id=? AND month=? AND status='pending'", (payload["id"], month),
        ).fetchone()
        if adjustment is None:
            raise ValueError("Bekleyen bordro düzeltme fişi bulunamadı.")
        if payload["approve"]:
            status, _ = payroll_delivery_status(handler, store, month)
            if (not status["versions"] or status["versions"][0]["id"] != adjustment["delivery_id"]
                    or status["fingerprint"] != adjustment["current_fingerprint"]):
                raise ValueError("Teslim veya güncel bordro değişti; fiş eski kaynakla onaylanamaz.")
        decision = "approved" if payload["approve"] else "rejected"
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.connection.execute(
            "UPDATE operations_payroll_adjustments SET status=?,decided_by=?,decided_at=? WHERE id=?",
            (decision, store.actor or store.user_name(), now, payload["id"]),
        )
        store.record_change(f"operations.payroll_adjustment_{decision}",
                            "Bordro düzeltme fişi " + ("onaylandı" if payload["approve"] else "reddedildi"),
                            {"month": month, "id": payload["id"]})
        store.connection.commit()
        return {"id": payload["id"], "month": month, "status": decision}
    except Exception:
        store.connection.rollback()
        raise


def effective_home_section(store, employee_id: int, on_date: str) -> str:
    employee_id = _strict_int(employee_id, "Personel seçimi", 1, _MAX_EMPLOYEE_ID)
    day_text = _date(on_date).isoformat()
    profile = store.employee_profile(employee_id)
    if not store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operations_lifecycle_events'"
    ).fetchone():
        return str(profile.get("home_section") or "")
    events = list(store.connection.execute(
        "SELECT effective_date,from_section,section FROM operations_lifecycle_events "
        "WHERE employee_id=? AND action='transfer' ORDER BY effective_date,id",
        (employee_id,),
    ))
    section = str(events[0]["from_section"] or profile.get("home_section") or "") if events else str(
        profile.get("home_section") or ""
    )
    for event in events:
        if event["effective_date"] > day_text:
            break
        section = event["section"]
    return section


def _lifecycle_payload(payload: Any) -> tuple[int, str, str, str, list[str]]:
    if not isinstance(payload, dict):
        raise ValueError("Personel yaşam döngüsü önizleme bilgisi geçersiz.")
    employee_id = _strict_int(payload.get("employee_id"), "Personel seçimi", 1, _MAX_EMPLOYEE_ID)
    action = payload.get("action")
    if action not in ("hire", "exit", "transfer", "fixed_pattern"):
        raise ValueError("İşlem işe giriş, ayrılış, bölüm veya sabit düzen değişikliği olmalıdır.")
    extra = {"section"} if action == "transfer" else {"pattern"} if action == "fixed_pattern" else set()
    expected = {"employee_id", "action", "effective_date"} | extra
    if set(payload) != expected:
        raise ValueError("Geçerlilik tarihi ve gerekiyorsa bölüm veya haftalık düzen belirtilmelidir.")
    effective_date = _date(payload["effective_date"], "Geçerlilik tarihi").isoformat()
    section = ""
    pattern = []
    if action == "transfer":
        section = payload["section"]
        if not isinstance(section, str) or section not in scheduling.section_names():
            raise ValueError("Yeni bölüm seçimi geçersiz.")
    elif action == "fixed_pattern":
        pattern = payload["pattern"]
        if (not isinstance(pattern, list) or len(pattern) != 7
                or any(code not in scheduling.FIXED_PATTERN_CODES for code in pattern)):
            raise ValueError("Sabit düzen 7 gün için X, H veya boş olmalıdır.")
        if effective_date <= date.today().isoformat():
            raise ValueError("Sabit düzen değişikliği ileri bir tarihte yürürlüğe girmelidir.")
    return employee_id, action, effective_date, section, pattern


def _fixed_pattern_impacts(store, person: dict[str, Any], profile: dict[str, Any],
                           effective_date: str, pattern: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    employee_id = int(person["id"])
    current_person = {
        "id": employee_id, "name": person["name"], "active": person.get("active", True),
        "staff_type": "nonshift",
        "fixed_pattern": list(profile.get("fixed_pattern") or scheduling.DEFAULT_FIXED_PATTERN),
        "fixed_pattern_exceptions": list(profile.get("fixed_pattern_exceptions") or []),
        "fixed_pattern_changes": list(profile.get("fixed_pattern_changes") or []),
        "fixed_start": profile.get("fixed_start") or "08:00",
        "fixed_end": profile.get("fixed_end") or "16:00",
    }
    proposed_person = dict(current_person)
    proposed_person["fixed_pattern_changes"] = [
        *current_person["fixed_pattern_changes"],
        {"effective_date": effective_date, "pattern": list(pattern)},
    ]
    impacts, revisions = [], []
    periods = [row["period"] for row in store.connection.execute(
        "SELECT period FROM schedule_months ORDER BY period"
    )]
    for period in periods:
        dates = scheduling.month_dates(period)
        if dates[-1].isoformat() < effective_date:
            continue
        schedule = store.schedule_month(period)
        month_data = store.attendance_month(period)
        revisions.append({
            "period": period, "schedule": schedule["revision"],
            "attendance": month_data["revision"],
        })
        current = scheduling.calculate_attendance(
            period, schedule["entries"], month_data["overrides"], [current_person],
            month_data["deductions"], store.overtime_settings(period), month_data["overtime"],
        )
        proposed = scheduling.calculate_attendance(
            period, schedule["entries"], month_data["overrides"], [proposed_person],
            month_data["deductions"], store.overtime_settings(period), month_data["overtime"],
        )
        before = current["rows"][0]["codes"]
        after = proposed["rows"][0]["codes"]
        for day in dates:
            day_text = day.isoformat()
            if day_text < effective_date or before.get(day_text, "") == after.get(day_text, ""):
                continue
            impacts.append({
                "date": day_text, "old_code": before.get(day_text, ""),
                "new_code": after.get(day_text, ""),
                "source": proposed["rows"][0]["sources"].get(day_text, ""),
            })
    return impacts, revisions


def lifecycle_preview(store, payload: Any) -> dict[str, Any]:
    employee_id, action, effective_date, section, pattern = _lifecycle_payload(payload)
    people = {int(item["id"]): item for item in store.people()}
    person = people.get(employee_id)
    if person is None:
        raise ValueError("Personel bulunamadı.")
    profile = store.employee_profile(employee_id)
    if action == "transfer":
        previous_section = effective_home_section(
            store, employee_id, (date.fromisoformat(effective_date) - timedelta(days=1)).isoformat(),
        )
        changes = {
            "effective_date": effective_date, "from_section": previous_section, "section": section,
        }
    elif action == "fixed_pattern":
        if profile.get("staff_type") != "nonshift":
            raise ValueError("Sabit haftalık düzen yalnız vardiya dışı personelde değiştirilebilir.")
        if any(item.get("effective_date") == effective_date
               for item in profile.get("fixed_pattern_changes", []) if isinstance(item, dict)):
            raise ValueError("Bu tarihte zaten bir sabit düzen değişikliği var.")
        changes = {
            "effective_date": effective_date,
            "old_pattern": list(scheduling.fixed_pattern_for_date(profile, date.fromisoformat(effective_date))),
            "pattern": list(pattern),
        }
    elif action == "hire":
        changes = {"hire_date": effective_date}
    else:
        changes = {"exit_date": effective_date}
    revision = _fingerprint({
        "employee": person, "profile": profile,
        "schedule_revisions": {
            row["period"]: row["revision"]
            for row in store.connection.execute("SELECT period,revision FROM schedule_months")
        },
        "lifecycle_events": _snapshot_table_rows(store.connection, ("operations_lifecycle_events",)),
    })
    future_work = []
    attendance_revisions = []
    if action == "fixed_pattern":
        future_work, attendance_revisions = _fixed_pattern_impacts(
            store, person, profile, effective_date, pattern,
        )
    revision = _fingerprint({
        "lifecycle": revision, "attendance_revisions": attendance_revisions,
    })
    if action in ("exit", "transfer"):
        cutover = date.fromisoformat(effective_date)
        for row in store.connection.execute("SELECT period,payload FROM schedule_months"):
            for entry in json.loads(row["payload"]):
                if int(entry.get("employee_id", -1)) != employee_id:
                    continue
                for day_text, item in (entry.get("days") or {}).items():
                    if item.get("kind") == "work" and date.fromisoformat(day_text) >= cutover:
                        future_work.append({
                            "date": day_text, "section": entry.get("section", ""),
                            "home_section": effective_home_section(store, employee_id, day_text),
                            "proposed_home_section": section if action == "transfer" else
                                effective_home_section(store, employee_id, day_text),
                            "start": item.get("start"), "end": item.get("end"),
                        })
    offboarding_checklist = None
    if action == "exit":
        tables = {row["name"] for row in store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        open_assets = []
        if "employee_assets" in tables:
            open_assets = [
                {"item": row["item"], "category": row["category"], "issued_at": row["issued_at"]}
                for row in store.connection.execute(
                    "SELECT item,category,issued_at FROM employee_assets "
                    "WHERE employee_id=? AND returned_at IS NULL ORDER BY id",
                    (employee_id,),
                )
            ]
        pending_documents = []
        if "workflow_documents" in tables:
            pending_documents = [
                {"filename": row["filename"], "status": row["status"], "created_at": row["created_at"]}
                for row in store.connection.execute(
                    "SELECT filename,status,created_at FROM workflow_documents "
                    "WHERE employee_id=? AND status!='signed' ORDER BY created_at,id",
                    (employee_id,),
                )
            ]
        active_tokens = []
        if "portal_tokens" in tables:
            now_epoch = int(datetime.now(timezone.utc).timestamp())
            active_tokens = [
                {"expires_at": int(row["expires_at"])}
                for row in store.connection.execute(
                    "SELECT expires_at FROM portal_tokens WHERE employee_id=? "
                    "AND (revoked_at IS NULL OR revoked_at='') AND expires_at>?",
                    (employee_id, now_epoch),
                )
            ]
        active_shares = []
        if "workflow_shares" in tables:
            now_iso = datetime.now(timezone.utc).isoformat()
            active_shares = [
                {"month": row["month"], "expires_at": row["expires_at"]}
                for row in store.connection.execute(
                    "SELECT month,expires_at FROM workflow_shares WHERE employee_id=? "
                    "AND revoked_at='' AND expires_at>?",
                    (employee_id, now_iso),
                )
            ]
        offboarding_checklist = {
            "open_assets": open_assets,
            "pending_documents": pending_documents,
            "active_portal_tokens": active_tokens,
            "active_workflow_shares": active_shares,
        }
    return {
        "employee_id": employee_id, "name": person["name"], "action": action,
        "effective_date": effective_date, "changes": changes, "revision": revision,
        "future_work": future_work,
        "offboarding_checklist": offboarding_checklist,
        "warning": "Değişiklik geçerlilik tarihinde yürürlüğe girer; geçmiş vardiyalar yeniden yazılmaz.",
    }


def _revoke_employee_access(connection: sqlite3.Connection, employee_id: int) -> None:
    tables = {row["name"] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    now_text = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if "workflow_shares" in tables:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(workflow_shares)")}
        if {"employee_id", "revoked_at"} <= columns:
            connection.execute(
                "UPDATE workflow_shares SET revoked_at=? WHERE employee_id=? "
                "AND (revoked_at='' OR revoked_at IS NULL)",
                (now_text, employee_id),
            )
    if "portal_tokens" in tables:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(portal_tokens)")}
        if {"employee_id", "revoked_at"} <= columns:
            connection.execute(
                "UPDATE portal_tokens SET revoked_at=? WHERE employee_id=? AND revoked_at IS NULL",
                (int(datetime.now(timezone.utc).timestamp()), employee_id),
            )


def process_due_lifecycle(store, today: str | date | None = None) -> list[dict[str, Any]]:
    """Parent startup hook: apply dated hire/exit state and access revocation once due."""
    effective_day = _date(today or date.today()).isoformat()
    connection = store.connection
    if not connection.execute(
        "SELECT 1 FROM operations_lifecycle_events WHERE applied_at='' AND effective_date<=? LIMIT 1",
        (effective_day,),
    ).fetchone():
        return []
    connection.execute("BEGIN IMMEDIATE")
    try:
        events = list(connection.execute(
            "SELECT id,employee_id,action,effective_date,pattern FROM operations_lifecycle_events "
            "WHERE applied_at='' AND effective_date<=? "
            "ORDER BY effective_date,CASE action WHEN 'hire' THEN 0 WHEN 'transfer' THEN 1 ELSE 2 END,id",
            (effective_day,),
        ))
        applied = []
        now_text = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for event in events:
            employee_id = int(event["employee_id"])
            if event["action"] == "hire":
                connection.execute("UPDATE employees SET active=1 WHERE id=?", (employee_id,))
            elif event["action"] == "exit":
                connection.execute("UPDATE employees SET active=0 WHERE id=?", (employee_id,))
                _revoke_employee_access(connection, employee_id)
            connection.execute(
                "UPDATE operations_lifecycle_events SET applied_at=? WHERE id=? AND applied_at=''",
                (now_text, event["id"]),
            )
            store.record_change("operations.lifecycle.effective",
                                f"{event['effective_date']} personel işlemi yürürlüğe girdi",
                                {"employee_id": employee_id, "action": event["action"],
                                 "effective_date": event["effective_date"],
                                 "pattern": json.loads(event["pattern"]) if event["pattern"] else []})
            applied.append({"employee_id": employee_id, "action": event["action"],
                            "effective_date": event["effective_date"]})
        connection.commit()
        return applied
    except Exception:
        connection.rollback()
        raise


def lifecycle_pending(store) -> list[dict[str, Any]]:
    names = {int(person["id"]): person["name"] for person in store.people()}
    return [
        {"id": row["id"], "employee_id": row["employee_id"],
         "name": names.get(row["employee_id"], "Personel"), "action": row["action"],
         "effective_date": row["effective_date"], "from_section": row["from_section"],
         "section": row["section"],
         "pattern": json.loads(row["pattern"]) if row["pattern"] else [],
         "created_at": row["created_at"]}
        for row in store.connection.execute(
            "SELECT id,employee_id,action,effective_date,from_section,section,pattern,created_at "
            "FROM operations_lifecycle_events WHERE applied_at='' ORDER BY effective_date,id"
        )
    ]


def apply_lifecycle(store, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("confirm") is not True \
            or not isinstance(payload.get("revision"), str) or not payload["revision"]:
        raise ValueError("Personel yaşam döngüsü için açık onay ve revision gereklidir.")
    action_payload = {key: value for key, value in payload.items() if key not in ("revision", "confirm")}
    preview = lifecycle_preview(store, action_payload)
    if preview["revision"] != payload["revision"]:
        raise ValueError("Personel veya vardiya kayıtları değişti; önizlemeyi yenileyin.")
    store.assert_periods_unlocked(sorted({item["date"][:7] for item in preview["future_work"]}))
    store._backup_before_change()
    connection = store.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        latest = lifecycle_preview(store, action_payload)
        if latest["revision"] != payload["revision"]:
            raise ValueError("Personel veya vardiya kayıtları değişti; işlem uygulanmadı.")
        store.assert_periods_unlocked(sorted({item["date"][:7] for item in latest["future_work"]}))
        employee_id = preview["employee_id"]
        action = preview["action"]
        effective_date = preview["effective_date"]
        profile = store.employee_profiles().get(str(employee_id), {}).copy()
        if action == "hire" and effective_date <= date.today().isoformat():
            connection.execute("UPDATE employees SET active=1 WHERE id=?", (employee_id,))
        if action == "hire":
            profile["hire_date"] = effective_date
            old_exit = str(profile.get("exit_date") or "")
            if old_exit and old_exit <= effective_date:
                profile["exit_date"] = ""
        elif action == "exit":
            profile["exit_date"] = effective_date
            if effective_date <= date.today().isoformat():
                connection.execute("UPDATE employees SET active=0 WHERE id=?", (employee_id,))
                _revoke_employee_access(connection, employee_id)
        elif action == "fixed_pattern":
            changes = list(profile.get("fixed_pattern_changes") or [])
            changes.append({
                "effective_date": effective_date, "pattern": list(preview["changes"]["pattern"]),
            })
            profile["fixed_pattern_changes"] = changes
        profiles = store.employee_profiles()
        profiles[str(employee_id)] = profile
        store._write_setting("employee_profiles", profiles)
        transition = preview["changes"]
        event_id = secrets.token_hex(12)
        now_text = datetime.now(timezone.utc).isoformat(timespec="seconds")
        applied_at = now_text if effective_date <= date.today().isoformat() else ""
        connection.execute(
            "INSERT INTO operations_lifecycle_events"
            "(id,employee_id,action,effective_date,from_section,section,pattern,source_revision,created_at,applied_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (event_id, employee_id, action, effective_date, transition.get("from_section", ""),
             transition.get("section", ""), _json(transition["pattern"]) if action == "fixed_pattern" else "",
             preview["revision"], now_text, applied_at),
        )
        store.record_change("operations.lifecycle", f"{preview['name']} yaşam döngüsü güncellendi", {
            "employee_id": employee_id, "action": action, "effective_date": effective_date,
            "future_work_count": len(preview["future_work"]),
        })
        connection.commit()
        return {
            "employee_id": employee_id, "action": action, "effective_date": effective_date,
            "future_work": preview["future_work"], "applied": True,
            "pending": not bool(applied_at), "event_id": event_id,
        }
    except Exception:
        connection.rollback()
        raise


def branch_comparison(data_path: str | Path, branch_id: str, month: str) -> dict[str, Any]:
    """Read configured branch files via SQLite read-only mode; never instantiate Store."""
    month = scheduling.validate_month(month)
    current = Path(data_path).resolve()
    root = workspace_tools.root_for(current)
    branches = workspace_tools.load_branches(root)
    if not isinstance(branch_id, str) or branch_id not in {item["id"] for item in branches["branches"]}:
        raise ValueError("Yalnızca yapılandırılmış şubeler karşılaştırılabilir.")
    target = workspace_tools.branch_file(root, branch_id).resolve()
    allowed_root = root.resolve()
    if target != allowed_root / workspace_tools.DATA_FILE_NAME and allowed_root not in target.parents:
        raise ValueError("Şube verisi çalışma alanı dışına çıkamaz.")
    if not target.is_file():
        raise ValueError("Seçilen şubenin veri dosyası bulunamadı.")
    uri = target.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        schema = {row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "schedule_months" not in schema:
            raise ValueError("Şube dosyasında vardiya tablosu bulunamadı.")
        row = connection.execute(
            "SELECT payload,revision FROM schedule_months WHERE period=?", (month,)
        ).fetchone()
        entries = json.loads(row["payload"]) if row else []
        count = sum(1 for entry in entries for item in (entry.get("days") or {}).values()
                    if item.get("kind") == "work")
        hours = 0.0
        for entry in entries:
            for day_text, item in (entry.get("days") or {}).items():
                if item.get("kind") == "work":
                    start, end = scheduling._time_interval(day_text, item["start"], item["end"])
                    hours += (end - start).total_seconds() / 3600
        branch = next(item for item in branches["branches"] if item["id"] == branch_id)
        return {
            "month": month, "branch_id": branch_id, "branch_name": branch["name"],
            "revision": row["revision"] if row else "", "work_shifts": count,
            "work_hours": round(hours, 2), "people": len({entry.get("employee_id") for entry in entries}),
            "read_only": True,
        }
    finally:
        connection.close()


def _query_one(query, key: str, default: str = "") -> str:
    value = query.get(key, [default])
    return value[0] if isinstance(value, list) else str(value or default)


def _require_admin(handler) -> None:
    user = getattr(handler, "current_user", None)
    if user and user.get("role") != "admin":
        raise PermissionError("Bu operasyon aracı yönetici yetkisi gerektirir.")
    if not user:
        store_factory = getattr(handler, "_store", None)
        if callable(store_factory):
            with store_factory() as store:
                if store.users_enabled():
                    raise PermissionError("Bu operasyon aracı yönetici yetkisi gerektirir.")


def _with_store(handler):
    return handler._store()


def _normalize_temporary_assignment(payload: Any, people: list[dict[str, Any]]) -> dict[str, Any]:
    required = {
        "employee_id", "location_type", "location", "start_date", "end_date",
        "start_time", "end_time", "note",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("Geçici görevlendirme bilgileri geçersiz.")
    employee_id = _strict_int(payload["employee_id"], "Personel seçimi", 1, _MAX_EMPLOYEE_ID)
    if employee_id not in {
        int(person["id"]) for person in people if person.get("active", True)
    }:
        raise ValueError("Personel bulunamadı.")
    location_type = payload["location_type"]
    if location_type not in ("section", "branch"):
        raise ValueError("Görevlendirme türü bölüm veya şube olmalıdır.")
    if not isinstance(payload["location"], str):
        raise ValueError("Hedef bölüm veya şube geçersiz.")
    location = " ".join(payload["location"].split())
    if not 1 <= len(location) <= 80:
        raise ValueError("Hedef bölüm veya şube 1–80 karakter olmalıdır.")
    if location_type == "section" and location not in scheduling.section_names():
        raise ValueError("Hedef bölüm tanımlı bölümlerden biri olmalıdır.")
    start = _date(payload["start_date"], "Görevlendirme başlangıcı")
    end = _date(payload["end_date"], "Görevlendirme bitişi")
    if end < start or (end - start).days > 90:
        raise ValueError("Görevlendirme en fazla 91 gün olabilir.")
    start_time, end_time = payload["start_time"], payload["end_time"]
    begin = _minutes(start_time)
    finish = _minutes(end_time)
    if finish == begin:
        raise ValueError("Görevlendirme başlangıç ve bitiş saatleri farklı olmalıdır.")
    if not isinstance(payload["note"], str):
        raise ValueError("Görevlendirme notu geçersiz.")
    note = " ".join(payload["note"].split())
    if len(note) > 300:
        raise ValueError("Görevlendirme notu en fazla 300 karakter olabilir.")
    return {
        "employee_id": employee_id, "location_type": location_type, "location": location,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "start_time": start_time, "end_time": end_time, "note": note,
    }


def _assignment_interval(day: date, start_time: str, end_time: str) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.fromisoformat(start_time))
    end = datetime.combine(day, time.fromisoformat(end_time))
    if end <= start:
        end += timedelta(days=1)
    return start, end


def temporary_assignment_preview(store, payload: Any) -> dict[str, Any]:
    item = _normalize_temporary_assignment(payload, store.people())
    start, end = date.fromisoformat(item["start_date"]), date.fromisoformat(item["end_date"])
    scan_start, scan_end = start - timedelta(days=1), end + timedelta(days=1)
    periods = sorted({(scan_start + timedelta(days=offset)).strftime("%Y-%m")
                      for offset in range((scan_end - scan_start).days + 1)})
    schedules = {period: store.schedule_month(period) for period in periods}
    entries = [row for value in schedules.values() for row in value["entries"]]
    source_revisions = {period: value["revision"] for period, value in schedules.items()}
    conflicts = []
    employee_id = item["employee_id"]
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        proposed_start, proposed_end = _assignment_interval(day, item["start_time"], item["end_time"])
        for row in entries:
            if int(row.get("employee_id") or 0) != employee_id:
                continue
            for shift_day_text, shift in (row.get("days") or {}).items():
                shift_day = date.fromisoformat(shift_day_text)
                if shift_day not in (day - timedelta(days=1), day, day + timedelta(days=1)):
                    continue
                interval = _minutes_interval(shift_day_text, shift)
                if not interval or interval[0] >= proposed_end or interval[1] <= proposed_start:
                    continue
                different_place = (
                    item["location_type"] == "branch"
                    or (row.get("section") or "").casefold() != item["location"].casefold()
                )
                if different_place:
                    overlap_start, overlap_end = max(interval[0], proposed_start), min(interval[1], proposed_end)
                    conflicts.append({
                        "date": day.isoformat(), "scheduled_section": row.get("section") or "",
                        "location": item["location"], "start": overlap_start.strftime("%H:%M"),
                        "end": overlap_end.strftime("%H:%M"), "row_id": row["row_id"],
                    })
    existing = [dict(row) for row in store.connection.execute(
        "SELECT id,location_type,location,start_date,end_date,start_time,end_time "
        "FROM operations_temporary_assignments WHERE employee_id=? AND end_date>=? AND start_date<=? "
        "ORDER BY start_date,id", (employee_id, item["start_date"], item["end_date"]),
    )]
    for other in existing:
        if (other["location_type"] == item["location_type"]
                and other["location"].casefold() == item["location"].casefold()):
            continue
        overlap_start = max(start, date.fromisoformat(other["start_date"])) - timedelta(days=1)
        overlap_end = min(end, date.fromisoformat(other["end_date"])) + timedelta(days=1)
        for offset in range((overlap_end - overlap_start).days + 1):
            day = overlap_start + timedelta(days=offset)
            other_start = date.fromisoformat(other["start_date"])
            other_end = date.fromisoformat(other["end_date"])
            if not start <= day <= end or not other_start <= day <= other_end:
                continue
            first = _assignment_interval(day, item["start_time"], item["end_time"])
            second = _assignment_interval(day, other["start_time"], other["end_time"])
            if first[0] < second[1] and second[0] < first[1]:
                conflicts.append({
                    "date": max(first[0], second[0]).date().isoformat(),
                    "scheduled_section": other["location"],
                    "location": item["location"],
                    "start": max(first[0], second[0]).strftime("%H:%M"),
                    "end": min(first[1], second[1]).strftime("%H:%M"),
                    "row_id": f"assignment:{other['id']}",
                })
    conflicts.sort(key=lambda value: (value["date"], value["start"], value["location"]))
    source = {
        "item": item, "source_revisions": source_revisions, "existing": existing,
        "conflicts": conflicts,
    }
    return {
        **item, "days": (end - start).days + 1, "conflicts": conflicts,
        "source_revisions": source_revisions, "preview": _fingerprint(source),
    }


def save_temporary_assignment(store, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"assignment", "preview"} \
            or not isinstance(payload["preview"], str):
        raise ValueError("Görevlendirme kaydı için güncel önizleme gereklidir.")
    connection = store.connection
    started = not connection.in_transaction
    if started:
        connection.execute("BEGIN IMMEDIATE")
    try:
        preview = temporary_assignment_preview(store, payload["assignment"])
        if preview["preview"] != payload["preview"]:
            raise ValueError("Görevlendirme önizlemesi eskidi; çakışmaları yeniden denetleyin.")
        if preview["conflicts"]:
            raise ValueError("Aynı saatte başka bölüm veya şubede vardiya var; görevlendirme kaydedilmedi.")
        item = {key: preview[key] for key in (
            "employee_id", "location_type", "location", "start_date", "end_date",
            "start_time", "end_time", "note",
        )}
        assignment_id = secrets.token_hex(12)
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.connection.execute(
            "INSERT INTO operations_temporary_assignments "
            "(id,employee_id,location_type,location,start_date,end_date,start_time,end_time,note,created_at,actor) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (assignment_id, item["employee_id"], item["location_type"], item["location"],
             item["start_date"], item["end_date"], item["start_time"], item["end_time"],
             item["note"], created_at, store.actor or store.user_name()),
        )
        store.record_change(
            "operations.temporary_assignment", "Geçici görevlendirme kaydedildi",
            {"assignment_id": assignment_id, **item},
        )
        if started:
            connection.commit()
        return {"id": assignment_id, **item, "created_at": created_at}
    except Exception:
        if started:
            connection.rollback()
        raise


def report_metadata(store, month):
    schedule = store.schedule_month(month)
    attendance = store.attendance_month(month)
    pdks = store._setting("pdks_records", {}).get(month, {})
    plan_revision = schedule.get("revision") or None
    attendance_revision = attendance.get("revision") or None
    pdks_file = pdks.get("file") or None
    missing = []
    if not plan_revision:
        missing.append("Kayıtlı vardiya planı yok")
    if not attendance_revision:
        missing.append("Kayıtlı puantaj sürümü yok")
    if not pdks_file:
        missing.append("PDKS aktarımı yok (isteğe bağlı kaynak)")
    return {
        "month": month,
        "calculated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "plan_revision": plan_revision,
        "attendance_revision": attendance_revision,
        "pdks_file": pdks_file,
        "missing_sources": missing,
    }


def _report_json(handler, store, month, payload):
    if isinstance(payload, dict):
        payload = {**payload, "report_meta": report_metadata(store, month)}
    handler._json(payload)


def handle_get(handler, path: str, query) -> bool:
    if path not in _GET_ROUTES:
        return False
    month = scheduling.validate_month(_query_one(query, "month", date.today().strftime("%Y-%m")))
    if path in ("/api/operations/cost", "/api/operations/lifecycle", "/api/operations/availability",
                "/api/operations/temporary-assignments",
                "/api/operations/overtime-budget", "/api/operations/payroll-delivery", "/api/operations/mutual-swaps"):
        _require_admin(handler)
    with _with_store(handler) as store:
        if path == "/api/operations/quality":
            _report_json(handler, store, month, quality_report(store, month))
        elif path == "/api/operations/shift-activities":
            report = shift_activity_report(store, _activity_day(month, _query_one(query, "date")))
            user = handler.current_user
            report["can_edit"] = not user or user.get("role") == "admin"
            _report_json(handler, store, month, report)
        elif path == "/api/operations/mutual-swaps":
            handler._json({"items": mutual_swaps(store, month), "people": store.people()})
        elif path == "/api/operations/hourly-staffing":
            day_text = _query_one(query, "date")
            day = next((candidate for candidate in scheduling.month_dates(month)
                        if candidate.isoformat() == day_text), None)
            if day is None:
                raise ValueError("Kadro kontrolü günü seçilen ayda olmalıdır.")
            periods = {month, (day - timedelta(days=1)).strftime("%Y-%m")}
            entries = [row for period in periods for row in store.schedule_month(period)["entries"]]
            windows = store.compliance_settings()["min_staffing_windows"]
            _report_json(handler, store, month, {
                "date": day.isoformat(), "rows": compliance.hourly_staffing(day, entries, windows),
            })
        elif path == "/api/operations/rules":
            handler._json({"rules": _rules(store.connection)})
        elif path == "/api/operations/availability":
            handler._json({"items": [json.loads(row["payload"]) for row in store.connection.execute(
                "SELECT payload FROM operations_availability ORDER BY employee_id"
            )]})
        elif path == "/api/operations/emergency":
            payload = {key: _query_one(query, key) for key in ("employee_id", "date", "reason")}
            if not payload["employee_id"].isdigit():
                raise ValueError("Personel seçimi geçersiz.")
            _report_json(handler, store, month, emergency_preview(store, {
                "employee_id": int(payload["employee_id"]), "date": payload["date"], "reason": payload["reason"],
                "status": _query_one(query, "status", "absent"),
            }))
        elif path == "/api/operations/publish-diff":
            _report_json(handler, store, month, publish_diff(store, month))
        elif path == "/api/operations/closing":
            _report_json(handler, store, month, closing_checklist(store, month))
        elif path == "/api/operations/fatigue":
            report = quality_report(store, month)
            _report_json(handler, store, month, {
                "month": month, "findings": [item for item in report["findings"]
                                             if item.get("type") in ("consecutive_nights", "long_streak", "short_transition")],
            })
        elif path == "/api/operations/leave-conflicts":
            _report_json(handler, store, month, leave_conflicts(store, month))
        elif path == "/api/operations/trends":
            raw = _query_one(query, "months", "6")
            if not raw.isdigit():
                raise ValueError("Ay aralığı geçersiz.")
            _report_json(handler, store, month, trends(handler, store, month, int(raw)))
        elif path == "/api/operations/branch-compare":
            branch_id = _query_one(query, "branch_id")
            data_path = getattr(handler.server, "data_path", None)
            if not data_path:
                raise ValueError("Şube verisi bu sunucu bağlamında kullanılamıyor.")
            _report_json(handler, store, month, branch_comparison(data_path, branch_id, month))
        elif path == "/api/operations/payroll-delivery":
            store.connection.execute("BEGIN")
            try:
                result, _ = payroll_delivery_status(handler, store, month)
            finally:
                store.connection.rollback()
            _report_json(handler, store, month, result)
        elif path == "/api/operations/overtime-budget":
            store.connection.execute("BEGIN")
            try:
                attendance, _, saved = handler._attendance_for(store, month)
                result = overtime_budget(store, month, attendance, store.schedule_month(month)["entries"],
                                         bool(saved["revision"]))
            finally:
                store.connection.rollback()
            _report_json(handler, store, month, result)
        elif path == "/api/operations/cost":
            scenario_id = _query_one(query, "scenario_id")
            if scenario_id:
                row = store.connection.execute(
                    "SELECT month,name,payload FROM operations_cost_scenarios WHERE id=?", (scenario_id,)
                ).fetchone()
                if row is None:
                    raise ValueError("Kaydedilmiş maliyet senaryosu bulunamadı.")
                scenario_month = scheduling.validate_month(row["month"])
                attendance, _entries = handler._month_attendance(store, scenario_month)
                attendance["month"] = scenario_month
                result = cost_simulation(attendance, json.loads(row["payload"]))
                _report_json(handler, store, scenario_month, {"scenario": row["name"], "result": result})
                return True
            scenarios = [
                {"id": row["id"], "name": row["name"], "month": row["month"], "created_at": row["created_at"]}
                for row in store.connection.execute(
                    "SELECT id,name,month,created_at FROM operations_cost_scenarios ORDER BY created_at DESC LIMIT 20"
                )
            ]
            _report_json(handler, store, month, {"month": month, "scenarios": scenarios})
        elif path == "/api/operations/lifecycle":
            profiles = store.employee_profiles()
            _report_json(handler, store, month, {
                "people": store.people(), "sections": scheduling.section_names(),
                "fixed_patterns": {
                    employee_id: {
                        "staff_type": profile.get("staff_type", "shift"),
                        "pattern": profile.get("fixed_pattern") or list(scheduling.DEFAULT_FIXED_PATTERN),
                    }
                    for employee_id, profile in profiles.items()
                },
                "pending_transitions": lifecycle_pending(store),
            })
        elif path == "/api/operations/temporary-assignments":
            month_days = scheduling.month_dates(month)
            first, last = month_days[0], month_days[-1]
            people = {int(person["id"]): person["name"] for person in store.people()}
            items = [
                dict(row) | {"employee_name": people.get(int(row["employee_id"]), "Personel")}
                for row in store.connection.execute(
                    "SELECT id,employee_id,location_type,location,start_date,end_date,start_time,end_time,note,created_at,actor "
                    "FROM operations_temporary_assignments WHERE start_date<=? AND end_date>=? "
                    "ORDER BY start_date,employee_id,id", (last.isoformat(), first.isoformat()),
                )
            ]
            handler._json({"month": month, "people": store.people(), "sections": scheduling.section_names(),
                           "items": items})
        else:
            return False
    return True


def handle_write(handler, method: str, path: str) -> bool:
    if (method, path) not in _WRITE_ROUTES:
        return False
    if path in ("/api/operations/mutual-swaps", "/api/operations/shift-activities", "/api/operations/rules", "/api/operations/availability", "/api/operations/cost",
                "/api/operations/lifecycle", "/api/operations/emergency/apply",
                "/api/operations/lifecycle/preview", "/api/operations/lifecycle/apply",
                "/api/operations/temporary-assignments/preview",
                "/api/operations/publish", "/api/operations/overtime-budget", "/api/operations/payroll-delivery",
                "/api/operations/payroll-reconcile/preview", "/api/operations/payroll-adjustments/request",
                "/api/operations/payroll-adjustments/review"):
        _require_admin(handler)
    payload = handler._read_json()
    with _with_store(handler) as store:
        if path == "/api/operations/temporary-assignments/preview":
            if not isinstance(payload, dict) or payload.get("action") not in ("preview", "save"):
                raise ValueError("Geçici görevlendirme işlemi önizleme veya kaydetme olmalıdır.")
            if payload["action"] == "preview" and set(payload) == {"action", "assignment"}:
                handler._json(temporary_assignment_preview(store, payload["assignment"]))
            elif payload["action"] == "save" and set(payload) == {"action", "assignment", "preview"}:
                handler._json(save_temporary_assignment(store, {
                    "assignment": payload["assignment"], "preview": payload["preview"],
                }), 201)
            else:
                raise ValueError("Geçici görevlendirme isteği geçersiz.")
        elif path == "/api/operations/payroll-delivery":
            handler._json(register_payroll_delivery(handler, store, payload), 201)
        elif path == "/api/operations/payroll-adjustments/request":
            handler._json(register_payroll_adjustment(handler, store, payload), 201)
        elif path == "/api/operations/payroll-adjustments/review":
            handler._json(review_payroll_adjustment(handler, store, payload))
        elif path == "/api/operations/payroll-reconcile/preview":
            if not isinstance(payload, dict) or set(payload) != {"month", "filename", "content"}:
                raise ValueError("Muhasebe mutabakat isteği geçersiz.")
            month = scheduling.validate_month(payload["month"])
            attendance, _, saved = handler._attendance_for(store, month)
            result = payroll.reconcile_accounting_csv(
                month, attendance, store.employee_profiles(), payload["filename"], payload["content"],
            )
            result["schedule_revision"] = store.schedule_month(month)["revision"]
            result["attendance_revision"] = saved["revision"]
            handler._json(result)
        elif path == "/api/operations/shift-activities":
            handler._json(save_shift_activity(store, payload))
        elif path == "/api/operations/mutual-swaps":
            if "month" not in payload:
                raise ValueError("Takas ayı geçersiz.")
            month = scheduling.validate_month(payload.pop("month"))
            if payload.get("action") == "preview":
                started = not store.connection.in_transaction
                if started:
                    store.connection.execute("BEGIN")
                try:
                    result = preview_mutual_swap(handler, store, month, payload)
                finally:
                    if started and store.connection.in_transaction:
                        store.connection.rollback()
                handler._json(result)
            else:
                handler._json(manage_mutual_swap(store, month, payload))
        elif path == "/api/operations/overtime-budget":
            handler._json(save_overtime_budget(store, payload))
        elif path == "/api/operations/rules" and method == "PUT":
            rules = save_rules(store.connection, payload.get("rules"))
            with store.connection:
                store.record_change("operations.rules", "Operasyon kuralları güncellendi", {"rules": len(rules)})
            handler._json({"rules": rules})
        elif path == "/api/operations/availability" and method == "PUT":
            with store.connection:
                if payload.get("delete") is True and set(payload) == {"employee_id", "delete"}:
                    employee_id = _strict_int(payload["employee_id"], "Personel seçimi", 1, _MAX_EMPLOYEE_ID)
                    store.connection.execute(
                        "DELETE FROM operations_availability WHERE employee_id=?", (employee_id,)
                    )
                    store.record_change("operations.availability.delete", "Personel uygunluk kuralı silindi",
                                        {"employee_id": employee_id})
                    handler._json({"deleted": employee_id})
                else:
                    item = save_availability(store.connection, payload, store.people())
                    store.record_change("operations.availability", "Personel uygunluk kuralı güncellendi",
                                        {"employee_id": item["employee_id"]})
                    handler._json({"availability": item})
        elif path == "/api/operations/emergency/preview" and method == "POST":
            handler._json(emergency_preview(store, payload))
        elif path == "/api/operations/emergency/apply" and method == "POST":
            handler._json(apply_emergency(store, payload))
        elif path == "/api/operations/publish" and method == "POST":
            result = publish_in_transaction(store, payload.get("month"), payload.get("revision"))
            handler._json(result)
        elif path == "/api/operations/closing" and method == "POST":
            if set(payload) != {"month", "step", "name", "fingerprint"}:
                raise ValueError("Kapanış adımı bilgisi geçersiz.")
            with store.connection:
                handler._json(mark_closing_step(store, payload["month"], payload["step"], payload["name"],
                                                payload["fingerprint"]))
        elif path == "/api/operations/cost" and method == "POST":
            month = scheduling.validate_month(payload.get("month"))
            scenario = payload.get("scenario")
            if scenario is not None:
                if not isinstance(scenario, dict) or set(scenario) != {"name", "rates", "hours_per_month",
                                                                       "overtime_multiplier", "holiday_multiplier"}:
                    raise ValueError("Maliyet senaryosu alanları geçersiz.")
                name = " ".join(str(scenario["name"] or "").split())
                if not 1 <= len(name) <= 60:
                    raise ValueError("Senaryo adı 1-60 karakter olmalıdır.")
                saved = {
                    "rates": scenario["rates"], "hours_per_month": scenario["hours_per_month"],
                    "overtime_multiplier": scenario["overtime_multiplier"],
                    "holiday_multiplier": scenario["holiday_multiplier"],
                }
                simulation_payload = saved
            else:
                simulation_payload = payload
            attendance, _entries = handler._month_attendance(store, month)
            attendance["month"] = month
            result = cost_simulation(attendance, simulation_payload)
            if scenario is not None:
                with store.connection:
                    store.connection.execute(
                        "INSERT INTO operations_cost_scenarios(id,month,name,payload,created_at) VALUES (?,?,?,?,?)",
                        (secrets.token_hex(12), month, name, _json(saved),
                         datetime.now(timezone.utc).isoformat(timespec="seconds")),
                    )
                    store.connection.execute(
                        "DELETE FROM operations_cost_scenarios WHERE id IN ("
                        "SELECT id FROM operations_cost_scenarios ORDER BY created_at DESC LIMIT -1 OFFSET 100)"
                    )
                    store.record_change("operations.cost_scenario", f"{month} maliyet senaryosu kaydedildi",
                                        {"name": name})
                handler._json({"scenario": name, "result": result})
                return True
            handler._json(result)
        elif path == "/api/operations/lifecycle/preview" and method == "POST":
            handler._json(lifecycle_preview(store, payload))
        elif path == "/api/operations/lifecycle/apply" and method == "POST":
            handler._json(apply_lifecycle(store, payload))
        else:
            return False
    return True
