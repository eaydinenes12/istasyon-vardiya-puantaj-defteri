from __future__ import annotations

import json
import hashlib
import os
import base64
import binascii
import calendar
import mimetypes
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import zipfile
from urllib.parse import parse_qs, quote, unquote, urlparse

from PIL import Image, UnidentifiedImageError

import access
import scheduling
import compliance
import controls
import payroll
import analytics
import planning
import document_import
import forms
import reports
import workspace_tools
import linked_leave
import document_workflows
import output_tracking
import data_continuity
import access_services
import mail_automation
import operations
import schedule_protection
import attendance_corrections
import live_assistant
import usability
import version
from storage import Store


ATTENDANCE_EVENT_KINDS = {
    "annual_leave", "sick_leave", "unpaid_leave", "absence", "marriage_leave", "other",
}


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _update_powershell_script(
    current: Path,
    current_manifest: Path,
    candidate: Path,
    candidate_manifest: Path,
    previous: Path,
    previous_manifest: Path,
    status_path: Path,
    process_id: int,
    expected_current_hash: str,
    expected_current_manifest_hash: str,
    expected_candidate_hash: str,
    expected_candidate_manifest_hash: str,
) -> str:
    literal = _powershell_literal
    return f"""$ErrorActionPreference = 'Stop'
$current = {literal(str(current))}
$currentManifest = {literal(str(current_manifest))}
$candidate = {literal(str(candidate))}
$candidateManifest = {literal(str(candidate_manifest))}
$previous = {literal(str(previous))}
$previousManifest = {literal(str(previous_manifest))}
$statusPath = {literal(str(status_path))}
$processId = {int(process_id)}
$expectedCurrentHash = {literal(expected_current_hash)}
$expectedCurrentManifestHash = {literal(expected_current_manifest_hash)}
$expectedCandidateHash = {literal(expected_candidate_hash)}
$expectedCandidateManifestHash = {literal(expected_candidate_manifest_hash)}
$transactionId = [Guid]::NewGuid().ToString('N')
$stageCurrent = "$current.stage-$transactionId"
$stageCurrentManifest = "$currentManifest.stage-$transactionId"
$stageCandidate = "$current.candidate-$transactionId"
$stageCandidateManifest = "$currentManifest.candidate-$transactionId"
$stagePrevious = "$previous.stage-$transactionId"
$stagePreviousManifest = "$previousManifest.stage-$transactionId"
$removedCurrent = "$current.removed-$transactionId"
$removedCurrentManifest = "$currentManifest.removed-$transactionId"
$removedPrevious = "$previous.removed-$transactionId"
$removedPreviousManifest = "$previousManifest.removed-$transactionId"
$hadPreviousImage = $false
$hadPreviousManifest = $false
$commitStarted = $false
$launched = $false
$restored = $false

function Get-FileHashValue([string]$path) {{
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}}

function Assert-ExecutablePair([string]$image, [string]$manifestPath, [string]$imageHash, [string]$sidecarHash) {{
    if (-not (Test-Path -LiteralPath $image -PathType Leaf) -or
        -not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {{
        throw "EXE veya şema bildirimi bulunamadı."
    }}
    if ((Get-FileHashValue $image) -ne $imageHash -or
        (Get-FileHashValue $manifestPath) -ne $sidecarHash) {{
        throw "EXE ve şema bildirimi beklenen sürümle eşleşmiyor."
    }}
    $document = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($document.format -ne 'vardiya-schema-compatibility' -or
        $document.version -ne 1 -or $document.executable_sha256 -ne $imageHash) {{
        throw "Şema bildirimi EXE içeriğiyle eşleşmiyor."
    }}
}}

function Write-Failure([string]$message) {{
    $record = [pscustomobject]@{{ status = 'failed'; message = $message }}
    $record | ConvertTo-Json -Compress | Set-Content -LiteralPath $statusPath -Encoding UTF8
}}

function Show-FailureDialog([string]$message) {{
    try {{
        Add-Type -AssemblyName System.Windows.Forms
        [void][System.Windows.Forms.MessageBox]::Show(
            $message, 'Vardiya Puantaj güncelleme hatası',
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error)
    }} catch {{
        Write-Warning "Güncelleme hatası kullanıcıya gösterilemedi: $message"
    }}
}}

try {{
    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    $imageReleased = $false
    do {{
        $processAlive = $false
        if ($processId -gt 0) {{
            $processAlive = $null -ne (Get-Process -Id $processId -ErrorAction SilentlyContinue)
        }}
        if (-not $processAlive) {{
            try {{
                $probe = [IO.File]::Open($current, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
                $probe.Dispose()
                $imageReleased = $true
            }} catch {{
                $imageReleased = $false
            }}
        }}
        if (-not $processAlive -and $imageReleased) {{ break }}
        Start-Sleep -Milliseconds 500
    }} while ([DateTime]::UtcNow -lt $deadline)
    if ($processAlive -or -not $imageReleased) {{
        throw "Uygulama işlemi veya EXE dosya kilidi 120 saniye içinde kapanmadı; dosyalar değiştirilmedi."
    }}

    Assert-ExecutablePair $current $currentManifest $expectedCurrentHash $expectedCurrentManifestHash
    Assert-ExecutablePair $candidate $candidateManifest $expectedCandidateHash $expectedCandidateManifestHash
    [IO.File]::Copy($current, $stageCurrent)
    [IO.File]::Copy($currentManifest, $stageCurrentManifest)
    [IO.File]::Copy($candidate, $stageCandidate)
    [IO.File]::Copy($candidateManifest, $stageCandidateManifest)
    Assert-ExecutablePair $stageCurrent $stageCurrentManifest $expectedCurrentHash $expectedCurrentManifestHash
    Assert-ExecutablePair $stageCandidate $stageCandidateManifest $expectedCandidateHash $expectedCandidateManifestHash

    $hadPreviousImage = Test-Path -LiteralPath $previous -PathType Leaf
    $hadPreviousManifest = Test-Path -LiteralPath $previousManifest -PathType Leaf
    if ($hadPreviousImage) {{ [IO.File]::Copy($previous, $stagePrevious) }}
    if ($hadPreviousManifest) {{ [IO.File]::Copy($previousManifest, $stagePreviousManifest) }}

    $commitStarted = $true
    [IO.File]::Move($current, $removedCurrent)
    [IO.File]::Move($currentManifest, $removedCurrentManifest)
    if ($hadPreviousImage) {{ [IO.File]::Move($previous, $removedPrevious) }}
    if ($hadPreviousManifest) {{ [IO.File]::Move($previousManifest, $removedPreviousManifest) }}
    [IO.File]::Move($stageCandidate, $current)
    [IO.File]::Move($stageCandidateManifest, $currentManifest)
    Assert-ExecutablePair $current $currentManifest $expectedCandidateHash $expectedCandidateManifestHash
    [IO.File]::Move($removedCurrent, $previous)
    [IO.File]::Move($removedCurrentManifest, $previousManifest)
    Assert-ExecutablePair $previous $previousManifest $expectedCurrentHash $expectedCurrentManifestHash

    if (Test-Path -LiteralPath $statusPath) {{ Remove-Item -LiteralPath $statusPath -Force }}
    $env:VARDIYA_PUANTAJ_UPDATE_STATUS_PATH = $statusPath
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
    $newProcess = Start-Process -FilePath $current -WorkingDirectory (Split-Path -Parent $current) -PassThru -WindowStyle Normal
    if ($null -eq $newProcess -or $newProcess.Id -le 0) {{
        throw "Yeni uygulama süreci başlatılamadı."
    }}
    $launched = $true
    $startupFailure = ""
    $ready = $false
    $readyDeadline = [DateTime]::UtcNow.AddSeconds(60)
    do {{
        if (Test-Path -LiteralPath $statusPath) {{
            try {{
                $startupStatus = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($startupStatus.status -eq 'ready') {{ $ready = $true; break }}
                if ($startupStatus.status -eq 'failed') {{
                    $startupFailure = [string]$startupStatus.message
                    break
                }}
            }} catch {{}}
        }}
        $newProcess.Refresh()
        if ($newProcess.HasExited) {{ break }}
        Start-Sleep -Milliseconds 250
    }} while ([DateTime]::UtcNow -lt $readyDeadline)
    if (-not $ready) {{
        $releaseDeadline = [DateTime]::UtcNow.AddSeconds(20)
        $newImageReleased = $false
        do {{
            $newProcess.Refresh()
            $newProcessAlive = -not $newProcess.HasExited
            if (-not $newProcessAlive) {{
                try {{
                    $probe = [IO.File]::Open($current, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
                    $probe.Dispose()
                    $newImageReleased = $true
                }} catch {{
                    $newImageReleased = $false
                }}
            }}
            if (-not $newProcessAlive -and $newImageReleased) {{ break }}
            Start-Sleep -Milliseconds 250
        }} while ([DateTime]::UtcNow -lt $releaseDeadline)
        if (-not $newProcessAlive -and $newImageReleased) {{
            $launched = $false
            if ($startupFailure) {{ throw "Yeni sürüm başlatılamadı: $startupFailure Eski sürüm geri yüklendi." }}
            throw "Yeni sürüm 60 saniye içinde hazır olamadı. Eski sürüm geri yüklendi."
        }}
        throw "Yeni sürüm başlatıldı ancak hazır olamadı ve EXE hâlâ kullanımda. Güvenlik için dosyalar ve .onceki korundu; uygulamayı yeniden başlatmadan önce teknik desteğe başvurun."
    }}
    foreach ($path in @($stageCurrent, $stageCurrentManifest, $stageCandidate, $stageCandidateManifest,
        $stagePrevious, $stagePreviousManifest, $removedPrevious, $removedPreviousManifest)) {{
        if (Test-Path -LiteralPath $path) {{ Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }}
    }}
}} catch {{
    $failure = $_.Exception.Message
    if ($commitStarted -and -not $launched) {{
        try {{
            foreach ($path in @($current, $currentManifest, $previous, $previousManifest)) {{
                if (Test-Path -LiteralPath $path) {{ Remove-Item -LiteralPath $path -Force }}
            }}
            [IO.File]::Copy($stageCurrent, $current)
            [IO.File]::Copy($stageCurrentManifest, $currentManifest)
            if ($hadPreviousImage) {{ [IO.File]::Copy($stagePrevious, $previous) }}
            if ($hadPreviousManifest) {{ [IO.File]::Copy($stagePreviousManifest, $previousManifest) }}
            Assert-ExecutablePair $current $currentManifest $expectedCurrentHash $expectedCurrentManifestHash
            if ($hadPreviousImage -and (Get-FileHashValue $previous) -ne (Get-FileHashValue $stagePrevious)) {{
                throw "Önceki EXE geri yüklemesi doğrulanamadı."
            }}
            if ($hadPreviousManifest -and
                (Get-FileHashValue $previousManifest) -ne (Get-FileHashValue $stagePreviousManifest)) {{
                throw "Önceki şema bildirimi geri yüklemesi doğrulanamadı."
            }}
            if (-not $hadPreviousImage -and (Test-Path -LiteralPath $previous)) {{
                throw "Beklenmeyen önceki EXE geri yükleme sırasında oluştu."
            }}
            if (-not $hadPreviousManifest -and (Test-Path -LiteralPath $previousManifest)) {{
                throw "Beklenmeyen önceki şema bildirimi geri yükleme sırasında oluştu."
            }}
            $restored = $true
        }} catch {{
            $failure = "$failure Geri alma doğrulanamadı: $($_.Exception.Message)"
        }}
    }}
    if (-not $launched -and ($restored -or -not $commitStarted)) {{
        foreach ($path in @($stageCurrent, $stageCurrentManifest, $stageCandidate, $stageCandidateManifest,
            $stagePrevious, $stagePreviousManifest, $removedCurrent, $removedCurrentManifest,
            $removedPrevious, $removedPreviousManifest)) {{
            if (Test-Path -LiteralPath $path) {{ Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }}
        }}
    }}
    try {{ Write-Failure $failure }} catch {{ Write-Error "Güncelleme başarısız: $failure" }}
    Show-FailureDialog $failure
    exit 1
}}
"""


def _employee_name_key(value: str) -> str:
    return value.strip().replace("I", "ı").replace("İ", "i").casefold()


_SEARCH_FOLD = str.maketrans("çğıöşü", "cgiosu")


def _search_key(value: str) -> str:
    return " ".join(_employee_name_key(value).replace("i̇", "i").translate(_SEARCH_FOLD).split())


def normalize_attendance_event(payload) -> dict:
    fields = {"employee_id", "kind", "start", "end", "document", "notes"}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError("İzin/devamsızlık kayıt bilgisi geçersiz.")
    employee_id = payload["employee_id"]
    if not isinstance(employee_id, int) or isinstance(employee_id, bool) or employee_id < 1:
        raise ValueError("Personel seçimi geçersiz.")
    kind = payload["kind"]
    if not isinstance(kind, str) or kind not in ATTENDANCE_EVENT_KINDS:
        raise ValueError("İzin/devamsızlık türü geçersiz.")
    parsed_dates = []
    for field in ("start", "end"):
        value = payload[field]
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError("Başlangıç ve bitiş tarihlerini seçin.")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Tarih YYYY-AA-GG biçiminde olmalıdır.") from exc
        if parsed.isoformat() != value:
            raise ValueError("Tarih YYYY-AA-GG biçiminde olmalıdır.")
        parsed_dates.append(parsed)
    if parsed_dates[1] < parsed_dates[0]:
        raise ValueError("Bitiş tarihi başlangıç tarihinden önce olamaz.")
    result = {"employee_id": employee_id, "kind": kind, "start": payload["start"], "end": payload["end"]}
    for field, maximum, label in (("document", 160, "Belge adı"), ("notes", 1000, "Açıklama")):
        value = payload[field]
        if not isinstance(value, str) or len(value.strip()) > maximum:
            raise ValueError(f"{label} en fazla {maximum} karakter olabilir.")
        result[field] = value.strip()
    return result


def normalize_import_profiles(profiles) -> list[dict]:
    if not isinstance(profiles, list) or len(profiles) > 50:
        raise ValueError("En fazla 50 içe aktarma profili saklanabilir.")
    mapping_fields = {
        "mode", "start_row", "person_col", "section_col", "default_section", "date_col",
        "status_col", "start_col", "end_col", "header_row", "first_day_col",
    }
    numeric_limits = {
        "start_row": 5000, "person_col": 80, "section_col": 80, "date_col": 80,
        "status_col": 80, "start_col": 80, "end_col": 80, "header_row": 5000,
        "first_day_col": 80,
    }
    names = set()
    ids = set()
    normalized = []
    for profile in profiles:
        profile_fields = {"id", "name", "mapping"}
        if not isinstance(profile, dict) or set(profile) not in (
            profile_fields, profile_fields | {"header_signature"},
        ):
            raise ValueError("İçe aktarma profili geçersiz.")
        name = profile["name"].strip() if isinstance(profile["name"], str) else ""
        key = name.casefold()
        if not name or len(name) > 80 or key in names:
            raise ValueError("Profil adları boş olamaz ve yinelenemez.")
        names.add(key)
        mapping = profile["mapping"]
        if not isinstance(mapping, dict) or set(mapping) != mapping_fields:
            raise ValueError("Profil sütun eşleştirmesi geçersiz.")
        if mapping["mode"] not in ("rows", "matrix"):
            raise ValueError("Profil tablo düzeni geçersiz.")
        if mapping["default_section"] not in ("",) + tuple(scheduling.section_names()):
            raise ValueError("Profil varsayılan bölümü geçersiz.")
        clean_mapping = dict(mapping)
        for field, maximum in numeric_limits.items():
            value = mapping[field]
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > maximum
            ):
                raise ValueError("Profil satır veya sütun numarası geçersiz.")
        profile_id = profile["id"]
        if not isinstance(profile_id, str) or len(profile_id) > 64:
            raise ValueError("Profil kimliği geçersiz.")
        profile_id = profile_id or secrets.token_hex(12)
        if profile_id in ids:
            raise ValueError("Profil kimliği yinelenemez.")
        ids.add(profile_id)
        header_signature = document_import.validate_import_header_signature(
            profile.get("header_signature")
        )
        normalized.append({
            "id": profile_id,
            "name": name,
            "mapping": clean_mapping,
            "header_signature": header_signature,
        })
    return normalized


class ScheduleHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, data_path: Path, web_path: Path):
        super().__init__(address, ScheduleRequestHandler)
        self.data_path = data_path
        self.web_path = web_path
        self.easy_mode = False
        self.save_excel = None
        self.save_form = None
        self.app_executable: Path | None = None
        self.request_exit = None
        self.internal_token = secrets.token_urlsafe(24)
        self.sessions: dict[str, dict] = {}
        self.users_cache: tuple[float, bool] | None = None
        self.schedule_normalized_cache = {}
        self.schedule_normalized_cache_lock = threading.Lock()
        self.lock = access.EditLock(data_path)
        self.open_file = getattr(os, "startfile", None)
        self.phone = None
        self.mobile_diagnostics = {
            "service": {"status": "info", "message": "Mobil HTTPS tanılama başlatılmadı."},
            "network": {"status": "info", "message": "Henüz denetlenmedi."},
            "address": {"status": "info", "message": "Henüz denetlenmedi."},
            "certificate": {"status": "info", "message": "Henüz denetlenmedi."},
            "addresses": [],
        }
        self.mail_scheduler_stop = threading.Event()
        self.mail_scheduler_thread = None

    def serve_forever(self, poll_interval: float = 0.5):
        self.mail_scheduler_stop.clear()
        self.mail_scheduler_thread = threading.Thread(
            target=mail_automation.run_scheduler,
            args=(self, self.mail_scheduler_stop),
            name="vardiya-mail-scheduler",
            daemon=True,
        )
        self.mail_scheduler_thread.start()
        try:
            super().serve_forever(poll_interval=poll_interval)
        finally:
            self.mail_scheduler_stop.set()
            self.mail_scheduler_thread.join()


