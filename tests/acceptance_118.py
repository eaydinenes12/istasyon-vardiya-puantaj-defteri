"""#118: standalone Kolay Kullanım browser acceptance (not a unittest discovery test).

From the project root on Windows with project requirements and Edge WebView2 installed:
    python -B tests\\acceptance_118.py

Runs the source app on loopback. Only the OS Save As destination is supplied by the
test; the menu, bulk preview/apply, save confirmation, Undo and Excel buttons are real.
The two exported workbooks, SQLite database and fresh browser profile are disposable.
"""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NAME = "Acceptance 118"


def until(label, probe, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"Timed out: {label}")


class Browser:
    """Send Chromium input to this WebView only (no OS mouse/keyboard or debug port)."""

    def __init__(self, window):
        self.window = window

    def cdp(self, method, **params):
        from System import Action

        control = self.window.native.webview
        tasks = []
        control.Invoke(Action(lambda: tasks.append(
            control.CoreWebView2.CallDevToolsProtocolMethodAsync(method, json.dumps(params)))))
        if not tasks[0].Wait(10000):
            raise TimeoutError(method)
        return json.loads(tasks[0].Result)

    def js(self, expression):
        result = self.cdp("Runtime.evaluate", expression=expression, returnByValue=True)
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result["result"].get("value")

    def click(self, selector, text=None):
        expression = f"""(() => {{
          const el = [...document.querySelectorAll({json.dumps(selector)})].find(e =>
            !e.disabled && e.getClientRects().length &&
            ({json.dumps(text)} === null || e.textContent.trim() === {json.dumps(text)}));
          if (!el) return null;
          el.scrollIntoView({{block:'nearest', inline:'nearest', behavior:'instant'}});
          const r = el.getBoundingClientRect(), x = r.x+r.width/2, y = r.y+r.height/2;
          return el.contains(document.elementFromPoint(x,y)) ? {{x,y}} : null;
        }})()"""
        last = None

        def stable_point():
            nonlocal last
            point = self.js(expression)
            stable = point == last
            last = point
            return point if stable else None

        point = until(f"clickable {selector} {text or ''}", stable_point)
        for event in ("mousePressed", "mouseReleased"):
            self.cdp("Input.dispatchMouseEvent", type=event, button="left", clickCount=1, **point)

    def choose_code(self, code):
        # Type-ahead on the focused native select (do not set its value in JavaScript).
        until("bulk code selector", lambda: self.js("""(() => {
          const e = document.querySelector('#easy-bulk-editor[open] #easy-bulk-code');
          if (e) e.focus();
          return !!e && e === document.activeElement;
        })()"""))
        for event in ("keyDown", "keyUp"):
            self.cdp("Input.dispatchKeyEvent", type=event, key=code.lower(), code=f"Key{code}",
                     text=code.lower() if event == "keyDown" else "", windowsVirtualKeyCode=ord(code))
        until(f"selected {code}", lambda: self.js(
            f"document.querySelector('#easy-bulk-code').value === {json.dumps(code)}"))


