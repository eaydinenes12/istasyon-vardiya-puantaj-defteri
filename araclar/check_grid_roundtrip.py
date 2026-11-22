"""Check schedule Undo and calendar fills against real XLSX exports of the same UI state."""
from contextlib import closing
from datetime import date
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
from urllib.request import build_opener, ProxyHandler

from openpyxl import load_workbook

def contrast(foreground, background):
    def light(color):
        rgb = [int(color[i:i+2], 16) / 255 for i in (0, 2, 4)]
        return sum(w * (c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4)
                   for w, c in zip((.2126, .7152, .0722), rgb))
    lo, hi = sorted(map(light, [foreground, background]))
    return (hi + .05) / (lo + .05)


def run(tree, folder):
    sys.path[:0] = [str(tree), str(tree / "tests")]
    from acceptance_118 import Browser, until
    os.environ.update(TEMP=str(folder), TMP=str(folder), LOCALAPPDATA=str(folder),
                      VARDIYA_PUANTAJ_DATA_DIR=str(folder / "data"), WEBVIEW2_USER_DATA_FOLDER=str(folder / "profile"),
                      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--disable-background-networking --disable-sync "
                      "--proxy-server=http://127.0.0.1:0 --proxy-bypass-list=127.0.0.1;localhost")
    import webview
    from server import create_server
    from storage import Store
    db = folder / "grid.sqlite3"
    month = date.today().strftime("%Y-%m")
    day = month + "-01"
    with Store(db) as store:
        person = store.add_person("Grid Fixture")["id"]
        store.save_schedule_month(month, [{"employee_id": person, "row_id": "proof1", "section": "MARKET",
                                          "slot": 1, "days": {day: {"kind": "work", "start": "09:00", "end": "17:00"}}}], "")
        for period in {month, "2026-03", "2026-05", "2026-10"}:
            store.save_attendance_month(period, {period + "-01": {str(person): "X"}}, {}, "",
                                        roster={"added": [person], "removed": [], "order": [person]})

    def records():
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as con:
            return [con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                    for t in ("employees", "schedule_months", "attendance_months")]

    before = records()
    server = create_server(db, tree / "web")
    server.easy_mode = True
    origin = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(ProxyHandler({}))
    window = webview.create_window("Grid acceptance", origin, width=1440, height=900, focus=False)
    failure, result = [], {}

    schedule_exports = 0

    def workbook(kind, period, week=0):
        nonlocal schedule_exports
        with client.open(origin + f"/api/export?kind={kind}&month={period}&week={week}", timeout=30) as response:
            book = load_workbook(BytesIO(response.read()))
        if kind == "schedule":
            schedule_exports += 1
        return book

    def scenario():
        page = Browser(window)
        assert window.events.loaded.wait(40)
        until("easy UI", lambda: page.js("document.body.classList.contains('easy-mode') && !document.querySelector('#app-loader')"))
        page.click('.main-nav [data-view="schedule"]')
        page.js("const e=document.querySelector('#schedule-week'); e.value='0'; e.dispatchEvent(new Event('change')); true")
        until("fixture week rendered", lambda: page.js("state.weekIndex === 0 && document.querySelector('#schedule-table select[data-row=\"proof1\"]')?.selectedOptions[0]?.textContent.trim() === 'Grid Fixture'"))
        if not page.js("document.querySelector('#schedule-view .easy-more')?.open"):
            page.click("#schedule-view .easy-more > summary")
        if page.js("!!document.querySelector('#tt-tip [data-tip=\"ok\"]')?.getClientRects().length"):
            page.click('#tt-tip [data-tip="ok"]')
        page.click("#schedule-view .format-panel > summary")
        tools = "#schedule-format-tools "
        page.click(tools + "[data-format-mode]")
        sig = "İSTASYON MÜDÜRÜ"

        def geometry(color=None):
            ui = page.js("""(() => {
              const cells=[...document.querySelectorAll('#schedule-table td')];
              return Object.fromEntries(['İSTASYON MÜDÜRÜ','Grid Fixture'].map(name => {
                const c=cells.find(e => e.textContent.trim()===name ||
                    e.querySelector('select[data-action="person"]')?.selectedOptions[0]?.textContent.trim()===name);
                return [name,c ? [+c.dataset.cellRow,+c.dataset.cellCol] : null];
              }));
            })()""")
            with closing(workbook("schedule", month)) as book:
                sheet = book.worksheets[0]
                dimensions = page.js("""(() => {const t=document.querySelector('#schedule-table');
                  const cells=[...t.querySelectorAll('tbody td:not(.ghost-cell)')];
                  return [Math.max(...cells.map(c=>+c.dataset.cellRow+c.rowSpan-1)),
                          Math.max(...cells.map(c=>+c.dataset.cellCol+c.colSpan-1))];})()""")
                assert dimensions == [sheet.max_row, sheet.max_column], (dimensions, sheet.max_row, sheet.max_column)
                for label, coords in ui.items():
                    assert coords and sheet.cell(*coords).value == label, (label, coords, sheet.title, sheet.max_row)
                cell = sheet.cell(*ui["Grid Fixture"])
                fill = (cell.fill.patternType, str(cell.fill.fgColor))
                if color:
                    assert cell.fill.fgColor.rgb[-6:] == color, (cell.coordinate, cell.fill)
                return [sheet.max_row, sheet.max_column, ui[sig], ui["Grid Fixture"], fill]

        original = geometry()
        assert original[:3] == [35, 22, [35, 14]], original
        selector = f'#schedule-table td[data-cell-row="{original[3][0]}"][data-cell-col="{original[3][1]}"]'
        assert page.js(f"(() => {{const c=document.querySelector({json.dumps(selector)}); c.focus(); return document.activeElement === c}})()")
        for event in ("keyDown", "keyUp"):
            page.cdp("Input.dispatchKeyEvent", type=event, key="Enter", code="Enter", windowsVirtualKeyCode=13)
        page.js("""const color=document.querySelector('#schedule-format-tools [data-format-color]');
            color.value='#b4d1e3';color.dispatchEvent(new Event('input',{bubbles:true})); true""")
        page.click(tools + '[data-format-action="color"]')
        undo = tools + '[data-format-history="undo"]'

        def ready(label):
            until(label, lambda: page.js(f"!state.formattingBusy && document.querySelector({json.dumps(undo)})?.getAttribute('aria-label') === {json.dumps('Geri al: ' + label)}"))

        ready("biçim değişikliği")
        styled = geometry("B4D1E3")
        page.js("window.__confirmCount=0;window.__accept=false;window.confirm=()=>{window.__confirmCount++;return window.__accept};true")
        page.click('#schedule-table th[data-format-row="34"]')
        delete_row = tools + '[data-format-delete="row"]'
        page.click(delete_row)
        assert page.js("window.__confirmCount") == 1 and geometry() == styled
        page.js("window.__accept=true")
        page.click(delete_row)
        ready("satır silme")
        assert geometry("B4D1E3")[:3] == [34, 22, [34, 14]]
        page.click('#schedule-table th[data-format-column="1"]')
        page.click(tools + '[data-format-delete="column"]')
        ready("sütun silme")
        assert geometry("B4D1E3")[:3] == [34, 21, [34, 13]]
        page.click(undo)
        ready("satır silme")
        assert geometry("B4D1E3")[:3] == [34, 22, [34, 14]]
        page.click(undo)
        ready("biçim değişikliği")
        assert geometry() == styled
        page.click(undo)
        until("format undo finished", lambda: page.js(f"!state.formattingBusy && document.querySelector({json.dumps(undo)})?.disabled"))
        assert geometry() == original
        page.click(tools + "[data-format-mode]")
        page.click('.main-nav [data-view="attendance"]')
        dates = {"2026-10": {1: "weekday", 3: "weekend", 28: "official", 29: "official"},
                 "2026-03": {19: "official", **dict.fromkeys(range(20, 23), "religious")},
                 "2026-05": {26: "official", **dict.fromkeys(range(27, 31), "religious")}}
        palette, contrasts = {}, []
        for period, expected in dates.items():
            page.js(f"""(() => {{const e=document.querySelector('#attendance-month'); e.value={json.dumps(period)};
              e.dispatchEvent(new Event('change')); return true}})()""")
            until(period, lambda: page.js(f"!!state.attendance?.calendar?.[{json.dumps(period + '-01')}] && document.querySelector('#attendance-table td[data-source-row=\"3\"][data-source-col=\"3\"]')?.title.includes('2026')"))
            with closing(workbook("attendance", period)) as book:
                for n, category in expected.items():
                    for row in (2, 3):
                        css = page.js(f"""(() => {{const e=document.querySelector('#attendance-table td[data-source-row="{row}"][data-source-col="{n+2}"]');
                          const s=getComputedStyle(e); return [s.color,s.backgroundColor,e.textContent.trim()];}})()""")
                        fg, bg = ["".join(f"{int(x):02X}" for x in re.fullmatch(r"rgb\((\d+), (\d+), (\d+)\)", v).groups()) for v in css[:2]]
                        fill = book.active.cell(row, n+2).fill.fgColor.rgb[-6:]
                        assert css[2] and (fill == bg == palette.setdefault(category, bg)), (period, n, row, css, fill)
                        ratio = contrast(fg, bg)
                        assert ratio >= 4.5, (period, n, row, css, ratio)
                        contrasts.append(ratio)
        assert len(set(palette.values())) == 4 and records() == before
        result.update(undo_steps=3, schedule_exports=schedule_exports, dates=sum(map(len, dates.values())), palette=palette,
                      minimum_ui_contrast=round(min(contrasts), 2), business_rows_unchanged=True)

    def drive():
        try:
            scenario()
        except Exception as exc:
            failure.append(exc)
        finally:
            window.destroy()

    webview.settings.update(ALLOW_FILE_URLS=False, OPEN_EXTERNAL_LINKS_IN_BROWSER=False)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        webview.start(drive, gui="edgechromium", private_mode=True, storage_path=str(folder / "profile"))
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        server.lock.release()
    if failure:
        raise failure[0]
    assert result
    return result


if __name__ == "__main__":
    tree = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="grid-proof-") as folder:
        checked = run(tree, Path(folder))
    print(json.dumps(checked))
