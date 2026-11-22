"""Document and PDKS UI acceptance (not a unittest discovery test).

Run on Windows from this project copy with Edge WebView2 installed:
    python -B tests\\acceptance_documents.py

The local server uses a disposable SQLite database, generated workbooks/PDF/CSV,
and a private WebView2 profile. It never uses the desktop data path.
"""
import json
import base64
import os
import sys
import tempfile
import threading
import time
from io import BytesIO
from datetime import date
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NAME = "Acceptance Documents"


def until(label, probe, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"Timed out: {label}")


class Browser:
    """Drive the real WebView through CDP; fail on JavaScript exceptionDetails."""

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

    def fill(self, selector, value):
        expression = f"""(() => {{
          const el = document.querySelector({json.dumps(selector)});
          if (!el) return false;
          el.focus();
          el.value = {json.dumps(value)};
          el.dispatchEvent(new Event('input', {{bubbles:true}}));
          el.dispatchEvent(new Event('change', {{bubbles:true}}));
          return true;
        }})()"""
        if not self.js(expression):
            raise AssertionError(f"Missing form field: {selector}")

    def upload(self, selector, path):
        root = self.cdp("DOM.getDocument", depth=-1, pierce=True)["root"]
        node = self.cdp("DOM.querySelector", nodeId=root["nodeId"], selector=selector)
        if not node.get("nodeId"):
            raise AssertionError(f"Missing file input: {selector}")
        self.cdp("DOM.setFileInputFiles", nodeId=node["nodeId"], files=[str(path)])

def make_workbook(path, headers, rows):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Vardiya"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def assert_template_output_excludes_guides(origin, month):
    from openpyxl import load_workbook

    with urlopen(f"{origin}/api/export?kind=schedule&month={month}&week=0", timeout=20) as response:
        workbook = load_workbook(BytesIO(response.read()), read_only=True, data_only=True)
    try:
        values = [
            str(cell.value)
            for sheet in workbook.worksheets
            for row in sheet.iter_rows()
            for cell in row
            if cell.value is not None
        ]
    finally:
        workbook.close()
    if any("Kesik çizgi yalnız şablon düzenleme" in value or "İmza satırı" in value for value in values):
        raise AssertionError("Template editing guide text leaked into the generated workbook")