class ScheduleRequestHandler(BaseHTTPRequestHandler):
    server: ScheduleHTTPServer

    def log_message(self, format_string: str, *args) -> None:
        if self.path.startswith("/employee-portal/"):
            return
        print(f"[VardiyaPuantaj] {self.address_string()} {format_string % args}")

    def _send(self, body: bytes, content_type: str, status: int = 200, filename: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value, status: int = 200) -> None:
        self._send(
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def _read_json(self, maximum: int = 1_000_000):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > maximum:
            raise ValueError("İstek verisi çok büyük.")
        try:
            value = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("İstek JSON biçiminde olmalıdır.") from exc
        if not isinstance(value, dict):
            raise ValueError("İstek gövdesi JSON nesnesi olmalıdır.")
        return value

    def _store(self) -> Store:
        store = Store(self.server.data_path)
        operations.process_due_lifecycle(store)
        user = getattr(self, "current_user", None)
        if user:
            store.actor = user["username"]
        scheduling.set_custom_holidays(store.custom_holidays())
        scheduling.set_schedule_sections(store.schedule_sections())
        return store

    def _generate_form(self, payload):
        required = {"id", "person_id", "start", "end", "category"}
        allowed = required | {"section", "description"}
        if not required.issubset(payload) or not set(payload).issubset(allowed):
            raise ValueError("Form hazırlama bilgisi geçersiz.")
        if not isinstance(payload["person_id"], int) or isinstance(payload["person_id"], bool):
            raise ValueError("Personel seçimi geçersiz.")
        with self._store() as store:
            template = forms.get_template(store.connection, payload["id"])
            person = next((p for p in store.people() if p["id"] == payload["person_id"]), None)
            if not person:
                raise ValueError("Personel bulunamadı.")
            content = forms.generate(
                template, person["name"], payload["start"], payload["end"],
                payload["category"], payload.get("section", ""),
                payload.get("description", ""),
            )
            return content, template["extension"]

    def _record_form_generation(self, payload, file_name: str) -> None:
        if not isinstance(file_name, str) or not file_name.strip() or len(file_name) > 255:
            raise ValueError("Form dosya adı geçersiz.")
        with self._store() as store:
            template = forms.get_template(store.connection, payload["id"])
            person = next(
                (person for person in store.people() if person["id"] == payload["person_id"]),
                None,
            )
            if person is None:
                raise ValueError("Personel bulunamadı.")
            start = date.fromisoformat(payload["start"]).strftime("%d.%m.%Y")
            end = date.fromisoformat(payload["end"]).strftime("%d.%m.%Y")
            category_label = {
                "G": "İşe gelmedi", "S": "Yıllık izin", "Ü": "Ücretsiz izin",
            }[payload["category"]]
            summary = f"{template['name']} · {person['name']} · {start}"
            if end != start:
                summary += f"–{end}"
            with store.connection:
                store.record_change(
                    "form.generated",
                    summary,
                    {
                        "template_id": payload["id"],
                        "template_name": template["name"],
                        "employee_id": payload["person_id"],
                        "employee_name": person["name"],
                        "category": category_label,
                        "start": payload["start"],
                        "end": payload["end"],
                        "file_name": Path(file_name).name,
                    },
                )

    @staticmethod
    def _attendance_people(people, entries, overrides, deductions=None):
        used_ids = {int(entry["employee_id"]) for entry in entries}
        used_ids.update(int(person_id) for values in overrides.values() for person_id in values)
        used_ids.update(int(person_id) for person_id in (deductions or {}))
        return [
            person for person in people
            if person["active"] or person["id"] in used_ids
        ]

    @staticmethod
    def _attendance_staff(store, month, entries, month_data, people=None, profiles=None):
        """Puantaj satırları: o ayın vardiyasındaki kişiler + vardiya dışı personel + elle eklenenler."""
        people = store.people() if people is None else people
        profiles = store.employee_profiles() if profiles is None else profiles
        by_id = {person["id"]: person for person in people}
        year, month_number = map(int, month.split("-"))
        first = date(year, month_number, 1)
        last = date(year, month_number, calendar.monthrange(year, month_number)[1])
        before_first = (first - timedelta(days=1)).isoformat()
        roster = month_data.get("roster") or {}
        removed = set(roster.get("removed") or [])
        added = [item for item in roster.get("added") or [] if item in by_id]
        order = {item: index for index, item in enumerate(roster.get("order") or [])}

        scheduled: dict[int, tuple] = {}

        def mark(entry):
            employee_id = int(entry["employee_id"])
            key = (scheduling._section_sort_key(str(entry.get("section", ""))), int(entry.get("slot") or 99))
            scheduled[employee_id] = min(scheduled.get(employee_id, key), key)

        for entry in store.schedule_month(month)["entries"]:
            days = entry.get("days") or {}
            if not days or any(day[:7] == month for day in days):
                mark(entry)
        for entry in entries:
            for day, item in (entry.get("days") or {}).items():
                overnight = (day == before_first and item.get("kind") == "work"
                             and str(item.get("end", "")) < str(item.get("start", "")))
                if day[:7] == month or overnight:
                    mark(entry)
                    break
        records = {int(person_id) for values in (month_data.get("overrides") or {}).values()
                   if isinstance(values, dict) for person_id in values}
        records.update(int(person_id) for person_id in (month_data.get("deductions") or {}))
        records.update(int(person_id) for person_id in (month_data.get("overtime") or {}))

        def employed(profile):
            hire, leave = profile.get("hire_date") or "", profile.get("exit_date") or ""
            return not (hire and hire > last.isoformat()) and not (leave and leave < first.isoformat())

        groups: dict[int, tuple] = {}
        for employee_id, key in scheduled.items():
            groups[employee_id] = (1, *key)
        for person in people:
            profile = profiles.get(str(person["id"]), {})
            if (person["active"] and profile.get("staff_type") == "nonshift" and employed(profile)
                    and person["id"] not in groups):
                groups[person["id"]] = (2, 0, 0)
        for employee_id in added:
            if by_id[employee_id]["active"] or employee_id in records:
                groups.setdefault(employee_id, (3, 0, 0))
        for employee_id in records:
            groups.setdefault(employee_id, (4, 0, 0))
        staff = []
        for employee_id, group in groups.items():
            person = by_id.get(employee_id)
            if person is None or (employee_id in removed and employee_id not in scheduled):
                continue
            item = {"id": employee_id, "name": person["name"], "active": person["active"]}
            profile = profiles.get(str(employee_id), {})
            if profile.get("daily_limit_hours"):
                item["daily_limit_hours"] = profile["daily_limit_hours"]
            if profile.get("break_exempt"):
                item["break_exempt"] = True
            if profile.get("staff_type") == "nonshift":
                item["fixed_pattern"] = list(profile.get("fixed_pattern") or scheduling.DEFAULT_FIXED_PATTERN)
                item["fixed_pattern_exceptions"] = list(profile.get("fixed_pattern_exceptions") or [])
                item["fixed_pattern_changes"] = list(profile.get("fixed_pattern_changes") or [])
                item["fixed_start"] = profile.get("fixed_start") or "08:00"
                item["fixed_end"] = profile.get("fixed_end") or "16:00"
            item["_key"] = (order.get(employee_id, len(order)), *group, person["name"].casefold(), employee_id)
            staff.append(item)
        staff.sort(key=lambda item: item["_key"])
        labels = {1: "schedule", 2: "nonshift", 3: "added", 4: "records"}
        month_data["sources"] = {
            str(employee_id): labels[group[0]] for employee_id, group in groups.items()
        }
        for index, item in enumerate(staff):
            item.pop("_key")
            item["sort"] = index
        return staff

    @staticmethod
    def _attendance_missing(store, month, available, month_data):
        """Active people employed this month who are neither in the attendance nor deliberately removed."""
        removed = {int(item) for item in (month_data.get("roster") or {}).get("removed") or []}
        profiles = store.employee_profiles()
        year, month_number = map(int, month.split("-"))
        first = f"{month}-01"
        last = f"{month}-{calendar.monthrange(year, month_number)[1]:02d}"
        missing = []
        for person in available:
            profile = profiles.get(str(person["id"]), {})
            hire, leave = profile.get("hire_date") or "", profile.get("exit_date") or ""
            if person["id"] in removed or (hire and hire > last) or (leave and leave < first):
                continue
            missing.append({"id": person["id"], "name": person["name"]})
        return missing

    def _attendance_for(self, store, month, entries=None, people=None, profiles=None, settings=None):
        if entries is None:
            entries = [
                entry for period in scheduling.adjacent_months(month)
                for entry in store.schedule_month(period)["entries"]
            ]
        month_data = store.attendance_month(month)
        staff = self._attendance_staff(store, month, entries, month_data, people, profiles)
        attendance = scheduling.calculate_attendance(
            month, entries, month_data["overrides"], staff, month_data["deductions"],
            store.overtime_settings(month) if settings is None else settings, month_data["overtime"],
        )
        return attendance, entries, month_data

    def _assistant_record_answer(self, store, kind: str, message: str, context: dict) -> dict:
        month = context.get("month")
        if not month:
            return {"reply": "Kayıtlı sonucu hesaplamak için önce bir dönem seçin."}
        month = scheduling.validate_month(month)
        if kind in {"staffing", "constraints"}:
            report = operations.quality_report(store, month)
            staffing = [
                item for item in report["findings"]
                if item.get("type") in {"min_staffing", "staffing"}
            ]
            sources = []
            if kind == "staffing":
                settings = store.compliance_settings()
                if not settings.get("min_staffing") and not settings.get("min_staffing_windows"):
                    return {"reply": f"{month} dönemi için asgari kadro kuralı kayıtlı değil; boş hücreleri eksik vardiya sayamam."}
                findings = staffing
            else:
                findings = []
                for item in report["findings"]:
                    issue_type = str(item.get("type", ""))
                    if ("leave" in issue_type or "protect" in issue_type or "staffing" in issue_type
                            or issue_type in {"overlap", "rest"}):
                        findings.append(item)
                import schedule_protection
                protected = schedule_protection.locks(store, month)
                for lock in protected:
                    findings.append({
                        "type": "protection", "date": lock["day"], "name": lock["section"],
                        "message": f"{lock['section']} · {lock['day']} günü korunmuş vardiya.",
                    })
                if month in store.locked_periods():
                    findings.append({
                        "type": "period_lock", "date": None, "name": month,
                        "message": f"{month} dönemi kayıt kilidi etkin.",
                    })
            if kind == "staffing":
                asserted = re.search(r"\b(\d{1,3}|bir|iki|üç|uc|dört|dort|beş|bes)\b", message.casefold())
                number_names = {"bir": 1, "iki": 2, "üç": 3, "uc": 3, "dört": 4, "dort": 4, "beş": 5, "bes": 5}
                stated_count = None
                if asserted:
                    token = asserted.group(1)
                    stated_count = number_names.get(token, int(token) if token.isdigit() else None)
                count = len(findings)
                comparison = (
                    f" İletide {stated_count} denmiş; kayıtlı raporda {count} açık bulundu."
                    if stated_count is not None and stated_count != count else ""
                )
                if not findings:
                    reply = f"{month} döneminde kayıtlı asgari kadro kurallarına göre açık bulunmadı.{comparison}"
                else:
                    reply = (
                        f"{month} döneminin kayıtlı asgari kadro raporunda {count} açık var.{comparison} "
                        "Her bağlantı ilgili gün, bölüm ve plan sürümünü açar."
                    )
            else:
                categories = {
                    "leave": "izin", "staffing": "kadro", "protect": "koruma",
                    "period_lock": "dönem kilidi", "overlap": "vardiya çakışması", "rest": "dinlenme",
                }
                if findings:
                    grouped = {}
                    for item in findings:
                        issue_type = str(item.get("type", ""))
                        label = next((name for key, name in categories.items() if key in issue_type), "kural")
                        grouped[label] = grouped.get(label, 0) + 1
                    summary = ", ".join(f"{count} {label}" for label, count in sorted(grouped.items()))
                    reply = (
                        f"{month} kayıtlı plan kalite raporundaki birlikte değerlendirilmesi gereken koşullar: "
                        f"{summary}. Bunlar kayıtlı bulgulardır; tek başına planın matematiksel olarak olanaksız "
                        "olduğunu kanıtlamaz."
                    )
                else:
                    reply = (
                        f"{month} kayıtlı plan kalite raporunda izin, koruma veya kadro kaynaklı engel bulunmadı. "
                        "Bu, plan oluşturma işleminin başarılı olduğunu veya tüm koşulların sağlandığını göstermez."
                    )
            for item in findings[:20]:
                day = item.get("date")
                sources.append({
                    "kind": "schedule",
                    "month": month,
                    "date": day if isinstance(day, str) and day.startswith(month + "-") else "",
                    "section": str(item.get("section") or item.get("name") or ""),
                    "label": str(item.get("message") or ""),
                    "revision": str(report.get("revision") or ""),
                })
            if len(findings) > len(sources):
                reply += f" İlk {len(sources)} kaynak gösteriliyor; toplam {len(findings)} bulgu var."
            return {"reply": reply, "sources": sources}

        people_names = context.get("people", [])
        if len(people_names) != 1:
            return {"reply": "Hesap sonucunu bağlayabilmem için mesajda personelin tam adını ve dönemi belirtin."}
        matching = [person for person in store.people()
                    if person["name"].casefold() == people_names[0].casefold()]
        if len(matching) != 1:
            return {"reply": "Personel kartıyla tekil eşleşme bulunamadı; kayıtlı hesap sonucu gösterilmedi."}
        detail = document_workflows.calculation_detail(self, store, month, int(matching[0]["id"]))
        totals = detail["monthly_totals"]
        row = detail["result"]

        def display(value):
            if not isinstance(value, (int, float)):
                return "hesaplanmadı"
            return f"{value:.2f}".rstrip("0").rstrip(".")

        if "fazla mesai" in message.casefold() or "mesai" in message.casefold():
            reply = (
                f"{matching[0]['name']} · {month}: uygulamanın puantaj motorundaki çalışma toplamı "
                f"{display(totals.get('worked_hours'))} saat; hesaplanan fazla mesai "
                f"{display(totals.get('overtime_computed_hours'))} saat; elle bildirilen mesai "
                f"{display(totals.get('overtime_manual'))} saat. Ücret hesabı yapılmadı."
            )
        else:
            reply = (
                f"{matching[0]['name']} · {month}: uygulamanın puantaj motorundaki çalışma toplamı "
                f"{display(totals.get('worked_hours'))} saat; hesaplanan fazla mesai "
                f"{display(totals.get('overtime_computed_hours'))} saat. Ücret hesabı yapılmadı."
            )
        attendance_revision = store.attendance_month(month)["revision"]
        schedule_revision = store.schedule_month(month)["revision"]
        rule_date = detail.get("overtime_rule_effective_date") or "kayıtlı yürürlük tarihi yok"
        reply += (
            f" Kapsam: {month}; puantaj sürümü {attendance_revision or 'yok'}, "
            f"plan sürümü {schedule_revision or 'yok'}, mesai kuralı {rule_date}."
        )
        return {
            "reply": reply,
            "sources": [{
                "kind": "attendance",
                "month": month,
                "date": "",
                "section": matching[0]["name"],
                "employee_id": int(matching[0]["id"]),
                "label": f"Puantaj hesabı · {matching[0]['name']} · {month}",
                "revision": str(attendance_revision or ""),
            }],
        }

    def _leave_balance_ledger(self, store, employee_id, year, through_month, opening):
        history = store.balance_opening_history(employee_id, year)
        if history:
            opening_value = float(history[0]["previous_annual_leave_days"])
        else:
            opening_value = float(opening.get("annual_leave_days", 0))
        configured = employee_id in store.balance_openings(year)
        movements = []
        if configured:
            movements.append({
                "date": f"{year}-01-01", "type": "opening", "source": f"Açılış bakiyesi · {year}",
                "delta": opening_value, "actor": "", "detail": "Yıl başlangıç bakiyesi",
            })
        for item in history:
            previous = float(item["previous_annual_leave_days"])
            current = float(item["new_annual_leave_days"])
            movements.append({
                "date": str(item["occurred_at"])[:10], "type": "adjustment",
                "source": "Açılış bakiyesi düzeltmesi", "delta": round(current - previous, 2),
                "actor": item["actor"], "detail": f"{previous:g} → {current:g} gün",
            })
        for month_number in range(1, through_month + 1):
            period = f"{year}-{month_number:02}"
            attendance, _, month_data = self._attendance_for(store, period)
            for row in attendance["rows"]:
                if int(row["employee_id"]) != employee_id:
                    continue
                overrides = month_data["overrides"]
                for day, code in row["codes"].items():
                    if code != "S":
                        continue
                    is_manual = overrides.get(day, {}).get(str(employee_id)) == "S"
                    movements.append({
                        "date": day, "type": "used",
                        "source": "Elle puantaj düzeltmesi" if is_manual else "Vardiyadan hesaplanan puantaj",
                        "delta": -1.0, "actor": "", "detail": "Yıllık izin kodu (S)",
                    })
        cancelled = store.connection.execute(
            "SELECT id,start_date,end_date,updated_at FROM linked_leave_requests "
            "WHERE employee_id=? AND kind='annual_leave' AND status='cancelled' "
            "AND start_date<=? AND end_date>=? ORDER BY updated_at,id",
            (employee_id, f"{year}-12-31", f"{year}-01-01"),
        )
        for item in cancelled:
            movements.append({
                "date": str(item["updated_at"])[:10], "type": "cancellation",
                "source": f"İptal edilen izin talebi · {item['id']}",
                "delta": 0.0, "actor": "", "detail": f"{item['start_date']} – {item['end_date']}; puantaj hareketi ayrıca listelenir",
            })
        movements.sort(key=lambda item: (item["date"], {"opening": 0, "adjustment": 1, "used": 2, "cancellation": 3}.get(item["type"], 4)))
        balance = opening_value if configured else None
        for item in movements:
            if item["type"] == "opening":
                balance = item["delta"]
            elif balance is not None:
                balance = round(balance + item["delta"], 2)
            item["running_balance"] = balance
        return {
            "employee_id": employee_id, "year": year, "through_month": through_month,
            "opening_configured": configured, "current_opening_days": opening_value if configured else None,
            "closing_days": balance, "movements": movements,
            "notice": "Yalnız açılış bakiyesi, kaydedilmiş puantaj S kodları ve izlenebilir açılış düzeltmeleri hesaba katılır. İptal edilen talep sıfır hareketlidir; mevzuat kaynaklı hak ediş eklenmez.",
        }

    def _balance_summary(self, store, month, ledger_employee_id=None):
        year, through_month = map(int, month.split("-"))
        people = store.people()
        totals = {
            person["id"]: {
                "annual_leave_used_days": 0,
                "sick_days": 0,
                "unpaid_leave_days": 0,
                "overtime_reported_hours": 0.0,
                "absence_days": 0,
            }
            for person in people
        }
        profiles = store.employee_profiles()
        for month_number in range(1, through_month + 1):
            period = f"{year}-{month_number:02}"
            attendance, _, _ = self._attendance_for(store, period, people=people, profiles=profiles)
            for row in attendance["rows"]:
                person_totals = totals[row["employee_id"]]
                codes = row["codes"].values()
                person_totals["annual_leave_used_days"] += sum(code == "S" for code in codes)
                person_totals["sick_days"] += sum(code == "R" for code in codes)
                person_totals["unpaid_leave_days"] += sum(code == "Ü" for code in codes)
                person_totals["absence_days"] += sum(code == "G" for code in codes)
                person_totals["overtime_reported_hours"] += float(row["overtime_hours"])
        openings = store.balance_openings(year)
        profiles = store.employee_profiles()
        reference_day = min(
            date(year, through_month, calendar.monthrange(year, through_month)[1]), date.today()
        ) if year >= date.today().year else date(year, 12, 31)
        result = []
        for person in people:
            opening = openings.get(person["id"], {})
            usage = totals[person["id"]]
            leave_opening = float(opening.get("annual_leave_days", 0))
            overtime_opening = float(opening.get("overtime_hours", 0))
            profile = profiles.get(str(person["id"]), {})
            try:
                entitlement = reports.leave_entitlement(
                    profile.get("hire_date", ""), profile.get("birth_date", ""), reference_day,
                )
            except ValueError:
                entitlement = None
            result.append({
                "entitlement": entitlement,
                "employee_id": person["id"],
                "name": person["name"],
                "active": person["active"],
                "has_opening": person["id"] in openings,
                "annual_leave_opening_days": leave_opening,
                "annual_leave_used_days": usage["annual_leave_used_days"],
                "annual_leave_remaining_days": round(leave_opening - usage["annual_leave_used_days"], 2),
                "overtime_opening_hours": overtime_opening,
                "overtime_reported_hours": round(usage["overtime_reported_hours"], 2),
                "overtime_tracked_hours": round(overtime_opening + usage["overtime_reported_hours"], 2),
                "sick_days": usage["sick_days"],
                "unpaid_leave_days": usage["unpaid_leave_days"],
                "absence_days": usage["absence_days"],
            })
        if ledger_employee_id is not None:
            selected = next((person for person in result if person["employee_id"] == ledger_employee_id), None)
            if selected is None:
                raise ValueError("İzin hareket dökümü için personel bulunamadı.")
            selected_opening = openings.get(ledger_employee_id, {})
            selected["leave_balance_ledger"] = self._leave_balance_ledger(
                store, ledger_employee_id, year, through_month, selected_opening,
            )
        return {"year": year, "through_month": through_month, "people": result}

    @staticmethod
    def _person_rules(store):
        """Kişiye özel günlük sınır / mola muafiyeti olan personel kartları."""
        return {
            int(employee_id): profile for employee_id, profile in store.employee_profiles().items()
            if str(employee_id).isdigit() and (profile.get("daily_limit_hours") or profile.get("break_exempt"))
        }

    @staticmethod
    def _schedule_week_totals(store, month):
        periods = scheduling.adjacent_months(month)
        entries = scheduling._merge_neighbor_entries([
            entry for period in (periods[0], periods[2], periods[1])
            for entry in store.schedule_month(period)["entries"]
        ])
        totals = scheduling.schedule_week_totals(
            entries, scheduling.week_starts(month), store.overtime_settings(month), ScheduleRequestHandler._person_rules(store),
        )
        return {
            str(employee_id): {monday: list(values) for monday, values in weeks.items()}
            for employee_id, weeks in totals.items()
        }

    @staticmethod
    def _leave_remaining(balances):
        return {
            int(item["employee_id"]): item["annual_leave_remaining_days"]
            for item in balances["people"] if item["has_opening"]
        }

    def _month_attendance(self, store, month):
        attendance, entries, _ = self._attendance_for(store, month)
        return attendance, entries

    def _payroll_data(self, month):
        with self._store() as store:
            attendance, entries = self._month_attendance(store, month)
            profiles = store.employee_profiles()
            settings = store.overtime_settings(month)
        sgk = payroll.sgk_summary(month, attendance, profiles)
        overtime = payroll.overtime_breakdown(month, attendance)
        return attendance, sgk, overtime, settings

    def _analytics_summary(self, month, hours_per_month=analytics.HOURS_PER_MONTH):
        with self._store() as store:
            return self._analytics_summary_from_store(store, month, hours_per_month)

    def _analytics_summary_from_store(self, store, month, hours_per_month=analytics.HOURS_PER_MONTH):
        if not store.connection.in_transaction:
            store.connection.execute("BEGIN")
        previous_month, last_year_month = analytics.shift_month(month, -1), analytics.shift_month(month, -12)
        comparison_months = (
            month, previous_month, last_year_month, analytics.shift_month(month, -2),
        )
        attendance, entries = self._month_attendance(store, month)
        balances = self._balance_summary(store, month)
        check = self._check_report(store, month, attendance, entries, balances)
        profiles = store.employee_profiles()
        others = {}
        for other in comparison_months[1:]:
            other_attendance, other_entries = self._month_attendance(store, other)
            others[other] = (other_attendance, other_entries)
        source_periods = sorted({
            period for report_month in comparison_months
            for period in scheduling.adjacent_months(report_month)
        })
        source_revisions = {
            period: self._month_revisions(store, period) for period in source_periods
        }
        pdks_imported = bool(store._setting("pdks_records", {}).get(month))
        sgk = payroll.sgk_summary(month, attendance, profiles)
        overtime = payroll.overtime_breakdown(month, attendance)
        metrics = analytics.month_metrics(attendance, overtime)
        compared = {}
        for other in (previous_month, last_year_month):
            other_attendance, other_entries = others[other]
            compared[other] = analytics.month_metrics(
                other_attendance, payroll.overtime_breakdown(other, other_attendance),
            )
        important = [item for item in check["items"] if item["severity"] in ("error", "warning")]
        has_attendance_records = any(
            any(row.get("codes", {}).values()) for row in attendance["rows"]
        )
        missing_sources = []
        if not entries:
            missing_sources.append("Vardiya planı kaydı yok")
        if not has_attendance_records:
            missing_sources.append("Puantaj kodu kaydı yok")
        if not pdks_imported:
            missing_sources.append("PDKS aktarımı yok (isteğe bağlı kaynak)")
        summary = {
            "month": month, "metrics": metrics,
            "report_context": {
                "calculated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "source_revisions": source_revisions,
                "missing_sources": missing_sources,
                "has_attendance_records": has_attendance_records,
            },
            "previous_month": previous_month, "previous": compared[previous_month],
            "last_year_month": last_year_month, "last_year": compared[last_year_month],
            "compare_previous": analytics.compare(metrics, compared[previous_month]),
            "compare_last_year": analytics.compare(metrics, compared[last_year_month]),
            "cost": analytics.cost_estimate(attendance, sgk, overtime, profiles, hours_per_month),
            "patterns": analytics.absence_patterns(
                [(analytics.shift_month(month, -2), others[analytics.shift_month(month, -2)][0]),
                 (previous_month, others[previous_month][0]), (month, attendance)],
            ),
            "overtime": overtime,
            "warnings": {"total": len(important), "counts": check["counts"], "items": important},
        }
        report_version = analytics.manager_report_version(summary)
        summary["report_context"]["report_version"] = report_version
        summary["chart_notes"] = store.manager_chart_notes(month, report_version)
        return summary

    def _output_sources(self, store, month):
        periods = set(scheduling.adjacent_months(month))
        periods.update(f"{month[:4]}-{number:02}" for number in range(1, int(month[5:]) + 1))
        return {period: self._month_revisions(store, period) for period in sorted(periods)}

    def _template_revision(self, store, kind, month, sheet_index):
        settings = scheduling.repair_workbook_settings(store.workbook_settings())
        key = f"{kind}:{month}:{sheet_index}"
        template_key = f"{kind}:template:{sheet_index}"
        sheets = settings.get("sheets", {})
        payload = {
            "kind": kind,
            "layout": scheduling.workbook_template(kind, sheet_index),
            "settings": {
                name: sheets[name]
                for name in (template_key, key)
                if name in sheets
            },
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()

    def _track_output(
        self, store, content, extension, filename, month, sources, draft=False, template_revision="",
    ):
        source_code = hashlib.sha256(
            json.dumps(sources, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12].upper()
        identity = {
            "id": document_workflows.new_export_code(),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "app_version": version.APP_VERSION,
            "source_code": source_code,
            "draft": draft,
        }
        content = output_tracking.stamp_output(content, extension, identity)
        store._backup_before_change()
        store.connection.execute("BEGIN IMMEDIATE")
        try:
            if any(self._month_revisions(store, period) != saved for period, saved in sources.items()):
                raise ValueError("Çıktı hazırlanırken kaynak kayıtlar değişti. Güncel verilerle yeniden deneyin.")
            if extension in ("csv", "xml"):
                filename = f"{Path(filename).stem}_{identity['id']}.{extension}"
            document_workflows.record_export(
                store.connection, f"draft-{extension}" if draft else extension,
                month, filename, content, sources,
                export_id=identity["id"],
                template_revision=template_revision,
            )
        except Exception:
            store.connection.rollback()
            raise
        return content, filename

    def _report_file(self, path, query):
        if path in ("/api/reports/problem-report", "/api/reports/settings-export"):
            return self._untracked_report_file(path, query)
        month = query.get("month", [""])[0]
        if path == "/api/reports/yearly":
            raw_year = query.get("year", [""])[0]
            if not raw_year.isdigit() or not 2000 <= int(raw_year) <= 2100:
                raise ValueError("Yıl seçimi geçersiz.")
            month = f"{int(raw_year)}-12"
        month = scheduling.validate_month(month)
        with self._store() as store:
            sources = self._output_sources(store, month)
        content, extension, filename, mime = self._untracked_report_file(path, query)
        with self._store() as store:
            content, filename = self._track_output(
                store, content, extension, filename, month, sources,
            )
        return content, extension, filename, mime

    def _untracked_report_file(self, path, query):
        if path == "/api/reports/problem-report":
            with self._store() as store:
                content = workspace_tools.problem_report_zip(store, Path(self.server.data_path), query.get("review", [None])[0])
            stamp = date.today().isoformat()
            return content, "zip", f"Sorun_Raporu_{stamp}.zip", "application/zip"
        if path == "/api/reports/settings-export":
            with self._store() as store:
                data = workspace_tools.export_settings(store)
            content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            return content, "json", f"VardiyaPuantaj_Ayarlar_{date.today().isoformat()}.json", "application/json"
        if path == "/api/reports/manager-summary":
            month = scheduling.validate_month(query.get("month", [""])[0])
            content = analytics.manager_summary_pdf(month, self._analytics_summary(month))
            return content, "pdf", f"Yonetici_Ozeti_{month}.pdf", "application/pdf"
        if path == "/api/reports/payroll-program":
            month = scheduling.validate_month(query.get("month", [""])[0])
            attendance, sgk, overtime, _ = self._payroll_data(month)
            import payroll_extras
            with self._store() as store:
                added = payroll_extras.month_rows(attendance, payroll_extras.load(store, month))
            content, filename, mime = payroll.payroll_program_file(
                month, payroll.export_rows(month, attendance, sgk, overtime, added), query.get("format", [""])[0],
            )
            return content, filename.rsplit(".", 1)[1], filename, mime
        if path == "/api/reports/yearly":
            raw_year = query.get("year", [""])[0]
            if not raw_year.isdigit() or not 2000 <= int(raw_year) <= 2100:
                raise ValueError("Yıl seçimi geçersiz.")
            year = int(raw_year)
            with self._store() as store:
                months = [
                    (f"{year}-{number:02}", self._month_attendance(store, f"{year}-{number:02}")[0])
                    for number in range(1, 13)
                ]
            return (
                reports.yearly_xlsx(year, months), "xlsx", f"YILLIK_PUANTAJ_{year}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        month = scheduling.validate_month(query.get("month", [""])[0])
        if path == "/api/reports/month-package":
            return self._month_package(month), "zip", f"AY_SONU_PAKETI_{month}.zip", "application/zip"
        with self._store() as store:
            attendance, _ = self._month_attendance(store, month)
            if path == "/api/reports/person-program":
                raw_id = query.get("employee_id", [""])[0]
                if raw_id and not raw_id.isdigit():
                    raise ValueError("Personel seçimi geçersiz.")
                employee_id = int(raw_id) if raw_id else None
                schedule = store.schedule_month(month)
                content = reports.person_program_pdf(
                    month, schedule["entries"], store.people(), employee_id,
                )
                person = next((item for item in store.people() if item["id"] == employee_id), None)
                store.record_change(
                    "schedule.program_export",
                    "Vardiya programı PDF dışa aktarıldı" + (f" · {person['name']}" if person else ""),
                    {"month": month, "employee_id": employee_id, "schedule_revision": schedule["revision"]},
                )
                store.connection.commit()
                return content, "pdf", f"VARDIYA_PROGRAMI_{month}.pdf", "application/pdf"
            if path == "/api/reports/payroll":
                file_format = query.get("format", ["xlsx"])[0]
                if file_format == "csv":
                    return reports.payroll_csv(attendance), "csv", f"BORDRO_{month}.csv", "text/csv; charset=utf-8"
                if file_format != "xlsx":
                    raise ValueError("Bordro biçimi csv veya xlsx olmalıdır.")
                return (
                    reports.payroll_xlsx(month, attendance), "xlsx", f"BORDRO_{month}.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            raw_id = query.get("employee_id", [""])[0]
            if raw_id and not raw_id.isdigit():
                raise ValueError("Personel seçimi geçersiz.")
            employee_id = int(raw_id) if raw_id else None
            content = reports.person_summary_pdf(
                month, attendance, self._balance_summary(store, month), employee_id,
            )
            suffix = ""
            if employee_id is not None:
                name = next(row["name"] for row in attendance["rows"] if int(row["employee_id"]) == employee_id)
                ascii_name = name.translate(str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU"))
                suffix = "_" + re.sub(r"[^A-Za-z0-9]+", "_", ascii_name).strip("_")
            return content, "pdf", f"PERSONEL_OZET_{month}{suffix}.pdf", "application/pdf"

    def _month_package(self, month: str) -> bytes:
        import zipfile
        archive = BytesIO()
        with self._store() as store, zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
            people = store.people()
            periods = scheduling.adjacent_months(month)
            entries = [
                entry for period in (periods[0], periods[2], periods[1])
                for entry in store.schedule_month(period)["entries"]
            ]
            selected_entries = store.schedule_month(month)["entries"]
            attendance, month_entries = self._month_attendance(store, month)
            balances = self._balance_summary(store, month)
            with tempfile.TemporaryDirectory(prefix="vardiya-paket-") as temp:
                if selected_entries:
                    output = Path(temp) / f"VARDIYA_LISTESI_{month}.xlsx"
                    scheduling.export_schedule_xlsx(
                        output, month, entries, people, 0, scheduling.repair_workbook_settings(store.workbook_settings()),
                        selected_entries=selected_entries, overtime_settings=store.overtime_settings(month),
                        leave_remaining=self._leave_remaining(balances), person_rules=self._person_rules(store),
                    )
                    package.write(output, output.name)
                    package.writestr(
                        f"VARDIYA_PROGRAMI_{month}.pdf",
                        reports.person_program_pdf(month, selected_entries, people),
                    )
                if any(any(row.get("codes", {}).values()) for row in attendance["rows"]):
                    output = Path(temp) / f"PUANTAJ_{month}.xlsx"
                    scheduling.export_attendance_xlsx(output, month, attendance, scheduling.repair_workbook_settings(store.workbook_settings()))
                    package.write(output, output.name)
                    package.writestr(f"BORDRO_{month}.xlsx", reports.payroll_xlsx(month, attendance))
                    package.writestr(f"BORDRO_{month}.csv", reports.payroll_csv(attendance))
                    package.writestr(
                        f"PERSONEL_OZET_{month}.pdf",
                        reports.person_summary_pdf(month, attendance, balances),
                    )
            if not package.namelist():
                raise ValueError("Bu ay için kayıtlı vardiya veya puantaj yok.")
            check = self._check_report(store, month, attendance, month_entries, balances)
            lines = [f"DÖNEM KONTROLÜ {month}", ""]
            labels = {"error": "HATA", "warning": "UYARI", "info": "BİLGİ"}
            lines += [
                f"[{labels[item['severity']]}] {item['name']}"
                + (f" · {item['date']}" if item.get("date") else "") + f" · {item['message']}"
                for item in check["items"]
            ] or ["Hata veya uyarı yok."]
            package.writestr(f"DONEM_KONTROLU_{month}.txt", "\ufeff" + "\r\n".join(lines))
        return archive.getvalue()

    def _check_report(self, store, month: str, attendance: dict, entries: list, balances: dict) -> dict:
        rest = float(store.schedule_validation_settings().get("minimum_rest_hours", 0) or 0)
        names = {int(row["employee_id"]): row["name"] for row in attendance["rows"]}
        settings = store.compliance_settings()
        history = []
        cursor = scheduling.date.fromisoformat(f"{month}-01")
        for _ in range(max(2, -(-settings["balancing_weeks"] * 7 // 28) + 1)):
            cursor = (cursor - scheduling.timedelta(days=1)).replace(day=1)
            history += store.schedule_month(cursor.strftime("%Y-%m"))["entries"]
        profiles = store.employee_profiles()
        extra = compliance.compliance_items(month, history + entries, names, profiles, settings, rest)
        plan = store.planning_settings()
        extra += planning.preference_items(month, entries, names, profiles)
        extra += planning.skill_items(month, entries, names, profiles, plan["skill_requirements"])
        requests = store.leave_requests() + linked_leave.calendar_requests(store, month)
        extra += planning.leave_request_items(month, entries, names, requests, plan["daily_limit"])
        extra += compliance.staffing_items(
            month, history + entries, settings.get("min_staffing", {}), settings.get("min_staffing_windows", []),
        )
        dates = scheduling.month_dates(month)
        for person in store.people():
            profile = profiles.get(str(person["id"]), {})
            hire, leave = profile.get("hire_date") or "", profile.get("exit_date") or ""
            if (not person["active"] or person["id"] in names
                    or (hire and hire > dates[-1].isoformat()) or (leave and leave < dates[0].isoformat())):
                continue
            extra.append({
                "type": "no_records", "severity": "warning", "employee_id": person["id"],
                "name": person["name"], "date": None,
                "message": "Aktif personel bu ayın vardiya listesinde ve puantajda yok",
            })
        for person in store.people():
            leave = profiles.get(str(person["id"]), {}).get("exit_date") or ""
            if not leave or leave >= dates[-1].isoformat():
                continue
            late = sorted({
                day for entry in entries if int(entry["employee_id"]) == person["id"]
                for day, item in (entry.get("days") or {}).items()
                if day > leave and day[:7] == month and item.get("kind") == "work"
            })
            if late:
                extra.append({
                    "type": "after_exit", "severity": "error", "employee_id": person["id"],
                    "name": person["name"], "date": late[0],
                    "message": f"İşten çıkış tarihi {leave}; sonrasında {len(late)} gün vardiya yazılmış",
                })
        soon_limit = (dates[-1] + scheduling.timedelta(days=30)).isoformat()
        for person in store.people():
            profile = profiles.get(str(person["id"]), {})
            if not person["active"] or (profile.get("exit_date") or "9999") < dates[0].isoformat():
                continue
            for document in profile.get("documents") or []:
                expires = document.get("expires") or ""
                if not expires or expires > soon_limit:
                    continue
                expired = expires < dates[0].isoformat()
                extra.append({
                    "type": "document_expiry", "severity": "warning", "employee_id": person["id"],
                    "name": person["name"], "date": expires if expires[:7] == month else None,
                    "message": (f"{document.get('name', 'Belge')} süresi {expires} tarihinde dolmuş"
                                if expired else f"{document.get('name', 'Belge')} süresi {expires} tarihinde doluyor"),
                })
        import warning_rules
        report = reports.check_report(
            month, attendance, entries, balances, rest,
            {int(item["employee_id"]): float(item["overtime_tracked_hours"]) for item in balances["people"]},
            extra,
            break_rules=store.overtime_settings(month).get("break_rules", []),
            person_rules=self._person_rules(store),
        )
        return warning_rules.apply(report, warning_rules.load(store))

    def _duty_board(self, store, day: date) -> dict:
        """Who works on ``day`` grouped by section, plus that day's handover notes."""
        month = day.strftime("%Y-%m")
        people = {person["id"]: person for person in store.people()}
        tags = store.person_tags()
        day_text = day.isoformat()
        previous = (day - scheduling.timedelta(days=1)).isoformat()
        sections: dict[str, list] = {}
        off = []
        seen = set()
        for entry in (entry for period in scheduling.adjacent_months(month)
                      for entry in store.schedule_month(period)["entries"]):
            employee_id = int(entry["employee_id"])
            person = people.get(employee_id)
            if person is None:
                continue
            days = entry.get("days") or {}
            for target in (day_text, previous):
                item = days.get(target)
                if not item or (employee_id, target, entry.get("section")) in seen:
                    continue
                seen.add((employee_id, target, entry.get("section")))
                if item["kind"] == "work":
                    overnight = item["end"] < item["start"]
                    if target == previous and not overnight:
                        continue
                    sections.setdefault(entry.get("section") or "Diğer", []).append({
                        "employee_id": employee_id, "name": person["name"], "start": item["start"],
                        "end": item["end"], "label": item.get("shift_label", ""),
                        "from_previous": target == previous, "overnight": overnight,
                        "color": tags.get(str(employee_id), {}).get("color", ""),
                    })
                elif target == day_text:
                    off.append({
                        "employee_id": employee_id, "name": person["name"], "section": entry.get("section", ""),
                        "status": (item.get("custom_label") or item.get("custom_code")
                                   if item["kind"] == "custom"
                                   else reports.SCHEDULE_KIND_LABELS.get(item["kind"], item["kind"])),
                    })
        order = {name: index for index, name in enumerate(scheduling.section_names())}
        return {
            "date": day_text,
            "category": scheduling.attendance_day_category(day),
            "sections": [
                {"name": name, "people": sorted(items, key=lambda item: (item["from_previous"] is False, item["start"], item["name"]))}
                for name, items in sorted(sections.items(), key=lambda pair: (order.get(pair[0], 99), pair[0]))
            ],
            "off": sorted(off, key=lambda item: item["name"]),
            "notes": [
                note for note in store.handover_notes(end=day_text)
                if note["status"] != "completed" or note["date"] == day_text
            ],
        }

    def _dashboard(self, store, day: date) -> dict:
        month = day.strftime("%Y-%m")
        people = {person["id"]: person["name"] for person in store.people()}
        entries = [
            entry for period in scheduling.adjacent_months(month)
            for entry in store.schedule_month(period)["entries"]
        ]
        working, off, upcoming = [], [], []
        day_text = day.isoformat()
        tomorrow = (day + scheduling.timedelta(days=1)).isoformat()
        for entry in entries:
            name = people.get(int(entry["employee_id"]), "Personel")
            for target, bucket in ((day_text, None), (tomorrow, upcoming)):
                item = entry.get("days", {}).get(target)
                if not item:
                    continue
                record = {"name": name, "section": entry.get("section", "")}
                if item["kind"] == "work":
                    record.update(start=item["start"], end=item["end"], label=item.get("shift_label", ""))
                    (bucket if bucket is not None else working).append(record)
                elif bucket is None:
                    record["status"] = (
                        item.get("custom_label") or item.get("custom_code")
                        if item["kind"] == "custom"
                        else reports.SCHEDULE_KIND_LABELS.get(item["kind"], item["kind"])
                    )
                    off.append(record)
        working.sort(key=lambda item: (item["start"], item["name"]))
        upcoming.sort(key=lambda item: (item["start"], item["name"]))
        off.sort(key=lambda item: item["name"])
        attendance, month_entries = self._month_attendance(store, month)
        balances = self._balance_summary(store, month)
        check = self._check_report(store, month, attendance, month_entries, balances)
        backups = store.backups()
        archive = {item["month"]: item for item in store.archive_months()}
        return {
            "date": day_text,
            "category": scheduling.attendance_day_category(day),
            "working": working,
            "off": off,
            "tomorrow": upcoming,
            "check": {"counts": check["counts"], "items": [
                item for item in check["items"] if item["severity"] != "info"
            ][:8]},
            "people": {"active": sum(1 for person in store.people() if person["active"]),
                       "planned": len({int(entry["employee_id"]) for entry in store.schedule_month(month)["entries"]})},
            "month": month,
            "locked": bool(archive.get(month, {}).get("locked")),
            "last_backup": backups[0] if backups else None,
            "backup_mirror": store.backup_mirror_folder(),
            "low_leave": [
                {"name": item["name"], "remaining": item["annual_leave_remaining_days"]}
                for item in balances["people"]
                if item["active"] and item["has_opening"] and item["annual_leave_remaining_days"] <= 2
            ][:6],
        }

    def _update_info(self, store) -> dict:
        settings = store._setting("update_settings", {})
        folder = settings.get("folder", "") if isinstance(settings, dict) else ""
        current = self.server.app_executable
        result = {"supported": current is not None, "folder": folder, "available": False}
        if current is None or not current.is_file():
            return result
        result["current"] = {
            "path": str(current), "modified": date.fromtimestamp(current.stat().st_mtime).isoformat(),
            "size": current.stat().st_size,
        }
        if not folder:
            return result
        candidate = Path(folder) / current.name
        if candidate.is_file() and candidate.resolve() != current.resolve():
            stat = candidate.stat()
            result["candidate"] = {
                "path": str(candidate), "modified": date.fromtimestamp(stat.st_mtime).isoformat(),
                "size": stat.st_size,
            }
            result["available"] = stat.st_mtime > current.stat().st_mtime + 1 or stat.st_size != current.stat().st_size
            result["schema_compatibility"] = self._update_schema_compatibility(store, current, candidate)
        return result

    @staticmethod
    def _read_update_schema_manifest(executable: Path) -> dict:
        manifest_path = Path(f"{executable}.schema.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Şema uyumluluk bildirimi bulunamadı veya okunamadı: {manifest_path.name}.") from exc
        fields = {"format", "version", "executable_sha256", "minimum_schema", "maximum_schema"}
        if not isinstance(manifest, dict) or set(manifest) != fields \
                or manifest["format"] != "vardiya-schema-compatibility" \
                or type(manifest["version"]) is not int or manifest["version"] != 1 \
                or not isinstance(manifest["executable_sha256"], str) \
                or not re.fullmatch(r"[a-f0-9]{64}", manifest["executable_sha256"]) \
                or type(manifest["minimum_schema"]) is not int or manifest["minimum_schema"] < 0 \
                or type(manifest["maximum_schema"]) is not int \
                or manifest["maximum_schema"] < manifest["minimum_schema"]:
            raise ValueError(f"Şema uyumluluk bildirimi geçersiz: {manifest_path.name}.")
        digest = hashlib.sha256()
        with executable.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != manifest["executable_sha256"]:
            raise ValueError(f"Şema bildirimi {executable.name} dosyasıyla eşleşmiyor.")
        return manifest

    def _update_schema_compatibility(self, store, current: Path, candidate: Path) -> dict:
        schema = int(store.connection.execute("PRAGMA user_version").fetchone()[0])
        try:
            manifests = (
                self._read_update_schema_manifest(current),
                self._read_update_schema_manifest(candidate),
            )
        except ValueError as exc:
            return {"compatible": False, "database_schema": schema, "reason": str(exc)}
        for path, manifest in zip((current, candidate), manifests):
            if not manifest["minimum_schema"] <= schema <= manifest["maximum_schema"]:
                return {
                    "compatible": False, "database_schema": schema,
                    "reason": f"{path.name} bu veritabanı şemasını desteklemiyor.",
                }
        return {"compatible": True, "database_schema": schema, "reason": ""}

    def _apply_update(self, rollback: bool = False) -> dict:
        import os
        import subprocess
        with self._store() as store:
            info = self._update_info(store)
            if rollback:
                current_path = self.server.app_executable
                previous = Path(f"{current_path}.onceki") if current_path else None
                if previous is None or not previous.is_file():
                    raise ValueError("Geri dönülecek önceki sürüm bulunamadı.")
                info = {"current": {"path": str(current_path)}, "candidate": {"path": str(previous)}}
            elif not info.get("available"):
                raise ValueError("Uygulanacak yeni sürüm bulunamadı.")
            current = Path(info["current"]["path"])
            candidate = Path(info["candidate"]["path"])
            compatibility = self._update_schema_compatibility(store, current, candidate)
            if not compatibility["compatible"]:
                raise ValueError(
                    "Veri şeması uyumluluk ön denetimi başarısız; EXE değiştirilmedi. "
                    + compatibility["reason"]
                )
            backup = store.create_backup("before-update")
            verified_backup = store.verify_backup(backup["id"])
            if not verified_backup.get("valid"):
                raise ValueError("Güncelleme öncesi güvenlik yedeği doğrulanamadı; EXE değiştirilmedi.")
            backup_connection = sqlite3.connect(store._backup_path(backup["id"]))
            try:
                backup_schema = int(backup_connection.execute("PRAGMA user_version").fetchone()[0])
            finally:
                backup_connection.close()
            if backup_schema != compatibility["database_schema"]:
                raise ValueError("Güvenlik yedeğinin şeması değişti; EXE değiştirilmedi.")
        current_manifest_path = Path(f"{current}.schema.json")
        candidate_manifest_path = Path(f"{candidate}.schema.json")
        previous = Path(f"{current}.onceki")
        previous_manifest_path = Path(f"{previous}.schema.json")
        current_manifest = self._read_update_schema_manifest(current)
        candidate_manifest = self._read_update_schema_manifest(candidate)
        operation_id = secrets.token_hex(8)
        status_path = Path(tempfile.gettempdir()) / f"vardiya-update-{operation_id}.json"
        script = _update_powershell_script(
            current, current_manifest_path, candidate, candidate_manifest_path,
            previous, previous_manifest_path, status_path, os.getpid(),
            current_manifest["executable_sha256"],
            hashlib.sha256(current_manifest_path.read_bytes()).hexdigest(),
            candidate_manifest["executable_sha256"],
            hashlib.sha256(candidate_manifest_path.read_bytes()).hexdigest(),
        )
        encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        subprocess.Popen(
            [
                "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                "-WindowStyle", "Hidden", "-EncodedCommand", encoded_script,
            ],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
        with self._store() as store, store.connection:
            store.record_change(
                "app.update", "Önceki sürüme dönülüyor" if rollback else "Uygulama güncellemesi başlatıldı",
                {"source": str(candidate), "backup": backup["id"]},
            )
        if self.server.request_exit is not None:
            self.server.request_exit()
        return {
            "started": True, "backup": backup["id"],
            "operation_id": operation_id, "status_path": str(status_path),
        }

    def _health_routes(self, method: str, path: str) -> bool:
        import health
        from version import APP_VERSION, WHATS_NEW
        if method == "GET" and path == "/api/app-info":
            executable = self.server.app_executable
            application_path = (
                executable.resolve().parent if executable
                else self.server.web_path.resolve().parent
            )
            self._json({
                "version": APP_VERSION,
                "runtime": "exe" if executable else "source",
                "executable_path": str(executable.resolve()) if executable else "",
                "application_path": str(application_path),
                "data_path": str(Path(self.server.data_path).resolve()),
                "branch": workspace_tools.active_branch(Path(self.server.data_path)),
            })
            return True
        if method == "GET" and path == "/api/diagnostics/errors":
            operations._require_admin(self)
            with self._store() as store:
                self._json({"events": store.technical_error_history()})
            return True
        if method == "GET" and path == "/api/health":
            with self._store() as store:
                result = health.check(store)
                info = store._setting("app_version", {})
                executable = self.server.app_executable
                previous = Path(f"{executable}.onceki") if executable else None
                result["rollback_compatibility"] = (
                    self._update_schema_compatibility(store, executable, previous)
                    if executable and executable.is_file() and previous and previous.is_file()
                    else {"compatible": False, "reason": "Önceki EXE veya şema bildirimi bulunamadı."}
                )
            result["version"] = APP_VERSION
            result["version_info"] = info if isinstance(info, dict) else {}
            result["rollback_available"] = bool(
                executable and previous and previous.is_file()
            )
            self._json(result)
            return True
        if method == "POST" and path == "/api/health/repair":
            self._read_json()
            with self._store() as store:
                result = health.repair(store)
            self._json(result)
            return True
        if method == "GET" and path == "/api/whats-new":
            with self._store() as store:
                info = store._setting("app_version", {})
            info = info if isinstance(info, dict) else {}
            self._json({"version": APP_VERSION, "items": WHATS_NEW, "unseen": bool(info.get("previous")) and not info.get("seen_whats_new")})
            return True
        if method == "POST" and path == "/api/whats-new/seen":
            self._read_json()
            with self._store() as store, store.connection:
                info = store._setting("app_version", {})
                info = info if isinstance(info, dict) else {"current": APP_VERSION}
                info["seen_whats_new"] = True
                store._write_setting("app_version", info)
            self._json({"ok": True})
            return True
        if path == "/api/ui-settings" and method in ("GET", "PUT"):
            import ui_settings
            with self._store() as store:
                if method == "GET":
                    result = ui_settings.load(store)
                else:
                    result = ui_settings.save(store, self._read_json())
            self._json({"settings": result})
            return True
        if path == "/api/export-name" and method == "GET":
            import ui_settings
            query = parse_qs(urlparse(self.path).query)
            kind = query.get("kind", ["schedule"])[0]
            if kind not in ("schedule", "attendance"):
                raise ValueError("Excel çıktı türü geçersiz.")
            month = scheduling.validate_month(query.get("month", [""])[0])
            week_text = query.get("week", ["0"])[0]
            week = int(week_text) if week_text.isdigit() else 0
            employee = query.get("employee", [""])[0][:80]
            with self._store() as store:
                prefs = ui_settings.load(store).get("export") or {}
            template = query.get("template", [None])[0]
            if template is None:
                template = prefs.get("filename_template", "")
            self._json({
                "filename": ui_settings.export_filename(template[:120], kind, month, week, employee, "xlsx"),
                "folder": prefs.get("last_folder", ""),
            })
            return True
        if path == "/api/export-preview" and method == "GET":
            import export_preview
            body, _ = self._export_workbook(parse_qs(urlparse(self.path).query))
            self._json({"html": export_preview.workbook_html(body)})
            return True
        if path == "/api/payroll-extras" and method in ("GET", "PUT"):
            import payroll_extras
            if method == "GET":
                month = scheduling.validate_month(parse_qs(urlparse(self.path).query).get("month", [""])[0])
            else:
                payload = self._read_json()
                if not isinstance(payload, dict) or set(payload) != {"month", "people"}:
                    raise ValueError("Bordro ekleri isteği geçersiz.")
                month = scheduling.validate_month(payload["month"])
            year = int(month[:4])
            with self._store() as store:
                if method == "PUT":
                    ids = {str(item["id"]) for item in store.people()}
                    payroll_extras.save(store, month, payload["people"], ids)
                history = []
                for number in range(1, int(month[5:]) + 1):
                    other = f"{year}-{number:02}"
                    other_attendance = self._month_attendance(store, other)[0]
                    history.append((other, payroll_extras.month_rows(other_attendance, payroll_extras.load(store, other))))
                extras = payroll_extras.load(store, month)
            self._json({
                "month": month, "people": extras, "rows": history[-1][1],
                "comp_leave_code": payroll_extras.COMP_LEAVE_CODE,
                "comp_balance": payroll_extras.comp_leave_balance(history),
            })
            return True
        if path == "/api/payroll/balancing" and method == "GET":
            import payroll_extras
            query = parse_qs(urlparse(self.path).query)
            month = scheduling.validate_month(query.get("month", [""])[0])
            weeks_text = query.get("weeks", [""])[0]
            with self._store() as store:
                configured = int(store.compliance_settings().get("balancing_weeks") or 0)
                weeks = int(weeks_text) if weeks_text.isdigit() else (configured or 4)
                first, last = payroll_extras.balancing_period(month, weeks)
                attendances = {
                    other: self._month_attendance(store, other)[0]
                    for other in payroll_extras.months_between(first, last)
                }
            self._json(payroll_extras.balancing_report(month, weeks, attendances))
            return True
        if path in ("/api/warning-rules", "/api/warning-rules/ignore") and method in ("GET", "PUT", "POST"):
            import warning_rules
            with self._store() as store:
                if method == "GET":
                    rules = warning_rules.load(store)
                elif path == "/api/warning-rules/ignore" and method == "POST":
                    payload = self._read_json()
                    if not isinstance(payload, dict):
                        raise ValueError("Uyarı kuralı isteği geçersiz.")
                    rules = warning_rules.ignore(store, payload.get("scope"), payload.get("item"))
                elif path == "/api/warning-rules" and method == "PUT":
                    rules = warning_rules.save(store, self._read_json())
                else:
                    raise ValueError("Uyarı kuralı isteği geçersiz.")
            self._json({"rules": rules})
            return True
        if path == "/api/template-library" and method in ("GET", "PUT"):
            import ui_settings
            with self._store() as store:
                if method == "GET":
                    items = ui_settings.load_library(store)
                else:
                    payload = self._read_json()
                    if set(payload) != {"items"}:
                        raise ValueError("Şablon kitaplığı isteği geçersiz.")
                    items = ui_settings.save_library(store, payload["items"])
            self._json({"items": items, "ready": ui_settings.READY_LIBRARY})
            return True
        if method == "GET" and path == "/api/personnel-overview":
            fields = ("title", "phone", "hire_date", "exit_date", "payroll_code", "home_section",
                      "color_tag", "staff_type", "special_status", "notes", "skills",
                      "experienced_skills", "training_skills", "documents", "duty_required_fields")
            card_field_labels = {
                "home_section": "Bölüm", "payroll_code": "Sicil no", "skills": "Yetkinlik",
                "experienced_skills": "Deneyimli görev", "training_skills": "Eğitim ihtiyacı",
                "documents": "Belge",
            }
            with self._store() as store:
                profiles = store.employee_profiles()
                people = []
                for person in store.people():
                    profile = profiles.get(str(person["id"]), {})
                    required_fields = profile.get("duty_required_fields", [])
                    missing = ["Görev"] if not str(profile.get("title", "") or "").strip() else []
                    for field in required_fields if isinstance(required_fields, list) else []:
                        value = profile.get(field)
                        if field == "documents":
                            present = isinstance(value, list) and bool(value)
                        elif isinstance(value, list):
                            present = bool(value)
                        else:
                            present = bool(str(value or "").strip())
                        if not present:
                            missing.append(card_field_labels[field])
                    people.append(person | {field: profile.get(field, "" if field != "special_status" else [])
                                            for field in fields}
                                  | {"card_missing": missing, "card_required_count": 1 + len(required_fields)})
            self._json({"people": people})
            return True
        if method == "GET" and path == "/api/personnel-assets":
            import personnel_assets
            query = parse_qs(urlparse(self.path).query)
            raw_id = query.get("employee_id", [""])[0]
            if not raw_id.isdigit():
                raise ValueError("Personel seçimi geçersiz.")
            with self._store() as store:
                assets = personnel_assets.list_assets(store, int(raw_id))
            self._json({"assets": assets})
            return True
        if method == "POST" and path == "/api/personnel/bulk":
            payload = self._read_json()
            ids, action = payload.get("ids"), payload.get("action")
            if (not set(payload) <= {"ids", "action", "value", "force"} or not isinstance(ids, list)
                    or not 1 <= len(ids) <= 500
                    or any(not isinstance(item, int) or isinstance(item, bool) for item in ids)
                    or action not in ("activate", "deactivate", "delete", "color", "section", "staff_type")):
                raise ValueError("Toplu personel işlemi geçersiz.")
            done, blocked = [], []
            with self._store() as store:
                for employee_id in ids:
                    if action in ("activate", "deactivate"):
                        store.set_person_active(employee_id, action == "activate")
                    elif action == "delete":
                        try:
                            store.delete_person(employee_id, bool(payload.get("force", False)))
                        except PermissionError:
                            blocked.append(employee_id)
                            continue
                    else:
                        field = {"color": "color_tag", "section": "home_section", "staff_type": "staff_type"}[action]
                        store.save_employee_profile(employee_id, {field: payload.get("value", "")})
                    done.append(employee_id)
            self._json({"done": done, "blocked": blocked})
            return True
        if method == "POST" and path == "/api/update/rollback":
            self._read_json()
            self._json(self._apply_update(rollback=True))
            return True
        return False

    def _month_end_check(self, store, month: str) -> dict[str, Any]:
        import health
        year, number = map(int, month.split("-"))
        first = date(year, number, 1)
        last = date(year, number, calendar.monthrange(year, number)[1])
        cutoff = min(last, date.today())
        steps: list[dict[str, Any]] = []

        def step(step_id: str, title: str, status: str, detail: str, items: list[str] | None = None) -> None:
            steps.append({"id": step_id, "title": title, "status": status, "detail": detail, "items": (items or [])[:12]})

        issues = [item for item in health.check(store)["issues"] if item.get("level") in ("error", "warning")
                  and item.get("code") not in ("no-backup", "old-backup")]
        errors = sum(item.get("level") == "error" for item in issues)
        step("health", "Veri sağlığı", "error" if errors else ("warning" if issues else "ok"),
             f"{len(issues)} sorun bulundu." if issues else "Veritabanında sorun yok.",
             [item.get("message", "") for item in issues])

        people = store.people()
        names = {person["id"]: person["name"] for person in people}
        entries = store.schedule_month(month)["entries"]
        planned = {day for entry in entries for day in (entry.get("days") or {}) if day.startswith(month)}
        unplanned = [first + timedelta(days=offset) for offset in range((last - first).days + 1)
                     if (first + timedelta(days=offset)).isoformat() not in planned]
        if not entries:
            step("schedule", "Vardiya planı", "warning", "Bu ay için kayıtlı vardiya planı yok.")
        elif unplanned:
            step("schedule", "Vardiya planı", "warning", f"{len(unplanned)} günde hiç vardiya yazılmamış.",
                 [day.strftime("%d.%m.%Y") for day in unplanned])
        else:
            step("schedule", "Vardiya planı", "ok", "Ayın her gününde vardiya kaydı var.")

        attendance, _, month_data = self._attendance_for(store, month, people=people)
        shown = {row["employee_id"] for row in attendance["rows"]}
        available = [person for person in people if person["active"] and person["id"] not in shown]
        missing = self._attendance_missing(store, month, available, month_data)
        step("attendance-people", "Puantajdaki personel", "warning" if missing else "ok",
             f"{len(missing)} aktif personel puantajda yok." if missing else "Aktif personelin tamamı puantajda.",
             [item["name"] for item in missing])

        profiles = store.employee_profiles()
        empty: list[str] = []
        empty_total = 0
        if cutoff >= first:
            for row in attendance["rows"]:
                profile = profiles.get(str(row["employee_id"]), {})
                hire, leave = profile.get("hire_date") or "", profile.get("exit_date") or ""
                codes = row.get("codes") or {}
                count = sum(1 for day in attendance["dates"]
                            if day <= cutoff.isoformat() and not (hire and day < hire) and not (leave and day > leave)
                            and not str(codes.get(day) or "").strip())
                if count:
                    empty_total += count
                    empty.append(f"{row['name']}: {count} gün")
        if cutoff < first:
            step("attendance-cells", "Puantaj boş günleri", "warning", "Dönem henüz başlamadı.")
        else:
            step("attendance-cells", "Puantaj boş günleri", "warning" if empty_total else "ok",
                 (f"{cutoff.strftime('%d.%m.%Y')} tarihine kadar {empty_total} boş puantaj hücresi var."
                  if empty_total else "Geçmiş günlerin tamamında puantaj kodu var."), empty)

        pending = [item for item in linked_leave._month_requests(store.connection, month, approved_only=False)
                   if item["status"] == "pending"]
        legacy = [item for item in store.leave_requests()
                  if isinstance(item, dict) and item.get("status") == "pending"
                  and str(item.get("start", "")) <= last.isoformat() and str(item.get("end", "")) >= first.isoformat()]
        pending_items = [f"{names.get(item.get('employee_id'), 'Silinmiş personel')}: "
                         f"{date.fromisoformat(item['start']).strftime('%d.%m')}–{date.fromisoformat(item['end']).strftime('%d.%m')}"
                         for item in pending + legacy]
        step("leave", "Onay bekleyen izinler", "warning" if pending_items else "ok",
             f"{len(pending_items)} izin talebi onay bekliyor." if pending_items else "Onay bekleyen izin yok.",
             pending_items)

        backups = store.backups()
        if backups:
            latest = datetime.fromisoformat(backups[0]["created_at"])
            age_hours = (datetime.now(timezone.utc) - latest).total_seconds() / 3600
            step("backup", "Yedek", "ok" if age_hours <= 24 else "warning",
                 f"Son yedek {latest.astimezone().strftime('%d.%m.%Y %H:%M')} tarihinde alındı."
                 + ("" if age_hours <= 24 else " Dönemi kapatmadan önce yeni yedek alın."))
        else:
            step("backup", "Yedek", "warning", "Henüz yedek alınmamış.")

        archived = any(item["month"] == month for item in store.archive_months())
        locked = month in store.locked_periods()
        step("lock", "Dönem kilidi", "ok" if locked else "warning",
             "Dönem kilitli; kayıtlar değiştirilemez." if locked
             else ("Dönem açık. Kontroller bitince kilitleyin." if archived
                   else "Bu ay için kayıt olmadığından dönem kilitlenemez."))
        return {"month": month, "steps": steps, "locked": locked, "can_lock": archived and not locked,
                "errors": sum(item["status"] == "error" for item in steps),
                "warnings": sum(item["status"] == "warning" for item in steps)}

    def _extra_get(self, path: str, query) -> bool:
        if path == "/api/month-end-check":
            month = scheduling.validate_month(query.get("month", [date.today().strftime("%Y-%m")])[0])
            with self._store() as store:
                self._json(self._month_end_check(store, month))
            return True
        if path == "/api/workspace/branches":
            self._json(workspace_tools.branches_info(Path(self.server.data_path)))
            return True
        if path == "/api/workspace/mobile-diagnostics":
            self._json(self.server.mobile_diagnostics)
            return True
        if path == "/api/workspace/phone":
            self._json(self._phone_server().status())
            return True
        if path == "/api/dashboard":
            raw = query.get("date", [""])[0] or date.today().isoformat()
            try:
                day = date.fromisoformat(raw)
            except ValueError as exc:
                raise ValueError("Tarih geçersiz.") from exc
            with self._store() as store:
                self._json(self._dashboard(store, day))
            return True
        if path == "/api/handover":
            start = query.get("from", [""])[0]
            end = query.get("to", [""])[0] or start
            for value in (start, end):
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError("Tarih YYYY-AA-GG biçiminde olmalıdır.") from exc
            if end < start:
                raise ValueError("Bitiş tarihi başlangıçtan önce olamaz.")
            with self._store() as store:
                self._json({"notes": store.handover_notes(start, end)})
            return True
        if path == "/api/duty-board":
            raw = query.get("date", [""])[0] or date.today().isoformat()
            try:
                day = date.fromisoformat(raw)
            except ValueError as exc:
                raise ValueError("Tarih YYYY-AA-GG biçiminde olmalıdır.") from exc
            with self._store() as store:
                self._json(self._duty_board(store, day))
            return True
        if path == "/api/person-tags":
            with self._store() as store:
                self._json({"tags": store.person_tags()})
            return True
        if path == "/api/employee-profile":
            raw_id = query.get("employee_id", [""])[0]
            if not raw_id.isdigit():
                raise ValueError("Personel seçimi geçersiz.")
            employee_id = int(raw_id)
            with self._store() as store:
                person = next((item for item in store.people() if item["id"] == employee_id), None)
                if person is None:
                    raise ValueError("Personel bulunamadı.")
                fields = query.get("fields", [])
                if fields:
                    if fields != ["title"]:
                        raise ValueError("İstenen personel alanı desteklenmiyor.")
                    self._json({"title": store.employee_profile(employee_id).get("title", "")})
                    return True
                month = date.today().strftime("%Y-%m")
                balance = next(
                    (item for item in self._balance_summary(store, month)["people"]
                     if item["employee_id"] == employee_id), None,
                )
                swaps = [
                    item for item in store.shift_swaps()
                    if employee_id in (item.get("first_id"), item.get("second_id"))
                ][:10]
                self._json({
                    "person": person, "profile": store.employee_profile(employee_id),
                    "balance": balance, "swaps": swaps,
                    "history": store.person_history(employee_id, person["name"]),
                    "months": store.matching_archive_months("schedule", "", person["name"], "", ""),
                })
            return True
        if path == "/api/schedule-sections":
            with self._store():
                pass
            self._json({"sections": scheduling.schedule_sections(), "limits": {
                "max_rows": scheduling.MAX_SECTION_ROWS, "max_custom": scheduling.MAX_CUSTOM_SECTIONS,
                "extra_rows": scheduling.EXTRA_SECTION_ROWS, "max_personnel": scheduling.MAX_MONTH_PERSONNEL,
            }})
            return True
        if path == "/api/holidays":
            raw_year = query.get("year", [str(date.today().year)])[0]
            if not raw_year.isdigit() or not 2000 <= int(raw_year) <= 2100:
                raise ValueError("Yıl seçimi geçersiz.")
            year = int(raw_year)
            with self._store() as store:
                self._json({
                    "year": year, "builtin": scheduling.builtin_holidays(year),
                    "custom": store.custom_holidays(), "labels": scheduling.HOLIDAY_KIND_LABELS,
                    "religious_years": list(scheduling.RELIGIOUS_CALENDAR_YEARS),
                    "religious_known": scheduling.RELIGIOUS_CALENDAR_YEARS[0] <= year <= scheduling.RELIGIOUS_CALENDAR_YEARS[1],
                })
            return True
        if path == "/api/swaps":
            with self._store() as store:
                self._json({"swaps": store.shift_swaps()[:200]})
            return True
        if path == "/api/search":
            text = query.get("q", [""])[0].strip()
            if len(text) > 60:
                raise ValueError("Arama metni çok uzun.")
            key = _search_key(text)
            with self._store() as store:
                matches = [person for person in store.people() if key and key in _search_key(person["name"])][:8]
                self._json({"results": [
                    {**person, "months": store.matching_archive_months("schedule", "", person["name"], "", "")[:24]}
                    for person in matches
                ]})
            return True
        if path == "/api/backup-mirror":
            with self._store() as store:
                from health import cloud_sync_root
                self._json({
                    "folder": store.backup_mirror_folder(), "suggested": store.default_mirror_folder(),
                    "local_suggested": store.local_mirror_folder(),
                    "database_synced": cloud_sync_root(store.path),
                })
            return True
        if path == "/api/update":
            with self._store() as store:
                self._json(self._update_info(store))
            return True
        return self._controls_get(path, query)

    def _month_revisions(self, store, month: str) -> dict[str, str]:
        return {
            "schedule": store.schedule_month(month)["revision"],
            "attendance": store.attendance_month(month)["revision"],
        }

    def _pdks_result(self, store, month: str, tolerance: int) -> dict:
        saved = store._setting("pdks_records", {}).get(month)
        if not saved:
            return {"month": month, "imported": None}
        result = controls.pdks_compare(
            month, store.schedule_month(month)["entries"], store.people(), saved["records"], tolerance,
        )
        result["imported"] = {key: saved[key] for key in ("file", "imported_at", "actor")}
        result["source_health"] = saved.get("source_health")
        result["punch_records"] = saved["records"]
        return result

    def _controls_get(self, path: str, query) -> bool:
        if path == "/api/analytics/details":
            month = scheduling.validate_month(query.get("month", [""])[0])
            metric = query.get("metric", [""])[0]
            with self._store() as store:
                store.connection.execute("BEGIN")
                attendance, _ = self._month_attendance(store, month)
                result = {
                    "month": month,
                    "metric": metric,
                    "revisions": self._month_revisions(store, month),
                    "items": analytics.metric_details(attendance, metric),
                }
            self._json(result)
            return True
        if path == "/api/analytics":
            month = scheduling.validate_month(query.get("month", [""])[0])
            raw_hours = query.get("hours", [str(analytics.HOURS_PER_MONTH)])[0]
            try:
                hours = float(raw_hours)
            except ValueError:
                raise ValueError("Aylık saat geçersiz.") from None
            summary = self._analytics_summary(month, hours)
            summary.pop("overtime")
            summary["warnings"]["items"] = summary["warnings"]["items"][:20]
            self._json(summary)
            return True
        if path == "/api/planning/fairness":
            first = scheduling.validate_month(query.get("from", [""])[0])
            last = scheduling.validate_month(query.get("to", [""])[0])
            months, cursor = [], date.fromisoformat(f"{first}-01")
            while cursor.strftime("%Y-%m") <= last:
                months.append(cursor.strftime("%Y-%m"))
                if len(months) > 12:
                    raise ValueError("Adil dağılım en fazla 12 ay için hesaplanır.")
                cursor = (cursor + scheduling.timedelta(days=32)).replace(day=1)
            if not months:
                raise ValueError("Başlangıç ayı bitiş ayından sonra olamaz.")
            with self._store() as store:
                names = {person["id"]: person["name"] for person in store.people()}
                data = {month: store.schedule_month(month)["entries"] for month in months}
                profiles = store.employee_profiles()
            self._json(planning.fairness(data, names, profiles))
            return True
        if path == "/api/planning/borrowed":
            month = scheduling.validate_month(query.get("month", [""])[0])
            with self._store() as store:
                names = {person["id"]: person["name"] for person in store.people()}
                entries = store.schedule_month(month)["entries"]
                profiles = store.employee_profiles()
            self._json({"month": month, "people": planning.borrowed_staff(month, entries, names, profiles)})
            return True
        if path == "/api/planning-settings":
            with self._store() as store:
                settings = store.planning_settings()
                skills = sorted({skill for profile in store.employee_profiles().values()
                                 for skill in profile.get("skills") or []}, key=str.casefold)
                sections = sorted({(entry.get("section") or "").strip()
                                   for month in scheduling.adjacent_months(date.today().strftime("%Y-%m"))
                                   for entry in store.schedule_month(month)["entries"]} - {""}, key=str.casefold)
            self._json({**settings, "known_skills": skills, "known_sections": sections})
            return True
        if path == "/api/leave-requests":
            month = scheduling.validate_month(query.get("month", [""])[0])
            with self._store() as store:
                names = {person["id"]: person["name"] for person in store.people()}
                requests = store.leave_requests() + linked_leave.calendar_requests(store, month)
                limit = store.planning_settings()["daily_limit"]
                calendar = planning.leave_calendar(
                    month, requests, names, limit,
                    section_for=lambda employee_id, day: operations.effective_home_section(
                        store, employee_id, day,
                    ),
                )
            self._json(calendar)
            return True
        if path == "/api/payroll/sgk":
            month = scheduling.validate_month(query.get("month", [""])[0])
            attendance, sgk, overtime, settings = self._payroll_data(month)
            self._json({"month": month, "sgk": sgk, "overtime": overtime,
                        "daily_limit_hours": settings.get("daily_limit_hours", 8),
                        "break_rules": settings.get("break_rules", [])})
            return True
        if path == "/api/sick-reports":
            month = scheduling.validate_month(query.get("month", [""])[0])
            with self._store() as store:
                attendance, _ = self._month_attendance(store, month)
                records = store._setting("sick_reports", [])
            self._json(payroll.sick_report_summary(month, records, attendance))
            return True
        if path == "/api/leave/termination":
            raw_id = query.get("employee_id", [""])[0]
            exit_day = payroll._date(query.get("exit_date", [""])[0])
            if not raw_id.isdigit() or not exit_day:
                raise ValueError("Personel ve çıkış tarihi seçin.")
            with self._store() as store:
                balance = self._balance_summary(store, exit_day.strftime("%Y-%m"))
                person = next((item for item in balance["people"] if item["employee_id"] == int(raw_id)), None)
                if person is None:
                    raise ValueError("Personel bulunamadı.")
                profile = store.employee_profile(int(raw_id))
            pay = payroll.leave_pay(person["annual_leave_remaining_days"], float(profile.get("monthly_wage") or 0))
            self._json({**pay, "name": person["name"], "has_opening": person["has_opening"],
                        "monthly_wage": profile.get("monthly_wage") or 0, "exit_date": exit_day.isoformat()})
            return True
        if path == "/api/compliance-settings":
            with self._store() as store:
                self._json({**store.compliance_settings(), "statuses": compliance.SPECIAL_STATUSES})
            return True
        if path == "/api/pdks":
            month = scheduling.validate_month(query.get("month", [""])[0])
            raw = query.get("tolerance", ["5"])[0]
            tolerance = int(raw) if raw.isdigit() and int(raw) <= 120 else 5
            with self._store() as store:
                self._json(self._pdks_result(store, month, tolerance))
            return True
        if path == "/api/schedule/plan-diff":
            month = scheduling.validate_month(query.get("month", [""])[0])
            with self._store() as store:
                published = store._setting("published_plans", {}).get(month)
                comparison = operations.publish_diff(store, month)
                self._json({
                    "month": month,
                    "published": {key: published[key] for key in ("at", "actor")} if published else None,
                    "changes": comparison["changes"],
                    "revision": comparison["revision"],
                })
            return True
        if path == "/api/approvals":
            month = scheduling.validate_month(query.get("month", [""])[0])
            with self._store() as store:
                record = store._setting("approvals", {}).get(month)
                self._json(controls.approval_status(month, record, self._month_revisions(store, month)))
            return True
        return False

    def _controls_write(self, method: str, path: str) -> bool:
        if method == "PUT" and path == "/api/compliance-settings":
            payload = self._read_json()
            with self._store() as store:
                self._json(store.save_compliance_settings(payload))
            return True
        if method == "PUT" and path == "/api/planning-settings":
            payload = self._read_json()
            with self._store() as store:
                self._json(store.save_planning_settings(payload))
            return True
        if method != "POST":
            return False
        if path == "/api/leave-requests":
            payload = self._read_json()
            with self._store() as store, store.connection:
                records = store.leave_requests()
                action = payload.get("action")
                if action == "add" and set(payload) == {"action", "request"}:
                    record = planning.normalize_leave_request(payload["request"], {item["id"] for item in store.people()})
                    record["id"] = secrets.token_hex(6)
                    record["created_at"] = scheduling.datetime.now().isoformat(timespec="seconds")
                    records.append(record)
                    store.record_change("leave_request.add", f"İzin talebi eklendi ({record['start']} – {record['end']})",
                                        {"employee_id": record["employee_id"], "code": record["code"]})
                elif action in ("status", "delete") and set(payload) == ({"action", "id", "status"} if action == "status" else {"action", "id"}):
                    target = next((item for item in records if item.get("id") == payload["id"]), None)
                    if target is None:
                        raise ValueError("İzin talebi bulunamadı.")
                    if action == "delete":
                        records.remove(target)
                    else:
                        if payload["status"] not in planning.LEAVE_REQUEST_STATUSES:
                            raise ValueError("Talep durumu geçersiz.")
                        target["status"] = payload["status"]
                    store.record_change(f"leave_request.{action}", "İzin talebi güncellendi",
                                        {"id": payload["id"], "status": payload.get("status")})
                else:
                    raise ValueError("İzin talebi isteği geçersiz.")
                store._write_setting("leave_requests", records[-3000:])
            self._json({"ok": True})
            return True
        if path == "/api/import/attendance-excel":
            payload = self._read_json(12_000_000)
            if not {"month", "filename", "content", "apply"} <= set(payload) <= {
                "month", "filename", "content", "apply", "revision", "include_codes",
            }:
                raise ValueError("Puantaj içe aktarma isteği geçersiz.")
            month = scheduling.validate_month(payload["month"])
            try:
                content = base64.b64decode(payload["content"], validate=True)
            except (binascii.Error, TypeError, ValueError) as exc:
                raise ValueError("Dosya içeriği okunamadı.") from exc
            if not str(payload["filename"]).lower().endswith((".xlsx", ".xlsm")):
                raise ValueError("Yalnızca .xlsx / .xlsm puantaj dosyaları içe aktarılabilir.")
            with self._store() as store:
                people = store.people()
                result = planning.parse_attendance_workbook(
                    content, month, people, payload.get("include_codes"),
                )
                current = store.attendance_month(month)
                existing = sum(len(values) for values in current["overrides"].values())
                result["existing_overrides"] = existing
                if payload["apply"] is True:
                    if payload.get("revision") != current["revision"]:
                        self._json({"error": "Bu ay başka bir pencerede değiştirildi. Yenileyip tekrar deneyin."}, 409)
                        return True
                    merged = {day: dict(values) for day, values in current["overrides"].items()}
                    for day, values in result["overrides"].items():
                        merged.setdefault(day, {}).update(values)
                    normalized = scheduling.normalize_attendance_overrides(month, merged, people)
                    revision = store.save_attendance_month(month, normalized, current["deductions"], current["revision"])
                    if revision is None:
                        self._json({"error": "Bu ay başka bir pencerede değiştirildi. Yenileyip tekrar deneyin."}, 409)
                        return True
                    store.record_change("attendance.import_excel", f"{month} puantajı Excel'den içe aktarıldı",
                                        {"month": month, "file": str(payload["filename"])[:120],
                                         "codes": result["code_count"]})
                    result["revision"] = revision
                else:
                    result["revision"] = current["revision"]
            result["applied"] = payload["apply"] is True
            self._json(result)
            return True
        if path == "/api/sick-reports":
            payload = self._read_json()
            with self._store() as store, store.connection:
                records = store._setting("sick_reports", [])
                if payload.get("action") == "delete" and set(payload) == {"action", "id"}:
                    kept = [item for item in records if item.get("id") != payload["id"]]
                    if len(kept) == len(records):
                        raise ValueError("Rapor kaydı bulunamadı.")
                    records = kept
                    store.record_change("sick_report.delete", "Rapor kaydı silindi", {"id": payload["id"]})
                elif payload.get("action") == "add" and set(payload) == {"action", "report"}:
                    record = payroll.normalize_sick_report(payload["report"], {item["id"] for item in store.people()})
                    record["id"] = secrets.token_hex(6)
                    records.append(record)
                    store.record_change(
                        "sick_report.add", f"Rapor kaydı eklendi ({record['start']} – {record['end']})",
                        {"employee_id": record["employee_id"], "report_no": record["report_no"]},
                    )
                else:
                    raise ValueError("Rapor isteği geçersiz.")
                store._write_setting("sick_reports", records[-2000:])
            self._json({"ok": True})
            return True
        if path == "/api/leave/carry-over":
            payload = self._read_json()
            year = payload.get("year")
            if (set(payload) != {"year", "include_entitlement"} or not isinstance(year, int)
                    or isinstance(year, bool) or not 2000 <= year <= 2099):
                raise ValueError("Devir isteği geçersiz.")
            with self._store() as store:
                balance = self._balance_summary(store, f"{year}-12")
                existing = store.balance_openings(year + 1)
                profiles = store.employee_profiles()
                done, skipped = [], []
                for person in balance["people"]:
                    if not person["has_opening"] or person["employee_id"] in existing:
                        skipped.append(person["name"])
                        continue
                    days = person["annual_leave_remaining_days"]
                    earned = 0
                    profile = profiles.get(str(person["employee_id"]), {})
                    hired = payroll._date(profile.get("hire_date"))
                    if payload["include_entitlement"] and hired:
                        anniversary = hired.replace(year=year + 1) if (hired.month, hired.day) != (2, 29) else date(year + 1, 3, 1)
                        try:
                            right = reports.leave_entitlement(hired.isoformat(), profile.get("birth_date", ""), anniversary)
                        except ValueError:
                            right = None
                        earned = right["days"] if right else 0
                    store.save_balance_opening(person["employee_id"], year + 1, round(days + earned, 2), 0.0)
                    done.append({"name": person["name"], "carried": days, "earned": earned, "opening": round(days + earned, 2)})
            self._json({"year": year + 1, "done": done, "skipped": skipped})
            return True
        if path == "/api/pdks/import":
            payload = self._read_json(12_000_000)
            if set(payload) != {"month", "filename", "content", "tolerance"}:
                raise ValueError("PDKS içe aktarma isteği geçersiz.")
            month = scheduling.validate_month(payload["month"])
            tolerance = payload["tolerance"]
            if not isinstance(tolerance, int) or isinstance(tolerance, bool) or not 0 <= tolerance <= 120:
                raise ValueError("Tolerans 0–120 dakika olmalıdır.")
            if not isinstance(payload["filename"], str) or not isinstance(payload["content"], str):
                raise ValueError("PDKS dosyası geçersiz.")
            try:
                content = base64.b64decode(payload["content"], validate=True)
            except binascii.Error as exc:
                raise ValueError("PDKS dosyası okunamadı.") from exc
            source_health = {}
            try:
                records = controls.parse_pdks(payload["filename"], content, health=source_health, month=month)
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError("PDKS dosyası okunamadı; .xlsx veya .csv kaydedip tekrar deneyin.") from exc
            with self._store() as store:
                with store.connection:
                    saved = store._setting("pdks_records", {})
                    saved[month] = {
                        "file": Path(payload["filename"]).name[:120],
                        "imported_at": scheduling.datetime.now().isoformat(timespec="minutes"),
                        "actor": store.user_name(), "records": records, "source_health": source_health,
                    }
                    for old in sorted(saved)[:-6]:
                        saved.pop(old)
                    store._write_setting("pdks_records", saved)
                    store.record_change(
                        "pdks.import", f"{month} PDKS verisi alındı ({len(records)} kayıt)",
                        {"month": month, "file": saved[month]["file"]},
                    )
                self._json(self._pdks_result(store, month, tolerance))
            return True
        if path == "/api/pdks/clear":
            payload = self._read_json()
            month = scheduling.validate_month(payload.get("month", "") if set(payload) == {"month"} else "")
            with self._store() as store, store.connection:
                saved = store._setting("pdks_records", {})
                saved.pop(month, None)
                store._write_setting("pdks_records", saved)
                store.record_change("pdks.clear", f"{month} PDKS verisi silindi", {"month": month})
            self._json({"month": month, "imported": None})
            return True
        if path == "/api/schedule/publish":
            payload = self._read_json()
            if set(payload) != {"month", "revision"} or not isinstance(payload["revision"], str):
                raise ValueError("Yayın için önizlenen kayıt sürümü gereklidir.")
            month = scheduling.validate_month(payload["month"])
            with self._store() as store:
                result = operations.publish_in_transaction(store, month, payload["revision"])
                published = store._setting("published_plans", {})[month]
            self._json({**result, "published": {"at": published["at"], "actor": published["actor"]}})
            return True
        if path == "/api/approvals":
            payload = self._read_json()
            if set(payload) != {"month", "step", "name"}:
                raise ValueError("Onay isteği geçersiz.")
            month = scheduling.validate_month(payload["month"])
            with self._store() as store:
                revisions = self._month_revisions(store, month)
                with store.connection:
                    approvals = store._setting("approvals", {})
                    record = controls.advance_approval(
                        approvals.get(month), payload["step"], payload["name"], revisions,
                    )
                    if record:
                        approvals[month] = record
                    else:
                        approvals.pop(month, None)
                    store._write_setting("approvals", approvals)
                    label = dict(controls.APPROVAL_STEPS).get(payload["step"], "sıfırlandı")
                    store.record_change(
                        "attendance.approval", f"{month} puantajı: {label}"
                        + (f" — {' '.join(str(payload['name']).split())}" if record else ""),
                        {"month": month, "step": payload["step"]},
                    )
                self._json(controls.approval_status(month, record, revisions))
            return True
        return False

    def _restart_app(self) -> bool:
        """Masaüstü uygulamasını kapatıp aynı programla yeniden açar."""
        import subprocess
        executable = self.server.app_executable
        if executable is None or self.server.request_exit is None or os.name != "nt":
            return False
        script = Path(tempfile.gettempdir()) / f"vardiya-yeniden-{secrets.token_hex(4)}.bat"
        script.write_text(
            "@echo off\r\n"
            f":wait\r\ntasklist /FI \"PID eq {os.getpid()}\" | find \"{os.getpid()}\" >nul\r\n"
            "if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)\r\n"
            f"start \"\" \"{executable}\"\r\n"
            "del \"%~f0\"\r\n",
            encoding="mbcs",
        )
        subprocess.Popen(["cmd", "/c", str(script)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         close_fds=True)
        self.server.request_exit()
        return True

    def _phone_server(self):
        if self.server.phone is None:
            data_path = Path(self.server.data_path)
            self.server.phone = workspace_tools.PhoneViewServer(data_path, lambda: Store(data_path), self.server.lock)
        return self.server.phone

    def _extra_write(self, method: str, path: str) -> bool:
        if method == "POST" and path == "/api/analytics/chart-note":
            payload = self._read_json()
            if not isinstance(payload, dict) or set(payload) != {
                "month", "report_version", "chart_key", "text",
            }:
                raise ValueError("Grafik açıklaması isteği geçersiz.")
            month = scheduling.validate_month(payload["month"])
            report_version = payload["report_version"]
            if not isinstance(report_version, str) or not re.fullmatch(
                    r"manager-v1-[0-9a-f]{24}", report_version):
                raise ValueError("Rapor sürümü geçersiz.")
            chart_key = payload["chart_key"]
            if not isinstance(chart_key, str) or chart_key not in analytics.MANAGER_CHART_NOTE_KEYS:
                raise ValueError("Grafik seçimi geçersiz.")
            text = payload["text"]
            if not isinstance(text, str) or not text.strip() or len(text.strip()) > 280:
                raise ValueError("Açıklama 1-280 karakter olmalıdır.")
            text = text.strip()
            with self._store() as store:
                store.connection.execute("BEGIN IMMEDIATE")
                summary = self._analytics_summary_from_store(store, month)
                current_version = summary["report_context"]["report_version"]
                if current_version != report_version:
                    self._json({
                        "error": "Rapor verisi değişti. Güncel raporu yeniden açıp açıklamayı orada kaydedin.",
                        "report_version": current_version,
                    }, 409)
                    return True
                note = store.save_manager_chart_note(month, report_version, chart_key, text)
            self._json({
                "month": month, "report_version": report_version, "chart_key": chart_key, "note": note,
            })
            return True
        if method == "POST" and path == "/api/workspace/settings-import/preview":
            payload = self._read_json()
            if set(payload) - {"data", "keys"} or "data" not in payload:
                raise ValueError("Ayar içe aktarma önizlemesi geçersiz.")
            with self._store() as store:
                self._json(workspace_tools.preview_settings_import(
                    store, payload["data"], payload.get("keys"),
                ))
            return True
        if method == "POST" and path == "/api/workspace/settings-import/backup":
            if self._read_json() != {}:
                raise ValueError("Ayar aktarımı yedeği isteği geçersiz.")
            with self._store() as store:
                backup = store.create_backup("before-settings-import")
                if not store.verify_backup(backup["id"]).get("valid"):
                    raise ValueError("Ayar aktarımı öncesi güvenlik yedeği doğrulanamadı.")
                self._json({"backup": backup["id"]})
            return True
        if method == "POST" and path == "/api/workspace/settings-import":
            payload = self._read_json()
            if set(payload) - {"data", "keys", "backup"} or "data" not in payload:
                raise ValueError("Ayar içe aktarma isteği geçersiz.")
            staged_backup = payload.get("backup")
            keys = payload.get("keys")
            if staged_backup is not None and (
                not isinstance(staged_backup, str) or not isinstance(keys, list) or len(keys) != 1
            ):
                raise ValueError("Aşamalı ayar aktarımı için doğrulanmış yedek ve tek bir grup seçilmelidir.")
            with self._store() as store:
                result = workspace_tools.import_settings(
                    store, payload["data"], keys, backup_id=staged_backup,
                )
            self._json(result)
            return True
        if method == "POST" and path == "/api/workspace/sample-data":
            payload = self._read_json()
            if set(payload) != {"month"}:
                raise ValueError("Örnek veri isteği geçersiz.")
            with self._store() as store:
                self._json(workspace_tools.load_sample_data(store, payload["month"]))
            return True
        if method == "POST" and path == "/api/workspace/new-month":
            payload = self._read_json()
            if set(payload) != {"month", "copy_roster", "fill_pattern", "lock_previous"}:
                raise ValueError("Yeni ay isteği geçersiz.")
            with self._store() as store:
                self._json(workspace_tools.new_month(
                    store, payload["month"], payload["copy_roster"], payload["fill_pattern"], payload["lock_previous"]))
            return True
        if method == "POST" and path == "/api/workspace/branches":
            payload = self._read_json()
            if set(payload) - {"action", "id", "name", "restart"}:
                raise ValueError("Şube isteği geçersiz.")
            restart = payload.pop("restart", False) is True
            result = workspace_tools.change_branches(Path(self.server.data_path), payload)
            if restart and result["restart_required"]:
                result["restarting"] = self._restart_app()
            self._json(result)
            return True
        if method == "POST" and path == "/api/workspace/phone":
            payload = self._read_json()
            if payload.get("action") not in ("start", "stop") or set(payload) - {"action", "port"}:
                raise ValueError("Telefon görünümü isteği geçersiz.")
            port = payload.get("port", 0)
            if not isinstance(port, int) or isinstance(port, bool) or not (port == 0 or 1024 <= port <= 65535):
                raise ValueError("Port 1024-65535 arasında olmalıdır.")
            phone = self._phone_server()
            self._json(phone.start(port) if payload["action"] == "start" else phone.stop())
            return True
        if method == "POST" and path == "/api/handover":
            payload = self._read_json()
            with self._store() as store:
                if isinstance(payload, dict) and "id" in payload:
                    if set(payload) - {"id", "done", "delete", "status"}:
                        raise ValueError("Devir notu isteği geçersiz.")
                    store.update_handover_note(
                        payload["id"], payload.get("done"), payload.get("delete") is True,
                        payload.get("status"),
                    )
                    self._json({"ok": True})
                else:
                    self._json({"note": store.add_handover_note(payload)})
            return True
        if method == "PUT" and path == "/api/employee-profile":
            payload = self._read_json()
            if set(payload) != {"employee_id", "profile"}:
                raise ValueError("Personel kartı isteği geçersiz.")
            with self._store() as store:
                self._json({"profile": store.save_employee_profile(payload["employee_id"], payload["profile"])})
            return True
        if method == "POST" and path == "/api/personnel-assets":
            import personnel_assets
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise ValueError("Zimmet isteği geçersiz.")
            with self._store() as store:
                if payload.get("action") == "issue":
                    asset = personnel_assets.issue_asset(store, payload)
                elif payload.get("action") == "return":
                    asset = personnel_assets.return_asset(store, payload)
                else:
                    raise ValueError("Zimmet işlemi teslim veya iade olmalıdır.")
            self._json({"asset": asset})
            return True
        if method == "PUT" and path == "/api/schedule-sections":
            payload = self._read_json()
            if set(payload) != {"sections"}:
                raise ValueError("Bölüm ayarı isteği geçersiz.")
            sections = scheduling.normalize_schedule_sections(payload["sections"])
            with self._store() as store:
                previous = store.schedule_sections()
                scheduling.set_schedule_sections(sections)
                capacities = {name: scheduling.section_capacity(name) for name in scheduling.section_names()}
                try:
                    store.save_schedule_sections(sections, capacities)
                except ValueError:
                    scheduling.set_schedule_sections(previous)
                    raise
            self._json({"sections": scheduling.schedule_sections()})
            return True
        if method == "PUT" and path == "/api/holidays":
            payload = self._read_json()
            if set(payload) != {"holidays"}:
                raise ValueError("Tatil takvimi isteği geçersiz.")
            with self._store() as store:
                custom = store.save_custom_holidays(payload["holidays"])
            scheduling.set_custom_holidays(custom)
            self._json({"custom": custom})
            return True
        if method == "PUT" and path == "/api/backup-mirror":
            payload = self._read_json()
            if set(payload) != {"folder"}:
                raise ValueError("Bulut yedek isteği geçersiz.")
            with self._store() as store:
                self._json(store.save_backup_mirror_folder(payload["folder"]))
            return True
        if method == "PUT" and path == "/api/update":
            payload = self._read_json()
            folder = payload.get("folder") if set(payload) == {"folder"} else None
            if not isinstance(folder, str) or len(folder) > 400:
                raise ValueError("Güncelleme klasörü geçersiz.")
            folder = folder.strip().strip('"')
            if folder and not Path(folder).is_absolute():
                raise ValueError("Güncelleme klasörü tam yol olmalıdır.")
            with self._store() as store:
                with store.connection:
                    store._write_setting("update_settings", {"folder": folder})
                    store.record_change("settings.update", f"Güncelleme klasörü: {folder or 'kapalı'}")
                self._json(self._update_info(store))
            return True
        if method == "POST" and path == "/api/update/apply":
            if self._read_json() != {}:
                raise ValueError("Güncelleme isteği geçersiz.")
            self._json(self._apply_update())
            return True
        if method == "POST" and path == "/api/swaps":
            payload = self._read_json()
            if set(payload) != {"date", "first_id", "second_id", "reason", "first_before", "second_before"}:
                raise ValueError("Vardiya değişim kaydı geçersiz.")
            day = date.fromisoformat(str(payload["date"]))
            first_id, second_id = payload["first_id"], payload["second_id"]
            if (not all(isinstance(value, int) and not isinstance(value, bool) for value in (first_id, second_id))
                    or first_id == second_id):
                raise ValueError("İki farklı personel seçin.")
            reason = " ".join(str(payload["reason"]).split())[:200]
            with self._store() as store:
                names = {person["id"]: person["name"] for person in store.people()}
                if first_id not in names or second_id not in names:
                    raise ValueError("Personel bulunamadı.")
                swap = {
                    "id": secrets.token_hex(6), "date": day.isoformat(),
                    "first_id": first_id, "first_name": names[first_id],
                    "second_id": second_id, "second_name": names[second_id],
                    "first_before": str(payload["first_before"])[:60],
                    "second_before": str(payload["second_before"])[:60],
                    "reason": reason, "recorded_at": scheduling.datetime.now().isoformat(timespec="seconds"),
                    "actor": store.user_name(),
                }
                with store.connection:
                    store.log_shift_swap(swap)
                    store.record_change(
                        "schedule.swap",
                        f"Vardiya değişimi {day.strftime('%d.%m.%Y')}: {names[first_id]} ↔ {names[second_id]}",
                        swap,
                    )
                self._json({"swap": swap})
            return True
        if method == "POST" and path == "/api/attendance-events/apply":
            payload = self._read_json()
            if set(payload) != {"id"} or not isinstance(payload["id"], str) or not payload["id"]:
                raise ValueError("Kayıt seçimi geçersiz.")
            self._json(self._apply_attendance_event(payload["id"]))
            return True
        return self._controls_write(method, path)

    _EVENT_SCHEDULE_KINDS = {
        "annual_leave": ("annual_leave", "#fff2cc"), "sick_leave": ("sick_leave", "#f8d7da"),
        "unpaid_leave": ("unpaid_leave", "#eadcf0"), "absence": ("absent", "#ffffff"),
        "marriage_leave": ("marriage_leave", "#ffffff"),
    }

    def _apply_attendance_event(self, event_id: str) -> dict:
        with self._store() as store:
            row = store.connection.execute(
                "SELECT employee_id, kind, start_date, end_date FROM attendance_events WHERE id=?", (event_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Kayıt bulunamadı.")
            if row["kind"] not in self._EVENT_SCHEDULE_KINDS:
                raise ValueError("“Diğer” türündeki kayıtlar vardiyaya işlenemez; hücreye özel kod girin.")
            kind, color = self._EVENT_SCHEDULE_KINDS[row["kind"]]
            employee_id = int(row["employee_id"])
            start, end = date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"])
            if (end - start).days > 92:
                raise ValueError("En fazla 93 günlük kayıt vardiyaya işlenebilir.")
            days: dict[str, list[str]] = {}
            cursor = start
            while cursor <= end:
                days.setdefault(cursor.strftime("%Y-%m"), []).append(cursor.isoformat())
                cursor += scheduling.timedelta(days=1)
            people = store.people()
            months, applied, skipped = {}, 0, []
            for month, month_days in days.items():
                current = store.schedule_month(month)
                entries = [dict(entry, days=dict(entry.get("days", {}))) for entry in current["entries"]]
                rows = [entry for entry in entries if int(entry["employee_id"]) == employee_id]
                if not rows:
                    skipped.append(month)
                    continue
                for day in month_days:
                    rows[0]["days"][day] = {"kind": kind, "fill_color": color}
                    for other in rows[1:]:
                        other["days"].pop(day, None)
                    applied += 1
                months[month] = {
                    "revision": current["revision"],
                    "entries": scheduling.normalize_schedule_entries(month, entries, people),
                }
            if not months:
                raise ValueError("Bu personelin kayıt aylarında vardiya satırı yok; önce çizelgeye ekleyin.")
            if store.save_schedule_months(months) is None:
                raise ValueError("Çizelge başka bir pencerede değişti; yenileyip tekrar deneyin.")
        return {"applied_days": applied, "months": sorted(months), "skipped_months": skipped}

    # --- Grup G: kullanıcılar, oturum, kayıt kilidi, e-posta taslağı ---------
    VIEWER_POSTS = {
        "/api/save-jpeg", "/api/save-excel", "/api/forms/preview", "/api/forms/generate",
        "/api/forms/save", "/api/forms/history", "/api/import-preview", "/api/import-extract",
        "/api/import-mapped-preview", "/api/import-profile-headers", "/api/backups/verify",
        "/api/mail-draft", "/api/open-whatsapp",
        "/api/whats-new/seen", "/api/assistant/chat",
        "/api/schedule/compare-plans",
    }
    AUTH_FREE = {"/api/environment", "/api/auth/status", "/api/auth/login", "/api/auth/logout"}

    def _session_token(self) -> str:
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "vp_session":
                return value
        return ""

    def _users_enabled(self) -> bool:
        cached = self.server.users_cache
        if cached and time.monotonic() - cached[0] < 5:
            return cached[1]
        with Store(self.server.data_path) as store:
            enabled = store.users_enabled()
        self.server.users_cache = (time.monotonic(), enabled)
        return enabled

    LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

    def _host_name(self, value: str) -> str:
        value = value.strip().lower()
        if value.startswith("["):
            return value[1:value.find("]")] if "]" in value else value
        return value.rsplit(":", 1)[0] if value.count(":") == 1 else value

    def _request_origin_error(self, method: str, path: str) -> tuple[int, str] | None:
        """Yerel API'yi DNS yeniden bağlama ve başka sitelerden gelen isteklere karşı korur."""
        host = self.headers.get("Host")
        if host and self._host_name(host) not in self.LOCAL_HOSTS:
            return 403, "İstek yalnız bu bilgisayardaki uygulama penceresinden kabul edilir."
        if method == "GET":
            return None
        origin = self.headers.get("Origin")
        fetch_site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        if origin is not None:
            parsed = urlparse(origin)
            try:
                port = parsed.port or 80
            except ValueError:
                port = -1
            if origin == "null" or parsed.scheme != "http" or (parsed.hostname or "") not in self.LOCAL_HOSTS \
                    or port != self.server.server_address[1]:
                return 403, "Başka bir siteden gelen değişiklik isteği engellendi."
        if fetch_site in ("cross-site", "same-site"):
            return 403, "Başka bir siteden gelen değişiklik isteği engellendi."
        length = int(self.headers.get("Content-Length") or 0)
        if (origin is not None or fetch_site) and length > 0:
            media = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            binary_backup_route = method == "POST" and path in (
                "/api/continuity/database-backup/preview",
                "/api/continuity/database-backup/restore",
            )
            if media != "application/json" and not (
                binary_backup_route and media == "application/octet-stream"
            ):
                return 415, "İstek JSON biçiminde gönderilmelidir."
        return None

    def _gate(self, method: str, path: str) -> bool:
        """İstek işlenebilirse True; değilse yanıtı gönderip False döndürür."""
        self.current_user = None
        if not path.startswith("/api/"):
            return True
        blocked = self._request_origin_error(method, path)
        if blocked:
            self._json({"error": blocked[1]}, blocked[0])
            return False
        internal = self.headers.get("X-VP-Internal", "")
        if internal and secrets.compare_digest(internal, self.server.internal_token):
            return True
        if path not in self.AUTH_FREE and not access_services.authorize(
            self, method, path, parse_qs(urlparse(self.path).query),
        ):
            return False
        if (method != "GET" or path == "/api/access/notifications") and path not in self.AUTH_FREE and path not in ("/api/lock/take-over",) \
                and not path.startswith("/api/reports/") and path not in self.VIEWER_POSTS:
            status = self.server.lock.status()
            if status["read_only"]:
                holder = status["holder"]
                self._json({
                    "error": f"Kayıtlar şu anda {holder['user']} ({holder['host']}) tarafından düzenleniyor; "
                             "değişiklik kaydedilemedi. Araçlar → Kayıt kilidi menüsünden devralabilirsiniz.",
                    "locked": True, "holder": holder,
                }, 423)
                return False
        return True

    def _auth_status(self) -> dict:
        with Store(self.server.data_path) as store:
            enabled = store.users_enabled()
            cached = self.server.sessions.get(self._session_token())
            user = next((item for item in store.users()
                         if cached and item["username"].casefold() == cached.get("username", "").casefold()), None)
            if user is None:
                self.server.sessions.pop(self._session_token(), None)
            else:
                self.server.sessions[self._session_token()] = user
        return {"enabled": enabled, "user": user, "lock": self.server.lock.status()}

    def _start_session(self, user: dict) -> str:
        token = secrets.token_urlsafe(32)
        self.server.sessions[token] = user
        return token

    def _json_cookie(self, value, token: str | None) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if token is not None:
            cookie = f"vp_session={token}; Path=/; HttpOnly; SameSite=Strict"
            self.send_header("Set-Cookie", cookie if token else cookie + "; Max-Age=0")
        self.end_headers()
        self.wfile.write(body)

    def _access_routes(self, method: str, path: str) -> bool:
        if method == "GET" and path == "/api/auth/status":
            self._json(self._auth_status())
            return True
        if method == "GET" and path == "/api/lock":
            self._json(self.server.lock.status())
            return True
        if method == "POST" and path == "/api/lock/take-over":
            if self._read_json() != {}:
                raise ValueError("Kilit isteği geçersiz.")
            self._json(self.server.lock.take_over())
            return True
        if method == "POST" and path == "/api/auth/login":
            payload = self._read_json()
            with Store(self.server.data_path) as store:
                user = store.authenticate(payload.get("username"), payload.get("password"))
            if user is None:
                time.sleep(0.4)
                raise ValueError("Kullanıcı adı veya parola hatalı.")
            self._json_cookie({"user": user}, self._start_session(user))
            return True
        if method == "POST" and path == "/api/auth/logout":
            self.server.sessions.pop(self._session_token(), None)
            self._json_cookie({"ok": True}, "")
            return True
        if path in ("/api/users", "/api/users/delete"):
            with self._store() as store:
                enabled = store.users_enabled()
                if enabled and (self.current_user or {}).get("role") != "admin" and not self._is_internal():
                    self._json({"error": "Kullanıcıları yalnız yönetici yönetebilir."}, 403)
                    return True
                if method == "GET":
                    self._json({"users": store.users()})
                    return True
                payload = self._read_json()
                token = None
                if method == "PUT":
                    if set(payload) - {"username", "password", "role", "confirm_admin"} \
                            or not {"username", "password", "role"} <= set(payload) \
                            or ("confirm_admin" in payload and type(payload["confirm_admin"]) is not bool):
                        raise ValueError("Kullanıcı bilgisi geçersiz.")
                    previous = next((user for user in store.users()
                                     if user["username"].casefold() == str(payload["username"]).strip().casefold()), None)
                    if enabled and payload["role"] == "admin" and (previous or {}).get("role") != "admin" \
                            and payload.get("confirm_admin") is not True:
                        raise ValueError("Yeni yönetici yetkisini ayrıca onaylayın.")
                    users = store.save_user(payload["username"], payload["password"], payload["role"])
                    if not enabled:
                        user = next(item for item in users if item["role"] == "admin")
                        token = self._start_session(user)
                elif method == "POST" and path == "/api/users/delete":
                    if set(payload) != {"username"}:
                        raise ValueError("Silme isteği geçersiz.")
                    users = store.delete_user(payload["username"])
                    names = {item["username"] for item in users}
                    for key, value in list(self.server.sessions.items()):
                        if value["username"] not in names:
                            self.server.sessions.pop(key, None)
                    if not users:
                        token = ""
                else:
                    return False
            self.server.users_cache = None
            self._json_cookie({"users": users}, token)
            return True
        if method == "POST" and path == "/api/mail-draft":
            self._json(self._mail_draft(self._read_json()))
            return True
        if method == "POST" and path == "/api/open-whatsapp":
            payload = self._read_json()
            text = payload.get("text", "")
            if set(payload) - {"text"} or not isinstance(text, str) or len(text) > 1000:
                raise ValueError("WhatsApp mesajı geçersiz.")
            if self.server.open_file is None:
                raise ValueError("WhatsApp yalnız masaüstü uygulamasından açılabilir.")
            target = "whatsapp://send?text=" + quote(text)
            try:
                self.server.open_file(target)
                app = "desktop"
            except OSError:
                target = "https://web.whatsapp.com/send?text=" + quote(text)
                self.server.open_file(target)
                app = "web"
            self._json({"opened": app})
            return True
        return False

    def _is_internal(self) -> bool:
        internal = self.headers.get("X-VP-Internal", "")
        return bool(internal) and secrets.compare_digest(internal, self.server.internal_token)

    def _has_permission(self, permission: str, store) -> bool:
        user = self.current_user
        return self._is_internal() or not store.users_enabled() or bool(
            user and (user.get("role") == "admin" or permission in access_services._perms(user))
        )

    MAIL_KINDS = {
        "schedule": "Vardiya listesi (Excel)", "attendance": "Puantaj (Excel)",
        "manager-summary": "Yönetici özeti (PDF)", "month-package": "Ay sonu paketi (ZIP)",
    }

    def _mail_draft(self, payload, *, open_draft: bool = True, recipient_section: str = "") -> dict:
        if not isinstance(payload, dict) or "month" not in payload or set(payload) - {
            "kind", "kinds", "month", "week", "to", "subject", "body",
        }:
            raise ValueError("E-posta taslağı bilgisi geçersiz.")
        kinds = mail_automation.draft_kinds(payload)
        month = scheduling.validate_month(payload["month"])
        if recipient_section and recipient_section not in scheduling.section_names():
            raise ValueError("Alıcı bölüm yetkisi artık geçerli değil.")
        if recipient_section and any(kind in ("manager-summary", "month-package") for kind in kinds):
            raise ValueError("Bu ek tüm bölümleri içerir ve alıcının bölüm yetkisini aşar.")
        week = payload.get("week", 0)
        if not isinstance(week, int) or isinstance(week, bool):
            raise ValueError("Hafta numarası geçersiz.")
        texts = {}
        for field, maximum in (("to", 500), ("subject", 200), ("body", 5000)):
            value = payload.get(field, "")
            if not isinstance(value, str) or len(value) > maximum:
                raise ValueError("E-posta alanları çok uzun veya geçersiz.")
            texts[field] = value.strip()
        if texts["to"] and any(not re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", item.strip())
                               for item in re.split(r"[;,]", texts["to"]) if item.strip()):
            raise ValueError("Alıcı e-posta adreslerini kontrol edin (birden fazlaysa ; ile ayırın).")
        attachments = []
        for kind in kinds:
            if kind in ("schedule", "attendance"):
                query = {"kind": [kind], "month": [month], "week": [str(week)]}
                if recipient_section:
                    query["section"] = [recipient_section]
                content, filename = self._export_workbook(query)
                mime = ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                content, _, filename, full_mime = self._report_file(f"/api/reports/{kind}", {"month": [month]})
                mime = tuple(full_mime.split(";")[0].split("/", 1))
            attachments.append((content, mime, filename))
        label = f"{scheduling.MONTH_NAMES[int(month[5:]) - 1]} {month[:4]}"
        names = [self.MAIL_KINDS[kind] for kind in kinds]
        message = EmailMessage()
        message["Subject"] = texts["subject"] or f"{', '.join(name.split(' (')[0] for name in names)} - {label}"
        if texts["to"]:
            message["To"] = ", ".join(item.strip() for item in re.split(r"[;,]", texts["to"]) if item.strip())
        message["X-Unsent"] = "1"
        if len(names) == 1:
            default_body = f"Merhaba,\n\n{label} dönemine ait {names[0].lower()} ektedir.\n\nİyi çalışmalar."
        else:
            listing = "\n".join(f"- {name}" for name in names)
            default_body = f"Merhaba,\n\n{label} dönemine ait aşağıdaki dosyalar ektedir:\n{listing}\n\nİyi çalışmalar."
        message.set_content(texts["body"] or default_body)
        for content, mime, filename in attachments:
            message.add_attachment(content, maintype=mime[0], subtype=mime[1], filename=filename)
        filenames = [filename for _, _, filename in attachments]
        folder = Path(tempfile.gettempdir()) / "VardiyaPuantaj-eposta"
        folder.mkdir(parents=True, exist_ok=True)
        stem = Path(filenames[0]).stem if len(filenames) == 1 else f"RAPORLAR_{month}"
        draft = folder / f"{stem}_{secrets.token_hex(3)}.eml"
        draft.write_bytes(bytes(message))
        opened = False
        if open_draft and self.server.open_file is not None:
            self.server.open_file(draft)
            opened = True
        return {"path": str(draft), "opened": opened, "attachment": ", ".join(filenames), "attachments": filenames}

    def _mail_preview(self, payload, *, section: str = "") -> dict:
        result = self._mail_draft({
            "kinds": payload["kinds"], "month": payload["month"], "week": payload["week"],
            "to": payload["recipient"], "subject": payload["subject"], "body": payload["body"],
        }, open_draft=False, recipient_section=section)
        draft = Path(result["path"])
        try:
            message = BytesParser(policy=policy.default).parsebytes(draft.read_bytes())
        finally:
            draft.unlink(missing_ok=True)
        attachments = []
        total = 0
        for part in message.iter_attachments():
            content = part.get_payload(decode=True) or b""
            total += len(content)
            if total > mail_automation.MAX_ATTACHMENT_BYTES:
                raise ValueError("Önizleme ekleri 20 MiB sınırını aşıyor; raporları ayrı önizleyin.")
            filename = part.get_filename() or "ek"
            attachments.append({
                "filename": filename, "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "content_base64": base64.b64encode(content).decode("ascii"),
                "content_preview": self._mail_attachment_preview(content, filename),
            })
        return {
            "recipient": payload["recipient"], "subject": str(message["Subject"]),
            "body": message.get_body(preferencelist=("plain",)).get_content(),
            "attachments": attachments,
        }

    @staticmethod
    def _mail_attachment_preview(content: bytes, filename: str) -> str:
        suffix = Path(filename).suffix.casefold()
        if suffix == ".xlsx":
            from openpyxl import load_workbook
            workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
            try:
                excerpts = []
                for sheet in workbook.worksheets:
                    lines = [f"[{sheet.title}]"]
                    for row in sheet.iter_rows(min_row=1, max_row=8, max_col=12, values_only=True):
                        values = [str(value)[:100] if value is not None else "" for value in row]
                        lines.append(" | ".join(values).rstrip(" |"))
                    excerpts.append("\n".join(lines))
                return "\n\n".join(excerpts)
            finally:
                workbook.close()
        if suffix == ".pdf":
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages[:3])
            return text[:6000] or "PDF dosyasında metin önizlemesi bulunamadı."
        if suffix == ".zip":
            with zipfile.ZipFile(BytesIO(content)) as archive:
                return "\n".join(
                    f"{item.filename} · {item.file_size:,} bayt"
                    for item in archive.infolist()[:40]
                ) or "ZIP arşivi boş."
        return f"Bu {suffix or 'dosya'} türü için metin önizlemesi yok; indirerek tam içeriği inceleyin."

    def _technical_step(self, step: str) -> None:
        steps = getattr(self, "_technical_error_steps", None)
        if steps is not None and step not in steps:
            steps.append(step)

    def _internal_error(self, method: str, path: str, error: Exception) -> None:
        parts = path.strip("/").split("/")
        family = parts[1] if len(parts) > 1 and parts[0] == "api" else "request"
        stage = f"{method} /api/{family}" if family != "request" else f"{method} request"
        error_type = type(error).__name__
        error_code = "VP-" + hashlib.sha256(
            f"{version.APP_VERSION}:{stage}:{error_type}".encode("utf-8")
        ).hexdigest()[:10].upper()
        operation_id = secrets.token_hex(8)
        steps = list(getattr(self, "_technical_error_steps", ()))
        for step in ("operation_failed", "error_code_assigned", "diagnostic_record_prepared"):
            if step not in steps:
                steps.append(step)
        try:
            with Store(self.server.data_path) as store, store.connection:
                store.record_technical_error(
                    operation_id, version.APP_VERSION, stage, error_code, error_type, steps,
                )
        except (OSError, sqlite3.Error, ValueError) as log_error:
            self.log_error("Technical error event could not be persisted (%s).", type(log_error).__name__)
        self.log_error("%s failed [%s] (%s)", stage, error_code, error_type)
        self._json({
            "error": "İstek tamamlanamadı.",
            "error_code": error_code,
            "operation_id": operation_id,
        }, 500)

    def do_GET(self) -> None:
        self._dispatch("GET", self._route_GET)

    def do_POST(self) -> None:
        self._dispatch("POST", self._route_POST)

    def do_PUT(self) -> None:
        self._dispatch("PUT", self._route_PUT)

    def _dispatch(self, method: str, route) -> None:
        path = unquote(urlparse(self.path).path)
        self._technical_error_steps = []
        self._technical_step("request_received")
        try:
            self._technical_step("route_resolved")
            if re.fullmatch(r"/employee-portal/[A-Za-z0-9_-]{43}(/requests|/swaps|/read|/attendance-corrections)?", urlparse(self.path).path):
                if self.server.lock.status()["read_only"]:
                    access_services._public_send(self, 423, "Kayıt alanı başka bilgisayarda kullanılıyor; daha sonra deneyin.")
                    return
                if access_services.handle_public(self, self.path, method):
                    return
            if method == "GET" and document_workflows.handle_public_share(self, urlparse(self.path).path):
                return
            if not self._gate(method, path):
                return
            self._technical_step("authorization_passed")
            self._technical_step("operation_started")
            if method == "GET" and path == "/api/assistant/models":
                try:
                    self._json({"models": live_assistant.list_models()})
                except live_assistant.AssistantUnavailable as exc:
                    self._json({"error": str(exc)}, 503)
                return
            if method == "POST" and path == "/api/assistant/chat":
                payload = self._read_json(32_000)
                if not isinstance(payload, dict) or set(payload) != {"model", "messages", "context"}:
                    raise ValueError("Asistan isteği geçersiz.")
                messages = live_assistant._validate_messages(payload["messages"])
                context = live_assistant.normalize_context(payload["context"])
                context["today"] = date.today().isoformat()
                try:
                    query_type = live_assistant.record_query_type(messages[-1]["content"])
                    if query_type:
                        with self._store() as store:
                            result = self._assistant_record_answer(
                                store, query_type, messages[-1]["content"], context,
                            )
                    else:
                        result = live_assistant.chat(payload["model"], messages, context)
                except live_assistant.AssistantUnavailable as exc:
                    self._json({"error": str(exc)}, 503)
                    return
                self._json(result)
                return
            if path == "/api/schedule/protection" and method in ("GET", "PUT"):
                if method == "PUT":
                    operations._require_admin(self)
                    payload = self._read_json(8192)
                    with Store._schedule_protection_lock, self._store() as store:
                        result = schedule_protection.set_lock(store, payload)
                else:
                    month = scheduling.validate_month(parse_qs(urlparse(self.path).query).get("month", [""])[0])
                    with self._store() as store:
                        result = schedule_protection.locks(store, month)
                self._json({"cells": result})
                return
            if path == "/api/overtime-settings/transition" and method in ("POST", "PUT"):
                operations._require_admin(self)
                self._overtime_transition(self._read_json(8192), save=method == "PUT")
                return
            if path == "/api/attendance/corrections" and method in ("GET", "POST", "PUT"):
                with self._store() as store:
                    if method == "POST":
                        if not self._has_permission("attendance.write", store):
                            self._json({
                                "error": "Puantaj düzeltme talebi için puantaj düzenleme yetkisi gerekir.",
                            }, 403)
                            return
                        self._json(attendance_corrections.create(self, store, self._read_json(8192)), 201)
                    elif method == "PUT":
                        operations._require_admin(self)
                        self._json(attendance_corrections.decide(self, store, self._read_json(8192)))
                    else:
                        query = parse_qs(urlparse(self.path).query)
                        month = scheduling.validate_month(query.get("month", [""])[0])
                        admin = not self.current_user or self.current_user["role"] == "admin"
                        if set(query) == {"month"}:
                            self._json({"requests": attendance_corrections.list_requests(store, month, admin=admin)})
                        elif set(query) == {"month", "date", "employee_id"}:
                            if not self._has_permission("attendance.write", store):
                                self._json({
                                    "error": "Kaynak ayrıntılarını yalnız puantaj düzenleme yetkisi olanlar görebilir.",
                                }, 403)
                                return
                            raw_id = query["employee_id"][0]
                            employee_id = int(raw_id) if re.fullmatch(r"[0-9]{1,19}", raw_id) else 0
                            day = query["date"][0]
                            attendance_corrections.validate_cell(month, day, employee_id)
                            store.connection.execute("BEGIN")
                            try:
                                result = attendance_corrections.evidence(self, store, month, day, employee_id)
                            finally:
                                store.connection.rollback()
                            self._json(result)
                        else:
                            raise ValueError("Puantaj düzeltme sorgusu geçersiz.")
                return
            if method == "POST" and path in ("/api/schedule/restore-cell", "/api/attendance/restore-cell"):
                attendance = path == "/api/attendance/restore-cell"
                identity = "employee_id" if attendance else "row_id"
                payload = self._read_json(8192)
                if set(payload) != {"month", "revision", "expected_revision", identity, "date"}:
                    raise ValueError("Hücre geri alma isteği geçersiz.")
                month = scheduling.validate_month(payload["month"])
                if not all(isinstance(payload[key], str) and 0 < len(payload[key]) <= maximum
                           for key, maximum in (("revision", 64), ("expected_revision", 64), ("date", 10))):
                    raise ValueError("Hücre seçimi veya sürüm geçersiz.")
                identifier = payload[identity]
                if (attendance and (type(identifier) is not int or identifier <= 0)) or (
                    not attendance and (not isinstance(identifier, str) or not 0 < len(identifier) <= 80)
                ):
                    raise ValueError("Hücre seçimi geçersiz.")
                if payload["date"] not in {day.isoformat() for day in scheduling.month_dates(month)}:
                    raise ValueError("Hücre tarihi bu ayda değil.")
                with self._store() as store:
                    restore = store.restore_attendance_cell if attendance else store.restore_schedule_cell
                    revision = restore(
                        month, payload["revision"], payload["expected_revision"], identifier, payload["date"],
                    )
                if revision is None:
                    self._json({"error": "Çizelge yeniden değişti; sürümleri yeniden açın."}, 409)
                else:
                    self._json({"revision": revision})
                return
            if method == "POST" and path == "/api/view-output":
                self._view_output()
                return
            query = parse_qs(urlparse(self.path).query)
            if usability.handle(self, method, path, query):
                return
            if method == "GET" and (
                linked_leave.handle_get(self, path, query)
                or mail_automation.handle_request(self, method, path)
                or document_workflows.handle_get(self, path, query)
                or data_continuity.handle_get(self, path, query)
                or access_services.handle_get(self, path, query)
                or operations.handle_get(self, path, query)
            ):
                return
            if method in ("POST", "PUT") and (
                linked_leave.handle_write(self, method, path)
                or mail_automation.handle_request(self, method, path)
                or document_workflows.handle_write(self, method, path)
                or data_continuity.handle_write(self, method, path)
                or access_services.handle_write(self, method, path)
                or operations.handle_write(self, method, path)
            ):
                return
            if self._access_routes(method, path) or self._health_routes(method, path):
                return
        except PermissionError as exc:
            self._json({"error": str(exc)}, 403)
            return
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
            return
        except Exception as exc:
            if isinstance(exc, sqlite3.IntegrityError) and str(exc) == schedule_protection.ERROR:
                self._json({"error": str(exc)}, 409)
                return
            self._internal_error(method, path, exc)
            return
        self._technical_step("operation_started")
        route()

    def _view_output(self):
        payload = self._read_json(12_000_000)
        if not isinstance(payload, dict) or set(payload) != {"kind", "month", "content", "draft", "sources"}:
            raise ValueError("Görünüm çıktısı isteği geçersiz.")
        if payload["kind"] not in ("schedule", "attendance", "archive", "import") \
                or not isinstance(payload["draft"], bool):
            raise ValueError("Görünüm çıktısı türü geçersiz.")
        supplied = payload["sources"]
        if supplied is not None and (
            not isinstance(supplied, dict) or not 1 <= len(supplied) <= 24
            or any(
                not isinstance(period, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period)
                or not isinstance(revisions, dict) or set(revisions) != {"schedule", "attendance"}
                or any(not isinstance(value, str) or len(value) > 128 for value in revisions.values())
                for period, revisions in supplied.items()
            )
        ):
            raise ValueError("Görünüm kaynak sürümleri geçersiz.")
        month = scheduling.validate_month(payload["month"])
        try:
            content = base64.b64decode(payload["content"], validate=True)
        except (ValueError, TypeError, binascii.Error) as exc:
            raise ValueError("Görünüm görüntüsü okunamadı.") from exc
        if not content or len(content) > 8_000_000:
            raise ValueError("Görünüm görüntüsü 8 MB sınırını aşıyor veya boş.")
        try:
            with Image.open(BytesIO(content)) as image:
                if image.format != "JPEG" or image.width * image.height > 30_000_000:
                    raise ValueError("Görünüm JPEG olmalı ve 30 milyon pikseli aşmamalıdır.")
                image.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("Görünüm görüntüsü geçersiz.") from exc
        draft = payload["draft"] or payload["kind"] in ("archive", "import")
        with self._store() as store:
            sources = self._output_sources(store, month)
            draft = draft or payload["sources"] != sources
            filename = f"{'TASLAK_' if draft else ''}TABLO_{month}.jpg"
            content, _ = self._track_output(
                store, content, "jpeg", filename, month,
                sources, draft=draft,
            )
        self._json({"content": base64.b64encode(content).decode("ascii"), "footer_height": 48})

    def _export_workbook(self, query, *, restored_store=None) -> tuple[bytes, str]:
        month = scheduling.validate_month(query.get("month", [""])[0])
        kind = query.get("kind", ["schedule"])[0]
        employee_filter = query.get("employee", [""])[0].strip()
        section_filter = query.get("section", [""])[0].strip()
        if len(employee_filter) > 80:
            raise ValueError("Personel filtresi çok uzun.")
        if section_filter and section_filter not in scheduling.section_names():
            raise ValueError("Bölüm filtresi geçersiz.")
        if kind not in ("schedule", "attendance"):
            raise ValueError("Excel çıktı türü geçersiz.")
        active_week_index = 0
        if kind == "schedule":
            try:
                active_week_index = int(query.get("week", ["0"])[0])
            except ValueError as exc:
                raise ValueError("Hafta numarası geçersiz.") from exc
            if not 0 <= active_week_index < len(scheduling.week_starts(month)):
                raise ValueError("Seçilen hafta bulunamadı.")
        with (self._store() if restored_store is None else nullcontext(restored_store)) as store:
            sources = self._output_sources(store, month) if restored_store is None else None
            people = store.people()
            if section_filter:
                people = [
                    person for person in people
                    if store.employee_profile(int(person["id"])).get("home_section") == section_filter
                ]
            if employee_filter:
                matched_people = [
                    person for person in people
                    if _employee_name_key(person["name"]) == _employee_name_key(employee_filter)
                ]
                if len(matched_people) != 1:
                    raise ValueError("Dışa aktarma için personel adını tam ve doğru yazın.")
                selected_person_id = int(matched_people[0]["id"])
                people = matched_people
            periods = scheduling.adjacent_months(month)
            entries = [
                entry
                for period in (periods[0], periods[2], periods[1])
                for entry in store.schedule_month(period)["entries"]
            ]
            selected_entries = store.schedule_month(month)["entries"]
            if section_filter:
                section_ids = {int(person["id"]) for person in people}
                entries = [entry for entry in entries if int(entry["employee_id"]) in section_ids]
                selected_entries = [
                    entry for entry in selected_entries if int(entry["employee_id"]) in section_ids
                ]
            if employee_filter:
                entries = [entry for entry in entries if int(entry["employee_id"]) == selected_person_id]
                selected_entries = [
                    entry for entry in selected_entries
                    if int(entry["employee_id"]) == selected_person_id
                ]
            with tempfile.TemporaryDirectory(prefix="vardiya-puantaj-") as temp:
                if kind == "schedule":
                    if employee_filter and not selected_entries:
                        raise ValueError("Seçilen personel için bu ayda vardiya kaydı yok.")
                    template_revision = self._template_revision(store, kind, month, active_week_index)
                    output = Path(temp) / f"VARDIYA_LISTESI_{month}.xlsx"
                    scheduling.export_schedule_xlsx(
                        output, month, entries, people, active_week_index, scheduling.repair_workbook_settings(store.workbook_settings()),
                        selected_entries=selected_entries, overtime_settings=store.overtime_settings(month),
                        leave_remaining=self._leave_remaining(self._balance_summary(store, month)),
                        person_rules=self._person_rules(store),
                    )
                else:
                    template_revision = self._template_revision(store, kind, month, 0)
                    output = Path(temp) / f"PUANTAJ_{month}.xlsx"
                    attendance, _, _ = self._attendance_for(store, month, entries=entries, people=people)
                    if employee_filter:
                        attendance["rows"] = [
                            row for row in attendance["rows"] if row["employee_id"] == selected_person_id
                        ]
                    if employee_filter and not attendance["rows"]:
                        raise ValueError("Seçilen personel için bu ayda puantaj kaydı yok.")
                    scheduling.export_attendance_xlsx(output, month, attendance, scheduling.repair_workbook_settings(store.workbook_settings()))
                if restored_store is not None:
                    return output.read_bytes(), output.name
                body, filename = self._track_output(
                    store, output.read_bytes(), "xlsx", output.name, month, sources,
                    template_revision=template_revision,
                )
        return body, filename

    def _route_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/reports/problem-report" and parse_qs(parsed.query).get("preview") == ["1"]:
                with self._store() as store:
                    files = workspace_tools.problem_report_files(store, Path(self.server.data_path))
                self._json({"files": files, "review": workspace_tools.problem_report_digest(files)})
                return
            if path == "/api/reports/check":
                month = scheduling.validate_month(parse_qs(parsed.query).get("month", [""])[0])
                with self._store() as store:
                    attendance, entries = self._month_attendance(store, month)
                    balances = self._balance_summary(store, month)
                    report = self._check_report(store, month, attendance, entries, balances)
                self._json(report)
                return
            if path in ("/api/reports/payroll", "/api/reports/person-pdf", "/api/reports/yearly",
                        "/api/reports/person-program", "/api/reports/month-package",
                        "/api/reports/payroll-program", "/api/reports/manager-summary",
                        "/api/reports/problem-report", "/api/reports/settings-export"):
                content, _, filename, mime = self._report_file(path, parse_qs(parsed.query))
                self._send(content, mime, filename=filename)
                return
            if path == "/api/environment":
                self._json({"desktop": self.server.save_excel is not None})
                return
            if path == "/api/forms":
                with self._store() as store:
                    self._json({"forms": forms.list_templates(store.connection)})
                return
            if path == "/api/forms/template":
                template_id = parse_qs(parsed.query).get("id", [""])[0]
                with self._store() as store:
                    item = forms.get_template(store.connection, template_id)
                    self._send(item["content"], "application/octet-stream", filename=f"form.{item['extension']}")
                return
            if path == "/api/archive":
                with self._store() as store:
                    self._json({"months": store.archive_months()})
                return
            if path == "/api/archive/search":
                query = parse_qs(parsed.query, keep_blank_values=True)
                kind = query.get("kind", ["schedule"])[0]
                with self._store() as store:
                    months = store.matching_archive_months(
                        kind,
                        query.get("month", [""])[0],
                        query.get("employee", [""])[0],
                        query.get("section", [""])[0],
                        query.get("type", [""])[0],
                    )
                self._json({"months": months})
                return
            if path in ("/api/archive/revisions", "/api/archive/compare"):
                query = parse_qs(parsed.query)
                kind = query.get("kind", [""])[0]
                month = scheduling.validate_month(query.get("month", [""])[0])
                revision = query.get("revision", [""])[0]
                if kind not in ("schedule", "attendance"):
                    raise ValueError("Arşiv türü geçersiz.")
                with self._store() as store:
                    if path == "/api/archive/revisions":
                        self._json(store.archive_revisions(kind, month))
                    else:
                        if not revision or len(revision) > 64:
                            raise ValueError("Arşiv sürümü geçersiz.")
                        self._json(store.compare_archive_revision(kind, month, revision))
                return
            if path == "/api/backups":
                with self._store() as store:
                    self._json({"backups": store.backups(), "settings": store.backup_settings()})
                return
            if path == "/api/history":
                query = parse_qs(parsed.query)
                raw_limit = query.get("limit", ["100"])[0]
                try:
                    limit = int(raw_limit)
                except ValueError as exc:
                    raise ValueError("Geçmiş kayıt sayısı geçersiz.") from exc
                with self._store() as store:
                    self._json({"history": store.change_history(
                        limit, query.get("q", [""])[0][:80], query.get("action", [""])[0][:40],
                    )})
                return
            if self._extra_get(path, parse_qs(parsed.query)):
                return
            if path == "/api/attendance-events":
                month = scheduling.validate_month(parse_qs(parsed.query).get("month", [""])[0])
                year, month_number = map(int, month.split("-"))
                month_start = f"{month}-01"
                month_end = date(year, month_number, calendar.monthrange(year, month_number)[1]).isoformat()
                with self._store() as store:
                    self._json({"events": store.attendance_events(month_start, month_end)})
                return
            if path == "/api/workbook-settings":
                with self._store() as store:
                    self._json(scheduling.repair_workbook_settings(store.workbook_settings()))
                return
            if path == "/api/template-versions":
                with self._store() as store:
                    self._json({"versions": store.template_versions()})
                return
            if path == "/api/user-settings":
                with self._store() as store:
                    self._json({"user_name": store.user_name()})
                return
            if path == "/api/schedule-validation-settings":
                with self._store() as store:
                    self._json(store.schedule_validation_settings())
                return
            if path == "/api/overtime-settings":
                period = parse_qs(parsed.query).get("month", [None])[0]
                with self._store() as store:
                    self._json(store.overtime_settings(scheduling.validate_month(period) if period is not None else None))
                return
            if path == "/api/balances":
                query = parse_qs(parsed.query)
                month = scheduling.validate_month(
                    query.get("month", [date.today().strftime("%Y-%m")])[0]
                )
                employee_text = query.get("employee_id", [""])[0]
                if employee_text and not employee_text.isdigit():
                    raise ValueError("İzin hareket dökümü için personel seçimi geçersiz.")
                employee_id = int(employee_text) if employee_text else None
                with self._store() as store:
                    self._json(self._balance_summary(store, month, employee_id))
                return
            if path == "/api/import-profiles":
                with self._store() as store:
                    self._json({"profiles": store.import_profiles()})
                return
            if path == "/api/form-draft":
                with self._store() as store:
                    self._json({"draft": store.form_draft()})
                return
            if path == "/api/quick-entries":
                with self._store() as store:
                    self._json({"entries": store.quick_entries()})
                return
            if path == "/api/schedule-templates":
                with self._store() as store:
                    self._json({"templates": store.schedule_templates()})
                return
            if path == "/api/people":
                with self._store() as store:
                    self._json({"people": store.people()})
                return
            if path == "/api/table-templates":
                self._json({kind: scheduling.table_template_status(kind) for kind in scheduling.TEMPLATE_FILES})
                return
            if path == "/api/table-templates/download":
                kind = parse_qs(parsed.query).get("kind", [""])[0]
                content = scheduling.table_template_bytes(kind)
                self._send(
                    content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    filename=f"{'VARDIYA' if kind == 'schedule' else 'PUANTAJ'}_SABLONU.xlsx",
                )
                return
            if path == "/api/template":
                query = parse_qs(parsed.query)
                kind = query.get("kind", ["schedule"])[0]
                sheet_text = query.get("sheet", ["0"])[0]
                if not sheet_text.isdigit():
                    raise ValueError("Excel çalışma sayfası numarası geçersiz.")
                sheet_index = int(sheet_text)
                if kind == "schedule" and sheet_index >= 5:
                    sheet_index = 1
                elif kind == "attendance":
                    sheet_index = 0
                self._json(scheduling.workbook_template(kind, sheet_index))
                return
            if path in ("/api/schedule", "/api/attendance"):
                query = parse_qs(parsed.query)
                month = scheduling.validate_month(
                    query.get("month", [date.today().strftime("%Y-%m")])[0]
                )
                with self._store() as store:
                    people = store.people()
                    if path == "/api/schedule":
                        result = store.schedule_month(month)
                        self._json({
                            "month": month,
                            "entries": result["entries"],
                            "revision": result["revision"],
                            "personnel": people,
                            "week_totals": self._schedule_week_totals(store, month),
                            "source_revisions": self._output_sources(store, month),
                            "leave_remaining": {
                                str(key): value
                                for key, value in self._leave_remaining(self._balance_summary(store, month)).items()
                            },
                        })
                    else:
                        attendance, _, month_data = self._attendance_for(store, month, people=people)
                        payroll_visible = self._has_permission("payroll", store)
                        documents_visible = self._has_permission("documents", store)
                        if not payroll_visible or not documents_visible:
                            attendance = json.loads(json.dumps(attendance))
                            for row in attendance["rows"]:
                                if not payroll_visible:
                                    row["deduction_amount"] = None
                                if not documents_visible:
                                    row["notes"] = {}
                        shown = {row["employee_id"] for row in attendance["rows"]}
                        available = [person for person in people if person["active"] and person["id"] not in shown]
                        self._json({
                            "month": month,
                            "attendance": attendance,
                            "calendar": scheduling.attendance_calendar(month),
                            "overrides": month_data["overrides"],
                            "deductions": month_data["deductions"] if payroll_visible else {},
                            "payroll_redacted": not payroll_visible,
                            "roster": month_data["roster"],
                            "roster_sources": month_data.get("sources", {}),
                            "overtime": month_data["overtime"],
                            "available_people": available,
                            "missing_people": self._attendance_missing(store, month, available, month_data),
                            "overtime_settings": store.overtime_settings(month),
                            "source_revisions": self._output_sources(store, month),
                            "revision": month_data["revision"],
                        })
                return
            if path == "/api/export":
                body, export_name = self._export_workbook(parse_qs(parsed.query))
                self._send(
                    body,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    filename=export_name,
                )
                return
            static_files = {
                "/": "index.html",
                "/app.js": "app.js",
                "/extras.js": "extras.js",
                "/controls.js": "controls.js",
                "/grid.js": "grid.js",
                "/features.js": "features.js",
                "/table-tools.js": "table-tools.js",
                "/sheet-tools.js": "sheet-tools.js",
                "/alerts.js": "alerts.js",
                "/output.js": "output.js", "/payroll.js": "payroll.js", "/insights.js": "insights.js", "/workspace.js": "workspace.js",
                "/access.js": "access.js",
                "/format-tools.js": "format-tools.js",
                "/app.css": "app.css",
                "/easy-mode.css": "easy-mode.css",
                "/easy-mode.js": "easy-mode.js",
                "/easy-hubs.js": "easy-hubs.js",
                "/easy-menu.js": "easy-menu.js",
                "/easy-assistant.js": "easy-assistant.js",
                "/linked-leave.js": "linked-leave.js",
                "/document-workflows.js": "document-workflows.js",
                "/data-continuity.js": "data-continuity.js",
                "/access-services.js": "access-services.js",
                "/operations.js": "operations.js",
                "/easy-usability.js": "easy-usability.js",
                "/live-assistant.js": "live-assistant.js",
            }
            filename = static_files.get(path)
            if filename:
                body = (self.server.web_path / filename).read_bytes()
                if filename == "index.html" and self.server.easy_mode:
                    body = body.replace(
                        b"</head>",
                        b'<link rel="stylesheet" href="/easy-mode.css"></head>',
                    ).replace(
                        b"</body>",
                        b'<script src="/easy-assistant.js" defer></script>'
                        b'<script src="/linked-leave.js" defer></script>'
                        b'<script src="/document-workflows.js" defer></script>'
                        b'<script src="/data-continuity.js" defer></script>'
                        b'<script src="/operations.js" defer></script>'
                        b'<script src="/easy-mode.js" defer></script>'
                        b'<script src="/easy-hubs.js" defer></script>'
                        b'<script src="/easy-menu.js" defer></script></body>',
                    )
                content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                if filename.endswith((".html", ".js", ".css")):
                    content_type += "; charset=utf-8"
                self._send(body, content_type)
                return
            self._json({"error": "Sayfa bulunamadı."}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            self._internal_error("GET", path, exc)

    def _schedule_suggestion_check(self, payload: dict[str, Any]) -> None:
        if set(payload) != {"month", "entries"}:
            raise ValueError("Vardiya önerisi denetimi geçersiz.")
        month = scheduling.validate_month(payload["month"])
        with self._store() as store:
            people = store.people()
            proposed_entries = scheduling.normalize_schedule_entries(month, payload["entries"], people)
            baseline_entries = store.schedule_month(month)["entries"]
            settings = store.compliance_settings()
            plan = store.planning_settings()
            profiles = store.employee_profiles()
            names = {int(person["id"]): person["name"] for person in people}
            history = []
            previous = scheduling.date.fromisoformat(f"{month}-01")
            for _ in range(2):
                previous = (previous - scheduling.timedelta(days=1)).replace(day=1)
                history.extend(store.schedule_month(previous.strftime("%Y-%m"))["entries"])

            def suggestion_counts(entries):
                skills = planning.skill_items(month, entries, names, profiles, plan["skill_requirements"])
                staffing = compliance.staffing_items(
                    month, history + entries, settings.get("min_staffing", {}),
                    settings.get("min_staffing_windows", []),
                )
                return {"skill_coverage": len(skills), "staffing": len(staffing)}

            self._json({
                "baseline": suggestion_counts(baseline_entries),
                "proposed": suggestion_counts(proposed_entries),
            })

    def _overtime_transition(self, payload: dict, *, save: bool) -> None:
        expected = {"month", "settings"} | ({"preview"} if save else set())
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("Mesai kuralı geçiş isteği geçersiz.")
        month = scheduling.validate_month(payload["month"])
        if month <= date.today().strftime("%Y-%m"):
            raise ValueError("Eski hesapları korumak için gelecek bir yürürlük ayı seçin.")
        proposed = scheduling.normalize_overtime_settings(payload["settings"])
        with self._store() as store:
            store.connection.execute("BEGIN IMMEDIATE" if save else "BEGIN")
            try:
                versions = store._setting("overtime_rule_versions", {})
                if versions and month <= max(versions):
                    raise ValueError("Yürürlük ayı son kayıtlı kuraldan sonra olmalıdır.")
                if any(period >= month for period in store.locked_periods()):
                    raise ValueError("Bu geçiş kilitli bir dönemi etkiliyor.")
                previous = scheduling.normalize_overtime_settings(store.overtime_settings(month))
                if previous == proposed:
                    raise ValueError("Mesai kuralında değişiklik yok.")
                first = date.fromisoformat(f"{month}-01")
                for rules in (previous, proposed):
                    if (not rules["daily_limit_hours"] and rules["weekly_limit_hours"]
                            and first.weekday() != rules["week_start"]):
                        raise ValueError("Haftalık kural geçişi için ilk günü çalışma haftasının başlangıcına denk gelen ayı seçin.")
                totals = []
                for rules in (previous, proposed):
                    attendance, _, _ = self._attendance_for(store, month, settings=rules)
                    totals.append(round(sum(row["overtime_computed_hours"] for row in attendance["rows"]), 2))
                preview = operations._fingerprint({
                    "month": month, "old": previous, "new": proposed, "totals": totals,
                    "sources": operations.current_snapshot(store, month)["attendance_fingerprint"],
                })
                if save:
                    if payload["preview"] != preview:
                        raise ValueError("Önizlemenin kaynağı değişti; geçişi yeniden önizleyin.")
                    store._write_setting("overtime_rule_versions", {**versions, month: proposed})
                    store.record_change("settings.overtime_transition", "Yürürlük tarihli mesai kuralı kaydedildi",
                                        {"effective_date": first.isoformat()})
                    store.connection.commit()
                else:
                    store.connection.rollback()
            except Exception:
                store.connection.rollback()
                raise
        self._json({"month": month, "effective_date": first.isoformat(), "previous_hours": totals[0],
                    "proposed_hours": totals[1], "preview": preview})

    def _compare_schedule_plans(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict) or set(payload) != {"month", "a", "b"}:
            raise ValueError("A/B karşılaştırma isteği geçersiz.")
        month = scheduling.validate_month(payload["month"])
        with self._store() as store:
            store.connection.execute("BEGIN")
            try:
                staff = store.people()
                names = {person["id"]: person["name"] for person in staff}
                periods = {period: store.schedule_month(period)["entries"]
                           for period in scheduling.adjacent_months(month)}
                result = {}
                for key in ("a", "b"):
                    entries = (periods[month] if payload[key] is None else
                               scheduling.normalize_schedule_entries(month, payload[key], staff))
                    quality = operations.quality_report(store, month, entries)
                    attendance, _, _ = self._attendance_for(
                        store, month, [entry for period, rows in periods.items()
                                       for entry in (entries if period == month else rows)],
                    )
                    weekend = planning.fairness({month: entries}, names)["people"]
                    published = store._setting("published_plans", {}).get(month)
                    published_available = isinstance(published, dict) and isinstance(published.get("entries"), list)
                    published_changes = operations._publication_changes(
                        month, published["entries"] if published_available else [], entries, names,
                    ) if published_available else []
                    change_dates = sorted({item["date"] for item in published_changes})
                    earliest_change = change_dates[0] if change_dates else ""
                    result[key] = {
                        "staffing_findings": sum(item["type"] in ("min_staffing", "staffing")
                                                 for item in quality["findings"]),
                        "overtime_hours": round(sum(row["overtime_computed_hours"]
                                                    for row in attendance["rows"]), 2),
                        "weekend": [{"employee_id": row["employee_id"], "name": row["name"],
                                     "shifts": row["weekend"]} for row in weekend],
                        "published_plan_available": published_available,
                        "published_change_count": len(published_changes),
                        "published_affected_people": len({item["employee_id"] for item in published_changes}),
                        "earliest_change_date": earliest_change,
                        "days_until_earliest_change": (
                            (date.fromisoformat(earliest_change) - date.today()).days if earliest_change else None
                        ),
                        "published_changes": published_changes,
                    }
            finally:
                store.connection.rollback()
        self._json({"month": month, **result})

    def _route_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/schedule/suggestion-check":
                self._schedule_suggestion_check(self._read_json())
                return
            if path == "/api/schedule/compare-plans":
                self._compare_schedule_plans(self._read_json())
                return
            if self._extra_write("POST", path):
                return
            if path in ("/api/reports/payroll", "/api/reports/person-pdf", "/api/reports/yearly",
                        "/api/reports/person-program", "/api/reports/month-package",
                        "/api/reports/payroll-program", "/api/reports/manager-summary",
                        "/api/reports/problem-report", "/api/reports/settings-export"):
                if self._read_json() != {}:
                    raise ValueError("Rapor isteği geçersiz.")
                if self.server.save_form is None:
                    raise ValueError("Masaüstü kaydetme penceresi kullanılamıyor.")
                content, extension, filename, _ = self._report_file(path, parse_qs(parsed.query))
                self._json(self.server.save_form(extension, content, filename))
                return
            if path == "/api/backups":
                if self._read_json() != {}:
                    raise ValueError("Yedekleme isteği geçersiz.")
                with self._store() as store:
                    backup = store.create_backup("manual")
                self._json({"backup": backup}, 201)
                return
            if path == "/api/backups/verify":
                payload = self._read_json()
                if set(payload) != {"id"}:
                    raise ValueError("Yedek seçimi geçersiz.")
                with self._store() as store:
                    backup = store.verify_backup(payload["id"])
                self._json({"backup": backup})
                return
            if path == "/api/backups/restore":
                payload = self._read_json()
                if set(payload) != {"id"}:
                    raise ValueError("Yedek seçimi geçersiz.")
                with self._store() as store:
                    result = store.restore_backup(payload["id"])
                self._json(result)
                return
            if path == "/api/save-jpeg":
                payload = self._read_json(25_000_000)
                if set(payload) != {"content"} or not isinstance(payload["content"], str):
                    raise ValueError("JPEG verisi geçersiz.")
                try:
                    content = base64.b64decode(payload["content"], validate=True)
                    with Image.open(BytesIO(content)) as image:
                        if image.format != "JPEG" or image.width * image.height > 32_000_000:
                            raise ValueError("JPEG boyutu veya biçimi geçersiz.")
                        image.verify()
                except (binascii.Error, UnidentifiedImageError, OSError) as exc:
                    raise ValueError("JPEG dosyası okunamadı.") from exc
                if self.server.save_form is None:
                    raise ValueError("Masaüstü kaydetme penceresi kullanılamıyor.")
                self._json(self.server.save_form("jpg", content))
                return
            if path == "/api/table-templates/save":
                payload = self._read_json()
                if set(payload) != {"kind"}:
                    raise ValueError("Şablon isteği geçersiz.")
                if self.server.save_form is None:
                    raise ValueError("Masaüstü kaydetme penceresi kullanılamıyor.")
                kind = payload["kind"]
                content = scheduling.table_template_bytes(kind)
                self._json(self.server.save_form(
                    "xlsx", content, f"{'VARDIYA' if kind == 'schedule' else 'PUANTAJ'}_SABLONU.xlsx"
                ))
                return
            if path in ("/api/table-templates/upload", "/api/table-templates/reset"):
                upload = path.endswith("upload")
                payload = self._read_json(7_000_000)
                if set(payload) != ({"kind", "content"} if upload else {"kind"}):
                    raise ValueError("Şablon isteği geçersiz.")
                kind = payload["kind"]
                if upload:
                    if not isinstance(payload["content"], str):
                        raise ValueError("Şablon dosyası geçersiz.")
                    try:
                        content = base64.b64decode(payload["content"], validate=True)
                    except binascii.Error as exc:
                        raise ValueError("Şablon dosyası okunamadı.") from exc
                    status = scheduling.save_table_template(kind, content)
                else:
                    status = scheduling.reset_table_template(kind)
                with self._store() as store, store.connection:
                    store.record_change(
                        "table_template.upload" if upload else "table_template.reset",
                        ("Tablo şablonu yüklendi" if upload else "Tablo şablonu varsayılana döndürüldü")
                        + f": {'Vardiya' if kind == 'schedule' else 'Puantaj'}",
                        {"kind": kind},
                    )
                self._json(status)
                return
            if path == "/api/forms":
                payload = self._read_json(7_000_000)
                with self._store() as store:
                    store.create_backup("automatic")
                    template_id = forms.save_template(store.connection, payload)
                    with store.connection:
                        store.record_change(
                            "form_template.save",
                            "Form şablonu kaydedildi",
                            {"template_id": template_id},
                        )
                self._json({"id": template_id})
                return
            if path == "/api/forms/status":
                payload = self._read_json()
                if not isinstance(payload, dict) or set(payload) != {"id", "status"}:
                    raise ValueError("Form şablonu durumu geçersiz.")
                with self._store() as store:
                    store.create_backup("automatic")
                    status = forms.set_template_status(store.connection, payload["id"], payload["status"])
                    with store.connection:
                        store.record_change(
                            "form_template.status",
                            f"Form şablonu durumu: {payload['status']}",
                            {"template_id": payload["id"], "status": status},
                        )
                self._json({"id": payload["id"], "status": status})
                return
            if path == "/api/forms/delete":
                payload = self._read_json()
                if set(payload) != {"id"}:
                    raise ValueError("Form seçimi geçersiz.")
                with self._store() as store:
                    store.create_backup("automatic")
                    forms.delete_template(store.connection, payload["id"])
                    with store.connection:
                        store.record_change(
                            "form_template.delete",
                            "Form şablonu silindi",
                            {"template_id": payload["id"]},
                        )
                self._json({"ok": True})
                return
            if path == "/api/forms/template-save":
                payload = self._read_json()
                if set(payload) != {"id"}:
                    raise ValueError("Form seçimi geçersiz.")
                if self.server.save_form is None:
                    raise ValueError("Masaüstü kaydetme penceresi kullanılamıyor.")
                with self._store() as store:
                    item = forms.get_template(store.connection, payload["id"])
                    extension, content = item["extension"], item["content"]
                self._json(self.server.save_form(extension, content))
                return
            if path == "/api/forms/history":
                payload = self._read_json()
                if set(payload) != {
                    "id", "person_id", "start", "end", "category",
                    "section", "description", "file_name",
                }:
                    raise ValueError("Form geçmişi bilgisi geçersiz.")
                form_payload = {key: payload[key] for key in (
                    "id", "person_id", "start", "end", "category",
                    "section", "description",
                )}
                self._generate_form(form_payload)
                self._record_form_generation(form_payload, payload["file_name"])
                self._json({"ok": True})
                return
            if path in ("/api/forms/preview", "/api/forms/generate", "/api/forms/save"):
                payload = self._read_json()
                content, extension = self._generate_form(payload)
                if path == "/api/forms/preview":
                    self._json({
                        "text": forms.preview(content, extension),
                        "html": forms.preview_layout(content, extension),
                    })
                elif path == "/api/forms/save":
                    if self.server.save_form is None:
                        raise ValueError("Masaüstü kaydetme penceresi kullanılamıyor.")
                    result = self.server.save_form(extension, content)
                    if not result.get("cancelled"):
                        self._record_form_generation(payload, result.get("path", ""))
                    self._json(result)
                else:
                    self._send(content, "application/octet-stream", filename=f"form.{extension}")
                return
            if path == "/api/import-extract":
                payload = self._read_json(7_000_000)
                if (set(payload) != {"filename", "content", "force_ocr"}
                    or not isinstance(payload["force_ocr"], bool)):
                    raise ValueError("Belge okuma bilgisi geçersiz.")
                self._json(document_import.extract_document(payload["filename"], payload["content"], payload["force_ocr"]))
                return
            if path == "/api/import-mapped-preview":
                payload = self._read_json(2_000_000)
                if not {"month", "text", "mapping"}.issubset(payload) or set(payload) - {
                    "month", "text", "mapping", "fields", "existing", "partial", "filename", "page"
                }:
                    raise ValueError("Elle eşleştirme bilgisi geçersiz.")
                partial = payload.get("partial", False)
                if not isinstance(partial, bool):
                    raise ValueError("Kısmi içe aktarma seçeneği geçersiz.")
                parsed = document_import.parse_mapped_table(payload, partial=partial)
                rows = parsed["rows"] if partial else parsed
                review = parsed["review"] if partial else []
                rows = document_import.filter_import_rows(rows, payload.get("fields"), allow_empty=partial)
                self._json({
                    "rows": rows,
                    "comparison": document_import.compare_import_rows(rows, payload.get("existing", [])),
                    "review": review,
                })
                return
            if path == "/api/import-profile-headers":
                payload = self._read_json(2_000_000)
                if set(payload) != {"text", "mapping", "expected"}:
                    raise ValueError("Profil başlık karşılaştırma bilgisi geçersiz.")
                self._json(document_import.check_import_profile_headers(
                    payload["text"], payload["mapping"], payload["expected"],
                ))
                return
            if path == "/api/save-excel":
                payload = self._read_json()
                if (
                    not isinstance(payload, dict)
                    or set(payload) not in ({"kind", "month", "week"}, {"kind", "month", "week", "employee"})
                    or ("employee" in payload and (
                        not isinstance(payload["employee"], str) or len(payload["employee"]) > 80
                    ))
                ):
                    raise ValueError("Excel kaydetme bilgisi geçersiz.")
                if self.server.save_excel is None:
                    raise ValueError("Masaüstü kaydetme penceresi kullanılamıyor.")
                self._json(self.server.save_excel(
                    payload["kind"], payload["month"], payload["week"], payload.get("employee", "")
                ))
                return
            if path == "/api/archive/delete":
                payload = self._read_json()
                if (set(payload) != {"kind", "month", "revision"}
                    or payload["kind"] not in ("schedule", "attendance")
                    or not isinstance(payload["revision"], str) or not payload["revision"]):
                    raise ValueError("Arşiv silme bilgisi geçersiz.")
                month = scheduling.validate_month(payload["month"])
                with self._store() as store:
                    deleted = store.delete_archive_month(payload["kind"], month, payload["revision"])
                if not deleted:
                    self._json({"error": "Kayıt değişmiş veya silinmiş. Arşivi yenileyip tekrar deneyin."}, 409)
                    return
                self._json({"ok": True})
                return
            if path == "/api/archive/restore":
                payload = self._read_json()
                if (
                    not isinstance(payload, dict)
                    or set(payload) != {"kind", "month", "revision", "expected_revision"}
                    or payload["kind"] not in ("schedule", "attendance")
                    or not isinstance(payload["revision"], str) or not payload["revision"]
                    or len(payload["revision"]) > 64
                    or not isinstance(payload["expected_revision"], str)
                    or len(payload["expected_revision"]) > 64
                ):
                    raise ValueError("Arşiv sürümü geri yükleme bilgisi geçersiz.")
                month = scheduling.validate_month(payload["month"])
                with self._store() as store:
                    revision = store.restore_archive_revision(
                        payload["kind"], month, payload["revision"], payload["expected_revision"]
                    )
                if revision is None:
                    self._json({"error": "Arşiv değişmiş. Listeyi yenileyip tekrar deneyin."}, 409)
                    return
                self._json({"month": month, "revision": revision})
                return
            if path == "/api/archive/period-lock":
                payload = self._read_json()
                if (
                    set(payload) not in ({"month", "locked"}, {"month", "locked", "reason"})
                    or not isinstance(payload["locked"], bool)
                    or not isinstance(payload.get("reason", ""), str)
                ):
                    raise ValueError("Dönem kilit bilgisi geçersiz.")
                month = scheduling.validate_month(payload["month"])
                reason = " ".join(payload.get("reason", "").split())[:200]
                if not payload["locked"] and len(reason) < 5:
                    raise ValueError("Kapanmış dönemi yeniden açmak için gerekçe yazın (en az 5 karakter).")
                with self._store() as store:
                    if not any(item["month"] == month for item in store.archive_months()):
                        raise ValueError("Bu ay için arşiv kaydı bulunamadı.")
                    store.set_period_locked(month, payload["locked"], reason)
                self._json({"month": month, "locked": payload["locked"]})
                return
            if path == "/api/attendance-events":
                event_data = normalize_attendance_event(self._read_json())
                with self._store() as store:
                    event = store.add_attendance_event(**event_data)
                self._json({"event": event}, 201)
                return
            if path == "/api/attendance-events/delete":
                payload = self._read_json()
                if set(payload) != {"id"} or not isinstance(payload["id"], str) or not payload["id"]:
                    raise ValueError("İzin/devamsızlık kayıt seçimi geçersiz.")
                with self._store() as store:
                    deleted = store.delete_attendance_event(payload["id"])
                if not deleted:
                    self._json({"error": "Kayıt bulunamadı veya daha önce silindi."}, 404)
                    return
                self._json({"ok": True})
                return
            if path == "/api/import-preview":
                payload = self._read_json(7_000_000)
                if not {"filename", "content", "month"}.issubset(payload) or set(payload) - {
                    "filename", "content", "month", "fields", "existing", "partial"
                }:
                    raise ValueError("İçe aktarma bilgisi geçersiz.")
                partial = payload.get("partial", False)
                if not isinstance(partial, bool):
                    raise ValueError("Kısmi içe aktarma seçeneği geçersiz.")
                parsed = document_import.parse_document(
                    payload["filename"], payload["content"], payload["month"], partial=partial
                )
                rows = parsed["rows"] if partial else parsed
                review = parsed["review"] if partial else []
                rows = document_import.filter_import_rows(rows, payload.get("fields"), allow_empty=partial)
                self._json({
                    "rows": rows,
                    "comparison": document_import.compare_import_rows(rows, payload.get("existing", [])),
                    "review": review,
                })
                return
            if path == "/api/people/bulk":
                payload = self._read_json()
                if set(payload) != {"names"} or not isinstance(payload["names"], list):
                    raise ValueError("Toplu personel listesi geçersiz.")
                with self._store() as store:
                    people = store.add_people(payload["names"])
                self._json({"people": people}, 201)
                return
            if path == "/api/people":
                payload = self._read_json()
                if set(payload) != {"name"} or not isinstance(payload["name"], str):
                    raise ValueError("Personel bilgisi geçersiz.")
                with self._store() as store:
                    person = store.add_person(payload["name"])
                self._json({"person": person}, 201)
                return
            person_path = path.removeprefix("/api/people/")
            if person_path.endswith("/delete"):
                person_id_text = person_path.removesuffix("/delete").rstrip("/")
                if not person_id_text.isdigit():
                    raise ValueError("Personel numarası geçersiz.")
                payload = self._read_json()
                if not set(payload) <= {"force"} or not isinstance(payload.get("force", False), bool):
                    raise ValueError("Silme isteği geçersiz.")
                with self._store() as store:
                    try:
                        result = store.delete_person(int(person_id_text), payload.get("force", False))
                    except PermissionError as exc:
                        self._json({"error": str(exc), "has_records": True,
                                    "records": store.person_record_summary(int(person_id_text))}, 409)
                        return
                self._json(result)
                return
            if person_path.endswith("/active"):
                person_id_text = person_path.removesuffix("/active").rstrip("/")
                if not person_id_text.isdigit():
                    raise ValueError("Personel numarası geçersiz.")
                payload = self._read_json()
                if set(payload) != {"active"} or not isinstance(payload["active"], bool):
                    raise ValueError("Personel durumu geçersiz.")
                with self._store() as store:
                    store.set_person_active(int(person_id_text), payload["active"])
                self._json({"ok": True})
                return
            self._json({"error": "İstek yolu bulunamadı."}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            if isinstance(exc, sqlite3.IntegrityError) and str(exc) == schedule_protection.ERROR:
                self._json({"error": str(exc)}, 409)
                return
            self._internal_error("POST", path, exc)

    def _route_PUT(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if self._extra_write("PUT", path):
                return
            if path == "/api/schedule-months":
                payload = self._read_json()
                if not {"months"} <= set(payload) <= {"months", "merge_independent"} or not isinstance(payload["months"], dict) or not 1 <= len(payload["months"]) <= 3:
                    raise ValueError("Aylık çizelge kayıtları geçersiz.")
                merge = payload.get("merge_independent", False)
                if not isinstance(merge, bool):
                    raise ValueError("Ortak çalışma seçeneği geçersiz.")
                with self._store() as store:
                    import schedule_collaboration
                    people = store.people()
                    months = {}
                    for month, data in payload["months"].items():
                        validated_days = None
                        scheduling.validate_month(month)
                        if not isinstance(data, dict) or not isinstance(data.get("revision"), str):
                            raise ValueError("Aylık çizelge kaydı geçersiz.")
                        if set(data) == {"revision", "entries"}:
                            entries = data["entries"]
                        elif set(data) == {"revision", "changes"}:
                            current = store.schedule_month(month)
                            baseline = current["entries"]
                            with self.server.schedule_normalized_cache_lock:
                                cached = self.server.schedule_normalized_cache.get(month)
                            # Cached cells are trusted only for the exact committed revision.
                            if cached and cached[0] == data["revision"] == current["revision"]:
                                validated_days = cached[1]
                            if merge and current["revision"] != data["revision"]:
                                archived = schedule_collaboration.archived_entries(
                                    store.connection, month, data["revision"],
                                )
                                if archived is not None:
                                    baseline = archived
                            entries = schedule_collaboration.apply_entry_changes(baseline, data["changes"])
                        else:
                            raise ValueError("Aylık çizelge kaydı geçersiz.")
                        months[month] = {
                            "revision": data["revision"],
                            "entries": scheduling.normalize_schedule_entries(
                                month, entries, people, validated_days=validated_days),
                        }
                    revisions = store.save_schedule_months(months, merge_independent=merge)
                    canonical = store.schedule_saved_entries
                    conflicts = store.linked_leave_conflicts
                    merge_conflicts = store.schedule_merge_conflicts
                    merged_months = store.schedule_merged_months
                if revisions is None:
                    self._json({"error": "Çizelgede ortak çalışma çakışması var. Hiçbir ay kaydedilmedi." if merge_conflicts else
                                "Haftanın aylarından biri başka bir pencerede değiştirildi. Yenileyip tekrar deneyin.",
                                "schedule_conflicts": merge_conflicts}, 409)
                    return
                with self.server.schedule_normalized_cache_lock:
                    for month, revision in revisions.items():
                        # Linked-leave reconciliation or merging may change the validated proposal.
                        if canonical[month] == months[month]["entries"]:
                            self.server.schedule_normalized_cache[month] = (
                                revision, {(row["row_id"], day): entry.copy()
                                           for row in canonical[month] for day, entry in row["days"].items()})
                        else:
                            self.server.schedule_normalized_cache.pop(month, None)
                    while len(self.server.schedule_normalized_cache) > 12:
                        self.server.schedule_normalized_cache.pop(next(iter(self.server.schedule_normalized_cache)))
                self._json({
                    "revisions": revisions, "entries": canonical,
                    "linked_leave_conflicts": conflicts,
                    "merged_months": merged_months,
                })
                return
            if path == "/api/workbook-settings":
                settings = scheduling.normalize_workbook_settings(self._read_json())
                with self._store() as store:
                    store.save_workbook_settings(settings)
                self._json(settings)
                return
            if path == "/api/backup-settings":
                payload = self._read_json()
                if set(payload) != {"keep_automatic"}:
                    raise ValueError("Yedek ayarı isteği geçersiz.")
                with self._store() as store:
                    self._json(store.save_backup_settings(payload["keep_automatic"]))
                return
            if path == "/api/user-settings":
                payload = self._read_json()
                if not isinstance(payload, dict) or set(payload) != {"user_name"}:
                    raise ValueError("Kullanıcı ayarı isteği geçersiz.")
                with self._store() as store:
                    name = store.save_user_name(payload["user_name"])
                self._json({"user_name": name})
                return
            if path == "/api/schedule-validation-settings":
                settings = scheduling.normalize_schedule_validation_settings(self._read_json())
                with self._store() as store:
                    store.save_schedule_validation_settings(settings)
                self._json(settings)
                return
            if path == "/api/overtime-settings":
                settings = scheduling.normalize_overtime_settings(self._read_json())
                with self._store() as store:
                    store.save_overtime_settings(settings)
                self._json(settings)
                return
            if path == "/api/balances":
                payload = self._read_json()
                expected = {
                    "year", "employee_id", "annual_leave_opening_days", "overtime_opening_hours",
                }
                if set(payload) != expected:
                    raise ValueError("Açılış bakiyesi kaydı geçersiz.")
                with self._store() as store:
                    opening = store.save_balance_opening(
                        payload["employee_id"], payload["year"],
                        payload["annual_leave_opening_days"], payload["overtime_opening_hours"],
                    )
                self._json({"opening": opening})
                return
            if path == "/api/import-profiles":
                payload = self._read_json()
                if set(payload) != {"profiles"}:
                    raise ValueError("İçe aktarma profilleri geçersiz.")
                profiles = normalize_import_profiles(payload["profiles"])
                with self._store() as store:
                    store.save_import_profiles(profiles)
                self._json({"profiles": profiles})
                return
            if path == "/api/form-draft":
                payload = self._read_json()
                if set(payload) != {
                    "id", "person_id", "category", "start", "end", "section", "description",
                }:
                    raise ValueError("Form taslağı alanları geçersiz.")
                if (
                    not isinstance(payload["id"], str) or len(payload["id"]) > 80
                    or not isinstance(payload["person_id"], int)
                    or isinstance(payload["person_id"], bool) or payload["person_id"] < 0
                    or payload["category"] not in ("G", "S", "Ü")
                    or not isinstance(payload["section"], str) or len(payload["section"]) > 80
                    or not isinstance(payload["description"], str) or len(payload["description"]) > 500
                ):
                    raise ValueError("Form taslağı alanları geçersiz.")
                for key in ("start", "end"):
                    value = payload[key]
                    if not isinstance(value, str) or len(value) > 10:
                        raise ValueError("Taslak tarihleri geçersiz.")
                    if value:
                        try:
                            if date.fromisoformat(value).isoformat() != value:
                                raise ValueError("Taslak tarihleri YYYY-AA-GG biçiminde olmalıdır.")
                        except ValueError as exc:
                            raise ValueError("Taslak tarihleri YYYY-AA-GG biçiminde olmalıdır.") from exc
                if payload["start"] and payload["end"] and payload["start"] > payload["end"]:
                    raise ValueError("Bitiş tarihi başlangıç tarihinden önce olamaz.")
                with self._store() as store:
                    if payload["person_id"] and not any(
                        person["id"] == payload["person_id"] for person in store.people()
                    ):
                        raise ValueError("Taslak personeli bulunamadı.")
                    store.save_form_draft(payload)
                self._json({"draft": payload})
                return
            if path == "/api/quick-entries":
                payload = self._read_json()
                if set(payload) != {"entries"}:
                    raise ValueError("Hızlı giriş listesi geçersiz.")
                normalized = scheduling.normalize_quick_entries(payload["entries"])
                with self._store() as store:
                    store.save_quick_entries(normalized)
                self._json({"entries": normalized})
                return
            if path == "/api/schedule-templates":
                payload = self._read_json()
                if set(payload) != {"templates"}:
                    raise ValueError("Vardiya şablonu listesi geçersiz.")
                with self._store() as store:
                    normalized = scheduling.normalize_schedule_templates(
                        payload["templates"], store.people()
                    )
                    store.save_schedule_templates(normalized)
                self._json({"templates": normalized})
                return
            if path not in ("/api/schedule", "/api/attendance"):
                self._json({"error": "İstek yolu bulunamadı."}, 404)
                return
            payload = self._read_json()
            key = "entries" if path == "/api/schedule" else "overrides"
            expected_keys = {"month", "revision", key}
            valid_keys = {frozenset(expected_keys)}
            if path == "/api/attendance":
                optional = ("deductions", "roster", "overtime", "merge_independent")
                if not (set(payload) - expected_keys) <= set(optional) or not expected_keys <= set(payload):
                    raise ValueError("Aylık çizelge kaydı geçersiz.")
                valid_keys.add(frozenset(payload))
            if frozenset(payload) not in valid_keys or not isinstance(payload.get("revision"), str):
                raise ValueError("Aylık çizelge kaydı geçersiz.")
            month = scheduling.validate_month(payload["month"])
            merge_attendance = payload.get("merge_independent", False)
            if not isinstance(merge_attendance, bool):
                raise ValueError("Puantaj birleştirme seçeneği geçersiz.")
            with self._store() as store:
                people = store.people()
                if path == "/api/schedule":
                    normalized = scheduling.normalize_schedule_entries(month, payload[key], people)
                    controls.assert_no_new_overlaps(
                        store.schedule_month(month)["entries"], normalized,
                        {person["id"]: person["name"] for person in people},
                    )
                    revision = store.save_schedule_month(month, normalized, payload["revision"])
                else:
                    normalized = scheduling.normalize_attendance_overrides(month, payload[key], people)
                    current = store.attendance_month(month)
                    deductions = (
                        scheduling.normalize_attendance_deductions(payload["deductions"], people)
                        if "deductions" in payload else current["deductions"]
                    )
                    if not self._has_permission("payroll", store):
                        if payload.get("deductions") and deductions != current["deductions"]:
                            self._json({"error": "Kesinti tutarını değiştirmek için bordro yetkisi gereklidir."}, 403)
                            return
                        deductions = current["deductions"]
                    revision = store.save_attendance_month(
                        month, normalized, deductions, payload["revision"],
                        scheduling.normalize_attendance_roster(payload["roster"], people)
                        if "roster" in payload else None,
                        scheduling.normalize_attendance_overtime(payload["overtime"], people)
                        if "overtime" in payload else None,
                        merge_independent=merge_attendance,
                    )
                    if merge_attendance and revision is None:
                        self._json({"error": "Puantajda çakışma var; hiçbir değişiklik kaydedilmedi.",
                                    "attendance_conflicts": [{"month": month, **item}
                                                             for item in store.attendance_merge_conflicts]}, 409)
                        return
                    if merge_attendance and store.attendance_merged:
                        self._json({"revision": revision, "merged": True})
                        return
            if revision is None:
                self._json({"error": "Bu ay başka bir pencerede değiştirildi. Yenileyip tekrar deneyin."}, 409)
                return
            self._json({"revision": revision})
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:
            if isinstance(exc, sqlite3.IntegrityError) and str(exc) == schedule_protection.ERROR:
                self._json({"error": str(exc)}, 409)
                return
            self._internal_error("PUT", path, exc)


def create_server(data_path: str | Path, web_path: str | Path, port: int = 0) -> ScheduleHTTPServer:
    scheduling.set_template_override_dir(Path(data_path).parent / "table_templates")
    reset_flag = Path(data_path).parent / "reset_users.txt"
    if reset_flag.exists() and Path(data_path).exists():
        # Parolası unutulduğunda kurtarma: dosya varsa kullanıcı listesi temizlenir.
        with Store(Path(data_path)) as store, store.connection:
            store._write_setting("users", [])
            store.record_change("settings.users", "reset_users.txt ile kullanıcılar sıfırlandı (parola koruması kapandı)")
        try:
            reset_flag.unlink()
        except OSError:
            print(f"[VardiyaPuantaj] Uyarı: {reset_flag} silinemedi; her açılışta kullanıcılar yeniden sıfırlanır.")
        else:
            print("[VardiyaPuantaj] reset_users.txt ile kullanıcı hesapları sıfırlandı; dosya kaldırıldı.")
    if Path(data_path).exists():
        record_version_start(Path(data_path))
        with Store(Path(data_path)) as store:
            operations.process_due_lifecycle(store)
        try:
            with Store(Path(data_path)) as store:
                store.close_exited_people()
        except Exception:  # noqa: BLE001 - açılış asla bu yüzden durmamalı
            pass
    return ScheduleHTTPServer(("127.0.0.1", port), Path(data_path), Path(web_path))


def record_version_start(data_path: Path) -> dict:
    """Sürüm değiştiyse açılışta veriyi yedekler ve yeni sürümü kaydeder."""
    from version import APP_VERSION
    try:
        with Store(data_path) as store:
            info = store._setting("app_version", {})
            previous = info.get("current", "") if isinstance(info, dict) else ""
            if previous == APP_VERSION:
                return {"changed": False}
            backup = store.create_backup("before-update") if previous else None
            with store.connection:
                store._write_setting("app_version", {
                    "current": APP_VERSION, "previous": previous,
                    "backup": backup["id"] if backup else "", "seen_whats_new": False,
                })
                if previous:
                    store.record_change("app.version", f"Uygulama {previous} → {APP_VERSION} sürümüne geçti", {"backup": backup["id"]})
            return {"changed": True, "previous": previous}
    except Exception:  # noqa: BLE001 - açılışı asla engellememeli
        return {"changed": False}
