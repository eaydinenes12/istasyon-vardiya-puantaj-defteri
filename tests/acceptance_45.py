"""#45: WebView2 acceptance for sequential missing-cell navigation.

Run on Windows from the project root with Edge WebView2 installed:
    python -B tests\\acceptance_45.py

The app database and fresh WebView2 profile live in an OS temporary directory.
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def until(label, probe, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"Timed out: {label}")


class Browser:
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

    def click(self, selector):
        expression = f"""(() => {{
          const element = document.querySelector({json.dumps(selector)});
          if (!element || element.disabled || !element.getClientRects().length) return null;
          element.scrollIntoView({{block: 'nearest', inline: 'nearest', behavior: 'instant'}});
          const rect = element.getBoundingClientRect();
          return {{x: rect.x + rect.width / 2, y: rect.y + rect.height / 2}};
        }})()"""
        point = until(f"clickable {selector}", lambda: self.js(expression))
        for event in ("mousePressed", "mouseReleased"):
            self.cdp("Input.dispatchMouseEvent", type=event, button="left", clickCount=1, **point)


def run(folder):
    profile = folder / "browser"
    os.environ.update(
        TEMP=str(folder),
        TMP=str(folder),
        NO_PROXY="127.0.0.1,localhost",
        WEBVIEW2_USER_DATA_FOLDER=str(profile),
        WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=(
            "--disable-background-networking --disable-component-update --disable-sync "
            "--disable-quic --proxy-server=http://127.0.0.1:0 "
            "--proxy-bypass-list=127.0.0.1;localhost"
        ),
    )
    tempfile.tempdir = str(folder)

    import webview
    import scheduling
    from storage import Store
    from server import create_server

    db = folder / "acceptance.sqlite3"
    month = time.strftime("%Y-%m")
    days = scheduling.month_dates(month)
    work_day = days[0].isoformat()
    person_name = "Kabul 45 Personeli"
    with Store(db) as store:
        person_id = store.add_person(person_name)["id"]
        entry = {
            "row_id": "acceptance-45-row",
            "employee_id": person_id,
            "section": "MARKET",
            "slot": 1,
            "days": {work_day: {"kind": "work", "start": "09:00", "end": "17:00"}},
        }
        store.save_schedule_month(month, [entry], "")

    server = create_server(db, ROOT / "web")
    origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever)
    failures = []
    passed = threading.Event()
    window = webview.create_window("Acceptance 45", origin, width=1440, height=900, focus=False)

    def drive():
        page = Browser(window)
        try:
            if not window.events.loaded.wait(40):
                raise TimeoutError("WebView2 did not load the local application")
            until("schedule ready", lambda: page.js(
                "!document.querySelector('#app-loader') && !!document.querySelector('#schedule-table td[data-shift-row]')"))
            if page.js("document.querySelectorAll('#schedule-sheet-tabs button').length > 1"):
                page.click("#schedule-sheet-tabs button:nth-child(2)")
                until("second schedule week selected", lambda: page.js(
                    "document.querySelectorAll('#schedule-sheet-tabs button')[1]?.getAttribute('aria-selected') === 'true'"))
            page.click("#open-schedule-check")
            until("period check dialog", lambda: page.js(
                "document.querySelector('#period-check-dialog').open"))
            count = page.js(
                "document.querySelectorAll('#period-check-content [data-check-sequence=\"schedule-missing\"]').length")
            if count < 2:
                raise AssertionError(f"Expected at least two missing cells, found {count}")
            page.click('#period-check-content [data-check-sequence="schedule-missing"]')
            until("inline sequence starts", lambda: page.js(
                "!document.querySelector('#schedule-period-check-flow').hidden"
                " && document.querySelector('#schedule-period-check-flow [data-period-check-position]').textContent"
                " === '1/" + str(count) + "'"))
            first_day = page.js("document.activeElement.dataset.shiftDay")
            if not first_day:
                raise AssertionError("The first missing schedule cell did not receive focus")
            page.click('#schedule-period-check-flow [data-period-check-step="1"]')
            until("next missing cell focused", lambda: page.js(
                "document.activeElement.dataset.shiftDay && document.activeElement.dataset.shiftDay !== "
                + json.dumps(first_day)
            ))
            second_day = page.js("document.activeElement.dataset.shiftDay")
            position = page.js(
                "document.querySelector('#schedule-period-check-flow [data-period-check-position]').textContent")
            if position != f"2/{count}" or second_day == first_day:
                raise AssertionError(f"Expected second distinct cell; position={position}, day={second_day}")
            page.click("#schedule-period-check-flow [data-period-check-close]")
            until("sequence closes", lambda: page.js(
                "document.querySelector('#schedule-period-check-flow').hidden"))
            print(f"PASS #45: WebView2 navigated missing cells {first_day} -> {second_day}, 1/{count} -> 2/{count}.",
                  flush=True)
            passed.set()
        except Exception as exc:
            failures.append(exc)
            print("Browser at failure:", page.js("""({
                view: document.querySelector('.view.active')?.id,
                dialog: document.querySelector('#period-check-dialog')?.open,
                summary: document.querySelector('#period-check-summary')?.textContent,
                content: document.querySelector('#period-check-content')?.innerText,
                cells: [...document.querySelectorAll('#schedule-table td[data-shift-row]')].slice(0, 4)
                  .map(cell => ({row: cell.dataset.shiftRow, day: cell.dataset.shiftDay,
                    section: cell.dataset.shiftSection, text: cell.innerText})),
                flow: document.querySelector('#schedule-period-check-flow')?.textContent,
                focus: document.activeElement?.outerHTML?.slice(0, 180),
            })"""), flush=True)
        finally:
            window.destroy()

    webview.settings.update(ALLOW_FILE_URLS=False, OPEN_EXTERNAL_LINKS_IN_BROWSER=False)
    thread.start()
    try:
        webview.start(drive, gui="edgechromium", private_mode=True, storage_path=str(profile))
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        server.lock.release()
    if failures:
        raise failures[0]
    if not passed.is_set():
        raise AssertionError("WebView2 closed before the scenario finished")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Run this standalone acceptance test on Windows with Edge WebView2.")
    with tempfile.TemporaryDirectory(prefix="ui45-") as directory:
        run(Path(directory))