def make_scanned_pdf(path, day):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (2400, 620), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 62)
    draw.text((50, 40), "Personel  Bolum  Tarih  Baslangic  Bitis", fill="black", font=font)
    draw.text((50, 170), f"{NAME}  MARKET  {day}  08:00  16:00", fill="black", font=font)
    image.save(path, format="PDF", resolution=200)


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
            '--host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"'),
    )
    tempfile.tempdir = str(folder)

    import webview
    import document_workflows
    from server import create_server
    from storage import Store
    from docx import Document

    db = folder / "synthetic.sqlite3"
    month = date.today().strftime("%Y-%m")
    day, next_day = month + "-01", month + "-02"
    original_neutral_form = document_workflows._neutral_status_form
    generation_failure = [True]

    def neutral_form_with_one_synthetic_failure(
        person_name, day_text, code, description, title="", event_time="",
    ):
        if generation_failure[0] and day_text == next_day:
            raise ValueError("Sentetik çıktı üretim hatası")
        return original_neutral_form(person_name, day_text, code, description, title, event_time)

    document_workflows._neutral_status_form = neutral_form_with_one_synthetic_failure
    with Store(db) as store:
        person = store.add_person(NAME)["id"]
        store.save_employee_profile(person, {
            "title": "Vardiya Sorumlusu",
            "phone": "05000000000",
            "notes": "PRIVATE_INTERNAL_NOTE",
        })
        scope_code_name = "Acceptance Scope Code"
        scope_person = store.add_person(scope_code_name)["id"]
        store.save_schedule_month(month, [
            {
                "row_id": "acceptance-documents",
                "employee_id": person,
                "section": "MARKET",
                "slot": 1,
                "days": {
                    day: {"kind": "work", "start": "07:00", "end": "15:00", "shift_label": "PRIVATE_SHIFT_NOTE"},
                    month + "-03": {
                        "kind": "sick_leave",
                        "reason": "PRIVATE_PERMISSION_REASON",
                        "notes": "PRIVATE_LEAVE_NOTE",
                    },
                },
            },
            {
                "row_id": "acceptance-scope",
                "employee_id": scope_person,
                "section": "MARKET",
                "slot": 2,
                "days": {day: {"kind": "work", "start": "09:00", "end": "17:00"}},
            },
        ], "")

    same = folder / "renamed-identical.xlsx"
    changed = folder / "renamed-changed.xlsx"
    scope_file = folder / "working-hours-only.xlsx"
    review_file = folder / "valid-and-review.xlsx"
    profile_base = folder / "profile-baseline.xlsx"
    profile_changed = folder / "profile-changed-header.xlsx"
    profile_misaligned = folder / "profile-misaligned-person-column.xlsx"
    scan = folder / "scanned-source.pdf"
    pdks = folder / "pdks-punches.csv"

    headers = ["Personel", "Bölüm", "Tarih", "Başlangıç", "Bitiş", "Durum"]
    make_workbook(same, headers, [[NAME, "MARKET", day, "07:00", "15:00", ""]])
    make_workbook(changed, headers, [[NAME, "MARKET", day, "08:00", "16:00", ""]])
    make_workbook(scope_file, headers, [
        [NAME, "MARKET", next_day, "08:00", "16:00", ""],
        [scope_code_name, "MARKET", next_day, "", "", "H"],
    ])
    make_workbook(review_file, headers, [
        [NAME, "MARKET", next_day, "08:00", "16:00", ""],
        [NAME, "MARKET", "not-a-date", "09:00", "17:00", ""],
    ])
    make_workbook(profile_base, headers, [[NAME, "MARKET", day, "07:00", "15:00", ""]])
    make_workbook(profile_changed, ["Çalışan", *headers[1:]], [
        [NAME, "MARKET", day, "07:00", "15:00", ""],
    ])
    make_workbook(profile_misaligned, ["Tarih", "Personel", "Bölüm", "Başlangıç", "Bitiş", "Durum"], [
        [day, NAME, "MARKET", "08:00", "16:00", ""],
    ])
    make_scanned_pdf(scan, day)
    pdks.write_text(
        "Personel,Tarih,Okutma\n"
        f"{NAME},{day},08:00\n"
        f"{NAME},{day},12:00\n"
        f"{NAME},{day},13:00\n"
        f"{NAME},{day},17:00\n"
        f"{NAME},{day},18:00\n",
        encoding="utf-8-sig",
    )

    server = create_server(db, ROOT / "web")
    server.easy_mode = True
    origin = f"http://127.0.0.1:{server.server_port}"
    server_thread = threading.Thread(target=server.serve_forever)
    failures = []
    passed = threading.Event()
    window = webview.create_window("Acceptance Documents", origin, width=1440, height=900, focus=False)

    def open_import(page, path):
        if not page.js("document.querySelector('#import-dialog')?.open"):
            if not page.js("document.querySelector('#open-import')?.getClientRects().length"):
                page.click("#schedule-view .easy-more:not([open]) > summary", "İşlemler")
            page.click("#open-import")
        until("import dialog open", lambda: page.js("document.querySelector('#import-dialog')?.open"))
        page.upload("#import-file", path)

    def preview_ready(page):
        return page.js("""(() => {
          const table = document.querySelector('#import-preview > table');
          return !!table && table.querySelectorAll('tbody tr').length > 0;
        })()""")

    def close_import(page):
        if page.js("document.querySelector('#import-dialog')?.open"):
            page.click("#close-import")
            until("import dialog closed", lambda: not page.js(
                "document.querySelector('#import-dialog')?.open"))

    def scenario(page):
        until("application ready", lambda: page.js(
            "document.body.classList.contains('easy-mode') && !document.querySelector('#app-loader')"))
        web_scripts = sorted(path.name for path in (ROOT / "web").glob("*.js"))
        page.js(f"""(() => {{
          window.__webScriptChecks = undefined;
          Promise.all({json.dumps(web_scripts)}.map(async name => {{
            const response = await fetch('/' + name);
            if (!response.ok) throw new Error(`${{name}} returned ${{response.status}}`);
            const source = await response.text();
            try {{ new Function(source); return {{name, ok:true}}; }}
            catch (error) {{ return {{name, ok:false, error:String(error)}}; }}
          }})).then(results => {{ window.__webScriptChecks = results; }});
          return true;
        }})()""")
        script_checks = until("WebView2 JavaScript parse checks", lambda: page.js("window.__webScriptChecks"))
        invalid_scripts = [item for item in script_checks if not item["ok"]]
        if invalid_scripts:
            raise AssertionError(f"Web scripts failed to parse in WebView2: {invalid_scripts}")
        page.click('.main-nav [data-view="schedule"]')

        # #49: identical content under a new filename is recognized as unchanged.
        open_import(page, same)
        until("identical-content preview", lambda: preview_ready(page))
        same_preview = page.js("document.querySelector('#import-preview').innerText")
        if NAME not in same_preview or not page.js("document.querySelector('#apply-import').disabled"):
            raise AssertionError(f"Renamed identical file was not treated as unchanged: {same_preview}")
        close_import(page)

        # #49: changed content is distinguished and becomes an applicable draft.
        open_import(page, changed)
        until("changed-content preview", lambda: preview_ready(page))
        changed_preview = page.js("document.querySelector('#import-preview').innerText")
        if not any(word in changed_preview.lower() for word in ("değiş", "fark")):
            raise AssertionError(f"Changed row has no change classification: {changed_preview}")
        if not page.js("document.querySelector('#apply-import').disabled"):
            raise AssertionError("Changed imported row was applicable before the user chose its group")
        page.click("#import-apply-valid-group")
        if page.js("document.querySelector('#apply-import').disabled"):
            raise AssertionError("Choosing the changed valid group did not enable apply")
        close_import(page)

        # #50: choosing working-hours-only invalidates the first preview and requires a new one.
        open_import(page, scope_file)
        until("initial scope preview", lambda: preview_ready(page))
        if page.js("document.querySelector('#preview-import-scope').disabled"):
            raise AssertionError("Initial import rows are not available for a scoped preview")
        page.fill("#import-scope", "hours")
        until("scope re-preview enabled", lambda: not page.js(
            "document.querySelector('#preview-import-scope').disabled"))
        if not page.js("document.querySelector('#apply-import').disabled"):
            raise AssertionError("Changing scope did not invalidate the original preview")
        page.click("#preview-import-scope")
        until("working-hours-only preview", lambda: preview_ready(page))
        scope_preview = page.js("document.querySelector('#import-preview').innerText")
        review_rows = page.js(
            "document.querySelectorAll('#import-preview .import-review tbody tr').length")
        if "08:00" not in scope_preview or "16:00" not in scope_preview or review_rows:
            raise AssertionError(f"Hours-only preview included non-work rows or lost hours: {scope_preview}")
        close_import(page)

        # #51: malformed rows remain in review and never enter the valid draft table.
        original_review_bytes = review_file.read_bytes()
        open_import(page, review_file)
        until("all-records preview", lambda: preview_ready(page))
        valid_count = page.js("document.querySelectorAll('#import-preview > table tbody tr').length")
        review_count = page.js(
            "document.querySelectorAll('#import-preview .import-review tbody tr').length")
        if valid_count != 1 or review_count != 1:
            raise AssertionError(f"Expected one valid and one review record, got {valid_count}/{review_count}")
        if not page.js("document.querySelector('#import-apply-valid-group')?.checked === false"):
            raise AssertionError("The valid group was not presented as an explicit, initially unselected choice")
        if not page.js("document.querySelector('#apply-import').disabled"):
            raise AssertionError("Import apply was enabled before the user chose a group")
        valid_text = page.js("document.querySelector('#import-preview > table').innerText")
        review_text = page.js("document.querySelector('#import-preview .import-review').innerText")
        if "not-a-date" in valid_text or "not-a-date" not in review_text:
            raise AssertionError("Malformed source row was not isolated from the valid preview")
        # #52: source location and immutable source text stay beside the edited row.
        source_context = page.js("document.querySelector('#import-source-context').innerText")
        for expected in ("valid-and-review.xlsx", "Sayfa / tablo 1", "Tablo 1, satır 3", "not-a-date"):
            if expected not in source_context:
                raise AssertionError(f"Source comparison omitted {expected!r}: {source_context}")
        source_text = page.js("document.querySelector('#import-source').value")
        page.fill("#import-source", source_text.replace("not-a-date", "still-not-a-date", 1))
        edited_context = page.js("document.querySelector('#import-source-context').innerText")
        if "Özgün içerik" not in edited_context or "not-a-date" not in edited_context \
                or "still-not-a-date" not in edited_context:
            raise AssertionError(f"Original and edited source rows were not shown side by side: {edited_context}")
        if review_file.read_bytes() != original_review_bytes:
            raise AssertionError("Editing a source row modified the uploaded workbook")
        if not page.js("document.querySelector('#apply-import').disabled"):
            raise AssertionError("Editing the source row left an outdated preview applicable")
        page.js("window.confirm = () => true")
        close_import(page)
        open_import(page, review_file)
        until("fresh valid/review group preview", lambda: preview_ready(page))
        if page.js("document.querySelector('#import-apply-valid-group').checked"):
            raise AssertionError("A fresh preview unexpectedly retained the prior group selection")
        if not page.js("document.querySelector('#apply-import').disabled"):
            raise AssertionError("Apply was enabled without a fresh explicit group choice")
        page.click("#import-apply-valid-group")
        if page.js("document.querySelector('#apply-import').disabled"):
            raise AssertionError("Choosing the valid group did not enable apply")
        page.js("window.__importGroupConfirmation = ''; window.confirm = text => { window.__importGroupConfirmation = text; return true; }")
        page.click("#apply-import")
        result = page.js(f"""(() => ({{
          confirmation: window.__importGroupConfirmation,
          toast: document.querySelector('#toast').textContent,
          validApplied: state.schedule.entries.some(entry =>
            String(entry.employee_id) === String({json.dumps(str(person))})
            && entry.days[{json.dumps(next_day)}]?.start === "08:00"
            && entry.days[{json.dumps(next_day)}]?.end === "16:00"),
          toastVisible: document.querySelector('#toast').getClientRects().length > 0,
        }}))()""")
        if not result or not result["validApplied"] or not result["toastVisible"] \
                or "1 kayıt" not in result["confirmation"] \
                or "İnceleme grubu (1 kayıt) aktarılmayacak" not in result["confirmation"] \
                or "Uygun grup: 1 uygulandı (1 yeni, 0 güncellendi, 0 aynı); inceleme grubu: 1 aktarılmadı" not in result["toast"]:
            raise AssertionError(f"Only the chosen valid group was not applied with separate result counts: {result}")
        close_import(page)
        if not page.js("document.querySelector('#undo-import').getClientRects().length"):
            page.click("#schedule-view .easy-more:not([open]) > summary", "İşlemler")
        page.click("#undo-import")
        page.js("saveSchedule({assistantConfirmed:true}); true")
        until("temporary schedule restored after import test", lambda: page.js("!state.scheduleDirty"))

        # #53: changed source headings require explicit confirmation before mapped preview.
        open_import(page, profile_base)
        until("profile baseline preview", lambda: preview_ready(page))
        page.click("#open-import-mapping")
        for selector, value in (
            ("#map-mode", "rows"),
            ("#map-start-row", "2"),
            ("#map-person-col", "1"),
            ("#map-section-col", "2"),
            ("#map-date-col", "3"),
            ("#map-start-col", "4"),
            ("#map-end-col", "5"),
            ("#map-status-col", "6"),
        ):
            page.fill(selector, value)
        page.fill("#import-profile-name", "Acceptance header profile")
        page.click("#save-import-profile")
        until("profile saved", lambda: page.js(
            "document.querySelector('#import-profile-select').options.length > 1"))
        page.upload("#import-file", profile_changed)
        until("changed-header mapping", lambda: not page.js(
            "document.querySelector('#import-mapping').hidden"))
        page.click("#open-import-mapping")
        page.click("#preview-import-mapping")
        until("header-change confirmation required", lambda: not page.js(
            "document.querySelector('#import-profile-header-warning').hidden"))
        warning = page.js("document.querySelector('#import-profile-header-warning').innerText")
        if "başlık" not in warning.lower() and "sütun" not in warning.lower():
            raise AssertionError(f"Header mismatch warning is unclear: {warning}")
        if preview_ready(page):
            raise AssertionError("Mapped preview was produced before the header change was confirmed")
        page.click("#import-profile-header-confirm")
        page.click("#preview-import-mapping")
        until("confirmed mapped preview", lambda: preview_ready(page))
        if NAME not in page.js("document.querySelector('#import-preview').innerText"):
            raise AssertionError("Confirmed mapped preview omitted the employee row")
        close_import(page)

        # #63: a reordered layout flags the stale personnel-column mapping and isolates the row for review.
        open_import(page, profile_misaligned)
        until("reordered-source extraction", lambda: page.js(
            "document.querySelector('#import-source').value.includes('Tarih')"))
        page.click("#open-import-mapping")
        profile_id = page.js("""[...document.querySelector('#import-profile-select').options]
          .find(option => option.textContent === 'Acceptance header profile')?.value || ''""")
        if not profile_id:
            raise AssertionError("Saved profile was not available for the changed personnel layout")
        page.fill("#import-profile-select", profile_id)
        page.click("#preview-import-mapping")
        until("misaligned personnel mapping warning", lambda: not page.js(
            "document.querySelector('#import-profile-header-warning').hidden"))
        mapping_warning = page.js("document.querySelector('#import-profile-header-warning').innerText")
        if "person · sütun 1" not in mapping_warning or "PERSONEL" not in mapping_warning \
                or "TARIH" not in mapping_warning:
            raise AssertionError(f"Changed personnel-column mapping was not identified: {mapping_warning}")
        if preview_ready(page):
            raise AssertionError("Misaligned personnel mapping produced a preview before explicit confirmation")
        page.click("#import-profile-header-confirm")
        page.click("#preview-import-mapping")
        until("misaligned row isolated for review", lambda: page.js(
            "document.querySelectorAll('#import-preview .import-review tbody tr').length === 1"))
        mapping_result = page.js("""(() => ({
          valid: document.querySelectorAll('#import-preview > table tbody tr').length,
          review: document.querySelectorAll('#import-preview .import-review tbody tr').length,
          reviewText: document.querySelector('#import-preview .import-review').innerText,
          applyDisabled: document.querySelector('#apply-import').disabled,
        }))()""")
        if mapping_result["valid"] != 0 or mapping_result["review"] != 1 \
                or not mapping_result["applyDisabled"] or NAME not in mapping_result["reviewText"]:
            raise AssertionError(f"Wrong personnel mapping was not prevented from entering the valid group: {mapping_result}")
        close_import(page)

        # #54: the real OCR review renders the crop belonging to a recognized source row.
        open_import(page, scan)
        until("OCR source crop rendered", lambda: page.js("""(() => {
          const section = document.querySelector('#import-ocr-evidence');
          const image = section?.querySelector('img');
          return !!section && !section.hidden && !!image && image.complete && image.naturalWidth > 0;
        })()"""), timeout=60)
        evidence = page.js("document.querySelector('#import-ocr-evidence').innerText")
        image_width = page.js("document.querySelector('#import-ocr-evidence img').naturalWidth")
        message = page.js("document.querySelector('#import-message').textContent")
        if image_width < 1 or not evidence.strip() or "alan bazlı" not in message.lower():
            raise AssertionError("OCR row lacks rendered source evidence or honest confidence messaging")
        close_import(page)

        # #57: editing-only boundary guides appear around template regions but not in output.
        page.click('.main-nav [data-view="templates"]')
        until("template management view ready", lambda: page.js("""(() => {
          const view = document.querySelector('#templates-view');
          const button = view?.querySelector('[data-template-kind="schedule"] [data-template-edit]');
          return view?.classList.contains('active') && button?.getClientRects().length;
        })()"""))
        page.click('#templates-view [data-template-kind="schedule"] [data-template-edit]')
        until("template guide boundaries", lambda: page.js("""(() => {
          const table = document.querySelector('#schedule-table');
          const signature = document.querySelector('.template-guide-signature');
          return !!table && table.querySelector('.template-guide-header')
            && table.querySelector('.template-guide-person')
            && !!signature && signature.getClientRects().length > 0;
        })()"""))
        # #63: template layout edits require review before the changed settings can be saved.
        page.js("""(() => {
          window.__templateReviewPrompt = '';
          window.__templateReviewResult = undefined;
          window.confirm = text => { window.__templateReviewPrompt = text; return false; };
          const settings = structuredClone(state.workbookSettings);
          const key = templateSheetKey('schedule');
          const before = settings.sheets[key] || {cells: {}, hide_notes: false, zoom: 55};
          settings.sheets[key] = {
            ...before,
            merges_add: [...(before.merges_add || []), {row: 40, column: 40, rowspan: 1, colspan: 2}],
            column_widths: {...(before.column_widths || {}), "1": 18},
            row_heights: {...(before.row_heights || {}), "1": 30},
            page_setup: {...(before.page_setup || {}), footer_text: "İmza konumu"},
          };
          persistWorkbookSettings(settings).then(result => { window.__templateReviewResult = result; });
          return true;
        })()""")
        until("template change review completed", lambda: page.js(
            "window.__templateReviewResult !== undefined"))
        review = page.js("window.__templateReviewPrompt")
        if page.js("window.__templateReviewResult") is not False or not all(
            label in review for label in ("Eklenen birleştirmeler", "Sütun genişlikleri", "Satır yükseklikleri", "Alt bilgi")
        ):
            raise AssertionError(f"Template edits were not reviewed before saving: {review}")
        layout = page.js("""(() => {
          const table = document.querySelector('#schedule-table');
          const rows = [...table.tBodies[0].rows].filter(row => row.getClientRects().length);
          if (rows.length < 2) return {rows: rows.length};
          const pageHeight = table.getBoundingClientRect().width * 194 / 281;
          const zoom = parseFloat(getComputedStyle(table).zoom) || 1;
          rows[0].style.height = `${pageHeight * 0.8 / zoom}px`;
          rows[1].style.height = `${pageHeight * 0.8 / zoom}px`;
          renderTemplateGuides('schedule');
          const breaks = table.querySelectorAll('.template-guide-page-break').length;
          rows[rows.length - 1].style.height = `${pageHeight * 2 / zoom}px`;
          renderTemplateGuides('schedule');
          const signature = document.querySelector('.template-guide-signature');
          return {
            rows: rows.length,
            breaks,
            overflowRows: table.querySelectorAll('.template-guide-row-overflow').length,
            signatureOverflow: signature?.dataset.overflow === 'true',
          };
        })()""")
        if not layout or layout.get("rows", 0) < 2 or not layout.get("breaks") \
                or not layout.get("overflowRows") or not layout.get("signatureOverflow"):
            raise AssertionError(f"Page-break/overflow guides did not match the forced layout: {layout}")
        assert_template_output_excludes_guides(origin, month)
        page.js("""(() => {
          document.querySelectorAll('#schedule-table tbody tr').forEach(row => row.style.removeProperty('height'));
          renderTemplateGuides('schedule');
          return true;
        })()""")
        page.click(".template-editor-heading button", "Şablonlara dön")
        until("template guide removal", lambda: not page.js(
            "document.querySelector('.template-guide-signature')"))

        # #64: warn about very small single-page text, let the user choose a wider page count.
        page.js("window.confirm = () => true; openPrintWizard();")
        until("print wizard ready", lambda: page.js(
            "!!document.querySelector('#print-wizard-form [name=\"fit_width\"]')"))
        page.fill('#print-wizard-form [name="fit_width"]', "1")
        readability = until("small-print warning", lambda: page.js("""(() => {
          const notice = document.querySelector('[data-pw-readability]');
          return notice && !notice.hidden ? notice.textContent : '';
        })()"""))
        if "öneri" not in readability.lower() and "sayfa" not in readability.lower():
            raise AssertionError(f"Readability warning has no multi-page suggestion: {readability}")
        page.fill('#print-wizard-form [name="fit_width"]', "2")
        if not page.js("document.querySelector('[data-pw-readability]').hidden"):
            raise AssertionError("A two-page setting still shows the single-page readability warning")
        page.click("#print-wizard .extras-actions button", "Önizle")
        until("two-page print preview", lambda: page.js(
            "!document.querySelector('[data-pw-preview]').hidden && document.querySelector('[data-pw-preview]').innerText.length > 0"))
        with urlopen(f"{origin}/api/export?kind=schedule&month={month}&week=0", timeout=20) as response:
            from openpyxl import load_workbook
            print_workbook = load_workbook(BytesIO(response.read()), read_only=False, data_only=False)
        try:
            if print_workbook.active.page_setup.fitToWidth != 2 or print_workbook.active.page_setup.fitToHeight != 0:
                raise AssertionError("The chosen two-page width was not applied to the generated Excel output")
        finally:
            print_workbook.close()
        page.js("document.querySelector('#print-wizard')?.close()")

        # #59: output scope is explicit and independent of the active search filter.
        page.click('.main-nav [data-view="schedule"]')
        page.fill("#schedule-search", NAME)
        until("one schedule person visible", lambda: "1 eşleşen personel satırı" in page.js(
            "document.querySelector('#schedule-filter-count').textContent"))
        if not page.js("document.querySelector('[data-output-kind=\"schedule\"]')?.getClientRects().length"):
            page.click("#schedule-view .easy-more:not([open]) > summary", "İşlemler")
        page.click('[data-output-kind="schedule"]')
        until("output scope choices", lambda: page.js("""(() => {
          const field = document.querySelector('#output-scope-field');
          const options = [...document.querySelectorAll('#output-scope option')].map(option => option.textContent);
          return !field.hidden && options.some(text => text.includes('Görünen 1 personel'))
            && options.some(text => text.includes('Tüm 2 personel'));
        })()"""))
        full_range = page.js("""(() => {
          const table = document.querySelector('#schedule-table');
          return `${excelColumnName(table.tHead.rows[0].cells.length - 1)}${table.tBodies[0].rows.length}`;
        })()""")
        page.fill("#output-start", "A1")
        page.fill("#output-end", full_range)
        page.click("#output-preview-button")
        visible_height = until("visible-scope output preview", lambda: page.js(
            "document.querySelector('#output-preview img')?.naturalHeight || 0"))
        visible_source = page.js("document.querySelector('#output-preview img')?.src")
        page.fill("#output-scope", "all")
        page.click("#output-preview-button")
        all_height = until("all-scope output preview", lambda: page.js(f"""(() => {{
          const image = document.querySelector('#output-preview img');
          return image && image.src !== {json.dumps(visible_source)} ? image.naturalHeight : 0;
        }})()"""))
        if all_height <= visible_height:
            raise AssertionError(f"All-personnel output did not include filtered rows: {visible_height} vs {all_height}")
        if page.js("document.querySelector('#schedule-search').value") != NAME:
            raise AssertionError("All-personnel output changed the active schedule filter")
        page.click('#output-dialog [data-close-dialog="output-dialog"]')

        # #60: the copied employee summary contains only safe schedule fields.
        with urlopen(
            f"{origin}/api/employee-profile?employee_id={person}&fields=title", timeout=20,
        ) as response:
            profile_fields = json.loads(response.read().decode("utf-8"))
        if profile_fields != {"title": "Vardiya Sorumlusu"}:
            raise AssertionError(f"Minimal role lookup returned extra employee data: {profile_fields}")
        page.js("""(() => {
          window.__personalCardDrawnText = [];
          window.__personalCardDownload = null;
          const fillText = CanvasRenderingContext2D.prototype.fillText;
          CanvasRenderingContext2D.prototype.fillText = function(text, ...args) {
            window.__personalCardDrawnText.push(String(text));
            return fillText.call(this, text, ...args);
          };
          const createObjectURL = URL.createObjectURL.bind(URL);
          URL.createObjectURL = blob => {
            if (blob?.type === 'image/png') {
              window.__personalCardDownload = {
                type: blob.type,
                size: blob.size,
                filename: '',
                signature: null,
              };
              blob.arrayBuffer().then(buffer => {
                window.__personalCardDownload.signature = Array.from(new Uint8Array(buffer).slice(0, 8));
              });
            }
            return createObjectURL(blob);
          };
          const anchorClick = HTMLAnchorElement.prototype.click;
          HTMLAnchorElement.prototype.click = function() {
            if (this.download?.startsWith('Vardiya_') && window.__personalCardDownload) {
              window.__personalCardDownload.filename = this.download;
            }
            return anchorClick.call(this);
          };
          return true;
        })()""")
        page.click("#tools-menu-button")
        page.click("#tools-menu .easy-tool-heading", "Personel")
        page.click(".easy-tool-flyout button", "Kişisel vardiya kartı (PNG)")
        until("personal card and role", lambda: page.js("""(() => {
          const canvas = document.querySelector('#personal-card-preview canvas');
          return !!canvas && canvas.width > 0;
        })()"""))
        page.click('#personal-card-dialog [data-personal="download"]')
        output = until("downloaded shared staff noticeboard PNG", lambda: page.js("""(() => {
          const result = window.__personalCardDownload;
          return result?.signature?.join(',') === '137,80,78,71,13,10,26,10' ? result : null;
        })()"""))
        drawn_text = page.js("window.__personalCardDrawnText")
        for expected in (NAME, "Görev: Vardiya Sorumlusu", "07:00–15:00", "Plan sürümü"):
            if not any(expected in value for value in drawn_text):
                raise AssertionError(f"Shared PNG did not render {expected!r}: {drawn_text}")
        if output["type"] != "image/png" or output["size"] < 1000 \
                or not output["filename"].startswith("Vardiya_Acceptance_Documents_"):
            raise AssertionError(f"Personal card did not produce the downloadable PNG artifact: {output}")
        for private_value in (
            "05000000000", "PRIVATE_INTERNAL_NOTE", "PRIVATE_SHIFT_NOTE",
            "PRIVATE_PERMISSION_REASON", "PRIVATE_LEAVE_NOTE",
        ):
            if any(private_value in value for value in drawn_text):
                raise AssertionError(f"Private field leaked into the rendered PNG: {private_value}")
        page.click("#personal-card-dialog [data-extras-close]")

        # #61: partial output results show reasons and retry only failed documents.
        page.js(f"""(() => {{
          window.__documentBatchPosts = [];
          const originalFetch = window.fetch.bind(window);
          window.fetch = (input, options) => {{
            if (String(input).includes('/api/document-workflows/documents/batch')) {{
              window.__documentBatchPosts.push(JSON.parse(options.body));
            }}
            return originalFetch(input, options);
          }};
          void window.documentWorkflows.openSelection([
            {{employee_id: {person}, date: {json.dumps(day)}, code: 'G'}},
            {{employee_id: {person}, date: {json.dumps(next_day)}, code: 'G'}}
          ]);
          return true;
        }})()""")
        until("batch document form", lambda: page.js(
            "!!document.querySelector('#easy-document-generate')"))
        page.fill('[data-template-code="G"]', "neutral")
        page.js("""(() => {
          const content = document.querySelector('#easy-document-workflows .easy-document-workflows-content');
          const preview = document.querySelector('#easy-document-field-preview');
          content.style.maxHeight = '180px';
          content.style.overflowY = 'auto';
          preview.style.marginTop = '600px';
          content.scrollTop = content.scrollHeight;
          return true;
        })()""")
        page.click('[data-document-field="event_time"]')
        source_field_state = page.js("""(() => {
          const content = document.querySelector('#easy-document-workflows .easy-document-workflows-content');
          const input = document.querySelector('#easy-document-event-time');
          const outer = content.getBoundingClientRect(), field = input.getBoundingClientRect();
          return {focused:document.activeElement === input,
            visible:field.top >= outer.top && field.bottom <= outer.bottom,
            scrollTop:content.scrollTop};
        })()""")
        if not source_field_state["focused"] or not source_field_state["visible"]:
            raise AssertionError(f"Missing preview field did not navigate to its visible source: {source_field_state}")
        page.js("""(() => {
          const content = document.querySelector('#easy-document-workflows .easy-document-workflows-content');
          content.style.maxHeight = '';
          content.style.overflowY = '';
          document.querySelector('#easy-document-field-preview').style.marginTop = '';
          return true;
        })()""")
        page.fill("#easy-document-event-time", "14:35")
        preview = page.js("document.querySelector('#easy-document-field-preview').innerText")
        if "[Olay saati]" in preview or "14:35" not in preview:
            raise AssertionError(f"Event-time field preview did not replace its helper marker: {preview}")
        page.fill("#easy-document-visibility", "staff")
        page.fill("#easy-document-internal-note", "PRIVATE_MANAGER_NOTE")
        page.js("""(() => {
          window.__visibilityPrompt = '';
          window.confirm = text => { window.__visibilityPrompt = text; return true; };
          return true;
        })()""")
        page.fill("#easy-document-signers", "İmza A\nİmza B")
        page.click("#easy-document-generate")
        generation_scope_prompt = until("visibility confirmation before generation", lambda: page.js(
            "window.__visibilityPrompt"))
        if "personelin" not in generation_scope_prompt.lower() or "iç yönetici notu" not in generation_scope_prompt.lower():
            raise AssertionError(f"Generation did not confirm staff visibility and internal-note separation: {generation_scope_prompt}")
        first_result = until("batch output result rendering", lambda: page.js("""(() => {
          const result = document.querySelector('#easy-document-generation-result');
          return result?.innerText || '';
        })()"""))
        if "1 başarılı, 1 başarısız" not in first_result:
            raise AssertionError(f"Partial output result did not preserve the successful and failed counts: {first_result}")
        if "Sentetik çıktı üretim hatası" not in first_result:
            raise AssertionError(f"Failed output does not show its reason: {first_result}")
        if not all(value in first_result for value in (NAME, day, next_day, "Yalnız başarısızları yeniden dene (1)")):
            raise AssertionError(f"Bulk result summary omitted a person, date or retry count: {first_result}")
        first_posts = page.js("window.__documentBatchPosts")
        if len(first_posts) != 1 or len(first_posts[0]["cells"]) != 2:
            raise AssertionError(f"Initial output request did not contain both selected documents: {first_posts}")
        if page.js("document.querySelectorAll('#easy-document-generation-result [data-preview-document]').length") != 1:
            raise AssertionError("Partial result did not list its one successful document separately")
        if page.js("document.querySelectorAll('#easy-document-generation-result [data-save-document]').length") != 1:
            raise AssertionError("Partial result did not expose a save action for its successful document")
        generation_failure[0] = False
        page.click("#easy-documents-retry")
        retry_result = until("failed-only retry result", lambda: page.js("""(() => {
          const result = document.querySelector('#easy-document-generation-result');
          return result?.innerText.includes('2 başarılı, 0 başarısız') ? result.innerText : '';
        })()"""))
        second_posts = page.js("window.__documentBatchPosts")
        if len(second_posts) != 2 or len(second_posts[1]["cells"]) != 1 \
                or second_posts[1]["cells"][0]["date"] != next_day:
            raise AssertionError(f"Retry did not send only the failed document: {second_posts}")
        if "Başarılı belgeler" not in retry_result or "Başarısız belgeler ve nedenleri" not in retry_result:
            raise AssertionError(f"Batch result sections are missing: {retry_result}")
        if "Başarısız belge yok." not in retry_result:
            raise AssertionError(f"Successful retry left an incorrect failure summary: {retry_result}")
        with Store(db) as result_store:
            rows = result_store.connection.execute(
                "SELECT exact_dates,filename,content,visibility_scope,internal_note FROM workflow_documents ORDER BY exact_dates"
            ).fetchall()
        if len(rows) != 2:
            raise AssertionError(f"Expected two generated documents after retry, found {len(rows)}")
        from openpyxl import load_workbook
        output_text = []
        for row in rows:
            if row["filename"].endswith(".docx"):
                output_text.append(document_workflows.forms.preview(row["content"], "docx"))
            else:
                workbook = load_workbook(BytesIO(row["content"]), read_only=True, data_only=True)
                try:
                    output_text.append("\n".join(
                        str(cell.value)
                        for sheet in workbook.worksheets
                        for row_cells in sheet.iter_rows()
                        for cell in row_cells
                        if cell.value is not None
                    ))
                finally:
                    workbook.close()
        if not all(NAME in text for text in output_text):
            raise AssertionError(f"Generated DOCX files do not contain the selected employee: {output_text}")
        expected_dates = {day, next_day}
        if not all(any(expected in text for text in output_text) for expected in expected_dates):
            raise AssertionError(f"Generated DOCX files do not contain both selected dates: {output_text}")
        if not all("14:35" in text and "[Olay saati]" not in text for text in output_text):
            raise AssertionError(f"Event-time helper was not replaced in generated official documents: {output_text}")
        if not all(row["visibility_scope"] == "staff" and row["internal_note"] == "PRIVATE_MANAGER_NOTE" for row in rows):
            raise AssertionError("Visibility scope or separate internal-note metadata was not retained")
        page.click("#easy-document-generation-result [data-preview-document]")
        until("staff-visible preview", lambda: page.js(
            "!!document.querySelector('#easy-document-workflows .easy-document-preview-frame')?.srcdoc.includes('14:35')"))
        preview_frame = page.js("document.querySelector('#easy-document-workflows .easy-document-preview-frame').srcdoc")
        if "PRIVATE_MANAGER_NOTE" in preview_frame:
            raise AssertionError("Manager-only note leaked into the staff-visible document preview")
        if "önizleme" not in page.js("window.__visibilityPrompt").lower():
            raise AssertionError("Staff-visible preview did not request a scope confirmation")
        page.js("""(() => {
          window.__printRequested = false;
          const frame = document.querySelector('#easy-document-workflows .easy-document-preview-frame');
          frame.contentWindow.print = () => { window.__printRequested = true; };
          return true;
        })()""")
        page.click("#easy-document-workflows [data-print-document]")
        until("staff output scope reconfirmed", lambda: page.js("window.__printRequested"))
        if "yazdırma" not in page.js("window.__visibilityPrompt").lower():
            raise AssertionError("Staff-visible print did not request a scope confirmation")
        page.click("#easy-document-workflows [data-back-document-center]")
        until("signer list in document records", lambda: page.js(
            "document.querySelector('#easy-document-workflows')?.innerText.includes('İmza A')"))
        record_text = page.js("document.querySelector('#easy-document-workflows').innerText")
        if "Personele gösterilebilir" not in record_text or "PRIVATE_MANAGER_NOTE" not in record_text:
            raise AssertionError(f"Document record did not keep visibility and private-note metadata separate: {record_text}")
        page.click('#easy-document-workflows .easy-document-row button[data-next-status="printed"]')
        until("printed status for signer tracking", lambda: page.js(
            "!!document.querySelector('#easy-document-workflows .easy-document-row [data-document-signer]')"))
        signed_document_id = page.js(
            "document.querySelector('#easy-document-workflows .easy-document-row [data-document-signer]').dataset.documentSigner")
        document_row = f'#easy-document-workflows .easy-document-row[data-document-id="{signed_document_id}"]'
        page.js("""(async () => {
          const response = await fetch(`/api/document-workflows/documents/${encodeURIComponent(
            document.querySelector('#easy-document-workflows .easy-document-row [data-document-signer]').dataset.documentSigner
          )}/status`, {
            method: 'PUT', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({status:'signed'})
          });
          window.__blockedSignature = {status: response.status, body: await response.json()};
        })()""")
        blocked_signature = until("unsigned completion rejected", lambda: page.js("window.__blockedSignature"))
        if blocked_signature["status"] != 400 or "tüm imzacıların" not in blocked_signature["body"]["error"].lower():
            raise AssertionError(f"Document was allowed to complete with unsigned signers: {blocked_signature}")
        page.click(f'{document_row} [data-document-signer="{signed_document_id}"]')
        until("one signer remains incomplete", lambda: page.js("""(() => {
          const row = document.querySelector(""" + json.dumps(document_row) + """);
          return row && row.innerText.includes('İmza A · Tamamlandı')
            && row.innerText.includes('İmza B · Bekliyor') ? row.innerText : '';
        })()"""))
        signed_button = f'{document_row} button[data-next-status="signed"]'
        if page.js(f"!!document.querySelector({json.dumps(signed_button)})"):
            raise AssertionError("The overall document was signable while one signer was still pending")
        page.click(f'{document_row} [data-document-signer="{signed_document_id}"]')
        until("all signers complete", lambda: page.js("""(() => {
          const row = document.querySelector(""" + json.dumps(document_row) + """);
          return row && row.innerText.includes('İmza A · Tamamlandı')
            && row.innerText.includes('İmza B · Tamamlandı');
        })()"""))
        page.click(f'{document_row} button[data-next-status="signed"]')
        signed_text = until("overall document marked signed", lambda: page.js(
            f"document.querySelector({json.dumps(document_row)})?.innerText.includes('İmzalandı')"))
        if not signed_text:
            raise AssertionError("Overall document status did not complete after all signers")
        page.click("#easy-document-workflows .dialog-close")

        # #67: reading persists and carries forward independently of claim/completion.
        page.js(f"""(() => {{
          window.__handoverCreated = undefined;
          fetch('/api/handover', {{
            method:'POST', headers:{{'Content-Type':'application/json'}},
            body:JSON.stringify({{date:{json.dumps(day)}, section:'MARKET', shift:'Sabah',
              text:'Acceptance carry-forward task'}})
          }}).then(async response => {{
            window.__handoverCreated = {{status:response.status, body:await response.json()}};
          }});
          return true;
        }})()""")
        handover_created = until("handover task created", lambda: page.js("window.__handoverCreated"))
        if handover_created["status"] != 200:
            raise AssertionError(f"Could not create synthetic handover task: {handover_created}")
        handover_id = handover_created["body"]["note"]["id"]
        handover_row = f'#duty-board-dialog .duty-note:has([data-note-delete="{handover_id}"])'

        def persisted_handover(start, end, expected_status):
            with urlopen(
                f"{origin}/api/handover?from={start}&to={end}", timeout=20,
            ) as response:
                notes = json.loads(response.read().decode("utf-8"))["notes"]
            note = next((note for note in notes if note["id"] == handover_id), None)
            if not note or note["status"] != expected_status:
                return None
            if expected_status == "read" and (
                note["done"] or note["claimed_by"]
                or not note.get("read_by") or not note.get("read_at")
            ):
                return None
            return note

        page.js(f"openDutyBoard({json.dumps(next_day)}); true")
        until("open task carried to next team", lambda: page.js(f"""(() => {{
          const row = document.querySelector({json.dumps(handover_row)});
          return row?.querySelector('strong')?.textContent === 'Açık';
        }})()"""))
        if page.js("!!document.querySelector('#duty-board-dialog [data-note-done]')"):
            raise AssertionError("Handover state is represented by a completion checkbox instead of distinct actions")
        page.js(f"""(() => {{
          const fetchBeforeRead = window.fetch;
          window.__restoreHandoverFetch = () => {{ window.fetch = fetchBeforeRead; }};
          window.fetch = async (input, options) => {{
            if (input === '/api/handover' && options?.method === 'POST') {{
              const payload = JSON.parse(options.body);
              if (payload.id === {json.dumps(handover_id)} && payload.status === 'read') {{
                await new Promise(resolve => setTimeout(resolve, 500));
              }}
            }}
            return fetchBeforeRead(input, options);
          }};
          return true;
        }})()""")
        page.click(f'{handover_row} [data-note-transition="read"]')
        until("correct task rendered as read without being claimed", lambda: page.js(f"""(() => {{
          const row = document.querySelector({json.dumps(handover_row)});
          return row?.querySelector('strong')?.textContent === 'Okundu'
            && !!row.querySelector('[data-note-transition="claimed"]')
            && !row.querySelector('[data-note-transition="read"]')
            && row.innerText.includes('Okuyan:')
            && !row.innerText.includes('Üstlenen:');
        }})()"""))
        read_note = until("correct task read acknowledgment persisted", lambda:
            persisted_handover(day, day, "read"))
        page.js("window.__restoreHandoverFetch(); true")
        page.js("document.querySelector('#duty-board-dialog')?.close()")
        page.js(f"openDutyBoard({json.dumps(next_day)}); true")
        until("read state survives reopening and carry-forward", lambda: page.js(f"""(() => {{
          const dialog = document.querySelector('#duty-board-dialog');
          const row = document.querySelector({json.dumps(handover_row)});
          return dialog?.open && row?.querySelector('strong')?.textContent === 'Okundu'
            && !!row.querySelector('[data-note-transition="claimed"]')
            && !row.querySelector('[data-note-transition="completed"]')
            && row.innerText.includes('Okuyan:');
        }})()"""))
        reopened_note = persisted_handover(next_day, next_day, "read")
        if not reopened_note or any(
            reopened_note[field] != read_note[field] for field in ("read_by", "read_at")
        ):
            raise AssertionError(f"Reopening lost the carried read acknowledgment: {reopened_note}")
        page.click(f'{handover_row} [data-note-transition="claimed"]')
        until("correct task claimed", lambda: page.js(f"""(() => {{
          const row = document.querySelector({json.dumps(handover_row)});
          return row?.querySelector('strong')?.textContent === 'Üstlenildi'
            && row.innerText.includes('Üstlenen:') && row.innerText.includes('Okuyan:');
        }})()"""))
        until("correct task claim persisted", lambda:
            persisted_handover(next_day, next_day, "claimed"))
        page.click(f'{handover_row} [data-note-transition="completed"]')
        until("completed task removed from the next shift", lambda: page.js(
            f"!document.querySelector({json.dumps(handover_row)})"))
        completed_note = persisted_handover(day, day, "completed")
        if not completed_note or not completed_note["done"]:
            raise AssertionError(f"Completion was not retained in task history: {completed_note}")
        with urlopen(
            f"{origin}/api/handover?from={next_day}&to={next_day}", timeout=20,
        ) as response:
            carried_notes = json.loads(response.read().decode("utf-8"))["notes"]
        if any(note["id"] == handover_id for note in carried_notes):
            raise AssertionError("Completed task still carries forward in the independent ledger GET")
        page.js("document.querySelector('#duty-board-dialog')?.close()")

        # #56: every source punch is visible; the fifth, unpaired punch stays unmatched.
        page.click("#tools-menu-button")
        page.click("#tools-menu .easy-tool-heading", "Puantaj ve bordro")
        page.click(".easy-tool-flyout button", "PDKS ↔ plan mutabakatı")
        until("PDKS dialog open", lambda: page.js("!!document.querySelector('#pdks-dialog')"))
        page.upload("#pdks-file", pdks)
        page.click("#pdks-import")
        until("PDKS timeline generated", lambda: page.js(
            "!!document.querySelector('#pdks-result .pdks-timeline summary' )"), timeout=40)
        page.click("#pdks-result .pdks-timeline summary")
        timeline = page.js("document.querySelector('#pdks-result .pdks-timeline').innerText")
        for punch in ("08:00", "12:00", "13:00", "17:00", "18:00", "Çıkış yok"):
            if punch not in timeline:
                raise AssertionError(f"PDKS source timeline omitted {punch}: {timeline}")
        source_health = page.js("document.querySelector('#pdks-result').innerText")
        if "eksik" not in source_health.lower():
            raise AssertionError(f"Unpaired final punch is not surfaced as incomplete: {source_health}")
        page.click("#pdks-dialog .dialog-close")

        # #68: newly uploaded templates remain drafts until activated and may be retired once.
        template_doc = Document()
        template_doc.add_paragraph("{{personel}} {{tarih}}")
        template_bytes = BytesIO()
        template_doc.save(template_bytes)
        template_payload = {
            "id": "", "name": "Acceptance lifecycle template", "category": "G",
            "filename": "acceptance-lifecycle.docx",
            "content": base64.b64encode(template_bytes.getvalue()).decode("ascii"),
        }
        page.js(f"""(async () => {{
          const response = await fetch('/api/forms', {{
            method:'POST', headers:{{'Content-Type':'application/json'}},
            body:JSON.stringify({json.dumps(template_payload)})
          }});
          window.__templateCreated = {{status:response.status, body:await response.json()}};
        }})()""")
        template_created = until("new form template stored as draft", lambda: page.js("window.__templateCreated"))
        if template_created["status"] != 200:
            raise AssertionError(f"Could not create synthetic form template: {template_created}")
        template_id = template_created["body"]["id"]
        page.js("window.confirm = () => true")
        page.click(".nav-tab[data-view='attendance']")
        until("attendance view for template settings", lambda: page.js(
            "document.querySelector('#attendance-view').classList.contains('active')"))
        if page.js("document.querySelector('#attendance-view').classList.contains('easy-ribbon-collapsed')"):
            page.click("#attendance-view .easy-ribbon-toggle")
            until("attendance ribbon expanded", lambda: page.js(
                "!document.querySelector('#attendance-view').classList.contains('easy-ribbon-collapsed')"))
        if not page.js("document.querySelector('#open-form-settings')?.getClientRects().length"):
            page.click("#attendance-view .easy-more:not([open]) > summary", "İşlemler")
        page.js("""(() => {
          const button = document.querySelector('#open-form-settings');
          button?.scrollIntoView({block:'nearest', inline:'center', behavior:'instant'});
          const ribbon = button?.closest('.control-strip');
          if (ribbon) ribbon.scrollLeft = ribbon.scrollWidth;
        })()""")
        page.click("#open-form-settings")
        template_row = f'#form-settings-list .form-settings-row button[data-form-id="{template_id}"]'
        until("draft template status in form settings", lambda: page.js(f"""(() => {{
          const action = document.querySelector('#form-settings-list .form-settings-row button[data-form-id="{template_id}"][data-form-status="active"]');
          return action?.textContent.trim() === 'Kullanıma aç';
        }})()"""))
        page.click(f'{template_row}[data-form-status="active"]')
        until("template activated in form settings", lambda: page.js(f"""(() => {{
          const action = document.querySelector('#form-settings-list .form-settings-row button[data-form-id="{template_id}"][data-form-status="retired"]');
          return action?.textContent.trim() === 'Kullanımdan kaldır';
        }})()"""))
        page.click(f'{template_row}[data-form-status="retired"]')
        until("template retired in form settings", lambda: page.js(f"""(() => {{
          const row = document.querySelector('#form-settings-list .form-settings-row');
          return row?.innerText.includes('Kullanımdan kaldırıldı')
            && !document.querySelector('#form-settings-list .form-settings-row button[data-form-id="{template_id}"][data-form-status]');
        }})()"""))
        page.click("#form-settings-dialog [data-close-dialog='form-settings-dialog']")

        # #103: retention is unset until configured; due records are reviewed, never deleted.
        with Store(db) as retention_store:
            before_count = retention_store.connection.execute(
                "SELECT COUNT(*) FROM workflow_documents",
            ).fetchone()[0]
            old_document = retention_store.connection.execute(
                "SELECT id FROM workflow_documents ORDER BY created_at,id LIMIT 1",
            ).fetchone()
            if old_document is None:
                raise AssertionError("No synthetic document is available for retention review")
            with retention_store.connection:
                retention_store.connection.execute(
                    "UPDATE workflow_documents SET created_at=? WHERE id=?",
                    ("2000-01-01T00:00:00+00:00", old_document["id"]),
                )
        if not page.js("document.querySelector('#open-document-center')?.getClientRects().length"):
            page.click("#attendance-view .easy-more:not([open]) > summary", "İşlemler")
        page.click("#open-document-center")
        until("document retention settings", lambda: page.js("!!document.querySelector('#easy-retention-days')"))
        retention_initial = page.js("""(() => ({
          days:document.querySelector('#easy-retention-days').value,
          status:document.querySelector('#easy-document-workflows [role="status"]')?.textContent || '',
          review:document.querySelector('#easy-document-workflows').innerText.includes('İnceleme listesi (0)'),
        }))()""")
        if retention_initial["days"] or "süre varsaymaz" not in retention_initial["status"] \
                or not retention_initial["review"]:
            raise AssertionError(f"Retention did not start explicitly unset: {retention_initial}")
        page.fill("#easy-retention-days", "1")
        page.click("#easy-document-workflows [data-save-retention]")
        until("configured retention review", lambda: page.js("""(() => {
          const root = document.querySelector('#easy-document-workflows');
          return root?.innerText.includes('İnceleme listesi (1)')
            && root.innerText.includes('1 gün sonra inceleme listesine alınır')
            && root.innerText.includes('silinmemiştir');
        })()"""))
        with Store(db) as retention_store:
            if retention_store.connection.execute(
                "SELECT COUNT(*) FROM workflow_documents",
            ).fetchone()[0] != before_count:
                raise AssertionError("Configuring retention deleted a due document")
        page.js("window.confirm = () => true")
        page.click("#easy-document-workflows [data-clear-retention]")
        until("cleared retention with documents preserved", lambda: page.js("""(() => {
          const root = document.querySelector('#easy-document-workflows');
          return document.querySelector('#easy-retention-days')?.value === ''
            && root?.innerText.includes('İnceleme listesi (0)')
            && root.innerText.includes('süre varsaymaz');
        })()"""))
        with Store(db) as retention_store:
            if retention_store.connection.execute(
                "SELECT COUNT(*) FROM workflow_documents",
            ).fetchone()[0] != before_count:
                raise AssertionError("Clearing retention deleted stored documents")

        print(
            "PASS #49: renamed identical/changed content previews; #50: scope re-preview; "
            "#51: only explicitly selected valid group applied; review group excluded; confirmation and result counts shown; "
            "#52: original file/page/row beside edited source, source file unchanged; "
            "#53: header confirmation; #54: OCR crop; "
            "#56: complete PDKS punch timeline; #57: editor-only template guides; "
            "#58: inline page breaks and signature overflow warning; "
            "#59: explicit filtered versus all-personnel output; "
            "#60: downloadable shared PNG rendered name/role/hours/revision; private fields excluded from its actual draw commands; "
            "#61: itemized partial-output summary and failed-only retry; "
            "#63: template edits summarized; reordered personnel column flagged and misaligned row isolated from valid/applicable records; "
            "#64: small-print warning suggests a user-selected two-page output; "
            "#65: per-signer completion gates the overall signed state; "
            "#66: clicking [Olay saati] scrolls to the source field; helper is not printed; "
            "#67: read acknowledgment persists and carries forward without claiming; reopen, claim, and completion remain separate; "
            "#68: new templates remain drafts, activate, then retire; "
            "#72: private notes stay separate and staff-visible output requires confirmation; "
            "#103: retention is organization-configured, due records are reviewed and never auto-deleted.",
            flush=True,
        )

    def drive():
        page = Browser(window)
        try:
            if not window.events.loaded.wait(40):
                raise TimeoutError("WebView2 did not load the local application")
            scenario(page)
            passed.set()
        except Exception as exc:
            failures.append(exc)
            try:
                print("Browser at failure:", page.js("""({
                    view: document.querySelector('.view.active')?.id,
                    modal: document.querySelector('dialog[open]')?.id,
                    toast: document.querySelector('#toast')?.textContent,
                    importMessage: document.querySelector('#import-message')?.textContent,
                    formSettingsButton: (() => {
                      const e = document.querySelector('#open-form-settings');
                      if (!e) return null;
                      const r = e.getBoundingClientRect(), x = r.x + r.width / 2, y = r.y + r.height / 2;
                      const ribbon = e.closest('.control-strip');
                      return {disabled:e.disabled, hidden:e.hidden, rect:{x:r.x,y:r.y,w:r.width,h:r.height},
                        display:getComputedStyle(e).display, visibility:getComputedStyle(e).visibility,
                        target:document.elementFromPoint(x,y)?.outerHTML?.slice(0,200),
                        ribbon:ribbon && {scrollLeft:ribbon.scrollLeft,scrollWidth:ribbon.scrollWidth,clientWidth:ribbon.clientWidth},
                        parents:[e.parentElement,e.parentElement?.parentElement,e.parentElement?.parentElement?.parentElement]
                          .map(n=>n&&({tag:n.tagName,id:n.id,className:n.className,display:getComputedStyle(n).display,hidden:n.hidden}))};
                    })(),
                    importButton: (() => {
                      const e = document.querySelector('#open-import');
                      if (!e) return null;
                      const r = e.getBoundingClientRect();
                      const x = r.x + r.width / 2, y = r.y + r.height / 2;
                      return {disabled:e.disabled, hidden:e.hidden, rect:{x:r.x,y:r.y,w:r.width,h:r.height},
                        style:getComputedStyle(e).display,
                        parents:(() => {let n=e,a=[];while(n&&a.length<8){a.push({tag:n.tagName,id:n.id,className:n.className,hidden:n.hidden,display:getComputedStyle(n).display});n=n.parentElement;}return a;})(),
                        target:document.elementFromPoint(x,y)?.outerHTML?.slice(0,200)};
                    })(),
                    pdksResult: document.querySelector('#pdks-result')?.textContent,
                })"""), flush=True)
            except Exception:
                pass
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
        document_workflows._neutral_status_form = original_neutral_form
    if failures:
        raise failures[0]
    if not passed.is_set():
        raise AssertionError("WebView2 closed before the scenario finished")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Run this standalone acceptance test on Windows with Edge WebView2.")
    with tempfile.TemporaryDirectory(prefix=".ui-documents-", dir=ROOT) as directory:
        run(Path(directory))
