from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import scheduling
from server import create_server


class DesktopAPI:
    def __init__(self, base_url: str, internal_token: str = ""):
        self.base_url = base_url
        self.internal_token = internal_token
        self._window = None

    def save_excel(self, kind: str, month: str, week: int = 0, employee: str = ""):
        if kind not in ("schedule", "attendance"):
            raise ValueError("Excel çıktı türü geçersiz.")
        scheduling.validate_month(month)
        if not isinstance(week, int) or isinstance(week, bool) or not 0 <= week < len(scheduling.week_starts(month)):
            raise ValueError("Hafta numarası geçersiz.")
        import webview
        if self._window is None:
            raise RuntimeError("Uygulama penceresi hazır değil.")
        params = urlencode({"kind": kind, "month": month, "week": week, "employee": employee})
        try:
            naming = self._internal_json(f"/api/export-name?{params}")
        except Exception:
            naming = {}
        suffix = "_PERSONEL" if employee else ""
        filename = naming.get("filename") or f"{'VARDIYA_LISTESI' if kind == 'schedule' else 'PUANTAJ'}_{month}{suffix}.xlsx"
        folder = naming.get("folder") or ""
        options = {"save_filename": filename, "file_types": ("Excel dosyası (*.xlsx)",)}
        if folder and Path(folder).is_dir():
            options["directory"] = folder
        selected = self._window.create_file_dialog(webview.SAVE_DIALOG, **options)
        if not selected:
            return {"cancelled": True}
        path = Path(selected if isinstance(selected, str) else selected[0])
        if path.suffix.lower() != ".xlsx":
            raise ValueError("Dosya uzantısı .xlsx olmalıdır.")
        self._remember_folder(path.parent)
        request = Request(f"{self.base_url}/api/export?{params}", headers={"X-VP-Internal": self.internal_token})
        with urlopen(request, timeout=60) as response:
            contents = response.read()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".vardiya-", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                file.write(contents)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return {"cancelled": False, "path": str(path)}

    def _internal_json(self, path: str, method: str = "GET", payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"X-VP-Internal": self.internal_token, "Content-Type": "application/json"}
        with urlopen(Request(f"{self.base_url}{path}", data=data, method=method, headers=headers), timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def _remember_folder(self, folder: Path) -> None:
        try:
            current = self._internal_json("/api/ui-settings")["settings"].get("export") or {}
            if current.get("last_folder") != str(folder):
                self._internal_json("/api/ui-settings", "PUT", {"export": {
                    "filename_template": current.get("filename_template", ""), "last_folder": str(folder),
                }})
        except Exception:
            pass

    def save_form(self, extension: str, contents: bytes, filename: str | None = None):
        if extension not in ("docx", "xlsx", "jpg", "pdf", "csv", "zip", "ics"):
            raise ValueError("Form dosyası türü geçersiz.")
        import webview
        if self._window is None:
            raise RuntimeError("Uygulama penceresi hazır değil.")
        label = {"docx": "Word", "xlsx": "Excel", "jpg": "JPEG", "pdf": "PDF", "csv": "CSV", "zip": "ZIP",
                 "ics": "Takvim"}[extension]
        options = {
            "save_filename": filename or f"{'TABLO' if extension == 'jpg' else 'FORM'}.{extension}",
            "file_types": (f"{label} dosyası (*.{extension})",),
        }
        try:
            folder = (self._internal_json("/api/ui-settings")["settings"].get("export") or {}).get("last_folder", "")
        except Exception:
            folder = ""
        if folder and Path(folder).is_dir():
            options["directory"] = folder
        selected = self._window.create_file_dialog(webview.SAVE_DIALOG, **options)
        if not selected:
            return {"cancelled": True}
        path = Path(selected if isinstance(selected, str) else selected[0])
        if path.suffix.lower() != "." + extension:
            raise ValueError(f"Dosya uzantısı .{extension} olmalıdır.")
        self._remember_folder(path.parent)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".vardiya-", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                file.write(contents)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return {"cancelled": False, "path": str(path)}


def app_root() -> Path:
    return Path(__file__).resolve().parent


def _migrate_legacy_data(source: Path, target: Path) -> None:
    if target.exists() or not source.is_dir():
        return
    created = False
    installed = False
    try:
        target.mkdir()
        created = True
        shutil.copytree(source, target, dirs_exist_ok=True)
        import workspace_tools
        database = workspace_tools.resolve_data_file(target / "vardiya_puantaj.sqlite3")
        if database.is_file():
            uri = database.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=5)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
            finally:
                connection.close()
            if integrity is None or integrity[0] != "ok":
                raise ValueError("Eski uygulama veritabanı bütünlük denetiminden geçmedi.")
        installed = True
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise RuntimeError(
            f"Eski uygulama verileri {target} konumuna güvenli biçimde taşınamadı; kaynak veriler korundu: {exc}"
        ) from exc
    finally:
        if created and not installed and target.exists():
            shutil.rmtree(target, ignore_errors=True)


