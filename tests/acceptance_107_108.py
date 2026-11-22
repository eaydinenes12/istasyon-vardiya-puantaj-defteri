"""#107/#108: assistant-chat WebView2 acceptance against recorded application results.

Run on Windows from the project root:
    python -B tests\\acceptance_107_108.py

The app database and fresh WebView2 profile are created under an OS temporary
directory. A fake model-list entry enables the chat form; recognized report
questions must be answered by the application's existing services without a
local-model call.
"""
import json
import os
import sys
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.acceptance_45 import Browser, until


def _display(value):
    return f"{value:.2f}".rstrip("0").rstrip(".")


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
    import operations
    import schedule_protection
    import scheduling
    import server as server_module
    from server import create_server
    from storage import Store

    now = date.today()
    month = now.strftime("%Y-%m")
    work_day = (now + timedelta(days=1)).isoformat()
    if work_day[:7] != month:
        work_day = now.isoformat()
    person_name = "Kabul 107-108 Hesap Personeli"
    db = folder / "acceptance.sqlite3"
    with Store(db) as store:
        operations.initialize(store.connection)
        schedule_protection.initialize(store.connection)
        person_id = store.add_person(person_name)["id"]
        entries = scheduling.normalize_schedule_entries(month, [{
            "row_id": "acceptance-107-108-row",
            "employee_id": person_id,
            "section": "MARKET",
            "days": {work_day: {"kind": "work", "start": "09:00", "end": "17:00"}},
        }], store.people())
        schedule_revision = store.save_schedule_month(month, entries, "")
        store.save_compliance_settings({"min_staffing": {"MARKET": 1}})
        schedule_protection.set_lock(store, {
            "month": month,
            "row_id": "acceptance-107-108-row",
            "date": work_day,
            "revision": schedule_revision,
            "protected": True,
            "reason": "Kabul testi: asgari personel bulgusunu koru.",
        })
        attendance_revision = store.save_attendance_month(
            month, {work_day: {str(person_id): "X"}}, {}, "",
        )
        report = operations.quality_report(store, month)
        if not any(item.get("type") in {"staffing", "min_staffing"} for item in report["findings"]):
            raise AssertionError("The temporary plan did not produce a recorded staffing finding")
        if not schedule_protection.locks(store, month):
            raise AssertionError("The temporary plan did not retain its recorded protection")

    server_module.live_assistant.list_models = lambda: ["acceptance-local"]

    def reject_model_call(*_args, **_kwargs):
        raise AssertionError("Recognized report questions must not call a local model")

    server_module.live_assistant.chat = reject_model_call
    server = create_server(db, ROOT / "web")
    server.easy_mode = True
    origin = f"http://127.0.0.1:{server.server_port}"
    server_thread = threading.Thread(target=server.serve_forever)
    failures = []
    passed = threading.Event()
    window = webview.create_window("Acceptance 107/108", origin, width=1440, height=900, focus=False)

    try:
        server_thread.start()
        with urlopen(
            f"{origin}/api/document-workflows/calculation?month={month}&employee_id={person_id}",
            timeout=10,
        ) as response:
            calculation = json.loads(response.read().decode("utf-8"))
        totals = calculation["monthly_totals"]
        if totals.get("worked_hours") is None or totals.get("overtime_computed_hours") is None:
            raise AssertionError(f"Calculation API did not return engine totals: {totals!r}")

        def drive():
            page = Browser(window)

            def last_assistant_message():
                return page.js("""(() => {
                  const nodes = [...document.querySelectorAll(
                    '#live-assistant-transcript .live-assistant-message[data-role="assistant"]'
                  )];
                  return nodes.at(-1)?.querySelector('p')?.innerText || '';
                })()""")

            def submit(message):
                page.js(f"""(() => {{
                  const input = document.querySelector('#live-assistant-input');
                  input.value = {json.dumps(message)};
                  input.dispatchEvent(new Event('input', {{bubbles: true}}));
                  document.querySelector('#live-assistant-form').requestSubmit();
                  return true;
                }})()""")

            try:
                if not window.events.loaded.wait(40):
                    raise TimeoutError("WebView2 did not load the local application")
                until("application ready", lambda: page.js(
                    "!document.querySelector('#app-loader') && !!document.querySelector('#open-live-assistant')"))
                page.click("#open-live-assistant")
                until("assistant dialog", lambda: page.js(
                    "!!document.querySelector('#live-assistant-dialog[open]')"))
                until("synthetic model-list entry", lambda: page.js(
                    "!!document.querySelector('#live-assistant-model option[value=\"acceptance-local\"]')"))
                page.js("""(() => {
                  const model = document.querySelector('#live-assistant-model');
                  model.value = 'acceptance-local';
                  model.dispatchEvent(new Event('change', {bubbles: true}));
                  return true;
                })()""")

                submit("Plan oluşturamadı, neden?")
                until("constraint answer", lambda: (
                    "matematiksel olarak olanaksız" in last_assistant_message()
                    and "kadro" in last_assistant_message()
                    and "koruma" in last_assistant_message()
                ))
                constraints = page.js("""(() => {
                  const notes = [...document.querySelectorAll(
                    '#live-assistant-transcript .live-assistant-message[data-role="assistant"]'
                  )].at(-1);
                  return {
                    reply: notes?.querySelector('p')?.innerText || '',
                    citations: [...(notes?.querySelectorAll('.live-assistant-source small') || [])]
                      .map((node) => node.innerText),
                  };
                })()""")
                if not constraints["citations"] or not all(
                    "rapor sürümü" in citation and schedule_revision in citation
                    for citation in constraints["citations"]
                ):
                    raise AssertionError(f"Constraint answer was not linked to its plan revision: {constraints!r}")
                print("PASS #107: assistant chat grouped recorded staffing/protection constraints, linked their plan revision, and did not claim mathematical impossibility.",
                      flush=True)

                calculation_prompt = f"{person_name} çalışma saati ve fazla mesai toplamı?"
                submit(calculation_prompt)
                expected_worked = _display(totals["worked_hours"])
                expected_overtime = _display(totals["overtime_computed_hours"])
                expected_manual = _display(totals.get("overtime_manual") or 0)
                until("calculation answer", lambda: (
                    f"çalışma toplamı {expected_worked} saat" in last_assistant_message()
                    and f"hesaplanan fazla mesai {expected_overtime} saat" in last_assistant_message()
                ))
                calculation_result = page.js("""(() => {
                  const node = [...document.querySelectorAll(
                    '#live-assistant-transcript .live-assistant-message[data-role="assistant"]'
                  )].at(-1);
                  return {
                    reply: node?.querySelector('p')?.innerText || '',
                    citations: [...(node?.querySelectorAll('.live-assistant-source small') || [])]
                      .map((item) => item.innerText),
                  };
                })()""")
                reply = calculation_result["reply"]
                if (f"elle bildirilen mesai {expected_manual} saat" not in reply
                        or "Ücret hesabı yapılmadı." not in reply
                        or month not in reply
                        or not calculation_result["citations"]
                        or not any(attendance_revision in citation for citation in calculation_result["citations"])):
                    raise AssertionError(
                        f"Assistant calculation did not match the engine result and attendance revision: "
                        f"{calculation_result!r}"
                    )
                print("PASS #108: assistant chat matched the calculation API's engine totals, showed scope/revision, and explicitly omitted wage calculation.",
                      flush=True)
                passed.set()
            except Exception as exc:
                failures.append(exc)
                print("Browser at failure:", page.js("""({
                  dialog: document.querySelector('#live-assistant-dialog')?.open,
                  status: document.querySelector('#live-assistant-status')?.innerText,
                  transcript: document.querySelector('#live-assistant-transcript')?.innerText,
                })"""), flush=True)
            finally:
                window.destroy()

        webview.settings.update(ALLOW_FILE_URLS=False, OPEN_EXTERNAL_LINKS_IN_BROWSER=False)
        webview.start(drive, gui="edgechromium", private_mode=True, storage_path=str(profile))
    finally:
        server.shutdown()
        server_thread.join()
        server.server_close()
        server.lock.release()

    if failures:
        raise failures[0]
    if not passed.is_set():
        raise AssertionError("WebView2 closed before the scenarios finished")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Run this standalone acceptance test on Windows with Edge WebView2.")
    with tempfile.TemporaryDirectory(prefix="ui107-108-") as directory:
        run(Path(directory))
