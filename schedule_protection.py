from __future__ import annotations

from datetime import datetime, timezone

import scheduling


ERROR = "Kesinleşen vardiya korunuyor; önce yetkili olarak korumayı kaldırın."


def initialize(connection):
    connection.executescript(f"""
        CREATE TABLE IF NOT EXISTS schedule_protection (
            month TEXT NOT NULL, row_id TEXT NOT NULL, day TEXT NOT NULL,
            employee_id INTEGER NOT NULL, section TEXT NOT NULL, protected_at TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '', protected_by TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(month, row_id, day)
        );
        CREATE TRIGGER IF NOT EXISTS schedule_protection_update
        BEFORE UPDATE OF period,payload ON schedule_months
        WHEN EXISTS (
            SELECT 1 FROM schedule_protection AS lock
            JOIN json_each(OLD.payload) AS old_row ON json_extract(old_row.value,'$.row_id')=lock.row_id
            WHERE lock.month=OLD.period AND (
                NEW.period<>OLD.period OR NOT EXISTS (
                    SELECT 1 FROM json_each(NEW.payload) AS new_row
                    WHERE json_extract(new_row.value,'$.row_id')=lock.row_id
                    AND json_extract(new_row.value,'$.employee_id')=lock.employee_id
                    AND json_extract(new_row.value,'$.section')=lock.section
                    AND json_type(new_row.value,'$.days."' || lock.day || '"')='object'
                    AND NOT EXISTS (
                        SELECT fullkey,type,atom FROM json_tree(old_row.value,'$.days."' || lock.day || '"')
                        EXCEPT SELECT fullkey,type,atom FROM json_tree(new_row.value,'$.days."' || lock.day || '"')
                    )
                    AND NOT EXISTS (
                        SELECT fullkey,type,atom FROM json_tree(new_row.value,'$.days."' || lock.day || '"')
                        EXCEPT SELECT fullkey,type,atom FROM json_tree(old_row.value,'$.days."' || lock.day || '"')
                    )
                )
            )
        )
        BEGIN SELECT RAISE(ABORT,'{ERROR}'); END;
        CREATE TRIGGER IF NOT EXISTS schedule_protection_delete
        BEFORE DELETE ON schedule_months
        WHEN EXISTS (SELECT 1 FROM schedule_protection WHERE month=OLD.period)
        BEGIN SELECT RAISE(ABORT,'{ERROR}'); END;
    """)
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(schedule_protection)")}
    if "reason" not in columns:
        connection.execute("ALTER TABLE schedule_protection ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
    if "protected_by" not in columns:
        connection.execute("ALTER TABLE schedule_protection ADD COLUMN protected_by TEXT NOT NULL DEFAULT ''")


def locks(store, month):
    return [dict(row) for row in store.connection.execute(
        "SELECT row_id,day,employee_id,section,protected_at,reason,protected_by FROM schedule_protection WHERE month=? "
        "ORDER BY day,section,employee_id", (month,),
    )]


def set_lock(store, payload):
    required = {"month", "row_id", "date", "revision", "protected"}
    if (not isinstance(payload, dict) or not required.issubset(payload)
            or set(payload) - required - {"reason"}):
        raise ValueError("Vardiya koruma isteği geçersiz.")
    month = scheduling.validate_month(payload["month"])
    row_id, day = payload["row_id"], payload["date"]
    if (not isinstance(row_id, str) or not 1 <= len(row_id) <= 80 or type(payload["protected"]) is not bool
            or not isinstance(payload["revision"], str)
            or day not in {date.isoformat() for date in scheduling.month_dates(month)}):
        raise ValueError("Vardiya günü veya sürümü geçersiz.")
    reason = payload.get("reason", "")
    if payload["protected"]:
        if not isinstance(reason, str):
            raise ValueError("Koruma gerekçesi metin olmalıdır.")
        reason = " ".join(reason.split())
        if not 5 <= len(reason) <= 200:
            raise ValueError("Koruma gerekçesi 5-200 karakter olmalıdır.")
    connection = store.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        current = store.schedule_month(month)
        if current["revision"] != payload["revision"]:
            raise ValueError("Vardiya kaydı değişti; ayı yeniden açın.")
        if payload["protected"]:
            row = next((row for row in current["entries"] if row["row_id"] == row_id and day in row["days"]), None)
            if row is None:
                raise ValueError("Yalnız kayıtlı ve dolu vardiya günleri korunabilir.")
            protected_by = store.actor or store.user_name()
            change = connection.execute(
                "INSERT OR IGNORE INTO schedule_protection "
                "(month,row_id,day,employee_id,section,protected_at,reason,protected_by) VALUES (?,?,?,?,?,?,?,?)",
                (month, row_id, day, row["employee_id"], row["section"],
                 datetime.now(timezone.utc).isoformat(), reason, protected_by),
            )
        else:
            existing = connection.execute(
                "SELECT reason,protected_by FROM schedule_protection WHERE month=? AND row_id=? AND day=?",
                (month, row_id, day),
            ).fetchone()
            change = connection.execute("DELETE FROM schedule_protection WHERE month=? AND row_id=? AND day=?",
                                        (month, row_id, day))
        if change.rowcount:
            store.record_change("schedule.protect" if payload["protected"] else "schedule.unprotect",
                                "Vardiya koruması değiştirildi", {
                                    "month": month, "row_id": row_id, "date": day,
                                    "reason": reason if payload["protected"] else (existing["reason"] if existing else ""),
                                    "protected_by": protected_by if payload["protected"] else (existing["protected_by"] if existing else ""),
                                })
        connection.commit()
        return locks(store, month)
    except Exception:
        connection.rollback()
        raise


def keep_protected(store, month, before, proposal):
    original = {row["row_id"]: row for row in before}
    edited = {row["row_id"]: row for row in proposal}
    skipped = []
    for cell in locks(store, month):
        row_id, day = cell["row_id"], cell["day"]
        row = edited.get(row_id)
        if row is None or any(row[field] != cell[field] for field in ("employee_id", "section")):
            raise ValueError(ERROR)
        saved = original[row_id]["days"][day]
        if row["days"].get(day) != saved:
            row["days"][day] = saved
            skipped.append(cell)
    return skipped
