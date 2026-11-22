from __future__ import annotations

import calendar
import base64
import ctypes
import hashlib
import io
import json
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import threading
import zipfile
from contextlib import closing, suppress
from ctypes import wintypes
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import scheduling
from storage import Store


_KINDS = {"attendance", "schedule", "manager-summary", "month-package"}
_CADENCES = {"hourly", "daily", "weekly", "monthly"}
_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
_MAX_JOBS = 20
MAX_ATTACHMENTS = 4
REHEARSAL_DAYS = 7
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
# Failed automatic deliveries are retried after these delays (1 attempt + 3 retries).
RETRY_DELAYS = (timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=60))
HISTORY_LIMIT = 200
# A delivery still marked "sending" after this long was interrupted; only a person may resend it.
_STALE_SENDING = timedelta(minutes=10)
_UNKNOWN_DETAIL = "Sunucunun iletiyi kabul edip etmediği doğrulanamadı. Yeniden gönderim mükerrer ileti oluşturabilir."
_RESEND_STATES = ("retry", "error", "unknown", "partial")
# How far back the latest missed occurrence may be caught up once (hourly: current hour only).
_CATCH_UP_WINDOWS = {
    "hourly": timedelta(0), "daily": timedelta(days=1),
    "weekly": timedelta(days=7), "monthly": timedelta(days=31),
}
_JOB_KEYS = {
    "id", "kind", "cadence", "weekday", "month_day", "hour", "minute",
    "to", "subject", "body", "enabled",
}
_SCHEDULE_FIELDS = ("cadence", "weekday", "month_day", "hour", "minute")
_JOB_COLUMNS = {
    "kinds": "TEXT NOT NULL DEFAULT ''",
    "source_revisions": "TEXT NOT NULL DEFAULT ''",
    "active_since": "TEXT NOT NULL DEFAULT ''",
    "pending_occurrence": "TEXT NOT NULL DEFAULT ''",
    "pending_month": "TEXT NOT NULL DEFAULT ''",
    "pending_week": "INTEGER NOT NULL DEFAULT 0",
    "attempts": "INTEGER NOT NULL DEFAULT 0",
    "next_retry_at": "TEXT NOT NULL DEFAULT ''",
    "last_attempt_at": "TEXT NOT NULL DEFAULT ''",
    "last_sent_at": "TEXT NOT NULL DEFAULT ''",
}
_STATE_DEFAULTS = {
    "last_occurrence": "", "last_status": "", "last_error": "", "pending_occurrence": "",
    "pending_month": "", "pending_week": 0, "source_revisions": "", "attempts": 0, "next_retry_at": "",
    "last_attempt_at": "", "last_sent_at": "",
}
_BLOB = ctypes.c_ubyte


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(_BLOB))]


def initialize(connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS mail_delivery_settings (
            id INTEGER PRIMARY KEY CHECK(id=1),
            host TEXT NOT NULL,
            port INTEGER NOT NULL,
            username TEXT NOT NULL,
            from_address TEXT NOT NULL,
            use_ssl INTEGER NOT NULL,
            enabled INTEGER NOT NULL,
            password_cipher BLOB NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mail_automation_jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            cadence TEXT NOT NULL,
            weekday INTEGER,
            month_day INTEGER,
            hour INTEGER NOT NULL,
            minute INTEGER NOT NULL,
            recipients TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            last_occurrence TEXT NOT NULL DEFAULT '',
            last_status TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mail_delivery_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempted_at TEXT NOT NULL,
            job_id TEXT NOT NULL,
            source TEXT NOT NULL,
            occurrence TEXT NOT NULL,
            recipients INTEGER NOT NULL,
            kinds TEXT NOT NULL,
            month TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mail_recipient_permissions (
            email TEXT PRIMARY KEY COLLATE NOCASE,
            employee_id INTEGER NOT NULL REFERENCES employees(id),
            section_scope TEXT NOT NULL,
            all_sections INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(mail_automation_jobs)")}
    for name, definition in _JOB_COLUMNS.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE mail_automation_jobs ADD COLUMN {name} {definition}")
    if "active_since" not in columns:
        # Plans saved by older versions only catch up occurrences after their last save.
        connection.execute("UPDATE mail_automation_jobs SET active_since=updated_at WHERE active_since=''")
        connection.commit()


def recipient_permissions(store) -> list[dict[str, Any]]:
    initialize(store.connection)
    rows = store.connection.execute(
        "SELECT r.email,r.employee_id,r.section_scope,r.all_sections,r.updated_at,e.name,e.active "
        "FROM mail_recipient_permissions r JOIN employees e ON e.id=r.employee_id "
        "ORDER BY e.active DESC,e.name COLLATE NOCASE,r.email COLLATE NOCASE"
    )
    return [{
        "email": row["email"], "employee_id": int(row["employee_id"]), "name": row["name"],
        "active": bool(row["active"]), "section_scope": row["section_scope"],
        "all_sections": bool(row["all_sections"]), "updated_at": row["updated_at"],
    } for row in rows]


def save_recipient_permission(store, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "email", "employee_id", "section_scope", "all_sections",
    }:
        raise ValueError("Alıcı yetki kaydı geçersiz.")
    recipients = _emails(payload["email"])
    if len(recipients) != 1:
        raise ValueError("Her alıcıya ayrı yetki kaydı girin.")
    employee_id = _integer(payload["employee_id"], "Personel", 1, 2_147_483_647)
    if not isinstance(payload["section_scope"], str) or len(payload["section_scope"]) > 80:
        raise ValueError("Bölüm kapsamı geçersiz.")
    if not isinstance(payload["all_sections"], bool):
        raise ValueError("Bütün bölümler seçeneği geçersiz.")
    section = payload["section_scope"].strip()
    if payload["all_sections"]:
        if section:
            raise ValueError("Bütün bölümler yetkisinde ayrıca bölüm seçmeyin.")
    elif section not in scheduling.section_names():
        raise ValueError("Bölüm kapsamı kayıtlı bölümlerden biri olmalıdır.")
    person = store.connection.execute(
        "SELECT 1 FROM employees WHERE id=?", (employee_id,),
    ).fetchone()
    if not person:
        raise ValueError("Alıcıyı bağlı personel kaydı bulunamadı.")
    now = _stamp(datetime.now().astimezone())
    with store.connection:
        store.connection.execute(
            "INSERT INTO mail_recipient_permissions(email,employee_id,section_scope,all_sections,updated_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET employee_id=excluded.employee_id, "
            "section_scope=excluded.section_scope,all_sections=excluded.all_sections,updated_at=excluded.updated_at",
            (recipients[0], employee_id, section, int(payload["all_sections"]), now),
        )
        store.record_change(
            "settings.mail_recipient_permission", "E-posta alıcı yetkisi güncellendi",
            {"employee_id": employee_id, "all_sections": payload["all_sections"],
             "section_scope": section},
        )
    return next(item for item in recipient_permissions(store) if item["email"].casefold() == recipients[0].casefold())


def remove_recipient_permission(store, email: Any) -> bool:
    recipients = _emails(email)
    if len(recipients) != 1:
        raise ValueError("Tek bir alıcı seçin.")
    with store.connection:
        removed = store.connection.execute(
            "DELETE FROM mail_recipient_permissions WHERE email=? COLLATE NOCASE", (recipients[0],),
        ).rowcount
        if removed:
            store.record_change(
                "settings.mail_recipient_permission", "E-posta alıcı yetkisi kaldırıldı",
                {"email_hash": hashlib.sha256(recipients[0].casefold().encode("utf-8")).hexdigest()[:12]},
            )
    return bool(removed)


class _RecipientScopeError(ValueError):
    pass


def _recipient_scopes(store, recipients: list[str], kinds: list[str]) -> dict[str, str]:
    initialize(store.connection)
    rows = {
        row["email"].casefold(): row for row in store.connection.execute(
            "SELECT r.email,r.employee_id,r.section_scope,r.all_sections,e.active "
            "FROM mail_recipient_permissions r JOIN employees e ON e.id=r.employee_id"
        )
    }
    scopes = {}
    for email in recipients:
        record = rows.get(email.casefold())
        if record is None:
            raise _RecipientScopeError("Bir e-posta alıcısı için sunucuda gözden geçirilmiş etkin yetki yok.")
        if not record["active"]:
            raise _RecipientScopeError("Bir e-posta alıcısı pasif personele bağlı; gönderim engellendi.")
        if not record["all_sections"] and any(kind in ("manager-summary", "month-package") for kind in kinds):
            raise _RecipientScopeError(
                f"Bir alıcı yalnız {record['section_scope']} bölümüne yetkili; tüm bölümleri içeren ekleri ayırın."
            )
        if not record["all_sections"] and record["section_scope"] not in scheduling.section_names():
            raise _RecipientScopeError("Bir alıcının bölüm yetkisi artık geçerli değil; yetkiyi gözden geçirin.")
        scopes[email] = "" if record["all_sections"] else record["section_scope"]
    return scopes


def _source_revision_snapshot(store, month: str) -> str:
    periods = scheduling.adjacent_months(month)
    snapshot = {
        period: {
            "schedule": store.schedule_month(period)["revision"],
            "attendance": store.attendance_month(period)["revision"],
        }
        for period in periods
    }
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))


def _protect_password(password: str) -> bytes:
    if os.name != "nt":
        raise ValueError("Otomatik gönderim için Windows kullanıcı hesabıyla şifreleme gerekir.")
    source_bytes = password.encode("utf-8")
    source_buffer = (_BLOB * len(source_bytes)).from_buffer_copy(source_bytes)
    source = _DataBlob(len(source_bytes), ctypes.cast(source_buffer, ctypes.POINTER(_BLOB)))
    output = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptProtectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output),
    ):
        raise OSError(ctypes.get_last_error(), "E-posta parolası güvenli olarak saklanamadı.")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def _unprotect_password(ciphertext: bytes) -> str:
    if os.name != "nt":
        raise ValueError("E-posta parolası yalnız şifreleyen Windows kullanıcı hesabında açılabilir.")
    source_buffer = (_BLOB * len(ciphertext)).from_buffer_copy(ciphertext)
    source = _DataBlob(len(ciphertext), ctypes.cast(source_buffer, ctypes.POINTER(_BLOB)))
    output = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output),
    ):
        raise OSError(ctypes.get_last_error(), "Kayıtlı e-posta parolası bu kullanıcıyla açılamadı.")
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(output.pbData)


