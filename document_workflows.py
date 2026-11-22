from __future__ import annotations

import base64
import binascii
import hashlib
import html
import json
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from io import BytesIO
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from openpyxl import load_workbook

import forms
import scheduling
import version


MAX_SELECTION = 250
MAX_DOCUMENT_BATCH = 50
MAX_ATTACHMENT_BYTES = 8_000_000
MAX_DOCUMENT_BYTES = 12_000_000
MAX_DOCUMENTS = 5000
MAX_SHARES_TOTAL = 5000
MAX_DOCUMENT_ARCHIVE_BYTES = 500_000_000
MAX_ATTACHMENT_ARCHIVE_BYTES = 250_000_000
MAX_ATTACHMENTS_PER_DOCUMENT = 10
MAX_SHARES_PER_EMPLOYEE = 20
MAX_RETENTION_DAYS = 365_000
ALLOWED_ATTACHMENTS = {
    ".pdf": ("application/pdf", b"%PDF-"),
    ".png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
    ".jpeg": ("image/jpeg", b"\xff\xd8\xff"),
}
STATUS_LABELS = {"draft": "Taslak", "printed": "Yazdırıldı", "signed": "İmzalandı"}
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
EXPORT_CODE = re.compile(r"^EX-[A-F0-9]{16}$")
MAX_BACKUP_DETAIL_BYTES = 2_000_000
MAX_BACKUP_DETAIL_ITEMS = 2000


