from __future__ import annotations

import hashlib
import json
import secrets
import calendar
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import unquote

import compliance
import planning
import scheduling


_LEAVE_KINDS = {
    "annual_leave", "sick_leave", "unpaid_leave", "marriage_leave",
    "quarantine", "half_day", "absent", "weekly_off",
}
_PATTERNS = {"single", "range", "weekdays"}
_STATUSES = {"pending", "approved", "cancelled"}
_MAX_DAYS = 366
# The existing calendar indexes this label registry directly for every code.
planning.LEAVE_REQUEST_TYPES.update({
    scheduling.KIND_CODES[kind]: scheduling.CODE_LABELS[kind] for kind in _LEAVE_KINDS
})


def initialize(connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS linked_leave_requests (
            id TEXT PRIMARY KEY,
            employee_id INTEGER NOT NULL REFERENCES employees(id),
            pattern TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            weekdays TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS linked_leave_employee_dates
            ON linked_leave_requests(employee_id, start_date, end_date, status);
        CREATE TABLE IF NOT EXISTS linked_leave_applications (
            request_id TEXT NOT NULL REFERENCES linked_leave_requests(id),
            month TEXT NOT NULL,
            employee_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            row_id TEXT NOT NULL,
            before_json TEXT NOT NULL,
            after_json TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            undone_at TEXT,
            PRIMARY KEY(request_id, month, day)
        );
        CREATE INDEX IF NOT EXISTS linked_leave_applications_month
            ON linked_leave_applications(month, undone_at);
        """
    )


def _strict_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{label} YYYY-AA-GG biçiminde olmalıdır.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} YYYY-AA-GG biçiminde olmalıdır.") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} YYYY-AA-GG biçiminde olmalıdır.")
    return parsed


def _normalize_request(payload: Any, employee_ids: set[int], *, create: bool) -> dict[str, Any]:
    required = {"employee_id", "pattern", "start", "end", "kind"}
    allowed = required | {"weekdays", "note"}
    if not isinstance(payload, dict) or not required.issubset(payload) or not set(payload) <= allowed:
        raise ValueError("Bağlı izin bilgileri geçersiz.")
    employee_id = payload["employee_id"]
    if not isinstance(employee_id, int) or isinstance(employee_id, bool) or employee_id not in employee_ids:
        raise ValueError("Personel seçimi geçersiz.")
    pattern = payload["pattern"]
    if not isinstance(pattern, str) or pattern not in _PATTERNS:
        raise ValueError("Yinelenme türü geçersiz.")
    start = _strict_date(payload["start"], "Başlangıç tarihi")
    end = _strict_date(payload["end"], "Bitiş tarihi")
    if end < start or (end - start).days >= _MAX_DAYS:
        raise ValueError("İzin aralığı en fazla bir yıl olabilir; bitiş başlangıçtan önce olamaz.")
    if pattern == "single" and start != end:
        raise ValueError("Tek günlük izin için başlangıç ve bitiş aynı gün olmalıdır.")
    weekdays = payload.get("weekdays", [])
    if pattern == "weekdays":
        if (not isinstance(weekdays, list) or not weekdays or len(weekdays) > 7
                or any(not isinstance(day, int) or isinstance(day, bool) or not 0 <= day <= 6
                       for day in weekdays)
                or len(set(weekdays)) != len(weekdays)):
            raise ValueError("Yinelenen izin için haftanın en az bir günü seçilmelidir.")
        weekdays = sorted(weekdays)
    else:
        if weekdays not in ([], None):
            raise ValueError("Yinelenme olmayan izinde hafta günü seçilemez.")
        weekdays = []
    kind = payload["kind"]
    if not isinstance(kind, str) or kind not in _LEAVE_KINDS:
        raise ValueError("İzin türü geçersiz.")
    note = payload.get("note", "")
    if not isinstance(note, str) or len(note.strip()) > 300:
        raise ValueError("Not en fazla 300 karakter olabilir.")
    return {
        "employee_id": employee_id,
        "pattern": pattern,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "weekdays": weekdays,
        "kind": kind,
        "note": note.strip(),
        "status": "pending" if create else None,
    }


def _request_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "employee_id": int(row["employee_id"]),
        "pattern": row["pattern"],
        "start": row["start_date"],
        "end": row["end_date"],
        "weekdays": json.loads(row["weekdays"]),
        "kind": row["kind"],
        "status": row["status"],
        "note": row["note"],
        "revision": int(row["revision"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _all_requests(connection, approved_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM linked_leave_requests"
    if approved_only:
        sql += " WHERE status='approved'"
    sql += " ORDER BY start_date, employee_id, id"
    return [_request_dict(row) for row in connection.execute(sql)]


def _request_days(item: dict[str, Any]) -> list[date]:
    start = date.fromisoformat(item["start"])
    end = date.fromisoformat(item["end"])
    weekdays = set(item["weekdays"])
    days = []
    cursor = start
    while cursor <= end:
        if item["pattern"] != "weekdays" or cursor.weekday() in weekdays:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _month_bounds(month: str) -> tuple[date, date]:
    month = scheduling.validate_month(month)
    year, number = map(int, month.split("-"))
    return date(year, number, 1), date(year, number, calendar.monthrange(year, number)[1])


def _month_requests(connection, month: str, request_ids: list[str] | None = None,
                    approved_only: bool = True) -> list[dict[str, Any]]:
    first, last = _month_bounds(month)
    items = _all_requests(connection, approved_only=approved_only)
    selected = set(request_ids or [])
    if request_ids is not None and len(selected) != len(request_ids):
        raise ValueError("İzin talebi kimlikleri yinelenemez.")
    return [
        item for item in items
        if (not selected or item["id"] in selected)
        and date.fromisoformat(item["start"]) <= last
        and date.fromisoformat(item["end"]) >= first
    ]


def calendar_requests(store, month: str) -> list[dict[str, Any]]:
    """Day-exact read-only linked and scheduled weekly-off rows; never persist."""
    month = scheduling.validate_month(month)
    rows = []
    linked_weekly_offs = set()
    for request in _month_requests(store.connection, month, approved_only=False):
        if request["status"] not in ("pending", "approved"):
            continue
        for day in _request_days(request):
            if day.strftime("%Y-%m") != month:
                continue
            day_text = day.isoformat()
            rows.append({
                "id": f"linked:{request['id']}:{day_text}",
                "linked_request_id": request["id"], "source": "linked_leave",
                "employee_id": request["employee_id"], "start": day_text, "end": day_text,
                "code": scheduling.KIND_CODES[request["kind"]], "kind": request["kind"],
                "status": request["status"], "note": request["note"],
                "created_at": request["created_at"],
            })
            if request["kind"] == "weekly_off":
                linked_weekly_offs.add((request["employee_id"], day_text))
    for entry in store.schedule_month(month)["entries"]:
        employee_id = int(entry["employee_id"])
        for day_text, item in (entry.get("days") or {}).items():
            if day_text[:7] != month or item.get("kind") != "weekly_off" \
                    or (employee_id, day_text) in linked_weekly_offs:
                continue
            rows.append({
                "id": f"fixed:{employee_id}:{day_text}", "source": "fixed_schedule",
                "employee_id": employee_id, "start": day_text, "end": day_text,
                "code": scheduling.KIND_CODES["weekly_off"], "kind": "weekly_off",
                "status": "approved", "note": "", "created_at": "",
                "section": entry.get("section", ""),
            })
    return rows


def _schedule_revision(store, month: str) -> tuple[list[dict[str, Any]], str]:
    result = store.schedule_month(month)
    return result["entries"], result["revision"]


def _slot_and_section(store, entries: list[dict[str, Any]], employee_id: int, day: str):
    rows = [row for row in entries if row.get("employee_id") == employee_id]
    for row in rows:
        if day not in (row.get("days") or {}):
            return row, False
    if rows:
        return None, False
    import operations
    sections = scheduling.section_names()
    preferred = operations.effective_home_section(store, employee_id, day)
    ordered = ([preferred] if preferred in sections else []) + [
        section for section in sections if section != preferred
    ]
    for section in ordered:
        section_rows = [row for row in entries if row.get("section") == section]
        if len(section_rows) >= scheduling.section_capacity(section):
            continue
        used = {row.get("slot") for row in section_rows if row.get("slot") is not None}
        slot = next((index for index in range(1, scheduling.section_capacity(section) + 1)
                     if index not in used), None)
        return {
            "row_id": "linked-" + hashlib.sha256(
                f"{employee_id}:{section}:{slot}:{day[:7]}".encode("utf-8")
            ).hexdigest()[:20],
            "employee_id": employee_id,
            "section": section,
            "slot": slot,
            "days": {},
        }, True
    return None, False


def _plan_month(store, month: str, entries: list[dict[str, Any]],
                requests: list[dict[str, Any]],
                ignore_application_ids: set[str] | None = None) -> dict[str, Any]:
    first, last = _month_bounds(month)
    placements, conflicts, already = [], [], []
    occupied_keys = set()
    virtual_entries = json.loads(json.dumps(entries, ensure_ascii=False))
    nearby = _schedules_around(store, month)
    nearby[month] = virtual_entries
    approved = _month_requests(store.connection, month)
    for request in requests:
        if request["status"] != "approved":
            continue
        for day in _request_days(request):
            if not first <= day <= last:
                continue
            day_text = day.isoformat()
            key = (request["employee_id"], day_text)
            app = store.connection.execute(
                "SELECT after_json FROM linked_leave_applications "
                "WHERE request_id=? AND month=? AND day=? AND undone_at IS NULL",
                (request["id"], month, day_text),
            ).fetchone()
            if app and request["id"] not in (ignore_application_ids or set()):
                occupied_keys.add(key)
                already.append({"request_id": request["id"], "employee_id": request["employee_id"],
                                "date": day_text})
                continue
            if key in occupied_keys:
                conflicts.append({"request_id": request["id"], "employee_id": request["employee_id"],
                                  "date": day_text, "reason": "Aynı güne birden fazla bağlı izin düşüyor."})
                continue
            occupied_keys.add(key)
            if any(
                other["id"] != request["id"] and other["employee_id"] == request["employee_id"]
                and day in _request_days(other) for other in approved
            ):
                conflicts.append({
                    "request_id": request["id"], "employee_id": request["employee_id"],
                    "date": day_text, "reason": "Aynı günü kapsayan başka onaylı izin var; elle inceleyin.",
                })
                continue
            person_rows = [row for row in virtual_entries if row.get("employee_id") == request["employee_id"]]
            existing = next((row for row in person_rows if day_text in (row.get("days") or {})), None)
            if existing is not None:
                item = existing["days"][day_text]
                conflicts.append({
                    "request_id": request["id"], "employee_id": request["employee_id"],
                    "date": day_text, "reason": "Bu gün çizelgede dolu; kayıt korunacak.",
                    "existing_kind": item.get("kind", ""),
                })
                continue
            start_of_day = datetime.combine(day, time.min)
            end_of_day = start_of_day + timedelta(days=1)
            if any(start < end_of_day and end > start_of_day
                   for start, end, _, _ in _work_for_person(nearby, request["employee_id"])):
                conflicts.append({
                    "request_id": request["id"], "employee_id": request["employee_id"],
                    "date": day_text,
                    "reason": "Komşu gündeki gece vardiyası izin gününe taşıyor; kayıt korundu.",
                    "existing_kind": "work",
                })
                continue
            row, create_row = _slot_and_section(store, virtual_entries, request["employee_id"], day_text)
            if row is None:
                conflicts.append({"request_id": request["id"], "employee_id": request["employee_id"],
                                  "date": day_text,
                                  "reason": "Personel için boş satır veya bölüm kapasitesi bulunamadı."})
                continue
            placements.append({
                "request_id": request["id"], "employee_id": request["employee_id"],
                "date": day_text, "kind": request["kind"], "row_id": row["row_id"],
                "section": row["section"], "slot": row.get("slot"), "create_row": create_row,
            })
            if create_row:
                virtual_entries.append(row)
            row.setdefault("days", {})[day_text] = {"kind": request["kind"]}
    return {"placements": placements, "conflicts": conflicts, "already_applied": already}


def _materialize_plan(entries: list[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(entries, ensure_ascii=False))
    by_id = {row["row_id"]: row for row in result}
    applications = []
    for placement in plan["placements"]:
        row = by_id.get(placement["row_id"])
        if row is None and placement["create_row"]:
            row = {
                "row_id": placement["row_id"], "employee_id": placement["employee_id"],
                "section": placement["section"], "days": {},
            }
            if placement["slot"] is not None:
                row["slot"] = placement["slot"]
            result.append(row)
            by_id[row["row_id"]] = row
        if row is None:
            plan["conflicts"].append({
                "request_id": placement["request_id"], "employee_id": placement["employee_id"],
                "date": placement["date"], "reason": "Çizelge satırı değişti; izin uygulanmadı.",
            })
            continue
        row.setdefault("days", {})[placement["date"]] = {"kind": placement["kind"]}
        applications.append({
            "request_id": placement["request_id"], "employee_id": placement["employee_id"],
            "date": placement["date"], "row_id": row["row_id"], "before": None,
            "after": {"kind": placement["kind"]},
        })
    return {"entries": result, "applications": applications}


def _preview_hash(month: str, revision: str, requests: list[dict[str, Any]],
                  plan: dict[str, Any]) -> str:
    value = {
        "month": month,
        "revision": revision,
        "requests": sorted((item["id"], item["revision"]) for item in requests),
        "placements": plan["placements"],
        "conflicts": plan["conflicts"],
        "already_applied": plan["already_applied"],
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def reconcile_month(store, month: str, entries: list[dict[str, Any]],
                    *, request_ids: list[str] | None = None) -> dict[str, Any]:
    """Return generated entries with eligible approved leave applied; does not persist.

    Inside the existing schedule-save transaction, after checking revisions,
    persist ``result["entries"]``, then call
    ``record_applications_in_transaction(store, month, result["applications"])``.
    """
    month = scheduling.validate_month(month)
    if not isinstance(entries, list):
        raise ValueError("Vardiya kayıtları liste olmalıdır.")
    requests = _month_requests(store.connection, month, request_ids)
    if month in store.locked_periods():
        return {
            "entries": json.loads(json.dumps(entries, ensure_ascii=False)),
            "applications": [],
            "already_applied": [],
            "conflicts": [
                {"request_id": request["id"], "employee_id": request["employee_id"],
                 "date": day.isoformat(), "reason": "Dönem kilitli; izin uygulanmadı."}
                for request in requests for day in _request_days(request)
                if day.strftime("%Y-%m") == month
            ],
        }
    plan = _plan_month(store, month, entries, requests)
    materialized = _materialize_plan(entries, plan)
    return {"entries": materialized["entries"], "applications": materialized["applications"],
            "conflicts": plan["conflicts"], "already_applied": plan["already_applied"]}


def _move_request_payload(payload: Any) -> tuple[str, int, str, str]:
    if not isinstance(payload, dict) or set(payload) != {
        "request_id", "request_revision", "start", "end",
    }:
        raise ValueError("İzin taşıma önizlemesi geçersiz.")
    request_id = _request_id(payload["request_id"])
    revision = _strict_int(payload["request_revision"], "İzin sürümü")
    start = _strict_date(payload["start"], "Yeni başlangıç").isoformat()
    end = _strict_date(payload["end"], "Yeni bitiş").isoformat()
    return request_id, revision, start, end


def preview_request_move(store, payload: Any) -> dict[str, Any]:
    request_id, expected_revision, start, end = _move_request_payload(payload)
    row = store.connection.execute(
        "SELECT * FROM linked_leave_requests WHERE id=?", (request_id,),
    ).fetchone()
    if row is None or row["status"] != "approved" or int(row["revision"]) != expected_revision:
        raise ValueError("Taşınacak onaylı izin değişmiş; listeyi yenileyin.")
    original = _request_dict(row)
    if (start, end) == (original["start"], original["end"]):
        raise ValueError("Yeni tarih aralığı mevcut izin tarihleriyle aynı.")
    moved = _normalize_request({
        "employee_id": original["employee_id"], "pattern": original["pattern"],
        "start": start, "end": end, "weekdays": original["weekdays"],
        "kind": original["kind"], "note": original["note"],
    }, {original["employee_id"]}, create=False)
    moved = original | moved | {"status": "approved"}
    active_markers = [dict(mark) for mark in store.connection.execute(
        "SELECT * FROM linked_leave_applications WHERE request_id=? AND undone_at IS NULL "
        "ORDER BY month,day", (request_id,),
    )]
    if len(active_markers) > 4000:
        raise ValueError("İzin taşıma için ilişkili hücre sayısı sınırı aşıldı.")
    first_month, last_month = start[:7], end[:7]
    target_months = {
        item["period"] for item in store.connection.execute(
            "SELECT period FROM schedule_months WHERE period>=? AND period<=?",
            (first_month, last_month),
        )
    }
    months = sorted(target_months | {mark["month"] for mark in active_markers})
    if len(months) > 24:
        raise ValueError("İzin taşıma en fazla 24 kayıtlı dönemi kapsayabilir.")
    locked = set(store.locked_periods())
    blocked, conflicts, plans, unscheduled = [], [], [], []
    new_days = {day.isoformat() for day in _request_days(moved)}
    for day_text in sorted(new_days):
        if day_text[:7] not in target_months:
            unscheduled.append(day_text)
    for month in months:
        current = store.schedule_month(month)
        entries = json.loads(json.dumps(current["entries"], ensure_ascii=False))
        month_markers = [mark for mark in active_markers if mark["month"] == month]
        if month in locked:
            blocked.append({"month": month, "reason": "Dönem kilitli; taşıma yapılamaz."})
        by_id = {entry["row_id"]: entry for entry in entries}
        old_removals = []
        for mark in month_markers:
            entry = by_id.get(mark["row_id"])
            after = json.loads(mark["after_json"])
            if (entry is None or entry.get("employee_id") != int(mark["employee_id"])
                    or (entry.get("days") or {}).get(mark["day"]) != after):
                blocked.append({
                    "month": month, "date": mark["day"],
                    "reason": "Eski bağlı izin hücresi değişmiş; elle inceleme gerekir.",
                })
                continue
            before = json.loads(mark["before_json"])
            if before is None:
                entry["days"].pop(mark["day"], None)
            else:
                entry["days"][mark["day"]] = before
            old_removals.append(mark["day"])
        plan = {"placements": [], "conflicts": [], "already_applied": []}
        if month in target_months and month not in locked:
            plan = _plan_month(
                store, month, entries, [moved], ignore_application_ids={request_id},
            )
        materialized = _materialize_plan(entries, plan)
        conflicts.extend({"month": month, **item} for item in plan["conflicts"])
        if len(old_removals) != len(month_markers):
            continue
        plans.append({
            "month": month, "revision": current["revision"], "old_removals": old_removals,
            "applications": materialized["applications"], "entries": materialized["entries"],
            "conflicts": plan["conflicts"],
        })
    source = {
        "request_id": request_id, "request_revision": expected_revision,
        "original": original, "moved": moved,
        "markers": active_markers, "plans": plans, "blocked": blocked,
        "conflicts": conflicts, "unscheduled": unscheduled,
    }
    preview_hash = hashlib.sha256(
        json.dumps(source, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "request_id": request_id, "request_revision": expected_revision,
        "old_start": original["start"], "old_end": original["end"],
        "start": moved["start"], "end": moved["end"],
        "old_removals": sum(len(plan["old_removals"]) for plan in plans),
        "new_applications": sum(len(plan["applications"]) for plan in plans),
        "months": [{
            "month": plan["month"], "revision": plan["revision"],
            "old_removals": plan["old_removals"],
            "new_applications": plan["applications"],
        } for plan in plans],
        "blocked": blocked, "conflicts": conflicts, "unscheduled": unscheduled,
        "can_apply": not blocked and not conflicts,
        "preview_hash": preview_hash, "_plans": plans, "_moved": moved,
        "_markers": active_markers, "_original": original,
    }


def apply_request_move(store, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "request_id", "request_revision", "start", "end", "preview_hash", "confirmed",
    } or payload["confirmed"] is not True or not isinstance(payload["preview_hash"], str):
        raise ValueError("İzin taşıma için güncel önizleme ve açık onay gereklidir.")
    preview_payload = {key: payload[key] for key in ("request_id", "request_revision", "start", "end")}
    store._backup_before_change()
    connection = store.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        preview = preview_request_move(store, preview_payload)
        if preview["preview_hash"] != payload["preview_hash"]:
            raise ValueError("İzin veya vardiya kayıtları değişti; taşıma önizlemesini yenileyin.")
        if not preview["can_apply"]:
            raise ValueError("Yeni izin tarihi çakışıyor veya eski dönem güvenle değiştirilemiyor; hiçbir kayıt taşınmadı.")
        periods = [plan["month"] for plan in preview["_plans"]]
        store.assert_periods_unlocked(periods)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection.execute(
            "UPDATE linked_leave_requests SET start_date=?,end_date=?,weekdays=?,revision=revision+1,updated_at=? "
            "WHERE id=? AND revision=? AND status='approved'",
            (preview["_moved"]["start"], preview["_moved"]["end"],
             json.dumps(preview["_moved"]["weekdays"]), now,
             preview["request_id"], preview["request_revision"]),
        )
        if connection.execute("SELECT changes()").fetchone()[0] != 1:
            raise ValueError("Onaylı izin değişmiş; hiçbir kayıt taşınmadı.")
        new_application_keys = {
            (item["month"], application["date"])
            for item in preview["_plans"] for application in item["applications"]
        }
        for mark in preview["_markers"]:
            if (mark["month"], mark["day"]) not in new_application_keys:
                connection.execute(
                    "UPDATE linked_leave_applications SET undone_at=? "
                    "WHERE request_id=? AND month=? AND day=? AND undone_at IS NULL",
                    (now, preview["request_id"], mark["month"], mark["day"]),
                )
        revisions = {}
        applied = 0
        for plan in preview["_plans"]:
            if plan["entries"] == store.schedule_month(plan["month"])["entries"] \
                    and not plan["applications"] and not plan["old_removals"]:
                continue
            revisions[plan["month"]] = _write_month_in_transaction(
                store, plan["month"], plan["entries"], plan["revision"], plan["applications"],
            )
            applied += len(plan["applications"])
        store.record_change(
            "linked_leave.move", "Onaylı izin tarihleri birlikte taşındı",
            {"request_id": preview["request_id"], "old_start": preview["old_start"],
             "old_end": preview["old_end"], "start": preview["start"], "end": preview["end"],
             "removed": preview["old_removals"], "applied": applied, "months": revisions},
        )
        connection.commit()
        return {
            "request_id": preview["request_id"], "status": "approved",
            "old_removals": preview["old_removals"], "new_applications": applied,
            "schedule_revisions": revisions, "unscheduled": preview["unscheduled"],
        }
    except Exception:
        connection.rollback()
        raise


def prepare_schedule_save(store, month: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Prepare entries inside the caller's transaction, after revision/lock checks."""
    return reconcile_month(store, month, entries)


def record_applications_in_transaction(store, month: str,
                                       applications: list[dict[str, Any]]) -> int:
    """Validate persisted cells and UPSERT markers, without BEGIN/COMMIT/ROLLBACK.

    The caller owns the transaction and must roll it back if this function raises.
    Repeat calls for active identical markers are no-ops; undone markers may be
    reapplied. Each transition remains preserved in change_history.
    """
    month = scheduling.validate_month(month)
    connection = store.connection
    if not connection.in_transaction:
        raise ValueError("Bağlı izin kaydı mevcut vardiya kayıt işlemi içinde yazılmalıdır.")
    if not isinstance(applications, list) or len(applications) > 4000:
        raise ValueError("Uygulanan izin sayısı geçersiz.")
    if not applications:
        return 0
    store.assert_periods_unlocked([month])
    entries = {row["row_id"]: row for row in store.schedule_month(month)["entries"]}
    validated = []
    seen = set()
    for item in applications:
        required = {"request_id", "employee_id", "date", "row_id", "before", "after"}
        if not isinstance(item, dict) or not required <= set(item):
            raise ValueError("Uygulanan izin kaydı geçersiz.")
        request_id = _request_id(item["request_id"])
        day = _strict_date(item["date"], "İzin tarihi")
        key = (request_id, day.isoformat())
        if day.strftime("%Y-%m") != month or key in seen:
            raise ValueError("Uygulanan izin tarihi dönem dışında veya yineleniyor.")
        seen.add(key)
        row = entries.get(item["row_id"])
        if (row is None or row.get("employee_id") != item["employee_id"]
                or row.get("days", {}).get(day.isoformat()) != item["after"]):
            raise ValueError("Uygulanan izin hücresi artık çizelgedekiyle aynı değil.")
        request_row = connection.execute(
            "SELECT * FROM linked_leave_requests WHERE id=? AND status='approved'",
            (request_id,),
        ).fetchone()
        if request_row is None:
            raise ValueError("Onaylı izin bulunamadı.")
        request = _request_dict(request_row)
        if (request["employee_id"] != item["employee_id"]
                or day not in _request_days(request)
                or item["after"] != {"kind": request["kind"]}
                or item["before"] is not None):
            raise ValueError("Uygulanan hücre onaylı izinle eşleşmiyor.")
        prior = connection.execute(
            "SELECT * FROM linked_leave_applications WHERE request_id=? AND month=? AND day=?",
            (request_id, month, day.isoformat()),
        ).fetchone()
        if prior is not None and prior["undone_at"] is None:
            if (prior["employee_id"] != item["employee_id"] or prior["row_id"] != item["row_id"]
                    or json.loads(prior["after_json"]) != item["after"]):
                raise ValueError("Bağlı izin uygulama kaydı değişmiş.")
            continue
        validated.append(item)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for item in validated:
        connection.execute(
            "INSERT INTO linked_leave_applications "
            "(request_id, month, employee_id, day, row_id, before_json, after_json, applied_at, undone_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL) "
            "ON CONFLICT(request_id, month, day) DO UPDATE SET "
            "employee_id=excluded.employee_id, row_id=excluded.row_id, "
            "before_json=excluded.before_json, after_json=excluded.after_json, "
            "applied_at=excluded.applied_at, undone_at=NULL "
            "WHERE linked_leave_applications.undone_at IS NOT NULL",
            (item["request_id"], month, item["employee_id"], item["date"], item["row_id"],
             json.dumps(item["before"], ensure_ascii=False, separators=(",", ":")),
             json.dumps(item["after"], ensure_ascii=False, separators=(",", ":")), now),
        )
    if validated:
        store.record_change(
            "linked_leave.applications", f"{month} bağlantılı izin hücreleri kaydedildi",
            {"month": month, "applications": validated},
        )
    return len(validated)


def record_reconciled_applications(store, month: str, applications: list[dict[str, Any]],
                                   saved_revision: str) -> int:
    """Record reconcile audit markers after the caller has saved that exact month."""
    month = scheduling.validate_month(month)
    current = store.schedule_month(month)
    if current["revision"] != saved_revision:
        raise ValueError("Vardiya kaydı değişmiş; bağlı izin denetimi yazılmadı.")
    connection = store.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        if store.schedule_month(month)["revision"] != saved_revision:
            raise ValueError("Vardiya kaydı değişmiş; bağlı izin denetimi yazılmadı.")
        count = record_applications_in_transaction(store, month, applications)
        connection.commit()
        return count
    except Exception:
        connection.rollback()
        raise


def save_reconciled_month(store, month: str, result: dict[str, Any],
                          expected_revision: str) -> dict[str, Any]:
    """Atomically save reconciled entries and their linked-cell audit records."""
    if (not isinstance(result, dict)
            or not {"entries", "applications", "conflicts"} <= set(result)
            or not isinstance(result["entries"], list)
            or not isinstance(result["applications"], list)
            or not isinstance(result["conflicts"], list)):
        raise ValueError("Bağlı izinli vardiya sonucu geçersiz.")
    revision = _replace_month_schedule(
        store, scheduling.validate_month(month), result["entries"], expected_revision,
        "linked_leave.reconcile",
        {"applications": len(result["applications"]), "conflicts": len(result["conflicts"])},
        result["applications"],
    )
    return {
        "revision": revision,
        "applied": len(result["applications"]),
        "conflicts": result["conflicts"],
    }


def _strict_int(value: Any, label: str, minimum: int = 1, maximum: int = 2_147_483_647) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{label} geçersiz.")
    return value


def _request_id(value: Any) -> str:
    if not isinstance(value, str) or not 8 <= len(value) <= 64:
        raise ValueError("İzin talebi kimliği geçersiz.")
    return value


def _replace_month_schedule(store, month: str, entries: list[dict[str, Any]],
                            expected_revision: str, audit_action: str,
                            audit_detail: dict[str, Any],
                            applications: list[dict[str, Any]] | None = None) -> str:
    store.assert_periods_unlocked([month])
    store._backup_before_change()
    connection = store.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        revision = _write_month_in_transaction(
            store, month, entries, expected_revision, applications or [],
        )
        store.record_change(audit_action, f"{month} çizelgesine bağlı izin işlendi",
                            {"month": month, "schedule_revision": revision, **audit_detail})
        connection.commit()
        return revision
    except Exception:
        connection.rollback()
        raise


def _write_month_in_transaction(store, month: str, entries: list[dict[str, Any]],
                                 expected_revision: str,
                                 applications: list[dict[str, Any]]) -> str:
    connection = store.connection
    if not connection.in_transaction:
        raise ValueError("Vardiya yazımı mevcut kayıt işlemi içinde yapılmalıdır.")
    store.assert_periods_unlocked([month])
    cleaned = scheduling.normalize_schedule_entries(month, entries, store.people())
    current = connection.execute(
        "SELECT payload, revision FROM schedule_months WHERE period=?", (month,),
    ).fetchone()
    current_revision = current["revision"] if current else ""
    if current_revision != expected_revision:
        raise ValueError("Vardiya kaydı değişmiş; yenileyip işlemi yeniden deneyin.")
    if current:
        connection.execute(
            "INSERT INTO archive_revisions(kind, period, payload, revision, recorded_at) "
            "VALUES ('schedule', ?, ?, ?, ?)",
            (month, current["payload"], current_revision, datetime.now(timezone.utc).isoformat()),
        )
    revision = secrets.token_hex(16)
    connection.execute(
        "INSERT INTO schedule_months(period, payload, revision) VALUES (?, ?, ?) "
        "ON CONFLICT(period) DO UPDATE SET payload=excluded.payload, revision=excluded.revision",
        (month, json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")), revision),
    )
    record_applications_in_transaction(store, month, applications)
    return revision


def _apply_approved_in_transaction(store, request: dict[str, Any]) -> dict[str, Any]:
    """Apply one newly approved request to every saved covered month."""
    connection = store.connection
    if not connection.in_transaction:
        raise ValueError("İzin onayı mevcut kayıt işlemi içinde yapılmalıdır.")
    rows = list(connection.execute(
        "SELECT period, payload, revision FROM schedule_months "
        "WHERE period>=? AND period<=? ORDER BY period",
        (request["start"][:7], request["end"][:7]),
    ))
    conflicts, months, total = [], {}, 0
    locked = set(store.locked_periods())
    for row in rows:
        month = row["period"]
        if month in locked:
            conflicts.extend(
                {"month": month, "request_id": request["id"],
                 "employee_id": request["employee_id"], "date": day.isoformat(),
                 "reason": "Dönem kilitli; izin uygulanmadı."}
                for day in _request_days(request) if day.strftime("%Y-%m") == month
            )
            continue
        prepared = reconcile_month(
            store, month, json.loads(row["payload"]), request_ids=[request["id"]],
        )
        conflicts.extend({"month": month, **item} for item in prepared["conflicts"])
        if not prepared["applications"]:
            continue
        revision = _write_month_in_transaction(
            store, month, prepared["entries"], row["revision"], prepared["applications"],
        )
        total += len(prepared["applications"])
        months[month] = revision
    store.record_change(
        "linked_leave.approve_apply", "Onaylanan izin kayıtlı dönemlere otomatik işlendi",
        {"request_id": request["id"], "applied": total,
         "months": months, "conflicts": conflicts},
    )
    return {"applied": total, "months": months, "conflicts": conflicts}


def _shift_interval(day_text: str, item: dict[str, Any]):
    try:
        start = datetime.combine(date.fromisoformat(day_text), time.fromisoformat(item["start"]))
        end = datetime.combine(date.fromisoformat(day_text), time.fromisoformat(item["end"]))
    except (KeyError, TypeError, ValueError):
        return None
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _work_for_person(entries_by_month: dict[str, list[dict[str, Any]]], employee_id: int):
    shifts = []
    for month_entries in entries_by_month.values():
        for row in month_entries:
            if row.get("employee_id") != employee_id:
                continue
            for day, item in (row.get("days") or {}).items():
                if item.get("kind") == "work":
                    interval = _shift_interval(day, item)
                    if interval:
                        shifts.append((interval[0], interval[1], item, row))
    return sorted(shifts, key=lambda shift: shift[0])


def _candidate_reasons(store, employee_id: int, day_text: str, section: str,
                       start: str, end: str, entries_by_month: dict[str, list[dict[str, Any]]],
                       required_skill: str | list[str] = "") -> list[str]:
    reasons = []
    person = next((item for item in store.people() if item["id"] == employee_id), None)
    if not person or not person["active"]:
        return ["Personel etkin değil veya bulunamadı."]
    profile = store.employee_profile(employee_id)
    preferences = profile.get("preferences") or {}
    target_day = date.fromisoformat(day_text)
    if profile.get("hire_date") and day_text < profile["hire_date"]:
        reasons.append("Vardiya tarihi işe girişten önce.")
    if profile.get("exit_date") and day_text > profile["exit_date"]:
        reasons.append("Vardiya tarihi işten ayrılıştan sonra.")
    skills = {str(item).strip().casefold() for item in profile.get("skills", [])}
    required_skills = [required_skill] if isinstance(required_skill, str) else required_skill
    for skill in required_skills:
        if skill and skill.casefold() not in skills:
            reasons.append(f"Gerekli yetkinlik yok: {skill}.")
    if target_day.weekday() in preferences.get("unavailable_weekdays", []):
        reasons.append("Personel bu hafta günü için uygun olmadığını belirtmiş.")
    for interval in preferences.get("unavailable_ranges", []):
        if interval.get("start", "") <= day_text <= interval.get("end", ""):
            reasons.append("Tarih personelin uygun olmadığı aralıkta.")
            break
    target_interval = _shift_interval(day_text, {"start": start, "end": end})
    if target_interval is None:
        return reasons + ["Vardiya saati geçersiz."]
    start_dt, end_dt = target_interval
    if preferences.get("no_night") and compliance.night_minutes(start_dt, end_dt) > 0:
        reasons.append("Gece vardiyası tercihiyle çakışıyor.")
    if preferences.get("no_weekend") and target_day.weekday() >= 5:
        reasons.append("Hafta sonu çalışmama tercihiyle çakışıyor.")
    touched_dates = [target_day]
    if end_dt.date() != target_day and end_dt.time() != time.min:
        touched_dates.append(end_dt.date())
    touched_months = {day.strftime("%Y-%m") for day in touched_dates}
    linked = [
        item for month in touched_months
        for item in calendar_requests(store, month)
        if item["status"] == "approved" and item["employee_id"] == employee_id
    ]
    for item in linked:
        if item["start"] in {day.isoformat() for day in touched_dates}:
            reasons.append("Onaylı bağlı izin bu günü kapsıyor.")
            break
    for item in store.leave_requests():
        if (item.get("employee_id") == employee_id and item.get("status") == "approved"
                and any(item["start"] <= day.isoformat() <= item["end"] for day in touched_dates)):
            reasons.append("Onaylı izin talebi vardiyayla çakışıyor.")
            break
    events = store.attendance_events(touched_dates[0].isoformat(), touched_dates[-1].isoformat())
    if any(item["employee_id"] == employee_id for item in events):
        reasons.append("Personelin izin/rapor kaydı vardiyayla çakışıyor.")
    for row in [row for entries in entries_by_month.values() for row in entries
                if row.get("employee_id") == employee_id]:
        existing = (row.get("days") or {}).get(day_text)
        if existing:
            reasons.append(f"Gün çizelgede dolu ({existing.get('kind', 'kayıt')}).")
            break
    min_rest = store.schedule_validation_settings().get("minimum_rest_hours", 0)
    for prior_start, prior_end, _, _ in _work_for_person(entries_by_month, employee_id):
        if prior_start < end_dt and prior_end > start_dt:
            reasons.append("Mevcut vardiyayla saat çakışması var.")
            break
        gap = (start_dt - prior_end).total_seconds() / 3600
        reverse_gap = (prior_start - end_dt).total_seconds() / 3600
        if min_rest and ((0 <= gap < min_rest) or (0 <= reverse_gap < min_rest)):
            reasons.append(f"Asgari {min_rest:g} saat dinlenme aralığı sağlanmıyor.")
            break
    return reasons


def _warning_signatures(entries_by_month: dict[str, list[dict[str, Any]]],
                        minimum_rest_hours: float) -> set[tuple[Any, ...]]:
    by_person: dict[int, list[tuple[datetime, datetime, str]]] = {}
    for month, entries in entries_by_month.items():
        for row in entries:
            employee_id = row.get("employee_id")
            for day, item in (row.get("days") or {}).items():
                if item.get("kind") != "work":
                    continue
                interval = _shift_interval(day, item)
                if interval:
                    by_person.setdefault(employee_id, []).append((*interval, day))
    warnings = set()
    for employee_id, shifts in by_person.items():
        shifts.sort(key=lambda item: item[0])
        for index, (start, end, day) in enumerate(shifts):
            for next_start, next_end, next_day in shifts[index + 1:]:
                if next_start >= end and minimum_rest_hours <= 0:
                    break
                if next_start < end:
                    warnings.add(("overlap", employee_id, day, next_day))
                elif minimum_rest_hours and (next_start - end).total_seconds() < minimum_rest_hours * 3600:
                    warnings.add(("rest", employee_id, day, next_day))
    return warnings


def _schedules_around(store, month: str) -> dict[str, list[dict[str, Any]]]:
    periods = scheduling.adjacent_months(month)
    return {period: store.schedule_month(period)["entries"] for period in periods}


def _candidate_hour_projection(employee_id: int, target_interval: tuple[datetime, datetime],
                               entries_by_month: dict[str, list[dict[str, Any]]],
                               week_start: int, weekly_limit_hours: float) -> dict[str, Any]:
    target_start, target_end = target_interval
    existing = _work_for_person(entries_by_month, employee_id)
    weeks = []
    cursor = target_start
    while cursor < target_end:
        week_day = cursor.date() - timedelta(days=(cursor.weekday() - week_start) % 7)
        week_begin = datetime.combine(week_day, time.min)
        week_end = week_begin + timedelta(days=7)
        before_minutes = sum(
            max(0, int((min(shift_end, week_end) - max(shift_start, week_begin)).total_seconds() // 60))
            for shift_start, shift_end, _, _ in existing
        )
        added_minutes = max(
            0, int((min(target_end, week_end) - max(target_start, week_begin)).total_seconds() // 60)
        )
        before_hours = round(before_minutes / 60, 2)
        after_hours = round((before_minutes + added_minutes) / 60, 2)
        weeks.append({
            "start": week_day.isoformat(),
            "end": (week_day + timedelta(days=6)).isoformat(),
            "scheduled_hours_before": before_hours,
            "projected_hours": after_hours,
            "configured_limit_hours": weekly_limit_hours or None,
            "limit_assessment": (
                "unconfigured" if not weekly_limit_hours else
                "exceeds_configured_limit" if after_hours > weekly_limit_hours else "within_configured_limit"
            ),
        })
        cursor = week_end
    unknowns = [
        "Yalnız kaydedilmiş vardiya çizelgesi ve önerilen ek vardiya hesaba katıldı; kaydedilmemiş taslaklar dahil değil.",
        "Vardiya saat aralıkları toplandı; mola süresi düşülmedi.",
    ]
    if not weekly_limit_hours:
        unknowns.append("Haftalık saat sınırı yapılandırılmamış; sınır değerlendirmesi yapılmadı.")
    return {"weeks": weeks, "unknowns": unknowns}


def _replacement_candidates(store, payload: dict[str, Any]) -> dict[str, Any]:
    required = {"employee_id", "date", "section", "start", "end"}
    if not isinstance(payload, dict) or not required.issubset(payload) or not set(payload) <= required | {"exclude_employee_id"}:
        raise ValueError("Yedek personel arama bilgileri geçersiz.")
    source_id = _strict_int(payload["employee_id"], "Personel seçimi")
    day = _strict_date(payload["date"], "Vardiya tarihi").isoformat()
    section = payload["section"]
    if not isinstance(section, str) or section not in scheduling.section_names():
        raise ValueError("Bölüm seçimi geçersiz.")
    start, end = payload["start"], payload["end"]
    if not isinstance(start, str) or not isinstance(end, str):
        raise ValueError("Vardiya saatleri geçersiz.")
    try:
        start = scheduling._validate_time(start)
        end = scheduling._validate_time(end)
    except ValueError as exc:
        raise ValueError("Vardiya saatleri geçersiz.") from exc
    if start == end:
        raise ValueError("Vardiya saatleri aynı olamaz.")
    excluded = payload.get("exclude_employee_id", source_id)
    excluded = _strict_int(excluded, "Dışlanacak personel")
    requirements = [rule["skill"] for rule in store.planning_settings()["skill_requirements"]
                    if not rule["section"] or rule["section"].casefold() == section.casefold()]
    checked_rules = [
        "Etkinlik ve işe giriş/çıkış tarihleri",
        "Bölüm için gereken yetkinlikler",
        "Çalışılamayan hafta günleri ve tarih aralıkları",
        "Gece ve hafta sonu çalışma tercihleri",
        "Onaylı izin, rapor ve devamsızlık kayıtları",
        "Mevcut vardiya çakışmaları ve tanımlı dinlenme süresi",
        "Kayıtlı vardiyalarla haftalık saat toplamı",
    ]
    schedules = _schedules_around(store, day[:7])
    rules = scheduling.normalize_overtime_settings(store.overtime_settings(day[:7]))
    candidates = []
    for person in store.people(active_only=True):
        if person["id"] == excluded:
            continue
        reasons = _candidate_reasons(store, person["id"], day, section, start, end, schedules, requirements)
        candidates.append({"employee_id": person["id"], "name": person["name"],
                           "eligible": not reasons, "reasons": reasons,
                           "hour_projection": _candidate_hour_projection(
                               person["id"], _shift_interval(day, {"start": start, "end": end}),
                               schedules, rules["week_start"], rules["weekly_limit_hours"],
                           )})
    candidates.sort(key=lambda item: (not item["eligible"], len(item["reasons"]), item["name"].casefold()))
    return {
        "date": day, "section": section, "required_skills": requirements,
        "checked_rules": checked_rules, "candidates": candidates,
    }


def leave_impact(store, month: str, request_id: str,
                 alternative_starts: list[str] | None = None,
                 alternative_ends: list[str] | None = None) -> dict[str, Any]:
    month = scheduling.validate_month(month)
    row = store.connection.execute(
        "SELECT * FROM linked_leave_requests WHERE id=?", (_request_id(request_id),),
    ).fetchone()
    if row is None or row["status"] != "pending":
        raise ValueError("Etki önizlemesi için bekleyen bir izin talebi seçin.")
    item = _request_dict(row)
    employee_id = item["employee_id"]

    def calculate(selected_days):
        dates = {day.isoformat() for day in selected_days if day.strftime("%Y-%m") == month}
        schedules = _schedules_around(store, month)
        entries = [entry for rows in schedules.values() for entry in rows]
        removed = set()
        touched = set()
        shifts = []
        for schedule_row in schedules.get(month, []):
            if schedule_row["employee_id"] != employee_id:
                continue
            for day in sorted(dates & schedule_row["days"].keys()):
                shift = schedule_row["days"][day]
                if shift["kind"] != "work":
                    continue
                section = schedule_row["section"]
                removed.add((section, day))
                start, end = scheduling._time_interval(day, shift["start"], shift["end"])
                touched.add((start.date(), section))
                if end.date() != start.date() and end.time() != time.min:
                    touched.add((end.date(), section))
                candidates = _replacement_candidates(store, {
                    "employee_id": employee_id, "date": day, "section": section,
                    "start": shift["start"], "end": shift["end"],
                })
                eligible = [person for person in candidates["candidates"] if person["eligible"]]
                shifts.append({"date": day, "section": section, "start": shift["start"], "end": shift["end"],
                               "candidate_count": len(eligible), "candidates": eligible[:10]})
        proposed = [{**schedule_row, "days": {day: shift for day, shift in schedule_row["days"].items()
                                              if (schedule_row["section"], day) not in removed}}
                    if schedule_row["employee_id"] == employee_id else schedule_row for schedule_row in entries]
        windows = store.compliance_settings()["min_staffing_windows"]
        gaps = []
        for day, section in sorted(touched):
            selected = [window for window in windows if window["section"] == section]
            before = compliance.hourly_staffing(day, entries, selected)
            after = compliance.hourly_staffing(day, proposed, selected)
            for original, projected in zip(before, after):
                old, new = original["gaps"], projected["gaps"]
                edges = sorted({edge for gap in old + new for edge in (gap["start"], gap["end"])})
                for start, end in zip(edges, edges[1:]):
                    previous = next((gap["shortfall"] for gap in old if gap["start"] <= start < gap["end"]), 0)
                    expected = next((gap["shortfall"] for gap in new if gap["start"] <= start < gap["end"]), 0)
                    if expected > previous:
                        gaps.append({"date": day.isoformat(), "section": section, "start": start, "end": end,
                                     "before": previous, "after": expected})
        return {
            "requested_days": len(dates), "shifts": shifts, "gaps": gaps,
            "has_windows": any(window["section"] in {section for _, section in touched} for window in windows),
        }

    result = {"month": month, **calculate(_request_days(item))}
    starts = alternative_starts or []
    ends = alternative_ends or []
    if len(starts) != len(ends) or len(starts) > 3:
        raise ValueError("En fazla üç alternatif izin aralığı birlikte karşılaştırılabilir.")
    if starts:
        people_ids = {person["id"] for person in store.people()}
        alternatives = []
        for index, (start, end) in enumerate(zip(starts, ends), 1):
            alternative = _normalize_request({
                "employee_id": employee_id, "pattern": item["pattern"],
                "start": start, "end": end, "weekdays": item["weekdays"],
                "kind": item["kind"], "note": item["note"],
            }, people_ids, create=False)
            alternatives.append({"label": f"Alternatif {index}", "start": alternative["start"],
                                 "end": alternative["end"], **calculate(_request_days(alternative))})
        result["alternatives"] = alternatives
    return result


def hourly_leave_preview(store, payload: Any) -> dict[str, Any]:
    required = {"employee_id", "date", "start", "end"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("Saatlik izin önizleme alanları geçersiz.")
    employee_id = _strict_int(payload["employee_id"], "Personel seçimi", 1)
    day = _strict_date(payload["date"], "İzin tarihi")
    people = {person["id"]: person for person in store.people()}
    if employee_id not in people:
        raise ValueError("Personel bulunamadı.")
    shift = next((
        entry["days"][day.isoformat()]
        for entry in store.schedule_month(day.strftime("%Y-%m"))["entries"]
        if int(entry["employee_id"]) == employee_id
        and (entry.get("days") or {}).get(day.isoformat(), {}).get("kind") == "work"
    ), None)
    if shift is None:
        raise ValueError("Seçilen gün için kayıtlı çalışma vardiyası yok.")
    shift_interval = _shift_interval(day.isoformat(), shift)
    if shift_interval is None:
        raise ValueError("Kayıtlı vardiya saatleri geçersiz.")
    start = scheduling._validate_time(payload["start"])
    end = scheduling._validate_time(payload["end"])
    leave_interval = _shift_interval(day.isoformat(), {"start": start, "end": end})
    if leave_interval is None or leave_interval[0] < shift_interval[0] or leave_interval[1] > shift_interval[1]:
        raise ValueError("Saatlik izin aralığı vardiyanın içinde kalmalıdır.")
    remaining = []
    if shift_interval[0] < leave_interval[0]:
        remaining.append({
            "start": shift_interval[0].isoformat(timespec="minutes"),
            "end": leave_interval[0].isoformat(timespec="minutes"),
        })
    if leave_interval[1] < shift_interval[1]:
        remaining.append({
            "start": leave_interval[1].isoformat(timespec="minutes"),
            "end": shift_interval[1].isoformat(timespec="minutes"),
        })
    rule = store._setting("hourly_leave_attendance_rule", {})
    configured = isinstance(rule, dict) and bool(rule.get("description"))
    attendance_month = store.attendance_month(day.strftime("%Y-%m"))
    manual_code = attendance_month["overrides"].get(day.isoformat(), {}).get(str(employee_id), "")
    return {
        "employee_id": employee_id, "name": people[employee_id]["name"], "date": day.isoformat(),
        "shift": {"start": shift_interval[0].isoformat(timespec="minutes"),
                  "end": shift_interval[1].isoformat(timespec="minutes")},
        "leave": {"start": leave_interval[0].isoformat(timespec="minutes"),
                  "end": leave_interval[1].isoformat(timespec="minutes"),
                  "minutes": int((leave_interval[1] - leave_interval[0]).total_seconds() // 60)},
        "remaining_intervals": remaining, "manual_attendance_code": manual_code,
        "attendance_rule": {
            "configured": configured,
            "description": rule.get("description", "") if configured else "",
            "effective_date": rule.get("effective_date", "") if configured else "",
            "notice": (
                "Kurumca girilmiş kural yalnızca açıklama olarak gösterilir; bu önizleme puantajı değiştirmez."
                if configured else
                "Kurumun saatlik izin puantaj karşılığı yapılandırılmamış; hukuki veya bordro varsayımı yapılmadı."
            ),
        },
        "saved": False,
    }


def save_hourly_leave_rule(store, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"description", "effective_date"}:
        raise ValueError("Saatlik izin puantaj kuralı bilgileri geçersiz.")
    description = payload["description"]
    effective_date = payload["effective_date"]
    if not isinstance(description, str) or len(description.strip()) > 300:
        raise ValueError("Kural açıklaması en fazla 300 karakter olmalıdır.")
    description = " ".join(description.split())
    if description:
        effective = _strict_date(effective_date, "Kural yürürlük tarihi").isoformat()
        rule = {"description": description, "effective_date": effective}
    elif effective_date not in ("", None):
        raise ValueError("Kural kaldırılırken yürürlük tarihi boş bırakılmalıdır.")
    else:
        rule = {}
    store._write_setting("hourly_leave_attendance_rule", rule)
    store.record_change(
        "linked_leave.hourly_rule",
        "Saatlik izin puantaj açıklaması güncellendi",
        {"configured": bool(rule), "effective_date": rule.get("effective_date", "")},
    )
    return {"configured": bool(rule), **rule}


def _fairness_proposals(store, month: str) -> dict[str, Any]:
    month = scheduling.validate_month(month)
    schedules = _schedules_around(store, month)
    people = store.people(active_only=True)
    names = {item["id"]: item["name"] for item in people}
    report = planning.fairness(schedules, names)
    fairness_by_person = {item["employee_id"]: item for item in report["people"]}
    loads = {
        employee_id: fairness_by_person.get(employee_id, {"hours": 0.0, "burden": 0.0})
        for employee_id in names
    }
    minimum_rest = store.schedule_validation_settings().get("minimum_rest_hours", 0)
    baseline_warnings = _warning_signatures(schedules, minimum_rest)
    requirements = store.planning_settings()["skill_requirements"]
    profiles = store.employee_profiles()
    compliance_settings = store.compliance_settings()

    def planning_warnings(draft):
        warnings = set()
        all_entries = [row for entries in draft.values() for row in entries]
        for period, entries in draft.items():
            records = planning.skill_items(period, entries, names, profiles, requirements)
            records += planning.preference_items(period, entries, names, profiles)
            records += compliance.compliance_items(
                period, all_entries, names, profiles, compliance_settings, minimum_rest,
            )
            records += compliance.staffing_items(
                period, all_entries, compliance_settings.get("min_staffing", {}),
                compliance_settings.get("min_staffing_windows", []),
            )
            records += planning.leave_request_items(
                period, entries, names, store.leave_requests() + calendar_requests(store, period),
                store.planning_settings()["daily_limit"],
            )
            warnings.update(
                (record["type"], record.get("employee_id"), record["date"], record["message"])
                for record in records
            )
        return warnings

    def load_score(fairness):
        rows = {row["employee_id"]: row for row in fairness["people"]}
        return sum(
            rows.get(employee_id, {}).get("hours", 0) ** 2
            + rows.get(employee_id, {}).get("burden", 0) ** 2
            for employee_id in names
        )

    baseline_planning_warnings = planning_warnings(schedules)
    baseline_score = load_score(report)
    current_entries = schedules.get(month, [])
    proposals = []
    donors = sorted(
        (row for row in current_entries if row.get("employee_id") in loads),
        key=lambda row: (loads[row["employee_id"]]["hours"], loads[row["employee_id"]]["burden"]),
        reverse=True,
    )
    for donor in donors:
        for day, item in sorted((donor.get("days") or {}).items()):
            if not day.startswith(month) or item.get("kind") != "work":
                continue
            section = donor.get("section", "")
            required = [rule["skill"] for rule in requirements
                        if not rule["section"] or rule["section"].casefold() == section.casefold()]
            for candidate in sorted(
                people,
                key=lambda person: (
                    loads[person["id"]]["hours"], loads[person["id"]]["burden"], person["name"].casefold(),
                ),
            ):
                if candidate["id"] == donor["employee_id"]:
                    continue
                donor_load = loads[donor["employee_id"]]
                candidate_load = loads[candidate["id"]]
                if (candidate_load["hours"], candidate_load["burden"]) >= (
                    donor_load["hours"], donor_load["burden"],
                ):
                    continue
                reasons = _candidate_reasons(
                    store, candidate["id"], day, section, item["start"], item["end"], schedules, required,
                )
                if reasons:
                    continue
                candidate_row = next((row for row in current_entries
                                      if row.get("employee_id") == candidate["id"]
                                      and row.get("section") == section), None)
                if candidate_row is None:
                    continue
                draft = json.loads(json.dumps(schedules, ensure_ascii=False))
                source = next(row for row in draft[month] if row["row_id"] == donor["row_id"])
                target = next(row for row in draft[month] if row["row_id"] == candidate_row["row_id"])
                source["days"].pop(day, None)
                target.setdefault("days", {})[day] = item
                new_warnings = _warning_signatures(draft, minimum_rest) - baseline_warnings
                if new_warnings or planning_warnings(draft) - baseline_planning_warnings:
                    continue
                after = planning.fairness(draft, names)
                if load_score(after) >= baseline_score:
                    continue
                proposals.append({
                    "date": day, "section": section,
                    "from": {"employee_id": donor["employee_id"], "name": names[donor["employee_id"]]},
                    "to": {"employee_id": candidate["id"], "name": candidate["name"]},
                    "shift": {"start": item["start"], "end": item["end"]},
                    "reason": "Yükü daha düşük ve uygun personel; mevcut kontrol uyarılarına yenisi eklenmiyor.",
                    "before": report["averages"], "after": after["averages"],
                })
                if len(proposals) >= 12:
                    return {"month": month, "report": report, "proposals": proposals}
                break
            if len(proposals) >= 12:
                break
    return {"month": month, "report": report, "proposals": proposals}


def forecast_annual_leave(store, month, balances):
    _, cutoff = _month_bounds(month)
    year_end = date(cutoff.year, 12, 31)
    days_by_employee = {}
    for row in store.connection.execute(
        "SELECT * FROM linked_leave_requests WHERE kind='annual_leave' AND status IN ('approved','pending') "
        "AND end_date>? AND start_date<=?", (cutoff.isoformat(), year_end.isoformat()),
    ):
        item = _request_dict(row)
        statuses = days_by_employee.setdefault(item["employee_id"], {"approved": set(), "pending": set()})
        statuses[item["status"]].update(day for day in _request_days(item) if cutoff < day <= year_end)
    result = []
    for person in balances["people"]:
        days = days_by_employee.get(person["employee_id"])
        if not days:
            continue
        approved = len(days["approved"])
        pending = len(days["pending"] - days["approved"])
        remaining = person["annual_leave_remaining_days"] if person["has_opening"] else None
        result.append({"name": person["name"], "remaining": remaining, "approved": approved, "pending": pending,
                       "projected": round(remaining - approved, 2) if remaining is not None else None})
    return {"from": cutoff.isoformat(), "through": year_end.isoformat(), "people": result}


def handle_get(handler, path: str, query) -> bool:
    if path == "/api/linked-leave/hourly-rule":
        with handler._store() as store:
            rule = store._setting("hourly_leave_attendance_rule", {})
            configured = isinstance(rule, dict) and bool(rule.get("description"))
            handler._json({"configured": configured, **(rule if configured else {})})
        return True
    if path == "/api/linked-leave/impact":
        with handler._store() as store:
            store.connection.execute("BEGIN")
            try:
                result = leave_impact(
                    store, query.get("month", [""])[0], query.get("request_id", [""])[0],
                    query.get("alternative_start", []), query.get("alternative_end", []),
                )
            finally:
                store.connection.rollback()
            handler._json(result)
        return True
    if path == "/api/linked-leave/forecast":
        month = scheduling.validate_month(query.get("month", [""])[0])
        with handler._store() as store:
            handler._json(forecast_annual_leave(store, month, handler._balance_summary(store, month)))
        return True
    if path != "/api/linked-leave/requests":
        return False
    month_value = query.get("month", [None])[0]
    with handler._store() as store:
        items = _all_requests(store.connection)
        if month_value:
            month = scheduling.validate_month(month_value)
            first, last = _month_bounds(month)
            items = [item for item in items if date.fromisoformat(item["start"]) <= last
                     and date.fromisoformat(item["end"]) >= first]
        applied_months = {}
        for row in store.connection.execute(
            "SELECT DISTINCT request_id, month FROM linked_leave_applications "
            "WHERE undone_at IS NULL ORDER BY month",
        ):
            applied_months.setdefault(row["request_id"], []).append(row["month"])
        for item in items:
            item["applied_months"] = applied_months.get(item["id"], [])
        handler._json({"requests": items, "kinds": sorted(_LEAVE_KINDS)})
    return True


def _undo_request(store, payload):
    if not isinstance(payload, dict) or set(payload) != {
        "request_id", "request_revision", "schedule_revisions", "confirmed",
    } or payload["confirmed"] is not True:
        raise ValueError("İzin onayını ve ilişkili vardiyaları geri almak için açık onay gereklidir.")
    request_id = _request_id(payload["request_id"])
    expected = _strict_int(payload["request_revision"], "İzin sürümü")
    supplied = payload["schedule_revisions"]
    if not isinstance(supplied, dict) or len(supplied) > 24 or any(
        not isinstance(month, str) or not isinstance(revision, str) or len(revision) > 128
        for month, revision in supplied.items()
    ):
        raise ValueError("Geri alınacak dönem sürümleri geçersiz.")
    connection = store.connection
    store._backup_before_change()
    connection.execute("BEGIN IMMEDIATE")
    try:
        request = connection.execute(
            "SELECT * FROM linked_leave_requests WHERE id=?", (request_id,),
        ).fetchone()
        if request is None or request["status"] != "approved" or request["revision"] != expected:
            raise ValueError("Onaylı izin kaydı değişmiş; listeyi yenileyin.")
        marks = list(connection.execute(
            "SELECT * FROM linked_leave_applications WHERE request_id=? AND undone_at IS NULL ORDER BY month,day",
            (request_id,),
        ))
        months = sorted({mark["month"] for mark in marks})
        if set(months) != set(supplied):
            raise ValueError("İznin uygulandığı dönemler değişmiş; listeyi yenileyin.")
        store.assert_periods_unlocked(months)
        revisions = {}
        for month in months:
            current = store.schedule_month(month)
            if current["revision"] != supplied[month]:
                raise ValueError("Vardiya kaydı değişmiş; hiçbir dönem geri alınmadı.")
            entries = current["entries"]
            by_id = {entry["row_id"]: entry for entry in entries}
            for mark in (item for item in marks if item["month"] == month):
                row = by_id.get(mark["row_id"])
                if row is None or row.get("employee_id") != mark["employee_id"] \
                        or row.get("days", {}).get(mark["day"]) != json.loads(mark["after_json"]):
                    raise ValueError("Bağlı hücre sonradan değişmiş; izin onayı ve tüm dönemler korundu.")
                before = json.loads(mark["before_json"])
                if before is None:
                    row["days"].pop(mark["day"], None)
                else:
                    row["days"][mark["day"]] = before
            clean = scheduling.normalize_schedule_entries(month, entries, store.people())
            source = connection.execute(
                "SELECT payload FROM schedule_months WHERE period=?", (month,),
            ).fetchone()
            connection.execute(
                "INSERT INTO archive_revisions(kind,period,payload,revision,recorded_at) "
                "VALUES ('schedule',?,?,?,?)",
                (month, source["payload"], current["revision"], datetime.now(timezone.utc).isoformat()),
            )
            revision = secrets.token_hex(16)
            connection.execute(
                "UPDATE schedule_months SET payload=?,revision=? WHERE period=?",
                (json.dumps(clean, ensure_ascii=False, separators=(",", ":")), revision, month),
            )
            revisions[month] = revision
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection.execute(
            "UPDATE linked_leave_applications SET undone_at=? WHERE request_id=? AND undone_at IS NULL",
            (now, request_id),
        )
        connection.execute(
            "UPDATE linked_leave_requests SET status='pending',revision=revision+1,updated_at=? WHERE id=?",
            (now, request_id),
        )
        store.record_change(
            "linked_leave.undo_request", "İzin onayı ve tüm ilişkili vardiyalar birlikte geri alındı",
            {"request_id": request_id, "months": months, "undone": len(marks)},
        )
        connection.commit()
        return {"undone": len(marks), "schedule_revisions": revisions, "status": "pending"}
    except Exception:
        connection.rollback()
        raise


def handle_write(handler, method: str, path: str) -> bool:
    prefix = "/api/linked-leave/"
    if not path.startswith(prefix):
        return False
    action = path[len(prefix):]
    if action == "hourly-preview" and method == "POST":
        payload = handler._read_json()
        with handler._store() as store:
            handler._json(hourly_leave_preview(store, payload))
        return True
    if action == "hourly-rule" and method == "PUT":
        payload = handler._read_json()
        with handler._store() as store:
            with store.connection:
                handler._json(save_hourly_leave_rule(store, payload))
        return True
    if action == "undo-request" and method == "POST":
        payload = handler._read_json()
        with handler._store() as store:
            handler._json(_undo_request(store, payload))
        return True
    if action == "move-preview" and method == "POST":
        payload = handler._read_json()
        with handler._store() as store:
            preview = preview_request_move(store, payload)
            handler._json({key: value for key, value in preview.items() if not key.startswith("_")})
        return True
    if action == "move" and method == "POST":
        payload = handler._read_json()
        with handler._store() as store:
            handler._json(apply_request_move(store, payload))
        return True
    if action == "requests/bulk" and method == "POST":
        payload = handler._read_json()
        if not isinstance(payload, dict) or set(payload) - {
            "employee_ids", "pattern", "start", "end", "kind", "weekdays", "note",
        } or not {"employee_ids", "pattern", "start", "end", "kind"}.issubset(payload):
            raise ValueError("Toplu izin bilgileri geçersiz.")
        employee_ids = payload["employee_ids"]
        if (not isinstance(employee_ids, list) or not 1 <= len(employee_ids) <= 60
                or any(not isinstance(item, int) or isinstance(item, bool) for item in employee_ids)
                or len(set(employee_ids)) != len(employee_ids)):
            raise ValueError("Toplu izin için 1–60 farklı personel seçilmelidir.")
        with handler._store() as store:
            known_ids = {item["id"] for item in store.people()}
            common = {key: value for key, value in payload.items() if key != "employee_ids"}
            requests = [
                _normalize_request({**common, "employee_id": employee_id}, known_ids, create=True)
                for employee_id in employee_ids
            ]
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            connection = store.connection
            created_ids = [secrets.token_hex(12) for _ in requests]
            connection.execute("BEGIN IMMEDIATE")
            try:
                for request_id, clean in zip(created_ids, requests):
                    connection.execute(
                        "INSERT INTO linked_leave_requests "
                        "(id, employee_id, pattern, start_date, end_date, weekdays, kind, status, note, revision, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, 1, ?, ?)",
                        (request_id, clean["employee_id"], clean["pattern"], clean["start"], clean["end"],
                         json.dumps(clean["weekdays"]), clean["kind"], clean["note"], now, now),
                    )
                    store.record_change(
                        "linked_leave.create", "Toplu bağlı izin talebi oluşturuldu",
                        {"request_id": request_id, "employee_id": clean["employee_id"],
                         "start": clean["start"], "end": clean["end"], "status": "pending"},
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            rows = [
                _request_dict(connection.execute(
                    "SELECT * FROM linked_leave_requests WHERE id=?", (request_id,),
                ).fetchone())
                for request_id in created_ids
            ]
            handler._json({"requests": rows}, 201)
        return True
    if action == "requests" and method == "POST":
        payload = handler._read_json()
        with handler._store() as store:
            clean = _normalize_request(payload, {item["id"] for item in store.people()}, create=True)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            request_id = secrets.token_hex(12)
            connection = store.connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO linked_leave_requests "
                    "(id, employee_id, pattern, start_date, end_date, weekdays, kind, status, note, revision, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, 1, ?, ?)",
                    (request_id, clean["employee_id"], clean["pattern"], clean["start"], clean["end"],
                     json.dumps(clean["weekdays"]), clean["kind"], clean["note"], now, now),
                )
                store.record_change(
                    "linked_leave.create", "Bağlı izin talebi oluşturuldu",
                    {"request_id": request_id, "employee_id": clean["employee_id"],
                     "start": clean["start"], "end": clean["end"], "status": "pending"},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            row = connection.execute("SELECT * FROM linked_leave_requests WHERE id=?", (request_id,)).fetchone()
            handler._json({"request": _request_dict(row)}, 201)
        return True
    if action.startswith("requests/"):
        parts = action.split("/")
        if len(parts) not in (2, 3):
            raise ValueError("Bağlı izin yolu geçersiz.")
        request_id = _request_id(unquote(parts[1]))
        if len(parts) == 3 and parts[2] == "approve" and method == "POST":
            payload = handler._read_json()
            if (set(payload) != {"revision", "status"} or not isinstance(payload["status"], str)
                    or payload["status"] not in _STATUSES):
                raise ValueError("İzin durumu isteği geçersiz.")
            expected = _strict_int(payload["revision"], "Kayıt sürümü")
            with handler._store() as store:
                connection = store.connection
                store._backup_before_change()
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute(
                        "SELECT * FROM linked_leave_requests WHERE id=?", (request_id,)
                    ).fetchone()
                    if row is None:
                        raise ValueError("Bağlı izin kaydı bulunamadı.")
                    if int(row["revision"]) != expected:
                        raise ValueError("İzin kaydı değişmiş; yenileyip tekrar deneyin.")
                    status = payload["status"]
                    if status == "pending" and row["status"] != "pending":
                        raise ValueError("İzin onaylandıktan sonra beklemeye döndürülemez.")
                    if status == "approved" and row["status"] != "pending":
                        raise ValueError("Yalnız bekleyen izin onaylanabilir.")
                    if status == "cancelled" and row["status"] == "cancelled":
                        raise ValueError("İzin zaten iptal edilmiş.")
                    if status == "cancelled" and connection.execute(
                        "SELECT 1 FROM linked_leave_applications WHERE request_id=? AND undone_at IS NULL",
                        (request_id,),
                    ).fetchone():
                        raise ValueError("Uygulanmış izin önce ilişkili geri alma ile kaldırılmalıdır.")
                    connection.execute(
                        "UPDATE linked_leave_requests SET status=?, revision=revision+1, updated_at=? WHERE id=?",
                        (status, datetime.now(timezone.utc).isoformat(timespec="seconds"), request_id),
                    )
                    store.record_change("linked_leave.status", "Bağlı izin durumu güncellendi",
                                        {"request_id": request_id, "status": status})
                    application = {"applied": 0, "months": {}, "conflicts": []}
                    if status == "approved":
                        approved = _request_dict(connection.execute(
                            "SELECT * FROM linked_leave_requests WHERE id=?", (request_id,),
                        ).fetchone())
                        application = _apply_approved_in_transaction(store, approved)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                updated = connection.execute(
                    "SELECT * FROM linked_leave_requests WHERE id=?", (request_id,)
                ).fetchone()
                handler._json({"request": _request_dict(updated), **application})
            return True
        if len(parts) == 2 and method == "PUT":
            payload = handler._read_json()
            if set(payload) != {"revision", "request"}:
                raise ValueError("İzin güncelleme isteği geçersiz.")
            expected = _strict_int(payload["revision"], "Kayıt sürümü")
            with handler._store() as store:
                clean = _normalize_request(
                    payload["request"], {item["id"] for item in store.people()}, create=False,
                )
                connection = store.connection
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute(
                        "SELECT * FROM linked_leave_requests WHERE id=?", (request_id,)
                    ).fetchone()
                    if row is None:
                        raise ValueError("Bağlı izin kaydı bulunamadı.")
                    if int(row["revision"]) != expected:
                        raise ValueError("İzin kaydı değişmiş; yenileyip tekrar deneyin.")
                    if row["status"] == "cancelled":
                        raise ValueError("İptal edilmiş izin değiştirilemez.")
                    if connection.execute(
                        "SELECT 1 FROM linked_leave_applications WHERE request_id=? AND undone_at IS NULL",
                        (request_id,),
                    ).fetchone():
                        raise ValueError("Uygulanmış izin önce ilişkili geri alma ile kaldırılmalıdır.")
                    connection.execute(
                        "UPDATE linked_leave_requests SET employee_id=?, pattern=?, start_date=?, end_date=?, "
                        "weekdays=?, kind=?, note=?, status='pending', revision=revision+1, updated_at=? WHERE id=?",
                        (clean["employee_id"], clean["pattern"], clean["start"], clean["end"],
                         json.dumps(clean["weekdays"]), clean["kind"], clean["note"],
                         datetime.now(timezone.utc).isoformat(timespec="seconds"), request_id),
                    )
                    store.record_change("linked_leave.update", "Bağlı izin talebi güncellendi",
                                        {"request_id": request_id, "status": "pending"})
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                updated = connection.execute(
                    "SELECT * FROM linked_leave_requests WHERE id=?", (request_id,)
                ).fetchone()
                handler._json({"request": _request_dict(updated)})
            return True
    if method != "POST":
        return False
    payload = handler._read_json()
    with handler._store() as store:
        if action == "preview":
            if set(payload) != {"month", "request_ids"} or not isinstance(payload["request_ids"], list) or not payload["request_ids"]:
                raise ValueError("Önizleme isteği geçersiz.")
            month = scheduling.validate_month(payload["month"])
            request_ids = [_request_id(value) for value in payload["request_ids"]]
            if len(request_ids) > 100:
                raise ValueError("Tek önizlemede en fazla 100 izin seçilebilir.")
            requests = _month_requests(store.connection, month, request_ids, approved_only=True)
            if len(requests) != len(set(request_ids)):
                raise ValueError("Seçilen onaylı izin bulunamadı veya bu ayı kapsamıyor.")
            entries, revision = _schedule_revision(store, month)
            plan = _plan_month(store, month, entries, requests)
            handler._json({"month": month, "schedule_revision": revision, **plan,
                           "preview_hash": _preview_hash(month, revision, requests, plan)})
            return True
        if action == "apply":
            expected_keys = {"month", "request_ids", "schedule_revision", "preview_hash", "confirm_conflicts"}
            if not isinstance(payload, dict) or set(payload) != expected_keys or not isinstance(payload["request_ids"], list):
                raise ValueError("İzin uygulama isteği geçersiz.")
            month = scheduling.validate_month(payload["month"])
            request_ids = [_request_id(value) for value in payload["request_ids"]]
            if not request_ids or len(request_ids) > 100:
                raise ValueError("Uygulama için 1-100 izin seçilmelidir.")
            if not isinstance(payload["schedule_revision"], str) or not isinstance(payload["preview_hash"], str):
                raise ValueError("Önizleme sürümü geçersiz.")
            if not isinstance(payload["confirm_conflicts"], bool):
                raise ValueError("Çakışma onayı geçersiz.")
            requests = _month_requests(store.connection, month, request_ids, approved_only=True)
            if len(requests) != len(set(request_ids)):
                raise ValueError("Seçilen onaylı izin bulunamadı veya bu ayı kapsamıyor.")
            entries, revision = _schedule_revision(store, month)
            if revision != payload["schedule_revision"]:
                raise ValueError("Vardiya kaydı değişmiş; yeniden önizleme yapın.")
            plan = _plan_month(store, month, entries, requests)
            digest = _preview_hash(month, revision, requests, plan)
            if digest != payload["preview_hash"]:
                raise ValueError("Önizleme eskimiş; yeni önizleme hazırlayın.")
            if plan["conflicts"] and not payload["confirm_conflicts"]:
                handler._json({"error": "Çakışmalar var; uygulanabilir kayıtları sürdürmek için açık onay gerekir.",
                               "conflicts": plan["conflicts"]}, 409)
                return True
            changed = json.loads(json.dumps(entries, ensure_ascii=False))
            by_row = {row["row_id"]: row for row in changed}
            apps = []
            for placement in plan["placements"]:
                row = by_row.get(placement["row_id"])
                if row is None and placement["create_row"]:
                    row = {"row_id": placement["row_id"], "employee_id": placement["employee_id"],
                           "section": placement["section"], "days": {}}
                    if placement["slot"] is not None:
                        row["slot"] = placement["slot"]
                    changed.append(row)
                    by_row[row["row_id"]] = row
                if row is None or placement["date"] in row.setdefault("days", {}):
                    raise ValueError("Önizleme sırasında çizelge çakışması oluştu; yeniden deneyin.")
                after = {"kind": placement["kind"]}
                row["days"][placement["date"]] = after
                apps.append({**placement, "before": None, "after": after})
            saved_revision = revision
            if apps:
                saved_revision = _replace_month_schedule(
                    store, month, changed, revision, "linked_leave.apply",
                    {"request_ids": sorted(set(request_ids)), "applied": len(apps),
                     "skipped_conflicts": len(plan["conflicts"])}, apps,
                )
            handler._json({"month": month, "schedule_revision": saved_revision,
                           "applied": len(apps), "conflicts": plan["conflicts"],
                           "already_applied": plan["already_applied"]})
            return True
        if action == "undo":
            if set(payload) != {"month", "request_ids", "schedule_revision"}:
                raise ValueError("Bağlı izin geri alma isteği geçersiz.")
            month = scheduling.validate_month(payload["month"])
            request_ids = [_request_id(value) for value in payload["request_ids"]]
            if not request_ids or len(request_ids) > 100 or not isinstance(payload["schedule_revision"], str):
                raise ValueError("Geri alma seçimleri geçersiz.")
            store.assert_periods_unlocked([month])
            entries, revision = _schedule_revision(store, month)
            if revision != payload["schedule_revision"]:
                raise ValueError("Vardiya kaydı değişmiş; geri almadan önce yenileyin.")
            marks = list(store.connection.execute(
                "SELECT * FROM linked_leave_applications WHERE month=? AND undone_at IS NULL "
                f"AND request_id IN ({','.join('?' for _ in request_ids)}) ORDER BY day",
                (month, *request_ids),
            ))
            if not marks:
                raise ValueError("Seçilen izinlere bağlı uygulanmış hücre bulunamadı.")
            current_by_id = {row["row_id"]: row for row in entries}
            for mark in marks:
                row = current_by_id.get(mark["row_id"])
                if row is None or row.get("employee_id") != mark["employee_id"] \
                        or (row.get("days") or {}).get(mark["day"]) != json.loads(mark["after_json"]):
                    raise ValueError("Bağlı hücre sonradan değişmiş; güvenli geri alma yapılmadı.")
            changed = json.loads(json.dumps(entries, ensure_ascii=False))
            changed_by_id = {row["row_id"]: row for row in changed}
            for mark in marks:
                before = json.loads(mark["before_json"])
                if before is None:
                    changed_by_id[mark["row_id"]]["days"].pop(mark["day"], None)
                else:
                    changed_by_id[mark["row_id"]]["days"][mark["day"]] = before
            # Keep empty staff rows; deleting them could discard unrelated schedule structure.
            connection = store.connection
            store._backup_before_change()
            clean = scheduling.normalize_schedule_entries(month, changed, store.people())
            new_revision = secrets.token_hex(16)
            connection.execute("BEGIN IMMEDIATE")
            try:
                store.assert_periods_unlocked([month])
                current = connection.execute(
                    "SELECT payload, revision FROM schedule_months WHERE period=?", (month,)
                ).fetchone()
                if (current["revision"] if current else "") != revision:
                    raise ValueError("Vardiya kaydı değişmiş; güvenli geri alma yapılamadı.")
                if current:
                    connection.execute(
                        "INSERT INTO archive_revisions(kind, period, payload, revision, recorded_at) "
                        "VALUES ('schedule', ?, ?, ?, ?)",
                        (month, current["payload"], revision, datetime.now(timezone.utc).isoformat()),
                    )
                connection.execute(
                    "UPDATE schedule_months SET payload=?, revision=? WHERE period=?",
                    (json.dumps(clean, ensure_ascii=False, separators=(",", ":")), new_revision, month),
                )
                undone_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                connection.executemany(
                    "UPDATE linked_leave_applications SET undone_at=? "
                    "WHERE request_id=? AND month=? AND day=? AND undone_at IS NULL",
                    [(undone_at, mark["request_id"], month, mark["day"]) for mark in marks],
                )
                store.record_change("linked_leave.undo", "Bağlı izin değişiklikleri geri alındı",
                                    {"month": month, "request_ids": sorted(set(request_ids)),
                                     "undone": len(marks), "schedule_revision": new_revision})
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            handler._json({"month": month, "schedule_revision": new_revision, "undone": len(marks)})
            return True
        if action == "candidates":
            handler._json(_replacement_candidates(store, payload))
            return True
        if action == "fairness-proposals":
            if set(payload) != {"month"}:
                raise ValueError("Adil dağılım önerisi isteği geçersiz.")
            handler._json(_fairness_proposals(store, payload["month"]))
            return True
    return False
