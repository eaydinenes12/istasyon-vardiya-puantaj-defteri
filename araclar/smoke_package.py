"""Start an isolated copy of a frozen Windows release and check its own listener."""
import ctypes
from ctypes import wintypes
from contextlib import closing
import hashlib
import json
import os
from io import BytesIO
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.request import ProxyHandler, build_opener
from zipfile import ZipFile

from openpyxl import load_workbook


def check(exe, version, url):
    net = build_opener(ProxyHandler({}))

    def get(endpoint):
        with net.open(url + endpoint, timeout=25) as response:
            return response.read()

    def read(endpoint):
        return json.loads(get(endpoint))

    info = read("/api/app-info")
    assert info["runtime"] == "exe" and info["version"] == version and info["branch"]["id"] == "ana"
    assert Path(info["executable_path"]).samefile(exe)
    assert Path(info["data_path"]).samefile(exe.parent / "data" / "vardiya_puantaj.sqlite3")
    with closing(sqlite3.connect(Path(info["data_path"]).as_uri() + "?mode=ro", uri=True)) as conn:
        assert conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        schema = conn.execute("PRAGMA user_version").fetchone()[0]
    assert read("/api/health")["version"] == read("/api/whats-new")["items"][0]["version"] == version
    for week in range(5):
        sheet = read(f"/api/template?kind=schedule&sheet={week}")
        assert len(sheet["rows"]) == 35 and sheet["cells"][34][13]["value"] == "İSTASYON MÜDÜRÜ"
    palette = {}
    for month, categories in {
        "2026-03": {19: "official", 20: "religious", 21: "religious", 22: "religious"},
        "2026-05": {26: "official", **dict.fromkeys(range(27, 31), "religious")},
        "2026-10": {1: "weekday", 3: "weekend", 28: "official", 29: "official"},
    }.items():
        calendar = read(f"/api/attendance?month={month}")["calendar"]
        book = load_workbook(BytesIO(get(f"/api/export?kind=attendance&month={month}")))
        try:
            for day, kind in categories.items():
                assert calendar[f"{month}-{day:02}"] == kind
                fills = [book.active.cell(row, day + 2).fill.fgColor.rgb[-6:] for row in (2, 3)]
                assert fills[0] == fills[1] == palette.setdefault(kind, fills[0])
        finally:
            book.close()
    assert len(set(palette.values())) == 4
    preview = read("/api/reports/problem-report?preview=1")
    with ZipFile(BytesIO(get("/api/reports/problem-report?review=" + preview["review"]))) as archive:
        assert {key: archive.read(key).decode() for key in archive.namelist()} == preview["files"]
    with exe.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    result = {"version": version, "sha256": digest, "database_schema": schema,
              "schedule_sheets": 5, "calendar_dates": 13, "palette": palette, "runtime": info["runtime"]}
    return result


def window_for(exe):
    user, kernel = ctypes.windll.user32, ctypes.windll.kernel32
    user.IsWindowVisible.argtypes = (wintypes.HWND,)
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    @callback
    def visit(hwnd, _):
        if not user.IsWindowVisible(hwnd):
            return True
        title = ctypes.create_unicode_buffer(256)
        user.GetWindowTextW(hwnd, title, len(title))
        if "Puantaj" not in title.value:
            return True
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel.OpenProcess(0x1000, False, pid.value)
        if handle:
            try:
                path = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(path))
                if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)) and Path(path.value) == exe:
                    found.append((pid.value, hwnd))
            finally:
                kernel.CloseHandle(handle)
        return not found

    user.EnumWindows(visit, 0)
    return found[0] if found else (None, None)


