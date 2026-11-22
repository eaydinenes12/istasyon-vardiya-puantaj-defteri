from __future__ import annotations

import secrets
from datetime import datetime, timezone

import controls
import operations
import scheduling


def initialize(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS attendance_corrections (
            id TEXT PRIMARY KEY, month TEXT NOT NULL, day TEXT NOT NULL, employee_id INTEGER NOT NULL,
            code TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL, requested_by TEXT NOT NULL,
            created_at TEXT NOT NULL, status TEXT NOT NULL, decided_by TEXT, decided_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS attendance_corrections_pending
            ON attendance_corrections(month,day,employee_id) WHERE status='pending';
    """)


def evidence(handler, store, month, day, employee_id):
    attendance, entries, saved = handler._attendance_for(store, month)
    person = next((row for row in attendance["rows"] if row["employee_id"] == employee_id), None)
    if person is None:
        raise ValueError("Personel seçilen ayın puantajında bulunamadı.")
    plan = [{"section": row["section"], "value": controls.day_label(row["days"][day])}
            for row in entries if row["employee_id"] == employee_id and day in row["days"]]
    key = controls.name_key(person["name"])
    people = store.people()
    matched = [row for row in people if controls.name_key(row["name"]) == key]
    pdks = store._setting("pdks_records", {}).get(month, {})
    punches = [{"in": row["in"], "out": row["out"]} for row in pdks.get("records", [])
               if row["date"] == day and controls.name_key(row["name"]) == key] if len(matched) == 1 else []
    manual = saved["overrides"].get(day, {}).get(str(employee_id))
    imports = [
        {"occurred_at": item["occurred_at"], "actor": item["actor"],
         "file": item["details"].get("file", ""), "codes": item["details"].get("codes")}
        for item in store.change_history(500, action="attendance.import_excel")
        if item["details"].get("month") == month
    ]
    corrections = [
        {key: row[key] for key in ("id", "code", "reason", "requested_by", "created_at",
                                    "status", "decided_by", "decided_at")}
        for row in store.connection.execute(
            "SELECT id,code,reason,requested_by,created_at,status,decided_by,decided_at "
            "FROM attendance_corrections WHERE month=? AND day=? AND employee_id=? "
            "ORDER BY created_at DESC LIMIT 50",
            (month, day, employee_id),
        )
    ]
    schedule_source = store.schedule_month(month)
    history = store.archive_revisions("attendance", month)
    pdks_source = {
        key: pdks.get(key, "") for key in ("file", "imported_at", "actor") if pdks.get(key)
    }
    result = {
        "name": person["name"], "plan": plan, "schedule_revision": schedule_source["revision"],
        "punches": punches, "pdks_source": pdks_source, "ambiguous": len(matched) != 1,
        "code": person["codes"].get(day, ""), "manual": manual,
        "attendance_revision": saved["revision"], "attendance_revisions": history["revisions"][:20],
        "imports": imports, "corrections": corrections,
    }
    source = {key: value for key, value in result.items() if key != "corrections"}
    return {**result, "fingerprint": operations._fingerprint(source)}


def create(handler, store, payload):
    required = {"month", "date", "employee_id", "code", "reason", "fingerprint"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("Puantaj düzeltme isteği geçersiz.")
    month = scheduling.validate_month(payload["month"])
    day, employee_id = payload["date"], payload["employee_id"]
    validate_cell(month, day, employee_id)
    reason = payload["reason"]
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 500:
        raise ValueError("Düzeltme gerekçesi 3–500 karakter olmalıdır.")
    code = scheduling.normalize_attendance_code(payload["code"])
    if not code:
        raise ValueError("Yeni bir puantaj kodu girin.")
    connection = store.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        store.assert_periods_unlocked([month])
        current = evidence(handler, store, month, day, employee_id)
        if current["fingerprint"] != payload["fingerprint"] or code == current["code"]:
            raise ValueError("Kaynak değişti veya yeni kod mevcut kodla aynı.")
        if connection.execute(
            "SELECT 1 FROM attendance_corrections WHERE month=? AND day=? AND employee_id=? AND status='pending'",
            (month, day, employee_id),
        ).fetchone():
            raise ValueError("Bu gün için zaten bekleyen bir düzeltme var.")
        identifier = secrets.token_hex(16)
        connection.execute(
            "INSERT INTO attendance_corrections VALUES (?,?,?,?,?,?,?,?,?,'pending',NULL,NULL)",
            (identifier, month, day, employee_id, code, reason.strip(), current["fingerprint"],
             store.actor or store.user_name(),
             datetime.now(timezone.utc).isoformat()),
        )
        store.record_change("attendance.correction_request", "Puantaj düzeltmesi beklemeye alındı", {"id": identifier})
        connection.commit()
        return {"id": identifier}
    except Exception:
        connection.rollback()
        raise


def validate_cell(month, day, employee_id):
    if type(employee_id) is not int or employee_id <= 0 or day not in {
        value.isoformat() for value in scheduling.month_dates(month)
    }:
        raise ValueError("Personel veya puantaj tarihi geçersiz.")


def list_requests(store, month, *, admin):
    query = "SELECT * FROM attendance_corrections WHERE month=?"
    args = (month,) if admin else (month, store.actor or store.user_name())
    if not admin:
        query += " AND requested_by=?"
    return [dict(row) for row in store.connection.execute(query + " ORDER BY created_at DESC LIMIT 200", args)]


def decide(handler, store, payload):
    if (not isinstance(payload, dict) or set(payload) != {"month", "id", "approve"}
            or type(payload["approve"]) is not bool or not isinstance(payload["id"], str)):
        raise ValueError("Düzeltme kararı geçersiz.")
    month = scheduling.validate_month(payload["month"])
    identifier = payload["id"]

    def update():
        request = store.connection.execute(
            "SELECT * FROM attendance_corrections WHERE id=? AND month=? AND status='pending'", (identifier, month),
        ).fetchone()
        if request is None:
            raise ValueError("Bekleyen düzeltme bulunamadı.")
        if payload["approve"]:
            current = evidence(handler, store, month, request["day"], request["employee_id"])
            if request["evidence"] != current["fingerprint"]:
                raise ValueError("Plan, PDKS veya puantaj değişti; talebi reddedip yeniden oluşturun.")
        status = "approved" if payload["approve"] else "rejected"
        store.connection.execute(
            "UPDATE attendance_corrections SET status=?, decided_by=?, decided_at=? WHERE id=?",
            (status, store.actor or store.user_name(), datetime.now(timezone.utc).isoformat(), identifier),
        )
        if not payload["approve"]:
            store.record_change("attendance.correction_reject", "Puantaj düzeltmesi reddedildi", {"id": identifier})
            return None
        saved = store.attendance_month(month)
        saved.pop("revision")
        saved["overrides"].setdefault(request["day"], {})[str(request["employee_id"])] = request["code"]
        return saved

    if payload["approve"]:
        revision = store._save_month(
            "attendance_months", month, None, store.attendance_month(month)["revision"],
            change_action="attendance.correction_approve", change_summary="Puantaj düzeltmesi onaylandı",
            change_details={"id": identifier}, prepare_payload=update,
        )
        if revision is None:
            raise ValueError("Puantaj yeniden değişti; tekrar inceleyin.")
    else:
        store.connection.execute("BEGIN IMMEDIATE")
        try:
            update()
            store.connection.commit()
        except Exception:
            store.connection.rollback()
            raise
    return {"id": identifier, "status": "approved" if payload["approve"] else "rejected"}