def _text(value: Any, label: str, maximum: int, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
        raise ValueError(f"{label} geçersiz veya çok uzun.")
    if "\x00" in value:
        raise ValueError(f"{label} geçersiz.")
    return value.strip()


def _emails(value: Any) -> list[str]:
    text = _text(value, "Alıcı adresleri", 500, allow_empty=False)
    result = []
    seen = set()
    for item in re.split(r"[;,]", text):
        address = item.strip()
        if not address:
            continue
        if not _EMAIL.fullmatch(address):
            raise ValueError("Alıcı e-posta adreslerini kontrol edin.")
        key = address.casefold()
        if key not in seen:
            result.append(address)
            seen.add(key)
    if not result:
        raise ValueError("En az bir alıcı e-posta adresi girin.")
    return result


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{label} geçersiz.")
    return value


def normalize_kinds(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("En az bir e-posta eki seçin.")
    if len(value) > MAX_ATTACHMENTS:
        raise ValueError(f"En fazla {MAX_ATTACHMENTS} e-posta eki seçilebilir.")
    if any(not isinstance(item, str) or item not in _KINDS for item in value):
        raise ValueError("E-posta eki türü geçersiz.")
    if len(set(value)) != len(value):
        raise ValueError("Aynı e-posta eki birden fazla seçilemez.")
    return list(value)


def draft_kinds(payload: dict[str, Any]) -> list[str]:
    """Attachment kinds of a mail draft; accepts ``kinds`` or the legacy single ``kind``."""
    if ("kind" in payload) == ("kinds" in payload):
        raise ValueError("E-posta taslağı bilgisi geçersiz.")
    if "kinds" in payload:
        return normalize_kinds(payload["kinds"])
    kind = payload["kind"]
    if not isinstance(kind, str) or kind not in _KINDS:
        raise ValueError("E-posta eki türü geçersiz.")
    return [kind]


def _field(row, name: str, default: Any = None) -> Any:
    try:
        value = row[name]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _job_kinds(job) -> list[str]:
    stored = [item for item in str(_field(job, "kinds", "")).split(",") if item]
    return stored or [job["kind"]]


def _recipient_count(value: Any) -> int:
    try:
        return len(_emails(value))
    except ValueError:
        return 0


def _local(moment: datetime) -> datetime:
    """Naive local wall-clock time used for schedule arithmetic."""
    return moment.astimezone().replace(tzinfo=None)


def _stamp(moment: datetime) -> str:
    return moment.astimezone().isoformat(timespec="seconds")


def _parse_stamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone()
    except ValueError:
        return None


def _is_stale_sending(row, now: datetime) -> bool:
    started = _parse_stamp(_field(row, "last_attempt_at", ""))
    return started is None or now - started > _STALE_SENDING


def _history(connection) -> list[dict[str, Any]]:
    return [{
        "id": row["id"], "attempted_at": row["attempted_at"], "job_id": row["job_id"],
        "source": row["source"], "occurrence": row["occurrence"],
        "recipients": int(row["recipients"]),
        "kinds": [item for item in row["kinds"].split(",") if item],
        "month": row["month"], "attempt": int(row["attempt"]),
        "status": row["status"], "error": row["error"],
    } for row in connection.execute(
        "SELECT * FROM mail_delivery_history ORDER BY id DESC LIMIT ?", (HISTORY_LIMIT,),
    )]


def _last_result(row, now: datetime) -> tuple[str, str]:
    if row["last_status"] == "sending" and _is_stale_sending(row, now):
        return "unknown", _UNKNOWN_DETAIL
    return row["last_status"], row["last_error"]


def _job_view(row, ready: bool, now: datetime) -> dict[str, Any]:
    status, error = _last_result(row, now)
    due_now = False
    next_run = None
    if ready and row["enabled"]:
        if status == "retry" and row["next_retry_at"]:
            next_run = row["next_retry_at"]
        else:
            due = _find_due(row, now)
            due_now = bool(due and due[0] != row["last_occurrence"])
            next_run = _stamp(now) if due_now else _stamp(_next_occurrence(row, _local(now)).astimezone())
    return {
        "id": row["id"], "kind": row["kind"], "kinds": _job_kinds(row), "cadence": row["cadence"],
        "weekday": row["weekday"], "month_day": row["month_day"],
        "hour": int(row["hour"]), "minute": int(row["minute"]),
        "to": row["recipients"], "subject": row["subject"], "body": row["body"],
        "enabled": bool(row["enabled"]), "last_status": status,
        "last_error": error, "attempts": int(row["attempts"]),
        "max_attempts": len(RETRY_DELAYS) + 1,
        "last_attempt_at": row["last_attempt_at"], "last_sent_at": row["last_sent_at"],
        "next_retry_at": row["next_retry_at"], "pending_month": row["pending_month"],
        "next_run_at": next_run, "due_now": due_now,
        "can_resend": bool(ready and row["enabled"] and status in _RESEND_STATES),
        "can_send_now": bool(ready and row["enabled"] and status != "sending"),
    }


def _configuration_rows(connection):
    settings = connection.execute(
        "SELECT * FROM mail_delivery_settings WHERE id=1"
    ).fetchone()
    jobs = connection.execute(
        "SELECT * FROM mail_automation_jobs ORDER BY updated_at DESC, id"
    ).fetchall()
    return settings, jobs


def get_configuration(store, now: datetime | None = None) -> dict[str, Any]:
    initialize(store.connection)
    now = (now or datetime.now()).astimezone()
    settings, jobs = _configuration_rows(store.connection)
    delivery = {
        "host": settings["host"] if settings else "",
        "port": int(settings["port"]) if settings else 587,
        "username": settings["username"] if settings else "",
        "from_address": settings["from_address"] if settings else "",
        "use_ssl": bool(settings["use_ssl"]) if settings else False,
        "enabled": bool(settings["enabled"]) if settings else False,
        "configured": bool(settings and settings["password_cipher"]),
    }
    ready = delivery["enabled"] and delivery["configured"]
    return {
        "settings": delivery,
        "jobs": [_job_view(row, ready, now) for row in jobs],
        "history": _history(store.connection),
        "retry_policy": {
            "delays_minutes": [int(delay.total_seconds() // 60) for delay in RETRY_DELAYS],
            "max_retries": len(RETRY_DELAYS),
        },
    }


def _normalize_settings(payload: Any, old) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "host", "port", "username", "from_address", "use_ssl", "enabled", "password",
    }:
        raise ValueError("SMTP ayarları geçersiz.")
    host = _text(payload["host"], "SMTP sunucusu", 255)
    if host and not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise ValueError("SMTP sunucusu yalnız alan adı veya IP biçiminde olmalıdır.")
    port = _integer(payload["port"], "SMTP bağlantı noktası", 1, 65535)
    username = _text(payload["username"], "SMTP kullanıcı adı", 254)
    from_address = _text(payload["from_address"], "Gönderen adresi", 254)
    if from_address and not _EMAIL.fullmatch(from_address):
        raise ValueError("Gönderen e-posta adresi geçersiz.")
    if not isinstance(payload["use_ssl"], bool) or not isinstance(payload["enabled"], bool):
        raise ValueError("SMTP güvenlik ve etkinlik seçenekleri geçersiz.")
    password = _text(payload["password"], "SMTP parolası", 500)
    old_password = bytes(old["password_cipher"]) if old else b""
    if password:
        cipher = _protect_password(password)
    elif old_password and old["username"] == username:
        cipher = old_password
    else:
        cipher = b""
    if payload["enabled"] and not (host and username and from_address and cipher):
        raise ValueError("SMTP'yi etkinleştirmek için sunucu, kullanıcı, gönderen ve parola bilgilerini tamamlayın.")
    return {
        "host": host, "port": port, "username": username, "from_address": from_address,
        "use_ssl": payload["use_ssl"], "enabled": payload["enabled"], "password_cipher": cipher,
    }


def _normalize_job(payload: Any, existing_ids: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) not in (_JOB_KEYS, _JOB_KEYS | {"kinds"}):
        raise ValueError("Otomatik rapor planı geçersiz.")
    job_id = payload["id"]
    if job_id:
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{24}", job_id) or job_id not in existing_ids:
            raise ValueError("Otomatik rapor kimliği geçersiz.")
    else:
        job_id = secrets.token_hex(12)
    kind = payload["kind"]
    cadence = payload["cadence"]
    if not isinstance(kind, str) or not isinstance(cadence, str) or kind not in _KINDS or cadence not in _CADENCES:
        raise ValueError("Rapor türü veya gönderim sıklığı geçersiz.")
    kinds = normalize_kinds(payload["kinds"]) if "kinds" in payload else [kind]
    if kind not in kinds:
        raise ValueError("Rapor türü veya gönderim sıklığı geçersiz.")
    weekday = payload["weekday"]
    month_day = payload["month_day"]
    if cadence == "hourly":
        weekday = month_day = None
    elif cadence == "weekly":
        weekday = _integer(weekday, "Gönderim günü", 0, 6)
        month_day = None
    elif cadence == "monthly":
        # Days missing from shorter months are sent on the month's last day.
        month_day = _integer(month_day, "Ayın gönderim günü", 1, 31)
        weekday = None
    else:
        weekday = month_day = None
    hour = _integer(payload["hour"], "Saat", 0, 23)
    minute = _integer(payload["minute"], "Dakika", 0, 59)
    if cadence == "hourly":
        hour = 0
    if not isinstance(payload["enabled"], bool):
        raise ValueError("Otomatik gönderim durumu geçersiz.")
    subject = _text(payload["subject"], "Konu", 200)
    if "\r" in subject or "\n" in subject:
        raise ValueError("E-posta konusu tek satır olmalıdır.")
    return {
        "id": job_id, "kind": kinds[0], "kinds": kinds, "cadence": cadence,
        "weekday": weekday, "month_day": month_day, "hour": hour, "minute": minute,
        "recipients": "; ".join(_emails(payload["to"])),
        "subject": subject,
        "body": _text(payload["body"], "İleti", 5000),
        "enabled": payload["enabled"],
    }


def save_configuration(store, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"settings", "jobs"}:
        raise ValueError("E-posta otomasyon ayarları geçersiz.")
    connection = store.connection
    initialize(connection)
    old_settings, old_jobs = _configuration_rows(connection)
    if not isinstance(payload["jobs"], list) or len(payload["jobs"]) > _MAX_JOBS:
        raise ValueError(f"En fazla {_MAX_JOBS} otomatik rapor planı tanımlanabilir.")
    normalized_settings = _normalize_settings(payload["settings"], old_settings)
    existing_ids = {row["id"] for row in old_jobs}
    normalized_jobs = [_normalize_job(item, existing_ids) for item in payload["jobs"]]
    ids = [item["id"] for item in normalized_jobs]
    if len(set(ids)) != len(ids):
        raise ValueError("Otomatik rapor kimlikleri yinelenemez.")
    if any(item["enabled"] for item in normalized_jobs) and not normalized_settings["enabled"]:
        raise ValueError("Otomatik rapor göndermeden önce SMTP ayarlarını etkinleştirin.")
    if any(item["enabled"] for item in normalized_jobs) and not normalized_settings["password_cipher"]:
        raise ValueError("Etkin otomatik rapor için SMTP parolası gereklidir.")
    now = _stamp(datetime.now())
    store._backup_before_change()
    connection.execute("BEGIN IMMEDIATE")
    try:
        # Re-read inside the write lock so delivery state changed by the scheduler is kept.
        old_by_id = {
            row["id"]: row for row in connection.execute("SELECT * FROM mail_automation_jobs")
        }
        connection.execute(
            "INSERT INTO mail_delivery_settings "
            "(id, host, port, username, from_address, use_ssl, enabled, password_cipher, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "host=excluded.host, port=excluded.port, username=excluded.username, "
            "from_address=excluded.from_address, use_ssl=excluded.use_ssl, "
            "enabled=excluded.enabled, password_cipher=excluded.password_cipher, updated_at=excluded.updated_at",
            (normalized_settings["host"], normalized_settings["port"], normalized_settings["username"],
             normalized_settings["from_address"], int(normalized_settings["use_ssl"]),
             int(normalized_settings["enabled"]), normalized_settings["password_cipher"], now),
        )
        connection.execute("DELETE FROM mail_automation_jobs")
        for item in normalized_jobs:
            old = old_by_id.get(item["id"])
            schedule_changed = old is None or (item["enabled"] and not old["enabled"]) or any(
                old[name] != item[name] for name in _SCHEDULE_FIELDS
            )
            scope_changed = old is not None and (
                _job_kinds(old) != item["kinds"]
                or any(old[name] != item[name] for name in ("recipients", "subject", "body"))
            )
            revised = schedule_changed or scope_changed
            if old and old["last_status"] == "sending" and revised and not _is_stale_sending(old, datetime.now().astimezone()):
                raise ValueError("Gönderim sürerken plan değiştirilemez.")
            values = {
                "id": item["id"], "kind": item["kind"], "kinds": ",".join(item["kinds"]),
                "cadence": item["cadence"], "weekday": item["weekday"], "month_day": item["month_day"],
                "hour": item["hour"], "minute": item["minute"], "recipients": item["recipients"],
                "subject": item["subject"], "body": item["body"], "enabled": int(item["enabled"]),
                **{name: old[name] if old else default for name, default in _STATE_DEFAULTS.items()},
                # Missed occurrences are caught up only while the current schedule was active.
                "active_since": now if revised else (old["active_since"] or old["updated_at"]),
                "updated_at": now,
            }
            if revised and old and (old["pending_occurrence"] or old["pending_month"]):
                values.update(last_status="revised", pending_occurrence="", pending_month="", pending_week=0,
                              source_revisions="", attempts=0, next_retry_at="",
                              last_error="Plan değişti; önceki dönemin bekleyen gönderimi iptal edildi.")
            connection.execute(
                f"INSERT INTO mail_automation_jobs ({', '.join(values)}) "
                f"VALUES ({', '.join('?' for _ in values)})",
                tuple(values.values()),
            )
        store.record_change(
            "settings.mail_automation", "Otomatik rapor ve e-posta ayarları güncellendi",
            {"jobs": len(normalized_jobs), "enabled_jobs": sum(item["enabled"] for item in normalized_jobs)},
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return get_configuration(store)


def _monthly_moment(year: int, month: int, job) -> datetime:
    day = min(int(job["month_day"]), calendar.monthrange(year, month)[1])
    return datetime(year, month, day, int(job["hour"]), int(job["minute"]))


def _latest_occurrence(job, local: datetime) -> datetime | None:
    """Most recent scheduled time at or before ``local`` (naive local time)."""
    cadence = job["cadence"]
    hour, minute = int(job["hour"]), int(job["minute"])
    if cadence == "hourly":
        moment = local.replace(minute=minute, second=0, microsecond=0)
        return moment if moment <= local else moment - timedelta(hours=1)
    if cadence == "daily":
        moment = datetime(local.year, local.month, local.day, hour, minute)
        return moment if moment <= local else moment - timedelta(days=1)
    if cadence == "weekly":
        day = local.date() - timedelta(days=(local.weekday() - int(job["weekday"])) % 7)
        moment = datetime(day.year, day.month, day.day, hour, minute)
        return moment if moment <= local else moment - timedelta(days=7)
    if cadence == "monthly":
        moment = _monthly_moment(local.year, local.month, job)
        if moment > local:
            previous = local.replace(day=1) - timedelta(days=1)
            moment = _monthly_moment(previous.year, previous.month, job)
        return moment
    return None


def _next_occurrence(job, local: datetime) -> datetime | None:
    latest = _latest_occurrence(job, local)
    if latest is None:
        return None
    cadence = job["cadence"]
    if cadence == "hourly":
        return latest + timedelta(hours=1)
    if cadence == "daily":
        return latest + timedelta(days=1)
    if cadence == "weekly":
        return latest + timedelta(days=7)
    return _monthly_moment(latest.year + latest.month // 12, latest.month % 12 + 1, job)


def _report_week(cadence: str, month: str, day: date) -> int:
    starts = scheduling.week_starts(month)
    report_date = day if cadence != "monthly" else date.fromisoformat(f"{month}-01")
    return next((index for index, start in enumerate(starts)
                 if start <= report_date <= start + timedelta(days=6)), 0)


def _occurrence_details(job, moment: datetime) -> tuple[str, str, int]:
    cadence = job["cadence"]
    if cadence == "hourly":
        key = f"hourly:{moment.strftime('%Y-%m-%d-%H')}"
        month = moment.strftime("%Y-%m")
    elif cadence == "daily":
        key = f"daily:{moment.date().isoformat()}"
        month = moment.strftime("%Y-%m")
    elif cadence == "weekly":
        iso = moment.isocalendar()
        key = f"weekly:{iso.year}-{iso.week:02d}"
        month = moment.strftime("%Y-%m")
    else:
        key = f"monthly:{moment.strftime('%Y-%m')}"
        month = (moment.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    return key, month, _report_week(cadence, month, moment.date())


def _message_fields(job, month: str, labels) -> dict[str, str]:
    """One envelope's content, shared by automated delivery and its metadata preview."""
    names = [labels[kind] for kind in _job_kinds(job)]
    label = f"{scheduling.MONTH_NAMES[int(month[5:]) - 1]} {month[:4]}"
    if len(names) == 1:
        body = f"Merhaba,\n\n{label} dönemine ait {names[0].lower()} ektedir.\n\nİyi çalışmalar."
    else:
        listing = "\n".join(f"- {name}" for name in names)
        body = f"Merhaba,\n\n{label} dönemine ait aşağıdaki dosyalar ektedir:\n{listing}\n\nİyi çalışmalar."
    return {"subject": job["subject"] or f"{', '.join(n.split(' (')[0] for n in names)} - {label}",
            "body": job["body"] or body}


def _retry_review(job, now: datetime, ready: bool) -> dict | None:
    status, error = _last_result(job, now)
    if status not in (*_RESEND_STATES, "sending", "revised", "expired"):
        return None
    pending = _field(job, "pending_occurrence", "")
    next_retry = None
    if status == "retry" and ready and job["enabled"] and pending:
        latest = _latest_occurrence(job, _local(now))
        retry_at = _parse_stamp(_field(job, "next_retry_at", ""))
        next_slot = _next_occurrence(job, _local(now))
        if latest and _occurrence_details(job, latest)[0] == pending and retry_at and _local(retry_at) < next_slot:
            next_retry = _stamp(retry_at)
    return {"status": status, "reason": error, "automatic_attempts": _field(job, "attempts", 0),
            "last_attempt_at": _field(job, "last_attempt_at", ""), "next_retry_at": next_retry,
            "occurrence": pending}


def schedule_rehearsal(data_path: Path, labels, now: datetime | None = None) -> dict:
    """Snapshot regular slots, never initialize/migrate, render reports, or enqueue mail."""
    now = (now or datetime.now()).astimezone()
    local = _local(now)
    end = datetime.combine(local.date() + timedelta(days=REHEARSAL_DAYS), datetime.min.time())
    result = {"as_of": _stamp(now), "until_exclusive": end.date().isoformat(), "ready": False,
              "plans": {}, "slots": [], "disabled_plans": 0}
    path = Path(data_path)
    if not path.is_file():
        return result
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('mail_delivery_settings','mail_automation_jobs')")}
        if "mail_delivery_settings" in tables:
            ready = connection.execute(
                "SELECT enabled AND length(password_cipher)>0 FROM mail_delivery_settings WHERE id=1"
            ).fetchone()
            result["ready"] = bool(ready and ready[0])
        jobs = connection.execute("SELECT * FROM mail_automation_jobs ORDER BY id").fetchall() \
            if "mail_automation_jobs" in tables else []
    for job in jobs:
        if not job["enabled"]:
            result["disabled_plans"] += 1
            continue
        plan = {"recipients": _emails(job["recipients"]), "kinds": _job_kinds(job),
                "messages": {}, "retry": _retry_review(job, now, result["ready"])}
        result["plans"][job["id"]] = plan
        if not result["ready"]:
            continue
        moment = _next_occurrence(job, local - timedelta(microseconds=1))
        while moment is not None and moment < end:
            key, month, week = _occurrence_details(job, moment)
            if key != job["last_occurrence"]:
                result["slots"].append({"local_time": moment.isoformat(timespec="minutes"),
                                        "job_id": job["id"], "month": month, "week": week})
                if month not in plan["messages"]:
                    plan["messages"][month] = _message_fields(job, month, labels)
            moment = _next_occurrence(job, moment)
    result["slots"].sort(key=lambda slot: (slot["local_time"], slot["job_id"]))
    return result


def _find_due(job, now: datetime) -> tuple[str, str, int, bool] | None:
    """Latest occurrence that should be sent now as (key, month, week, is_catch_up).

    An occurrence on the current day (current hour for hourly plans) is due as before.
    An earlier missed one is caught up once when it is within the cadence's window and
    happened while the plan's current schedule was active.
    """
    local = _local(now)
    moment = _latest_occurrence(job, local)
    if moment is None:
        return None
    if job["cadence"] == "hourly":
        current = (moment.date(), moment.hour) == (local.date(), local.hour)
    else:
        current = moment.date() == local.date()
    if not current:
        since = _parse_stamp(_field(job, "active_since", ""))
        if since is None or moment < _local(since) or local - moment > _CATCH_UP_WINDOWS[job["cadence"]]:
            return None
    return (*_occurrence_details(job, moment), not current)


def _due_occurrence(job, now: datetime) -> tuple[str, str, int] | None:
    due = _find_due(job, now)
    return due[:3] if due else None


def _delivery_error(error: Exception) -> str:
    if isinstance(error, _RecipientScopeError):
        return str(error)
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "SMTP kimlik doğrulaması başarısız; kullanıcı adı ve parola ayarlarını kontrol edin."
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "SMTP sunucusu alıcıları kabul etmedi; ileti gönderilmedi."
    if isinstance(error, (smtplib.SMTPSenderRefused, smtplib.SMTPDataError)) and 400 <= error.smtp_code < 600:
        return f"SMTP sunucusu gönderimi reddetti (kod {error.smtp_code})."
    if isinstance(error, (smtplib.SMTPException, OSError, TimeoutError)):
        return "SMTP bağlantısı veya teslimi başarısız; sunucu, güvenlik ve alıcı ayarlarını kontrol edin."
    return f"Rapor oluşturulamadı ({type(error).__name__}); ayarları kontrol edin."


class _AttachmentsTooLarge(ValueError):
    pass


class _MissingDataReply(Exception):
    pass


class _CheckedData:
    def data(self, msg: bytes):
        # send_message supplies CRLF-serialized bytes. Before 354 no message body has been sent.
        code, reply = self.docmd("DATA")
        if code != 354:
            raise smtplib.SMTPDataError(code, reply)
        quoted = re.sub(br"(?m)^\.", b"..", msg)
        payload = quoted + (b"" if quoted.endswith(b"\r\n") else b"\r\n") + b".\r\n"
        try:
            self.send(payload)
            code, reply = self.getreply()
        except Exception as exc:
            # sendall can fail after the terminator was transmitted. Only the reply settles it.
            raise _MissingDataReply from exc
        if code != 250 and not 400 <= code < 600:
            raise _MissingDataReply
        return code, reply


class _MailSMTP(_CheckedData, smtplib.SMTP):
    pass


class _MailSMTPSSL(_CheckedData, smtplib.SMTP_SSL):
    pass


@dataclass(frozen=True)
class _DeliveryResult:
    status: str
    detail: str = ""
    retryable: bool = False


def _add_history(connection, now: datetime, job, source: str, occurrence: str,
                 month: str, attempt: int, result: _DeliveryResult) -> None:
    connection.execute(
        "INSERT INTO mail_delivery_history (attempted_at, job_id, source, occurrence, recipients, "
        "kinds, month, attempt, status, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_stamp(now), job["id"], source, occurrence, _recipient_count(job["recipients"]),
         ",".join(_job_kinds(job)), month, attempt, result.status, result.detail[:300]),
    )
    connection.execute(
        "DELETE FROM mail_delivery_history WHERE id NOT IN "
        "(SELECT id FROM mail_delivery_history ORDER BY id DESC LIMIT ?)", (HISTORY_LIMIT,),
    )


def _deliver(server, job, month: str, week: int, settings) -> _DeliveryResult:
    handler = object.__new__(server.RequestHandlerClass)
    handler.server = server
    recipients = _emails(job["recipients"])
    kinds = _job_kinds(job)
    with Store(server.data_path) as store:
        expected_revisions = _field(job, "source_revisions", "")
        if expected_revisions and _source_revision_snapshot(store, month) != expected_revisions:
            return _DeliveryResult(
                "revised", "Kaynak plan veya puantaj değişti; eski rapor güvenlik için gönderilmedi. "
                "Güncel raporu gözden geçirip yeniden başlatın.",
            )
        scopes = _recipient_scopes(store, recipients, kinds)
    messages = []
    for recipient in recipients:
        result = handler._mail_draft({
            "kinds": kinds, "month": month, "week": week,
            "to": recipient, **_message_fields(job, month, handler.MAIL_KINDS),
        }, open_draft=False, recipient_section=scopes[recipient])
        draft = Path(result["path"])
        try:
            message = BytesParser(policy=policy.default).parsebytes(draft.read_bytes())
        finally:
            draft.unlink(missing_ok=True)
        total = sum(len(part.get_payload(decode=True) or b"") for part in message.iter_attachments())
        if total > MAX_ATTACHMENT_BYTES:
            raise _AttachmentsTooLarge(
                f"Ekler toplam {total:,} bayt; yerel sınır {MAX_ATTACHMENT_BYTES:,} bayt. "
                "Planı düzenleyerek gereksiz ekleri çıkarın veya raporları ayrı planlara bölün."
            )
        if "X-Unsent" in message:
            del message["X-Unsent"]
        message["From"] = settings["from_address"]
        messages.append((message, recipient))
    with Store(server.data_path) as store:
        if _field(job, "source_revisions", "") and _source_revision_snapshot(store, month) != job["source_revisions"]:
            return _DeliveryResult(
                "revised", "Rapor hazırlanırken kaynak plan veya puantaj değişti; ileti gönderilmedi. "
                "Güncel raporu gözden geçirip yeniden başlatın.",
            )
    password = _unprotect_password(bytes(settings["password_cipher"]))
    context = ssl.create_default_context()
    if settings["use_ssl"]:
        client = _MailSMTPSSL(settings["host"], int(settings["port"]), timeout=25, context=context)
    else:
        client = _MailSMTP(settings["host"], int(settings["port"]), timeout=25)
    try:
        if not settings["use_ssl"]:
            client.starttls(context=context)
        client.login(settings["username"], password)
        accepted = 0
        refused = {}
        for message, recipient in messages:
            try:
                rejected = client.send_message(message, from_addr=settings["from_address"], to_addrs=[recipient])
            except _MissingDataReply:
                if accepted:
                    return _DeliveryResult(
                        "unknown", "En az bir alıcı kabul edildi; sonraki iletinin sonucu belirsiz. "
                        "Otomatik yeniden gönderim yapılmayacak.",
                    )
                raise
            except Exception:
                if accepted:
                    return _DeliveryResult(
                        "partial", f"Sunucu iletiyi {len(recipients)} alıcıdan {accepted} tanesi için kabul etti; "
                        "sonraki gönderim başarısız oldu. "
                        "Yeniden gönderim mükerrer ileti oluşturabilir.",
                    )
                raise
            if rejected:
                refused.update(rejected)
            else:
                accepted += 1
        # QUIT is session cleanup, not a second acknowledgement of DATA.
        with suppress(Exception):
            client.quit()
    finally:
        with suppress(Exception):
            client.close()
    if refused and accepted:
        return _DeliveryResult(
            "partial",
            f"Sunucu iletiyi {len(recipients)} alıcıdan {accepted} tanesi için kabul etti. "
            "Tüm alıcılara yeniden gönderim mükerrer ileti oluşturabilir.",
        )
    if refused:
        raise smtplib.SMTPRecipientsRefused(refused)
    return _DeliveryResult("sent")


def _plan_attempt(job, now: datetime) -> dict[str, Any] | None:
    """Decide what the scheduler should do for one enabled job right now."""
    status = job["last_status"]
    if status == "sending" and not _is_stale_sending(job, now):
        return None
    due = _find_due(job, now)
    pending = job["pending_occurrence"]
    if status == "retry" and pending:
        latest = _latest_occurrence(job, _local(now))
        if latest is not None and _occurrence_details(job, latest)[0] == pending:
            retry_at = _parse_stamp(job["next_retry_at"])
            if retry_at is not None and retry_at > now:
                return None
            return {
                "occurrence": pending, "month": job["pending_month"], "week": int(job["pending_week"]),
                "source": "retry", "attempt": int(job["attempts"]) + 1,
            }
        expired = {"expire": pending}
        if due and due[0] != job["last_occurrence"]:
            occurrence, month, week, catch_up = due
            expired["next"] = {
                "occurrence": occurrence, "month": month, "week": week,
                "source": "catch-up" if catch_up else "scheduled", "attempt": 1,
            }
        return expired
    if not due or due[0] == job["last_occurrence"]:
        return None
    occurrence, month, week, catch_up = due
    return {
        "occurrence": occurrence, "month": month, "week": week,
        "source": "catch-up" if catch_up else "scheduled", "attempt": 1,
    }


def _expire_retry(data_path: Path, job: dict[str, Any], pending: str, now: datetime) -> bool:
    with Store(data_path) as store:
        connection = store.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT * FROM mail_automation_jobs WHERE id=? AND last_status='retry' "
                "AND pending_occurrence=?",
                (job["id"], pending),
            ).fetchone()
            if not row:
                connection.rollback()
                return False
            month = row["pending_month"] or "önceki dönem"
            detail = (
                f"Önceki dönemin ({month}) bekleyen e-posta denemesi dönem değişimi nedeniyle iptal edildi; "
                "yeni dönem planı engellenmedi."
            )
            result = _DeliveryResult("expired", detail)
            connection.execute(
                "UPDATE mail_automation_jobs SET last_status='expired',last_error=?,pending_occurrence='',"
                "pending_month='',pending_week=0,attempts=0,next_retry_at='' "
                "WHERE id=? AND last_status='retry' AND pending_occurrence=?",
                (detail[:300], job["id"], pending),
            )
            _add_history(
                connection, now, row, "retry-expired", pending, row["pending_month"],
                int(row["attempts"]), result,
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise


def _claim(data_path: Path, job_id: str, plan: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    with Store(data_path) as store:
        connection = store.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute("SELECT * FROM mail_automation_jobs WHERE id=?", (job_id,)).fetchone()
            # A manual resend, schedule edit, or another scheduler may have superseded this read.
            if not row or not row["enabled"] or _plan_attempt(row, now) != plan:
                connection.rollback()
                return None
            revisions = _source_revision_snapshot(store, plan["month"])
            if plan["source"] == "retry" and (
                not row["source_revisions"] or revisions != row["source_revisions"]
            ):
                result = _DeliveryResult(
                    "revised", "Kaynak plan veya puantaj değişti; bekleyen eski rapor güvenlik için gönderilmedi. "
                    "Güncel raporu gözden geçirip yeni gönderim başlatın.",
                )
                connection.execute(
                    "UPDATE mail_automation_jobs SET last_status='revised',last_error=?,pending_occurrence='',"
                    "pending_month='',pending_week=0,attempts=0,next_retry_at='',last_attempt_at=? WHERE id=?",
                    (result.detail[:300], _stamp(now), job_id),
                )
                _add_history(connection, now, row, plan["source"], plan["occurrence"], plan["month"],
                             plan["attempt"], result)
                connection.commit()
                return None
            if plan["source"] == "retry":
                revisions = row["source_revisions"]
            connection.execute(
                "UPDATE mail_automation_jobs SET last_occurrence=?, last_status='sending', last_error='', "
                "pending_occurrence=?, pending_month=?, pending_week=?, source_revisions=?, attempts=?, next_retry_at='', "
                "last_attempt_at=? WHERE id=?",
                (plan["occurrence"], plan["occurrence"], plan["month"], plan["week"], revisions,
                 plan["attempt"], _stamp(now), job_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    claimed = dict(row)
    claimed["source_revisions"] = revisions
    return claimed


def _finish(data_path: Path, job, plan: dict[str, Any], now: datetime, result: _DeliveryResult,
            retry_at: datetime | None) -> None:
    """Store the outcome of a claimed delivery and add it to the send history."""
    with Store(data_path) as store:
        connection = store.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            guard = (job["id"], plan["occurrence"], _stamp(now))
            where = "WHERE id=? AND pending_occurrence=? AND last_attempt_at=? AND last_status='sending'"
            if result.status == "sent":
                connection.execute(
                    "UPDATE mail_automation_jobs SET last_status='sent', last_error='', pending_occurrence='', "
                    f"pending_month='', pending_week=0, next_retry_at='', last_sent_at=? {where}",
                    (_stamp(now), *guard),
                )
            elif result.status == "revised":
                connection.execute(
                    "UPDATE mail_automation_jobs SET last_status='revised', last_error=?, pending_occurrence='', "
                    f"pending_month='', pending_week=0, attempts=0, next_retry_at='' {where}",
                    (result.detail[:300], *guard),
                )
            else:
                keep_period = plan["occurrence"] or result.status in ("unknown", "partial")
                connection.execute(
                    "UPDATE mail_automation_jobs SET last_status=?, last_error=?, next_retry_at=?, "
                    f"pending_month=?, pending_week=? {where}",
                    ("retry" if retry_at else result.status, result.detail[:300],
                     _stamp(retry_at) if retry_at else "", plan["month"] if keep_period else "",
                     plan["week"] if keep_period else 0, *guard),
                )
            _add_history(connection, now, job, plan["source"], plan["occurrence"], plan["month"],
                         plan["attempt"], result)
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _attempt(server, job, plan: dict[str, Any], settings) -> _DeliveryResult:
    try:
        return _deliver(server, job, plan["month"], plan["week"], settings)
    except _MissingDataReply:
        return _DeliveryResult("unknown", _UNKNOWN_DETAIL)
    except _AttachmentsTooLarge as error:
        return _DeliveryResult("error", str(error))
    except Exception as error:
        return _DeliveryResult("error", _delivery_error(error), retryable=True)


def _ready_settings(connection):
    settings = connection.execute("SELECT * FROM mail_delivery_settings WHERE id=1").fetchone()
    if not settings or not settings["enabled"] or not settings["password_cipher"]:
        return None
    return dict(settings)


def process_due(server, now: datetime | None = None) -> int:
    now = (now or datetime.now()).astimezone()
    with Store(server.data_path) as store:
        tables = {
            row["name"] for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('mail_delivery_settings', 'mail_automation_jobs')",
            )
        }
        if tables != {"mail_delivery_settings", "mail_automation_jobs"}:
            return 0
        initialize(store.connection)
        settings = _ready_settings(store.connection)
        if settings is None:
            return 0
        pending = [dict(job) for job in _configuration_rows(store.connection)[1] if job["enabled"]]
    attempted = 0
    for job in pending:
        plan = _plan_attempt(job, now)
        if plan is None:
            continue
        if "expire" in plan:
            expired = _expire_retry(server.data_path, job, plan["expire"], now)
            plan = plan.get("next")
            if not expired or plan is None:
                continue
        claimed = _claim(server.data_path, job["id"], plan, now)
        if claimed is None:
            continue
        result = _attempt(server, claimed, plan, settings)
        retry_at = now + RETRY_DELAYS[plan["attempt"] - 1] \
            if result.retryable and plan["attempt"] <= len(RETRY_DELAYS) else None
        _finish(server.data_path, claimed, plan, now, result, retry_at)
        attempted += 1
    return attempted


def send_now(server, job_id: Any, now: datetime | None = None) -> dict[str, Any]:
    """Send a plan immediately; resends the failed occurrence when one is waiting."""
    if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{24}", job_id):
        raise ValueError("Otomatik rapor planı bulunamadı.")
    now = (now or datetime.now()).astimezone()
    stale_resend = False
    with Store(server.data_path) as store:
        initialize(store.connection)
        connection = store.connection
        settings = _ready_settings(connection)
        if settings is None:
            raise ValueError("Önce SMTP hesabını kaydedip otomatik gönderimi açın.")
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute("SELECT * FROM mail_automation_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("Otomatik rapor planı bulunamadı.")
            if not row["enabled"]:
                raise ValueError("Pasif plan gönderilemez; önce planı etkinleştirip kaydedin.")
            status, _ = _last_result(row, now)
            if status == "sending":
                raise ValueError("Bu plan şu anda gönderiliyor; biraz sonra yeniden deneyin.")
            resend = status in _RESEND_STATES and bool(row["pending_occurrence"] or row["pending_month"])
            last_occurrence = row["last_occurrence"]
            if resend:
                occurrence, month, week = row["pending_occurrence"], row["pending_month"], int(row["pending_week"])
                attempts = int(row["attempts"])
            else:
                due = _find_due(row, now)
                if due and due[0] != last_occurrence:
                    # Sending a due occurrence by hand counts as that occurrence.
                    occurrence, month, week = due[:3]
                    last_occurrence = occurrence
                else:
                    local = _local(now)
                    occurrence = ""
                    month = (local.replace(day=1) - timedelta(days=1)).strftime("%Y-%m") \
                        if row["cadence"] == "monthly" else local.strftime("%Y-%m")
                    week = _report_week(row["cadence"], month, local.date())
                attempts = 0
            revisions = _source_revision_snapshot(store, month)
            if resend and (not row["source_revisions"] or revisions != row["source_revisions"]):
                result = _DeliveryResult(
                    "revised", "Kaynak plan veya puantaj değişti; eski rapor güvenlik için yeniden gönderilmedi. "
                    "Güncel raporu gözden geçirip yeni gönderim başlatın.",
                )
                connection.execute(
                    "UPDATE mail_automation_jobs SET last_status='revised',last_error=?,pending_occurrence='',"
                    "pending_month='',pending_week=0,attempts=0,next_retry_at='',last_attempt_at=? WHERE id=?",
                    (result.detail[:300], _stamp(now), job_id),
                )
                plan = {
                    "occurrence": occurrence, "month": month, "week": week,
                    "source": "manual", "attempt": attempts,
                }
                _add_history(connection, now, row, "manual", occurrence, month, attempts, result)
                stale_resend = True
            else:
                connection.execute(
                    "UPDATE mail_automation_jobs SET last_occurrence=?, last_status='sending', last_error='', "
                    "pending_occurrence=?, pending_month=?, pending_week=?, source_revisions=?, attempts=?, "
                    "next_retry_at='', last_attempt_at=? WHERE id=?",
                    (last_occurrence, occurrence, month, week, revisions,
                     attempts, _stamp(now), job_id),
                )
                plan = {"occurrence": occurrence, "month": month, "week": week, "source": "manual", "attempt": 0}
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    if stale_resend:
        with Store(server.data_path) as store:
            configuration = get_configuration(store, now)
        return {
            "delivery": {"status": "revised", "error": result.detail, "resend": True, "month": month},
            **configuration,
        }
    job = dict(row)
    job["source_revisions"] = revisions
    result = _attempt(server, job, plan, settings)
    retry_at = None
    if result.retryable and status == "retry":
        # A failed manual resend keeps the remaining automatic retries.
        previous = _parse_stamp(row["next_retry_at"])
        retry_at = max(previous, now + RETRY_DELAYS[0]) if previous else now + RETRY_DELAYS[0]
    _finish(server.data_path, job, plan, now, result, retry_at)
    with Store(server.data_path) as store:
        configuration = get_configuration(store, now)
    return {
        "delivery": {"status": result.status, "error": result.detail,
                     "resend": resend, "month": month},
        **configuration,
    }


def run_scheduler(server, stop_event: threading.Event, interval: int = 60) -> None:
    while not stop_event.is_set():
        try:
            process_due(server)
        except Exception as error:
            print(f"[VardiyaPuantaj] Otomatik e-posta denetimi başarısız: {type(error).__name__}")
        if stop_event.wait(interval):
            break


def handle_request(handler, method: str, path: str) -> bool:
    if method == "POST" and path == "/api/mail-automation/send-now":
        payload = handler._read_json(10_000)
        if set(payload) != {"job_id"}:
            raise ValueError("Gönderim isteği geçersiz.")
        handler._json(send_now(handler.server, payload["job_id"]))
        return True
    if path == "/api/mail-automation/recipients":
        with handler._store() as store:
            if method == "GET":
                people = []
                for person in store.connection.execute("SELECT id,name,active FROM employees ORDER BY name COLLATE NOCASE"):
                    employee_id = int(person["id"])
                    people.append({
                        "id": employee_id, "name": person["name"], "active": bool(person["active"]),
                        "home_section": store.employee_profile(employee_id).get("home_section", ""),
                    })
                handler._json({
                    "recipients": recipient_permissions(store), "people": people,
                    "sections": list(scheduling.section_names()),
                })
            elif method == "PUT":
                payload = handler._read_json(10_000)
                handler._json({"recipient": save_recipient_permission(store, payload),
                               "recipients": recipient_permissions(store)})
            else:
                return False
        return True
    if method == "POST" and path == "/api/mail-automation/recipients/remove":
        payload = handler._read_json(10_000)
        if set(payload) != {"email"}:
            raise ValueError("Alıcı yetkisini kaldırma isteği geçersiz.")
        with handler._store() as store:
            removed = remove_recipient_permission(store, payload["email"])
            handler._json({"removed": removed, "recipients": recipient_permissions(store)})
        return True
    if method == "POST" and path == "/api/mail-automation/preview-recipient":
        payload = handler._read_json(10_000)
        if set(payload) != {"job_id", "recipient", "month", "week"}:
            raise ValueError("Alıcı önizleme isteği geçersiz.")
        if not isinstance(payload["job_id"], str) or not re.fullmatch(r"[a-f0-9]{24}", payload["job_id"]):
            raise ValueError("Otomatik rapor planı bulunamadı.")
        recipient = _emails(payload["recipient"])
        if len(recipient) != 1:
            raise ValueError("Önizleme için tek alıcı seçin.")
        month = scheduling.validate_month(payload["month"])
        if not isinstance(payload["week"], int) or isinstance(payload["week"], bool):
            raise ValueError("Hafta numarası geçersiz.")
        with handler._store() as store:
            initialize(store.connection)
            job = store.connection.execute(
                "SELECT * FROM mail_automation_jobs WHERE id=?", (payload["job_id"],),
            ).fetchone()
            if not job:
                raise ValueError("Otomatik rapor planı bulunamadı.")
            if recipient[0].casefold() not in {email.casefold() for email in _emails(job["recipients"])}:
                raise ValueError("Önizlenecek alıcı bu plana kayıtlı değil.")
            scopes = _recipient_scopes(store, recipient, _job_kinds(job))
            fields = _message_fields(job, month, handler.MAIL_KINDS)
            handler._json(handler._mail_preview({
                "kinds": _job_kinds(job), "recipient": recipient[0], "month": month,
                "week": payload["week"], **fields,
            }, section=scopes[recipient[0]]))
        return True
    if path != "/api/mail-automation" or method not in ("GET", "PUT"):
        return False
    query = parse_qs(urlsplit(handler.path).query, keep_blank_values=True)
    if query:
        if method != "GET" or query != {"view": ["rehearsal"]}:
            raise ValueError("E-posta prova isteği geçersiz.")
        handler._json(schedule_rehearsal(handler.server.data_path, handler.MAIL_KINDS))
        return True
    with handler._store() as store:
        if method == "GET":
            handler._json(get_configuration(store))
        else:
            handler._json(save_configuration(store, handler._read_json()))
    return True