def check_help(tree, work, url, version):
    # An independent WebView consumes the assets and API served by the frozen process.
    sys.path.insert(0, str(tree / "tests"))
    from acceptance_118 import Browser, until
    import webview

    window = webview.create_window("Paket yardım doğrulaması", url, width=1280, height=850, focus=False)
    errors, checks = [], []

    def drive():
        try:
            assert window.events.loaded.wait(40)
            page = Browser(window)
            until("packaged UI ready", lambda: page.js(
                "document.body.classList.contains('easy-mode') && !document.querySelector('#app-loader')"))
            page.click("#tools-menu-button")
            page.click("#easy-tools-search")
            page.cdp("Input.insertText", text="Uygulama bilgisi")
            page.click(".easy-search-result button:first-child")
            rows = until("application info dialog", lambda: page.js("""(() => {
              const rows=[...document.querySelectorAll('#app-info-dialog[open] .app-info-row')];
              return rows.length ? Object.fromEntries(rows.map(r=>[
                r.querySelector('dt').textContent.trim(),r.querySelector('code').textContent.trim()])) : null;
            })()"""))
            assert rows["Sürüm"] == version and rows["Çalışma biçimi"] == "Kurulu EXE", rows
            assert rows["Çalışan şube"] and Path(rows["Çalışan EXE"]).samefile(work / "VardiyaPuantaj.exe")
            assert Path(rows["Etkin veritabanı"]).samefile(work / "data" / "vardiya_puantaj.sqlite3"), rows
            with build_opener(ProxyHandler({})).open(url + "/api/app-info", timeout=10) as response:
                assert rows["Çalışan şube"] == json.load(response)["branch"]["name"]
            checks.extend(rows)
        except Exception as exc:
            errors.append(exc)
        finally:
            window.destroy()

    webview.settings.update(ALLOW_FILE_URLS=False, OPEN_EXTERNAL_LINKS_IN_BROWSER=False)
    webview.start(drive, gui="edgechromium", private_mode=True, storage_path=str(work / "help-profile"))
    if errors:
        raise errors[0]
    assert len(checks) == 5
    return checks


def main():
    root = Path(__file__).resolve().parent
    package = Path(sys.argv[1]).resolve(strict=True)
    if package.parent != root or not package.name.startswith("package-"):
        raise ValueError("Only an isolated release package can be opened for this test.")
    version = sys.argv[2]
    with tempfile.TemporaryDirectory(prefix="vp-package-smoke-") as folder:
        work = Path(folder)
        exe = work / "VardiyaPuantaj.exe"
        shutil.copy2(package / exe.name, exe)
        (work / "data").mkdir()
        (work / "local").mkdir()
        env = {**os.environ, "LOCALAPPDATA": str(work / "local"), "TEMP": str(work), "TMP": str(work),
               "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--disable-background-networking --disable-sync "
               "--proxy-server=http://127.0.0.1:0 --proxy-bypass-list=localhost;127.0.0.1"}
        process = subprocess.Popen([str(exe)], cwd=work, env=env)
        hwnd = None
        try:
            deadline = time.monotonic() + 90
            url = None
            while time.monotonic() < deadline and process.poll() is None and not url:
                pid, hwnd = window_for(exe)
                if pid:
                    rows = subprocess.check_output(["netstat", "-ano", "-p", "tcp"]).decode("mbcs", "replace").splitlines()
                    for row in rows:
                        fields = row.split()
                        if (len(fields) == 5 and fields[0] == "TCP" and fields[-1] == str(pid)
                                and fields[1].startswith("127.0.0.1:") and fields[2] == "0.0.0.0:0"):
                            url = "http://" + fields[1]
                            break
                if not url:
                    time.sleep(0.25)
            if not url:
                raise RuntimeError("No visible package window with its own loopback listener.")
            result = check(exe, version, url)
            if sys.argv[3:] == ["--help-ui"]:
                tree = root.parent
                if not (tree / "tests" / "acceptance_118.py").is_file():
                    raise ValueError("Project source and test harness not found.")
                os.environ.update({key: env[key] for key in ("LOCALAPPDATA", "TEMP", "TMP", "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS")})
                result["help_fields"] = check_help(tree, work, url, version)
        finally:
            if hwnd:
                ctypes.windll.user32.PostMessageW(hwnd, 0x10, 0, 0)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-Process -Name VardiyaPuantaj -ErrorAction SilentlyContinue | "
                                "Where-Object { $_.Path -eq $env:VP_TEST_PATH } | "
                                "ForEach-Object { Stop-Process -Id $_.Id -ErrorAction Stop }"],
                               env={**os.environ, "VP_TEST_PATH": str(exe)}, check=True, capture_output=True)
                process.wait(timeout=15)
        if process.returncode != 0:
            raise RuntimeError(f"Frozen app did not close normally: {process.returncode}")
    if hashlib.sha256((package / exe.name).read_bytes()).hexdigest() != result["sha256"]:
        raise RuntimeError("Package changed during smoke test.")
    # Do not promise compatibility with a schema this executable has not opened.
    manifest = {"format": "vardiya-schema-compatibility", "version": 1, "executable_sha256": result["sha256"],
                "minimum_schema": result["database_schema"], "maximum_schema": result["database_schema"]}
    (package / (exe.name + ".schema.json")).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (package / "smoke.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    if len(sys.argv) == 3 or len(sys.argv) == 4 and sys.argv[3] == "--help-ui":
        main()
    else:
        exe = Path(os.environ["VP_TEST_PATH"]).resolve(strict=True)
        print(json.dumps(check(exe, os.environ["VP_TEST_VERSION"], os.environ["VP_TEST_URL"])))