def _legacy_data_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data is None:
        local_app_data = str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / "VardiyaPuantaj"


def data_file() -> Path:
    configured = os.environ.get("VARDIYA_PUANTAJ_DATA_DIR")
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent / "data"
        _migrate_legacy_data(_legacy_data_directory(), root)
    elif configured:
        root = Path(configured)
    elif os.name == "nt":
        root = app_root() / "data"
        _migrate_legacy_data(_legacy_data_directory(), root)
    else:
        root = Path.home() / ".local" / "share" / "VardiyaPuantaj"
    import workspace_tools
    return workspace_tools.resolve_data_file(root / "vardiya_puantaj.sqlite3")


def _write_update_startup_status(status: str, message: str = "") -> None:
    status_path = os.environ.get("VARDIYA_PUANTAJ_UPDATE_STATUS_PATH", "")
    if not status_path or status not in {"ready", "failed"}:
        return
    path = Path(status_path)
    if not path.name.startswith("vardiya-update-") or path.suffix.lower() != ".json":
        return
    try:
        if path.parent.resolve() != Path(tempfile.gettempdir()).resolve():
            return
    except OSError:
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps({
            "status": status, "message": message[:160],
        }, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        print(f"Güncelleme başlangıç durumu kaydedilemedi: {type(exc).__name__}", file=sys.stderr)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def main(*, data_path: Path | None = None, easy_mode: bool = False) -> int:
    root = app_root()
    server = create_server(data_path if data_path is not None else data_file(), root / "web")
    server.easy_mode = easy_mode
    thread = threading.Thread(target=server.serve_forever, name="vardiya-puantaj-http", daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        import webview
    except ImportError:
        _write_update_startup_status("failed", "Arayüz bağımlılığı bulunamadı.")
        server.shutdown()
        print("Uygulama penceresi için pywebview paketini kurun: python -m pip install -r requirements.txt")
        return 1
    mobile = None
    try:
        config_path = server.data_path.parent / "mobile-https.json"
        mobile_notice = ""
        import mobile_service
        server.mobile_diagnostics = mobile_service.diagnostics(config_path)
        if config_path.is_file():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8-sig"))
                if not isinstance(config, dict) or set(config) != {"bind", "port", "origin", "certificate", "key"}:
                    raise ValueError("Ayar alanları geçersiz.")
                mobile = mobile_service.start(server.data_path, config["bind"], config["port"], config["origin"],
                    config_path.parent / config["certificate"], config_path.parent / config["key"])
                server.mobile_diagnostics = mobile_service.diagnostics(config_path, runtime=mobile)
            except (OSError, ValueError, TypeError) as exc:
                mobile_notice = " · Mobil HTTPS başlatılamadı"
                print(f"Mobil HTTPS başlatılamadı: {exc}", file=sys.stderr)
                server.mobile_diagnostics = mobile_service.diagnostics(config_path, startup_error=str(exc))
        api = DesktopAPI(url.rstrip("/"), server.internal_token)
        api._window = webview.create_window(
            "Vardiya Planı ve Puantaj" + (" — Kolay Kullanım" if easy_mode else "") + mobile_notice,
            url,
            js_api=api,
            width=1200,
            height=800,
            min_size=(900, 600),
            resizable=True,
        )
        server.save_excel = api.save_excel
        server.save_form = api.save_form
        if getattr(sys, "frozen", False):
            server.app_executable = Path(sys.executable).resolve()
        window = api._window
        if os.environ.get("VARDIYA_PUANTAJ_UPDATE_STATUS_PATH"):
            window.events.loaded += lambda: _write_update_startup_status("ready")
        server.request_exit = lambda: threading.Timer(0.8, window.destroy).start()
        if easy_mode:
            webview.start(private_mode=False, storage_path=str(server.data_path.parent / "browser"))
        else:
            webview.start()
    except Exception as exc:
        _write_update_startup_status("failed", f"Arayüz başlatılamadı ({type(exc).__name__}).")
        print(f"Uygulama penceresi başlatılamadı: {exc}", file=sys.stderr)
        return 1
    finally:
        if mobile is not None:
            mobile.shutdown()
            mobile.server_close()
        server.shutdown()
        server.server_close()
        server.lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
