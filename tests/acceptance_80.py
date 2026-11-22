"""#80: WebView2 acceptance for report-version-scoped chart notes.

Run on Windows from the project root with Edge WebView2 installed:
    python -B tests\\acceptance_80.py

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

from tests.acceptance_45 import Browser, until


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
    work_day, second_day = days[0].isoformat(), days[1].isoformat()
    person_name = "Kabul 80 Grafik Personeli"
    with Store(db) as store:
        person_id = store.add_person(person_name)["id"]
        entries = scheduling.normalize_schedule_entries(month, [{
            "row_id": "acceptance-80-row",
            "employee_id": person_id,
            "section": "MARKET",
            "days": {work_day: {"kind": "work", "start": "09:00", "end": "17:00"}},
        }], store.people())
        store.save_schedule_month(month, entries, "")
        attendance_revision = store.save_attendance_month(
            month, {work_day: {str(person_id): "X"}}, {}, "",
        )

    server = create_server(db, ROOT / "web")
    origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever)
    failures = []
    passed = threading.Event()
    window = webview.create_window("Acceptance 80", origin, width=1440, height=900, focus=False)

    def drive():
        page = Browser(window)

        def open_manager_summary():
            page.click("#tools-menu-button")
            until("tools menu", lambda: page.js("!document.querySelector('#tools-menu').hidden"))
            item_index = until("manager summary menu item", lambda: page.js("""(() => {
              const item = [...document.querySelectorAll('#tools-menu [data-tool]')]
                .find((button) => button.textContent.includes('Yönetici özeti'));
              return item?.dataset.tool;
            })()"""))
            page.click(f'#tools-menu [data-tool="{item_index}"]')
            until("manager summary dialog", lambda: page.js(
                "!!document.querySelector('#manager-summary-dialog[open] [data-chart-note-toggle=\"worked_hours\"]')"))

        try:
            if not window.events.loaded.wait(40):
                raise TimeoutError("WebView2 did not load the local application")
            until("application ready", lambda: page.js(
                "!document.querySelector('#app-loader') && !!document.querySelector('#tools-menu-button')"))
            page.js(f"state.attendanceMonth = {json.dumps(month)}; state.scheduleMonth = {json.dumps(month)}; true")
            open_manager_summary()

            first_version = page.js(
                "document.querySelector('#manager-summary-dialog .analytics-freshness p code')?.textContent")
            page.click('#manager-summary-dialog [data-chart-note-toggle="worked_hours"]')
            editor_version = page.js(
                "document.querySelector('#manager-summary-dialog [data-chart-note=\"worked_hours\"] "
                ".analytics-chart-note-editor code')?.textContent")
            if not first_version or editor_version != first_version:
                raise AssertionError(f"Chart note editor is not tied to the visible report version: {first_version!r}, {editor_version!r}")

            note_text = "Bu hafta bakım nedeniyle ek vardiya vardı."
            page.js(f"""(() => {{
              const input = document.querySelector('#manager-summary-dialog [data-chart-note-input="worked_hours"]');
              input.value = {json.dumps(note_text)};
              return true;
            }})()""")
            page.click('#manager-summary-dialog [data-chart-note-save="worked_hours"]')
            until("saved chart note", lambda: page.js(
                "document.querySelector('#manager-summary-dialog .analytics-chart-note-saved')"
                f"?.innerText.includes({json.dumps(note_text)})"))
            page.click("#manager-summary-dialog [data-extras-close]")
            until("manager summary closed", lambda: page.js(
                "!document.querySelector('#manager-summary-dialog').open"))

            open_manager_summary()
            reopened_version = page.js(
                "document.querySelector('#manager-summary-dialog .analytics-freshness p code')?.textContent")
            reopened_note = page.js(
                "document.querySelector('#manager-summary-dialog .analytics-chart-note-saved')?.innerText || ''")
            if reopened_version != first_version or note_text not in reopened_note:
                raise AssertionError("Saved chart note did not reopen with the same report version")
            page.click("#manager-summary-dialog [data-extras-close]")
            until("manager summary closed before data update", lambda: page.js(
                "!document.querySelector('#manager-summary-dialog').open"))

            with Store(db) as store:
                store.save_attendance_month(
                    month,
                    {work_day: {str(person_id): "X"}, second_day: {str(person_id): "G"}},
                    {}, attendance_revision,
                )
                old_note = store.manager_chart_notes(month, first_version).get("worked_hours")
                if not old_note or old_note["text"] != note_text:
                    raise AssertionError("The original version's chart note was not persisted")

            open_manager_summary()
            current_version = page.js(
                "document.querySelector('#manager-summary-dialog .analytics-freshness p code')?.textContent")
            current_notes = page.js(
                "document.querySelectorAll('#manager-summary-dialog .analytics-chart-note-saved').length")
            if current_version == first_version or current_notes != 0:
                raise AssertionError(
                    f"Note was mixed into the changed report: old={first_version!r}, "
                    f"current={current_version!r}, visible notes={current_notes}"
                )
            print("PASS #80: WebView2 saves and reopens a chart explanation for its report version; a changed attendance revision gets a new version with no inherited note.",
                  flush=True)
            passed.set()
        except Exception as exc:
            failures.append(exc)
            print("Browser at failure:", page.js("""({
              dialog: document.querySelector('#manager-summary-dialog')?.open,
              title: document.querySelector('#manager-summary-dialog h2')?.textContent,
              version: document.querySelector('#manager-summary-dialog .analytics-freshness')?.innerText,
              notes: [...document.querySelectorAll('#manager-summary-dialog .analytics-chart-note-saved')]
                .map((node) => node.innerText),
              editors: [...document.querySelectorAll('#manager-summary-dialog .analytics-chart-note-editor')]
                .map((node) => ({hidden: node.hidden, text: node.innerText})),
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
    with tempfile.TemporaryDirectory(prefix="ui80-") as directory:
        run(Path(directory))