def initialize(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS workflow_documents (
            id TEXT PRIMARY KEY,
            employee_id INTEGER NOT NULL,
            person_name TEXT NOT NULL,
            exact_dates TEXT NOT NULL,
            code TEXT NOT NULL,
            template_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            content BLOB NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('draft','printed','signed')),
            source_revisions TEXT NOT NULL,
            app_version TEXT NOT NULL,
            code_revision TEXT NOT NULL,
            signers TEXT NOT NULL DEFAULT '[]',
            visibility_scope TEXT NOT NULL DEFAULT 'internal' CHECK(visibility_scope IN ('internal','staff')),
            internal_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS workflow_documents_created
            ON workflow_documents(created_at DESC);
        CREATE INDEX IF NOT EXISTS workflow_documents_person
            ON workflow_documents(employee_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS workflow_retention_policy (
            id INTEGER PRIMARY KEY CHECK(id=1),
            retention_days INTEGER NOT NULL CHECK(retention_days BETWEEN 1 AND 365000),
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workflow_attachments (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES workflow_documents(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            content BLOB NOT NULL,
            sha256 TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('draft','printed','signed')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS workflow_attachments_document
            ON workflow_attachments(document_id, created_at);
        CREATE TABLE IF NOT EXISTS workflow_shares (
            id TEXT PRIMARY KEY,
            token_hash TEXT NOT NULL UNIQUE,
            employee_id INTEGER NOT NULL,
            person_name TEXT NOT NULL,
            month TEXT NOT NULL,
            snapshot TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS workflow_shares_employee
            ON workflow_shares(employee_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS workflow_exports (
            id TEXT PRIMARY KEY,
            export_type TEXT NOT NULL,
            month TEXT NOT NULL,
            filename TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            source_revisions TEXT NOT NULL,
            app_version TEXT NOT NULL,
            code_revision TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS workflow_exports_created
            ON workflow_exports(created_at DESC);
        CREATE TABLE IF NOT EXISTS personal_calendar_exports (
            id TEXT PRIMARY KEY,
            employee_id INTEGER NOT NULL,
            person_name TEXT NOT NULL,
            month TEXT NOT NULL,
            plan_version TEXT NOT NULL,
            path TEXT NOT NULL,
            filename TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS personal_calendar_exports_person
            ON personal_calendar_exports(employee_id, month, created_at DESC);
        """
    )
    export_columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_exports)")}
    if "template_revision" not in export_columns:
        connection.execute(
            "ALTER TABLE workflow_exports ADD COLUMN template_revision TEXT NOT NULL DEFAULT ''"
        )
    document_columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_documents)")}
    if "signers" not in document_columns:
        connection.execute("ALTER TABLE workflow_documents ADD COLUMN signers TEXT NOT NULL DEFAULT '[]'")
    if "visibility_scope" not in document_columns:
        connection.execute(
            "ALTER TABLE workflow_documents ADD COLUMN visibility_scope TEXT NOT NULL DEFAULT 'internal'"
        )
    if "internal_note" not in document_columns:
        connection.execute("ALTER TABLE workflow_documents ADD COLUMN internal_note TEXT NOT NULL DEFAULT ''")


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_utc(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _valid_date(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("Tarih YYYY-AA-GG biçiminde olmalıdır.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Tarih YYYY-AA-GG biçiminde olmalıdır.") from exc
    if parsed.isoformat() != value:
        raise ValueError("Tarih YYYY-AA-GG biçiminde olmalıdır.")
    return parsed


def _valid_month(value):
    if not isinstance(value, str) or not MONTH_PATTERN.fullmatch(value):
        raise ValueError("Ay YYYY-AA biçiminde olmalıdır.")
    return scheduling.validate_month(value)


def normalize_selection(cells):
    if not isinstance(cells, list) or not cells or len(cells) > MAX_SELECTION:
        raise ValueError(f"Seçim 1-{MAX_SELECTION} hücre arasında olmalıdır.")
    normalized = []
    seen = {}
    for cell in cells:
        if not isinstance(cell, dict) or set(cell) != {"employee_id", "date", "code"}:
            raise ValueError("Seçili hücre bilgisi geçersiz.")
        employee_id = cell["employee_id"]
        if not isinstance(employee_id, int) or isinstance(employee_id, bool) or employee_id < 1:
            raise ValueError("Personel seçimi geçersiz.")
        day = _valid_date(cell["date"]).isoformat()
        code = cell["code"]
        if not isinstance(code, str) or len(code.strip()) > 12:
            raise ValueError("Puantaj kodu geçersiz.")
        code = code.strip().upper()
        key = (employee_id, day)
        if key in seen:
            if seen[key] != code:
                raise ValueError("Aynı personel ve tarih için birden fazla puantaj kodu seçilemez.")
            continue
        seen[key] = code
        normalized.append({"employee_id": employee_id, "date": day, "code": code})
    if not normalized:
        raise ValueError("Seçimde kullanılabilir hücre yok.")
    return normalized


def _normalize_signers(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 10:
        raise ValueError("Belge başına en fazla 10 imzacı tanımlanabilir.")
    signers = []
    seen = set()
    for name in value:
        if not isinstance(name, str):
            raise ValueError("İmzacı adı metin olmalıdır.")
        name = " ".join(name.split())
        key = name.casefold()
        if not name or len(name) > 80 or key in seen:
            raise ValueError("İmzacı adları 1-80 karakter olmalı ve yinelenmemelidir.")
        seen.add(key)
        signers.append(name)
    return signers


def selection_groups(cells):
    groups = {}
    for cell in normalize_selection(cells):
        groups.setdefault(cell["employee_id"], []).append(cell)
    return groups


def _document_currentness(handler, store, document):
    try:
        fingerprints = json.loads(document["source_revisions"])
    except (TypeError, json.JSONDecodeError):
        fingerprints = {}
    if not isinstance(fingerprints, dict):
        fingerprints = {}
    current = True
    current_revisions = {}
    try:
        dates = json.loads(document["exact_dates"])
    except (TypeError, json.JSONDecodeError):
        dates = []
    if not isinstance(dates, list) or any(not isinstance(day, str) or len(day) != 10 for day in dates):
        dates = []
        current = False
    months = sorted({day[:7] for day in dates})
    for month in months:
        revisions = handler._month_revisions(store, month)
        current_revisions[month] = revisions
        if fingerprints.get(month) != revisions:
            current = False
    return {"current": current, "source_revisions": fingerprints, "current_revisions": current_revisions}


def _fingerprints(handler, store, dates):
    return {
        month: handler._month_revisions(store, month)
        for month in sorted({day[:7] for day in dates})
    }


def _code_revision():
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in (
        "server.py", "storage.py", "forms.py", "scheduling.py", "reports.py",
        "payroll.py", "document_workflows.py",
    ):
        path = root / name
        try:
            digest.update(name.encode("ascii"))
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"unavailable")
    return digest.hexdigest()[:20]


@lru_cache(maxsize=1)
def current_code_revision():
    return _code_revision()


def _admin_required(handler, store):
    if store.users_enabled():
        user = getattr(handler, "current_user", None) or {}
        if user.get("role") != "admin":
            handler._json({"error": "Bu işlem yalnızca yönetici hesabıyla yapılabilir."}, 403)
            return False
    return True


def _snapshot_before_change(store):
    snapshot = getattr(store, "_backup_before_change", None)
    if callable(snapshot):
        snapshot()


def _document_payload(payload):
    required = {"template_id", "employee_id", "date", "code"}
    allowed = required | {"section", "description"}
    if not isinstance(payload, dict) or not required.issubset(payload) or not set(payload).issubset(allowed):
        raise ValueError("Belge bilgisi geçersiz.")
    if not isinstance(payload["employee_id"], int) or isinstance(payload["employee_id"], bool):
        raise ValueError("Personel seçimi geçersiz.")
    day = _valid_date(payload["date"]).isoformat()
    code = payload["code"]
    if not isinstance(code, str) or code not in ("G", "S", "Ü"):
        raise ValueError("Belge yalnız G, S veya Ü puantaj durumu için hazırlanabilir.")
    template_id = payload["template_id"]
    if not isinstance(template_id, str) or not template_id or len(template_id) > 64:
        raise ValueError("Form şablonu seçin.")
    section = payload.get("section", "")
    description = payload.get("description", "")
    if not isinstance(section, str) or len(section.strip()) > 80:
        raise ValueError("Bölüm bilgisi en fazla 80 karakter olabilir.")
    if not isinstance(description, str) or len(description.strip()) > 500:
        raise ValueError("Açıklama en fazla 500 karakter olabilir.")
    return {
        "template_id": template_id,
        "employee_id": payload["employee_id"],
        "date": day,
        "code": code,
        "section": section.strip(),
        "description": description.strip(),
    }


def create_document(handler, store, payload):
    data = _document_payload(payload)
    if not _admin_required(handler, store):
        return None
    person = next((item for item in store.people() if item["id"] == data["employee_id"]), None)
    if person is None:
        raise ValueError("Personel bulunamadı.")
    template = forms.get_template(store.connection, data["template_id"])
    content = forms.generate(
        template, person["name"], data["date"], data["date"], data["code"],
        data["section"], data["description"],
    )
    if not content or len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Oluşturulan belge 12 MB sınırını aşıyor.")
    filename = f"form-{data['date']}-{data['employee_id']}.{template['extension']}"
    now = _now()
    record_id = secrets.token_urlsafe(18)
    revisions = _fingerprints(handler, store, [data["date"]])
    _snapshot_before_change(store)
    with store.connection:
        if store.connection.execute("SELECT COUNT(*) FROM workflow_documents").fetchone()[0] >= MAX_DOCUMENTS:
            raise ValueError("Belge arşivi 5000 kayıt sınırına ulaştı.")
        stored_bytes = store.connection.execute(
            "SELECT COALESCE(SUM(length(content)),0) FROM workflow_documents"
        ).fetchone()[0]
        if stored_bytes + len(content) > MAX_DOCUMENT_ARCHIVE_BYTES:
            raise ValueError("Belge arşivi 500 MB sınırına ulaştı.")
        store.connection.execute(
            """INSERT INTO workflow_documents
               (id,employee_id,person_name,exact_dates,code,template_id,filename,content_type,content,
                status,source_revisions,app_version,code_revision,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,'draft',?,?,?,?,?)""",
            (
                record_id, person["id"], person["name"], json.dumps([data["date"]]),
                data["code"], data["template_id"], filename,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                if template["extension"] == "docx"
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                content, json.dumps(revisions, sort_keys=True), version.APP_VERSION,
                current_code_revision(), now, now,
            ),
        )
        store.record_change(
            "document.generated",
            f"Form taslağı oluşturuldu: {person['name']} · {data['date']}",
            {"document_id": record_id, "employee_id": person["id"], "date": data["date"]},
        )
    return _document_summary(store.connection.execute(
        "SELECT * FROM workflow_documents WHERE id=?", (record_id,)
    ).fetchone(), _document_currentness(handler, store, store.connection.execute(
        "SELECT * FROM workflow_documents WHERE id=?", (record_id,)
    ).fetchone()))


def _neutral_status_form(person_name, day, code, description, title="", event_time=""):
    from docx import Document

    document = Document()
    if title:
        document.add_heading(title, level=1)
    fields = [
        ("Personel", person_name),
        ("Tarih", day),
        ("Durum", code or "—"),
        ("Açıklama", description or "—"),
    ]
    if event_time:
        fields.append(("Olay saati", event_time))
    table = document.add_table(rows=len(fields), cols=2)
    for row, (label, value) in zip(table.rows, fields):
        row.cells[0].text = label
        row.cells[1].text = str(value)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def create_documents_batch_result(handler, store, payload):
    if not _admin_required(handler, store):
        return None
    if not isinstance(payload, dict) or not {"cells", "templates", "section", "description"} <= set(payload) <= {
        "cells", "templates", "section", "description", "title", "signers", "event_time",
        "visibility_scope", "internal_note",
    }:
        raise ValueError("Toplu belge bilgisi geçersiz.")
    title = payload.get("title", "")
    if not isinstance(title, str) or len(title.strip()) > 120:
        raise ValueError("Tutanak başlığı en fazla 120 karakter olabilir.")
    title = title.strip()
    event_time = payload.get("event_time", "")
    if not isinstance(event_time, str) or (
        event_time and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", event_time)
    ):
        raise ValueError("Olay saati SS:DD biçiminde olmalıdır.")
    visibility_scope = payload.get("visibility_scope", "internal")
    internal_note = payload.get("internal_note", "")
    if visibility_scope not in ("internal", "staff"):
        raise ValueError("Belge görünürlük kapsamı geçersiz.")
    if not isinstance(internal_note, str) or len(internal_note.strip()) > 500:
        raise ValueError("İç yönetici notu en fazla 500 karakter olabilir.")
    internal_note = internal_note.strip()
    cells = normalize_selection(payload["cells"])
    if len(cells) > MAX_DOCUMENT_BATCH:
        raise ValueError(f"Bir işlemde en fazla {MAX_DOCUMENT_BATCH} ayrı belge hazırlanabilir.")
    templates = payload["templates"]
    codes = {cell["code"] for cell in cells}
    if not isinstance(templates, dict) or set(templates) != codes:
        raise ValueError("Form şablonu seçimi geçersiz.")
    section = payload["section"]
    description = payload["description"]
    if not isinstance(section, str) or len(section.strip()) > 80:
        raise ValueError("Bölüm bilgisi en fazla 80 karakter olabilir.")
    if not isinstance(description, str) or len(description.strip()) > 500:
        raise ValueError("Açıklama en fazla 500 karakter olabilir.")
    signer_names = _normalize_signers(payload.get("signers"))
    people = {person["id"]: person for person in store.people()}
    prepared = []
    total_bytes = 0
    failures = []
    for cell in cells:
        employee_id, day, code = cell["employee_id"], cell["date"], cell["code"]
        try:
            if employee_id not in people:
                raise ValueError("Personel bulunamadı.")
            template_id = templates.get(code)
            if template_id == "neutral":
                template = {"extension": "docx"}
                content = _neutral_status_form(
                    people[employee_id]["name"], day, code, description.strip(), title,
                    event_time,
                )
            else:
                if code not in ("G", "S", "Ü"):
                    raise ValueError(
                        f"{day} tarihli “{code or 'boş'}” kodu için yalnızca nötr Durum kayıt formu kullanılabilir."
                    )
                if not isinstance(template_id, str) or not template_id or len(template_id) > 64:
                    raise ValueError(
                        f"{code or 'Kod yok'} için form şablonu seçin veya nötr Durum kayıt formunu seçin."
                    )
                template = forms.get_template(store.connection, template_id)
                if template["category"] not in (code, "GENEL"):
                    raise ValueError(f"{day} tarihli “{code}” koduyla seçilen form şablonu eşleşmiyor.")
                content = forms.generate(
                    template, people[employee_id]["name"], day, day, code,
                    section.strip(), description.strip(), event_time,
                )
            if not content or len(content) > MAX_DOCUMENT_BYTES:
                raise ValueError("Belge boş veya 12 MB sınırından büyük.")
            if total_bytes + len(content) > 30_000_000:
                raise ValueError("Belge grubu toplam 30 MB sınırını aşıyor.")
            current_count = store.connection.execute(
                "SELECT COUNT(*) FROM workflow_documents"
            ).fetchone()[0]
            if current_count + len(prepared) + 1 > MAX_DOCUMENTS:
                raise ValueError("Belge arşivi 5000 kayıt sınırını aşar.")
            stored_bytes = store.connection.execute(
                "SELECT COALESCE(SUM(length(content)),0) FROM workflow_documents"
            ).fetchone()[0]
            if stored_bytes + total_bytes + len(content) > MAX_DOCUMENT_ARCHIVE_BYTES:
                raise ValueError("Belge arşivi 500 MB sınırını aşar.")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            failures.append({
                "employee_id": employee_id,
                "person_name": people.get(employee_id, {}).get("name", f"Personel {employee_id}"),
                "date": day,
                "code": code,
                "reason": str(exc),
            })
            continue
        total_bytes += len(content)
        prepared.append({
            "id": secrets.token_urlsafe(18),
            "employee": people[employee_id],
            "day": day,
            "code": code,
            "template_id": "neutral-status" if template_id == "neutral" else template_id,
            "template": template,
            "content": content,
            "signers": [
                {"id": secrets.token_urlsafe(9), "name": name, "status": "pending"}
                for name in signer_names
            ],
            "visibility_scope": visibility_scope,
            "internal_note": internal_note,
            "revisions": _fingerprints(handler, store, [day]),
        })
    if prepared:
        now = _now()
        _snapshot_before_change(store)
        with store.connection:
            for item in prepared:
                extension = item["template"]["extension"]
                store.connection.execute(
                    """INSERT INTO workflow_documents
                       (id,employee_id,person_name,exact_dates,code,template_id,filename,content_type,content,
                        status,source_revisions,app_version,code_revision,signers,visibility_scope,internal_note,
                        created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,'draft',?,?,?,?,?,?,?,?)""",
                    (
                        item["id"], item["employee"]["id"], item["employee"]["name"],
                        json.dumps([item["day"]]), item["code"], item["template_id"],
                        f"form-{item['day']}-{item['employee']['id']}.{extension}",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        if extension == "docx"
                        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        item["content"], json.dumps(item["revisions"], sort_keys=True),
                        version.APP_VERSION, current_code_revision(),
                        json.dumps(item["signers"], ensure_ascii=False), item["visibility_scope"],
                        item["internal_note"], now, now,
                    ),
                )
                store.record_change(
                    "document.generated",
                    f"Form taslağı oluşturuldu: {item['employee']['name']} · {item['day']}",
                    {
                        "document_id": item["id"], "employee_id": item["employee"]["id"],
                        "date": item["day"], "code": item["code"],
                    },
                )
        rows = store.connection.execute(
            "SELECT * FROM workflow_documents WHERE id IN (%s)" % ",".join("?" for _ in prepared),
            tuple(item["id"] for item in prepared),
        ).fetchall()
        by_id = {row["id"]: row for row in rows}
        documents = [
            _document_summary(by_id[item["id"]], _document_currentness(handler, store, by_id[item["id"]]))
            for item in prepared
        ]
    else:
        documents = []
    return {"documents": documents, "failures": failures}


def create_documents_batch(handler, store, payload):
    return create_documents_batch_result(handler, store, payload)["documents"]


def _document_summary(row, currentness=None):
    try:
        dates = json.loads(row["exact_dates"])
    except (TypeError, json.JSONDecodeError):
        dates = []
    if not isinstance(dates, list):
        dates = []
    try:
        signers = json.loads(row["signers"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Belge imzacı bilgisi okunamadı.") from exc
    if not isinstance(signers, list) or any(
        not isinstance(item, dict)
        or set(item) != {"id", "name", "status"}
        or not isinstance(item["id"], str)
        or not isinstance(item["name"], str)
        or item["status"] not in ("pending", "signed")
        for item in signers
    ):
        raise ValueError("Belge imzacı bilgisi geçersiz.")
    item = {
        "id": row["id"],
        "employee_id": row["employee_id"],
        "person_name": row["person_name"],
        "dates": dates,
        "code": row["code"],
        "template_id": row["template_id"],
        "filename": row["filename"],
        "status": row["status"],
        "status_label": STATUS_LABELS[row["status"]],
        "signers": signers,
        "visibility_scope": row["visibility_scope"],
        "visibility_label": "Personele gösterilebilir" if row["visibility_scope"] == "staff" else "Yalnız yönetici",
        "internal_note": row["internal_note"],
        "app_version": row["app_version"],
        "code_revision": row["code_revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "attachments": [],
    }
    if currentness is not None:
        item.update(currentness)
    return item


def _list_documents(handler, store, query):
    raw_limit = query.get("limit", ["200"])[0]
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        raise ValueError("Belge listeleme sınırı geçersiz.") from None
    if not 1 <= limit <= 500:
        raise ValueError("Belge listeleme sınırı 1-500 arasında olmalıdır.")
    employee_raw = query.get("employee_id", [""])[0]
    employee_id = None
    if employee_raw:
        try:
            employee_id = int(employee_raw)
        except (ValueError, TypeError):
            raise ValueError("Personel seçimi geçersiz.") from None
        if employee_id < 1:
            raise ValueError("Personel seçimi geçersiz.")
    sql = "SELECT * FROM workflow_documents"
    parameters = []
    if employee_id is not None:
        sql += " WHERE employee_id=?"
        parameters.append(employee_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    parameters.append(limit)
    rows = store.connection.execute(sql, parameters).fetchall()
    result = []
    for row in rows:
        currentness = _document_currentness(handler, store, row)
        item = _document_summary(row, currentness)
        attachments = [dict(attachment) for attachment in store.connection.execute(
            "SELECT id,filename,content_type,sha256,status,created_at,updated_at "
            "FROM workflow_attachments WHERE document_id=? ORDER BY created_at",
            (row["id"],),
        ).fetchall()]
        digests_by_name = {}
        for attachment in attachments:
            digests_by_name.setdefault(attachment["filename"].casefold(), set()).add(attachment["sha256"])
        for attachment in attachments:
            attachment["same_name_changed"] = len(digests_by_name[attachment["filename"].casefold()]) > 1
        item["attachments"] = attachments
        result.append(item)
    return result


def _retention_review(store, *, now=None):
    policy = store.connection.execute(
        "SELECT retention_days,updated_at FROM workflow_retention_policy WHERE id=1"
    ).fetchone()
    if policy is None:
        return {"policy": None, "review_documents": []}

    retention_days = policy["retention_days"]
    current_time = now or datetime.now(timezone.utc).replace(microsecond=0)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)
    review_documents = []
    rows = store.connection.execute(
        "SELECT id,person_name,filename,status,created_at,visibility_scope,internal_note FROM workflow_documents"
    ).fetchall()
    for row in rows:
        created_at = _parse_utc(row["created_at"])
        if created_at is None:
            raise ValueError("Belge oluşturulma tarihi okunamadı; saklama incelemesi tamamlanamadı.")
        due_at = created_at + timedelta(days=retention_days)
        if due_at <= current_time:
            review_documents.append({
                "id": row["id"],
                "person_name": row["person_name"],
                "filename": row["filename"],
                "status": row["status"],
                "visibility_scope": row["visibility_scope"],
                "visibility_label": "Personele gösterilebilir"
                if row["visibility_scope"] == "staff" else "Yalnız yönetici",
                "internal_note": row["internal_note"],
                "created_at": created_at.isoformat(),
                "review_due_at": due_at.isoformat(),
            })
    review_documents.sort(key=lambda item: (item["review_due_at"], item["filename"]))
    return {
        "policy": {
            "retention_days": retention_days,
            "updated_at": policy["updated_at"],
            "basis": "created_at",
        },
        "review_documents": review_documents,
    }


def _advance_status(current, requested):
    if requested not in ("printed", "signed"):
        raise ValueError("Durum yalnız yazdırıldı veya imzalandı olarak işaretlenebilir.")
    allowed_next = {"draft": "printed", "printed": "signed", "signed": None}
    if current == requested:
        return current
    if allowed_next.get(current) != requested:
        raise ValueError("İşlem sırası taslak → yazdırıldı → imzalandı olmalıdır.")
    return requested


def _safe_filename(value):
    if not isinstance(value, str) or len(value) > 120 or Path(value).name != value \
            or "/" in value or "\\" in value:
        raise ValueError("Dosya adı geçersiz.")
    ext = Path(value).suffix.lower()
    if ext not in ALLOWED_ATTACHMENTS:
        raise ValueError("Yalnız PDF, PNG veya JPEG eklenebilir.")
    stem = Path(value).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")[:80] or "ek"
    return f"{stem}{ext}", ext


def add_attachment(handler, store, document_id, payload):
    if not _admin_required(handler, store):
        return None
    if not SAFE_ID.fullmatch(document_id):
        raise ValueError("Belge seçimi geçersiz.")
    if not isinstance(payload, dict) or set(payload) != {"filename", "content"}:
        raise ValueError("Ek dosya bilgisi geçersiz.")
    document = store.connection.execute(
        "SELECT id FROM workflow_documents WHERE id=?", (document_id,)
    ).fetchone()
    if document is None:
        raise ValueError("Belge bulunamadı.")
    attachment_count, total_bytes = store.connection.execute(
        """SELECT COUNT(*), COALESCE(SUM(length(content)),0)
           FROM workflow_attachments WHERE document_id=?""",
        (document_id,),
    ).fetchone()
    archive_bytes = store.connection.execute(
        "SELECT COALESCE(SUM(length(content)),0) FROM workflow_attachments"
    ).fetchone()[0]
    if attachment_count >= MAX_ATTACHMENTS_PER_DOCUMENT:
        raise ValueError(f"Bir belgeye en fazla {MAX_ATTACHMENTS_PER_DOCUMENT} ek bağlanabilir.")
    filename, extension = _safe_filename(payload["filename"])
    try:
        content = base64.b64decode(payload["content"], validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("Ek dosyası okunamadı.") from exc
    if not content or len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError("Ek dosyası boş veya 8 MB sınırından büyük.")
    if total_bytes + len(content) > MAX_ATTACHMENT_ARCHIVE_BYTES \
            or archive_bytes + len(content) > MAX_ATTACHMENT_ARCHIVE_BYTES:
        raise ValueError("Ek dosyaları toplamı 250 MB sınırını aşıyor.")
    media_type, signature = ALLOWED_ATTACHMENTS[extension]
    if not content.startswith(signature):
        raise ValueError("Dosya içeriği uzantısıyla eşleşmiyor.")
    attachment_id = secrets.token_urlsafe(18)
    now = _now()
    digest = hashlib.sha256(content).hexdigest()
    previous = store.connection.execute(
        """SELECT id,sha256 FROM workflow_attachments
           WHERE document_id=? AND filename=? ORDER BY created_at DESC LIMIT 1""",
        (document_id, filename),
    ).fetchone()
    _snapshot_before_change(store)
    with store.connection:
        store.connection.execute(
            """INSERT INTO workflow_attachments
               (id,document_id,filename,content_type,content,sha256,status,created_at,updated_at)
               VALUES (?,?,?,?,?,?,'draft',?,?)""",
            (attachment_id, document_id, filename, media_type, content, digest, now, now),
        )
        changed = previous is not None and previous["sha256"] != digest
        store.record_change(
               "document.attachment_changed" if changed else "document.attachment_added",
               f"{'Aynı adlı ekin içeriği değişti' if changed else 'Belgeye ek dosya eklendi'}: {filename}",
               {
                   "document_id": document_id,
                   "attachment_id": attachment_id,
                   "filename": filename,
                   "sha256": digest,
                   "previous_attachment_id": previous["id"] if changed else None,
                   "previous_sha256": previous["sha256"] if changed else None,
               },
        )
    return {
        "id": attachment_id, "document_id": document_id, "filename": filename,
        "content_type": media_type, "sha256": digest, "status": "draft",
        "status_label": STATUS_LABELS["draft"], "created_at": now,
    }


def _calendar_row_key(entry, legacy_counts):
    row_id = entry.get("row_id")
    if isinstance(row_id, str) and row_id.strip():
        return ("row", row_id)
    # Older snapshots may only identify a section/slot. Indistinguishable
    # legacy rows retain their relative published order, never their time order.
    section, slot = entry.get("section"), entry.get("slot")
    key = (section if isinstance(section, str) else "", slot if type(slot) is int else None)
    ordinal = legacy_counts.get(key, 0)
    legacy_counts[key] = ordinal + 1
    return ("legacy", *key, ordinal)


def _share_snapshot(store, employee_id, month, published, *, calendar=False):
    if not isinstance(published, dict) or not isinstance(published.get("entries"), list):
        raise ValueError("Bu ay için yayımlanmış vardiya planı bulunamadı.")
    person = next((item for item in store.people() if item["id"] == employee_id), None)
    if person is None:
        raise ValueError("Personel bulunamadı.")
    days = {}
    legacy_counts = {}
    for entry in published["entries"]:
        if (not isinstance(entry, dict) or type(entry.get("employee_id")) is not int
                or entry["employee_id"] != employee_id):
            continue
        raw_days = entry.get("days", {})
        if not isinstance(raw_days, dict):
            continue
        if calendar:
            row_key = _calendar_row_key(entry, legacy_counts)
        for day, shift in raw_days.items():
            try:
                parsed = _valid_date(day)
            except ValueError:
                continue
            if parsed.strftime("%Y-%m") != month or not isinstance(shift, dict):
                continue
            kind = shift.get("kind", "")
            if not isinstance(kind, str):
                continue
            if kind == "work":
                start = shift.get("start", "")
                end = shift.get("end", "")
                item = {
                    "type": "Çalışma",
                    "start": start if isinstance(start, str) and re.fullmatch(r"\d{2}:\d{2}", start) else "",
                    "end": end if isinstance(end, str) and re.fullmatch(r"\d{2}:\d{2}", end) else "",
                }
                if calendar:
                    identity = json.dumps([employee_id, day, row_key], separators=(",", ":"))
                    item["uid"] = hashlib.sha256(identity.encode("ascii")).hexdigest()[:32] + "@vardiyapuantaj"
            elif kind in ("weekly_off", "overnight_exit"):
                item = {
                    "weekly_off": {"type": "Hafta tatili"},
                    "overnight_exit": {"type": "Geceden çıkış"},
                }[kind]
            else:
                continue
            days.setdefault(day, []).append(item)
    return {"employee_id": employee_id, "person_name": person["name"], "month": month, "days": days}


def personal_calendar(store, employee_id, month):
    return _calendar_download(store, employee_id, month).content


@dataclass(frozen=True)
class _CalendarDownload:
    content: bytes
    filename: str
    plan_version: str
    person_name: str


def _calendar_download(store, employee_id, month):
    month = _valid_month(month)
    published = store._setting("published_plans", {}).get(month)
    snapshot = _share_snapshot(store, employee_id, month, published, calendar=True)
    plan_version = _published_plan_version(published)
    stamp = _parse_utc(published.get("at")) or datetime.now(timezone.utc)
    content = _personal_calendar_content(snapshot, plan_version, stamp)
    filename = f"vardiya-{employee_id}-{month}-{plan_version.replace(':', '-')}.ics"
    return _CalendarDownload(content, filename, plan_version, snapshot["person_name"])


def _personal_calendar_content(snapshot, plan_version, stamp):
    employee_id = snapshot["employee_id"]
    month = snapshot["month"]
    try:
        local_zone = ZoneInfo("Europe/Istanbul")
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Takvim için Türkiye saat dilimi verisi bulunamadı.") from exc
    stamp = stamp.strftime("%Y%m%dT%H%M%SZ")
    calendar_name = _ics_escape(f"{snapshot['person_name']} - {month}")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Vardiya Puantaj//Vardiya Takvimi//TR",
             "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{calendar_name}",
             f"X-VP-EMPLOYEE-ID:{employee_id}", f"X-VP-PERIOD:{month}",
             f"X-VP-PLAN-VERSION:{plan_version}"]
    seen = set()
    for day, shifts in sorted(snapshot["days"].items()):
        for shift in shifts:
            if shift["type"] != "Çalışma":
                continue
            start_text, end_text = shift["start"], shift["end"]
            try:
                start, end = scheduling._time_interval(day, start_text, "00:00" if end_text == "24:00" else end_text)
            except ValueError as exc:
                raise ValueError(f"{day} tarihli yayımlanan vardiyanın saati geçersiz.") from exc
            uid = shift["uid"]
            if uid in seen:
                raise ValueError(f"{day} tarihli yayımlanmış planda vardiya satırı kimliği yineleniyor.")
            seen.add(uid)
            start_utc, end_utc = (value.replace(tzinfo=local_zone).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                  for value in (start, end))
            lines.extend(["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
                          f"DTSTART:{start_utc}", f"DTEND:{end_utc}", "SUMMARY:Vardiya",
                          f"X-VP-PLAN-VERSION:{plan_version}", f"DESCRIPTION:Plan sürümü: {plan_version}", "END:VEVENT"])
    if not seen:
        raise ValueError("Bu kişi ve ay için yayımlanmış çalışma vardiyası yok.")
    return b"".join(_ics_line(line) for line in [*lines, "END:VCALENDAR"])


def _published_plan_version(published):
    revision = published.get("source_revision") if isinstance(published, dict) else None
    if isinstance(revision, str) and revision:
        if re.fullmatch(r"(?:[0-9a-fA-F]{32}|schedule-r[0-9]{1,10})", revision):
            return revision
        # Never copy an unfamiliar/free-text revision into a personal file or its name.
        return "sha256:" + hashlib.sha256(revision.encode("utf-8")).hexdigest()
    snapshot = {"entries": published.get("entries", []), "at": published.get("at", "")} \
        if isinstance(published, dict) else {}
    digest = hashlib.sha256(json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _ics_escape(value):
    return value.replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n") \
        .replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def _ics_line(line):
    # RFC 5545 folds at 75 octets without splitting a UTF-8 character.
    raw = line.encode("utf-8")
    parts = []
    while len(raw) > 75:
        cut = 75
        while raw[cut] & 0xC0 == 0x80:
            cut -= 1
        parts.append(raw[:cut])
        raw = b" " + raw[cut:]
    return b"\r\n".join([*parts, raw]) + b"\r\n"


def save_personal_calendar(store, employee_id, month, save_form):
    download = _calendar_download(store, employee_id, month)
    result = save_form("ics", download.content, download.filename)
    if not isinstance(result, dict):
        raise ValueError("Takvim kaydetme sonucu doğrulanamadı.")
    if result.get("cancelled") is True:
        return {"cancelled": True}
    path_value = result.get("path")
    if not isinstance(path_value, str) or not path_value:
        raise ValueError("Kaydedilen takvimin dosya yolu alınamadı.")
    try:
        path = Path(path_value).resolve(strict=True)
        if path.suffix.lower() != ".ics" or not path.is_file():
            raise ValueError("Kaydedilen takvim dosyası bulunamadı veya .ics biçiminde değil.")
        saved_content = path.read_bytes()
    except OSError as exc:
        raise ValueError("Kaydedilen takvim dosyasına erişilemiyor; dosya okunamadı.") from exc
    if saved_content != download.content:
        raise ValueError("Kaydedilen takvim dosyasının içeriği doğrulanamadı.")

    digest = hashlib.sha256(saved_content).hexdigest()
    export_id = secrets.token_hex(16)
    created_at = _now()
    with store.connection:
        store.connection.execute(
            """INSERT INTO personal_calendar_exports
               (id,employee_id,person_name,month,plan_version,path,filename,sha256,size_bytes,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (export_id, employee_id, download.person_name, month, download.plan_version, str(path),
             path.name, digest, len(saved_content), created_at),
        )
    return {
        "cancelled": False, "verified": True, "export_id": export_id,
        "employee_id": employee_id, "person_name": download.person_name, "month": month,
        "plan_version": download.plan_version, "path": str(path), "sha256": digest,
        "size_bytes": len(saved_content), "created_at": created_at,
    }


def create_share(handler, store, payload):
    if not _admin_required(handler, store):
        return None
    if not isinstance(payload, dict) or set(payload) != {"employee_id", "month", "days"}:
        raise ValueError("Paylaşım bilgisi geçersiz.")
    employee_id = payload["employee_id"]
    if not isinstance(employee_id, int) or isinstance(employee_id, bool) or employee_id < 1:
        raise ValueError("Personel seçimi geçersiz.")
    month = _valid_month(payload["month"])
    days = payload["days"]
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 30:
        raise ValueError("Paylaşım süresi 1-30 gün arasında olmalıdır.")
    snapshot = _share_snapshot(store, employee_id, month, store._setting("published_plans", {}).get(month))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires = now + timedelta(days=days)
    token = secrets.token_urlsafe(32)
    share_id = secrets.token_urlsafe(18)
    share_rows = store.connection.execute(
        "SELECT employee_id,expires_at,revoked_at FROM workflow_shares"
    ).fetchall()
    if len(share_rows) >= MAX_SHARES_TOTAL:
        raise ValueError("Paylaşım arşivi 5000 kayıt sınırına ulaştı.")
    active_for_employee = sum(
        row["employee_id"] == employee_id and not row["revoked_at"]
        and (_parse_utc(row["expires_at"]) or datetime.min.replace(tzinfo=timezone.utc)) > now
        for row in share_rows
    )
    if active_for_employee >= MAX_SHARES_PER_EMPLOYEE:
        raise ValueError("Personel başına en fazla 20 etkin paylaşım tutulabilir.")
    _snapshot_before_change(store)
    with store.connection:
        store.connection.execute(
            """INSERT INTO workflow_shares
               (id,token_hash,employee_id,person_name,month,snapshot,expires_at,revoked_at,created_at)
               VALUES (?,?,?,?,?,?,?,'',?)""",
            (
                share_id, hashlib.sha256(token.encode("ascii")).hexdigest(),
                employee_id, snapshot["person_name"], month,
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                expires.isoformat(), now.isoformat(),
            ),
        )
    return {
        "id": share_id,
        "token": token,
        "month": month,
        "employee_id": employee_id,
        "expires_at": expires.isoformat(),
    }


def _share_summaries(connection):
    now = datetime.now(timezone.utc)
    rows = connection.execute(
        "SELECT id,employee_id,person_name,month,expires_at,revoked_at,created_at "
        "FROM workflow_shares ORDER BY created_at DESC LIMIT 200"
    ).fetchall()
    items = []
    for row in rows:
        expires = _parse_utc(row["expires_at"])
        items.append({
            "id": row["id"],
            "employee_id": row["employee_id"],
            "person_name": row["person_name"],
            "month": row["month"],
            "expires_at": row["expires_at"],
            "revoked_at": row["revoked_at"],
            "created_at": row["created_at"],
            "active": not row["revoked_at"] and expires is not None and expires > now,
        })
    return items


def _bounded_backup_value(value, budget, depth=0):
    if depth > 12 or budget[0] <= 0:
        return "[kısaltıldı]"
    budget[0] -= 1
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:200]:
            if re.search(r"password|credential|secret|token", str(key), re.I):
                continue
            result[str(key)[:100]] = _bounded_backup_value(item, budget, depth + 1)
            if budget[0] <= 0:
                break
        return result
    if isinstance(value, list):
        return [_bounded_backup_value(item, budget, depth + 1)
                for item in value[:MAX_BACKUP_DETAIL_ITEMS]]
    if isinstance(value, str):
        return value[:2000]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)[:200]


def _inspect_backup(store, backup_id, month=None, kind=None):
    path = store._backup_path(backup_id)
    if not path.is_file() or path.stat().st_size > 500_000_000:
        raise ValueError("Yedek bulunamadı veya 500 MB sınırını aşıyor.")
    uri = path.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        started = time.monotonic()
        connection.set_progress_handler(lambda: int(time.monotonic() - started > 3), 1000)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.OperationalError as exc:
            raise ValueError("Yedek bütünlük denetimi 3 saniye içinde tamamlanamadı.") from exc
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        counts = {}
        for table in ("employees", "schedule_months", "attendance_months", "archive_revisions"):
            if table in tables:
                counts[table] = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        months = {}
        for table in ("schedule_months", "attendance_months"):
            if table in tables:
                months[table] = [
                    row[0] for row in connection.execute(
                        f'SELECT period FROM "{table}" ORDER BY period DESC LIMIT 24'
                    )
                ]
        detail = None
        if month is not None or kind is not None:
            month = _valid_month(month)
            if kind not in ("schedule", "attendance"):
                raise ValueError("Yedek kayıt türü geçersiz.")
            table = "schedule_months" if kind == "schedule" else "attendance_months"
            if table not in tables:
                raise ValueError("Yedekte bu kayıt tablosu bulunamadı.")
            row = connection.execute(
                f'SELECT payload FROM "{table}" WHERE period=?', (month,),
            ).fetchone()
            payload_text = row[0] if row else "{}"
            if len(payload_text.encode("utf-8")) > MAX_BACKUP_DETAIL_BYTES:
                raise ValueError("Yedek ay kaydı 2 MB inceleme sınırını aşıyor.")
            try:
                payload = json.loads(payload_text)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("Yedek ay kaydı geçerli JSON değil.") from exc
            if not isinstance(payload, (dict, list)):
                raise ValueError("Yedek ay kaydının biçimi beklenmiyor.")
            budget = [MAX_BACKUP_DETAIL_ITEMS]
            detail = {
                "month": month,
                "kind": kind,
                "records": _bounded_backup_value(payload, budget),
                "truncated": budget[0] <= 0,
            }
        return {
            "id": path.name,
            "size_bytes": path.stat().st_size,
            "valid": integrity == "ok",
            "integrity": integrity if integrity == "ok" else "Yedek bütünlük denetiminden geçmedi.",
            "counts": counts,
            "months": months,
            **({"detail": detail} if detail is not None else {}),
        }
    finally:
        connection.close()


def calculation_detail(handler, store, month, employee_id):
    month = _valid_month(month)
    if not isinstance(employee_id, int) or isinstance(employee_id, bool) or employee_id < 1:
        raise ValueError("Personel seçimi geçersiz.")
    summary = handler._balance_summary(store, month)
    result = next((row for row in summary["people"] if row["employee_id"] == employee_id), None)
    if result is None:
        raise ValueError("Personel bulunamadı.")
    attendance, _entries, month_data = handler._attendance_for(store, month)
    monthly_row = next(
        (row for row in attendance["rows"] if row["employee_id"] == employee_id), None,
    )
    if monthly_row is None:
        raise ValueError("Bu ay için puantaj hesabı bulunamadı.")
    codes = monthly_row.get("codes", {})
    sources = monthly_row.get("sources", {})
    overtime_details = monthly_row.get("overtime_details", {})
    overtime_detail_minutes = monthly_row.get("overtime_detail_minutes", {})
    daily_rows = []
    for day in attendance.get("dates", []):
        detail = overtime_details.get(day, {})
        detail_minutes = overtime_detail_minutes.get(day, {})
        daily_rows.append({
            "date": day,
            "code": codes.get(day, ""),
            "source": sources.get(day, ""),
            "worked_hours": detail.get("worked", 0),
            "break_hours": detail.get("break", 0),
            "holiday_hours": detail.get("holiday", 0),
            "daily_limit_hours": detail.get("limit", monthly_row.get("daily_limit_hours", 0)),
            "overtime_hours": detail.get("overtime", 0),
            "worked_minutes": detail_minutes.get("worked"),
            "break_minutes": detail_minutes.get("break"),
            "holiday_minutes": detail_minutes.get("holiday"),
            "daily_limit_minutes": detail_minutes.get("limit"),
            "overtime_minutes": detail_minutes.get("overtime"),
        })
    manual = []
    for month_number in range(1, summary["through_month"] + 1):
        source_month = f"{summary['year']}-{month_number:02}"
        attendance = store.attendance_month(source_month)
        for day, codes in attendance["overrides"].items():
            if isinstance(codes, dict) and str(employee_id) in codes:
                manual.append({
                    "month": source_month, "date": day,
                    "code": codes[str(employee_id)], "source": "manual",
                })
    manual.sort(key=lambda item: item["date"])
    return {
        "month": month,
        "employee_id": employee_id,
        "result": result,
        "daily_rows": daily_rows,
        "monthly_totals": {
            key: monthly_row.get(key)
            for key in (
                "worked_hours", "holiday_hours", "overtime_computed_hours",
                "overtime_hours", "overtime_manual", "daily_limit_hours",
            )
        },
        "overtime_settings": store.overtime_settings(month),
        "overtime_rule_effective_month": max(
            (period for period in store._setting("overtime_rule_versions", {}) if period <= month),
            default=None,
        ),
        "overtime_rule_effective_date": (
            f"{max((period for period in store._setting('overtime_rule_versions', {}) if period <= month))}-01"
            if any(period <= month for period in store._setting("overtime_rule_versions", {})) else None
        ),
        "overtime_rule_date_note": "Yürürlük tarihi kayıtlı değil; genel kural uygulanıyor."
            if not any(period <= month for period in store._setting("overtime_rule_versions", {}))
            else "Yürürlük tarihi kayıtlı kural sürümünden alındı.",
        "overtime_rule_source": {
            "daily_limit": "Personel kartı" if (
                monthly_row.get("daily_limit_hours") != store.overtime_settings(month).get("daily_limit_hours")
            ) else "Dönem kuralı",
            "break": "Personel kartı muafiyeti" if (
                store.employee_profiles().get(str(employee_id), {}).get("break_exempt")
            ) else "Dönem kuralı",
        },
        "rounding": {
            "unit": "dakika",
            "display": "Saat gösterimi round(dakika / 60, 2) ile iki ondalık basamağa yuvarlanır.",
        },
        "annual_leave_equation": {
            "opening_days": result["annual_leave_opening_days"],
            "used_days": result["annual_leave_used_days"],
            "remaining_days": result["annual_leave_remaining_days"],
        },
        "attendance_revision": month_data.get("revision", ""),
        "manual_overrides": manual,
        "notice": "Tutar ve gün toplamları mevcut uygulama hesaplamasından alınır; elle girilen puantaj düzeltmeleri ayrıca gösterilir.",
    }


def _related_records(handler, store, month):
    month = _valid_month(month)
    revisions = handler._month_revisions(store, month)
    approvals = store._setting("approvals", {}).get(month, {})
    if not isinstance(approvals, dict):
        approvals = {}
    approval_items = []
    for step in ("prepared", "checked", "approved"):
        entry = approvals.get(step)
        if not isinstance(entry, dict):
            continue
        approval_items.append({
            "step": step,
            "name": entry.get("name", ""),
            "at": entry.get("at", ""),
            "stale": entry.get("revisions") != revisions,
        })
    documents = store.connection.execute(
        "SELECT * FROM workflow_documents ORDER BY created_at DESC LIMIT ?",
        (MAX_DOCUMENTS,),
    ).fetchall()
    related = []
    for row in documents:
        try:
            dates = json.loads(row["exact_dates"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(dates, list) or month not in {
            day[:7] for day in dates if isinstance(day, str) and len(day) >= 7
        }:
            continue
        currentness = _document_currentness(handler, store, row)
        if not currentness["current"]:
            related.append(_document_summary(row, currentness))
    return {
        "month": month,
        "revisions": revisions,
        "stale_approvals": [item for item in approval_items if item["stale"]],
        "stale_documents": related,
    }


def new_export_code():
    return f"EX-{secrets.token_hex(8).upper()}"


def stamp_export(content, filename, export_id):
    if not isinstance(export_id, str) or not EXPORT_CODE.fullmatch(export_id):
        raise ValueError("Çıktı kodu biçimi geçersiz.")
    if not isinstance(content, (bytes, bytearray)) or not content or len(content) > 50_000_000:
        raise ValueError("Damgalanacak çıktı boş veya 50 MB sınırından büyük.")
    content = bytes(content)
    suffix = Path(filename).suffix.lower()
    if suffix == ".xlsx":
        if not content.startswith(b"PK"):
            raise ValueError("Excel dosya imzası geçersiz; çıktı kodu eklenemedi.")
        from openpyxl import load_workbook

        workbook = load_workbook(BytesIO(content))
        for sheet in workbook.worksheets:
            for footer in (sheet.oddFooter, sheet.evenFooter, sheet.firstFooter):
                footer.center.text = f"Code: {export_id}"
                footer.center.size = 8
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()
    if suffix == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("PDF dosya imzası geçersiz; çıktı kodu eklenemedi.")
        from pypdf import PdfReader, PdfWriter
        from pypdf._page import PageObject
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 500:
            raise ValueError("PDF şifreli veya 500 sayfa sınırının dışında.")
        writer = PdfWriter(clone_from=reader)
        for page in writer.pages:
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            overlay = PageObject.create_blank_page(None, width, height)
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            overlay[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            })
            stream = DecodedStreamObject()
            stream.set_data(
                f"BT /F1 8 Tf 1 0 0 1 20 12 Tm (Code: {export_id}) Tj ET".encode("ascii")
            )
            overlay[NameObject("/Contents")] = stream
            page.merge_page(overlay)
        output = BytesIO()
        writer.write(output)
        return output.getvalue()
    if suffix in (".jpg", ".jpeg"):
        if not content.startswith(b"\xff\xd8\xff"):
            raise ValueError("JPEG dosya imzası geçersiz; çıktı kodu eklenemedi.")
        from PIL import Image, ImageDraw, ImageFont

        with Image.open(BytesIO(content)) as source:
            if source.format != "JPEG" or source.width * source.height > 50_000_000:
                raise ValueError("JPEG biçimi veya görsel boyutu geçersiz.")
            image = source.convert("RGB")
            font = ImageFont.load_default()
            strip_height = max(24, min(64, image.width // 20))
            stamped = Image.new("RGB", (image.width, image.height + strip_height), "white")
            stamped.paste(image, (0, 0))
            draw = ImageDraw.Draw(stamped)
            draw.text((8, image.height + max(3, strip_height // 4)),
                      f"Code: {export_id}", fill="black", font=font)
            output = BytesIO()
            stamped.save(output, "JPEG", quality=95, exif=source.info.get("exif", b""))
            return output.getvalue()
    return content


def record_export(
    connection, export_type, month, filename, content, source_revisions,
    app_version=None, code_revision=None, export_id=None, template_revision="",
):
    if not isinstance(export_type, str) or not re.fullmatch(r"[a-z0-9-]{1,40}", export_type):
        raise ValueError("Çıktı türü geçersiz.")
    month = _valid_month(month)
    if not isinstance(filename, str) or not filename or len(filename) > 180 \
            or Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ValueError("Çıktı dosya adı geçersiz.")
    if not isinstance(content, (bytes, bytearray)) or len(content) > 50_000_000:
        raise ValueError("Çıktı boş veya 50 MB sınırından büyük.")
    if not content:
        raise ValueError("İzlenecek çıktı içeriği boş.")
    if not isinstance(template_revision, str) or len(template_revision) > 64:
        raise ValueError("Şablon sürümü geçersiz.")
    if export_id is None:
        export_id = new_export_code()
        while connection.execute("SELECT 1 FROM workflow_exports WHERE id=?", (export_id,)).fetchone():
            export_id = new_export_code()
    elif not isinstance(export_id, str) or not EXPORT_CODE.fullmatch(export_id):
        raise ValueError("Çıktı kodu biçimi geçersiz.")
    elif connection.execute("SELECT 1 FROM workflow_exports WHERE id=?", (export_id,)).fetchone():
        raise ValueError("Bu çıktı kodu zaten kullanılmış.")
    if not isinstance(source_revisions, dict) or not 1 <= len(source_revisions) <= 24:
        raise ValueError("Kaynak sürüm bilgisi geçersiz.")
    normalized_revisions = {}
    for source_month, revisions in source_revisions.items():
        source_month = _valid_month(source_month)
        if not isinstance(revisions, dict) or set(revisions) - {"schedule", "attendance"}:
            raise ValueError("Kaynak sürüm bilgisi geçersiz.")
        if any(not isinstance(value, str) or len(value) > 128 for value in revisions.values()):
            raise ValueError("Kaynak sürüm bilgisi geçersiz.")
        normalized_revisions[source_month] = dict(revisions)
    revision = json.dumps(normalized_revisions, sort_keys=True, separators=(",", ":"))
    with connection:
        connection.execute(
            """INSERT INTO workflow_exports
               (id,export_type,month,filename,sha256,source_revisions,app_version,code_revision,
                template_revision,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                export_id, export_type, month, filename,
                hashlib.sha256(bytes(content)).hexdigest(), revision,
                app_version or version.APP_VERSION, code_revision or current_code_revision(),
                template_revision, _now(),
            ),
        )
    return export_id


def _export_summary(handler, store, row):
    try:
        source = json.loads(row["source_revisions"])
    except (TypeError, json.JSONDecodeError):
        source = {}
    if not isinstance(source, dict):
        source = {}
    current = bool(source) and all(
        revisions == handler._month_revisions(store, source_month)
        for source_month, revisions in source.items()
    )
    if row["export_type"].startswith("draft-"):
        current = False
    return {
            "id": row["id"],
            "code": row["id"],
            "type": row["export_type"],
            "month": row["month"],
            "filename": row["filename"],
            "sha256": row["sha256"],
            "source_revisions": source,
            "app_version": row["app_version"],
            "code_revision": row["code_revision"],
            "template_revision": row["template_revision"],
            "created_at": row["created_at"],
            "current": current,
        }


def _export_summaries(handler, store):
    rows = store.connection.execute(
        "SELECT * FROM workflow_exports ORDER BY created_at DESC LIMIT 200"
    ).fetchall()
    return [_export_summary(handler, store, row) for row in rows]


def public_share_response(connection, token):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return 404, "<!doctype html><meta charset=\"utf-8\"><h1>Paylaşım kullanılamıyor.</h1>".encode("utf-8")
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    try:
        row = connection.execute(
            "SELECT snapshot,expires_at,revoked_at,employee_id FROM workflow_shares WHERE token_hash=?",
            (token_hash,),
        ).fetchone()
    except sqlite3.Error:
        row = None
    if row and connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operations_lifecycle_events'"
    ).fetchone():
        transition = connection.execute(
            "SELECT action FROM operations_lifecycle_events WHERE employee_id=? "
            "AND action IN ('hire','exit') AND effective_date<=? "
            "ORDER BY effective_date DESC,CASE action WHEN 'exit' THEN 1 ELSE 0 END DESC,id DESC LIMIT 1",
            (row["employee_id"], date.today().isoformat()),
        ).fetchone()
        if transition and transition["action"] == "exit":
            row = None
    expiry = _parse_utc(row["expires_at"]) if row else None
    if row is None or row["revoked_at"] or expiry is None or expiry <= datetime.now(timezone.utc):
        body = (
            "<!doctype html><html lang=\"tr\"><meta charset=\"utf-8\">"
            "<title>Paylaşım kullanılamıyor</title><h1>Bu vardiya bağlantısı geçersiz veya süresi dolmuş.</h1></html>"
        ).encode("utf-8")
        status = 404
    else:
        snapshot = json.loads(row["snapshot"])
        name = html.escape(str(snapshot.get("person_name", ""))[:100], quote=True)
        month = html.escape(str(snapshot.get("month", ""))[:7], quote=True)
        rows = []
        for day, shifts in sorted(snapshot.get("days", {}).items()):
            for shift in shifts:
                label = html.escape(str(shift.get("type", ""))[:50], quote=True)
                hours = ""
                start, end = shift.get("start", ""), shift.get("end", "")
                if isinstance(start, str) and isinstance(end, str) and start and end:
                    hours = f"{html.escape(start, quote=True)}–{html.escape(end, quote=True)}"
                rows.append(
                    f"<tr><th scope=\"row\">{html.escape(day, quote=True)}</th>"
                    f"<td>{label}</td><td>{hours}</td></tr>"
                )
        empty = "<p>Bu yayımlanmış planda gün kaydı yok.</p>" if not rows else ""
        body = (
            "<!doctype html><html lang=\"tr\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Vardiya planı</title></head><body>"
            f"<main><h1>{name} · {month}</h1><p>Yayımlanmış vardiya planı</p>"
            f"{empty}<table><thead><tr><th>Tarih</th><th>Plan</th><th>Saat</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></main></body></html>"
        ).encode("utf-8")
        status = 200
    return status, body


def handle_public_share(handler, path):
    """Serve only the exact capability URL; caller invokes before normal auth."""
    match = re.fullmatch(r"/document-share/([A-Za-z0-9_-]{43})", path)
    if not match or getattr(handler, "command", "GET") != "GET":
        return False
    with handler._store() as store:
        status, body = public_share_response(store.connection, match.group(1))
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Security-Policy", "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("X-Robots-Tag", "noindex, nofollow")
    handler.end_headers()
    handler.wfile.write(body)
    return True


def handle_get(handler, path, query):
    prefix = "/api/document-workflows/"
    if not path.startswith(prefix):
        return False
    with handler._store() as store:
        if path == prefix + "documents":
            if not _admin_required(handler, store):
                return True
            handler._json({"documents": _list_documents(handler, store, query)})
            return True
        if path == prefix + "retention":
            if not _admin_required(handler, store):
                return True
            handler._json(_retention_review(store))
            return True
        if path == prefix + "shares":
            if not _admin_required(handler, store):
                return True
            handler._json({"shares": _share_summaries(store.connection)})
            return True
        if path == prefix + "calendar":
            if not _admin_required(handler, store):
                return True
            employee = query.get("employee_id", [""])[0]
            if not re.fullmatch(r"[1-9][0-9]{0,17}", employee):
                raise ValueError("Personel seçimi geçersiz.")
            month = _valid_month(query.get("month", [""])[0])
            download = _calendar_download(store, int(employee), month)
            handler._send(download.content, "text/calendar; charset=utf-8", filename=download.filename)
            return True
        if path == prefix + "exports":
            if not _admin_required(handler, store):
                return True
            handler._json({"exports": _export_summaries(handler, store)})
            return True
        if path == prefix + "exports/lookup":
            if not _admin_required(handler, store):
                return True
            code = query.get("code", [""])[0]
            if not isinstance(code, str) or not EXPORT_CODE.fullmatch(code):
                raise ValueError("Çıktı kodu biçimi geçersiz.")
            row = store.connection.execute(
                "SELECT * FROM workflow_exports WHERE id=?", (code,),
            ).fetchone()
            if row is None:
                raise ValueError("Bu kodla izlenebilir çıktı bulunamadı.")
            handler._json({"export": _export_summary(handler, store, row)})
            return True
        if path == prefix + "related":
            handler._json(_related_records(handler, store, query.get("month", [""])[0]))
            return True
        if path == prefix + "calculation":
            raw_id = query.get("employee_id", [""])[0]
            try:
                employee_id = int(raw_id)
            except (TypeError, ValueError):
                raise ValueError("Personel seçimi geçersiz.") from None
            handler._json(calculation_detail(
                handler, store, query.get("month", [""])[0], employee_id,
            ))
            return True
        if path == prefix + "backup-inspection":
            if not _admin_required(handler, store):
                return True
            backup_id = query.get("id", [""])[0]
            month = query.get("month", [None])[0]
            kind = query.get("kind", [None])[0]
            handler._json({"inspection": _inspect_backup(store, backup_id, month, kind)})
            return True
        if path == prefix + "templates":
            handler._json({"forms": forms.list_templates(store.connection)})
            return True
        match = re.fullmatch(re.escape(prefix) + r"documents/([A-Za-z0-9_-]{1,64})/file", path)
        if match:
            if not _admin_required(handler, store):
                return True
            row = store.connection.execute(
                "SELECT filename,content_type,content FROM workflow_documents WHERE id=?",
                (match.group(1),),
            ).fetchone()
            if row is None:
                raise ValueError("Belge bulunamadı.")
            handler._send(row["content"], row["content_type"], filename=row["filename"])
            return True
        match = re.fullmatch(re.escape(prefix) + r"documents/([A-Za-z0-9_-]{1,64})/preview", path)
        if match:
            if not _admin_required(handler, store):
                return True
            row = store.connection.execute(
                "SELECT filename,content FROM workflow_documents WHERE id=?",
                (match.group(1),),
            ).fetchone()
            if row is None:
                raise ValueError("Belge bulunamadı.")
            extension = Path(row["filename"]).suffix.lower().lstrip(".")
            if extension not in ("docx", "xlsx"):
                raise ValueError("Belge önizleme biçimi desteklenmiyor.")
            handler._json({
                "text": forms.preview(row["content"], extension),
                "html": forms.preview_layout(row["content"], extension),
            })
            return True
        match = re.fullmatch(re.escape(prefix) + r"attachments/([A-Za-z0-9_-]{1,64})/file", path)
        if match:
            if not _admin_required(handler, store):
                return True
            row = store.connection.execute(
                "SELECT filename,content_type,content FROM workflow_attachments WHERE id=?",
                (match.group(1),),
            ).fetchone()
            if row is None:
                raise ValueError("Ek dosyası bulunamadı.")
            handler._send(row["content"], row["content_type"], filename=row["filename"])
            return True
    return False


def rehearse_backup(store, backup_id, export, *, periods=None):
    from storage import Store

    if periods is None:
        periods = {}
    if not isinstance(periods, dict) or set(periods) - {"schedule", "attendance"}:
        raise ValueError("Prova dönemleri geçersiz.")
    periods = {kind: scheduling.validate_month(month) for kind, month in periods.items()}
    original = store._backup_path(backup_id)
    if not original.is_file() or original.stat().st_size > 500_000_000:
        raise ValueError("Yedek bulunamadı veya 500 MB sınırını aşıyor.")
    with tempfile.TemporaryDirectory(prefix="vp-restore-rehearsal-") as directory:
        with Store(Path(directory) / "rehearsal.sqlite3") as isolated:
            isolated.backup_directory.mkdir(exist_ok=True)
            staged = isolated._backup_path(backup_id)
            shutil.copyfile(original, staged)
            templates = store._template_backup_path(original)
            if templates.is_dir():
                isolated._copy_templates(templates, isolated._template_backup_path(staged))
            isolated.restore_backup(backup_id)
            checks = {}
            with scheduling.restored_workbook_settings(
                isolated.template_directory, isolated.schedule_sections(), isolated.custom_holidays(),
            ):
                for kind, table in (("schedule", "schedule_months"), ("attendance", "attendance_months")):
                    if kind in periods:
                        row = isolated.connection.execute(
                            f"SELECT period FROM {table} WHERE period=?", (periods[kind],),
                        ).fetchone()
                        if row is None:
                            raise ValueError(f"Seçilen {periods[kind]} dönemi bu yedekteki "
                                             f"{'vardiya' if kind == 'schedule' else 'puantaj'} kayıtlarında yok.")
                    else:
                        row = isolated.connection.execute(
                            f"SELECT period FROM {table} ORDER BY period DESC LIMIT 1",
                        ).fetchone()
                    if row is None:
                        checks[kind] = None
                        continue
                    month = scheduling.validate_month(row["period"])
                    people = isolated.people()
                    if kind == "schedule":
                        entries = isolated.schedule_month(month)["entries"]
                        scheduling.normalize_schedule_entries(month, entries, people)
                        records = len(entries)
                    else:
                        overrides = isolated.attendance_month(month)["overrides"]
                        scheduling.normalize_attendance_overrides(month, overrides, people)
                        records = sum(map(len, overrides.values()))
                    content, _ = export({"month": [month], "kind": [kind]}, restored_store=isolated)
                    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
                    try:
                        if not workbook.sheetnames or any(
                            not any(value is not None for row in sheet.iter_rows(values_only=True) for value in row)
                            for sheet in workbook.worksheets
                        ):
                            raise ValueError("Prova sırasında üretilen Excel sayfası boş veya okunamıyor.")
                        checks[kind] = {"month": month, "records": records, "sheets": len(workbook.sheetnames)}
                    finally:
                        workbook.close()
            return {"backup_id": backup_id, "checks": checks, "templates_restored": templates.is_dir()}


def handle_write(handler, method, path):
    prefix = "/api/document-workflows/"
    if not path.startswith(prefix):
        return False
    if method == "POST" and path == prefix + "backup-rehearsal":
        from storage import Store

        payload = handler._read_json(8192)
        if (not isinstance(payload, dict) or set(payload) not in ({"id"}, {"id", "periods"})
                or ("periods" in payload and not isinstance(payload["periods"], dict))):
            raise ValueError("Yedek provası seçimi geçersiz.")
        with Store(handler.server.data_path) as store:
            if _admin_required(handler, store):
                handler._json({"rehearsal": rehearse_backup(
                    store, payload["id"], handler._export_workbook, periods=payload.get("periods"),
                )})
        return True
    with handler._store() as store:
        if method == "PUT" and path == prefix + "retention":
            if not _admin_required(handler, store):
                return True
            payload = handler._read_json(8192)
            if not isinstance(payload, dict) or set(payload) != {"retention_days"}:
                raise ValueError("Saklama süresi ayarı geçersiz.")
            retention_days = payload["retention_days"]
            if retention_days is not None and (
                type(retention_days) is not int or not 1 <= retention_days <= MAX_RETENTION_DAYS
            ):
                raise ValueError(f"Saklama süresi 1-{MAX_RETENTION_DAYS} gün arasında olmalı veya kaldırılmalıdır.")
            previous = store.connection.execute(
                "SELECT retention_days FROM workflow_retention_policy WHERE id=1"
            ).fetchone()
            previous_days = previous["retention_days"] if previous is not None else None
            if previous_days != retention_days:
                _snapshot_before_change(store)
                now = _now()
                with store.connection:
                    if retention_days is None:
                        store.connection.execute("DELETE FROM workflow_retention_policy WHERE id=1")
                        summary = "Belge saklama süresi kaldırıldı; kayıtlar korundu."
                    else:
                        store.connection.execute(
                            """INSERT INTO workflow_retention_policy (id,retention_days,updated_at)
                               VALUES (1,?,?)
                               ON CONFLICT(id) DO UPDATE SET
                                   retention_days=excluded.retention_days,
                                   updated_at=excluded.updated_at""",
                            (retention_days, now),
                        )
                        summary = f"Belge saklama süresi {retention_days} gün olarak belirlendi."
                    store.record_change("document.retention_policy_changed", summary, {
                        "retention_days": retention_days,
                    })
            handler._json(_retention_review(store))
            return True
        if method == "POST" and path == prefix + "calendar/save":
            if not _admin_required(handler, store):
                return True
            payload = handler._read_json(8192)
            if set(payload) != {"employee_id", "month"} or type(payload["employee_id"]) is not int \
                    or not 1 <= payload["employee_id"] < 10**18:
                raise ValueError("Takvim için personel seçimi geçersiz.")
            if handler.server.save_form is None:
                raise ValueError("Dosya kaydetme penceresi yalnız masaüstü uygulamasında kullanılabilir.")
            month = _valid_month(payload["month"])
            handler._json(save_personal_calendar(
                store, payload["employee_id"], month, handler.server.save_form,
            ))
            return True
        if method == "POST" and path == prefix + "documents":
            payload = handler._read_json(7_000_000)
            result = create_document(handler, store, payload)
            if result is not None:
                handler._json({"document": result}, 201)
            return True
        if method == "POST" and path == prefix + "documents/batch":
            result = create_documents_batch_result(handler, store, handler._read_json(2_000_000))
            if result is not None:
                handler._json(result, 201)
            return True
        match = re.fullmatch(re.escape(prefix) + r"documents/([A-Za-z0-9_-]{1,64})/save", path)
        if method == "POST" and match:
            if not _admin_required(handler, store):
                return True
            if handler._read_json(8192) != {}:
                raise ValueError("Belge kaydetme isteği geçersiz.")
            if handler.server.save_form is None:
                raise ValueError("Dosya kaydetme penceresi yalnız masaüstü uygulamasında kullanılabilir.")
            row = store.connection.execute(
                "SELECT filename,content FROM workflow_documents WHERE id=?",
                (match.group(1),),
            ).fetchone()
            if row is None:
                raise ValueError("Belge bulunamadı.")
            extension = Path(row["filename"]).suffix.lower().lstrip(".")
            if extension not in ("docx", "xlsx"):
                raise ValueError("Belge dosya biçimi desteklenmiyor.")
            handler._json(handler.server.save_form(extension, row["content"], row["filename"]))
            return True
        if method == "POST" and path == prefix + "shares":
            result = create_share(handler, store, handler._read_json())
            if result is not None:
                handler._json({"share": result}, 201)
            return True
        match = re.fullmatch(re.escape(prefix) + r"documents/([A-Za-z0-9_-]{1,64})/status", path)
        if method == "PUT" and match:
            if not _admin_required(handler, store):
                return True
            payload = handler._read_json()
            if set(payload) != {"status"}:
                raise ValueError("Belge durumu geçersiz.")
            row = store.connection.execute(
                "SELECT status,signers,employee_id,person_name,exact_dates FROM workflow_documents WHERE id=?",
                (match.group(1),),
            ).fetchone()
            if row is None:
                raise ValueError("Belge bulunamadı.")
            if payload["status"] == "signed" and any(
                signer["status"] != "signed" for signer in json.loads(row["signers"])
            ):
                raise ValueError("Belgenin tamamlanması için tüm imzacıların imzası tamamlanmalıdır.")
            status = _advance_status(row["status"], payload["status"])
            _snapshot_before_change(store)
            with store.connection:
                store.connection.execute(
                    "UPDATE workflow_documents SET status=?,updated_at=? WHERE id=?",
                    (status, _now(), match.group(1)),
                )
                store.record_change(
                    f"document.{status}",
                    f"Belge durumu: {row['person_name']} · {json.loads(row['exact_dates'])[0]} · {STATUS_LABELS[status]}",
                    {"document_id": match.group(1), "employee_id": row["employee_id"]},
                )
            handler._json({"id": match.group(1), "status": status, "status_label": STATUS_LABELS[status]})
            return True
        match = re.fullmatch(re.escape(prefix) + r"documents/([A-Za-z0-9_-]{1,64})/signers", path)
        if method == "PUT" and match:
            if not _admin_required(handler, store):
                return True
            payload = handler._read_json(8192)
            if not isinstance(payload, dict) or set(payload) != {"signer_id", "status"} \
                    or not isinstance(payload["signer_id"], str) \
                    or payload["status"] not in ("pending", "signed"):
                raise ValueError("İmzacı durumu geçersiz.")
            row = store.connection.execute(
                "SELECT status,signers,employee_id,person_name,exact_dates FROM workflow_documents WHERE id=?",
                (match.group(1),),
            ).fetchone()
            if row is None:
                raise ValueError("Belge bulunamadı.")
            if row["status"] == "draft":
                raise ValueError("İmzacı imzaları yalnız belge yazdırıldı olarak işaretlendikten sonra kaydedilebilir.")
            signers = json.loads(row["signers"])
            signer = next((item for item in signers if item["id"] == payload["signer_id"]), None)
            if signer is None:
                raise ValueError("İmzacı bulunamadı.")
            if row["status"] == "signed" and payload["status"] != signer["status"]:
                raise ValueError("Tamamlanan belgenin imzacı durumu değiştirilemez.")
            if signer["status"] != payload["status"]:
                signer["status"] = payload["status"]
                _snapshot_before_change(store)
                with store.connection:
                    store.connection.execute(
                        "UPDATE workflow_documents SET signers=?,updated_at=? WHERE id=?",
                        (json.dumps(signers, ensure_ascii=False), _now(), match.group(1)),
                    )
                    store.record_change(
                        "document.signer_signed" if payload["status"] == "signed" else "document.signer_reopened",
                        f"İmzacı durumu: {signer['name']} · {json.loads(row['exact_dates'])[0]}",
                        {"document_id": match.group(1), "employee_id": row["employee_id"]},
                    )
            handler._json({"id": match.group(1), "signers": signers})
            return True
        match = re.fullmatch(re.escape(prefix) + r"documents/([A-Za-z0-9_-]{1,64})/attachments", path)
        if method == "POST" and match:
            result = add_attachment(handler, store, match.group(1), handler._read_json(12_000_000))
            if result is not None:
                handler._json({"attachment": result}, 201)
            return True
        match = re.fullmatch(re.escape(prefix) + r"attachments/([A-Za-z0-9_-]{1,64})/status", path)
        if method == "PUT" and match:
            if not _admin_required(handler, store):
                return True
            payload = handler._read_json()
            if set(payload) != {"status"}:
                raise ValueError("Ek dosya durumu geçersiz.")
            row = store.connection.execute(
                "SELECT status FROM workflow_attachments WHERE id=?", (match.group(1),)
            ).fetchone()
            if row is None:
                raise ValueError("Ek dosyası bulunamadı.")
            status = _advance_status(row["status"], payload["status"])
            _snapshot_before_change(store)
            with store.connection:
                store.connection.execute(
                    "UPDATE workflow_attachments SET status=?,updated_at=? WHERE id=?",
                    (status, _now(), match.group(1)),
                )
            handler._json({"id": match.group(1), "status": status, "status_label": STATUS_LABELS[status]})
            return True
        match = re.fullmatch(re.escape(prefix) + r"shares/([A-Za-z0-9_-]{1,64})/revoke", path)
        if method == "POST" and match:
            if not _admin_required(handler, store):
                return True
            if handler._read_json() != {}:
                raise ValueError("Paylaşım iptal isteği geçersiz.")
            now = _now()
            _snapshot_before_change(store)
            with store.connection:
                cursor = store.connection.execute(
                    "UPDATE workflow_shares SET revoked_at=? WHERE id=? AND revoked_at=''",
                    (now, match.group(1)),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Etkin paylaşım bulunamadı.")
            handler._json({"id": match.group(1), "revoked_at": now})
            return True
    return False


__all__ = [
    "initialize", "handle_get", "handle_write", "handle_public_share", "public_share_response",
    "normalize_selection", "selection_groups", "create_document", "add_attachment",
    "create_documents_batch", "create_share", "calculation_detail",
    "record_export", "new_export_code", "stamp_export", "current_code_revision",
]
