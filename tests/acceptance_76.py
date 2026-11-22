"""#76: WebView2 acceptance for manager-summary person/day and period-only drill-downs.

Run on Windows from the project root with Edge WebView2 installed:
    python -B tests\\acceptance_76.py

The app database and fresh WebView2 profile live in an OS temporary directory.
"""
import os
import sys
import tempfile
import threading
import time
from datetime import date
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
    from storage import Store
    from server import create_server

    db = folder / "acceptance.sqlite3"
    month = time.strftime("%Y-%m")
    work_day = date.today().isoformat()
    person_name = "Kabul 76 Grafik Personeli"
    with Store(db) as store:
        person_id = store.add_person(person_name)["id"]
        store.save_attendance_month(
            month, {work_day: {str(person_id): "G"}}, {}, "",
            overtime={str(person_id): 3.5},
        )

    server = create_server(db, ROOT / "web")
    origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever)
    failures = []
    passed = threading.Event()
    window = webview.create_window("Acceptance 76", origin, width=1440, height=900, focus=False)

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
            until("manager summary charts", lambda: page.js(
                "!!document.querySelector('#manager-summary-dialog[open] [data-analytics-metric=\"absence_days\"]')"))

        try:
            if not window.events.loaded.wait(40):
                raise TimeoutError("WebView2 did not load the local application")
            until("application ready", lambda: page.js(
                "!document.querySelector('#app-loader') && !!document.querySelector('#tools-menu-button')"))
            page.js(f"state.attendanceMonth = {time.strftime('%Y-%m')!r}; "
                    f"state.scheduleMonth = {time.strftime('%Y-%m')!r}; true")
            open_manager_summary()

            page.click('#manager-summary-dialog [data-analytics-metric="absence_days"]')
            absence_text = until("dated absence detail", lambda: (
                text if (
                    (text := page.js("document.querySelector('#manager-summary-dialog [data-analytics-detail]')?.innerText || ''"))
                    and person_name in text and "Elle puantaj düzeltmesi" in text
                    and date.fromisoformat(work_day).strftime("%d.%m.%Y") in text
                ) else None
            ))
            for expected in (person_name, date.fromisoformat(work_day).strftime("%d.%m.%Y"),
                             "1 gün", "G", "Elle puantaj düzeltmesi"):
                if expected not in absence_text:
                    raise AssertionError(f"Dated absence detail omitted {expected!r}: {absence_text!r}")

            page.click('#manager-summary-dialog [data-analytics-metric="overtime_hours"]')
            overtime_text = until("period-only overtime detail", lambda: (
                text if (
                    (text := page.js("document.querySelector('#manager-summary-dialog [data-analytics-detail]')?.innerText || ''"))
                    and "Dönem geneli" in text
                    and "Elle girilen dönem toplamı" in text
                    and "günlük kırılımı mevcut değildir" in text
                ) else None
            ))
            for expected in (person_name, "Dönem geneli", "3,5 sa",
                             "Elle girilen dönem toplamı",
                             "günlük kırılımı mevcut değildir", "tek bir güne dağıtılmadı"):
                if expected not in overtime_text:
                    raise AssertionError(f"Period-only detail omitted {expected!r}: {overtime_text!r}")

            print(
                "PASS #76: WebView2 Devamsızlık showed the contributing person, "
                f"{date.fromisoformat(work_day).strftime('%d.%m.%Y')}, G, and manual source; "
                "Fazla mesai showed 3,5 sa as Dönem geneli with an explicit no-day explanation.",
                flush=True,
            )
            passed.set()
        except Exception as exc:
            failures.append(exc)
            print("Browser at failure:", page.js("""({
              dialog: document.querySelector('#manager-summary-dialog')?.open,
              title: document.querySelector('#manager-summary-dialog h2')?.textContent,
              detail: document.querySelector('#manager-summary-dialog [data-analytics-detail]')?.innerText,
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
    with tempfile.TemporaryDirectory(prefix="ui76-") as directory:
        run(Path(directory))
