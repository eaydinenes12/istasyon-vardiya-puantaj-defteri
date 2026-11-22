"""Çalışma alanı araçları: ayar aktarımı, sorun raporu, örnek veri, yeni ay sihirbazı,
şube (çoklu veri dosyası) yönetimi ve yerel ağdan salt okunur telefon görünümü."""
from __future__ import annotations

import html
import hashlib
import io
import json
import platform
import re
import secrets
import socket
import sqlite3
import threading
import zipfile
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import scheduling
import ui_settings
import warning_rules

EXPORT_FORMAT = "vardiya-puantaj-ayarlar"
DATA_FILE_NAME = "vardiya_puantaj.sqlite3"
BRANCH_FILE = "branches.json"
DEFAULT_BRANCH = {"id": "ana", "name": "Ana şube"}


# ---------------------------------------------------------------- ayar aktarımı
def _settings_spec(store) -> dict[str, tuple]:
    """anahtar -> (etiket, okuyucu, kaydedici). Kaydedici normalleştirir ve yazar."""

    def save_sections(value):
        sections = scheduling.normalize_schedule_sections(value)
        previous = store.schedule_sections()
        scheduling.set_schedule_sections(sections)
        capacities = {name: scheduling.section_capacity(name) for name in scheduling.section_names()}
        try:
            store.save_schedule_sections(sections, capacities)
        except ValueError:
            scheduling.set_schedule_sections(previous)
            raise

    def save_planning(value):
        store.save_planning_settings(value)

    def save_backup(value):
        if not isinstance(value, dict):
            raise ValueError("Yedek ayarı geçersiz.")
        store.save_backup_settings(value.get("keep_automatic"))

    return {
        "ui_settings": ("Görünüm ve vardiya türleri", lambda: ui_settings.load(store),
                        lambda v: ui_settings.save(store, v)),
        "template_library": ("Şablon kitaplığı", lambda: ui_settings.load_library(store),
                             lambda v: ui_settings.save_library(store, v)),
        "ignored_warnings": ("Görmezden gelinen uyarılar", lambda: warning_rules.load(store),
                             lambda v: warning_rules.save(store, v)),
        "overtime_settings": ("Fazla mesai kuralları", store.overtime_settings,
                              lambda v: store.save_overtime_settings(scheduling.normalize_overtime_settings(v))),
        "schedule_validation_settings": (
            "Vardiya doğrulama", store.schedule_validation_settings,
            lambda v: store.save_schedule_validation_settings(scheduling.normalize_schedule_validation_settings(v))),
        "workbook_settings": ("Excel çıktı ayarları", store.workbook_settings,
                              lambda v: store.save_workbook_settings(scheduling.normalize_workbook_settings(v))),
        "quick_entries": ("Hızlı girişler", store.quick_entries,
                          lambda v: store.save_quick_entries(scheduling.normalize_quick_entries(v))),
        "schedule_sections": ("Bölümler", lambda: store.schedule_sections() or scheduling.schedule_sections(),
                              save_sections),
        "custom_holidays": ("Özel tatiller", store.custom_holidays, store.save_custom_holidays),
        "compliance_settings": ("Yasal uyum", store.compliance_settings, store.save_compliance_settings),
        "planning_settings": ("Planlama kuralları", store.planning_settings, save_planning),
        "backup_settings": ("Yedek saklama", store.backup_settings, save_backup),
    }


def export_settings(store) -> dict[str, Any]:
    from version import APP_VERSION
    settings = {}
    for key, (_label, getter, _saver) in _settings_spec(store).items():
        try:
            settings[key] = getter()
        except Exception:  # noqa: BLE001 - bozuk bir ayar dışa aktarımı engellememeli
            continue
    return {
        "format": EXPORT_FORMAT, "app_version": APP_VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"), "settings": settings,
    }


