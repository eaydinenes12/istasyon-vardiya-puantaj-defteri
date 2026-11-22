"""Yetkiler, personel bağlantıları ve yerel bildirimler.

Parent integration: initialize(connection), authorize(handler, method, path, query),
handle_get(handler, path, query), handle_write(handler, method, path),
handle_public(handler, rawpath, method), notify_published_in_transaction(store, month, before, after).
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import scheduling

PERMISSIONS = {
    "schedule.read": "Vardiya görüntüleme",
    "schedule.write": "Vardiya düzenleme",
    "attendance.read": "Puantaj görüntüleme",
    "attendance.write": "Puantaj düzenleme",
    "portal.manage": "Personel bağlantıları",
    "portal.review": "Personel taleplerini inceleme",
    "notifications.read": "Bildirimler",
    "backup": "Yedekler",
    "payroll": "Bordro ve ücret",
    "documents": "Belgeler ve kişisel kayıtlar",
    "export": "Dışa aktarma",
    "share": "Paylaşma",
}
PERMISSION_EFFECTS = {
    "schedule.read": {
        "pages": ["Vardiya tablosu", "Güvenli vardiya kontrolleri"],
        "fields": ["Personel adı, bölüm, vardiya türü ve saatleri"],
        "actions": [], "outputs": [],
    },
    "schedule.write": {
        "pages": [], "fields": [],
        "actions": ["Vardiya planını düzenleme ve kaydetme"], "outputs": [],
    },
    "attendance.read": {
        "pages": ["Puantaj tablosu", "Güvenli puantaj kontrolleri"],
        "fields": ["Seçili dönemin puantaj kodları ve güvenli özetleri"],
        "actions": [], "outputs": [],
    },
    "attendance.write": {
        "pages": [], "fields": [],
        "actions": ["Puantaj kayıtlarını düzenleme ve kaydetme"], "outputs": [],
    },
    "portal.manage": {
        "pages": ["Personel bağlantıları"],
        "fields": ["Bağlı personel, bitiş zamanı ve iptal durumu (bağlantı kodu değil)"],
        "actions": ["Süreli personel bağlantısı oluşturma ve iptal etme"], "outputs": [],
    },
    "portal.review": {
        "pages": ["Personel talepleri"],
        "fields": ["Talep sahibi, türü, tarihleri ve kullanıcının yazdığı açıklama"],
        "actions": ["Talebi kabul etme veya reddetme"], "outputs": [],
    },
    "notifications.read": {
        "pages": ["Bildirimler"], "fields": ["Kullanıcıya ait bildirim ve okundu durumu"],
        "actions": ["Bildirimi okundu işaretleme"], "outputs": [],
    },
    "backup": {
        "pages": ["Yedekleme ve geri yükleme"],
        "fields": ["Yedeklerin kapsamı ve seçilen dönem karşılaştırması"],
        "actions": ["Yedek oluşturma, doğrulama ve seçili dönem kurtarma"], "outputs": [],
    },
    "payroll": {
        "pages": ["Bordro ve ücret"],
        "fields": ["Ücret, bordro kodu ve ücret hesap ayrıntıları"],
        "actions": [], "outputs": ["Bordro ve ücret çıktıları"],
    },
    "documents": {
        "pages": ["Personel kartları, belgeler ve arşiv"],
        "fields": ["Personel notları, profil bilgileri ve belge içerikleri"],
        "actions": ["Belge ve kişisel kayıtları görüntüleme"], "outputs": ["Belge önizlemeleri"],
    },
    "export": {
        "pages": ["Dışa aktarma ve çıktı önizlemesi"],
        "fields": ["Seçilen dışa aktarma içeriği"],
        "actions": [], "outputs": ["Kullanıcının seçtiği dışa aktarma dosyaları"],
    },
    "share": {
        "pages": ["E-posta taslağı ve otomatik gönderim", "WhatsApp paylaşımı"],
        "fields": ["Alıcı adresleri, SMTP ayarları ve gönderim geçmişi"],
        "actions": ["E-posta planı yönetme ve gönderimi başlatma"], "outputs": ["E-posta ve paylaşılan çıktılar"],
    },
}
SENSITIVE = {"backup", "payroll", "documents", "export", "share", "portal.manage"}

# Exact routes only: unknown endpoints are denied to non-admins.
READ_ROUTES = {
    "/api/schedule/workspace": "schedule.read", "/api/attendance/workspace": "attendance.read",
    "/api/schedule/draft": "schedule.write", "/api/attendance/draft": "attendance.write",
    "/api/environment": "schedule.read", "/api/lock": "schedule.read",
    "/api/people": "schedule.read", "/api/template": "schedule.read",
    "/api/schedule": "schedule.read", "/api/quick-entries": "schedule.read",
    "/api/import-profiles": "schedule.read",
    "/api/operations/hourly-staffing": "schedule.read",
    "/api/operations/shift-activities": "schedule.read",
    "/api/schedule-templates": "schedule.read", "/api/schedule-sections": "schedule.read",
    "/api/holidays": "schedule.read", "/api/schedule/plan-diff": "schedule.read",
    "/api/workbook-settings": "schedule.read", "/api/ui-settings": "schedule.read",
    "/api/schedule-validation-settings": "schedule.read", "/api/person-tags": "schedule.read",
    "/api/table-templates": "schedule.read", "/api/user-settings": "schedule.read",
    "/api/attendance": "attendance.read",
    "/api/linked-leave/forecast": "attendance.read",
    "/api/schedule/protection": "schedule.read",
    "/api/attendance/corrections": "attendance.read",
    "/api/reports/check": "attendance.read",
    "/api/backups": "backup", "/api/backup-mirror": "backup",
    "/api/export": "export", "/api/export-preview": "export", "/api/export-name": "export",
    "/api/mail-automation": "share",
    "/api/mail-automation/recipients": "share",
    "/api/employee-profile": "documents", "/api/personnel-overview": "documents",
    "/api/personnel-assets": "documents",
    "/api/forms": "documents", "/api/forms/template": "documents",
    "/api/archive": "documents", "/api/archive/search": "documents",
    "/api/archive/revisions": "documents", "/api/archive/compare": "documents",
    "/api/payroll-extras": "payroll", "/api/payroll/balancing": "payroll",
    "/api/payroll/sgk": "payroll", "/api/leave/termination": "payroll",
    "/api/access/permissions": "users", "/api/access/portal-links": "portal.manage",
    "/api/access/device-sessions": "portal.manage",
    "/api/access/requests": "portal.review", "/api/access/notifications": "notifications.read",
    "/api/workspace/mobile-diagnostics": "share",
    "/api/diagnostics/errors": "users",
}
WRITE_ROUTES = {
    ("PUT", "/api/schedule/draft"): "schedule.write",
    ("PUT", "/api/attendance/draft"): "attendance.write",
    ("POST", "/api/schedule/draft/discard"): "schedule.write",
    ("POST", "/api/attendance/draft/discard"): "attendance.write",
    ("POST", "/api/schedule/bulk-check"): "schedule.write",
    ("POST", "/api/attendance/bulk-check"): "attendance.write",
    ("POST", "/api/linked-leave/requests/bulk"): "attendance.write",
    ("PUT", "/api/schedule"): "schedule.write",
    ("PUT", "/api/schedule-months"): "schedule.write",
    ("POST", "/api/schedule/restore-cell"): "schedule.write",
    ("POST", "/api/attendance/restore-cell"): "attendance.write",
    ("POST", "/api/schedule/publish"): "schedule.write",
    ("PUT", "/api/attendance"): "attendance.write",
    ("PUT", "/api/mail-automation"): "share",
    ("PUT", "/api/mail-automation/recipients"): "share",
    ("POST", "/api/mail-automation/send-now"): "share",
    ("POST", "/api/mail-automation/preview-recipient"): "share",
    ("POST", "/api/mail-automation/recipients/remove"): "share",
    ("POST", "/api/attendance/corrections"): "attendance.write",
    ("POST", "/api/access/portal-links"): "portal.manage",
    ("POST", "/api/access/portal-links/revoke"): "portal.manage",
    ("POST", "/api/access/device-sessions/revoke"): "portal.manage",
    ("POST", "/api/access/requests/review"): "portal.review",
    ("POST", "/api/access/notifications/read"): "notifications.read",
    ("PUT", "/api/access/permissions"): "users",
    ("POST", "/api/backups"): "backup", ("POST", "/api/backups/verify"): "backup",
    ("POST", "/api/backups/restore"): "backup",
    ("POST", "/api/mail-draft"): "share", ("POST", "/api/open-whatsapp"): "share",
    ("POST", "/api/save-jpeg"): "export", ("POST", "/api/save-excel"): "export",
    ("POST", "/api/forms/preview"): "documents",
    ("POST", "/api/schedule/suggestion-check"): "schedule.read",
    ("POST", "/api/schedule/compare-plans"): "schedule.read",
    ("POST", "/api/import-extract"): "schedule.read",
    ("POST", "/api/import-preview"): "schedule.read",
    ("POST", "/api/import-mapped-preview"): "schedule.read",
    ("PUT", "/api/import-profiles"): "schedule.write",
    ("POST", "/api/personnel-assets"): "documents",
}
VIEWER = {"schedule.read", "attendance.read", "notifications.read"}


def initialize(connection):
    for sql in (
        """CREATE TABLE IF NOT EXISTS portal_tokens (
          token_hash TEXT PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
          expires_at INTEGER NOT NULL, revoked_at INTEGER, created_at INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS portal_requests (
          id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER NOT NULL REFERENCES employees(id),
          kind TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
          details TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          created_at INTEGER NOT NULL, reviewed_at INTEGER, reviewer TEXT)""",
        """CREATE TABLE IF NOT EXISTS access_notifications (
          id INTEGER PRIMARY KEY AUTOINCREMENT, recipient TEXT NOT NULL, category TEXT NOT NULL,
          message TEXT NOT NULL, created_at INTEGER NOT NULL, delivered_at INTEGER, read_at INTEGER)""",
        """CREATE TABLE IF NOT EXISTS portal_rate (
          key TEXT NOT NULL, bucket INTEGER NOT NULL, count INTEGER NOT NULL,
          PRIMARY KEY(key, bucket))""",
        """CREATE TABLE IF NOT EXISTS portal_plan_views (
          token_hash TEXT NOT NULL, month TEXT NOT NULL, revision TEXT NOT NULL,
          viewed_at INTEGER NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(token_hash, month))""",
        """CREATE TABLE IF NOT EXISTS portal_plan_read_tokens (
          token_hash TEXT NOT NULL, month TEXT NOT NULL, revision TEXT NOT NULL,
          nonce_hash TEXT NOT NULL, expires_at INTEGER NOT NULL,
          PRIMARY KEY(token_hash, month))""",
        """CREATE TABLE IF NOT EXISTS portal_access_activity (
          token_hash TEXT PRIMARY KEY, last_seen_at INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS portal_device_sessions (
          session_id TEXT PRIMARY KEY, session_hash TEXT NOT NULL UNIQUE,
          token_hash TEXT NOT NULL REFERENCES portal_tokens(token_hash),
          employee_id INTEGER NOT NULL REFERENCES employees(id),
          created_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL,
          expires_at INTEGER NOT NULL, revoked_at INTEGER)""",
        """CREATE INDEX IF NOT EXISTS portal_device_sessions_employee
          ON portal_device_sessions(employee_id, created_at DESC)""",
    ):
        connection.execute(sql)
    plan_view_columns = {row[1] for row in connection.execute("PRAGMA table_info(portal_plan_views)")}
    if "confirmed" not in plan_view_columns:
        connection.execute(
            "ALTER TABLE portal_plan_views ADD COLUMN confirmed INTEGER NOT NULL DEFAULT 0"
        )


def _perms(user):
    if user.get("role") == "viewer":
        return VIEWER
    if user.get("role") == "operator":
        value = user.get("permissions")
        return set(value) & PERMISSIONS.keys() if isinstance(value, list) and all(isinstance(x, str) for x in value) else set()
    return set()


def permission_preview(permissions):
    if not isinstance(permissions, (list, tuple, set)) or any(
        not isinstance(permission, str) or permission not in PERMISSIONS for permission in permissions
    ):
        raise ValueError("Yetki önizlemesi için bilinen yetkileri seçin.")
    result = {key: [] for key in ("pages", "fields", "actions", "outputs")}
    for permission in permissions:
        for key in result:
            for value in PERMISSION_EFFECTS[permission][key]:
                if value not in result[key]:
                    result[key].append(value)
    return result


def authorize(handler, method, path, query):
    """Return bool; on denial send a response. Refresh the session identity every request."""
    if not path.startswith("/api/"):
        return True
    if handler._is_internal():
        return True
    from storage import Store
    with Store(handler.server.data_path) as store:
        users = store.users()
    if not users:
        handler.current_user = None
        return True
    session = handler.server.sessions.get(handler._session_token())
    live = next((u for u in users if session and u["username"].casefold() == session.get("username", "").casefold()), None)
    if live is None:
        handler.current_user = None
        handler._json({"error": "Devam etmek için oturum açın.", "auth_required": True}, 401)
        return False
    handler.current_user = live
    handler.server.sessions[handler._session_token()] = live
    if live["role"] == "admin":
        return True
    if path in {"/api/assistant/models", "/api/assistant/chat"}:
        if _perms(live).intersection({"schedule.read", "attendance.read"}):
            return True
        handler._json({"error": "Vardiya veya puantaj görüntüleme yetkisi gereklidir."}, 403)
        return False
    required = READ_ROUTES.get(path) if method == "GET" else WRITE_ROUTES.get((method, path))
    if required and required in _perms(live):
        return True
    handler._json({"error": "Bu işlem için yetkiniz yok."}, 403)
    return False


def _now():
    return int(time.time())


def _token_hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _valid_token(connection, token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token or ""):
        return None
    return connection.execute(
        "SELECT t.employee_id FROM portal_tokens t JOIN employees e ON e.id=t.employee_id "
        "WHERE t.token_hash=? AND t.revoked_at IS NULL AND t.expires_at>? AND e.active=1",
        (_token_hash(token), _now())).fetchone()


def _rate(connection, key, maximum, seconds):
    bucket = _now() // seconds
    connection.execute("DELETE FROM portal_rate WHERE key=? AND bucket < ?", (key, bucket - 2))
    connection.execute("INSERT INTO portal_rate VALUES (?, ?, 1) ON CONFLICT(key,bucket) DO UPDATE SET count=count+1", (key, bucket))
    return connection.execute("SELECT count FROM portal_rate WHERE key=? AND bucket=?", (key, bucket)).fetchone()[0] <= maximum


def _recipients(store, permission):
    users = store.users()
    return [u["username"] for u in users
            if u["role"] == "admin" or permission in _perms(u)] if users else [""]


def _notify(store, recipients, category, message):
    for recipient in set(recipients):
        store.connection.execute(
            "INSERT INTO access_notifications(recipient,category,message,created_at) VALUES (?,?,?,?)",
            (recipient, category, message, _now()))


def _employee_plan_changes(plans, employee_id):
    changes = []
    for month in sorted(plans, reverse=True):
        if not isinstance(month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            continue
        plan = plans[month]
        if not isinstance(plan, dict):
            continue
        for item in plan.get("changes", ()):
            if not isinstance(item, dict) or item.get("employee_id") != employee_id:
                continue
            day = item.get("date")
            if not isinstance(day, str) or not re.fullmatch(re.escape(month) + r"-\d{2}", day):
                continue
            changes.append({
                "month": month, "date": day, "section": str(item.get("section", ""))[:80],
                "before": str(item.get("before", ""))[:80], "after": str(item.get("after", ""))[:80],
            })
    return changes


def _confirm_plan_read(connection, token, employee_id, month, revision, plans):
    initialize(connection)
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) \
            or not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{32}", revision):
        return False
    plan = plans.get(month)
    if not isinstance(plan, dict) or plan.get("source_revision") != revision or not any(
        isinstance(row, dict) and row.get("employee_id") == employee_id
        for row in plan.get("entries", ())
    ):
        return False
    token_hash = _token_hash(token)
    connection.execute(
        "INSERT INTO portal_plan_views(token_hash,month,revision,viewed_at,confirmed) VALUES(?,?,?,?,1) "
        "ON CONFLICT(token_hash,month) DO UPDATE SET revision=excluded.revision,"
        "viewed_at=excluded.viewed_at,confirmed=1",
        (token_hash, month, revision, _now()),
    )
    return True


def _new_plan_read_nonce(connection, token, month, revision):
    nonce = secrets.token_urlsafe(24)
    connection.execute(
        "DELETE FROM portal_plan_read_tokens WHERE expires_at<=?", (_now(),),
    )
    connection.execute(
        "INSERT INTO portal_plan_read_tokens(token_hash,month,revision,nonce_hash,expires_at) "
        "VALUES(?,?,?,?,?) ON CONFLICT(token_hash,month) DO UPDATE SET revision=excluded.revision,"
        "nonce_hash=excluded.nonce_hash,expires_at=excluded.expires_at",
        (_token_hash(token), month, revision, _token_hash(nonce), _now() + 600),
    )
    return nonce


def _consume_plan_read_nonce(connection, token, month, revision, nonce):
    if not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", nonce):
        return False
    row = connection.execute(
        "SELECT revision,nonce_hash,expires_at FROM portal_plan_read_tokens "
        "WHERE token_hash=? AND month=?",
        (_token_hash(token), month),
    ).fetchone()
    if not row or row["revision"] != revision or row["expires_at"] <= _now() \
            or not secrets.compare_digest(row["nonce_hash"], _token_hash(nonce)):
        return False
    connection.execute(
        "DELETE FROM portal_plan_read_tokens WHERE token_hash=? AND month=?",
        (_token_hash(token), month),
    )
    return True


def _record_portal_activity(connection, token):
    initialize(connection)
    connection.execute(
        "INSERT INTO portal_access_activity(token_hash,last_seen_at) VALUES(?,?) "
        "ON CONFLICT(token_hash) DO UPDATE SET last_seen_at=excluded.last_seen_at",
        (_token_hash(token), _now()),
    )


def _record_device_session(connection, session_id, session_cookie, token, employee_id, expires_at):
    initialize(connection)
    now = _now()
    connection.execute(
        "INSERT INTO portal_device_sessions(session_id,session_hash,token_hash,employee_id,created_at,"
        "last_seen_at,expires_at,revoked_at) VALUES(?,?,?,?,?,?,?,NULL)",
        (session_id, _token_hash(session_cookie), _token_hash(token), employee_id, now, now, expires_at),
    )


def _valid_device_session(connection, session_cookie, token, employee_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", session_cookie or ""):
        return None
    initialize(connection)
    now = _now()
    record = connection.execute(
        "SELECT s.session_id FROM portal_device_sessions s "
        "JOIN portal_tokens t ON t.token_hash=s.token_hash "
        "JOIN employees e ON e.id=s.employee_id "
        "WHERE s.session_hash=? AND s.token_hash=? AND s.employee_id=? "
        "AND s.revoked_at IS NULL AND s.expires_at>? "
        "AND t.revoked_at IS NULL AND t.expires_at>? AND e.active=1",
        (_token_hash(session_cookie), _token_hash(token), employee_id, now, now),
    ).fetchone()
    if record:
        with connection:
            connection.execute(
                "UPDATE portal_device_sessions SET last_seen_at=? WHERE session_id=?",
                (now, record["session_id"]),
            )
    return record


def _plan_version_status(store, token_hash, employee_id):
    initialize(store.connection)
    plans = store._setting("published_plans", {})
    views = {row["month"]: row for row in store.connection.execute(
        "SELECT month,revision,viewed_at FROM portal_plan_views WHERE token_hash=? AND confirmed=1",
        (token_hash,),
    )}
    activity = store.connection.execute(
        "SELECT last_seen_at FROM portal_access_activity WHERE token_hash=?", (token_hash,),
    ).fetchone()
    result = []
    for month in sorted(plans, reverse=True):
        plan = plans[month]
        if not isinstance(plan, dict) or not any(
            isinstance(row, dict) and row.get("employee_id") == employee_id
            for row in plan.get("entries", ())
        ):
            continue
        revision = str(plan.get("source_revision", ""))
        viewed = views.get(month)
        if revision:
            result.append({
                "month": month, "published_revision": revision,
                "viewed_revision": viewed["revision"] if viewed else "",
                "viewed_at": viewed["viewed_at"] if viewed else None,
            })
    return {"versions": result[:12], "last_seen_at": activity["last_seen_at"] if activity else None}


def notify_published_in_transaction(store, month, before, after):
    """No commit/begin: call within the existing publication transaction."""
    old = before.get("entries", []) if isinstance(before, dict) else (before or [])
    new = after.get("entries", []) if isinstance(after, dict) else (after or [])
    def canonical(entries):
        return sorted(
            (int(row["employee_id"]), str(row.get("section", "")), day,
             json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
            for row in entries for day, item in (row.get("days") or {}).items()
            if day.startswith(month + "-") and item
        )
    if canonical(old) != canonical(new):
        _notify(store, _recipients(store, "notifications.read"), "published",
                f"{month} yayınlanan vardiya planı değişti.")


def _request_data(payload):
    if set(payload) != {"kind", "start_date", "end_date", "details"}:
        raise ValueError("Talep alanları geçersiz.")
    if payload["kind"] not in ("leave", "swap", "availability"):
        raise ValueError("Talep türü geçersiz.")
    try:
        start, end = date.fromisoformat(payload["start_date"]), date.fromisoformat(payload["end_date"])
    except (ValueError, TypeError):
        raise ValueError("Tarihleri kontrol edin.") from None
    if end < start or (end - start).days > 365:
        raise ValueError("Tarih aralığı geçersiz.")
    details = payload["details"]
    if not isinstance(details, str) or len(details) > 300 or any(ord(c) < 32 and c not in "\n\t" for c in details):
        raise ValueError("Açıklama en fazla 300 karakter olmalıdır.")
    return payload["kind"], start.isoformat(), end.isoformat(), details.strip()


def _attendance_correction_data(payload):
    if set(payload) != {"date", "code", "reason"}:
        raise ValueError("Puantaj düzeltme alanları geçersiz.")
    try:
        day = date.fromisoformat(payload["date"])
    except (ValueError, TypeError):
        raise ValueError("Puantaj tarihini kontrol edin.") from None
    code = scheduling.normalize_attendance_code(payload["code"], allow_custom=False)
    if not code:
        raise ValueError("Yeni bir puantaj kodu seçin.")
    reason = payload["reason"]
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 500:
        raise ValueError("Düzeltme gerekçesi 3–500 karakter olmalıdır.")
    return day.isoformat(), code, reason.strip()


def _public_page(store, employee_id, token, message=""):
    import operations
    person = store.connection.execute("SELECT name FROM employees WHERE id=? AND active=1", (employee_id,)).fetchone()
    if not person:
        return None
    published = store._setting("published_plans", {})
    lines = []
    valid_months = sorted(
        (month for month, plan in published.items()
         if isinstance(month, str) and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month)
         and isinstance(plan, dict)), reverse=True)[:12]
    for month in valid_months:
        plan = published[month]
        for row in plan.get("entries", []):
            if row.get("employee_id") != employee_id:
                continue
            for day, item in sorted((row.get("days") or {}).items()):
                if not isinstance(day, str) or not re.fullmatch(re.escape(month) + r"-\d{2}", day):
                    continue
                if isinstance(item, dict) and item.get("kind") == "work" and all(
                        re.fullmatch(r"\d{2}:\d{2}", str(item.get(k, ""))) for k in ("start", "end")):
                    label = f"{item.get('start', '')}–{item.get('end', '')}"
                elif isinstance(item, dict) and item.get("kind") == "weekly_off":
                    label = "Hafta tatili"
                else:
                    continue
                lines.append(f"<tr><td>{html.escape(day)}</td><td>{html.escape(label)}</td></tr>")
    changes = _employee_plan_changes(published, employee_id)
    change_rows = "".join(
        f"<tr><td>{html.escape(item['date'])}</td><td>{html.escape(item['section'])}</td>"
        f"<td>{html.escape(item['before'] or 'Yeni vardiya')}</td>"
        f"<td>{html.escape(item['after'] or 'Kaldırıldı')}</td></tr>"
        for item in changes[:80]
    )
    receipt_rows = []
    receipts = {row["month"]: row for row in store.connection.execute(
        "SELECT month,revision FROM portal_plan_views WHERE token_hash=? AND confirmed=1",
        (_token_hash(token),),
    )}
    for month in valid_months:
        plan = published[month]
        revision = plan.get("source_revision")
        if not isinstance(revision, str) or not revision or not any(
            isinstance(row, dict) and row.get("employee_id") == employee_id
            for row in plan.get("entries", ())
        ):
            continue
        receipt = receipts.get(month)
        if receipt and receipt["revision"] == revision:
            receipt_rows.append(
                f"<p role=\"status\">{html.escape(month)} yayınının bu sürümünü okudunuz.</p>"
            )
        else:
            nonce = _new_plan_read_nonce(store.connection, token, month, revision)
            receipt_rows.append(
                f'<form method="post" action="/employee-portal/{token}/read">'
                f'<input type="hidden" name="month" value="{html.escape(month, quote=True)}">'
                f'<input type="hidden" name="revision" value="{html.escape(revision, quote=True)}">'
                f'<input type="hidden" name="csrf" value="{html.escape(nonce, quote=True)}">'
                f'<button>Bu yayın sürümünü okudum</button></form>'
            )
    requests = store.connection.execute(
        "SELECT kind,start_date,end_date,status FROM portal_requests WHERE employee_id=? ORDER BY id DESC LIMIT 50",
        (employee_id,)).fetchall()
    labels = {"leave": "İzin", "swap": "Takas", "availability": "Uygunluk",
              "pending": "Bekliyor", "accepted": "İşlem bekliyor", "rejected": "Reddedildi"}
    history_items = [
        html.escape(f"{labels.get(r[0], '')}: {r[1]} – {r[2]} · {labels.get(r[3], '')}")
        for r in requests
    ]
    corrections = store.connection.execute(
        "SELECT day,code,reason,status FROM attendance_corrections "
        "WHERE employee_id=? AND requested_by=? ORDER BY created_at DESC LIMIT 50",
        (employee_id, f"Personel portalı #{employee_id}"),
    ).fetchall()
    correction_statuses = {"pending": "Yönetici incelemesi bekleniyor",
                           "approved": "Yönetici onayladı", "rejected": "Yönetici reddetti"}
    history_items.extend(
        html.escape(f"Puantaj düzeltmesi: {row[0]} · {row[1]} · {row[2]} · {correction_statuses.get(row[3], row[3])}")
        for row in corrections
    )
    history = "".join(f"<li>{item}</li>" for item in history_items)
    swaps = []
    for item in operations.mutual_swaps(store, employee_id=employee_id):
        if item["status"] != "pending":
            continue
        index = item["employees"].index(employee_id)
        old, new = (item["pair"][i]["shift"] for i in (index, 1 - index))
        summary = html.escape(f"{item['date']} · {old['start']}–{old['end']} → {new['start']}–{new['end']}")
        accepted = employee_id in item["accepted"]
        swaps.append(f"""<li><p>{summary}</p>
<form method="post" action="/employee-portal/{token}/swaps">
<input type="hidden" name="id" value="{html.escape(item['id'], quote=True)}">
{'<p>Yanıtınız kaydedildi. Son karar yöneticiye aittir.</p>' if accepted else '<button name="decision" value="accept">Kabul et</button>'}
<button name="decision" value="decline">{'Kabulü geri çek' if accepted else 'Reddet'}</button>
</form></li>""")
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta name="referrer" content="no-referrer"><title>Personel alanı</title><style>
body{{font:16px 'Segoe UI',sans-serif;max-width:48rem;margin:auto;padding:1rem;color:#1c2a22;background:#f4f7f4}}
table{{border-collapse:collapse;background:white;width:100%}}td,th{{border:1px solid #ccd7cf;padding:.6rem;text-align:left}}
label{{display:block;margin:.7rem 0}}input,select,textarea,button{{font:inherit;min-height:44px;max-width:100%;box-sizing:border-box}}
button{{background:#174b31;color:white;border:0;padding:0 1rem}}textarea{{width:100%}}</style></head>
<body><h1>Personel alanı</h1><p>{html.escape(person[0])}</p>
<p role="status">{html.escape(message)}</p><h2>Yayınlanan programım</h2><p>Son 12 yayınlanan ay gösterilir.</p>
<table><thead><tr><th>Tarih</th><th>Saat</th></tr></thead><tbody>{''.join(lines) or '<tr><td colspan="2">Yayınlanan çalışma saati yok.</td></tr>'}</tbody></table>
<h2>Programımdaki son değişiklikler</h2><p>Eski ve yeni yayınlanan vardiya bilgisi.</p>
<table class="change-table"><thead><tr><th>Tarih</th><th>Bölüm</th><th>Önceki vardiya</th><th>Yeni yayınlanan vardiya</th></tr></thead>
<tbody>{change_rows or '<tr><td colspan="4">Yayımlanmış değişiklik yok.</td></tr>'}</tbody></table>
<h2>Yayın sürümü onayı</h2><p>Her yeni yayın sürümü için ayrı okuma onayı gerekir.</p>{''.join(receipt_rows)}
<h2>Talep gönder</h2><p>Sağlık veya ücret bilgisi yazmayın. Kabul edilen talepler ayrıca işlenir.</p>
<form method="post" action="/employee-portal/{token}/requests">
<label>Tür <select name="kind"><option value="leave">İzin</option><option value="swap">Takas</option><option value="availability">Uygunluk</option></select></label>
<label>Başlangıç <input type="date" name="start_date" required></label><label>Bitiş <input type="date" name="end_date" required></label>
<label>Açıklama <textarea name="details" maxlength="300"></textarea></label><button>Talep gönder</button></form>
<h2>Kendi puantaj günüm için düzeltme iste</h2>
<p>Yalnız kendi gününüz için talep oluşturabilirsiniz. Yönetici kaynakları inceleyip onaylamadan puantaj değişmez.</p>
<form method="post" action="/employee-portal/{token}/attendance-corrections">
<label>Tarih <input type="date" name="date" required></label>
<label>Önerilen kod <select name="code" required><option value="">Kod seçin</option>
<option value="X">Çalışma · X</option><option value="H">Hafta tatili · H</option><option value="G.Ç">Geceden çıkış · G.Ç</option>
<option value="S">Senelik izin · S</option><option value="R">Rapor · R</option><option value="Ü">Ücretsiz izin · Ü</option>
<option value="G">İşe gelmedi · G</option><option value="İST.">İstifa · İST.</option><option value="E">Evlilik izni · E</option>
<option value="K">Karantina · K</option><option value="Y">Yarım gün · Y</option></select></label>
<label>Gerekçe <textarea name="reason" minlength="3" maxlength="500" required></textarea></label>
<button>Düzeltme iste</button></form>
<h2>Taleplerim</h2><ul>{history or '<li>Henüz talep yok.</li>'}</ul>
<h2>Karşılıklı vardiya takası</h2><ul>{''.join(swaps) or '<li>Bekleyen takas yok.</li>'}</ul></body></html>"""


def _public_send(handler, status, body):
    data = body.encode("utf-8")
    handler.send_response(status)
    for key, value in (("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(data))),
                       ("Cache-Control", "no-store"), ("Referrer-Policy", "same-origin"),
                       ("X-Content-Type-Options", "nosniff"),
                       ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")):
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(data)


def _public_redirect(handler, path):
    handler.send_response(303)
    handler.send_header("Location", path)
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()


def _same_origin_post(headers) -> bool:
    host = headers.get("Host", "")
    if not host:
        return False
    expected = {"http://" + host, "https://" + host}
    origin = headers.get("Origin", "")
    if origin and origin != "null":
        return origin in expected
    if headers.get("Sec-Fetch-Site", "").lower() == "same-origin":
        return True
    referer = headers.get("Referer", "")
    parsed = urlsplit(referer)
    return bool(referer) and parsed.scheme + "://" + parsed.netloc in expected


def handle_public(handler, rawpath, method):
    parsed = urlsplit(rawpath)
    match = re.fullmatch(r"/employee-portal/([A-Za-z0-9_-]{43})(/requests|/swaps|/read|/attendance-corrections)?", parsed.path)
    if not match:
        return False
    suffix = match.group(2)
    if parsed.query or (method == "GET" and suffix not in (None, "/read")) \
            or (method == "POST" and suffix is None) or method not in ("GET", "POST"):
        _public_send(handler, 404, "Bağlantı kullanılamıyor.")
        return True
    token = match.group(1)
    from storage import Store
    with Store(handler.server.data_path) as store:
        import operations
        operations.process_due_lifecycle(store)
        with store.connection:
            identity = _valid_token(store.connection, token)
            ip = str(getattr(handler, "client_address", ("",))[0])[:80]
            allowed = _rate(store.connection, "ip:" + ip, 120, 60)
            if not identity or not allowed:
                _public_send(handler, 404 if not identity else 429, "Bağlantı kullanılamıyor.")
                return True
            employee_id = identity[0]
            _record_portal_activity(store.connection, token)
            plans = store._setting("published_plans", {})
            if method == "POST":
                if not _same_origin_post(handler.headers):
                    _public_send(handler, 403, "İstek doğrulanamadı.")
                    return True
                try:
                    length = int(handler.headers.get("Content-Length", "0"))
                    if not 0 < length <= 2048 or handler.headers.get("Content-Type", "").split(";")[0] != "application/x-www-form-urlencoded":
                        raise ValueError("İstek geçersiz.")
                    body = handler.rfile.read(length).decode("utf-8")
                    form = parse_qs(body, keep_blank_values=True, max_num_fields=4, strict_parsing=True)
                    if any(len(values) != 1 for values in form.values()):
                        raise ValueError("İstek geçersiz.")
                    if not _valid_token(store.connection, token):
                        _public_send(handler, 404, "Bağlantı kullanılamıyor.")
                        return True
                    fields = {key: values[0] for key, values in form.items()}
                    if match.group(2) == "/read":
                        if set(fields) != {"month", "revision", "csrf"}:
                            raise ValueError("Yayın onayı geçersiz.")
                        if not _rate(store.connection, "read:" + _token_hash(token), 120, 86400):
                            _public_send(handler, 429, "Okuma onayı sınırına ulaşıldı.")
                            return True
                        if not _consume_plan_read_nonce(
                            store.connection, token, fields["month"], fields["revision"], fields["csrf"],
                        ):
                            _public_send(handler, 403, "Okuma onayı doğrulanamadı; güncel programı yenileyin.")
                            return True
                        if not _confirm_plan_read(
                            store.connection, token, employee_id, fields["month"], fields["revision"], plans,
                        ):
                            page = _public_page(
                                store, employee_id, token,
                                "Yayın sürümü değişti; güncel programı açıp yeniden onaylayın.",
                            )
                            _public_send(handler, 409, page)
                            return True
                        _public_redirect(handler, "/employee-portal/" + token)
                        return True
                    elif not _rate(store.connection, "post:" + _token_hash(token), 20, 86400):
                        _public_send(handler, 429, "Talep sınırına ulaşıldı.")
                        return True
                    elif match.group(2) == "/swaps":
                        if set(fields) != {"id", "decision"} or not re.fullmatch(r"[a-f0-9]{24}", fields["id"]):
                            raise ValueError("Takas yanıtı geçersiz.")
                        operations.respond_mutual_swap(store, employee_id, fields["id"], fields["decision"])
                        page = _public_page(store, employee_id, token, "Takas yanıtınız kaydedildi.")
                    elif match.group(2) == "/attendance-corrections":
                        day, code, reason = _attendance_correction_data(fields)
                        import attendance_corrections

                        month = day[:7]
                        current = attendance_corrections.evidence(handler, store, month, day, employee_id)
                        store.connection.commit()
                        store.actor = f"Personel portalı #{employee_id}"
                        attendance_corrections.create(handler, store, {
                            "month": month, "date": day, "employee_id": employee_id, "code": code,
                            "reason": reason, "fingerprint": current["fingerprint"],
                        })
                        _notify(store, _recipients(store, "portal.review"), "request",
                                "Yeni puantaj düzeltmesi yönetici incelemesini bekliyor.")
                        page = _public_page(store, employee_id, token, "Puantaj düzeltme talebiniz yöneticiye iletildi.")
                    else:
                        data = _request_data(fields)
                        store.connection.execute(
                            "INSERT INTO portal_requests(employee_id,kind,start_date,end_date,details,created_at) VALUES(?,?,?,?,?,?)",
                            (employee_id, *data, _now()))
                        _notify(store, _recipients(store, "portal.review"), "request", "Yeni personel talebi incelenmeyi bekliyor.")
                        page = _public_page(store, employee_id, token, "Talebiniz gönderildi.")
                except (ValueError, UnicodeError) as exc:
                    page = _public_page(store, employee_id, token, str(exc))
            else:
                page = _public_page(store, employee_id, token)
            _public_send(handler, 200 if page else 404, page or "Bağlantı kullanılamıyor.")
    return True


def handle_get(handler, path, query):
        if path == "/api/reports/check":
            user = handler.current_user
            if not user or user.get("role") == "admin" or "documents" in _perms(user):
                return False
            month = scheduling.validate_month(query.get("month", [""])[0])
            with handler._store() as store:
                attendance, entries = handler._month_attendance(store, month)
                report = handler._check_report(store, month, attendance, entries, handler._balance_summary(store, month))
            safe_types = {"no_records", "empty_days", "overtime", "holiday_work", "weekly_rest",
                          "overlap", "rest", "legal_weekly", "legal_night", "legal_yearly_overtime"}
            items = [item for item in report.get("items", []) if item.get("type") in safe_types]
            ignored = [item for item in report.get("ignored_items", []) if item.get("type") in safe_types]
            counts = {severity: sum(item.get("severity") == severity for item in items)
                      for severity in ("error", "warning", "info")}
            handler._json({"month": report["month"], "people": report["people"], "items": items,
                           "counts": counts, "ignored": len(ignored), "ignored_items": ignored, "redacted": True})
            return True
        if path not in {"/api/access/permissions", "/api/access/portal-links", "/api/access/device-sessions",
                        "/api/access/requests", "/api/access/notifications"}:
            return False
        with handler._store() as store:
            if path == "/api/access/permissions":
                handler._json({"permissions": PERMISSIONS, "sensitive": sorted(SENSITIVE),
                               "effects": PERMISSION_EFFECTS, "users": store.users()})
            elif path == "/api/access/portal-links":
                rows = store.connection.execute(
                    "SELECT t.token_hash,t.employee_id,e.name,t.expires_at,t.revoked_at "
                    "FROM portal_tokens t JOIN employees e ON e.id=t.employee_id ORDER BY t.created_at DESC LIMIT 500").fetchall()
                links = []
                for row in rows:
                    link = dict(row)
                    activity = _plan_version_status(store, link["token_hash"], link["employee_id"])
                    link["plan_versions"] = activity["versions"]
                    link["last_seen_at"] = activity["last_seen_at"]
                    links.append(link)
                handler._json({"links": links})
            elif path == "/api/access/device-sessions":
                now = _now()
                rows = store.connection.execute(
                    "SELECT s.session_id,s.employee_id,e.name,s.created_at,s.last_seen_at,s.expires_at,s.revoked_at,"
                    "CASE WHEN s.revoked_at IS NULL AND s.expires_at>? AND e.active=1 "
                    "AND t.revoked_at IS NULL AND t.expires_at>? THEN 1 ELSE 0 END AS active "
                    "FROM portal_device_sessions s JOIN employees e ON e.id=s.employee_id "
                    "JOIN portal_tokens t ON t.token_hash=s.token_hash "
                    "ORDER BY s.created_at DESC LIMIT 500",
                    (now, now),
                )
                handler._json({"sessions": [dict(row) for row in rows]})
            elif path == "/api/access/requests":
                rows = store.connection.execute(
                    "SELECT r.*,e.name FROM portal_requests r JOIN employees e ON e.id=r.employee_id "
                    "ORDER BY r.id DESC LIMIT 500").fetchall()
                handler._json({"requests": [dict(row) for row in rows]})
            else:
                recipient = (handler.current_user or {}).get("username", "")
                with store.connection:
                    store.connection.execute(
                        "UPDATE access_notifications SET delivered_at=? WHERE recipient=? AND delivered_at IS NULL",
                        (_now(), recipient))
                    rows = store.connection.execute(
                        "SELECT id,category,message,created_at,delivered_at,read_at FROM access_notifications "
                        "WHERE recipient=? ORDER BY id DESC LIMIT 200", (recipient,)).fetchall()
                handler._json({"notifications": [dict(row) for row in rows]})
        return True


def handle_write(handler, method, path):
        routes = {("PUT", "/api/access/permissions"), ("POST", "/api/access/portal-links"),
                  ("POST", "/api/access/portal-links/revoke"), ("POST", "/api/access/requests/review"),
                  ("POST", "/api/access/device-sessions/revoke"),
                  ("POST", "/api/access/notifications/read")}
        if (method, path) not in routes:
            return False
        payload = handler._read_json(8192)
        with handler._store() as store, store.connection:
            if path == "/api/access/permissions":
                if set(payload) != {"username", "permissions", "confirm_sensitive"} or not isinstance(payload["username"], str):
                    raise ValueError("Yetki bilgisi geçersiz.")
                permissions = payload["permissions"]
                if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions) \
                        or len(permissions) != len(set(permissions)) or set(permissions) - PERMISSIONS.keys() \
                        or not isinstance(payload["confirm_sensitive"], bool):
                    raise ValueError("Yetki listesi geçersiz.")
                records = store._user_records()
                record = next ( (r for r in records if r["username"].casefold() == payload["username"].strip().casefold()), None)
                if not record or record.get("role") != "operator":
                    raise ValueError("Özel yetkili kullanıcı seçin.")
                previous = set(record.get("permissions", []))
                if (set(permissions) - previous) & SENSITIVE and not payload["confirm_sensitive"]:
                    raise ValueError("Hassas yetkileri ayrıca onaylayın.")
                record["permissions"] = sorted(permissions)
                store._write_setting("users", records)
                store.record_change("access.permissions", f"{record['username']} yetkileri değiştirildi",
                                    {"before": sorted(previous), "after": record["permissions"]})
                result = {"users": store.users()}
            elif path == "/api/access/portal-links":
                if set(payload) != {"employee_id", "days"} or type(payload["employee_id"]) is not int \
                        or type(payload["days"]) is not int or not 1 <= payload["days"] <= 90:
                    raise ValueError("Personel veya süre geçersiz.")
                if not store.connection.execute("SELECT 1 FROM employees WHERE id=? AND active=1", (payload["employee_id"],)).fetchone():
                    raise ValueError("Aktif personel bulunamadı.")
                token = secrets.token_urlsafe(32)
                expiry = _now() + payload["days"] * 86400
                store.connection.execute("INSERT INTO portal_tokens VALUES (?,?,?,?,?)",
                                         (_token_hash(token), payload["employee_id"], expiry, None, _now()))
                store.record_change("access.portal.create", f"Personel bağlantısı oluşturuldu: {payload['employee_id']}")
                phone = getattr(handler.server, "phone", None)
                addresses = phone.status().get("urls", []) if phone else []
                origins = []
                for address in addresses:
                    parsed = urlsplit(address)
                    if parsed.scheme in ("http", "https") and parsed.netloc:
                        origins.append(f"{parsed.scheme}://{parsed.netloc}")
                result = {"path": "/employee-portal/" + token, "expires_at": expiry, "origins": origins}
            elif path == "/api/access/portal-links/revoke":
                if set(payload) != {"token_hash"} or not isinstance(payload["token_hash"], str) \
                        or not re.fullmatch(r"[a-f0-9]{64}", payload["token_hash"]):
                    raise ValueError("Bağlantı geçersiz.")
                changed = store.connection.execute(
                    "UPDATE portal_tokens SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL", (_now(), payload["token_hash"])).rowcount
                if not changed:
                    raise ValueError("Bağlantı bulunamadı veya iptal edilmiş.")
                store.connection.execute(
                    "UPDATE portal_device_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                    (_now(), payload["token_hash"]),
                )
                store.record_change("access.portal.revoke", "Personel bağlantısı iptal edildi")
                result = {"ok": True}
            elif path == "/api/access/device-sessions/revoke":
                if set(payload) != {"session_id"} or not isinstance(payload["session_id"], str) \
                        or not re.fullmatch(r"[a-f0-9]{24}", payload["session_id"]):
                    raise ValueError("Cihaz oturumu geçersiz.")
                changed = store.connection.execute(
                    "UPDATE portal_device_sessions SET revoked_at=? "
                    "WHERE session_id=? AND revoked_at IS NULL AND expires_at>?",
                    (_now(), payload["session_id"], _now()),
                ).rowcount
                if not changed:
                    raise ValueError("Cihaz oturumu bulunamadı veya zaten kapalı.")
                store.record_change("access.portal.device_revoke", "Personel cihaz oturumu kapatıldı")
                result = {"ok": True}
            elif path == "/api/access/requests/review":
                if set(payload) != {"id", "decision"} or type(payload["id"]) is not int or payload["decision"] not in ("accepted", "rejected"):
                    raise ValueError("İnceleme bilgisi geçersiz.")
                changed = store.connection.execute(
                    "UPDATE portal_requests SET status=?,reviewed_at=?,reviewer=? WHERE id=? AND status='pending'",
                    (payload["decision"], _now(), (handler.current_user or {}).get("username", ""), payload["id"])).rowcount
                if not changed:
                    raise ValueError("Talep bulunamadı veya daha önce incelendi.")
                store.record_change("access.portal.review", f"Personel talebi incelendi: {payload['id']}",
                                    {"decision": payload["decision"]})
                result = {"ok": True, "status": payload["decision"], "processed": False}
            else:
                if set(payload) != {"id"} or type(payload["id"]) is not int:
                    raise ValueError("Bildirim geçersiz.")
                changed = store.connection.execute(
                    "UPDATE access_notifications SET read_at=COALESCE(read_at,?) WHERE id=? AND recipient=?",
                    (_now(), payload["id"], (handler.current_user or {}).get("username", ""))).rowcount
                if not changed:
                    raise ValueError("Bildirim bulunamadı.")
                result = {"ok": True}
        handler._json(result)
        return True