def run(folder):
    # Set isolation before loading the app or WebView. Never consult the desktop data path.
    profile = folder / "browser"
    os.environ.update(TEMP=str(folder), TMP=str(folder), NO_PROXY="127.0.0.1,localhost",
                      WEBVIEW2_USER_DATA_FOLDER=str(profile),
                      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=(
                          "--disable-background-networking --disable-component-update --disable-sync "
                          "--disable-quic --disable-background-timer-throttling --disable-renderer-backgrounding "
                          "--proxy-server=http://127.0.0.1:0 --proxy-bypass-list=127.0.0.1;localhost "
                          '--host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"'))
    tempfile.tempdir = str(folder)
    import webview
    from openpyxl import load_workbook
    from main import DesktopAPI
    from server import create_server
    from storage import Store

    db = folder / "synthetic.sqlite3"
    month = date.today().strftime("%Y-%m")
    day, neighbor = month + "-01", month + "-02"
    with Store(db) as store:
        person = store.add_person(NAME)["id"]
        store.save_attendance_month(month, {day: {str(person): "X"}, neighbor: {str(person): "H"}},
                                    {}, "", roster={"added": [person], "removed": [], "order": [person]})

    def saved():
        with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)) as connection:
            row = connection.execute("SELECT payload FROM attendance_months WHERE period=?", (month,)).fetchone()
        codes = json.loads(row[0])["overrides"]
        return tuple(codes.get(d, {}).get(str(person)) for d in (day, neighbor))

    server = create_server(db, ROOT / "web")
    server.easy_mode = True
    origin = f"http://127.0.0.1:{server.server_port}"
    server_thread = threading.Thread(target=server.serve_forever)
    files = [folder / "changed.xlsx", folder / "restored.xlsx"]
    paths = iter(files)
    api = DesktopAPI(origin, server.internal_token)
    api._window = SimpleNamespace(create_file_dialog=lambda *args, **kwargs: str(next(paths)))
    server.save_excel = api.save_excel

    def check_excel(page, path, code):
        page.click("#export-attendance")
        until(path.name, path.is_file)
        with closing(load_workbook(path, read_only=True, data_only=True)) as workbook:
            sheet = workbook.active
            actual = tuple(sheet[cell].value for cell in ("B4", "C4", "D4"))
            header = sheet["C3"].value
            if actual != (NAME, code, "H") or header.date().isoformat() != day:
                raise AssertionError(f"{path.name}: {sheet.title} B4:D4={actual}, C3={header}")
            print(f"{path.name}: {sheet.title}!B4:D4={actual!r}; C3={header.date()}", flush=True)

    def save(page, code):
        page.click("#save-attendance")
        page.click("#easy-changes[open] button", "Değişiklikleri kaydet")
        until(f"SQLite {day}={code}, {neighbor}=H", lambda: saved() == (code, "H"))
        until("save finished", lambda: page.js(
            "document.querySelector('#save-attendance').disabled && !document.querySelector('#save-state.unsaved')"))
        print(f"SQLite: {day}={code}, {neighbor}=H", flush=True)

    def scenario(page):
        until("easy UI ready", lambda: page.js(
            "document.body.classList.contains('easy-mode') && !document.querySelector('#app-loader')"))
        page.click('.main-nav [data-view="attendance"]')
        page.click(f'#attendance-table input[data-person="{person}"][data-day="{day}"]')
        page.click("#tools-menu-button")
        page.click("#tools-menu .easy-tool-heading", "Tablo ve görünüm")
        page.click(".easy-tool-flyout button", "Toplu giriş çalışma alanı")
        page.choose_code("R")
        page.click("#easy-bulk-editor button", "Önizlemeyi hazırla")
        page.click("#easy-bulk-preview button", "Onayla ve taslağa uygula")
        if saved() != ("X", "H"):
            raise AssertionError("Preview/apply wrote to the database before Save")
        save(page, "R")
        check_excel(page, files[0], "R")
        page.click("#undo-attendance")
        if saved() != ("R", "H"):
            raise AssertionError("Undo wrote to the database before Save")
        save(page, "X")
        check_excel(page, files[1], "X")

    failures = []
    passed = threading.Event()
    window = webview.create_window("Acceptance 118", origin, width=1440, height=900, focus=False)

    def drive():
        page = Browser(window)
        try:
            if not window.events.loaded.wait(40):
                raise TimeoutError("WebView2 did not load the local application")
            scenario(page)
            passed.set()
        except Exception as exc:
            failures.append(exc)
            print("Browser at failure:", page.js("""({
                view: document.querySelector('.view.active')?.id,
                modal: document.querySelector('dialog[open]')?.id,
                toast: document.querySelector('#toast')?.textContent,
                undoDisabled: document.querySelector('#undo-attendance')?.disabled,
            })"""), flush=True)
        finally:
            window.destroy()

    webview.settings.update(ALLOW_FILE_URLS=False, OPEN_EXTERNAL_LINKS_IN_BROWSER=False)
    server_thread.start()
    try:
        webview.start(drive, gui="edgechromium", private_mode=True, storage_path=str(profile))
    finally:
        server.shutdown()
        server_thread.join()
        server.server_close()
        server.lock.release()
    if failures:
        raise failures[0]
    if not passed.is_set():
        raise AssertionError("WebView2 closed before the scenario finished")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Run this standalone acceptance test on Windows with Edge WebView2.")
    with tempfile.TemporaryDirectory(prefix=".ui118-", dir=ROOT) as directory:
        run(Path(directory))
    print("PASS #118: saved edit, saved Undo and both UI Excel exports verified.")