def preview_settings_import(store, payload: Any, keys: Any = None) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("format") != EXPORT_FORMAT \
            or not isinstance(payload.get("settings"), dict):
        raise ValueError("Bu dosya Vardiya Puantaj ayar dosyası değil.")
    if keys is None:
        keys = list(payload["settings"])
    if not isinstance(keys, list) or len(keys) > 50 \
            or any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys):
        raise ValueError("Ayar grubu seçimi geçersiz.")
    spec = _settings_spec(store)
    groups = []
    for key in keys:
        entry = spec.get(key)
        if entry is None or key not in payload["settings"]:
            groups.append({"key": key, "label": key[:80], "recognized": False, "changed": None})
            continue
        label, getter, _saver = entry
        try:
            current = json.dumps(getter(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            incoming = json.dumps(payload["settings"][key], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            changed = current != incoming
        except (ValueError, TypeError, KeyError):
            changed = None
        groups.append({"key": key, "label": label, "recognized": True, "changed": changed})
    return {"groups": groups, "changed": sum(item["changed"] is True for item in groups)}


def import_settings(store, payload: Any, keys: Any = None, backup_id: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("format") != EXPORT_FORMAT \
            or not isinstance(payload.get("settings"), dict):
        raise ValueError("Bu dosya Vardiya Puantaj ayar dosyası değil.")
    spec = _settings_spec(store)
    wanted = set(spec) if keys is None else set(keys if isinstance(keys, list) else [])
    incoming = {key: value for key, value in payload["settings"].items() if key in spec and key in wanted}
    if not incoming:
        raise ValueError("Dosyada içe aktarılacak ayar bulunamadı.")
    if backup_id is None:
        backup = store.create_backup("before-settings-import")
        backup_id = backup["id"]
    else:
        backup = store.verify_backup(backup_id)
        if not backup.get("valid"):
            raise ValueError("Ayar aktarımı öncesi güvenlik yedeği doğrulanamadı.")
    applied, skipped = [], []
    for key, value in incoming.items():
        label, _getter, saver = spec[key]
        try:
            saver(value)
            applied.append(label)
        except (ValueError, TypeError, KeyError) as exc:
            skipped.append({"key": key, "label": label, "reason": str(exc) or "Geçersiz değer"})
    with store.connection:
        store.record_change("settings.import", f"Ayar dosyası içe aktarıldı ({len(applied)} grup)",
                            {"applied": applied, "skipped": [item["key"] for item in skipped],
                             "backup": backup_id})
    return {"applied": applied, "skipped": skipped, "backup": backup_id}


# --------------------------------------------------------------- sorun raporu
def problem_report_files(store, data_path: Path) -> dict[str, str]:
    """Export aggregate diagnostics, never free-form settings, paths or health messages."""
    import health
    from version import APP_VERSION
    connection = store.connection
    counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
        "employees", "schedule_months", "attendance_months", "change_history", "attendance_events",
    )}
    check = health.check(store)
    issues = [{key: item[key] for key in ("code", "level", "count", "repairable")} for item in check["issues"]]
    system = {
        "app_version": APP_VERSION, "python": platform.python_version(), "os": platform.system(),
        "data_file_size": data_path.stat().st_size if data_path.exists() else 0,
    }
    def encode(value):
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)

    return {
        "README.txt": "Yerel tanı özeti. Personel, e-posta, dosya yolları ve serbest metinler dahil edilmez.\n",
        "sistem.json": encode(system),
        "saglik_kontrolu.json": encode({"status": check["status"], "issues": issues}),
        "sayilar.json": encode({"tables": counts}),
        "ayarlar.json": encode({"saved_settings_count": connection.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0]}),
    }


