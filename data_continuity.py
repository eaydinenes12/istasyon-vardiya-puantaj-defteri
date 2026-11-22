from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
from datetime import date, datetime, time as datetime_time, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import shutil
import tempfile
import time

import controls
import scheduling
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


MAX_BYTES = 64_000_000
MAX_ENCRYPTED_PACKAGE_BYTES = 12_000_000
MAX_DATABASE_BACKUP_BYTES = 500_000_000
_ENCRYPTED_PACKAGE_FORMAT = "vardiya-encrypted-work-package"
_ENCRYPTED_PACKAGE_AAD = b"vardiya-encrypted-work-package:v1"
_DATABASE_BACKUP_MAGIC = b"VPSQLDB1"
_DATABASE_BACKUP_AAD = b"vardiya-sqlite-database-backup:v1"
TABLES = (
    "employees", "employee_assets", "app_settings", "form_templates", "schedule_months", "attendance_months", "schedule_protection",
    "manager_chart_notes",
    "attendance_events", "employee_balance_openings", "balance_opening_history", "archive_revisions",
    "linked_leave_requests", "linked_leave_applications",
    "workflow_documents", "workflow_attachments", "workflow_exports", "personal_calendar_exports",
    "workflow_retention_policy",
    "portal_requests",
    "operations_rules", "operations_availability", "operations_closings",
    "operations_emergency_audit", "operations_cost_scenarios", "operations_payroll_deliveries", "operations_payroll_adjustments", "attendance_corrections",
    "operations_lifecycle_events", "operations_temporary_assignments", "identity_match_decisions",
)
PRIVATE_SETTINGS = {
    "workspace_recovery_drafts",
    "users", "data_instance_id", "form_templates_initialized", "last_version",
    "branches", "active_branch", "phone", "mail", "session", "api",
    "backup_mirror", "update_settings", "app_version",
}


