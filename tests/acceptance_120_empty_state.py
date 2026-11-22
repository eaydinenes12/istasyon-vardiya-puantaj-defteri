"""#3: WebView2 acceptance for no-record recovery actions."""
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from acceptance_118 import Browser, until


def run(folder):
    profile = folder / "browser"
    os.environ.update(
        TEMP=str(folder),
        TMP=str(folder),
        NO_PROXY="127.0.0.1,localhost",
        WEBVIEW2_USER_DATA_FOLDER=str(profile),
        WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=(
            "--disable-background-networking --disable-component-update --disable-sync "
            "--disable-quic --disable-background-timer-throttling --disable-renderer-backgrounding "
            "--proxy-server=http://127.0.0.1:0 --proxy-bypass-list=127.0.0.1;localhost "
            '--host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"'
        ),
    )
    tempfile.tempdir = str(folder)
    import webview
    from main import DesktopAPI
    from server import create_server
    from storage import Store

    db = folder / "empty.sqlite3"
    with Store(db) as store:
        store.add_person("Acceptance empty state")
    server = create_server(db, ROOT / "web")
    server.easy_mode = True
    origin = f"http://127.0.0.1:{server.server_port}"
    server_thread = threading.Thread(target=server.serve_forever)
    api = DesktopAPI(origin, server.internal_token)
    api._window = SimpleNamespace(create_file_dialog=lambda *args, **kwargs: None)
    server.save_excel = api.save_excel
    failures = []

    window = webview.create_window("Acceptance 120 empty states", origin, width=1280, height=800, focus=False)

    def drive():
        page = Browser(window)
        try:
            if not window.events.loaded.wait(40):
                raise TimeoutError("WebView2 did not load the empty-state fixture")
            until("empty-state app ready", lambda: page.js(
                "document.body.classList.contains('easy-mode') && !document.querySelector('#app-loader')"))
            page.js("""window.__acceptance120Errors = [];
              window.__acceptance120Focus = [];
              document.addEventListener('focusin', event => window.__acceptance120Focus.push(event.target.id));
              window.addEventListener('error', event => window.__acceptance120Errors.push(event.message));
              window.addEventListener('unhandledrejection', event =>
                window.__acceptance120Errors.push(String(event.reason)));""")
            until("schedule no-data state", lambda: page.js("""(() => {
              const empty = document.querySelector('#schedule-empty');
              return !empty.hidden
                && empty.textContent.includes('Henüz kayıtlı vardiya personeli yok')
                && !!empty.querySelector('[data-empty-action="add-person"]');
            })()"""))
            page.click('#schedule-empty [data-empty-action="add-person"]')
            until("schedule person-entry guide opens", lambda: page.js(
                "document.querySelector('.easy-guide')?.open"))
            until("schedule person entry focused", lambda: page.js(
                "document.activeElement?.id === 'new-person-name'"))
            page.click('.main-nav [data-view="attendance"]')
            until("attendance no-data state", lambda: page.js("""(() => {
              const empty = document.querySelector('#attendance-empty');
              return !empty.hidden
                && empty.textContent.includes('Bu ay için puantaj satırı bulunmuyor')
                && !!empty.querySelector('[data-empty-action="add-person"]');
            })()"""))
            page.click('#attendance-empty [data-empty-action="add-person"]')
            until("attendance no-data recovery", lambda: page.js("""document.querySelector('#schedule-view').classList.contains('active')
              && document.querySelector('.easy-guide')?.open
              && document.activeElement?.id === 'new-person-name'"""))
            errors = page.js("window.__acceptance120Errors")
            if errors:
                raise AssertionError(f"Uncaught browser errors: {errors!r}")
            print("WebView: no-record schedule and attendance states offer working person-entry recovery.", flush=True)
        except Exception as exc:
            failures.append(exc)
            print("Empty-state browser diagnostics:", page.js("""({
              view: document.querySelector('.view.active')?.id,
              schedule: document.querySelector('#schedule-empty')?.outerHTML,
              rows: document.querySelectorAll('#schedule-table select[data-action="person"]').length,
              attendance: document.querySelector('#attendance-empty')?.outerHTML,
              load: document.querySelector('#app-loader')?.outerHTML,
              input: document.querySelector('#new-person-name')?.outerHTML,
              panelOpen: document.querySelector('#schedule-view .person-panel')?.open,
              active: document.activeElement?.outerHTML,
              focusEvents: window.__acceptance120Focus,
            })"""), flush=True)
        finally:
            window.destroy()

    server_thread.start()
    try:
        webview.settings.update(ALLOW_FILE_URLS=False, OPEN_EXTERNAL_LINKS_IN_BROWSER=False)
        webview.start(drive, gui="edgechromium", private_mode=True, storage_path=str(profile))
    finally:
        server.shutdown()
        server_thread.join()
        server.server_close()
        server.lock.release()
    if failures:
        raise failures[0]


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="acceptance-120-empty-") as directory:
        run(Path(directory))
