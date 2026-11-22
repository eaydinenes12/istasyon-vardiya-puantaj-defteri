from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
REQUIRED_FILES = (
    "main.py",
    "server.py",
    "scheduling.py",
    "storage.py",
    "forms.py",
    "linked_leave.py",
    "document_workflows.py",
    "output_tracking.py",
    "data_continuity.py",
    "access_services.py",
    "operations.py",
    "usability.py",
    "schedule_collaboration.py",
    "VardiyaPuantaj.spec",
    "ocr_windows.ps1",
    "web/index.html",
    "web/app.js",
    "web/app.css",
    "web/easy-mode.js",
    "web/easy-hubs.js",
    "web/easy-menu.js",
    "web/easy-assistant.js",
    "web/linked-leave.js",
    "web/document-workflows.js",
    "web/data-continuity.js",
    "web/access-services.js",
    "web/operations.js",
    "web/easy-usability.js",
    "web/easy-mode.css",
    "templates/weekly_schedule.xlsx",
    "templates/attendance.xlsx",
    "templates/forms/manifest.json",
)


def validate_build_inputs(root: str | Path = ROOT) -> list[str]:
    base = Path(root)
    errors = [f"Gerekli paket kaynağı bulunamadı: {path}" for path in REQUIRED_FILES
              if not (base / path).is_file()]
    manifest_path = base / "templates" / "forms" / "manifest.json"
    if not manifest_path.is_file():
        return errors
    try:
        manifest: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [*errors, f"Form manifest dosyası okunamadı: {exc}"]
    if not isinstance(manifest, list) or not manifest:
        return [*errors, "Form manifest listesi boş veya geçersiz."]
    seen_ids: set[str] = set()
    for index, item in enumerate(manifest, start=1):
        if not isinstance(item, dict):
            errors.append(f"Form manifest kaydı {index} geçersiz.")
            continue
        form_id, filename, extension = item.get("id"), item.get("file"), item.get("extension")
        if not isinstance(form_id, str) or not form_id or form_id in seen_ids:
            errors.append(f"Form manifest kaydı {index} için kimlik boş veya yinelenmiş.")
        else:
            seen_ids.add(form_id)
        if (not isinstance(filename, str) or not filename or Path(filename).name != filename
                or not isinstance(extension, str) or extension not in {"docx", "xlsx"}
                or Path(filename).suffix.lower() != f".{extension}"):
            errors.append(f"Form manifest kaydı {index} için dosya adı veya uzantı geçersiz.")
            continue
        if not (manifest_path.parent / filename).is_file():
            errors.append(f"Form şablonu eksik: templates/forms/{filename}")
    return errors


def main() -> int:
    errors = validate_build_inputs()
    if errors:
        print("EXE paketi oluşturulmadı; eksik veya geçersiz kaynaklar:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("EXE paket kaynakları ve form şablonları doğrulandı.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