def initialize(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS continuity_previews (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
            target_hash TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS continuity_actions (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, backup_id TEXT NOT NULL,
            after_hash TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL,
            undone INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS continuity_receipts (
            id TEXT PRIMARY KEY, origin TEXT NOT NULL, payload TEXT NOT NULL,
            received_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS identity_match_decisions (
            id TEXT PRIMARY KEY, employee_id INTEGER NOT NULL, candidate_id INTEGER NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('same','different')), reason TEXT NOT NULL,
            employee_name TEXT NOT NULL, candidate_name TEXT NOT NULL,
            employee_registry TEXT NOT NULL, candidate_registry TEXT NOT NULL,
            employee_section TEXT NOT NULL, candidate_section TEXT NOT NULL,
            actor TEXT NOT NULL, decided_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS identity_match_decisions_pair
            ON identity_match_decisions(employee_id,candidate_id,decided_at DESC);
    """)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _encode(value):
    return {"$bytes": base64.b64encode(value).decode("ascii")} if isinstance(value, bytes) else value


def _decode(value):
    if isinstance(value, dict) and set(value) == {"$bytes"}:
        try:
            return base64.b64decode(value["$bytes"], validate=True)
        except (ValueError, TypeError, binascii.Error) as exc:
            raise ValueError("Aktarımın ikili dosya içeriği geçersiz.") from exc
    return value


def _safe_setting(name):
    if not isinstance(name, str):
        raise ValueError("Kaynak ayar adı geçersiz.")
    folded = name.casefold()
    return name not in PRIVATE_SETTINGS and not any(
        word in folded for word in ("password", "token", "secret", "portal", "credential", "smtp", "email", "mail_")
    )


@contextmanager
def readonly(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in (".sqlite3", ".sqlite", ".db") \
            or str(path).startswith("\\\\") or path.stat().st_size > 500_000_000:
        raise ValueError("Yerel bir SQLite veri dosyası seçin (en fazla 500 MB).")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("BEGIN")
    started = time.monotonic()
    connection.set_progress_handler(lambda: int(time.monotonic() - started > 8), 5000)
    try:
        yield connection
    except sqlite3.DatabaseError as exc:
        raise ValueError("Kaynak veri dosyası okunamadı, bozuk veya işlem süresi aşıldı.") from exc
    finally:
        connection.close()


def _snapshot(connection):
    tables = {row["name"] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    result = {}
    size = 0
    for table in TABLES:
        if table not in tables:
            continue
        rows = []
        cursor = connection.execute(f'SELECT * FROM "{table}"')
        for row in cursor:
            if len(rows) >= 100_000:
                raise ValueError("Bir tabloda 100.000 kayıttan fazla veri var; aktarımı bölerek yapın.")
            if table == "app_settings" and not _safe_setting(row["name"]):
                continue
            item = {key: _encode(value) for key, value in dict(row).items()}
            size += len(_json(item))
            if size > MAX_BYTES:
                raise ValueError("Aktarım içeriği 64 MB sınırını aşıyor.")
            rows.append(item)
        result[table] = sorted(rows, key=_json)
    return result


def _target_hash(store):
    snapshot = _snapshot(store.connection)
    # Permission changes must also invalidate an outstanding destructive preview.
    snapshot["access"] = store._setting("users", [])
    files = {}
    for filename in scheduling.TEMPLATE_FILES.values():
        path = store.template_directory / filename
        if path.is_file():
            files[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    snapshot["templates"] = files
    return _digest(snapshot)


def _validate_snapshot(snapshot):
    try:
        people = snapshot["employees"]
        ids = set()
        for person in people:
            if type(person["id"]) is not int or person["id"] < 1 or person["id"] in ids \
                    or not isinstance(person["name"], str) or not 1 <= len(person["name"].strip()) <= 80 \
                    or person["active"] not in (0, 1):
                raise ValueError("Kaynak personel kaydı geçersiz.")
            ids.add(person["id"])
        cells = set()
        for row in snapshot["schedule_months"]:
            month = scheduling.validate_month(row["period"])
            entries = scheduling.normalize_schedule_entries(month, json.loads(row["payload"]), people)
            cells.update((month, entry["row_id"], day, entry["employee_id"], entry["section"])
                         for entry in entries for day in entry["days"])
        for lock in snapshot.get("schedule_protection", []):
            if tuple(lock[key] for key in ("month", "row_id", "day", "employee_id", "section")) not in cells:
                raise ValueError("Kaynak vardiya koruması geçerli bir hücreye bağlı değil.")
        asset_ids = set()
        for asset in snapshot.get("employee_assets", []):
            if (type(asset["id"]) is not int or asset["id"] < 1 or asset["id"] in asset_ids
                    or type(asset["employee_id"]) is not int or asset["employee_id"] not in ids
                    or asset["category"] not in {"key", "card", "clothing", "equipment", "other"}
                    or not isinstance(asset["item"], str) or not 1 <= len(asset["item"].strip()) <= 80
                    or not isinstance(asset["note"], str) or len(asset["note"]) > 250
                    or not isinstance(asset["issued_by"], str) or not isinstance(asset["issued_at"], str)
                    or ((asset["returned_at"] is None) != (asset["returned_by"] is None))
                    or (asset["returned_at"] is not None
                        and (not isinstance(asset["returned_at"], str) or not isinstance(asset["returned_by"], str)))):
                raise ValueError("Kaynak zimmet kaydı geçersiz.")
            asset_ids.add(asset["id"])
        for row in snapshot["attendance_months"]:
            month = scheduling.validate_month(row["period"])
            _attendance_clean(month, json.loads(row["payload"]), people)
        opening_keys = set()
        for opening in snapshot.get("employee_balance_openings", []):
            employee_id, year = opening["employee_id"], opening["year"]
            values = (opening["annual_leave_days"], opening["overtime_hours"])
            if (type(employee_id) is not int or employee_id not in ids
                    or type(year) is not int or not 2000 <= year <= 2100
                    or (employee_id, year) in opening_keys
                    or any(isinstance(value, bool) or not isinstance(value, (int, float))
                           or not math.isfinite(value) or abs(value) > 9999 for value in values)):
                raise ValueError("Kaynak açılış bakiye kaydı geçersiz.")
            opening_keys.add((employee_id, year))
        history_ids = set()
        for item in snapshot.get("balance_opening_history", []):
            history_id, employee_id, year = item["id"], item["employee_id"], item["year"]
            values = (item["previous_annual_leave_days"], item["new_annual_leave_days"])
            if (type(history_id) is not int or history_id < 1 or history_id in history_ids
                    or type(employee_id) is not int or employee_id not in ids
                    or type(year) is not int or (employee_id, year) not in opening_keys
                    or any(isinstance(value, bool) or not isinstance(value, (int, float))
                           or not math.isfinite(value) or abs(value) > 9999 for value in values)
                    or not isinstance(item["actor"], str) or not isinstance(item["occurred_at"], str)):
                raise ValueError("Kaynak açılış bakiye hareketi geçersiz.")
            history_ids.add(history_id)
        assignment_ids = set()
        for item in snapshot.get("operations_temporary_assignments", []):
            assignment_id = item["id"]
            employee_id = item["employee_id"]
            try:
                start = date.fromisoformat(item["start_date"])
                end = date.fromisoformat(item["end_date"])
                start_time = datetime_time.fromisoformat(item["start_time"])
                end_time = datetime_time.fromisoformat(item["end_time"])
            except (TypeError, ValueError) as exc:
                raise ValueError("Kaynak geçici görevlendirme kaydı geçersiz.") from exc
            if (not isinstance(assignment_id, str) or not 1 <= len(assignment_id) <= 100
                    or assignment_id in assignment_ids
                    or type(employee_id) is not int or employee_id not in ids
                    or item["location_type"] not in ("section", "branch")
                    or not isinstance(item["location"], str) or not 1 <= len(item["location"].strip()) <= 80
                    or (item["location_type"] == "section" and item["location"] not in scheduling.section_names())
                    or start.isoformat() != item["start_date"] or end.isoformat() != item["end_date"]
                    or end < start or (end - start).days > 90
                    or start_time.isoformat(timespec="minutes") != item["start_time"]
                    or end_time.isoformat(timespec="minutes") != item["end_time"]
                    or start_time == end_time
                    or not isinstance(item["note"], str) or len(item["note"]) > 300
                    or not isinstance(item["created_at"], str) or not isinstance(item["actor"], str)):
                raise ValueError("Kaynak geçici görevlendirme kaydı geçersiz.")
            assignment_ids.add(assignment_id)
        for row in snapshot["app_settings"]:
            json.loads(row["payload"])
        retention_policies = snapshot.get("workflow_retention_policy", [])
        if len(retention_policies) > 1:
            raise ValueError("Kaynak belge saklama ayarı birden fazla kayıt içeriyor.")
        if retention_policies:
            policy = retention_policies[0]
            if (policy.get("id") != 1 or type(policy.get("retention_days")) is not int
                    or not 1 <= policy["retention_days"] <= 365_000
                    or not isinstance(policy.get("updated_at"), str)):
                raise ValueError("Kaynak belge saklama ayarı geçersiz.")
            try:
                datetime.fromisoformat(policy["updated_at"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("Kaynak belge saklama ayarının tarihi geçersiz.") from exc
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Kaynak kaydın alanları veya JSON içeriği desteklenmiyor.") from exc


def _admin(handler, store):
    if store.users_enabled() and (getattr(handler, "current_user", None) or {}).get("role") != "admin" \
            and not (hasattr(handler, "_is_internal") and handler._is_internal()):
        handler._json({"error": "Bu veri işlemi yalnız yönetici tarafından yapılabilir."}, 403)
        return False
    return True


def _preview(store, kind, value, summary):
    token = secrets.token_hex(16)
    with store.connection:
        store.connection.execute("DELETE FROM continuity_previews WHERE created_at<?", (time.time() - 1800,))
        if store.connection.execute("SELECT COUNT(*) FROM continuity_previews").fetchone()[0] >= 30:
            raise ValueError("Çok fazla açık önizleme var; 30 dakika sonra tekrar deneyin.")
        store.connection.execute(
            "INSERT INTO continuity_previews VALUES (?,?,?,?,?)",
            (token, kind, _json(value), _target_hash(store), time.time()),
        )
    return {"preview_id": token, **summary}


def _load_preview(store, token, kind):
    if not isinstance(token, str):
        raise ValueError("Önizleme seçimi geçersiz.")
    row = store.connection.execute(
        "SELECT * FROM continuity_previews WHERE id=? AND kind=?", (token, kind),
    ).fetchone()
    if row is None or row["created_at"] < time.time() - 1800:
        raise ValueError("Önizleme bulunamadı veya süresi doldu; yeniden önizleyin.")
    if _target_hash(store) != row["target_hash"]:
        raise ValueError("Önizlemeden sonra hedef kayıtlar değişti; hiçbir kayıt aktarılmadı.")
    return json.loads(row["payload"])


def migration_preview(store, source_path):
    if not isinstance(source_path, str) or not 1 <= len(source_path) <= 1000:
        raise ValueError("Kaynak veritabanının tam dosya yolunu belirtin.")
    path = Path(source_path).expanduser().resolve()
    if path == store.path.resolve():
        raise ValueError("Kaynak ve hedef veritabanı aynı olamaz.")
    with readonly(path) as source:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Kaynak veritabanı bütünlük denetiminden geçmedi.")
        snapshot = _snapshot(source)
        snapshot.setdefault("workflow_retention_policy", [])
        if not {"employees", "schedule_months", "attendance_months", "app_settings"} <= set(snapshot):
            raise ValueError("Dosya desteklenen bir Vardiya Puantaj veritabanı değil.")
        _validate_snapshot(snapshot)
        omitted = {}
        protected = {"change_history", "workflow_shares", "portal_tokens", "portal_rate", "portal_plan_views",
                     "portal_plan_read_tokens",
                     "portal_access_activity", "access_notifications",
                     "continuity_previews", "continuity_actions", "continuity_receipts",
                     "technical_error_events", "portal_device_sessions", "mail_recipient_permissions"}
        for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            table = row["name"]
            if table in TABLES or table.startswith("sqlite_"):
                continue
            if table not in protected:
                raise ValueError(f"Kaynakta desteklenmeyen tablo var: {table}. Veri atlanmaması için aktarım durduruldu.")
            if re.fullmatch(r"[a-z_]+", table):
                omitted[table] = source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table, rows in snapshot.items():
            columns = {row["name"] for row in store.connection.execute(f'PRAGMA table_info("{table}")')}
            if rows and (not columns or any(set(row) - columns for row in rows)):
                raise ValueError(f"{table} tablosu daha yeni veya desteklenmeyen alanlar içeriyor; aktarım yapılmadı.")
    templates = {}
    for kind, filename in scheduling.TEMPLATE_FILES.items():
        file = path.parent / "table_templates" / filename
        if file.is_file():
            normalized = scheduling.validate_table_template(kind, file.read_bytes())
            templates[filename] = base64.b64encode(normalized).decode("ascii")
    counts = {table: len(rows) for table, rows in snapshot.items()}
    occupied = any(store.connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
                   for table in ("employees", "schedule_months", "attendance_months", "attendance_events"))
    return _preview(store, "migration", {
        "source": str(path), "source_hash": _digest(snapshot),
        "tables": snapshot, "templates": templates,
    }, {
        "counts": counts, "source": str(path), "target": str(store.path.resolve()),
        "target_occupied": occupied, "can_apply": not occupied,
        "settings": [row["name"] for row in snapshot["app_settings"]],
        "template_files": list(templates),
        "omitted_tables": omitted,
        "excluded": ["Kullanıcı hesapları ve parolalar", "Paylaşım/portal anahtarları", "Ağ ve API sırları"],
        "notice": "Tam aktarım yalnız boş kayıt alanına yapılır. Eski işlem günlüğü, bildirimler ve aktarım oturumları kaynakta kalır. Dolu hedef için seçili kurtarma veya çalışma paketi kullanın.",
    })


def _replace_tables(store, snapshot, *, allow_protection_reset=False):
    connection = store.connection
    if "schedule_months" in snapshot:
        protected = connection.execute("SELECT 1 FROM schedule_protection LIMIT 1").fetchone()
        if protected and not allow_protection_reset:
            from schedule_protection import ERROR
            raise ValueError(ERROR)
        if protected:
            connection.execute("DELETE FROM schedule_protection")
    for table in reversed(TABLES):
        if table not in snapshot:
            continue
        if table == "app_settings":
            for row in connection.execute("SELECT name FROM app_settings").fetchall():
                if _safe_setting(row["name"]):
                    connection.execute("DELETE FROM app_settings WHERE name=?", (row["name"],))
        else:
            connection.execute(f'DELETE FROM "{table}"')
    for table in TABLES:
        if table not in snapshot:
            continue
        columns = {row["name"] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        for row in snapshot[table]:
            if table == "app_settings" and not _safe_setting(row["name"]):
                continue
            keys = [key for key in row if key in columns]
            if not keys:
                raise ValueError(f"{table} tablosunun sütunları desteklenmiyor.")
            names = ",".join(f'"{key}"' for key in keys)
            parameters = ",".join("?" for _ in keys)
            connection.execute(
                f'INSERT INTO "{table}" ({names}) VALUES ({parameters})',
                tuple(_decode(row[key]) for key in keys),
            )
    if connection.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Aktarılan ilişkili kayıtlar tutarlı değil; tüm işlem geri alındı.")


def _write_templates(store, templates):
    for filename, encoded in templates.items():
        if filename not in scheduling.TEMPLATE_FILES.values():
            raise ValueError("Aktarımda bilinmeyen şablon dosyası var.")
        store.template_directory.mkdir(parents=True, exist_ok=True)
        path = store.template_directory / filename
        temporary = path.with_suffix(".continuity-tmp")
        temporary.write_bytes(base64.b64decode(encoded, validate=True))
        temporary.replace(path)


def apply_migration(store, preview_id, confirmed):
    if confirmed is not True:
        raise ValueError("Veri aktarımı için açık onay gereklidir.")
    data = _load_preview(store, preview_id, "migration")
    with readonly(data["source"]) as source:
        if _digest(_snapshot(source)) != data["source_hash"]:
            raise ValueError("Kaynak kayıtlar önizlemeden sonra değişti; yeniden önizleyin.")
    for kind, filename in scheduling.TEMPLATE_FILES.items():
        file = Path(data["source"]).parent / "table_templates" / filename
        encoded = base64.b64encode(scheduling.validate_table_template(kind, file.read_bytes())).decode("ascii") \
            if file.is_file() else None
        if encoded != data["templates"].get(filename):
            raise ValueError("Kaynak tablo şablonları değişti; yeniden önizleyin.")
    if any(store.connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
           for table in ("employees", "schedule_months", "attendance_months", "attendance_events")):
        raise ValueError("Hedef boş değil. Mevcut verilerin üzerine tam aktarım yapılmaz.")
    if store.locked_periods():
        raise ValueError("Hedefte kilitli dönem var; tam aktarım yapılmadı.")
    backup = store.create_backup("before-restore")
    connection = store.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        _load_preview(store, preview_id, "migration")
        _replace_tables(store, data["tables"])
        _write_templates(store, data["templates"])
        store.record_change("continuity.migrate", "Eski veriler boş kayıt alanına aktarıldı", {
            "source": data["source"], "source_hash": data["source_hash"], "backup": backup["id"],
        })
        action_id = secrets.token_hex(16)
        connection.execute("INSERT INTO continuity_actions VALUES (?,?,?,?,?,?,0)", (
            action_id, "migration", backup["id"], _target_hash(store),
            _json({"source": data["source"]}), datetime.now(timezone.utc).isoformat(),
        ))
        connection.execute("DELETE FROM continuity_previews WHERE id=?", (preview_id,))
        connection.commit()
        return {"action_id": action_id, "backup_id": backup["id"], "counts": {
            table: len(rows) for table, rows in data["tables"].items()
        }, "notice": "Aktarım tamamlandı. Kaynak dosya değiştirilmedi; kullanıcı hesapları taşınmadı."}
    except Exception:
        connection.rollback()
        store._copy_templates(store._template_backup_path(store._backup_path(backup["id"])), store.template_directory)
        raise


def undo_migration(store, action_id, confirmed):
    if confirmed is not True or not isinstance(action_id, str):
        raise ValueError("Aktarımı geri almak için açık onay gereklidir.")
    row = store.connection.execute(
        "SELECT * FROM continuity_actions WHERE id=? AND kind='migration' AND undone=0", (action_id,),
    ).fetchone()
    if row is None or _target_hash(store) != row["after_hash"]:
        raise ValueError("Aktarım sonrası veriler değişti; veri kaybını önlemek için otomatik geri alma yapılmadı.")
    with readonly(store._backup_path(row["backup_id"])) as backup:
        snapshot = _snapshot(backup)
    safety = store.create_backup("before-restore")
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        if _target_hash(store) != row["after_hash"]:
            raise ValueError("Kayıtlar değişti; geri alma yapılmadı.")
        _replace_tables(store, snapshot, allow_protection_reset=True)
        store._copy_templates(
            store._template_backup_path(store._backup_path(row["backup_id"])), store.template_directory,
        )
        store.connection.execute("UPDATE continuity_actions SET undone=1 WHERE id=?", (action_id,))
        store.record_change("continuity.undo", "Veri aktarımı güvenli biçimde geri alındı", {"action_id": action_id})
        store.connection.commit()
        return {"undone": True, "safety_backup": safety["id"]}
    except Exception:
        store.connection.rollback()
        store._copy_templates(store._template_backup_path(store._backup_path(safety["id"])), store.template_directory)
        raise


def _attendance_clean(month, payload, people):
    if isinstance(payload, dict) and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", key) for key in payload):
        payload = {"overrides": payload}
    if not isinstance(payload, dict) or set(payload) - {"overrides", "deductions", "roster", "overtime", "sources"}:
        raise ValueError("Puantaj içeriği geçersiz.")
    return {
        "overrides": scheduling.normalize_attendance_overrides(month, payload.get("overrides", {}), people),
        "deductions": scheduling.normalize_attendance_deductions(payload.get("deductions", {}), people),
        "roster": scheduling.normalize_attendance_roster(payload.get("roster", {}), people),
        "overtime": scheduling.normalize_attendance_overtime(payload.get("overtime", {}), people),
    }


def backup_preview(store, backup_id, selections):
    if not isinstance(selections, list) or not 1 <= len(selections) <= 24:
        raise ValueError("Kurtarılacak 1–24 dönem seçin.")
    names = {person["id"]: person["name"] for person in store.people()}
    details = []
    with readonly(store._backup_path(backup_id)) as source:
        source_names = {row["id"]: row["name"] for row in source.execute("SELECT id,name FROM employees")}
        for selection in selections:
            if not isinstance(selection, dict) or set(selection) != {"kind", "month"} \
                    or selection["kind"] not in ("schedule", "attendance"):
                raise ValueError("Kurtarma seçimi geçersiz.")
            month = scheduling.validate_month(selection["month"])
            kind = selection["kind"]
            if any(item["kind"] == kind and item["month"] == month for item in details):
                raise ValueError("Aynı kurtarma dönemi iki kez seçilemez.")
            row = source.execute(f"SELECT payload FROM {kind}_months WHERE period=?", (month,)).fetchone()
            if row is None:
                raise ValueError(f"Yedekte {month} {kind} kaydı yok.")
            payload = json.loads(row["payload"])
            used = set()
            if kind == "schedule":
                used.update(entry["employee_id"] for entry in payload)
                clean = scheduling.normalize_schedule_entries(month, payload, store.people())
                current = store.schedule_month(month)
            else:
                used.update(int(person) for values in payload.get("overrides", {}).values() for person in values)
                used.update(int(person) for key in ("deductions", "overtime") for person in payload.get(key, {}))
                used.update(int(person) for values in payload.get("roster", {}).values() for person in values)
                clean = _attendance_clean(month, payload, store.people())
                current = store.attendance_month(month)
            if any(names.get(person) != source_names.get(person) for person in used):
                raise ValueError("Personel kimliği farklı. Önce personel eşleştirmeli çalışma paketi kullanın.")
            blank_attendance = _attendance_clean(month, {}, store.people())
            before_content = {"schedule": current["entries"], "attendance": blank_attendance} if kind == "schedule" \
                else {"schedule": [], "attendance": {key: current[key] for key in clean}}
            after_content = {"schedule": clean, "attendance": blank_attendance} if kind == "schedule" \
                else {"schedule": [], "attendance": clean}
            before_cells, after_cells = _flatten(before_content), _flatten(after_content)
            differences = [{"cell": json.loads(key), "before": before_cells.get(key), "after": after_cells.get(key)}
                           for key in sorted(set(before_cells) | set(after_cells))
                           if before_cells.get(key) != after_cells.get(key)]
            details.append({
                "kind": kind, "month": month, "before": current,
                "after": clean, "changed": bool(differences), "differences": differences,
            })
    return _preview(store, "backup", {"selections": details}, {"selections": details, "backup_id": backup_id})


def backup_apply(store, preview_id, confirmed):
    if confirmed is not True:
        raise ValueError("Seçili kayıtların kurtarılması için açık onay gereklidir.")
    data = _load_preview(store, preview_id, "backup")
    store.assert_periods_unlocked(item["month"] for item in data["selections"])
    backup = store.create_backup("before-restore")
    connection = store.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        _load_preview(store, preview_id, "backup")
        store.assert_periods_unlocked(item["month"] for item in data["selections"])
        _persist_selections(store, data["selections"])
        connection.execute("DELETE FROM continuity_previews WHERE id=?", (preview_id,))
        store.record_change("continuity.recover", "Seçili dönemler yedekten kurtarıldı", {
            "backup": backup["id"], "selections": [
                {"month": item["month"], "kind": item["kind"]} for item in data["selections"]
            ],
        })
        connection.commit()
        return {"restored": len(data["selections"]), "safety_backup": backup["id"]}
    except Exception:
        connection.rollback()
        raise


def _persist_selections(store, selections):
    import linked_leave
    import operations
    connection = store.connection
    periods = {item["month"] for item in selections if item["kind"] == "schedule"}
    old_entries = []
    for row in connection.execute("SELECT payload FROM schedule_months"):
        old_entries.extend(json.loads(row["payload"]))
    for item in selections:
        kind, month = item["kind"], item["month"]
        current = connection.execute(f"SELECT * FROM {kind}_months WHERE period=?", (month,)).fetchone()
        if current:
            connection.execute(
                "INSERT INTO archive_revisions(kind,period,payload,revision,recorded_at) VALUES (?,?,?,?,?)",
                (kind, month, current["payload"], current["revision"], datetime.now(timezone.utc).isoformat()),
            )
        connection.execute(
            f"INSERT INTO {kind}_months VALUES (?,?,?) ON CONFLICT(period) "
            "DO UPDATE SET payload=excluded.payload,revision=excluded.revision",
            (month, _json(item["after"]), secrets.token_hex(16)),
        )
    for month in sorted(periods):
        prepared = linked_leave.prepare_schedule_save(store, month, store.schedule_month(month)["entries"])
        if prepared["conflicts"]:
            raise ValueError("Seçili kayıt güncel onaylı izinle çelişiyor; önce izin çakışmasını çözün.")
        connection.execute("UPDATE schedule_months SET payload=? WHERE period=?", (_json(prepared["entries"]), month))
        linked_leave.record_applications_in_transaction(store, month, prepared["applications"])
    new_entries = []
    for row in connection.execute("SELECT payload FROM schedule_months"):
        new_entries.extend(json.loads(row["payload"]))
    controls.assert_no_new_overlaps(old_entries, new_entries, {person["id"]: person["name"] for person in store.people()})
    for month in sorted(periods):
        operations.validate_schedule_in_transaction(store, month, store.schedule_month(month)["entries"])


def instance_id(store):
    value = store._setting("data_instance_id", "")
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        value = secrets.token_hex(16)
        with store.connection:
            store._write_setting("data_instance_id", value)
    return value


def _month_content(store, month):
    attendance = store.attendance_month(month)
    return {"schedule": store.schedule_month(month)["entries"], "attendance": {
        key: attendance[key] for key in ("overrides", "deductions", "roster", "overtime")
    }}


def export_package(store, month, lineage=None):
    month = scheduling.validate_month(month)
    current = _month_content(store, month)
    origin = instance_id(store)
    baseline = {"schedule": [], "attendance": _attendance_clean(month, {}, store.people())}
    if lineage:
        receipt = store.connection.execute("SELECT payload FROM continuity_receipts WHERE id=?", (lineage,)).fetchone()
        if receipt is None:
            raise ValueError("Dönüş paketi için alınan paket seçimi bulunamadı.")
        received = json.loads(receipt["payload"])
        if received["month"] != month:
            raise ValueError("Dönüş paketinin ayı eşleşmiyor.")
        # Receipts already contain local IDs, so the common base travels in local identity.
        baseline = received["local_snapshot"]
    package = {
        "format": "vardiya-work-package", "version": 1, "id": secrets.token_hex(16),
        "origin": origin, "month": month, "people": store.people(),
        "profiles": {key: {"registry_no": value.get("payroll_code", "")}
                     for key, value in store.employee_profiles().items()},
        "base": baseline, "current": current,
    }
    return package


def encrypt_package(package, passphrase):
    package = _validate_package(package)
    if not isinstance(passphrase, str) or not 12 <= len(passphrase) <= 1024 or "\x00" in passphrase:
        raise ValueError("Şifreli aktarım parolası 12–1024 karakter olmalıdır.")
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))
    ciphertext = AESGCM(key).encrypt(nonce, _json(package).encode("utf-8"), _ENCRYPTED_PACKAGE_AAD)
    envelope = {
        "format": _ENCRYPTED_PACKAGE_FORMAT, "version": 1,
        "kdf": "scrypt-32768-8-1", "cipher": "AES-256-GCM",
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    if len(_json(envelope)) > MAX_ENCRYPTED_PACKAGE_BYTES:
        raise ValueError("Şifreli çalışma paketi 12 MB sınırını aşıyor.")
    return envelope


def decrypt_package(envelope, passphrase):
    fields = {"format", "version", "kdf", "cipher", "salt", "nonce", "ciphertext"}
    if not isinstance(envelope, dict) or set(envelope) != fields \
            or envelope["format"] != _ENCRYPTED_PACKAGE_FORMAT or type(envelope["version"]) is not int \
            or envelope["version"] != 1 or envelope["kdf"] != "scrypt-32768-8-1" \
            or envelope["cipher"] != "AES-256-GCM":
        raise ValueError("Şifreli çalışma paketi biçimi desteklenmiyor.")
    if not isinstance(passphrase, str) or not 12 <= len(passphrase) <= 1024 or "\x00" in passphrase:
        raise ValueError("Şifreli aktarım parolası 12–1024 karakter olmalıdır.")
    if len(_json(envelope)) > MAX_ENCRYPTED_PACKAGE_BYTES:
        raise ValueError("Şifreli çalışma paketi 12 MB sınırını aşıyor.")
    try:
        salt = base64.b64decode(envelope["salt"], validate=True)
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("Şifreli çalışma paketi kodlaması geçersiz.") from exc
    if len(salt) != 16 or len(nonce) != 12 or not 16 <= len(ciphertext) <= MAX_BYTES + 16:
        raise ValueError("Şifreli çalışma paketi boyutu geçersiz.")
    key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _ENCRYPTED_PACKAGE_AAD)
        package = json.loads(plaintext.decode("utf-8"))
    except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Parola yanlış veya şifreli dosya bozuk; dosya açılmadı.") from exc
    return _validate_package(package)


def _validate_database_backup_passphrase(passphrase):
    if not isinstance(passphrase, str) or not 12 <= len(passphrase) <= 1024 or "\x00" in passphrase:
        raise ValueError("Tam veritabanı yedeği parolası 12–1024 karakter olmalıdır.")


def _database_backup_key(salt, passphrase):
    _validate_database_backup_passphrase(passphrase)
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _validate_database_file(path):
    from storage import Store, SCHEMA_VERSION
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_DATABASE_BACKUP_BYTES:
        raise ValueError("Tam veritabanı yedeği 500 MB sınırını aşıyor.")
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite bütünlük denetimi başarısız; yedek geri yüklenmedi.")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Yedekte geçersiz kayıt ilişkileri var; yedek geri yüklenmedi.")
            tables = {row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if not Store._REQUIRED_BACKUP_TABLES <= tables:
                raise ValueError("Dosya gerekli Vardiya Puantaj veritabanı tablolarını içermiyor.")
            schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if schema_version > SCHEMA_VERSION:
                raise ValueError("Yedek bu uygulamanın desteklediğinden daha yeni bir veri şeması kullanıyor.")
            row_counts = {}
            for table in ("employees", "schedule_months", "attendance_months", "archive_revisions"):
                row_counts[table] = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) \
                    if table in tables else 0
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError("Şifreli dosya geçerli SQLite veritabanı içermiyor.") from exc
    return {
        "size_bytes": path.stat().st_size, "schema_version": schema_version,
        "row_counts": row_counts,
    }


def encrypt_database_file(path, passphrase):
    _validate_database_backup_passphrase(passphrase)
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_DATABASE_BACKUP_BYTES:
        raise ValueError("Tam veritabanı yedeği 500 MB sınırını aşıyor.")
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    encryptor = Cipher(
        algorithms.AES(_database_backup_key(salt, passphrase)), modes.GCM(nonce),
    ).encryptor()
    encryptor.authenticate_additional_data(_DATABASE_BACKUP_AAD)
    envelope = bytearray(_DATABASE_BACKUP_MAGIC + salt + nonce)
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            envelope.extend(encryptor.update(chunk))
    encryptor.finalize()
    envelope.extend(encryptor.tag)
    return bytes(envelope)


def decrypt_database_file(envelope, passphrase, output_path):
    _validate_database_backup_passphrase(passphrase)
    if not isinstance(envelope, bytes) or len(envelope) < 52 \
            or len(envelope) > MAX_DATABASE_BACKUP_BYTES + 52 \
            or envelope[:8] != _DATABASE_BACKUP_MAGIC:
        raise ValueError("Tam veritabanı yedeği biçimi veya boyutu geçersiz.")
    salt, nonce = envelope[8:24], envelope[24:36]
    ciphertext_end = len(envelope) - 16
    decryptor = Cipher(
        algorithms.AES(_database_backup_key(salt, passphrase)),
        modes.GCM(nonce, envelope[ciphertext_end:]),
    ).decryptor()
    decryptor.authenticate_additional_data(_DATABASE_BACKUP_AAD)
    output_path = Path(output_path)
    try:
        with output_path.open("xb") as target:
            for offset in range(36, ciphertext_end, 1024 * 1024):
                target.write(decryptor.update(envelope[offset:min(offset + 1024 * 1024, ciphertext_end)]))
            target.write(decryptor.finalize())
    except InvalidTag as exc:
        output_path.unlink(missing_ok=True)
        raise ValueError("Parola yanlış veya şifreli veritabanı dosyası bozuk; kayıt değiştirilmedi.") from exc
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return _validate_database_file(output_path)


def create_encrypted_database_backup(store, passphrase):
    with tempfile.TemporaryDirectory(prefix="vardiya-db-backup-") as directory:
        snapshot = Path(directory) / "snapshot.sqlite3"
        target = sqlite3.connect(snapshot)
        try:
            store.connection.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite anlık görüntüsü bütünlük denetimini geçemedi.")
        finally:
            target.close()
        metadata = _validate_database_file(snapshot)
        return encrypt_database_file(snapshot, passphrase), metadata


def _validate_package(package):
    fields = {"format", "version", "id", "origin", "month", "people", "profiles", "base", "current"}
    if not isinstance(package, dict) or set(package) != fields \
            or package["format"] != "vardiya-work-package" or type(package["version"]) is not int \
            or package["version"] != 1 or any(
                not isinstance(package[key], str) or not re.fullmatch(r"[a-f0-9]{32}", package[key])
                for key in ("id", "origin")
            ):
        raise ValueError("Çalışma paketi biçimi geçersiz.")
    scheduling.validate_month(package["month"])
    if not isinstance(package["people"], list) or len(package["people"]) > 500:
        raise ValueError("Pakette en fazla 500 personel olabilir.")
    ids, names = set(), set()
    for person in package["people"]:
        if not isinstance(person, dict) or set(person) != {"id", "name", "active"} \
                or type(person["id"]) is not int or person["id"] < 1 \
                or not isinstance(person["name"], str) or not 1 <= len(person["name"].strip()) <= 80 \
                or type(person["active"]) is not bool or person["id"] in ids:
            raise ValueError("Paket personel bilgileri geçersiz veya yinelenmiş.")
        if controls.name_key(person["name"]) in names:
            raise ValueError("Pakette benzer adla birden fazla personel var; önce kaynakta ayırt edin.")
        ids.add(person["id"])
        names.add(controls.name_key(person["name"]))
    if not isinstance(package["profiles"], dict):
        raise ValueError("Paket sicil bilgileri geçersiz.")
    for key in ("base", "current"):
        content = package[key]
        if not isinstance(content, dict) or set(content) != {"schedule", "attendance"}:
            raise ValueError("Paket dönem içeriği geçersiz.")
        scheduling.normalize_schedule_entries(package["month"], content["schedule"], package["people"])
        _attendance_clean(package["month"], content["attendance"], package["people"])
    if len(_json(package)) > 8_000_000:
        raise ValueError("Çalışma paketi 8 MB sınırını aşıyor.")
    return package


def matching(store, package):
    package = _validate_package(package)
    current = store.people()
    profiles = store.employee_profiles()
    suggestions = []
    for person in package["people"]:
        registry = package["profiles"].get(str(person["id"]), {}).get("registry_no", "")
        candidates = [
            {"id": other["id"], "name": other["name"],
             "reason": "Sicil/bordro kodu aynı" if registry and profiles.get(str(other["id"]), {}).get("payroll_code") == registry
             else "Ad benzer; kullanıcı doğrulaması gerekir"}
            for other in current
            if controls.name_key(other["name"]) == controls.name_key(person["name"])
            or (registry and profiles.get(str(other["id"]), {}).get("payroll_code") == registry)
        ]
        suggestions.append({"source": person, "registry_no": registry, "candidates": candidates})
    return {"matching": suggestions, "people": current, "month": package["month"], "package_id": package["id"]}


def duplicate_review(store):
    initialize(store.connection)
    groups = {}
    profiles = store.employee_profiles()
    for person in store.people():
        registry = str(profiles.get(str(person["id"]), {}).get("payroll_code", "")).strip()
        for kind, value in (("registry", registry), ("name", controls.name_key(person["name"]))):
            if value:
                profile = profiles.get(str(person["id"]), {})
                groups.setdefault((kind, value), []).append({
                    **person,
                    "registry_no": registry,
                    "section": str(profile.get("home_section") or ""),
                    "skills": list(profile.get("skills") or []),
                })
    return {"groups": [
        {"kind": kind, "value": value, "people": people}
        for (kind, value), people in sorted(groups.items()) if len(people) > 1
    ], "missing_registry": [
        person for person in store.people()
        if not str(profiles.get(str(person["id"]), {}).get("payroll_code", "")).strip()
    ], "history": [
        dict(row) for row in store.connection.execute(
            "SELECT id,employee_id,candidate_id,decision,reason,employee_name,candidate_name,"
            "employee_registry,candidate_registry,employee_section,candidate_section,actor,decided_at "
            "FROM identity_match_decisions ORDER BY decided_at DESC,id DESC LIMIT 100"
        )]}


def record_identity_decision(store, payload):
    if not isinstance(payload, dict) or set(payload) != {
        "employee_id", "candidate_id", "decision", "reason",
    }:
        raise ValueError("Kimlik eşleştirme kararı geçersiz.")
    employee_id = payload["employee_id"]
    candidate_id = payload["candidate_id"]
    if (type(employee_id) is not int or type(candidate_id) is not int or
            employee_id < 1 or candidate_id < 1 or employee_id == candidate_id):
        raise ValueError("İki farklı personel kaydı seçilmelidir.")
    if payload["decision"] not in ("same", "different"):
        raise ValueError("Açık karar olarak aynı kişi veya farklı kişi seçilmelidir.")
    reason = " ".join(str(payload["reason"] or "").split())
    if not 5 <= len(reason) <= 250:
        raise ValueError("Karar gerekçesi 5-250 karakter olmalıdır.")
    people = {int(item["id"]): item for item in store.people()}
    profiles = store.employee_profiles()
    employee, candidate = people.get(employee_id), people.get(candidate_id)
    if employee is None or candidate is None:
        raise ValueError("Eşleştirilecek personel kaydı bulunamadı.")
    left = profiles.get(str(employee_id), {})
    right = profiles.get(str(candidate_id), {})
    decision_id = secrets.token_hex(12)
    decided_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    initialize(store.connection)
    store._backup_before_change()
    with store.connection:
        store.connection.execute(
            "INSERT INTO identity_match_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (decision_id, employee_id, candidate_id, payload["decision"], reason,
             employee["name"], candidate["name"], str(left.get("payroll_code") or ""),
             str(right.get("payroll_code") or ""), str(left.get("home_section") or ""),
             str(right.get("home_section") or ""), store.actor or store.user_name(), decided_at),
        )
        store.record_change("identity.match_decision", "Personel kimlik eşleştirme kararı kaydedildi", {
            "id": decision_id, "employee_id": employee_id, "candidate_id": candidate_id,
            "decision": payload["decision"],
        })
    return next(
        item for item in duplicate_review(store)["history"] if item["id"] == decision_id
    )


def _mapping(store, package, mapping):
    if not isinstance(mapping, dict) or set(mapping) != {str(person["id"]) for person in package["people"]}:
        raise ValueError("Her kaynak personel için açık eşleştirme yapın.")
    people = store.people()
    known = {person["id"] for person in people}
    used = set()
    for value in mapping.values():
        if type(value) is not int or value not in known or value in used:
            raise ValueError("Personel eşleştirmesi geçersiz veya bir hedef birden fazla kullanılmış.")
        used.add(value)
    return mapping


def _remap(content, mapping):
    content = json.loads(_json(content))
    for row in content["schedule"]:
        row["employee_id"] = mapping[str(row["employee_id"])]
    attendance = content["attendance"]
    attendance["overrides"] = {day: {str(mapping[str(person)]): code for person, code in values.items()}
                               for day, values in attendance["overrides"].items()}
    for field in ("deductions", "overtime"):
        attendance[field] = {str(mapping[str(person)]): amount for person, amount in attendance[field].items()}
    attendance["roster"] = {section: [mapping[str(person)] for person in values]
                            for section, values in attendance["roster"].items()}
    return content


def _flatten(content):
    result = {}
    for row in content["schedule"]:
        result[_json(["row", row["employee_id"], row["section"], row.get("slot")])] = True
        for day, value in row["days"].items():
            key = _json(["schedule", row["employee_id"], row["section"], row.get("slot"), day])
            if key in result:
                raise ValueError("Aynı personel/bölüm/satır/gün için yinelenen kayıt var.")
            result[key] = value
    attendance = content["attendance"]
    for day, values in attendance["overrides"].items():
        for person, code in values.items():
            result[_json(["override", str(person), day])] = code
    for field in ("deductions", "overtime", "roster"):
        for key, value in attendance[field].items():
            result[_json([field, key])] = value
    return result


def _inflate(store, month, values, current):
    content = {"schedule": [], "attendance": {"overrides": {}, "deductions": {}, "overtime": {}, "roster": {}}}
    rows = {}
    for row in current["schedule"]:
        key = (row["employee_id"], row["section"], row.get("slot"))
        if _json(["row", *key]) in values:
            rows[key] = {**row, "days": {}}
    for key, value in values.items():
        parts = json.loads(key)
        if parts[0] in ("schedule", "row"):
            person, section, slot = parts[1:4]
            row_key = (person, section, slot)
            if row_key not in rows:
                row = {"row_id": "exchange-" + _digest(list(row_key))[:20],
                       "employee_id": person, "section": section, "days": {}}
                if slot is not None:
                    row["slot"] = slot
                rows[row_key] = row
            if parts[0] == "schedule":
                rows[row_key]["days"][parts[4]] = value
        elif parts[0] == "override":
            content["attendance"]["overrides"].setdefault(parts[2], {})[parts[1]] = value
        else:
            content["attendance"][parts[0]][parts[1]] = value
    content["schedule"] = scheduling.normalize_schedule_entries(month, list(rows.values()), store.people())
    content["attendance"] = _attendance_clean(month, content["attendance"], store.people())
    return content


def package_preview(store, package, mapping):
    package = _validate_package(package)
    if store.connection.execute("SELECT 1 FROM continuity_receipts WHERE id=?", (package["id"],)).fetchone():
        raise ValueError("Bu paket daha önce alındı; ikinci kez uygulanmaz.")
    mapping = _mapping(store, package, mapping)
    month = package["month"]
    incoming_content = _remap(package["current"], mapping)
    local_content = _month_content(store, month)
    incoming, base, local = _flatten(incoming_content), _flatten(_remap(package["base"], mapping)), _flatten(local_content)
    changes = []
    for key in sorted(set(incoming) | set(base)):
        if incoming.get(key) == local.get(key):
            continue
        # First transfer into an empty cell is offered even when unchanged from export base.
        if incoming.get(key) == base.get(key) and key in local:
            continue
        conflict = local.get(key) != base.get(key) and (key in local or incoming.get(key) != base.get(key))
        changes.append({
            "key": key, "cell": json.loads(key), "before": local.get(key), "after": incoming.get(key),
            "base": base.get(key), "conflict": conflict,
        })
    return _preview(store, "package", {
        "package": package, "mapping": mapping, "changes": changes,
        "local": local_content,
    }, {"month": month, "changes": changes, "conflicts": sum(item["conflict"] for item in changes)})


def package_apply(store, preview_id, choices, confirmed):
    if confirmed is not True or not isinstance(choices, dict):
        raise ValueError("Paket aktarımı ve çakışma seçimleri onaylanmalıdır.")
    data = _load_preview(store, preview_id, "package")
    package = data["package"]
    conflicts = {item["key"] for item in data["changes"] if item["conflict"]}
    if set(choices) != conflicts or any(value not in ("local", "incoming") for value in choices.values()):
        raise ValueError("Her çakışma için mevcut veya gelen kaydı açıkça seçin.")
    month = package["month"]
    store.assert_periods_unlocked([month])
    backup = store.create_backup("before-restore")
    connection = store.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        _load_preview(store, preview_id, "package")
        store.assert_periods_unlocked([month])
        if connection.execute("SELECT 1 FROM continuity_receipts WHERE id=?", (package["id"],)).fetchone():
            raise ValueError("Paket daha önce uygulanmış.")
        values = _flatten(data["local"])
        for item in data["changes"]:
            if choices.get(item["key"]) == "local":
                continue
            if item["after"] is None:
                values.pop(item["key"], None)
            else:
                values[item["key"]] = item["after"]
        result = _inflate(store, month, values, data["local"])
        _persist_selections(store, [
            {"kind": kind, "month": month, "after": result[kind]} for kind in ("schedule", "attendance")
        ])
        connection.execute("INSERT INTO continuity_receipts VALUES (?,?,?,?)", (
            package["id"], package["origin"],
            _json({"month": month, "local_snapshot": result}), datetime.now(timezone.utc).isoformat(),
        ))
        connection.execute("DELETE FROM continuity_previews WHERE id=?", (preview_id,))
        store.record_change("continuity.package", "Karşılaştırılan çalışma paketi alındı", {
            "package_id": package["id"], "month": month, "conflicts": len(conflicts),
            "backup": backup["id"],
        })
        connection.commit()
        return {"received": True, "package_id": package["id"], "backup_id": backup["id"]}
    except Exception:
        connection.rollback()
        raise


def pdks_preview(store, month, filename, content, mapping):
    month = scheduling.validate_month(month)
    if not isinstance(filename, str) or not isinstance(content, str) or not isinstance(mapping, dict):
        raise ValueError("PDKS dosya ve eşleştirme bilgileri geçersiz.")
    try:
        raw = base64.b64decode(content, validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("PDKS dosyası okunamadı.") from exc
    if len(raw) > 5_000_000:
        raise ValueError("PDKS dosyası 5 MB sınırını aşıyor.")
    source_health = {}
    records = controls.parse_pdks(filename, raw, health=source_health, month=month)
    names = sorted({record["name"] for record in records})
    people = {person["id"]: person["name"] for person in store.people()}
    if not mapping:
        return {"matching": [
            {"name": name, "candidates": [
                {"id": person, "name": label} for person, label in people.items()
                if controls.name_key(label) == controls.name_key(name)
            ]} for name in names
        ], "people": [{"id": person, "name": name} for person, name in people.items()],
               "source_health": source_health}
    if set(mapping) != set(names) or any(type(value) is not int or value not in people for value in mapping.values()):
        raise ValueError("Her PDKS adı için kayıtlı personeli açıkça eşleştirin.")
    normalized = [{**record, "name": people[mapping[record["name"]]]} for record in records
                  if record["date"][:7] == month]
    if not normalized:
        raise ValueError("Dosyada seçili aya ait kayıt yok.")
    if len({(record["name"], record["date"]) for record in normalized}) != len(normalized):
        raise ValueError("Eşleştirme aynı kişi/gün için birden çok kayıt üretiyor; kaynak kayıtları birleştirin.")
    comparison = controls.pdks_compare(month, store.schedule_month(month)["entries"], store.people(), normalized)
    return _preview(store, "pdks", {
        "month": month, "records": normalized, "filename": Path(filename).name[:120],
        "source_health": source_health,
    }, {"month": month, "records": normalized, "comparison": comparison, "source_health": source_health,
        "notice": "Bu işlem yalnız PDKS kaynağını saklar. Puantaj veya vardiya kendiliğinden değiştirilmez."})


def pdks_apply(store, preview_id, confirmed):
    if confirmed is not True:
        raise ValueError("PDKS aktarımını onaylayın.")
    data = _load_preview(store, preview_id, "pdks")
    store.assert_periods_unlocked([data["month"]])
    store._backup_before_change()
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        _load_preview(store, preview_id, "pdks")
        store.assert_periods_unlocked([data["month"]])
        saved = store._setting("pdks_records", {})
        saved[data["month"]] = {
            "file": data["filename"], "records": data["records"],
            "imported_at": datetime.now(timezone.utc).isoformat(), "actor": store.user_name(),
            "source_health": data.get("source_health"),
        }
        store._write_setting("pdks_records", saved)
        store.connection.execute("DELETE FROM continuity_previews WHERE id=?", (preview_id,))
        store.record_change("pdks.matched_import", "Eşleştirilmiş PDKS kaynağı kaydedildi", {
            "month": data["month"], "records": len(data["records"]),
        })
        store.connection.commit()
        return {"saved": True, "records": len(data["records"]), "attendance_changed": False}
    except Exception:
        store.connection.rollback()
        raise


def handle_get(handler, path, query):
    if not path.startswith("/api/continuity/"):
        return False
    with handler._store() as store:
        if not _admin(handler, store):
            return True
        if path == "/api/continuity/status":
            actions = [dict(row) for row in store.connection.execute(
                "SELECT id,kind,backup_id,created_at,undone FROM continuity_actions ORDER BY created_at DESC LIMIT 20"
            )]
            handler._json({
                "target": str(store.path.resolve()), "actions": actions,
                "receipts": [dict(row) for row in store.connection.execute(
                    "SELECT id,received_at FROM continuity_receipts ORDER BY received_at DESC LIMIT 30"
                )],
            })
            return True
        if path == "/api/continuity/package":
            handler._json({"package": export_package(
                store, query.get("month", [""])[0], query.get("lineage", [None])[0],
            )})
            return True
        if path == "/api/continuity/duplicates":
            handler._json(duplicate_review(store))
            return True
    return False


def handle_write(handler, method, path):
    if not path.startswith("/api/continuity/") or method != "POST":
        return False
    suffix = path.removeprefix("/api/continuity/")
    if suffix == "database-backup/export":
        payload = handler._read_json(2048)
        if not isinstance(payload, dict) or set(payload) != {"passphrase"}:
            raise ValueError("Tam yedek dışa aktarma isteği geçersiz.")
        with handler._store() as store:
            if _admin(handler, store):
                envelope, metadata = create_encrypted_database_backup(store, payload["passphrase"])
                handler._send(envelope, "application/octet-stream", filename="vardiya-tam-veritabani.vpsql")
        return True
    if suffix in ("database-backup/preview", "database-backup/restore"):
        envelope, passphrase = _read_encrypted_database_upload(handler)
        with handler._store() as store:
            if not _admin(handler, store):
                return True
            envelope_hash = hashlib.sha256(envelope).hexdigest()
            if suffix.endswith("/preview"):
                with tempfile.TemporaryDirectory(prefix="vardiya-db-preview-") as directory:
                    metadata = decrypt_database_file(envelope, passphrase, Path(directory) / "preview.sqlite3")
                handler._json(_preview(
                    store, "database-backup",
                    {"envelope_sha256": envelope_hash, "metadata": metadata},
                    {"metadata": metadata, "encrypted_size_bytes": len(envelope)},
                ))
                return True
            preview_id = handler.headers.get("X-VP-Backup-Preview", "")
            if handler.headers.get("X-VP-Confirm-Restore") != "yes":
                raise ValueError("Tam veritabanı geri yükleme ayrıca onaylanmalıdır.")
            preview = _load_preview(store, preview_id, "database-backup")
            if preview.get("envelope_sha256") != envelope_hash:
                raise ValueError("Önizlemeden farklı bir şifreli dosya seçildi; hiçbir kayıt değiştirilmedi.")
            with tempfile.TemporaryDirectory(prefix="vardiya-db-restore-") as directory:
                snapshot = Path(directory) / "restore.sqlite3"
                metadata = decrypt_database_file(envelope, passphrase, snapshot)
                if metadata != preview.get("metadata"):
                    raise ValueError("Yedek önizlemesi değişti; hiçbir kayıt değiştirilmedi.")
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup_id = f"manual-{timestamp}-{secrets.token_hex(4)}.sqlite3"
                store.backup_directory.mkdir(parents=True, exist_ok=True)
                staged = store._backup_path(backup_id)
                try:
                    with snapshot.open("rb") as source, staged.open("xb") as target:
                        shutil.copyfileobj(source, target, 1024 * 1024)
                    store._verify_backup_file(staged)
                    restored = store.restore_backup(backup_id)
                finally:
                    staged.unlink(missing_ok=True)
            initialize(store.connection)
            store.connection.execute("DELETE FROM continuity_previews")
            store.record_change(
                "continuity.database_restore", "Şifreli tam veritabanı yedeği geri yüklendi",
                {"source_sha256": envelope_hash, "schema_version": metadata["schema_version"]},
            )
            store.connection.commit()
            sessions = getattr(handler.server, "sessions", None)
            if isinstance(sessions, dict):
                sessions.clear()
            phone = getattr(handler.server, "phone", None)
            if phone and hasattr(phone, "sessions") and hasattr(phone, "guard"):
                with phone.guard:
                    phone.sessions.clear()
            handler._json({
                "restored": metadata, "safety_backup": restored["safety_backup"],
                "message": "Yedek doğrulandı ve geri yüklendi. Güvenlik için oturumlar kapatıldı; uygulamayı yenileyin.",
            })
        return True
    payload = handler._read_json(12_000_000)
    if not isinstance(payload, dict):
        raise ValueError("Veri işlemi isteği geçersiz.")
    routes = {
        "migration/preview": ({"source"}, lambda store: migration_preview(store, payload["source"])),
        "migration/apply": ({"preview_id", "confirmed"}, lambda store: apply_migration(store, **payload)),
        "migration/undo": ({"action_id", "confirmed"}, lambda store: undo_migration(store, **payload)),
        "backup/preview": ({"backup_id", "selections"}, lambda store: backup_preview(store, **payload)),
        "backup/apply": ({"preview_id", "confirmed"}, lambda store: backup_apply(store, **payload)),
        "package/matching": ({"package"}, lambda store: matching(store, payload["package"])),
        "package/encrypt": ({"package", "passphrase"}, lambda store: {
            "envelope": encrypt_package(payload["package"], payload["passphrase"]),
        }),
        "package/decrypt": ({"envelope", "passphrase"}, lambda store: {
            "package": decrypt_package(payload["envelope"], payload["passphrase"]),
        }),
        "package/preview": ({"package", "mapping"}, lambda store: package_preview(store, **payload)),
        "package/apply": ({"preview_id", "choices", "confirmed"}, lambda store: package_apply(store, **payload)),
        "duplicates/decision": (
            {"employee_id", "candidate_id", "decision", "reason"},
            lambda store: record_identity_decision(store, payload),
        ),
        "pdks/preview": ({"month", "filename", "content", "mapping"}, lambda store: pdks_preview(store, **payload)),
        "pdks/apply": ({"preview_id", "confirmed"}, lambda store: pdks_apply(store, **payload)),
    }
    if suffix not in routes:
        return False
    fields, action = routes[suffix]
    if set(payload) != fields:
        raise ValueError("Veri işlemi alanları eksik veya beklenmeyen alan içeriyor.")
    with handler._store() as store:
        if _admin(handler, store):
            handler._json(action(store))
    return True


def _read_encrypted_database_upload(handler):
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Şifreli yedek boyutu geçersiz.") from exc
    if handler.headers.get("Content-Type", "").split(";")[0] != "application/octet-stream" \
            or not 52 <= length <= MAX_DATABASE_BACKUP_BYTES + 52:
        raise ValueError("Şifreli tam veritabanı yedeği geçersiz veya 500 MB sınırını aşıyor.")
    envelope = handler.rfile.read(length)
    if len(envelope) != length:
        raise ValueError("Şifreli yedek aktarımı tamamlanmadı.")
    try:
        password_bytes = base64.b64decode(handler.headers.get("X-VP-Backup-Passphrase", ""), validate=True)
        passphrase = password_bytes.decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("Şifreli yedek parolası geçersiz.") from exc
    _validate_database_backup_passphrase(passphrase)
    return envelope, passphrase
