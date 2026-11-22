from __future__ import annotations
import base64
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import access
import compliance
import planning
import forms
import scheduling

SCHEMA_VERSION = 2
_TECHNICAL_ERROR_STEPS = (
    "request_received",
    "route_resolved",
    "authorization_passed",
    "operation_started",
    "operation_failed",
    "error_code_assigned",
    "diagnostic_record_prepared",
)


class Store:
    _BACKUP_ID = re.compile(
        r"^(?:automatic-\d{4}-\d{2}-\d{2}|"
        r"(?:manual|before-restore|before-update|before-repair|before-settings-import)-\d{8}T\d{6}Z-[a-f0-9]{8})\.sqlite3$"
    )
    _REQUIRED_BACKUP_TABLES = {
        "employees", "schedule_months", "attendance_months", "app_settings",
        "attendance_events",
    }
    _initialized_paths: set[str] = set()
    _initialize_lock = threading.Lock()
    _schedule_protection_lock = threading.RLock()

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=15)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=15000")
        # Schema setup takes a write lock; doing it on every request serializes
        # concurrent page loads, so run it once per database file per process.
        key = str(self.path.resolve())
        if key not in Store._initialized_paths or not self._schema_present():
            with Store._initialize_lock:
                if key not in Store._initialized_paths or not self._schema_present():
                    try:
                        self._initialize()
                    except Exception:
                        self.connection.close()
                        raise
                    Store._initialized_paths.add(key)

    def _schema_present(self) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='form_templates'"
        ).fetchone() is not None

    def close(self) -> None:
        self.connection.close()

    @property
    def backup_directory(self) -> Path:
        return self.path.parent / "backups"

    def _backup_path(self, backup_id: str) -> Path:
        if not isinstance(backup_id, str) or not self._BACKUP_ID.fullmatch(backup_id):
            raise ValueError("Yedek seçimi geçersiz.")
        directory = self.backup_directory.resolve()
        path = (directory / backup_id).resolve()
        if path.parent != directory:
            raise ValueError("Yedek seçimi geçersiz.")
        return path

    @property
    def template_directory(self) -> Path:
        return self.path.parent / "table_templates"

    @staticmethod
    def _template_backup_path(path: Path) -> Path:
        return path.with_name(path.name + ".templates")

    def _copy_templates(self, source: Path, destination: Path) -> int:
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True, exist_ok=True)
        count = 0
        if source.is_dir():
            for item in source.iterdir():
                if item.is_file() and item.suffix.lower() == ".xlsx":
                    shutil.copy2(item, destination / item.name)
                    count += 1
        return count

    def _backup_metadata(self, path: Path) -> dict[str, Any]:
        stat = path.stat()
        templates = self._template_backup_path(path)
        return {
            "id": path.name,
            "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "size_bytes": stat.st_size,
            "template_count": (
                sum(1 for item in templates.iterdir() if item.is_file() and item.suffix.lower() == ".xlsx")
                if templates.is_dir() else None
            ),
        }

    def backups(self) -> list[dict[str, Any]]:
        if not self.backup_directory.is_dir():
            return []
        items = []
        for path in self.backup_directory.iterdir():
            if path.is_file() and self._BACKUP_ID.fullmatch(path.name):
                items.append(self._backup_metadata(path))
        return sorted(items, key=lambda item: item["created_at"], reverse=True)

    def create_backup(self, reason: str = "manual") -> dict[str, Any]:
        if reason == "automatic":
            backup_id = f"automatic-{date.today().isoformat()}.sqlite3"
            destination = self._backup_path(backup_id)
            if destination.exists():
                return self._backup_metadata(destination)
        elif reason in ("manual", "before-restore", "before-update", "before-repair", "before-settings-import"):
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_id = f"{reason}-{timestamp}-{secrets.token_hex(4)}.sqlite3"
            destination = self._backup_path(backup_id)
        else:
            raise ValueError("Yedek türü geçersiz.")

        self.backup_directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.backup_directory, prefix=".backup-", suffix=".tmp"
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            target = sqlite3.connect(temporary)
            try:
                self.connection.backup(target)
                result = target.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    raise RuntimeError(f"Oluşturulan yedek doğrulanamadı: {result}")
            finally:
                target.close()
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        self._copy_templates(self.template_directory, self._template_backup_path(destination))
        self._mirror_backup(destination)
        return self._backup_metadata(destination)

    def _mirror_backup(self, path: Path) -> None:
        folder = self.backup_mirror_folder()
        if not folder:
            return
        try:
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target / path.name)
            templates = self._template_backup_path(path)
            if templates.is_dir():
                self._copy_templates(templates, target / templates.name)
        except OSError:
            # A missing OneDrive folder must never block the local backup.
            pass

    def _setting(self, name: str, default: Any) -> Any:
        row = self.connection.execute(
            "SELECT payload FROM app_settings WHERE name=?", (name,)
        ).fetchone()
        return json.loads(row["payload"]) if row else default

    def _write_setting(self, name: str, payload: Any) -> None:
        self.connection.execute(
            "INSERT INTO app_settings(name, payload) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
            (name, json.dumps(payload, ensure_ascii=False)),
        )

    def manager_chart_notes(self, month: str, report_version: str) -> dict[str, dict[str, str]]:
        return {
            row["chart_key"]: {"text": row["text"], "updated_at": row["updated_at"]}
            for row in self.connection.execute(
                "SELECT chart_key, text, updated_at FROM manager_chart_notes "
                "WHERE month=? AND report_version=? ORDER BY chart_key",
                (month, report_version),
            )
        }

    def save_manager_chart_note(
        self, month: str, report_version: str, chart_key: str, text: str,
    ) -> dict[str, str]:
        updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.connection:
            self.connection.execute(
                "INSERT INTO manager_chart_notes(month, report_version, chart_key, text, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(month, report_version, chart_key) "
                "DO UPDATE SET text=excluded.text, updated_at=excluded.updated_at",
                (month, report_version, chart_key, text, updated_at),
            )
        return {"text": text, "updated_at": updated_at}

    def backup_mirror_folder(self) -> str:
        value = self._setting("backup_mirror", {}).get("folder", "")
        return value if isinstance(value, str) else ""

    def save_backup_mirror_folder(self, folder: Any) -> dict[str, Any]:
        if not isinstance(folder, str):
            raise ValueError("Klasör yolu metin olmalıdır.")
        folder = folder.strip().strip('"')
        if folder:
            path = Path(folder)
            if not path.is_absolute():
                raise ValueError("Bulut yedek klasörü tam yol olmalıdır (ör. C:\\Users\\...\\OneDrive\\Yedek).")
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ValueError(f"Klasör oluşturulamadı: {exc}") from exc
        with self.connection:
            self._write_setting("backup_mirror", {"folder": folder})
            self.record_change(
                "settings.backup_mirror",
                f"Bulut yedek klasörü: {folder}" if folder else "Bulut yedek klasörü kapatıldı",
            )
        copied = 0
        if folder:
            for item in self.backups():
                source = self.backup_directory / item["id"]
                if not (Path(folder) / item["id"]).exists():
                    self._mirror_backup(source)
                    copied += 1
        return {"folder": folder, "copied": copied}

    def default_mirror_folder(self) -> str:
        root = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer") or ""
        return str(Path(root) / "VardiyaPuantaj Yedekleri") if root else ""

    def local_mirror_folder(self) -> str:
        """Suggest a backup copy folder that OneDrive does not synchronise."""
        from health import cloud_sync_root

        home = os.environ.get("USERPROFILE") or str(Path.home())
        candidate = Path(home) / "VardiyaPuantaj Yedekleri"
        if cloud_sync_root(candidate):
            candidate = Path(Path(home).anchor or "C:\\") / "VardiyaPuantaj Yedekleri"
        return str(candidate)

    def _user_records(self) -> list[dict[str, Any]]:
        value = self._setting("users", [])
        return [item for item in value if isinstance(item, dict) and item.get("username")] if isinstance(value, list) else []

    def users(self) -> list[dict[str, str]]:
        return [{"username": item["username"], "role": item.get("role", "viewer"),
                 "permissions": item.get("permissions", []) if item.get("role") == "operator" else []}
                for item in self._user_records()]

    def users_enabled(self) -> bool:
        return bool(self._user_records())

    def authenticate(self, username: Any, password: Any) -> dict[str, str] | None:
        if not isinstance(username, str) or not isinstance(password, str):
            return None
        wanted = username.strip().casefold()
        for item in self._user_records():
            if item["username"].casefold() == wanted and access.verify_password(password, item):
                return {"username": item["username"], "role": item.get("role", "viewer"),
                        "permissions": item.get("permissions", []) if item.get("role") == "operator" else []}
        return None

    def save_user(self, username: Any, password: Any, role: Any) -> list[dict[str, str]]:
        name = access.normalize_username(username)
        if role not in access.ROLES:
            raise ValueError("Rol yönetici veya yalnız görüntüleme olmalıdır.")
        records = self._user_records()
        existing = next((item for item in records if item["username"].casefold() == name.casefold()), None)
        if not records and role != "admin":
            raise ValueError("İlk kullanıcı yönetici olmalıdır.")
        if existing is None or password:
            secret = access.hash_password(access.validate_password(password))
        else:
            secret = {key: existing[key] for key in ("salt", "hash", "iterations")}
        if existing is not None and existing.get("role") == "admin" and role != "admin" \
                and sum(item.get("role") == "admin" for item in records) == 1:
            raise ValueError("Son yönetici hesabının rolü değiştirilemez.")
        record = {"username": existing["username"] if existing else name, "role": role} | secret
        if role == "operator":
            record["permissions"] = (existing or {}).get("permissions", []) if (existing or {}).get("role") == "operator" else []
        records = [record if item is existing else item for item in records] if existing else records + [record]
        with self.connection:
            self._write_setting("users", records)
            self.record_change("settings.users", f"Kullanıcı {'güncellendi' if existing else 'eklendi'}: {record['username']} ({access.ROLES[role]})")
        return self.users()

    def delete_user(self, username: Any) -> list[dict[str, str]]:
        records = self._user_records()
        target = next((item for item in records if isinstance(username, str) and item["username"].casefold() == username.strip().casefold()), None)
        if target is None:
            raise ValueError("Kullanıcı bulunamadı.")
        remaining = [item for item in records if item is not target]
        if remaining and not any(item.get("role") == "admin" for item in remaining):
            raise ValueError("Son yönetici hesabı silinemez; önce başka bir yönetici ekleyin.")
        with self.connection:
            self._write_setting("users", remaining)
            self.record_change("settings.users", f"Kullanıcı silindi: {target['username']}" + ("" if remaining else " (parola koruması kapandı)"))
        return self.users()

    _PROFILE_FIELDS = ("title", "phone", "hire_date", "birth_date", "notes", "payroll_code", "exit_date", "monthly_wage", "home_section", "rotation", "special_status", "preferences", "skills", "experienced_skills", "training_skills", "duty_required_fields", "fixed_pattern_exceptions", "fixed_pattern_changes", "color_tag", "photo", "documents")
    _PHOTO_LIMIT = 150_000

    @classmethod
    def _clean_photo(cls, value: Any) -> str:
        if not value:
            return ""
        match = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)", value) if isinstance(value, str) else None
        if match is None or len(value) > cls._PHOTO_LIMIT:
            raise ValueError("Fotoğraf JPEG/PNG olmalı ve 100 KB'ı aşmamalıdır.")
        try:
            from PIL import Image
            with Image.open(BytesIO(base64.b64decode(match.group(2), validate=True))) as image:
                image.verify()
        except Exception as exc:
            raise ValueError("Fotoğraf dosyası okunamadı.") from exc
        return value

    @staticmethod
    def _clean_documents(value: Any) -> list[dict[str, Any]]:
        if value in (None, ""):
            return []
        if not isinstance(value, list) or len(value) > 20:
            raise ValueError("Personel kartına en fazla 20 belge eklenebilir.")
        documents = []
        seen_ids = set()
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("Belge kaydı geçersiz.")
            name = item.get("name", "")
            expires = item.get("expires", "") or ""
            note = item.get("note", "") or ""
            document_id = item.get("id") or secrets.token_hex(12)
            supersedes = item.get("supersedes", "") or ""
            stage = item.get("renewal_stage", "") or ""
            if (not isinstance(name, str) or not isinstance(expires, str) or not isinstance(note, str)
                    or not isinstance(document_id, str) or not isinstance(supersedes, str)
                    or not isinstance(stage, str)):
                raise ValueError("Belge alanları metin olmalıdır.")
            if (not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", document_id)
                    or document_id in seen_ids
                    or supersedes and not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", supersedes)):
                raise ValueError("Belge kimliği veya sürüm bağlantısı geçersiz.")
            if stage not in ("", "requested", "received", "checked", "renewed"):
                raise ValueError("Belge yenileme aşaması geçersiz.")
            name, note = " ".join(name.split()), " ".join(note.split())
            if not name and not expires and not note:
                continue
            if not name or len(name) > 60:
                raise ValueError("Belge adı 1-60 karakter olmalıdır.")
            if len(note) > 80:
                raise ValueError("Belge açıklaması en fazla 80 karakter olabilir.")
            if expires:
                try:
                    date.fromisoformat(expires)
                except ValueError as exc:
                    raise ValueError("Belge geçerlilik tarihi YYYY-AA-GG biçiminde olmalıdır.") from exc
            seen_ids.add(document_id)
            documents.append({
                "id": document_id, "name": name, "expires": expires, "note": note,
                "renewal_stage": stage, "supersedes": supersedes,
            })
        by_id = {item["id"]: item for item in documents}
        successors = {}
        for item in documents:
            previous_id = item["supersedes"]
            if not previous_id:
                continue
            previous = by_id.get(previous_id)
            if previous is None or previous_id == item["id"]:
                raise ValueError("Yeni belge sürümü önceki belge kaydını bulmalıdır.")
            if previous["renewal_stage"] != "renewed" or not item["expires"]:
                raise ValueError("Yenilenen belge eski kayda bağlanmalı ve geçerlilik tarihi içermelidir.")
            if previous_id in successors:
                raise ValueError("Bir belge için yalnızca bir yeni sürüm bağlanabilir.")
            successors[previous_id] = item["id"]
        if any(item["renewal_stage"] == "renewed" and item["id"] not in successors for item in documents):
            raise ValueError("Yenilendi aşaması için yeni belge sürümü ekleyin.")
        return sorted(documents, key=lambda item: (item["expires"] or "9999", item["name"], item["id"]))

    def person_history(self, employee_id: int, name: str, limit: int = 30) -> list[dict[str, Any]]:
        """Change-log entries that mention the person by id (details) or by name (summary)."""
        found = []
        for row in self.connection.execute(
            "SELECT id, occurred_at, actor, action, summary, details FROM change_history "
            "WHERE details LIKE ? OR summary LIKE ? ORDER BY id DESC LIMIT 2000",
            (f"%{employee_id}%", f"%{name}%"),
        ):
            try:
                details = json.loads(row["details"])
            except ValueError:
                details = {}
            ids = details.get("employee_ids") if isinstance(details, dict) else None
            if not (
                (isinstance(details, dict) and details.get("employee_id") == employee_id)
                or (isinstance(ids, list) and employee_id in ids)
                or (name and name in row["summary"])
            ):
                continue
            found.append({
                "id": int(row["id"]), "occurred_at": row["occurred_at"], "actor": row["actor"],
                "action": row["action"], "summary": row["summary"],
            })
            if len(found) >= limit:
                break
        return found

    _HANDOVER_LIMIT = 2000

    def handover_notes(self, start: str = "", end: str = "") -> list[dict[str, Any]]:
        value = self._setting("handover_notes", [])
        notes = []
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                note = dict(item)
                note.setdefault("status", "completed" if note.get("done") else "open")
                note.setdefault("claimed_by", "")
                note["done"] = note["status"] == "completed"
                notes.append(note)
        return sorted(
            (
                item for item in notes
                if (not end or item.get("date", "") <= end)
                and (
                    not start
                    or item.get("date", "") >= start
                    or item.get("status") != "completed"
                )
            ),
            key=lambda item: (item.get("date", ""), item.get("created_at", "")),
        )

    @staticmethod
    def _clean_handover(payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict):
            raise ValueError("Devir notu geçersiz.")
        day = payload.get("date", "")
        try:
            date.fromisoformat(day)
        except (TypeError, ValueError) as exc:
            raise ValueError("Devir notu tarihi YYYY-AA-GG biçiminde olmalıdır.") from exc
        clean = {"date": day}
        for field, limit in (("section", 60), ("shift", 40), ("text", 500)):
            value = payload.get(field, "") or ""
            if not isinstance(value, str) or len(value.strip()) > limit:
                raise ValueError(f"Devir notu alanı en fazla {limit} karakter olabilir.")
            clean[field] = value.strip()
        if not clean["text"]:
            raise ValueError("Devir notu boş olamaz.")
        return clean

    def add_handover_note(self, payload: Any) -> dict[str, Any]:
        note = self._clean_handover(payload) | {
            "id": secrets.token_hex(6), "author": self.actor or self.user_name(),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "open", "claimed_by": "", "done": False,
        }
        with self.connection:
            notes = self.handover_notes()
            notes.append(note)
            notes = notes[-self._HANDOVER_LIMIT:]
            self._write_setting("handover_notes", notes)
            self.record_change("handover.add", f"{note['date']} devir notu eklendi", {"date": note["date"]})
        return note

    def update_handover_note(
        self, note_id: Any, done: Any = None, delete: bool = False, status: Any = None,
    ) -> None:
        if not isinstance(note_id, str) or not note_id:
            raise ValueError("Devir notu seçimi geçersiz.")
        with self.connection:
            notes = self.handover_notes()
            target = next((item for item in notes if item.get("id") == note_id), None)
            if target is None:
                raise ValueError("Devir notu bulunamadı.")
            if delete:
                notes.remove(target)
                action = "handover.delete"
            elif status is not None:
                transitions = {
                    "open": {"read", "claimed"},
                    "read": {"claimed"},
                    "claimed": {"completed"},
                    "completed": set(),
                }
                current = target.get("status", "completed" if target.get("done") else "open")
                if not isinstance(status, str) or status not in transitions:
                    raise ValueError("Devir işi durumu geçersiz.")
                if status != current and status not in transitions.get(current, set()):
                    raise ValueError("Devir işi okunabilir, üstlenilebilir ve yalnız üstlenildikten sonra tamamlanabilir.")
                target["status"] = status
                target["done"] = status == "completed"
                if status == "read":
                    target["read_by"] = self.actor or self.user_name()
                    target["read_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                target["claimed_by"] = (
                    (self.actor or self.user_name()) if status == "claimed"
                    else target.get("claimed_by", "") if status == "completed" else ""
                )
                action = f"handover.{status}"
            else:
                if not isinstance(done, bool):
                    raise ValueError("Devir notu durumu geçersiz.")
                target["done"] = done
                target["status"] = "completed" if done else "open"
                target["claimed_by"] = ""
                if not done:
                    target.pop("read_by", None)
                    target.pop("read_at", None)
                action = "handover.done"
            self._write_setting("handover_notes", notes)
            self.record_change(action, f"{target['date']} devir notu güncellendi", {"date": target["date"]})

    def person_tags(self) -> dict[str, dict[str, str]]:
        return {
            employee_id: {"color": profile.get("color_tag", ""), "photo": profile.get("photo", "")}
            for employee_id, profile in self.employee_profiles().items()
            if profile.get("color_tag") or profile.get("photo")
        }

    def employee_profiles(self) -> dict[str, dict[str, Any]]:
        value = self._setting("employee_profiles", {})
        return value if isinstance(value, dict) else {}

    def employee_profile(self, employee_id: int) -> dict[str, Any]:
        return {field: "" for field in self._PROFILE_FIELDS} | {
            "monthly_wage": 0, "rotation": [], "rotation_anchor": "", "rotation_resume_mode": "calendar",
            "special_status": [], "skills": [], "documents": [],
            "experienced_skills": [], "training_skills": [], "duty_required_fields": [],
            "fixed_pattern_exceptions": [], "fixed_pattern_changes": [],
            "preferences": planning.normalize_preferences({}),
            "staff_type": "shift", "fixed_pattern": list(scheduling.DEFAULT_FIXED_PATTERN),
            "fixed_start": "08:00", "fixed_end": "16:00",
            "daily_limit_hours": 0, "break_exempt": False,
        } | self.employee_profiles().get(str(employee_id), {})

    def save_employee_profile(self, employee_id: Any, profile: Any) -> dict[str, Any]:
        if not isinstance(employee_id, int) or isinstance(employee_id, bool):
            raise ValueError("Personel seçimi geçersiz.")
        person = self.connection.execute(
            "SELECT name FROM employees WHERE id=?", (employee_id,)
        ).fetchone()
        if person is None:
            raise ValueError("Personel bulunamadı.")
        if not isinstance(profile, dict):
            raise ValueError("Personel kartı geçersiz.")
        # Fields missing from the request keep their saved values (partial updates).
        profile = self.employee_profiles().get(str(employee_id), {}) | profile
        clean: dict[str, Any] = {}
        for field in ("title", "phone", "notes", "payroll_code", "home_section"):
            value = profile.get(field, "")
            if not isinstance(value, str):
                raise ValueError("Personel kartı alanları metin olmalıdır.")
            limit = 500 if field == "notes" else 80
            if len(value.strip()) > limit:
                raise ValueError(f"Alan en fazla {limit} karakter olabilir.")
            clean[field] = value.strip()
        for field in ("hire_date", "birth_date", "exit_date"):
            value = profile.get(field, "") or ""
            if value:
                try:
                    date.fromisoformat(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError("Tarihler YYYY-AA-GG biçiminde olmalıdır.") from exc
            clean[field] = value
        wage = profile.get("monthly_wage", 0) or 0
        try:
            wage = round(float(wage), 2)
        except (TypeError, ValueError) as exc:
            raise ValueError("Aylık ücret sayı olmalıdır.") from exc
        if not math.isfinite(wage) or not 0 <= wage <= 10_000_000:
            raise ValueError("Aylık ücret 0 ile 10.000.000 arasında olmalıdır.")
        clean["monthly_wage"] = wage
        if clean["hire_date"] and clean["exit_date"] and clean["exit_date"] < clean["hire_date"]:
            raise ValueError("Çıkış tarihi işe giriş tarihinden önce olamaz.")
        rotation = profile.get("rotation", [])
        if not isinstance(rotation, list) or len(rotation) > 42:
            raise ValueError("Vardiya döngüsü en fazla 42 adım olabilir.")
        clean["rotation"] = [str(item)[:40] for item in rotation if isinstance(item, str)]
        anchor = profile.get("rotation_anchor", "")
        if anchor:
            try:
                if date.fromisoformat(anchor).isoformat() != anchor:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ValueError("Döngü başlangıcı YYYY-AA-GG biçiminde olmalıdır.") from exc
        elif anchor != "":
            raise ValueError("Döngü başlangıcı geçersiz.")
        clean["rotation_anchor"] = anchor
        resume_mode = profile.get("rotation_resume_mode", "calendar") or "calendar"
        if resume_mode not in ("calendar", "continue"):
            raise ValueError("İzin istisnası sonrası döngü takvimden veya kaldığı adımdan sürmelidir.")
        clean["rotation_resume_mode"] = resume_mode
        clean["special_status"] = compliance.normalize_statuses(profile.get("special_status", []))
        clean["preferences"] = planning.normalize_preferences(profile.get("preferences"))
        clean["skills"] = planning.normalize_skills(profile.get("skills"))
        clean["experienced_skills"] = planning.normalize_skills(profile.get("experienced_skills"))
        clean["training_skills"] = planning.normalize_skills(profile.get("training_skills"))
        required_fields = profile.get("duty_required_fields", [])
        allowed_required_fields = {
            "home_section", "payroll_code", "skills", "experienced_skills", "training_skills", "documents",
        }
        if (not isinstance(required_fields, list)
                or any(not isinstance(field, str) or field not in allowed_required_fields for field in required_fields)):
            raise ValueError("Görev için zorunlu kart alanları geçersiz.")
        clean["duty_required_fields"] = sorted(set(required_fields))
        color = profile.get("color_tag", "") or ""
        if not isinstance(color, str) or (color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color)):
            raise ValueError("Renk etiketi #RRGGBB biçiminde olmalıdır.")
        clean["color_tag"] = color.lower()
        clean["photo"] = self._clean_photo(profile.get("photo", ""))
        clean["documents"] = self._clean_documents(profile.get("documents", []))
        previous_documents = self.employee_profiles().get(str(employee_id), {}).get("documents", [])
        previous_by_id = {
            item.get("id"): item for item in previous_documents
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        stage_order = {"": 0, "requested": 1, "received": 2, "checked": 3, "renewed": 4}
        for item in clean["documents"]:
            previous = previous_by_id.get(item["id"])
            if previous is None:
                continue
            old_stage = previous.get("renewal_stage", "")
            new_stage = item["renewal_stage"]
            if stage_order.get(new_stage, -1) < stage_order.get(old_stage, -1):
                raise ValueError("Belge yenileme aşaması geriye alınamaz.")
            if stage_order.get(new_stage, 0) > stage_order.get(old_stage, 0) + 1:
                raise ValueError("Belge yenileme aşamaları sırayla kaydedilmelidir.")
        staff_type = profile.get("staff_type", "shift") or "shift"
        if staff_type not in ("shift", "nonshift"):
            raise ValueError("Personel türü vardiyalı veya vardiya dışı olmalıdır.")
        clean["staff_type"] = staff_type
        pattern = profile.get("fixed_pattern") or list(scheduling.DEFAULT_FIXED_PATTERN)
        if (not isinstance(pattern, list) or len(pattern) != 7
                or any(code not in scheduling.FIXED_PATTERN_CODES for code in pattern)):
            raise ValueError("Sabit haftalık düzen 7 gün için X, H veya boş olmalıdır.")
        clean["fixed_pattern"] = list(pattern)
        exceptions = profile.get("fixed_pattern_exceptions", [])
        if not isinstance(exceptions, list) or len(exceptions) > 20:
            raise ValueError("En fazla 20 haftalık sabit düzen istisnası eklenebilir.")
        clean_exceptions = []
        seen_weeks = set()
        for item in exceptions:
            if not isinstance(item, dict) or set(item) != {"start_date", "pattern"}:
                raise ValueError("Haftalık sabit düzen istisnası geçersiz.")
            start_text, exception_pattern = item["start_date"], item["pattern"]
            try:
                start = date.fromisoformat(start_text)
                if start.isoformat() != start_text or start.weekday() != 0:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ValueError("İstisna haftası pazartesi başlamalıdır.") from exc
            if (start_text in seen_weeks or not isinstance(exception_pattern, list)
                    or len(exception_pattern) != 7
                    or any(code not in scheduling.FIXED_PATTERN_CODES for code in exception_pattern)):
                raise ValueError("İstisna haftası 7 günlük geçerli X, H veya boş düzene sahip olmalıdır.")
            seen_weeks.add(start_text)
            clean_exceptions.append({"start_date": start_text, "pattern": list(exception_pattern)})
        clean["fixed_pattern_exceptions"] = sorted(clean_exceptions, key=lambda item: item["start_date"])
        pattern_changes = profile.get("fixed_pattern_changes", [])
        if not isinstance(pattern_changes, list) or len(pattern_changes) > 100:
            raise ValueError("En fazla 100 tarihli sabit düzen değişikliği saklanabilir.")
        clean_changes = []
        seen_dates = set()
        for item in pattern_changes:
            if not isinstance(item, dict) or set(item) != {"effective_date", "pattern"}:
                raise ValueError("Tarihli sabit düzen değişikliği geçersiz.")
            effective_text, changed_pattern = item["effective_date"], item["pattern"]
            try:
                effective = date.fromisoformat(effective_text)
                if effective.isoformat() != effective_text:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ValueError("Sabit düzen yürürlük tarihi YYYY-AA-GG biçiminde olmalıdır.") from exc
            if (effective_text in seen_dates or not isinstance(changed_pattern, list)
                    or len(changed_pattern) != 7
                    or any(code not in scheduling.FIXED_PATTERN_CODES for code in changed_pattern)):
                raise ValueError("Sabit düzen değişikliği 7 günlük geçerli X, H veya boş düzene sahip olmalıdır.")
            seen_dates.add(effective_text)
            clean_changes.append({"effective_date": effective_text, "pattern": list(changed_pattern)})
        clean["fixed_pattern_changes"] = sorted(clean_changes, key=lambda item: item["effective_date"])
        for field, default in (("fixed_start", "08:00"), ("fixed_end", "16:00")):
            value = profile.get(field) or default
            if not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
                raise ValueError("Sabit mesai saatleri SS:DD biçiminde olmalıdır.")
            clean[field] = value
        limit = profile.get("daily_limit_hours", 0) or 0
        if (not isinstance(limit, (int, float)) or isinstance(limit, bool)
                or not math.isfinite(limit) or not 0 <= limit <= 24):
            raise ValueError("Kişiye özel günlük sınır 0-24 saat arasında olmalıdır (0 = genel kural).")
        clean["daily_limit_hours"] = round(float(limit), 2)
        clean["break_exempt"] = bool(profile.get("break_exempt", False))
        with self.connection:
            profiles = self.employee_profiles()
            profiles[str(employee_id)] = clean
            self._write_setting("employee_profiles", profiles)
            self.record_change(
                "employee.profile", f"{person['name']} personel kartı güncellendi",
                {"employee_id": employee_id},
            )
        return self.employee_profile(employee_id)

    def planning_settings(self) -> dict[str, Any]:
        try:
            leave = planning.normalize_leave_settings(self._setting("leave_request_settings", None))
        except ValueError:
            leave = dict(planning.DEFAULT_LEAVE_SETTINGS)
        try:
            skills = planning.normalize_skill_requirements(self._setting("skill_requirements", []))
        except ValueError:
            skills = []
        return {"daily_limit": leave["daily_limit"], "skill_requirements": skills}

    def save_planning_settings(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {"daily_limit", "skill_requirements"}:
            raise ValueError("Planlama ayarları geçersiz.")
        leave = planning.normalize_leave_settings({"daily_limit": value["daily_limit"]})
        skills = planning.normalize_skill_requirements(value["skill_requirements"])
        with self.connection:
            self._write_setting("leave_request_settings", leave)
            self._write_setting("skill_requirements", skills)
            self.record_change("settings.planning", "Planlama kuralları güncellendi",
                               {"daily_limit": leave["daily_limit"], "skill_rules": len(skills)})
        return self.planning_settings()

    def leave_requests(self) -> list[dict[str, Any]]:
        value = self._setting("leave_requests", [])
        return value if isinstance(value, list) else []

    def compliance_settings(self) -> dict[str, Any]:
        try:
            return compliance.normalize_settings(self._setting("compliance_settings", None))
        except ValueError:
            return dict(compliance.DEFAULT_SETTINGS)

    def save_compliance_settings(self, value: Any) -> dict[str, Any]:
        merged = {**self.compliance_settings(), **(value if isinstance(value, dict) else {})}
        clean = compliance.normalize_settings(merged if isinstance(value, dict) else value)
        with self.connection:
            self._write_setting("compliance_settings", clean)
            self.record_change("settings.compliance", "Yasal uyum ayarları güncellendi", clean)
        return clean

    def schedule_sections(self) -> list[dict[str, Any]] | None:
        value = self._setting("schedule_sections", None)
        return value if isinstance(value, list) else None

    def save_schedule_sections(self, sections: list[dict[str, Any]], capacities: dict[str, int]) -> list[dict[str, Any]]:
        """Persist normalized sections; refuse when a stored month still uses a removed row."""
        conflicts: dict[tuple[str, str], int] = {}
        for period, payload, _revision in self.connection.execute(
            "SELECT period, payload, revision FROM schedule_months"
        ):
            try:
                entries = json.loads(payload)
            except (TypeError, ValueError):
                continue
            for entry in entries if isinstance(entries, list) else []:
                section, slot = entry.get("section"), entry.get("slot")
                if not isinstance(slot, int):
                    slot = 1
                if slot > capacities.get(section, 0):
                    key = (period, str(section))
                    conflicts[key] = conflicts.get(key, 0) + 1
        if conflicts:
            detail = ", ".join(f"{period} {section} ({count} kişi)" for (period, section), count in sorted(conflicts.items())[:6])
            raise ValueError(
                "Bu değişiklik kayıtlı vardiyalarda kullanılan satırları kaldırıyor: "
                f"{detail}. Önce bu kişileri tablodan çıkarın veya başka bölüme taşıyın."
            )
        with self.connection:
            self._write_setting("schedule_sections", sections)
            self.record_change(
                "settings.schedule_sections",
                "Bölümler güncellendi: " + ", ".join(f"{item['name']} {item['rows']} satır" for item in sections),
            )
        return sections

    def custom_holidays(self) -> list[dict[str, Any]]:
        value = self._setting("custom_holidays", [])
        return value if isinstance(value, list) else []

    def save_custom_holidays(self, holidays: Any) -> list[dict[str, Any]]:
        if not isinstance(holidays, list) or len(holidays) > 400:
            raise ValueError("Tatil listesi geçersiz.")
        clean = []
        seen = set()
        for item in holidays:
            if not isinstance(item, dict):
                raise ValueError("Tatil kaydı geçersiz.")
            try:
                day = date.fromisoformat(str(item.get("date", "")))
            except ValueError as exc:
                raise ValueError("Tatil tarihi YYYY-AA-GG biçiminde olmalıdır.") from exc
            kind = item.get("kind", "public")
            if kind not in ("public", "religious", "eve", "workday"):
                raise ValueError("Tatil türü geçersiz.")
            name = " ".join(str(item.get("name", "")).split())[:80]
            if day.isoformat() in seen:
                raise ValueError(f"{day.isoformat()} tarihi iki kez girilmiş.")
            seen.add(day.isoformat())
            clean.append({"date": day.isoformat(), "kind": kind, "name": name})
        clean.sort(key=lambda item: item["date"])
        with self.connection:
            self._write_setting("custom_holidays", clean)
            self.record_change("settings.holidays", f"Tatil takvimi güncellendi ({len(clean)} gün)")
        return clean

    def shift_swaps(self) -> list[dict[str, Any]]:
        value = self._setting("shift_swaps", [])
        return value if isinstance(value, list) else []

    def log_shift_swap(self, swap: dict[str, Any]) -> None:
        swaps = self.shift_swaps()
        swaps.insert(0, swap)
        self._write_setting("shift_swaps", swaps[:500])

    def _verify_backup_file(self, path: Path) -> None:
        if not path.is_file():
            raise ValueError("Yedek dosyası bulunamadı.")
        backup = None
        try:
            backup = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {
                row[0] for row in backup.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        except sqlite3.DatabaseError as exc:
            raise ValueError("Yedek dosyası okunamıyor veya bozuk.") from exc
        finally:
            if backup is not None:
                backup.close()
        if integrity != "ok" or not self._REQUIRED_BACKUP_TABLES.issubset(tables):
            raise ValueError("Yedek doğrulaması başarısız; veriler geri yüklenmedi.")

    def verify_backup(self, backup_id: str) -> dict[str, Any]:
        path = self._backup_path(backup_id)
        self._verify_backup_file(path)
        backup = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            tables = {row[0] for row in backup.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            attachment_count = (backup.execute("SELECT count(*) FROM workflow_attachments").fetchone()[0]
                                if "workflow_attachments" in tables else None)
            ui_settings = (backup.execute("SELECT 1 FROM app_settings WHERE name='ui_settings'").fetchone()
                           is not None) if "app_settings" in tables else False
        finally:
            backup.close()
        return {**self._backup_metadata(path), "valid": True,
                "contents": {"attachment_count": attachment_count, "ui_settings": ui_settings}}

    def restore_backup(self, backup_id: str) -> dict[str, Any]:
        import schedule_protection
        with self._schedule_protection_lock:
            if self.connection.execute("SELECT 1 FROM schedule_protection LIMIT 1").fetchone():
                raise ValueError(schedule_protection.ERROR)
            return self._restore_backup(backup_id)

    def _restore_backup(self, backup_id: str) -> dict[str, Any]:
        path = self._backup_path(backup_id)
        self._verify_backup_file(path)
        safety_backup = self.create_backup("before-restore")
        if self.connection.in_transaction:
            raise RuntimeError("Veritabanı işlemi sürerken yedek geri yüklenemez.")
        source = None
        try:
            source = sqlite3.connect(path)
            source.backup(self.connection)
            self.connection.commit()
            self._initialize()
            templates = self._template_backup_path(path)
            templates_restored = templates.is_dir()
            if templates_restored:
                self._copy_templates(templates, self.template_directory)
            self.record_change(
                "backup.restore",
                "Yedek geri yüklendi",
                {"backup_id": backup_id, "templates_restored": templates_restored},
            )
            self.connection.commit()
        except Exception:
            raise
        finally:
            if source is not None:
                source.close()
        return {
            "restored": self._backup_metadata(path),
            "safety_backup": safety_backup,
        }

    def _backup_before_change(self) -> None:
        backup_id = f"automatic-{date.today().isoformat()}.sqlite3"
        is_new = not self._backup_path(backup_id).exists()
        self.create_backup("automatic")
        if is_new:
            self.prune_automatic_backups()

    def backup_settings(self) -> dict[str, int]:
        row = self.connection.execute(
            "SELECT payload FROM app_settings WHERE name='backup_settings'"
        ).fetchone()
        keep = json.loads(row["payload"]).get("keep_automatic", 0) if row else 0
        return {"keep_automatic": int(keep)}

    def save_backup_settings(self, keep_automatic: Any) -> dict[str, Any]:
        if (not isinstance(keep_automatic, int) or isinstance(keep_automatic, bool)
                or not 0 <= keep_automatic <= 3650):
            raise ValueError("Saklanacak otomatik yedek sayısı 0 ile 3650 arasında tam sayı olmalıdır.")
        if keep_automatic and keep_automatic < 7:
            raise ValueError("Güvenlik için en az 7 otomatik yedek saklanmalıdır (sınırsız için 0).")
        with self.connection:
            self.connection.execute(
                "INSERT INTO app_settings(name, payload) VALUES ('backup_settings', ?) "
                "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (json.dumps({"keep_automatic": keep_automatic}),),
            )
            self.record_change(
                "settings.backup",
                "Otomatik yedek saklama: " + (f"son {keep_automatic}" if keep_automatic else "sınırsız"),
            )
        removed = self.prune_automatic_backups()
        return {**self.backup_settings(), "removed": removed}

    def prune_automatic_backups(self) -> list[str]:
        keep = self.backup_settings()["keep_automatic"]
        if not keep or not self.backup_directory.is_dir():
            return []
        automatic = sorted(
            (path for path in self.backup_directory.iterdir()
             if path.is_file() and path.name.startswith("automatic-")
             and self._BACKUP_ID.fullmatch(path.name)),
            key=lambda path: path.name, reverse=True,
        )
        removed = []
        for path in automatic[keep:]:
            templates = self._template_backup_path(path)
            if templates.is_dir():
                shutil.rmtree(templates)
            path.unlink()
            removed.append(path.name)
        return removed

    DEFAULT_USER_NAME = "Yerel kullanıcı"
    actor: str | None = None

    def user_name(self) -> str:
        row = self.connection.execute(
            "SELECT payload FROM app_settings WHERE name='user_settings'"
        ).fetchone()
        name = json.loads(row["payload"]).get("user_name", "") if row else ""
        return name or self.DEFAULT_USER_NAME

    def save_user_name(self, name: Any) -> str:
        if not isinstance(name, str):
            raise ValueError("Kullanıcı adı metin olmalıdır.")
        name = " ".join(name.split())
        if len(name) > 60:
            raise ValueError("Kullanıcı adı en fazla 60 karakter olabilir.")
        previous = self.user_name()
        with self.connection:
            self.connection.execute(
                "INSERT INTO app_settings(name, payload) VALUES ('user_settings', ?) "
                "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (json.dumps({"user_name": name}, ensure_ascii=False),),
            )
            current = self.user_name()
            if current != previous:
                self.record_change(
                    "settings.user", f"Kullanıcı adı “{current}” olarak ayarlandı",
                    {"previous": previous},
                )
        return self.user_name()

    def record_change(
        self, action: str, summary: str, details: dict[str, Any] | None = None
    ) -> None:
        self.connection.execute(
            "INSERT INTO change_history(occurred_at, actor, action, summary, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                self.actor or self.user_name(),
                action,
                summary,
                json.dumps(details or {}, ensure_ascii=False, separators=(",", ":")),
            ),
        )

    def change_history(
        self, limit: int = 100, query: str = "", action: str = ""
    ) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("Geçmiş kayıt sayısı 1-500 arasında olmalıdır.")
        conditions, parameters = [], []
        if query:
            conditions.append("(summary LIKE ? OR actor LIKE ? OR details LIKE ?)")
            parameters += [f"%{query}%"] * 3
        if action:
            conditions.append("action LIKE ?")
            parameters.append(f"{action}%")
        where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
        return [
            {
                "id": int(row["id"]),
                "occurred_at": row["occurred_at"],
                "actor": row["actor"],
                "action": row["action"],
                "summary": row["summary"],
                "details": json.loads(row["details"]),
            }
            for row in self.connection.execute(
                "SELECT id, occurred_at, actor, action, summary, details "
                f"FROM change_history {where}ORDER BY id DESC LIMIT ?",
                (*parameters, limit),
            )
        ]

    def record_technical_error(
        self, operation_id: str, app_version: str, stage: str, error_code: str, error_type: str,
        steps: list[str] | tuple[str, ...] = (),
    ) -> None:
        if not all(isinstance(value, str) and value for value in
                   (operation_id, app_version, stage, error_code, error_type)):
            raise ValueError("Teknik hata kaydı alanları geçersiz.")
        if not isinstance(steps, (list, tuple)) or any(
            not isinstance(step, str) or step not in _TECHNICAL_ERROR_STEPS for step in steps
        ) or len(steps) != len(set(steps)) or list(steps) != sorted(
            steps, key=_TECHNICAL_ERROR_STEPS.index,
        ):
            raise ValueError("Teknik hata işlem adımları geçersiz.")
        self.connection.execute(
            "INSERT INTO technical_error_events "
            "(operation_id, occurred_at, app_version, stage, error_code, error_type, steps_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (operation_id, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             app_version, stage[:80], error_code[:32], error_type[:40],
             json.dumps(list(steps), separators=(",", ":"))),
        )
        self.connection.execute(
            "DELETE FROM technical_error_events WHERE id NOT IN "
            "(SELECT id FROM technical_error_events ORDER BY id DESC LIMIT 200)"
        )

    def technical_error_history(self, limit: int = 50) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
            raise ValueError("Teknik hata sayısı 1-200 arasında olmalıdır.")
        return [
            dict(row) | {"steps": json.loads(row["steps_json"])}
            for row in self.connection.execute(
                "SELECT operation_id, occurred_at, app_version, stage, error_code, error_type, steps_json "
                "FROM technical_error_events ORDER BY id DESC LIMIT ?", (limit,),
            )
        ]

    def __enter__(self) -> Store:
        return self

    def __exit__(self, exception_type, exception, traceback) -> None:
        self.close()

    def _initialize(self) -> None:
        if int(self.connection.execute("PRAGMA user_version").fetchone()[0]) > SCHEMA_VERSION:
            raise ValueError("Veritabanı şeması bu uygulamanın desteklediğinden daha yenidir.")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS schedule_months (
                period TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                revision TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS archive_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(kind IN ('schedule', 'attendance')),
                period TEXT NOT NULL,
                payload TEXT NOT NULL,
                revision TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS archive_revisions_period
                ON archive_revisions(kind, period, id DESC);
            CREATE TABLE IF NOT EXISTS attendance_months (
                period TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                revision TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_settings (
                name TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS manager_chart_notes (
                month TEXT NOT NULL,
                report_version TEXT NOT NULL,
                chart_key TEXT NOT NULL,
                text TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (month, report_version, chart_key)
            );
            CREATE TABLE IF NOT EXISTS attendance_events (
                id TEXT PRIMARY KEY,
                employee_id INTEGER NOT NULL REFERENCES employees(id),
                kind TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                document TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS employee_balance_openings (
                employee_id INTEGER NOT NULL REFERENCES employees(id),
                year INTEGER NOT NULL,
                annual_leave_days REAL NOT NULL DEFAULT 0,
                overtime_hours REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (employee_id, year)
            );
            CREATE TABLE IF NOT EXISTS balance_opening_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL REFERENCES employees(id),
                year INTEGER NOT NULL,
                previous_annual_leave_days REAL NOT NULL,
                new_annual_leave_days REAL NOT NULL,
                actor TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS balance_opening_history_person_year
                ON balance_opening_history(employee_id, year, id);
            CREATE TABLE IF NOT EXISTS change_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                summary TEXT NOT NULL,
                details TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS technical_error_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                app_version TEXT NOT NULL,
                stage TEXT NOT NULL,
                error_code TEXT NOT NULL,
                error_type TEXT NOT NULL,
                steps_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS change_history_occurred
                ON change_history(occurred_at);
            CREATE INDEX IF NOT EXISTS technical_error_events_occurred
                ON technical_error_events(id DESC);
            CREATE INDEX IF NOT EXISTS attendance_events_period
                ON attendance_events(start_date, end_date);
            """
        )
        error_columns = {row[1] for row in self.connection.execute(
            "PRAGMA table_info(technical_error_events)"
        )}
        if "steps_json" not in error_columns:
            self.connection.execute(
                "ALTER TABLE technical_error_events ADD COLUMN steps_json TEXT NOT NULL DEFAULT '[]'"
            )
        forms.initialize(self.connection)
        import linked_leave
        import document_workflows
        import data_continuity
        import access_services
        import personnel_assets
        import operations
        linked_leave.initialize(self.connection)
        document_workflows.initialize(self.connection)
        data_continuity.initialize(self.connection)
        access_services.initialize(self.connection)
        personnel_assets.initialize(self.connection)
        operations.initialize(self.connection)
        import schedule_protection
        schedule_protection.initialize(self.connection)
        import attendance_corrections
        attendance_corrections.initialize(self.connection)
        self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.connection.commit()

    def people(self, active_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT id, name, active FROM employees"
        if active_only:
            query += " WHERE active=1"
        query += " ORDER BY active DESC, name COLLATE NOCASE, id"
        return [
            {"id": int(row["id"]), "name": row["name"], "active": bool(row["active"])}
            for row in self.connection.execute(query)
        ]

    def add_person(self, name: str) -> dict[str, Any]:
        return self.add_people([name])[0]

    def add_people(self, names: list[str]) -> list[dict[str, Any]]:
        if not isinstance(names, list) or not names or len(names) > 100:
            raise ValueError("Bir seferde 1-100 personel adı eklenebilir.")
        clean_names = []
        seen_names: set[str] = set()
        for name in names:
            if not isinstance(name, str):
                raise ValueError("Her personel adı metin olmalıdır.")
            clean_name = " ".join(name.strip().split())
            if not clean_name or len(clean_name) > 80:
                raise ValueError("Personel adı 1-80 karakter olmalıdır.")
            normalized_name = clean_name.casefold()
            if normalized_name in seen_names:
                raise ValueError(f"Listede yinelenen personel adı: {clean_name}.")
            seen_names.add(normalized_name)
            clean_names.append(clean_name)

        self._backup_before_change()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing_names = {
                row["name"].casefold()
                for row in self.connection.execute("SELECT name FROM employees")
            }
            duplicates = [name for name in clean_names if name.casefold() in existing_names]
            if duplicates:
                raise ValueError(
                    "Zaten kayıtlı personel: " + ", ".join(duplicates[:5]) + "."
                )
            person_ids = []
            for name in clean_names:
                cursor = self.connection.execute(
                    "INSERT INTO employees(name, active) VALUES (?, 1)", (name,)
                )
                person_ids.append(int(cursor.lastrowid))
            self.record_change(
                "employee.create",
                f"{len(clean_names)} personel eklendi",
                {"names": clean_names},
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

        placeholders = ",".join("?" for _ in person_ids)
        rows = self.connection.execute(
            f"SELECT id, name, active FROM employees WHERE id IN ({placeholders}) "
            "ORDER BY id",
            person_ids,
        )
        return [
            {"id": int(row["id"]), "name": row["name"], "active": bool(row["active"])}
            for row in rows
        ]

    def close_exited_people(self, today: date | None = None) -> list[int]:
        """Çıkış tarihi geçmiş aktif personeli pasife alır (kayıtları silinmez)."""
        limit = (today or date.today()).isoformat()
        profiles = self.employee_profiles()
        closed = [
            person["id"] for person in self.people(active_only=True)
            if (profiles.get(str(person["id"]), {}).get("exit_date") or "9999") < limit
        ]
        if closed:
            with self.connection:
                self.connection.executemany("UPDATE employees SET active=0 WHERE id=?", [(item,) for item in closed])
                self.record_change("people.exit_close", f"Çıkış tarihi geçen {len(closed)} personel pasife alındı",
                                   {"people": closed})
        return closed

    def set_person_active(self, person_id: int, active: bool) -> None:
        current = self.connection.execute(
            "SELECT name, active FROM employees WHERE id=?", (person_id,)
        ).fetchone()
        if current is None:
            raise ValueError("Personel bulunamadı.")
        if bool(current["active"]) == active:
            return
        self._backup_before_change()
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE employees SET active=? WHERE id=?", (int(active), person_id)
            )
            self.record_change(
                "employee.status",
                f"{current['name']} {'etkinleştirildi' if active else 'arşivlendi'}",
                {"employee_id": person_id, "active": active},
            )
        if cursor.rowcount == 0:
            raise ValueError("Personel bulunamadı.")

    def person_record_summary(self, person_id: int) -> dict[str, int]:
        key = str(person_id)
        schedule_months = attendance_months = 0
        for row in self.connection.execute("SELECT payload FROM schedule_months"):
            if any(isinstance(entry, dict) and entry.get("employee_id") == person_id
                   for entry in json.loads(row["payload"])):
                schedule_months += 1
        for row in self.connection.execute("SELECT payload FROM attendance_months"):
            payload = json.loads(row["payload"])
            if self._attendance_payload_mentions(payload, person_id, key):
                attendance_months += 1
        events = self.connection.execute(
            "SELECT COUNT(*) FROM attendance_events WHERE employee_id=?", (person_id,)
        ).fetchone()[0]
        openings = self.connection.execute(
            "SELECT COUNT(*) FROM employee_balance_openings WHERE employee_id=?", (person_id,)
        ).fetchone()[0]
        return {"schedule_months": schedule_months, "attendance_months": attendance_months,
                "events": int(events), "openings": int(openings)}

    @staticmethod
    def _attendance_payload_mentions(payload: Any, person_id: int, key: str) -> bool:
        if not isinstance(payload, dict):
            return False
        overrides = payload.get("overrides", payload) if "deductions" in payload else payload
        if any(isinstance(values, dict) and key in values for values in overrides.values()):
            return True
        if "deductions" not in payload:
            return False
        roster = payload.get("roster") or {}
        return (key in (payload.get("deductions") or {}) or key in (payload.get("overtime") or {})
                or any(person_id in (roster.get(name) or []) for name in ("added", "removed", "order")))

    def delete_person(self, person_id: int, force: bool = False) -> dict[str, Any]:
        current = self.connection.execute(
            "SELECT name FROM employees WHERE id=?", (person_id,)
        ).fetchone()
        if current is None:
            raise ValueError("Personel bulunamadı.")
        if self.connection.execute(
            "SELECT 1 FROM linked_leave_requests WHERE employee_id=? LIMIT 1", (person_id,)
        ).fetchone() or self.connection.execute(
            "SELECT 1 FROM workflow_documents WHERE employee_id=? LIMIT 1", (person_id,)
        ).fetchone() or self.connection.execute(
            "SELECT 1 FROM employee_assets WHERE employee_id=? LIMIT 1", (person_id,)
        ).fetchone():
            raise ValueError(
                "Bu personele bağlı izin, belge veya zimmet kayıtları var. Kayıtları korumak için personeli silmek yerine pasife alın."
            )
        summary = self.person_record_summary(person_id)
        if any(summary.values()) and not force:
            raise PermissionError(
                f"{current['name']} için kayıtlı vardiya/puantaj verisi var "
                f"({summary['schedule_months']} vardiya ayı, {summary['attendance_months']} puantaj ayı, "
                f"{summary['events']} izin kaydı). Pasife alabilir ya da tüm kayıtlarıyla birlikte silebilirsiniz."
            )
        key = str(person_id)
        if force:
            touched = {row["period"] for row in self.connection.execute("SELECT period, payload FROM schedule_months")
                       if any(isinstance(entry, dict) and entry.get("employee_id") == person_id
                              for entry in json.loads(row["payload"]))}
            touched |= {row["period"] for row in self.connection.execute("SELECT period, payload FROM attendance_months")
                        if self._attendance_payload_mentions(json.loads(row["payload"]), person_id, key)}
            self.assert_periods_unlocked(touched)
        self._backup_before_change()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for row in list(self.connection.execute("SELECT period, payload FROM schedule_months")):
                entries = json.loads(row["payload"])
                kept = [entry for entry in entries
                        if not (isinstance(entry, dict) and entry.get("employee_id") == person_id)]
                if len(kept) != len(entries):
                    self.connection.execute(
                        "UPDATE schedule_months SET payload=?, revision=? WHERE period=?",
                        (json.dumps(kept, ensure_ascii=False), secrets.token_hex(16), row["period"]),
                    )
            for row in list(self.connection.execute("SELECT period, payload FROM attendance_months")):
                payload = json.loads(row["payload"])
                if not self._attendance_payload_mentions(payload, person_id, key):
                    continue
                if "deductions" not in payload:
                    payload = {"overrides": payload, "deductions": {}}
                for values in payload["overrides"].values():
                    if isinstance(values, dict):
                        values.pop(key, None)
                payload["deductions"].pop(key, None)
                if isinstance(payload.get("overtime"), dict):
                    payload["overtime"].pop(key, None)
                roster = payload.get("roster")
                if isinstance(roster, dict):
                    for name in ("added", "removed", "order"):
                        roster[name] = [item for item in roster.get(name) or [] if item != person_id]
                self.connection.execute(
                    "UPDATE attendance_months SET payload=?, revision=? WHERE period=?",
                    (json.dumps(payload, ensure_ascii=False), secrets.token_hex(16), row["period"]),
                )
            self.connection.execute("DELETE FROM attendance_events WHERE employee_id=?", (person_id,))
            self.connection.execute("DELETE FROM employee_balance_openings WHERE employee_id=?", (person_id,))
            profiles = self.employee_profiles()
            if profiles.pop(key, None) is not None:
                self._write_setting("employee_profiles", profiles)
            self.connection.execute("DELETE FROM employees WHERE id=?", (person_id,))
            self.record_change(
                "employee.delete", f"{current['name']} silindi",
                {"employee_id": person_id, "records": summary},
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {"deleted": person_id, "name": current["name"], "records": summary}

    def _read_month(self, table: str, month: str) -> dict[str, Any]:
        row = self.connection.execute(
            f"SELECT payload, revision FROM {table} WHERE period=?", (month,)
        ).fetchone()
        if row is None:
            return {"payload": [] if table == "schedule_months" else {}, "revision": ""}
        return {"payload": json.loads(row["payload"]), "revision": row["revision"]}

    def schedule_month(self, month: str) -> dict[str, Any]:
        result = self._read_month("schedule_months", month)
        return {"entries": result["payload"], "revision": result["revision"]}

    def attendance_month(self, month: str) -> dict[str, Any]:
        result = self._read_month("attendance_months", month)
        payload = result["payload"]
        if isinstance(payload, dict) and {"overrides", "deductions"}.issubset(payload):
            overrides = payload["overrides"]
            deductions = payload["deductions"]
        else:
            overrides = payload
            deductions = {}
        roster = payload.get("roster") if isinstance(payload, dict) and "deductions" in payload else None
        roster = roster if isinstance(roster, dict) else {}
        overtime = payload.get("overtime") if isinstance(payload, dict) and "deductions" in payload else None
        return {
            "overrides": overrides,
            "deductions": deductions,
            "roster": {key: [int(item) for item in roster.get(key, []) if isinstance(item, int)]
                       for key in ("added", "removed", "order")},
            "overtime": overtime if isinstance(overtime, dict) else {},
            "revision": result["revision"],
        }

    def archive_months(self) -> list[dict[str, Any]]:
        schedules = list(self.connection.execute("SELECT period, payload, revision FROM schedule_months"))
        attendance = {row["period"]: row["revision"] for row in self.connection.execute(
            "SELECT period, revision FROM attendance_months"
        )}
        locked = set(self.locked_periods())
        schedule_revisions = {row["period"]: row["revision"] for row in schedules}
        derived = set(schedule_revisions)
        for row in schedules:
            for entry in json.loads(row["payload"]):
                for day, shift in entry.get("days", {}).items():
                    if shift.get("kind") == "work" and shift.get("end", "") < shift.get("start", ""):
                        following = date.fromisoformat(day) + timedelta(days=1)
                        if following.strftime("%Y-%m") != row["period"]:
                            derived.add(following.strftime("%Y-%m"))
        months = sorted(derived | set(attendance), reverse=True)
        return [
            {"month": month, "schedule_revision": schedule_revisions.get(month, ""),
             "attendance_revision": attendance.get(month, ""), "attendance_derived": month in derived,
             "locked": month in locked}
            for month in months
        ]

    def archive_revisions(self, kind: str, month: str) -> dict[str, Any]:
        if kind not in ("schedule", "attendance"):
            raise ValueError("Arşiv türü geçersiz.")
        table = "schedule_months" if kind == "schedule" else "attendance_months"
        current = self.connection.execute(
            f"SELECT payload, revision FROM {table} WHERE period=?", (month,)
        ).fetchone()
        history = [
            {"revision": row["revision"], "recorded_at": row["recorded_at"]}
            for row in self.connection.execute(
                "SELECT revision, recorded_at FROM archive_revisions "
                "WHERE kind=? AND period=? ORDER BY id DESC",
                (kind, month),
            )
        ]
        return {
            "current_revision": current["revision"] if current else "",
            "revisions": history,
        }

    def compare_archive_revision(self, kind: str, month: str, revision: str) -> dict[str, Any]:
        if kind not in ("schedule", "attendance"):
            raise ValueError("Arşiv türü geçersiz.")
        table = "schedule_months" if kind == "schedule" else "attendance_months"
        previous = self.connection.execute(
            "SELECT payload, recorded_at FROM archive_revisions "
            "WHERE kind=? AND period=? AND revision=?",
            (kind, month, revision),
        ).fetchone()
        if previous is None:
            raise ValueError("Karşılaştırılacak eski arşiv sürümü bulunamadı.")
        current = self.connection.execute(
            f"SELECT payload, revision FROM {table} WHERE period=?", (month,)
        ).fetchone()
        return {
            "previous_revision": revision,
            "previous_recorded_at": previous["recorded_at"],
            "previous": json.loads(previous["payload"]),
            "current_revision": current["revision"] if current else "",
            "current": json.loads(current["payload"]) if current else None,
        }

    def restore_archive_revision(
        self, kind: str, month: str, revision: str, expected_revision: str
    ) -> str | None:
        if kind not in ("schedule", "attendance"):
            raise ValueError("Arşiv türü geçersiz.")
        previous = self.connection.execute(
            "SELECT payload FROM archive_revisions "
            "WHERE kind=? AND period=? AND revision=?",
            (kind, month, revision),
        ).fetchone()
        if previous is None:
            raise ValueError("Geri yüklenecek eski arşiv sürümü bulunamadı.")
        table = "schedule_months" if kind == "schedule" else "attendance_months"
        payload = json.loads(previous["payload"])
        return self._save_month(
            table, month, payload, expected_revision,
            change_action=f"{kind}.archive.restore",
            change_summary=f"{month} arşivi önceki sürüme döndürüldü",
            change_details={"month": month, "restored_revision": revision},
        )

    def locked_periods(self) -> list[str]:
        row = self.connection.execute(
            "SELECT payload FROM app_settings WHERE name='locked_periods'"
        ).fetchone()
        return sorted(json.loads(row["payload"])) if row else []

    def assert_periods_unlocked(self, periods) -> None:
        locked = set(self.locked_periods())
        blocked = sorted(set(periods) & locked)
        if blocked:
            raise ValueError(
                f"Dönem kilitli: {', '.join(blocked)}. Değişiklik için arşivden kilidi kontrollü açın."
            )

    def assert_date_range_unlocked(self, start: str, end: str) -> None:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        periods = set()
        cursor = first.replace(day=1)
        while cursor <= last:
            periods.add(cursor.strftime("%Y-%m"))
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        self.assert_periods_unlocked(periods)

    def set_period_locked(self, month: str, locked: bool, reason: str = "") -> None:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise ValueError("Arşiv ayı geçersiz.")
        if not isinstance(locked, bool):
            raise ValueError("Dönem kilit durumu geçersiz.")
        self._backup_before_change()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT payload FROM app_settings WHERE name='locked_periods'"
            ).fetchone()
            periods = set(json.loads(row["payload"])) if row else set()
            if bool(month in periods) == locked:
                self.connection.commit()
                return
            if locked:
                periods.add(month)
            else:
                periods.discard(month)
            self.connection.execute(
                "INSERT INTO app_settings(name, payload) VALUES ('locked_periods', ?) "
                "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (json.dumps(sorted(periods), separators=(",", ":")),),
            )
            self.record_change(
                "archive.period_lock",
                f"{month} dönemi {'kilitlendi' if locked else 'kilidi açıldı'}"
                + (f" — gerekçe: {reason}" if reason else ""),
                {"month": month, "locked": locked, "reason": reason},
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def matching_archive_months(
        self, kind: str, month: str = "", employee: str = "",
        section: str = "", record_type: str = "",
    ) -> list[str]:
        if kind not in ("schedule", "attendance"):
            raise ValueError("Arşiv türü geçersiz.")
        filters = [value.strip().casefold() for value in (employee, section, record_type)]
        employee_filter, section_filter, type_filter = filters
        if any(len(value) > 80 for value in (*filters, month)):
            raise ValueError("Arşiv filtresi çok uzun.")
        if (
            len(month) > 7
            or (month and not re.fullmatch(r"\d{4}(?:-\d{2})?", month))
            or (len(month) == 7 and not 1 <= int(month[5:7]) <= 12)
        ):
            raise ValueError("Arşiv ayı geçersiz.")
        people = {
            int(row["id"]): row["name"]
            for row in self.connection.execute("SELECT id, name FROM employees")
        }
        matches: set[str] = set()
        schedules = list(self.connection.execute(
            "SELECT period, payload FROM schedule_months ORDER BY period"
        ))
        attendance_payloads = {
            row["period"]: json.loads(row["payload"])
            for row in self.connection.execute(
                "SELECT period, payload FROM attendance_months"
            )
        }
        scheduled_keys: set[tuple[str, int, str]] = set()
        sections_by_person_month: dict[tuple[str, int], set[str]] = {}

        def matches_record(
            period: str, employee_id: int, section_name: str,
            record_day: str, code: str, record_kind: str,
        ) -> bool:
            if month and not period.startswith(month):
                return False
            name = people.get(employee_id, "")
            if employee_filter and employee_filter not in name.casefold():
                return False
            if section_filter and section_filter not in section_name.casefold():
                return False
            description = f"{code} {record_kind} {kind_labels.get(record_kind, '')}"
            if type_filter and type_filter not in description.casefold():
                return False
            return True

        kind_codes = {
            "weekly_off": "H", "overnight_exit": "G.Ç", "annual_leave": "S",
            "sick_leave": "R", "unpaid_leave": "Ü", "marriage_leave": "E",
            "quarantine": "K", "half_day": "Y", "absent": "G",
            "resignation": "İST.",
        }
        kind_labels = {
            "work": "Çalışma", "weekly_off": "Hafta tatili",
            "overnight_exit": "Geceden çıkış", "annual_leave": "Senelik izin",
            "sick_leave": "Rapor", "unpaid_leave": "Ücretsiz izin",
            "marriage_leave": "Evlilik izni", "quarantine": "Karantina",
            "half_day": "Yarım gün", "absent": "İşe gelmedi",
            "resignation": "İstifa", "custom": "Özel kod",
            "manual": "Elle düzeltme", "deduction": "Kesinti",
        }
        for row in schedules:
            period = row["period"]
            for entry in json.loads(row["payload"]):
                try:
                    employee_id = int(entry["employee_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                section_name = str(entry.get("section", ""))
                sections_by_person_month.setdefault((period, employee_id), set()).add(section_name)
                for record_day, shift in entry.get("days", {}).items():
                    shift_kind = str(shift.get("kind", ""))
                    code = (
                        str(shift.get("attendance_code", "X")) if shift_kind == "work"
                        else str(shift.get("custom_code", "")) if shift_kind == "custom"
                        else kind_codes.get(shift_kind, "")
                    )
                    scheduled_keys.add((period, employee_id, record_day))
                    if kind == "schedule" and matches_record(
                        period, employee_id, section_name, record_day, code, shift_kind
                    ):
                        matches.add(period)
                    elif kind == "attendance":
                        stored = attendance_payloads.get(period, {})
                        overrides = stored.get("overrides", stored) if isinstance(stored, dict) else {}
                        override_code = overrides.get(record_day, {}).get(str(employee_id))
                        if override_code is not None:
                            code = str(override_code)
                        if code and matches_record(
                            period, employee_id, section_name, record_day, code, shift_kind
                        ):
                            matches.add(period)

        if kind == "attendance":
            for period, payload in attendance_payloads.items():
                overrides = payload.get("overrides", payload) if isinstance(payload, dict) else {}
                for record_day, day_values in overrides.items():
                    if not isinstance(day_values, dict):
                        continue
                    for person_id, code in day_values.items():
                        try:
                            employee_id = int(person_id)
                        except (TypeError, ValueError):
                            continue
                        if (period, employee_id, record_day) in scheduled_keys:
                            continue
                        sections = sections_by_person_month.get((period, employee_id), {""})
                        if code and any(matches_record(
                            period, employee_id, section_name, record_day, str(code), "manual"
                        ) for section_name in sections):
                            matches.add(period)
                deductions = payload.get("deductions", {}) if isinstance(payload, dict) else {}
                for person_id, amount in deductions.items():
                    try:
                        employee_id = int(person_id)
                    except (TypeError, ValueError):
                        continue
                    sections = sections_by_person_month.get((period, employee_id), {""})
                    if amount and any(matches_record(
                        period, employee_id, section_name, "", "KESİNTİ", "deduction"
                    ) for section_name in sections):
                        matches.add(period)
        return sorted(matches, reverse=True)

    def delete_archive_month(self, kind: str, month: str, expected_revision: str) -> bool:
        table = {"schedule": "schedule_months", "attendance": "attendance_months"}[kind]
        self.assert_periods_unlocked([month])
        self._backup_before_change()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.assert_periods_unlocked([month])
            row = self.connection.execute(
                f"SELECT payload, revision FROM {table} WHERE period=?", (month,)
            ).fetchone()
            if not row or row["revision"] != expected_revision:
                self.connection.rollback()
                return False
            self.connection.execute(
                "INSERT INTO archive_revisions(kind, period, payload, revision, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (kind, month, row["payload"], row["revision"], datetime.now(timezone.utc).isoformat()),
            )
            self.connection.execute(f"DELETE FROM {table} WHERE period=?", (month,))
            self.record_change(
                f"{kind}.archive.delete",
                f"{month} {kind} kaydı silindi",
                {"month": month},
            )
            self.connection.commit()
            return True
        except Exception:
            self.connection.rollback()
            raise

    def _save_month(
        self,
        table: str,
        month: str,
        payload: Any,
        expected_revision: str,
        change_action: str | None = None,
        change_summary: str | None = None,
        change_details: dict[str, Any] | None = None,
        *,
        merge_attendance_codes: bool = False,
        attendance_restore: tuple[str, int, str] | None = None,
        prepare_payload=None,
    ) -> str | None:
        revision = secrets.token_hex(16)
        self.assert_periods_unlocked([month])
        self._backup_before_change()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.assert_periods_unlocked([month])
            row = self.connection.execute(
                f"SELECT payload, revision FROM {table} WHERE period=?", (month,)
            ).fetchone()
            current = row["revision"] if row else ""
            if current != expected_revision:
                if merge_attendance_codes:
                    import schedule_collaboration
                    payload, self.attendance_merge_conflicts = schedule_collaboration.merge_attendance(
                        self.connection, month, expected_revision, self.attendance_month(month), payload,
                    )
                if not merge_attendance_codes or self.attendance_merge_conflicts:
                    self.connection.rollback()
                    return None
                payload["overrides"] = scheduling.normalize_attendance_overrides(month, payload["overrides"], self.people())
                self.attendance_merged = True
            if attendance_restore is not None:
                import schedule_collaboration
                origin, employee_id, day = attendance_restore
                people = self.people()
                if not any(person["id"] == employee_id for person in people):
                    raise ValueError("Personel bulunamadı.")
                payload = self.attendance_month(month)
                previous = schedule_collaboration.restore_attendance_code(
                    self.connection, month, origin, payload, employee_id, day,
                )
                payload.pop("revision")
                codes = payload["overrides"].setdefault(day, {})
                if previous is None:
                    codes.pop(str(employee_id), None)
                    if not codes:
                        payload["overrides"].pop(day)
                else:
                    checked = scheduling.normalize_attendance_overrides(
                        month, {day: {str(employee_id): previous}}, people,
                    )
                    codes[str(employee_id)] = checked[day][str(employee_id)]
            if prepare_payload is not None:
                payload = prepare_payload()
            self.linked_leave_conflicts = []
            prepared = None
            if table == "schedule_months":
                import linked_leave
                import operations
                prepared = linked_leave.prepare_schedule_save(self, month, payload)
                payload = prepared["entries"]
                self.linked_leave_conflicts = prepared["conflicts"]
                operations.validate_schedule_in_transaction(self, month, payload)
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            kind = "schedule" if table == "schedule_months" else "attendance"
            if row:
                self.connection.execute(
                    "INSERT INTO archive_revisions(kind, period, payload, revision, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (kind, month, row["payload"], current, datetime.now(timezone.utc).isoformat()),
                )
            self.connection.execute(
                f"INSERT INTO {table}(period, payload, revision) VALUES (?, ?, ?) "
                "ON CONFLICT(period) DO UPDATE SET payload=excluded.payload, revision=excluded.revision",
                (month, encoded, revision),
            )
            if prepared is not None:
                linked_leave.record_applications_in_transaction(self, month, prepared["applications"])
            count = len(payload) if isinstance(payload, list) else len(payload.get("overrides", {}))
            self.record_change(
                change_action or f"{kind}.save",
                change_summary or f"{month} kaydı kaydedildi",
                change_details or {"month": month, "record_count": count},
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return revision

    def save_schedule_month(
        self, month: str, entries: list[dict[str, Any]], expected_revision: str
    ) -> str | None:
        return self._save_month("schedule_months", month, entries, expected_revision)

    def save_schedule_months(self, months: dict[str, dict[str, Any]], *,
                             merge_independent: bool = False, exact: bool = False) -> dict[str, str] | None:
        import controls
        import linked_leave
        import operations
        import schedule_collaboration
        self.linked_leave_conflicts = []
        self.schedule_merge_conflicts = []
        self.schedule_saved_entries = {}
        self.schedule_merged_months = []
        self.assert_periods_unlocked(months)
        self._backup_before_change()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.assert_periods_unlocked(months)
            resolved = {}
            merged = []
            for month, data in months.items():
                current = self.schedule_month(month)
                if current["revision"] == data["revision"]:
                    resolved[month] = data
                    continue
                if not merge_independent:
                    self.connection.rollback()
                    return None
                baseline = schedule_collaboration.archived_entries(self.connection, month, data["revision"])
                if baseline is None:
                    self.schedule_merge_conflicts.append({"month": month, "reason": "Başlangıç sürümü bulunamadı."})
                    continue
                entries, conflicts = schedule_collaboration.merge_entries(baseline, current["entries"], data["entries"])
                if conflicts:
                    self.schedule_merge_conflicts.extend({"month": month, **item} for item in conflicts)
                    continue
                try:
                    entries = scheduling.normalize_schedule_entries(month, entries, self.people())
                except ValueError as exc:
                    self.schedule_merge_conflicts.append({"month": month, "reason": str(exc)})
                    continue
                resolved[month] = {"revision": current["revision"], "entries": entries}
                merged.append(month)
            if self.schedule_merge_conflicts:
                self.connection.rollback()
                return None
            months = resolved
            if merged or exact:
                periods = {period for month in months for period in scheduling.adjacent_months(month)}
                previous = [entry for period in sorted(periods) for entry in self.schedule_month(period)["entries"]]
            revisions = {}
            for month, data in months.items():
                current = self.connection.execute(
                    "SELECT payload, revision FROM schedule_months WHERE period=?", (month,)
                ).fetchone()
                if current:
                    self.connection.execute(
                        "INSERT INTO archive_revisions(kind, period, payload, revision, recorded_at) "
                        "VALUES ('schedule', ?, ?, ?, ?)",
                        (month, current["payload"], current["revision"], datetime.now(timezone.utc).isoformat()),
                    )
                revision = secrets.token_hex(16)
                self.connection.execute(
                    "INSERT INTO schedule_months(period,payload,revision) VALUES (?,?,?) "
                    "ON CONFLICT(period) DO UPDATE SET payload=excluded.payload,revision=excluded.revision",
                    (month, json.dumps(data["entries"], ensure_ascii=False, separators=(",", ":")), revision),
                )
                revisions[month] = revision
            for month, data in months.items():
                prepared = linked_leave.prepare_schedule_save(self, month, data["entries"])
                if exact and (prepared["entries"] != data["entries"] or prepared["conflicts"]):
                    raise ValueError("Bağlı izin kuralları nedeniyle tek hücre geri alınamadı; kayıt değişmedi.")
                self.connection.execute(
                    "UPDATE schedule_months SET payload=? WHERE period=?",
                    (json.dumps(prepared["entries"], ensure_ascii=False, separators=(",", ":")), month),
                )
                linked_leave.record_applications_in_transaction(self, month, prepared["applications"])
                self.linked_leave_conflicts.extend(
                    {"month": month, **item} for item in prepared["conflicts"]
                )
            for month in months:
                operations.validate_schedule_in_transaction(self, month, self.schedule_month(month)["entries"])
            if merged or exact:
                combined = [entry for period in sorted(periods) for entry in self.schedule_month(period)["entries"]]
                controls.assert_no_new_overlaps(previous, combined, {p["id"]: p["name"] for p in self.people()})
            saved_entries = {month: self.schedule_month(month)["entries"] for month in months}
            self.record_change(
                "schedule.save",
                f"{len(months)} vardiya dönemi kaydedildi",
                {"months": sorted(months), "record_count": sum(
                    len(data["entries"]) for data in months.values()
                )},
            )
            self.connection.commit()
            self.schedule_saved_entries = saved_entries
            self.schedule_merged_months = merged
            return revisions
        except Exception:
            self.connection.rollback()
            raise

    def restore_schedule_cell(self, month: str, revision: str, expected_revision: str,
                              row_id: str, day: str) -> str | None:
        import schedule_collaboration
        self.assert_periods_unlocked([month])
        current = self.schedule_month(month)
        if current["revision"] != expected_revision:
            return None
        entries = schedule_collaboration.restore_cell_entries(
            self.connection, month, revision, current["entries"], row_id, day,
        )
        entries = scheduling.normalize_schedule_entries(month, entries, self.people())
        result = self.save_schedule_months({month: {"revision": expected_revision, "entries": entries}}, exact=True)
        return result[month] if result is not None else None

    def restore_attendance_cell(self, month: str, revision: str, expected_revision: str,
                                employee_id: int, day: str) -> str | None:
        return self._save_month(
            "attendance_months", month, None, expected_revision,
            change_action="attendance.restore_cell",
            change_summary="Seçili puantaj kodu geri alındı.",
            change_details={"date": day, "employee_id": employee_id, "source_revision": revision},
            attendance_restore=(revision, employee_id, day),
        )

    def save_attendance_month(
        self,
        month: str,
        overrides: dict[str, dict[str, str]],
        deductions: dict[str, float],
        expected_revision: str,
        roster: dict[str, list[int]] | None = None,
        overtime: dict[str, float] | None = None,
        *,
        merge_independent: bool = False,
    ) -> str | None:
        if roster is None or overtime is None:
            current = self.attendance_month(month)
            roster = current["roster"] if roster is None else roster
            overtime = current["overtime"] if overtime is None else overtime
        payload = {"overrides": overrides, "deductions": deductions,
                   "roster": roster, "overtime": overtime}
        self.attendance_merge_conflicts = []
        self.attendance_merged = False
        return self._save_month("attendance_months", month, payload, expected_revision,
                                merge_attendance_codes=merge_independent)

    def attendance_events(self, month_start: str, month_end: str) -> list[dict[str, Any]]:
        return [
            {
                "id": row["id"],
                "employee_id": int(row["employee_id"]),
                "kind": row["kind"],
                "start": row["start_date"],
                "end": row["end_date"],
                "document": row["document"],
                "notes": row["notes"],
                "created_at": row["created_at"],
            }
            for row in self.connection.execute(
                "SELECT id, employee_id, kind, start_date, end_date, document, notes, created_at "
                "FROM attendance_events WHERE start_date<=? AND end_date>=? "
                "ORDER BY start_date, employee_id, id",
                (month_end, month_start),
            )
        ]

    def add_attendance_event(
        self, employee_id: int, kind: str, start: str, end: str,
        document: str, notes: str,
    ) -> dict[str, Any]:
        if not self.connection.execute(
            "SELECT 1 FROM employees WHERE id=?", (employee_id,)
        ).fetchone():
            raise ValueError("Personel bulunamadı.")
        self.assert_date_range_unlocked(start, end)
        event_id = secrets.token_hex(16)
        created_at = date.today().isoformat()
        self._backup_before_change()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.assert_date_range_unlocked(start, end)
            self.connection.execute(
                "INSERT INTO attendance_events "
                "(id, employee_id, kind, start_date, end_date, document, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, employee_id, kind, start, end, document, notes, created_at),
            )
            employee = self.connection.execute(
                "SELECT name FROM employees WHERE id=?", (employee_id,)
            ).fetchone()
            self.record_change(
                "attendance_event.create",
                f"{employee['name']} için {kind} kaydı eklendi",
                {"employee_id": employee_id, "kind": kind, "start": start, "end": end},
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return {
            "id": event_id, "employee_id": employee_id, "kind": kind,
            "start": start, "end": end, "document": document, "notes": notes,
            "created_at": created_at,
        }

    def delete_attendance_event(self, event_id: str) -> bool:
        event = self.connection.execute(
            "SELECT employee_id, kind, start_date, end_date "
            "FROM attendance_events WHERE id=?", (event_id,)
        ).fetchone()
        if event is None:
            return False
        self.assert_date_range_unlocked(event["start_date"], event["end_date"])
        self._backup_before_change()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            event = self.connection.execute(
                "SELECT employee_id, kind, start_date, end_date "
                "FROM attendance_events WHERE id=?", (event_id,)
            ).fetchone()
            if event is None:
                self.connection.commit()
                return False
            self.assert_date_range_unlocked(event["start_date"], event["end_date"])
            cursor = self.connection.execute(
                "DELETE FROM attendance_events WHERE id=?", (event_id,)
            )
            self.record_change(
                "attendance_event.delete",
                f"{event['kind']} kaydı silindi",
                {"event_id": event_id, "employee_id": int(event["employee_id"]),
                 "start": event["start_date"], "end": event["end_date"]},
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return cursor.rowcount > 0

    def quick_entries(self) -> list[dict[str, str]]:
        row = self.connection.execute(
            "SELECT payload FROM app_settings WHERE name='quick_entries'"
        ).fetchone()
        return json.loads(row["payload"]) if row else []

    def schedule_templates(self) -> list[dict[str, Any]]:
        row = self.connection.execute(
            "SELECT payload FROM app_settings WHERE name='schedule_templates'"
        ).fetchone()
        return json.loads(row["payload"]) if row else []

    def save_schedule_templates(self, templates: list[dict[str, Any]]) -> None:
        encoded = json.dumps(templates, ensure_ascii=False, separators=(",", ":"))
        self._backup_before_change()
        with self.connection:
            self.connection.execute(
                "INSERT INTO app_settings(name, payload) VALUES ('schedule_templates', ?) "
                "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (encoded,),
            )
            self.record_change("settings.schedule_templates", "Haftalık vardiya şablonları güncellendi")

    def schedule_validation_settings(self) -> dict[str, float]:
        row = self.connection.execute(
            "SELECT payload FROM app_settings WHERE name='schedule_validation_settings'"
        ).fetchone()
        return json.loads(row["payload"]) if row else {"minimum_rest_hours": 0.0}

    def save_schedule_validation_settings(self, settings: dict[str, float]) -> None:
        self._backup_before_change()
        with self.connection:
            self.connection.execute(
                "INSERT INTO app_settings(name, payload) VALUES ('schedule_validation_settings', ?) "
                "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (json.dumps(settings, ensure_ascii=False),),
            )
            self.record_change("settings.schedule_validation", "Vardiya kontrol ayarları güncellendi")

    def overtime_settings(self, month: str | None = None) -> dict[str, Any]:
        if month is not None:
            versions = self._setting("overtime_rule_versions", {})
            effective = max((period for period in versions if period <= month), default=None)
            if effective is not None:
                return versions[effective]
        row = self.connection.execute(
            "SELECT payload FROM app_settings WHERE name='overtime_settings'"
        ).fetchone()
        settings = json.loads(row["payload"]) if row else {"weekly_limit_hours": 0.0, "week_start": 0}
        if not isinstance(settings, dict):
            settings = {"weekly_limit_hours": 0.0, "week_start": 0}
        settings.setdefault("daily_limit_hours", scheduling.DEFAULT_DAILY_LIMIT_HOURS)
        settings.setdefault("break_rules", [])
        return settings

    def save_overtime_settings(self, settings: dict[str, Any]) -> None:
        self._backup_before_change()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if self._setting("overtime_rule_versions", {}):
                raise ValueError("Sürümlü mesai kuralı etkin; yeni bir yürürlük ayı seçin.")
            self.connection.execute(
                "INSERT INTO app_settings(name, payload) VALUES ('overtime_settings', ?) "
                "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (json.dumps(settings, ensure_ascii=False),),
            )
            self.record_change("settings.overtime", "Fazla mesai kuralları güncellendi")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def balance_openings(self, year: int) -> dict[int, dict[str, float]]:
        return {
            int(row["employee_id"]): {
                "annual_leave_days": float(row["annual_leave_days"]),
                "overtime_hours": float(row["overtime_hours"]),
            }
            for row in self.connection.execute(
                "SELECT employee_id, annual_leave_days, overtime_hours "
                "FROM employee_balance_openings WHERE year=?",
                (year,),
            )
        }

    def balance_opening_history(self, employee_id: int, year: int) -> list[dict[str, Any]]:
        return [
            dict(row) for row in self.connection.execute(
                "SELECT previous_annual_leave_days,new_annual_leave_days,actor,occurred_at "
                "FROM balance_opening_history WHERE employee_id=? AND year=? ORDER BY id",
                (employee_id, year),
            )
        ]

    def save_balance_opening(
        self, employee_id: int, year: int, annual_leave_days: float, overtime_hours: float,
    ) -> dict[str, float]:
        if (not isinstance(employee_id, int) or isinstance(employee_id, bool) or employee_id <= 0
                or not isinstance(year, int) or isinstance(year, bool) or not 2000 <= year <= 2100):
            raise ValueError("Personel veya bakiye yılı geçersiz.")
        values = (annual_leave_days, overtime_hours)
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or abs(value) > 9999 for value in values):
            raise ValueError("Açılış bakiyeleri -9999 ile 9999 arasında sayısal değer olmalıdır.")
        if not self.connection.execute(
            "SELECT 1 FROM employees WHERE id=?", (employee_id,)
        ).fetchone():
            raise ValueError("Personel bulunamadı.")
        leave = round(float(annual_leave_days), 2)
        overtime = round(float(overtime_hours), 2)
        self._backup_before_change()
        with self.connection:
            previous = self.connection.execute(
                "SELECT annual_leave_days FROM employee_balance_openings WHERE employee_id=? AND year=?",
                (employee_id, year),
            ).fetchone()
            previous_leave = float(previous["annual_leave_days"]) if previous else None
            actor = self.actor or self.user_name()
            occurred_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.connection.execute(
                "INSERT INTO employee_balance_openings "
                "(employee_id, year, annual_leave_days, overtime_hours) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(employee_id, year) DO UPDATE SET "
                "annual_leave_days=excluded.annual_leave_days, overtime_hours=excluded.overtime_hours",
                (employee_id, year, leave, overtime),
            )
            if previous_leave is not None and previous_leave != leave:
                self.connection.execute(
                    "INSERT INTO balance_opening_history "
                    "(employee_id,year,previous_annual_leave_days,new_annual_leave_days,actor,occurred_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (employee_id, year, previous_leave, leave, actor, occurred_at),
                )
            person = self.connection.execute(
                "SELECT name FROM employees WHERE id=?", (employee_id,)
            ).fetchone()
            self.record_change(
                "balance.opening",
                f"{person['name']} için {year} açılış bakiyeleri güncellendi",
                {
                    "employee_id": employee_id, "year": year,
                    "previous_annual_leave_days": previous_leave,
                    "annual_leave_days": leave,
                },
            )
        return {"annual_leave_days": leave, "overtime_hours": overtime}

    def import_profiles(self) -> list[dict[str, Any]]:
        row = self.connection.execute(
            "SELECT payload FROM app_settings WHERE name='import_profiles'"
        ).fetchone()
        return json.loads(row["payload"]) if row else []

    def save_import_profiles(self, profiles: list[dict[str, Any]]) -> None:
        encoded = json.dumps(profiles, ensure_ascii=False, separators=(",", ":"))
        self._backup_before_change()
        with self.connection:
            self.connection.execute(
                "INSERT INTO app_settings(name, payload) VALUES ('import_profiles', ?) "
                "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (encoded,),
            )
            self.record_change("settings.import_profiles", "İçe aktarma profilleri güncellendi")

    def form_draft(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT payload FROM app_settings WHERE name='form_draft'"
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_form_draft(self, draft: dict[str, Any]) -> None:
        encoded = json.dumps(draft, ensure_ascii=False, separators=(",", ":"))
        self._backup_before_change()
        with self.connection:
            self.connection.execute(
                "INSERT INTO app_settings(name, payload) VALUES ('form_draft', ?) "
                "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (encoded,),
            )
            self.record_change(
                "form.draft",
                "Form taslağı kaydedildi",
                {"template_id": draft["id"], "employee_id": draft["person_id"] or None},
            )

    def workbook_settings(self) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT payload FROM app_settings WHERE name='workbook_settings'"
        ).fetchone()
        return json.loads(row["payload"]) if row else {"sheets": {}, "quick_colors": {}}

    def save_workbook_settings(self, settings: dict[str, Any]) -> None:
        previous = self.workbook_settings().get("sheets", {})
        versions = self.template_versions()
        stamp = datetime.now().isoformat(timespec="seconds")
        for key in set(previous) | set(settings.get("sheets", {})):
            if ":template:" not in key or previous.get(key) == settings["sheets"].get(key):
                continue
            if key in previous:
                history = versions.setdefault(key, [])
                history.insert(0, {"saved_at": stamp, "sheet": previous[key]})
                del history[20:]
        self._backup_before_change()
        with self.connection:
            self.connection.execute(
                "INSERT INTO app_settings(name, payload) VALUES ('workbook_settings', ?) "
                "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (json.dumps(settings, ensure_ascii=False),),
            )
            self.connection.execute(
                "INSERT INTO app_settings(name, payload) VALUES ('template_versions', ?) "
                "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (json.dumps(versions, ensure_ascii=False),),
            )
            self.record_change("settings.workbook", "Çalışma kitabı ayarları güncellendi")

    def template_versions(self) -> dict[str, list[dict[str, Any]]]:
        row = self.connection.execute(
            "SELECT payload FROM app_settings WHERE name='template_versions'"
        ).fetchone()
        return json.loads(row["payload"]) if row else {}

    def save_quick_entries(self, entries: list[dict[str, str]]) -> None:
        encoded = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
        self._backup_before_change()
        with self.connection:
            self.connection.execute(
                "INSERT INTO app_settings(name, payload) VALUES ('quick_entries', ?) "
                "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                (encoded,),
            )
            self.record_change("settings.quick_entries", "Hızlı giriş seçenekleri güncellendi")