def problem_report_digest(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def problem_report_zip(store, data_path: Path, reviewed: str | None = None) -> bytes:
    files = problem_report_files(store, data_path)
    if reviewed is not None and reviewed != problem_report_digest(files):
        raise ValueError("Tanı özeti değişti. Kaydetmeden önce sorun raporunu yeniden açıp inceleyin.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


# ----------------------------------------------------------------- örnek veri
SAMPLE_NAMES = ["Örnek Ayşe", "Örnek Mehmet", "Örnek Zeynep", "Örnek Ali", "Örnek Elif", "Örnek Can"]
SAMPLE_SHIFTS = [("08:00", "16:00"), ("16:00", "24:00"), ("24:00", "08:00")]


def load_sample_data(store, month: str) -> dict[str, Any]:
    scheduling.validate_month(month)
    if store.people():
        raise ValueError("Örnek veri yalnızca boş bir veri dosyasına yüklenebilir.")
    people = store.add_people(SAMPLE_NAMES)
    sections = list(scheduling.section_names())
    days = scheduling.month_dates(month)[:14]
    entries = []
    for index, person in enumerate(people):
        section = sections[index % len(sections)]
        start, end = SAMPLE_SHIFTS[index % len(SAMPLE_SHIFTS)]
        cells = {}
        for offset, day in enumerate(days):
            if (offset + index) % 7 == 6:
                cells[day.isoformat()] = {"kind": "weekly_off"}
            else:
                cells[day.isoformat()] = {"kind": "work", "start": start, "end": end}
        entries.append({"row_id": f"ornek-{person['id']}", "employee_id": person["id"],
                        "section": section, "days": cells})
    normalized = scheduling.normalize_schedule_entries(month, entries, store.people())
    current = store.schedule_month(month)
    store.save_schedule_month(month, normalized, current["revision"])
    with store.connection:
        store.record_change("sample.load", "Örnek veri yüklendi", {"people": len(people), "month": month})
    return {"people": len(people), "rows": len(normalized), "month": month}


# ------------------------------------------------------------- yeni ay sihirbazı
def previous_month(month: str) -> str:
    year, number = map(int, scheduling.validate_month(month).split("-"))
    return f"{year - 1}-12" if number == 1 else f"{year}-{number - 1:02}"


def new_month(store, month: str, copy_roster: bool, fill_pattern: bool, lock_previous: bool) -> dict[str, Any]:
    month = scheduling.validate_month(month)
    if not all(isinstance(flag, bool) for flag in (copy_roster, fill_pattern, lock_previous)):
        raise ValueError("Yeni ay seçenekleri geçersiz.")
    source = previous_month(month)
    current = store.schedule_month(month)
    if (copy_roster or fill_pattern) and current["entries"]:
        raise ValueError(f"{month} için zaten vardiya girilmiş; sihirbaz mevcut planın üzerine yazmaz.")
    store.assert_periods_unlocked([month])
    result = {"month": month, "source": source, "rows": 0, "filled_days": 0, "locked": False}
    if copy_roster or fill_pattern:
        active = {person["id"] for person in store.people(active_only=True)}
        previous = [row for row in store.schedule_month(source)["entries"] if row.get("employee_id") in active]
        source_dates = scheduling.month_dates(source)
        last_week: dict[int, str] = {}
        for day in source_dates[-7:]:
            last_week[day.weekday()] = day.isoformat()
        entries, used = [], set()
        for index, row in enumerate(previous):
            key = (row["employee_id"], row["section"])
            if key in used:
                continue
            used.add(key)
            cells = {}
            if fill_pattern:
                for day in scheduling.month_dates(month):
                    value = (row.get("days") or {}).get(last_week.get(day.weekday(), ""))
                    if isinstance(value, dict) and value.get("kind") and value.get("kind") != "overnight_exit":
                        cells[day.isoformat()] = dict(value)
                result["filled_days"] += len(cells)
            entry = {"row_id": f"ay-{month}-{index}-{row['employee_id']}", "employee_id": row["employee_id"],
                     "section": row["section"], "days": cells}
            if row.get("slot") is not None:
                entry["slot"] = row["slot"]
            entries.append(entry)
        if entries:
            normalized = scheduling.normalize_schedule_entries(month, entries, store.people())
            store.save_schedule_month(month, normalized, current["revision"])
            result["rows"] = len(normalized)
    if lock_previous and source not in store.locked_periods():
        store.set_period_locked(source, True, f"{month} yeni ay sihirbazı")
        result["locked"] = True
    with store.connection:
        store.record_change("month.wizard", f"{month} için yeni ay hazırlandı", result)
    return result


# ------------------------------------------------------------------- şubeler
def _branch_root(default_file: Path) -> Path:
    return default_file.parent


def load_branches(root: Path) -> dict[str, Any]:
    path = root / BRANCH_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    branches = [item for item in data.get("branches", []) if isinstance(item, dict)
                and isinstance(item.get("id"), str) and re.fullmatch(r"[a-z0-9-]{1,40}", item["id"])
                and isinstance(item.get("name"), str)]
    if not any(item["id"] == DEFAULT_BRANCH["id"] for item in branches):
        branches.insert(0, dict(DEFAULT_BRANCH))
    active = data.get("active") if any(item["id"] == data.get("active") for item in branches) else "ana"
    return {"active": active, "branches": branches}


def branch_file(root: Path, branch_id: str) -> Path:
    if branch_id == DEFAULT_BRANCH["id"]:
        return root / DATA_FILE_NAME
    return root / "subeler" / branch_id / DATA_FILE_NAME


def resolve_data_file(default_file: Path) -> Path:
    root = _branch_root(default_file)
    target = branch_file(root, load_branches(root)["active"])
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def root_for(data_path: Path) -> Path:
    parent = data_path.parent
    if parent.parent.name == "subeler":
        return parent.parent.parent
    return parent


def active_branch(data_path: Path) -> dict[str, Any]:
    root = root_for(data_path)
    data = load_branches(root)
    running_id = data_path.parent.name if data_path.parent.parent.name == "subeler" else DEFAULT_BRANCH["id"]
    current = next((item for item in data["branches"] if item["id"] == running_id), None)
    return current or {"id": running_id, "name": running_id}


def branches_info(data_path: Path) -> dict[str, Any]:
    root = root_for(data_path)
    data = load_branches(root)
    running = active_branch(data_path)
    return {
        "running": running["id"], "active": data["active"],
        "restart_required": data["active"] != running["id"],
        "branches": [{**item, "exists": branch_file(root, item["id"]).exists()} for item in data["branches"]],
    }


def _slug(name: str) -> str:
    table = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    slug = re.sub(r"[^a-z0-9]+", "-", name.translate(table).lower()).strip("-")[:30]
    return slug or "sube"


def change_branches(data_path: Path, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("action") not in ("add", "rename", "switch", "remove"):
        raise ValueError("Şube isteği geçersiz.")
    root = root_for(data_path)
    data = load_branches(root)
    action = payload["action"]
    if action == "add":
        name = " ".join(str(payload.get("name", "")).split())
        if not 1 <= len(name) <= 60:
            raise ValueError("Şube adı 1-60 karakter olmalıdır.")
        if any(item["name"].casefold() == name.casefold() for item in data["branches"]):
            raise ValueError("Bu adla bir şube zaten var.")
        if len(data["branches"]) >= 20:
            raise ValueError("En fazla 20 şube tanımlanabilir.")
        base, number = _slug(name), 1
        branch_id = base
        while any(item["id"] == branch_id for item in data["branches"]):
            number += 1
            branch_id = f"{base[:36]}-{number}"
        data["branches"].append({"id": branch_id, "name": name})
    else:
        branch = next((item for item in data["branches"] if item["id"] == payload.get("id")), None)
        if branch is None:
            raise ValueError("Şube bulunamadı.")
        if action == "rename":
            name = " ".join(str(payload.get("name", "")).split())
            if not 1 <= len(name) <= 60:
                raise ValueError("Şube adı 1-60 karakter olmalıdır.")
            branch["name"] = name
        elif action == "switch":
            data["active"] = branch["id"]
        else:
            if branch["id"] == DEFAULT_BRANCH["id"]:
                raise ValueError("Ana şube kaldırılamaz.")
            if branch["id"] in (data["active"], active_branch(data_path)["id"]):
                raise ValueError("Açık veya seçili şube kaldırılamaz; önce başka şubeye geçin.")
            # Veri dosyası silinmez; yalnızca listeden çıkarılır.
            data["branches"] = [item for item in data["branches"] if item["id"] != branch["id"]]
    root.mkdir(parents=True, exist_ok=True)
    (root / BRANCH_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return branches_info(data_path)


# ------------------------------------------------- yerel ağ telefon görünümü
def week_dates(day: date) -> list[date]:
    start = day - timedelta(days=day.weekday())
    return [start + timedelta(days=offset) for offset in range(7)]


def _cell_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    kind = value.get("kind", "")
    if kind == "work":
        return f"{value.get('start', '')}–{value.get('end', '')}"
    if kind == "custom":
        return str(value.get("code", ""))
    return scheduling.KIND_CODES.get(kind, "")


DAY_NAMES = ("Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz")


def phone_week_html(store, day: date) -> str:
    dates = week_dates(day)
    months = sorted({item.strftime("%Y-%m") for item in dates})
    names = {person["id"]: person["name"] for person in store.people()}
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    for month in months:
        for entry in store.schedule_month(month)["entries"]:
            key = (entry.get("section", ""), entry.get("employee_id"))
            target = rows.setdefault(key, {})
            target.update(entry.get("days") or {})
    keys = {item.isoformat() for item in dates}
    order = list(scheduling.section_names())
    body = []
    for section in order + sorted({key[0] for key in rows} - set(order)):
        people = [(names.get(emp, "?"), cells) for (sec, emp), cells in rows.items() if sec == section
                  and any(k in keys for k in cells)]
        if not people:
            continue
        body.append(f'<tr class="sec"><th colspan="8">{html.escape(section)}</th></tr>')
        for name, cells in sorted(people, key=lambda item: item[0]):
            tds = "".join(f"<td>{html.escape(_cell_text(cells.get(item.isoformat())))}</td>" for item in dates)
            body.append(f"<tr><th>{html.escape(name)}</th>{tds}</tr>")
    head = "".join(f"<th>{DAY_NAMES[i]}<br><small>{item.strftime('%d.%m')}</small></th>"
                   for i, item in enumerate(dates))
    prev_day, next_day = (dates[0] - timedelta(days=7)).isoformat(), (dates[0] + timedelta(days=7)).isoformat()
    table = "".join(body) or '<tr><td colspan="8">Bu hafta için vardiya yok.</td></tr>'
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Haftalık vardiya</title>
<style>body{{font:14px system-ui,sans-serif;margin:0;background:#f4f5f7;color:#1d2330}}
header{{display:flex;justify-content:space-between;align-items:center;padding:10px 12px;background:#1f3a5f;color:#fff}}
header a{{color:#fff;text-decoration:none;font-size:20px;padding:0 10px}}
.wrap{{overflow-x:auto}}table{{border-collapse:collapse;min-width:100%;background:#fff}}
th,td{{border:1px solid #d5d9e0;padding:6px 5px;text-align:center;white-space:nowrap;font-size:13px}}
tbody th{{text-align:left;position:sticky;left:0;background:#fff}}tr.sec th{{background:#e7edf6;text-align:left}}
footer{{padding:10px 12px;color:#667;font-size:12px}}</style></head><body>
<header><a href="?t={{token}}&d={prev_day}" aria-label="Önceki hafta">‹</a>
<strong>{dates[0].strftime('%d.%m')} – {dates[-1].strftime('%d.%m.%Y')}</strong>
<a href="?t={{token}}&d={next_day}" aria-label="Sonraki hafta">›</a></header>
<div class="wrap"><table><thead><tr><th>Personel</th>{head}</tr></thead><tbody>{table}</tbody></table></div>
<footer>Salt okunur görünüm · Vardiya Puantaj</footer></body></html>"""


def local_addresses() -> list[str]:
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        addresses.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    return sorted(item for item in addresses if not item.startswith("127."))


class PhoneViewServer:
    """İsteğe bağlı, salt okunur ve belirteçle korunan yerel ağ sunucusu (varsayılan kapalı)."""

    host = "0.0.0.0"

    def __init__(self, data_path: Path, store_factory, edit_lock=None):
        self.data_path = data_path
        self.store_factory = store_factory
        self.edit_lock = edit_lock
        self.httpd: ThreadingHTTPServer | None = None
        self.token = ""

    def status(self) -> dict[str, Any]:
        if not self.httpd:
            return {"running": False, "urls": []}
        port = self.httpd.server_address[1]
        hosts = local_addresses() if self.host == "0.0.0.0" else []
        return {"running": True, "port": port,
                "urls": [f"http://{ip}:{port}/m?t={self.token}" for ip in hosts]
                or [f"http://127.0.0.1:{port}/m?t={self.token}"]}

    def start(self, port: int = 0) -> dict[str, Any]:
        if self.httpd:
            return self.status()
        owner = self
        self.token = secrets.token_urlsafe(12)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(self):
                if self._portal("GET"):
                    return
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                share_match = re.fullmatch(r"/document-share/([A-Za-z0-9_-]{43})", parsed.path)
                if share_match:
                    if parsed.query:
                        self._reply(404, "<h1>Paylaşım kullanılamıyor</h1>", share=True)
                        return
                    try:
                        import document_workflows

                        uri = owner.data_path.resolve().as_uri() + "?mode=ro"
                        connection = sqlite3.connect(uri, uri=True, timeout=3)
                        try:
                            connection.row_factory = sqlite3.Row
                            connection.execute("PRAGMA query_only=ON")
                            status, page = document_workflows.public_share_response(
                                connection, share_match.group(1),
                            )
                        finally:
                            connection.close()
                        self._reply(status, page, share=True)
                    except Exception:  # noqa: BLE001 - token capability failures stay non-diagnostic
                        self._reply(404, "<h1>Paylaşım kullanılamıyor</h1>", share=True)
                    return
                if parsed.path != "/m" or not secrets.compare_digest(query.get("t", [""])[0], owner.token):
                    self._reply(403, "<h1>Erişim reddedildi</h1>")
                    return
                try:
                    day = date.fromisoformat(query.get("d", [date.today().isoformat()])[0])
                except ValueError:
                    day = date.today()
                try:
                    with owner.store_factory() as store:
                        page = phone_week_html(store, day).replace("{token}", owner.token)
                    self._reply(200, page)
                except Exception:  # noqa: BLE001
                    self._reply(500, "<h1>Görünüm hazırlanamadı</h1>")

            def do_POST(self):
                if not self._portal("POST"):
                    self._reply(404, "<h1>Sayfa bulunamadı</h1>", share=True)

            def _portal(self, method):
                if not re.fullmatch(r"/employee-portal/[A-Za-z0-9_-]{43}(/requests)?", urlparse(self.path).path):
                    return False
                import access_services
                if owner.edit_lock is not None and owner.edit_lock.status()["read_only"]:
                    access_services._public_send(self, 423, "Kayıt alanı başka bilgisayarda kullanılıyor.")
                    return True
                try:
                    return access_services.handle_public(self, self.path, method)
                except (ValueError, sqlite3.Error):
                    access_services._public_send(self, 400, "İstek tamamlanamadı; daha sonra tekrar deneyin.")
                    return True

            def _reply(self, status, text, share=False):
                body = text.encode("utf-8") if isinstance(text, str) else bytes(text)
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                if share:
                    self.send_header(
                        "Content-Security-Policy",
                        "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
                    )
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("X-Frame-Options", "DENY")
                    self.send_header("X-Robots-Tag", "noindex, nofollow")
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer((self.host, port), Handler)
        server.data_path = owner.data_path
        server.daemon_threads = True
        self.httpd = server
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            self.token = ""
        return self.status()
