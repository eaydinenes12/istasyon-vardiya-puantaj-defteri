"""#46: WebView2 acceptance for attendance-cell source evidence.

Run on Windows from the project root:
    python -B tests\\acceptance_46.py

The database and WebView2 profile are created under an OS temporary directory.
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


def until(label, probe, timeout=20):
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
        result = self.cdp(
            "Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True,
        )
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
    import attendance_corrections
    from storage import Store
    from server import create_server

    db = folder / "acceptance.sqlite3"
    month = time.strftime("%Y-%m")
    day = scheduling.month_dates(month)[1].isoformat()
    person_name = "Kabul 46 Kaynak Personeli"
    with Store(db) as store:
        person_id = store.add_person(person_name)["id"]
        entries = scheduling.normalize_schedule_entries(month, [{
            "row_id": "acceptance-46-row",
            "employee_id": person_id,
            "section": "MARKET",
            "days": {day: {"kind": "work", "start": "08:00", "end": "16:00"}},
        }], store.people())
        schedule_revision = store.save_schedule_month(month, entries, "")
        attendance_revision = store.save_attendance_month(
            month, {day: {str(person_id): "X"}}, {}, "",
        )
        attendance_revision = store.save_attendance_month(
            month, {day: {str(person_id): "R"}}, {}, attendance_revision,
        )
        store.actor = "Kabul Excel aktarım sorumlusu"
        with store.connection:
            store._write_setting("pdks_records", {month: {
                "file": "kabul-46-pdks.csv",
                "imported_at": "2026-11-02T07:30:00+00:00",
                "actor": "Kabul PDKS aktarım sorumlusu",
                "records": [{
                    "name": person_name, "date": day, "in": "08:05", "out": "16:10",
                }],
            }})
            store.record_change("attendance.import_excel", "Kabul için dönem Excel aktarımı", {
                "month": month, "file": "kabul-46-puantaj.xlsx", "codes": 7,
            })
        attendance_corrections.initialize(store.connection)
        with store.connection:
            store.connection.execute(
                "INSERT INTO attendance_corrections "
                "(id,month,day,employee_id,code,reason,evidence,requested_by,created_at,status,decided_by,decided_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,'approved',?,?)",
                ("acceptance-46-correction", month, day, person_id, "S",
                 "Kabul için kaynaklı düzeltme", "source-fingerprint", "Kabul personeli",
                 "2026-11-02T09:00:00+00:00", "Kabul yöneticisi", "2026-11-02T10:00:00+00:00"),
            )

    server = create_server(db, ROOT / "web")
    server.easy_mode = True
    origin = f"http://localhost:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever)
    failures = []
    passed = threading.Event()
    window = webview.create_window("Acceptance 46", origin, width=1440, height=900, focus=False)

    def drive():
        page = Browser(window)
        try:
            if not window.events.loaded.wait(40):
                raise TimeoutError("WebView2 did not load the local application")
            until("application ready", lambda: page.js(
                "!document.querySelector('#app-loader') && !!window.operationsTools"))
            page.js("window.operationsTools.open('corrections'); true")
            until("correction source form", lambda: page.js(
                "!!document.querySelector('.operations-dialog[open] [data-correction]')"))
            page.js(f"""(() => {{
              const form = document.querySelector('.operations-dialog[open] [data-correction]');
              form.elements.day.value = {json.dumps(day)};
              form.elements.day.dispatchEvent(new Event('change', {{bubbles: true}}));
              return true;
            }})()""")
            page.click(".operations-dialog[open] [data-correction-check]")
            until("all source classes rendered", lambda: page.js(
                "document.querySelector('.operations-dialog[open] [data-correction-evidence]')"
                "?.innerText.includes('Bu hücre için düzeltme geçmişi')"))
            evidence = page.js(
                "document.querySelector('.operations-dialog[open] [data-correction-evidence]').innerText")
            checks = {
                "plan revision": "Plan revision:" in evidence and schedule_revision in evidence,
                "manual attendance revision": "Puantaj revision:" in evidence and attendance_revision in evidence,
                "scheduled shift": "MARKET" in evidence and "08:00" in evidence and "16:00" in evidence,
                "matched PDKS record and source": "08:05" in evidence and "16:10" in evidence
                    and "kabul-46-pdks.csv" in evidence and "Kabul PDKS aktarım sorumlusu" in evidence,
                "period-level Excel import": "kabul-46-puantaj.xlsx" in evidence
                    and "Kabul Excel aktarım sorumlusu" in evidence,
                "manual cell value": "R · Manuel" in evidence,
                "correction history": "Kabul için kaynaklı düzeltme" in evidence
                    and "Kabul yöneticisi" in evidence and "Onaylandı" in evidence,
                "historical ambiguity note": "hücre eşleşmesi ayrıca kaydedilmemiş olabilir" in evidence
                    and "ilişki varsayılmaz" in evidence,
            }
            missing = [label for label, present in checks.items() if not present]
            if missing:
                raise AssertionError(f"Cell evidence omitted {missing}: {evidence!r}")
            print("PASS #46: WebView2 shows the scheduled shift, matched PDKS row/file/actor, manual code, Excel period import, correction decision, and revisions; it explicitly avoids claiming an unrecorded Excel cell link.",
                  flush=True)
            passed.set()
        except Exception as exc:
            failures.append(exc)
            print("Browser at failure:", page.js("""({
              body: document.body.innerText.slice(0, 1800),
              evidence: document.querySelector('.operations-dialog[open] [data-correction-evidence]')?.innerText,
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
    with tempfile.TemporaryDirectory(prefix="ui46-") as directory:
        run(Path(directory))
