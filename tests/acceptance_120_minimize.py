"""WebView2 acceptance for interface items #1-16, #93-94, and #113-114."""
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
from acceptance_118 import Browser, until

NAME = "Acceptance 120"


def type_text(page, selector, text):
    page.click(selector)
    type_focused_text(page, selector, text)


def type_focused_text(page, selector, text):
    page.js(f"document.querySelector({json.dumps(selector)}).focus()")
    page.cdp("Input.dispatchKeyEvent", type="keyDown", key="Control", code="ControlLeft", modifiers=2)
    page.cdp("Input.dispatchKeyEvent", type="keyDown", key="a", code="KeyA", modifiers=2,
             windowsVirtualKeyCode=ord("A"))
    page.cdp("Input.dispatchKeyEvent", type="keyUp", key="a", code="KeyA", modifiers=2,
             windowsVirtualKeyCode=ord("A"))
    page.cdp("Input.dispatchKeyEvent", type="keyUp", key="Control", code="ControlLeft")
    page.cdp("Input.insertText", text=text)


def press_key(page, key, virtual_key):
    code = "Space" if key == " " else key
    page.cdp("Input.dispatchKeyEvent", type="keyDown", key=key, code=code,
             windowsVirtualKeyCode=virtual_key)
    page.cdp("Input.dispatchKeyEvent", type="keyUp", key=key, code=code,
             windowsVirtualKeyCode=virtual_key)


def audit_menu_keyboard(page):
    page.js("document.querySelector('#tools-menu-button').focus()")
    press_key(page, " ", 32)
    until("tools menu opened by keyboard", lambda: page.js(
        "!document.querySelector('#tools-menu').hidden"))
    groups = page.js("""[...document.querySelectorAll('#tools-menu .easy-tool-heading')]
      .map(node => ({id: node.id, label: node.textContent.trim()}))""")
    if not groups:
        raise AssertionError("Production tools menu has no command groups")
    visited = 0
    commands = 0
    exclusions = []
    for group in groups:
        for _ in range(len(groups) + 10):
            if page.js("document.activeElement?.id") == group["id"]:
                break
            press_key(page, "ArrowDown", 40)
        else:
            raise AssertionError(f"Menu group is not keyboard reachable: {group!r}")
        source = page.js(f"""(() => {{
          const group = document.getElementById({json.dumps(group["id"])}).closest('.easy-tool-group');
          return [...group.querySelectorAll('.easy-tool-items button')].map(button => ({{
            label: button.textContent.trim(), disabled: button.disabled, hidden: button.hidden,
            reason: button.title || button.getAttribute('aria-description') || '',
            native: button.tagName === 'BUTTON' && button.type === 'button',
            registered: button.dataset.tool !== undefined
              ? typeof EXTRAS_MENU[Number(button.dataset.tool)]?.[1] === 'function'
              : !!button.dataset.easyView && !!document.querySelector('.main-nav [data-view="' + button.dataset.easyView + '"]')
          }}));
        }})()""")
        for item in source:
            if item["disabled"] or item["hidden"]:
                exclusions.append({"group": group["label"], "label": item["label"],
                                   "reason": item["reason"] or (
                                       "DOM: hidden" if item["hidden"] else "DOM: disabled; no explanation")})
            elif not item["native"] or not item["registered"]:
                raise AssertionError(f"Enabled menu command is not a registered native button: {item!r}")
        press_key(page, "ArrowRight", 39)
        until(f"{group['label']} menu flyout opened", lambda: page.js(
            "!!document.querySelector('.easy-tool-flyout:not([hidden])')"))
        flyout = page.js("""[...document.querySelectorAll('.easy-tool-flyout:not([hidden]) button')]
          .filter(button => !button.disabled && !button.hidden)
          .map(button => ({label:button.textContent.trim(), native:button.type === 'button',
                           role:button.getAttribute('role')}))""")
        actual = [item["label"] for item in flyout if not item["label"].endswith(" sayfasını aç")]
        expected = [item["label"] for item in source if not item["disabled"] and not item["hidden"]]
        commands += len(expected)
        if actual != expected or any(not item["native"] or item["role"] != "menuitem" for item in flyout):
            raise AssertionError(f"Flyout commands do not match enabled production menu entries: {group!r}, {actual!r}, {expected!r}")
        for index, item in enumerate(flyout):
            current = page.js("document.activeElement?.textContent.trim()")
            if current != item["label"]:
                raise AssertionError(f"Keyboard focus skipped menu command {group['label']}/{item['label']}: {current!r}")
            visited += 1
            if index + 1 < len(flyout):
                press_key(page, "ArrowDown", 40)
        press_key(page, "Escape", 27)
        until(f"{group['label']} focus returned", lambda: page.js(
            f"document.activeElement?.id === {json.dumps(group['id'])}"))
    press_key(page, "Escape", 27)
    until("tools menu closed after keyboard audit", lambda: page.js(
        "document.querySelector('#tools-menu').hidden"))
    print(f"WebView: keyboard traversed {len(groups)} production menu groups, {commands} enabled commands "
          f"and {visited} total flyout controls; "
          f"excluded disabled/hidden commands: {json.dumps(exclusions, ensure_ascii=False)}.", flush=True)


def choose_option(page, selector, key):
    page.click(selector)
    if key.startswith("Arrow"):
        virtual_key = {"ArrowDown": 40, "ArrowUp": 38, "ArrowRight": 39, "ArrowLeft": 37}[key]
        page.cdp("Input.dispatchKeyEvent", type="keyDown", key=key, code=key, windowsVirtualKeyCode=virtual_key)
        page.cdp("Input.dispatchKeyEvent", type="keyUp", key=key, code=key, windowsVirtualKeyCode=virtual_key)
        page.cdp("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13)
        page.cdp("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)
    else:
        page.cdp("Input.dispatchKeyEvent", type="keyDown", key=key.lower(), code=f"Key{key.upper()}",
                 text=key.lower(), windowsVirtualKeyCode=ord(key.upper()))



        page.cdp("Input.dispatchKeyEvent", type="keyUp", key=key.lower(), code=f"Key{key.upper()}",
                 windowsVirtualKeyCode=ord(key.upper()))


def drag_select(page, start_selector, end_selector):
    expression = f"""(() => {{
      const selectors = [{json.dumps(start_selector)}, {json.dumps(end_selector)}];
      const points = selectors.map(selector => {{
        const el = document.querySelector(selector);
        if (!el || !el.getClientRects().length) return null;
        const r = el.getBoundingClientRect();
        return {{x:r.x+r.width/2, y:r.y+r.height/2}};
      }});
      return points.every(Boolean) ? points : null;
    }})()"""
    last = None

    def stable_points():
        nonlocal last
        points = page.js(expression)
        stable = points == last
        last = points
        return points if stable else None

    start, end = until("visible grid cells for drag selection", stable_points)
    page.cdp("Input.dispatchMouseEvent", type="mouseMoved", **start)
    page.cdp("Input.dispatchMouseEvent", type="mousePressed", button="left", buttons=1,
             clickCount=1, **start)
    page.cdp("Input.dispatchMouseEvent", type="mouseMoved", button="left", buttons=1, **end)
    page.cdp("Input.dispatchMouseEvent", type="mouseReleased", button="left", buttons=0,
             clickCount=1, **end)


def ensure_details_open(page, selector):
    if not page.js(f"!!document.querySelector({json.dumps(selector)})?.open"):
        page.click(f"{selector} > summary")


def run(folder):
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
    from main import DesktopAPI
    import schedule_protection
    from server import create_server
    from storage import Store

    db = folder / "synthetic.sqlite3"
    month = date.today().strftime("%Y-%m")
    day = month + "-01"
    with Store(db) as store:
        person = store.add_person(NAME)["id"]
        second_person = store.add_person("Acceptance 120 Alfa")["id"]
        leave_person = store.add_person("Acceptance leave fixture")["id"]
        other_person = store.add_person("Acceptance other section")["id"]
        second_day = month + "-02"
        revision = store.save_schedule_month(month, [
            {"row_id": "acceptance-person", "employee_id": person, "section": "MARKET", "slot": 2,
             "days": {day: {"kind": "work", "start": "09:00", "end": "17:00"}}},
            {"row_id": "acceptance-person-alfa", "employee_id": second_person, "section": "MARKET", "slot": 1,
             "days": {day: {"kind": "weekly_off"}}},
            {"row_id": "acceptance-person-leave", "employee_id": leave_person, "section": "MARKET", "slot": 3,
             "days": {day: {"kind": "annual_leave"}}},
        ], "")
        lock_request = {"month": month, "row_id": "acceptance-person",
                        "date": day, "revision": revision, "protected": True}
        protection_columns = {column["name"] for column in store.connection.execute(
            "PRAGMA table_info(schedule_protection)")}
        if "reason" in protection_columns:
            lock_request["reason"] = "Kabul testi için vardiya koruması"
        schedule_protection.set_lock(store, lock_request)
        store.save_attendance_month(month, {
            day: {str(person): "X"}, second_day: {str(person): "H"},
        }, {}, "",
                                    roster={"added": [person], "removed": [], "order": [person]})
        initial_quick_colors = store.workbook_settings().get("quick_colors", {})

    server = create_server(db, ROOT / "web")
    server.easy_mode = True
    origin = f"http://127.0.0.1:{server.server_port}"
    server_thread = threading.Thread(target=server.serve_forever)
    api = DesktopAPI(origin, server.internal_token)
    api._window = SimpleNamespace(create_file_dialog=lambda *args, **kwargs: None)
    server.save_excel = api.save_excel
    failures = []
    passed = threading.Event()
    format_copy_result = {}
    schedule_merge_expected = {}
    window = webview.create_window("Acceptance 120", origin, width=1440, height=900, focus=False)

    def drive():
        page = Browser(window)
        try:
            if not window.events.loaded.wait(40):
                raise TimeoutError("WebView2 did not load the local application")
            until("easy UI ready", lambda: page.js(
                "document.body.classList.contains('easy-mode') && !document.querySelector('#app-loader')"))
            page.js("""window.__acceptance120Errors = [];
              window.addEventListener('error', event => window.__acceptance120Errors.push(event.message));
              window.addEventListener('unhandledrejection', event =>
                window.__acceptance120Errors.push(String(event.reason)));""")
            audit_menu_keyboard(page)
            page.click("#tools-menu-button")
            type_text(page, "#easy-tools-search", "Sorun raporu")
            until("tool search location result", lambda: page.js("""(() => {
              const rows = [...document.querySelectorAll('.easy-search-result')];
              return rows.some(row => row.textContent.includes('Sorun raporu oluştur')
                && !!row.querySelector('[aria-label*="menüdeki yerini göster"]'));
            })()"""))
            page.click('.easy-search-result button[aria-label*="menüdeki yerini göster"]', "Yerini göster")
            until("tool location revealed without running it", lambda: page.js("""(() => {
              const active = document.activeElement;
              return !document.querySelector('#tools-menu').hidden
                && document.querySelector('#easy-tools-search').value === ''
                && !!active?.closest('.easy-tool-flyout')
                && active.textContent.includes('Sorun raporu oluştur')
                && !document.querySelector('#easy-document-workflows[open]')
                && !document.querySelector('#reports-dialog[open]');
            })()"""))
            print("WebView: tool search revealed the command inside its menu group without launching it.", flush=True)
            page.click("#tools-menu-button")
            page.click("#tools-menu-button")
            type_text(page, "#easy-tools-search", "Otomatik vardiya oluştur")
            until("rotation tool search result", lambda: page.js("""(() => {
              return [...document.querySelectorAll('.easy-search-result')].some(row =>
                row.textContent.includes('Otomatik vardiya oluştur')
                && !!row.querySelector('[aria-label*="menüdeki yerini göster"]'));
            })()"""))
            page.click('.easy-search-result button[aria-label*="menüdeki yerini göster"]', "Yerini göster")
            until("rotation tool keyboard target", lambda: page.js(
                "document.activeElement?.closest('.easy-tool-flyout')"
                " && document.activeElement.textContent.includes('Otomatik vardiya oluştur')"))
            press_key(page, " ", 32)
            until("rotation tool opened from keyboard", lambda: page.js(
                "!!document.querySelector('#rotation-dialog[open]')"
                " && !!document.activeElement?.closest('#rotation-dialog .extras-body')"))
            press_key(page, "Escape", 27)
            until("rotation tool returned focus to visible menu control", lambda: page.js(
                "!document.querySelector('#rotation-dialog')?.open"
                " && document.activeElement === document.querySelector('#tools-menu-button')"))
            print("WebView: asynchronous rotation tool opened by keyboard and returned focus to the visible tools menu control.", flush=True)
            page.click(".brand")
            until("shortcut ring opened", lambda: page.js(
                "!document.querySelector('#easy-ring').hidden"))
            original_ring = json.loads(page.js("""JSON.stringify(
              [...document.querySelectorAll('#easy-ring .easy-ring-item')].map(item => ({
                id:item.dataset.ringTarget,
                x:item.style.getPropertyValue('--x'),
                y:item.style.getPropertyValue('--y')
              })))"""))
            if len(original_ring) < 2:
                raise AssertionError(f"Shortcut ring fixture has too few items: {original_ring!r}")
            removed_id = original_ring[1]["id"]
            removed_position = (original_ring[1]["x"], original_ring[1]["y"])
            existing_ids = {item["id"] for item in original_ring}
            new_id = next(f"view:{view}" for view in
                json.loads(page.js("""JSON.stringify([...document.querySelectorAll('.nav-tab[data-view]')]
                  .map(tab => tab.dataset.view))"""))
                if f"view:{view}" not in existing_ids)
            page.click(".brand")
            page.click(".easy-header-options > summary")
            until("header options opened", lambda: page.js(
                "document.querySelector('.easy-header-options')?.open"))
            for _ in range(40):
                if page.js("document.activeElement?.id === 'easy-ring-settings'"):
                    break
                press_key(page, "Tab", 9)
            else:
                raise AssertionError("Shortcut-ring settings were not reachable through keyboard Tab navigation")
            press_key(page, " ", 32)
            until("shortcut ring editor opened", lambda: page.js(
                "!!document.querySelector('#easy-ring-dialog[open]')"))
            until("shortcut ring editor focused its first field", lambda: page.js(
                "!!document.activeElement?.closest('#easy-ring-dialog .extras-body')"))
            press_key(page, "Escape", 27)
            until("shortcut editor closed and focus restored", lambda: page.js(
                "!document.querySelector('#easy-ring-dialog')?.open"
                " && document.activeElement?.id === 'easy-ring-settings'"
                " && document.querySelector('.easy-header-options')?.open"))
            press_key(page, " ", 32)
            until("shortcut ring editor reopened from keyboard", lambda: page.js(
                "!!document.querySelector('#easy-ring-dialog[open]')"
                " && !!document.activeElement?.closest('#easy-ring-dialog .extras-body')"))
            scope = page.js("document.querySelector('#easy-ring-dialog [data-extras-scope]')?.textContent.trim() || ''")
            month_name = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
                          "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"][date.today().month - 1]
            if not all(part in scope for part in
                       ("Görünüm:", f"{month_name} {date.today().year}", "Tüm bölümler", "3 personel")):
                raise AssertionError(f"Opened tool did not identify its period and personnel scope: {scope!r}")
            print(f"WebView: opened tool showed scope {scope!r}.", flush=True)
            page.click(".easy-ring-order li:nth-child(2) .button.button-light", "Kaldır")
            page.click(f'.easy-ring-choices label[data-menu-id="{new_id}"]')
            page.click("#easy-ring-dialog .extras-actions .button-primary", "Kaydet")
            until("shortcut ring preferences saved", lambda: page.js(
                "!document.querySelector('#easy-ring-dialog')?.open"))
            page.click(".brand")
            until("updated shortcut ring opened", lambda: page.js(
                f"!!document.querySelector('#easy-ring [data-ring-target={json.dumps(new_id)}]')"))
            updated_ring = json.loads(page.js("""JSON.stringify(
              [...document.querySelectorAll('#easy-ring .easy-ring-item')].map(item => ({
                id:item.dataset.ringTarget,
                x:item.style.getPropertyValue('--x'),
                y:item.style.getPropertyValue('--y')
              })))"""))
            updated_positions = {item["id"]: (item["x"], item["y"]) for item in updated_ring}
            for item in original_ring:
                if item["id"] != removed_id and updated_positions.get(item["id"]) != (item["x"], item["y"]):
                    raise AssertionError(f"Removing one shortcut moved {item['id']!r} to {updated_positions.get(item['id'])!r}")
            if updated_positions.get(new_id) != removed_position:
                raise AssertionError(f"New shortcut did not fill the removed slot: {updated_positions.get(new_id)!r} != {removed_position!r}")
            retained_personnel = next((item["id"] for item in original_ring
                                       if item["id"].startswith("tool:Personel listesi") and item["id"] != removed_id), None)
            if not retained_personnel:
                raise AssertionError("The original shortcut ring did not include a retained personnel shortcut")
            page.click(f'#easy-ring [data-ring-target={json.dumps(retained_personnel)}]')
            until("retained ring shortcut opened its original personnel tool", lambda: page.js(
                "!!document.querySelector('#personnel-dialog[open]')"))
            press_key(page, "Escape", 27)
            until("personnel shortcut tool closed", lambda: page.js(
                "!document.querySelector('#personnel-dialog')?.open"))
            print("WebView: removing a ring icon kept other positions; an original-position shortcut opened its original personnel tool.", flush=True)
            for key, dialog_id in (("P", "personnel-dialog"), ("T", "shift-types-dialog")):
                page.js("document.querySelector('.brand').focus()")
                for event in ("keyDown", "keyUp"):
                    page.cdp("Input.dispatchKeyEvent", type=event, key=key, code=f"Key{key}",
                             modifiers=10, windowsVirtualKeyCode=ord(key))
                until(f"{dialog_id} keyboard focus", lambda: page.js(f"""(() => {{
                  const dialog = document.querySelector('#{dialog_id}[open]');
                  return !!dialog && !!document.activeElement?.closest('#{dialog_id} .extras-body');
                }})()"""))
                page.js("window.__acceptance120FocusBeforeTab = document.activeElement")
                press_key(page, "Tab", 9)
                until(f"{dialog_id} keyboard tab navigation", lambda: page.js(f"""(() => {{
                  return document.activeElement !== window.__acceptance120FocusBeforeTab
                    && !!document.activeElement?.closest('#{dialog_id}');
                }})()"""))
                press_key(page, "Escape", 27)
                until(f"{dialog_id} focus returned to opener", lambda: page.js(f"""(() => {{
                  return !document.querySelector('#{dialog_id}')?.open
                    && document.activeElement === document.querySelector('.brand');
                }})()"""))
            print("WebView: personnel and shift-type tools opened by keyboard, accepted Tab, and returned focus on Escape.", flush=True)
            page.cdp("Input.dispatchKeyEvent", type="keyDown", key="Q", code="KeyQ", modifiers=10,
                     windowsVirtualKeyCode=ord("Q"))
            page.cdp("Input.dispatchKeyEvent", type="keyUp", key="Q", code="KeyQ", modifiers=10,
                     windowsVirtualKeyCode=ord("Q"))
            until("quick-entry settings opened", lambda: page.js(
                "!!document.querySelector('#quick-entry-settings-dialog[open]')"))
            page.click('#quick-entry-color-list [data-preset-color-pick="work-0700"][data-color="#E5F1E7"]')
            until("quick-entry color preview updated", lambda: page.js("""(() => {
              const toolbar = document.querySelector('[data-preset-id="work-0700"]');
              const sample = document.querySelector('[data-color-preview="work-0700"]');
              return toolbar?.style.backgroundColor === 'rgb(229, 241, 231)'
                && sample?.style.backgroundColor === 'rgb(229, 241, 231)'
                && !document.querySelector('[data-save-quick-entry-colors]')?.disabled;
            })()"""))
            with Store(db) as store:
                if store.workbook_settings().get("quick_colors", {}) != initial_quick_colors:
                    raise AssertionError("Quick-entry color preview changed persisted settings before Save")
            page.click("[data-cancel-quick-entry-colors]")
            until("quick-entry color preview canceled", lambda: page.js("""(() =>
              document.querySelector('[data-preset-id="work-0700"]')?.style.backgroundColor !== 'rgb(229, 241, 231)'
            )()"""))
            with Store(db) as store:
                if store.workbook_settings().get("quick_colors", {}) != initial_quick_colors:
                    raise AssertionError("Cancel persisted an uncommitted quick-entry color")
            page.click("#quick-entry-color-list [data-preset-color-pick='work-0700'][data-color='#E5F1E7']")
            page.click("[data-save-quick-entry-colors]", "Renkleri kaydet")
            until("quick-entry color saved", lambda: page.js(
                "document.querySelector('#quick-entry-color-list [data-save-quick-entry-colors]')?.disabled"))
            with Store(db) as store:
                saved_color = store.workbook_settings().get("quick_colors", {}).get("work-0700", "").lower()
                if saved_color != "#e5f1e7":
                    raise AssertionError(f"Saving quick-entry color did not persist the selected color: {saved_color!r}")
            page.click("#quick-entry-color-list [data-preset-color-pick='work-1500'][data-color='#E5F1E7']")
            until("second quick-entry preview updated", lambda: page.js("""(() => {
              const toolbar = document.querySelector('[data-preset-id="work-1500"]');
              const sample = document.querySelector('[data-color-preview="work-1500"]');
              return toolbar?.style.backgroundColor === 'rgb(229, 241, 231)'
                && sample?.style.backgroundColor === 'rgb(229, 241, 231)';
            })()"""))
            page.click("[data-save-quick-entry-colors]", "Renkleri kaydet")
            until("second quick-entry color saved", lambda: page.js(
                "document.querySelector('#quick-entry-color-list [data-save-quick-entry-colors]')?.disabled"))
            with Store(db) as store:
                colors = store.workbook_settings().get("quick_colors", {})
                if any(colors.get(key, "").lower() != "#e5f1e7" for key in ("work-0700", "work-1500")):
                    raise AssertionError(f"Both quick-entry colors did not persist: {colors!r}")
            page.click('[data-close-dialog="quick-entry-settings-dialog"]')
            print("WebView: two quick-entry colors previewed and saved; Cancel discarded an unsaved color.", flush=True)
            page.cdp("Network.enable")
            page.cdp("Network.setBlockedURLs", urls=[f"*api/attendance?month={month}*"])
            page.click('.main-nav [data-view="attendance"]')
            until("attendance load retry action", lambda: page.js(
                "document.querySelector('#toast .toast-action')?.textContent === 'Yeniden dene'"))
            error_message = page.js("document.querySelector('#toast')?.textContent")
            if not error_message.startswith("Puantaj yüklenemedi."):
                raise AssertionError(f"Attendance load error was not distinguished: {error_message!r}")
            page.cdp("Network.setBlockedURLs", urls=[])
            page.click("#toast .toast-action")
            until("attendance loaded after retry", lambda: page.js(
                "document.querySelector('#attendance-view').classList.contains('active')"))
            until("attendance rows loaded after retry", lambda: page.js(
                f"!!document.querySelector('#attendance-table input[data-person=\"{person}\"][data-day=\"{day}\"]')"))
            print("WebView: failed attendance load exposed a retry action; retry loaded the page.", flush=True)
            page.click('.main-nav [data-view="schedule"]')
            ensure_details_open(page, "#schedule-view .easy-more")
            ensure_details_open(page, "#schedule-view .easy-filters")
            type_text(page, "#schedule-search", "No such acceptance person")
            until("filter-specific schedule empty state", lambda: page.js("""(() => {
              const empty = document.querySelector('#schedule-empty');
              return !empty.hidden && empty.textContent.includes('Bu ölçütlerle eşleşen vardiya satırı yok')
                && !!empty.querySelector('[data-empty-action="clear-schedule-filters"]');
            })()"""))
            if page.js("!!document.querySelector('[data-tip=\"ok\"]')?.getClientRects().length"):
                page.click('[data-tip="ok"]', "Anladım")
            page.js('document.querySelector("#schedule-empty [data-empty-action=\\"clear-schedule-filters\\"]").focus()')
            press_key(page, " ", 32)
            until("schedule filter empty state cleared", lambda: page.js(
                "document.querySelector('#schedule-empty').hidden"
                " && document.querySelectorAll('#schedule-table select[data-action=\"person\"]').length === 3"))
            print("WebView: filter-empty state differs from the load error and clears through its recovery button.", flush=True)
            labels = page.js(f"""(() => JSON.stringify([
              ...document.querySelectorAll('#schedule-table [data-shift-day="{day}"] .shift-slot-label')
            ].map(node => node.textContent.trim()).filter(Boolean)))()""")
            if not all(label in json.loads(labels) for label in ("HAFTA TATİLİ", "YILLIK İZİN")):
                raise AssertionError(f"Leave and weekly-rest labels were not distinct: {labels!r}")
            page.cdp("Emulation.setEmulatedMedia", media="print")
            printable_labels = page.js(f"""(() => JSON.stringify([
              ...document.querySelectorAll('#schedule-table [data-shift-day="{day}"] .shift-slot-label')
            ].map(node => node.textContent.trim()).filter(Boolean)))()""")
            page.cdp("Emulation.setEmulatedMedia", media="screen")
            if not all(label in json.loads(printable_labels) for label in ("HAFTA TATİLİ", "YILLIK İZİN")):
                raise AssertionError(f"Text labels disappeared in print media: {printable_labels!r}")
            print("WebView: leave and weekly-rest labels remain distinguishable in screen and print media.", flush=True)
            until("empty schedule slot available", lambda: page.js(
                "document.querySelectorAll('#schedule-table select[data-action=\"person-slot\"]').length > 0"))
            page.click(f'#schedule-table td[data-shift-day="{day}"]:has(.empty-row-hint) .quick-entry-open')
            until("inline prompt for an unassigned schedule row", lambda: page.js(f"""(() => {{
              const cell = document.querySelector('#schedule-table td[data-shift-day="{day}"]:has(.empty-row-hint)');
              return cell?.querySelector('.cell-action-notice[role="status"]')?.textContent === 'Önce personel seçin.'
                && !document.querySelector('#cell-quick-entry-dialog[open]');
            }})()"""))
            print("WebView: unassigned-cell guidance appears beside its action instead of only as a toast.", flush=True)
            page.js("""window.__acceptance120Access = {
              enabled: accessState.enabled, user: accessState.user, ready: accessState.ready
            };
            Object.assign(accessState, {
              enabled: true, user: {role: 'operator', permissions: []}, ready: true
            });""")
            page.click(f'#schedule-table td[data-shift-row="acceptance-person"][data-shift-day="{day}"] .quick-entry-open')
            until("inline permission guidance beside blocked quick entry", lambda: page.js(f"""(() => {{
              const cell = document.querySelector('#schedule-table td[data-shift-row="acceptance-person"][data-shift-day="{day}"]');
              return cell?.querySelector('.cell-action-notice[role="status"]')?.textContent === 'Bu işlem için yetkiniz yok.'
                && !document.querySelector('#cell-quick-entry-dialog[open]');
            }})()"""))
            page.js("""Object.assign(accessState, window.__acceptance120Access); applyAccessPermissions();""")
            print("WebView: permission denial was announced beside the blocked schedule action.", flush=True)
            page.js(f"""window.__acceptance120LockResult = null;
              fetch('/api/archive/period-lock', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{month: {json.dumps(month)}, locked: true}})
              }}).then(response => response.json()).then(value => window.__acceptance120LockResult = value);""")
            lock_result = until("test period locked through API", lambda: (
                page.js("window.__acceptance120LockResult") or False))
            if not lock_result:
                raise AssertionError("Isolated test period did not return its persisted lock result")
            page.click('.main-nav [data-view="attendance"]')
            page.click('.main-nav [data-view="schedule"]')
            until("locked schedule status refreshed", lambda: page.js(
                "document.querySelector('#easy-schedule-record-state')?.textContent.includes('Dönem kilitli')"))
            page.click(f'#schedule-table td[data-shift-row="acceptance-person"][data-shift-day="{day}"] .quick-entry-open')
            until("inline lock guidance beside quick entry", lambda: page.js(f"""(() => {{
              const cell = document.querySelector('#schedule-table td[data-shift-row="acceptance-person"][data-shift-day="{day}"]');
              return cell?.querySelector('.cell-action-notice[role="status"]')?.textContent === 'Dönem kilitli.'
                && !document.querySelector('#cell-quick-entry-dialog[open]');
            }})()"""))
            print("WebView: a locked-period message appeared beside the blocked cell action.", flush=True)
            page.js(f"""window.__acceptance120UnlockResult = null;
              fetch('/api/archive/period-lock', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{month: {json.dumps(month)}, locked: false, reason: 'acceptance cleanup'}})
              }}).then(response => response.json()).then(value => window.__acceptance120UnlockResult = value);""")
            unlock_result = until("test period unlocked through API", lambda: (
                page.js("window.__acceptance120UnlockResult") or False))
            if not unlock_result:
                raise AssertionError("Isolated test period did not return its unlock result")
            page.click('.main-nav [data-view="attendance"]')
            page.click('.main-nav [data-view="schedule"]')
            until("unlocked schedule status refreshed", lambda: not page.js(
                "document.querySelector('#easy-schedule-record-state')?.textContent.includes('Dönem kilitli')"))
            page.click('.main-nav [data-view="attendance"]')
            until("attendance table available for selection", lambda: page.js(
                f"!!document.querySelector('#attendance-table input[data-person=\"{person}\"][data-day=\"{day}\"]')"))
            page.click(f'#attendance-table input[data-person="{person}"][data-day="{day}"]')
            until("selected attendance cell", lambda: page.js(
                "document.querySelectorAll('#attendance-table td.grid-selected').length === 1"))
            page.click('.main-nav [data-view="schedule"]')
            page.click('.main-nav [data-view="attendance"]')
            until("attendance selection restored", lambda: page.js(
                "document.querySelectorAll('#attendance-table td.grid-selected').length === 1"))
            selected = page.js(f"""(() => {{
              const cell = document.querySelector('#attendance-table td.grid-selected input');
              return cell?.dataset.person === "{person}" && cell?.dataset.day === "{day}";
            }})()""")
            if not selected:
                raise AssertionError("The selected attendance person/day did not remain selected")
            print(f"WebView: selected person/day {NAME}/{day} restored after switching tables.", flush=True)
            third_day = month + "-03"
            with Store(db) as store:
                before_bulk_codes = store.attendance_month(month)["overrides"]
            page.cdp("Emulation.setDeviceMetricsOverride", width=720, height=900,
                     deviceScaleFactor=1, mobile=False)
            drag_select(page, f'#attendance-table input[data-person="{person}"][data-day="{day}"]',
                        f'#attendance-table input[data-person="{person}"][data-day="{third_day}"]')
            selection_state = page.js("""(() => JSON.stringify({
              cells: [...document.querySelectorAll('#attendance-table td.grid-selected input')]
                .map(input => `${input.dataset.person}/${input.dataset.day}`),
              summary: document.querySelector('#grid-selection-bar .grid-selection-summary')?.textContent || ''
            }))()""")
            selection_state = json.loads(selection_state)
            expected_selection_summary = "1 personel · 3 gün · Boş hücre: 1 · Korunan hücre: 0"
            if len(selection_state["cells"]) != 3 or selection_state["summary"] != expected_selection_summary \
                    or "saat" in selection_state["summary"].casefold():
                raise AssertionError(f"Mouse selection did not count attendance cells accurately: {selection_state!r}")
            page.cdp("Emulation.setTouchEmulationEnabled", enabled=True, maxTouchPoints=1)
            touch_before = page.js("""(() => {
              const frame = document.querySelector('#attendance-table').parentElement;
              const rect = frame.getBoundingClientRect();
              const left = Math.max(0, rect.left), right = Math.min(innerWidth, rect.right);
              const top = Math.max(0, rect.top), bottom = Math.min(innerHeight, rect.bottom);
              return {left:frame.scrollLeft, top:frame.scrollTop, page:document.scrollingElement.scrollTop,
                x:Math.min(right - 25, left + 350), y:(top + bottom) / 2,
                rect:rect.toJSON(), maxLeft:frame.scrollWidth-frame.clientWidth,
                selected:[...document.querySelectorAll('#attendance-table td.grid-selected input')]
                  .map(input => `${input.dataset.person}/${input.dataset.day}`),
                assistant:easyAssistant.selectedCells().map(cell => `${cell.employee_id}/${cell.date}`).sort()};
            })()""")
            page.cdp("Input.dispatchTouchEvent", type="touchStart",
                     touchPoints=[{"x": touch_before["x"], "y": touch_before["y"]}])
            for offset in range(25, 201, 25):
                page.cdp("Input.dispatchTouchEvent", type="touchMove",
                         touchPoints=[{"x": touch_before["x"] - offset, "y": touch_before["y"]}])
                time.sleep(0.03)
            page.cdp("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
            try:
                until("touch drag scrolls without altering cell selection", lambda: page.js(f"""(() => {{
                  const frame = document.querySelector('#attendance-table').parentElement;
                  const selected = [...document.querySelectorAll('#attendance-table td.grid-selected input')]
                    .map(input => `${{input.dataset.person}}/${{input.dataset.day}}`);
                  const assistant = easyAssistant.selectedCells().map(cell => `${{cell.employee_id}}/${{cell.date}}`).sort();
                  return (frame.scrollLeft > {touch_before["left"]} || frame.scrollTop > {touch_before["top"]}
                    || document.scrollingElement.scrollTop > {touch_before["page"]})
                    && selected.join('|') === {json.dumps("|".join(touch_before["selected"]))}
                    && assistant.join('|') === {json.dumps("|".join(touch_before["assistant"]))};
                }})()"""))
            except AssertionError as error:
                touch_after = page.js("""(() => {
                  const frame = document.querySelector('#attendance-table').parentElement;
                  return {left:frame.scrollLeft, top:frame.scrollTop, page:document.scrollingElement.scrollTop,
                    selected:[...document.querySelectorAll('#attendance-table td.grid-selected input')]
                      .map(input => `${input.dataset.person}/${input.dataset.day}`),
                    assistant:easyAssistant.selectedCells().map(cell => `${cell.employee_id}/${cell.date}`).sort()};
                })()""")
                raise AssertionError(f"{error}; before={touch_before!r}; after={touch_after!r}") from error
            stable_scroll = []
            def touch_scroll_settled():
                position = page.js("document.querySelector('#attendance-table').parentElement.scrollLeft")
                stable_scroll.append(position)
                return len(stable_scroll) >= 4 and len(set(stable_scroll[-4:])) == 1
            until("touch momentum settles before tap", touch_scroll_settled)
            touch_target = page.js("""(() => {
              const frame = document.querySelector('#attendance-table').parentElement.getBoundingClientRect();
              for (const input of document.querySelectorAll('#attendance-table input[data-person][data-day]')) {
                const r = input.getBoundingClientRect(), x = r.x + r.width / 2, y = r.y + r.height / 2;
                const hit = document.elementFromPoint(x, y);
                if (!input.closest('td').classList.contains('grid-selected')
                    && x > frame.left && x < frame.right && y > frame.top && y < Math.min(frame.bottom, innerHeight)
                    && (hit === input || hit?.closest('td') === input.closest('td'))) {
                  return {x, y, person: input.dataset.person, day: input.dataset.day,
                    left:document.querySelector('#attendance-table').parentElement.scrollLeft};
                }
              }
              return null;
            })()""")
            if not touch_target:
                raise AssertionError("No unobscured unselected attendance cell is available for touch selection")
            page.cdp("Input.dispatchTouchEvent", type="touchStart",
                     touchPoints=[{"x": touch_target["x"], "y": touch_target["y"]}])
            page.cdp("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
            try:
                until("touch tap selects its cell without scrolling", lambda: page.js(f"""(() => {{
                  const inputs = [...document.querySelectorAll('#attendance-table td.grid-selected input')];
                  return inputs.length === 1 && inputs[0].dataset.person === {json.dumps(touch_target["person"])}
                    && inputs[0].dataset.day === {json.dumps(touch_target["day"])}
                    && document.querySelector('#attendance-table').parentElement.scrollLeft === {touch_target["left"]};
                }})()"""))
            except AssertionError as error:
                touch_after = page.js("""(() => ({
                  selected:[...document.querySelectorAll('#attendance-table td.grid-selected input')]
                    .map(input => `${input.dataset.person}/${input.dataset.day}`),
                  assistant:easyAssistant.selectedCells().map(cell => `${cell.employee_id}/${cell.date}`),
                  focus:document.activeElement?.outerHTML.slice(0,200),
                  left:document.querySelector('#attendance-table').parentElement.scrollLeft
                }))()""")
                raise AssertionError(f"{error}; target={touch_target!r}; after={touch_after!r}") from error
            page.cdp("Emulation.setTouchEmulationEnabled", enabled=False)
            page.js(f"document.querySelector('#attendance-table').parentElement.scrollLeft = {touch_before['left']}")
            drag_select(page, f'#attendance-table input[data-person="{person}"][data-day="{day}"]',
                        f'#attendance-table input[data-person="{person}"][data-day="{third_day}"]')
            until("three-cell selection restored after touch test", lambda: page.js(
                "document.querySelectorAll('#attendance-table td.grid-selected').length === 3"))
            print("WebView: CDP touch drag scrolled without changing selection; a separate touch tap selected its cell without scrolling.", flush=True)
            scroll_before = page.js("""(() => {
              const frame = document.querySelector('#attendance-table').parentElement;
              return {left: frame.scrollLeft, top: frame.scrollTop, page: document.scrollingElement.scrollTop,
                rect: frame.getBoundingClientRect().toJSON()};
            })()""")
            page.cdp("Input.dispatchMouseEvent", type="mouseWheel",
                     x=scroll_before["rect"]["x"] + scroll_before["rect"]["width"] / 2,
                     y=scroll_before["rect"]["y"] + scroll_before["rect"]["height"] / 2,
                     deltaX=240, deltaY=180)
            try:
                until("attendance table scrolled without changing its cell selection", lambda: page.js(f"""(() => {{
                  const frame = document.querySelector('#attendance-table').parentElement;
                  const cells = [...document.querySelectorAll('#attendance-table td.grid-selected input')]
                    .map(input => `${{input.dataset.person}}/${{input.dataset.day}}`).join('|');
                  return (frame.scrollLeft > {scroll_before["left"]} || frame.scrollTop > {scroll_before["top"]}
                    || document.scrollingElement.scrollTop > {scroll_before["page"]})
                    && cells === {json.dumps("|".join(selection_state["cells"]))};
                }})()"""))
            except AssertionError as error:
                metrics = page.js("""(() => {
                  const frame = document.querySelector('#attendance-table').parentElement;
                  const rect = frame.getBoundingClientRect();
                  return {frame:{left:frame.scrollLeft, top:frame.scrollTop, maxLeft:frame.scrollWidth-frame.clientWidth,
                      maxTop:frame.scrollHeight-frame.clientHeight, rect:rect.toJSON()},
                    page:{top:document.scrollingElement.scrollTop,
                      max:document.scrollingElement.scrollHeight-document.scrollingElement.clientHeight},
                    selected:[...document.querySelectorAll('#attendance-table td.grid-selected input')]
                      .map(input => `${input.dataset.person}/${input.dataset.day}`)};
                })()""")
                raise AssertionError(f"{error}; before={scroll_before!r}; metrics={metrics!r}") from error
            if page.js("!!document.querySelector('#grid-bulk-preview-dialog[open]')"):
                raise AssertionError("Bulk preview opened before the explicit apply action")
            page.click('#grid-selection-bar [data-grid-apply="R"]')
            until("bulk operation preview", lambda: page.js("""(() => {
              const dialog = document.querySelector('#grid-bulk-preview-dialog[open]');
              const summary = dialog?.querySelector('.grid-bulk-summary')?.textContent || '';
              const rows = dialog?.querySelectorAll('tbody tr').length || 0;
              const highlighted = document.querySelectorAll('#attendance-table .bulk-preview-target').length;
              return !!dialog && summary.includes('1 personel') && summary.includes('3 gün')
                && summary.includes('1 boş hücre') && rows === 3 && highlighted === 3;
            })()"""))
            enlarged_preview = page.js("""(() => {
              const table = document.querySelector('#grid-bulk-preview-dialog .grid-bulk-preview-table');
              const cell = table?.querySelector('tbody td');
              return table && cell ? {
                rows: table.querySelectorAll('tbody tr').length,
                minCellWidth: cell.getBoundingClientRect().width,
                padding: getComputedStyle(cell).paddingTop,
                summary: document.querySelector('#grid-bulk-preview-dialog .grid-bulk-summary')?.textContent,
              } : null;
            })()""")
            if not enlarged_preview or enlarged_preview["rows"] != 3 \
                    or enlarged_preview["minCellWidth"] < 90 \
                    or enlarged_preview["padding"] != "9px":
                raise AssertionError(f"Bulk preview did not present a readable enlarged summary: {enlarged_preview!r}")
            with Store(db) as store:
                if store.attendance_month(month)["overrides"] != before_bulk_codes:
                    raise AssertionError("Previewing a bulk attendance change mutated the database")
            page.click("#grid-bulk-preview-dialog .extras-actions button", "Vazgeç")
            until("bulk operation preview closed", lambda: page.js(
                "!document.querySelector('#grid-bulk-preview-dialog')?.open"
                " && document.querySelectorAll('#attendance-table .bulk-preview-target').length === 0"))
            with Store(db) as store:
                if store.attendance_month(month)["overrides"] != before_bulk_codes:
                    raise AssertionError("Canceling the bulk preview mutated attendance codes")
            print("WebView: selected X, H and blank codes were counted as 1 person, 3 days, 1 blank and 0 protected; preview cancellation left saved codes unchanged.", flush=True)
            page.click("#grid-selection-bar [data-grid-close]")
            until("attendance selection cleared", lambda: page.js(
                "!document.querySelector('#grid-selection-bar')"))
            page.cdp("Emulation.clearDeviceMetricsOverride")
            page.click(f'#attendance-table input[data-person="{person}"][data-day="{day}"]')
            until("original attendance cell selected again", lambda: page.js(
                "document.querySelectorAll('#attendance-table td.grid-selected').length === 1"))

            page.click('.main-nav [data-view="schedule"]')
            ensure_details_open(page, "#schedule-view .easy-more")
            ensure_details_open(page, "#schedule-view .easy-filters")
            type_text(page, "#schedule-search", "Acceptance")
            choose_option(page, "#schedule-section-filter", "M")
            choose_option(page, "#schedule-sort", "ArrowDown")
            expected_order = f"{person},{second_person},{leave_person}"
            until("schedule filters and sort applied", lambda: page.js(f"""(() =>
              document.querySelector('#schedule-search').value === 'Acceptance'
              && document.querySelector('#schedule-section-filter').value === 'MARKET'
              && document.querySelector('#schedule-sort').value === 'name'
              && [...document.querySelectorAll('#schedule-table select[data-action="person"]')]
                .map(item => Number(item.value)).filter(Boolean).join(',') === '{expected_order}')()"""))
            page.click('.main-nav [data-view="attendance"]')
            until("combined attendance selection restored", lambda: page.js(
                "document.querySelectorAll('#attendance-table td.grid-selected').length === 1"))
            selected = page.js(f"""(() => {{
              const cell = document.querySelector('#attendance-table td.grid-selected input');
              return cell?.dataset.person === "{person}" && cell?.dataset.day === "{day}";
            }})()""")
            if not selected:
                raise AssertionError("The selected attendance person/day was lost alongside schedule filters")
            page.click('.main-nav [data-view="schedule"]')
            until("schedule filter, sort, section and selection restored", lambda: page.js(f"""(() => {{
              const details = document.querySelector('#schedule-view .easy-filters');
              const more = document.querySelector('#schedule-view .easy-more');
              const employees = [...document.querySelectorAll('#schedule-table select[data-action="person"]')]
                .map(item => Number(item.value)).filter(Boolean);
              return document.querySelector('#schedule-search').value === 'Acceptance'
                && document.querySelector('#schedule-section-filter').value === 'MARKET'
                && document.querySelector('#schedule-sort').value === 'name'
                && details?.open && more?.open
                && employees.join(',') === '{expected_order}';
            }})()"""))
            print("WebView: schedule query, section, A-Z order, open filter section and selected person/day restored.", flush=True)
            ensure_details_open(page, "#schedule-comparison-panel")
            until("schedule comparison choices populated", lambda: page.js(
                  "document.querySelectorAll('#schedule-compare-first option').length === 4"))
            choose_option(page, "#schedule-compare-second", "ArrowDown")
            compare_choices = page.js("""(() => {
                const first = document.querySelector('#schedule-compare-first').value;
                const second = document.querySelector('#schedule-compare-second').value;
                const rows = [...document.querySelectorAll('#schedule-table select[data-action="person"]')];
                return {
                  first, second,
                  firstSlot: state.schedule.entries.find(entry => entry.row_id === first)?.slot,
                  secondSlot: state.schedule.entries.find(entry => entry.row_id === second)?.slot,
                  mainOrder: rows.map(select => select.dataset.row),
                  rowDistance: Math.abs(rows.findIndex(select => select.dataset.row === first)
                    - rows.findIndex(select => select.dataset.row === second)),
                };
            })()""")
            if (compare_choices["firstSlot"], compare_choices["secondSlot"]) != (1, 3):
                  raise AssertionError(f"Comparison did not use distant assigned rows: {compare_choices!r}")
            page.click("#schedule-compare-run")
            until("two aligned comparison tables rendered", lambda: page.js("""(() => {
                const tables = [...document.querySelectorAll('#schedule-comparison-tables .schedule-comparison-table')];
                return tables.length === 2 && tables.every(table => table.querySelectorAll('tbody tr[data-day]').length === 7);
            })()"""))
            compare_state = page.js("""(() => {
                const wrappers = [...document.querySelectorAll('#schedule-comparison-tables .schedule-comparison-table-wrap')];
                const days = wrappers.map(wrapper => [...wrapper.querySelectorAll('tbody tr[data-day]')]
                  .map(row => row.dataset.day));
                return {
                  rows: wrappers.map(wrapper => wrapper.dataset.comparisonRowId),
                  people: wrappers.map(wrapper => wrapper.dataset.comparisonPersonId),
                  values: wrappers.map(wrapper => wrapper.querySelector('tr[data-day="{day}"] td')?.textContent.trim()),
                  days, mainOrder: [...document.querySelectorAll('#schedule-table select[data-action="person"]')]
                    .map(select => select.dataset.row),
                  scroll: [...document.querySelectorAll('#schedule-comparison-tables .schedule-comparison-scroll')]
                    .map(element => { const r = element.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; }),
                };
            })()""".replace("{day}", day))
            if compare_state["rows"] != [compare_choices["first"], compare_choices["second"]] \
                      or compare_state["people"] != [str(second_person), str(leave_person)] \
                      or compare_state["values"] != ["HAFTA TATİLİ", "YILLIK İZİN"] \
                      or compare_state["days"][0] != compare_state["days"][1] \
                      or compare_state["mainOrder"] != compare_choices["mainOrder"]:
                  raise AssertionError(f"Comparison misaligned real rows/dates or reordered the schedule: {compare_state!r}")
            page.js("document.querySelector('#schedule-comparison-tables .schedule-comparison-scroll').focus()")
            press_key(page, "PageDown", 34)
            try:
                until("paired comparison tables scrolled together", lambda: page.js("""(() => {
                    const [first, second] = [...document.querySelectorAll('#schedule-comparison-tables .schedule-comparison-scroll')];
                    return first?.scrollTop > 0 && Math.abs(first.scrollTop - second.scrollTop) < 1;
                })()"""))
            except AssertionError as error:
                metrics = page.js("""(() => [...document.querySelectorAll('#schedule-comparison-tables .schedule-comparison-scroll')]
                  .map(element => {
                    const rect = element.getBoundingClientRect();
                    return {top:element.scrollTop, height:element.clientHeight, content:element.scrollHeight,
                      rect:{x:rect.x,y:rect.y,width:rect.width,height:rect.height}};
                  }))()""")
                raise AssertionError(f"{error}; comparison scroll metrics: {metrics!r}") from error
            page.js("document.querySelectorAll('#schedule-comparison-tables .schedule-comparison-scroll')[1].focus()")
            press_key(page, "Home", 36)
            until("reverse paired comparison scroll", lambda: page.js("""(() => {
              const [first, second] = document.querySelectorAll('#schedule-comparison-tables .schedule-comparison-scroll');
              return second?.scrollTop === 0 && first?.scrollTop === 0;
            })()"""))
            if page.js("""[...document.querySelectorAll('#schedule-table select[data-action="person"]')]
                .map(select => select.dataset.row).join('|')""") != "|".join(compare_choices["mainOrder"]):
                  raise AssertionError("Comparing or scrolling changed the main schedule order")
            print("WebView: distant schedule rows were compared by record ID; both date-aligned tables scrolled together without reordering.", flush=True)
            page.click("#schedule-comparison-panel > summary")
            drag_select(page, f'#schedule-table td[data-shift-row="acceptance-person-alfa"][data-shift-day="{day}"]',
                        f'#schedule-table td[data-shift-row="acceptance-person"][data-shift-day="{day}"]')
            until("schedule selection loads nonzero protected count", lambda: page.js("""(() => {
              const summary = document.querySelector('#grid-selection-bar .grid-selection-summary')?.textContent || '';
              return summary.includes('2 personel · 1 gün') && summary.includes('Korunan hücre: 1');
            })()"""))
            page.click("#grid-selection-bar [data-grid-close]")
            with Store(db) as store:
                schedule_protection.set_lock(store, {"month": month, "row_id": "acceptance-person",
                    "date": day, "revision": store.schedule_month(month)["revision"], "protected": False})
            page.js("grid.protectionMonth = ''; grid.protectedCells.clear(); grid.protectionState = 'unknown';")
            print("WebView: two selected schedule people included one genuinely protected cell.", flush=True)
            page.click('.main-nav [data-view="attendance"]')

            page.click("#tools-menu-button")
            page.click("#tools-menu .easy-tool-heading", "Tablo ve görünüm")
            page.click(".easy-tool-flyout button", "Toplu giriş çalışma alanı")
            page.choose_code("R")
            preserved = page.js("""(() => {
              const dialog = document.querySelector('#easy-bulk-editor');
              return dialog?.open && document.querySelector('#easy-bulk-code')?.value === 'R';
            })()""")
            if not preserved:
                raise AssertionError("The live tool did not contain the entered code before minimizing")
            page.click("#easy-bulk-editor [data-extras-minimize]")
            minimized = page.js("""(() => {
              const dialog = document.querySelector('#easy-bulk-editor');
              const table = document.querySelector('#attendance-table');
              return dialog?.open && dialog.classList.contains('extras-dialog-minimized')
                && dialog.querySelector('#easy-bulk-code').value === 'R'
                && table.getClientRects().length > 0
                && dialog.querySelector('.extras-body').hidden;
            })()""")
            if not minimized:
                raise AssertionError("Minimized tool hid the table or lost its in-progress selection")
            page.click("#easy-bulk-editor [data-extras-restore]")
            restored = page.js("""(() => {
              const dialog = document.querySelector('#easy-bulk-editor');
              return dialog?.open && !dialog.classList.contains('extras-dialog-minimized')
                && document.querySelector('#easy-bulk-code').value === 'R'
                && document.activeElement?.id === 'easy-bulk-code';
            })()""")
            if not restored:
                raise AssertionError("Restored tool did not preserve the input and return focus")
            print(f"WebView: attendance cell {NAME}/{day} remained visible; tool input R and focus restored.", flush=True)
            page.click("#easy-bulk-editor button", "Önizlemeyi hazırla")
            page.click("#easy-bulk-preview button", "Onayla ve taslağa uygula")
            expected = "Geri al: 1 hücrenin puantaj kodu değişikliği"
            until("descriptive Undo action", lambda: page.js(
                "document.querySelector('#attendance-history-action')?.textContent") == expected)
            description = page.js("""({
              visible: document.querySelector('#attendance-history-action')?.textContent,
              title: document.querySelector('#undo-attendance')?.title,
              label: document.querySelector('#undo-attendance')?.getAttribute('aria-label'),
            })""")
            if description != {"visible": expected, "title": expected, "label": expected}:
                raise AssertionError(f"Undo does not describe the actual cell operation: {description!r}")
            page.click("#undo-attendance")
            restored_code = page.js(f"""document.querySelector(
              '#attendance-table input[data-person="{person}"][data-day="{day}"]').value""")
            if restored_code != "X":
                raise AssertionError(f"Described Undo did not restore the attendance code: {restored_code!r}")
            for event in ("keyDown", "keyUp"):
                page.cdp("Input.dispatchKeyEvent", type=event, key="Escape", code="Escape",
                         windowsVirtualKeyCode=27)
            until("bulk-entry dialog closed", lambda: page.js(
                "!document.querySelector('#easy-bulk-editor')?.open"))

            page.click('.main-nav [data-view="schedule"]')
            start = f'#schedule-table input[data-action="shift-time"][data-row="acceptance-person"][data-day="{day}"][data-field="start"]'
            if page.js("document.querySelectorAll('#schedule-table td.easy-bulk-selected').length"):
                page.click("#easy-schedule-actions button", "Seçimi temizle")
            if page.js('document.querySelector("#easy-schedule-actions [data-schedule-select]")?.getAttribute("aria-pressed") === "true"'):
                page.click("#easy-schedule-actions [data-schedule-select]")
            page.click("#easy-schedule-actions [data-schedule-select]")
            until("schedule multi-selection enabled", lambda: page.js(
                'document.querySelector("#easy-schedule-actions [data-schedule-select]")?.getAttribute("aria-pressed") === "true"'))
            page.click(f'#schedule-table td[data-shift-row="acceptance-person"][data-shift-day="{day}"]')
            until("first schedule cell selected", lambda: page.js(
                f'document.querySelector("#schedule-table td[data-shift-row=\\"acceptance-person\\"][data-shift-day=\\"{day}\\"]")?.classList.contains("easy-bulk-selected")'))
            page.click(f'#schedule-table td[data-shift-row="acceptance-person-alfa"][data-shift-day="{day}"]')
            until("two schedule cells selected before single-cell edit", lambda: page.js(
                "document.querySelectorAll('#schedule-table td.easy-bulk-selected').length === 2"))
            page.js(f"""window.__acceptance120RenderProbe = {{
              table: document.querySelector('#schedule-table'),
              untouched: document.querySelector('#schedule-table td[data-shift-row="acceptance-person-leave"][data-shift-day="{day}"]'),
              selected: [...document.querySelectorAll('#schedule-table td.easy-bulk-selected')]
                .map(cell => `${{cell.dataset.shiftRow}}/${{cell.dataset.shiftDay}}`).sort(),
              merges: [...document.querySelectorAll('#schedule-table td[colspan], #schedule-table td[rowspan]')]
                .filter(cell => cell.colSpan > 1 || cell.rowSpan > 1)
                .map(cell => ({{node: cell, colSpan: cell.colSpan, rowSpan: cell.rowSpan,
                  width: cell.offsetWidth, height: cell.offsetHeight}})),
              scrollLeft: document.querySelector('#schedule-table').parentElement.scrollLeft,
              scrollTop: document.querySelector('#schedule-table').parentElement.scrollTop,
            }};""")
            type_focused_text(page, start, "10:00")
            page.cdp("Input.dispatchKeyEvent", type="keyDown", key="Tab", code="Tab", windowsVirtualKeyCode=9)
            page.cdp("Input.dispatchKeyEvent", type="keyUp", key="Tab", code="Tab", windowsVirtualKeyCode=9)
            until("single-cell time edit recorded", lambda: page.js(
                f"state.scheduleDirty && document.querySelector({json.dumps(start)})?.value === '10:00'"))
            render_probe = page.js("""(() => {
              const frame = document.querySelector('#schedule-table').parentElement;
              return {
                sameTable: document.querySelector('#schedule-table') === window.__acceptance120RenderProbe.table,
                sameUntouched: document.querySelector('#schedule-table td[data-shift-row="acceptance-person-leave"]'
                  + '[data-shift-day="' + window.__acceptance120RenderProbe.selected[0].split('/')[1] + '"]')
                  === window.__acceptance120RenderProbe.untouched,
                selected: [...document.querySelectorAll('#schedule-table td.easy-bulk-selected')]
                  .map(cell => `${cell.dataset.shiftRow}/${cell.dataset.shiftDay}`).sort(),
                originalSelected: window.__acceptance120RenderProbe.selected,
                mergedCount: window.__acceptance120RenderProbe.merges.length,
                mergesPreserved: window.__acceptance120RenderProbe.merges.every(item =>
                  item.node.isConnected && item.node.colSpan === item.colSpan
                  && item.node.rowSpan === item.rowSpan && item.node.offsetWidth === item.width
                  && item.node.offsetHeight === item.height),
                scrollLeft: frame.scrollLeft, scrollTop: frame.scrollTop,
                originalScrollLeft: window.__acceptance120RenderProbe.scrollLeft,
                originalScrollTop: window.__acceptance120RenderProbe.scrollTop,
              };
            })()""")
            if not render_probe["sameTable"] or not render_probe["sameUntouched"] \
                    or not render_probe["mergedCount"] or not render_probe["mergesPreserved"] \
                    or render_probe["selected"] != render_probe["originalSelected"] \
                    or render_probe["scrollLeft"] != render_probe["originalScrollLeft"] \
                    or render_probe["scrollTop"] != render_probe["originalScrollTop"]:
                raise AssertionError(f"Single-cell edit redrew unrelated cells or lost selection/scroll position: {render_probe!r}")
            print(f"WebView: a single-cell edit preserved {render_probe['mergedCount']} merged cells and their geometry, selection, and scroll offset.", flush=True)
            expected_time_undo = "Geri al: 1 hücrenin saat değişikliği"
            until("schedule time Undo description", lambda: page.js(
                "document.querySelector('#schedule-history-action')?.textContent") == expected_time_undo)
            if page.js("document.querySelector('#undo-schedule')?.getAttribute('aria-label')") != expected_time_undo:
                raise AssertionError("Schedule Undo button did not describe the changed time cell")
            page.click("#undo-schedule")
            until("schedule time restored by Undo", lambda: page.js(
                f"document.querySelector({json.dumps(start)})?.value === '09:00'"))
            print("WebView: Undo described and restored the schedule time change.", flush=True)
            if page.js('document.querySelector("#easy-schedule-actions [data-schedule-select]")?.getAttribute("aria-pressed") === "true"'):
                page.click("#easy-schedule-actions [data-schedule-select]")
            if page.js("document.querySelectorAll('#schedule-table td.easy-bulk-selected').length"):
                page.click("#easy-schedule-actions button", "Seçimi temizle")

            page.js("""window.__acceptance120ConflictBase = {
              month: state.scheduleMonth, revision: state.schedule.revision,
              entries: structuredClone(state.schedule.entries)
            };
            window.easyAssistant.confirmChanges = async () => true;""")
            type_text(page, start, "10:00")
            press_key(page, "Tab", 9)
            until("local conflicting and independent edits entered", lambda: page.js(
                "state.scheduleDirty && document.querySelector('#schedule-table input[data-action=\"shift-time\"]"
                f"[data-row=\"acceptance-person\"][data-day=\"{day}\"][data-field=\"start\"]')?.value === '10:00'"))
            independent_row_id = "acceptance-person-leave"
            page.click(f'#schedule-table td[data-shift-row="{independent_row_id}"][data-shift-day="{day}"] .quick-entry-open')
            until("independent row quick entry opened", lambda: page.js(
                "!!document.querySelector('#cell-quick-entry-dialog[open]')"))
            page.click('#cell-quick-entry-options [data-cell-preset="work-0700"]')
            until("independent local schedule edit staged", lambda: page.js(
                f"state.schedule.entries.find(entry => entry.row_id === {json.dumps(independent_row_id)})"
                f"?.days[{json.dumps(day)}]?.kind === 'work'"))
            independent_value = json.loads(page.js(f"""JSON.stringify(
              state.schedule.entries.find(entry => entry.row_id === {json.dumps(independent_row_id)})
                ?.days[{json.dumps(day)}])"""))
            page.js(f"""window.__acceptance120ExternalSave = null;
              const external = structuredClone(window.__acceptance120ConflictBase.entries);
              external.find(entry => entry.row_id === 'acceptance-person').days[{json.dumps(day)}].start = '11:00';
              fetch('/api/schedule-months', {{
                method: 'PUT', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{months: {{
                  [window.__acceptance120ConflictBase.month]: {{
                    revision: window.__acceptance120ConflictBase.revision, entries: external
                  }}
                }}, merge_independent: true}})
              }}).then(async response => window.__acceptance120ExternalSave = {{
                ok: response.ok, value: await response.json()
              }});""")
            external_save = until("concurrent test edit saved", lambda: (
                page.js("window.__acceptance120ExternalSave") or False))
            if not external_save["ok"]:
                raise AssertionError(f"Concurrent test save failed: {external_save['value']!r}")
            page.click("#save-schedule")
            until("schedule conflict choices shown", lambda: page.js(
                "!!document.querySelector('#schedule-sharing-conflicts[open] "
                "[data-conflict-choice=\"acceptance-person|"
                f"{day}\"]')"))
            conflict_dialog = "#schedule-sharing-conflicts"
            conflict_select = f'{conflict_dialog} [data-conflict-choice="acceptance-person|{day}"]'
            conflict_values = page.js(f"""(() => {{
              const row = document.querySelector({json.dumps(conflict_select)})?.closest('tr');
              return row ? [row.children[2].textContent.trim(), row.children[3].textContent.trim()] : [];
            }})()""")
            if conflict_values != ["11:00–17:00", "10:00–17:00"]:
                raise AssertionError(f"Conflict did not show saved and draft values: {conflict_values!r}")
            choose_option(page, conflict_select, "ArrowDown")
            page.click(f'{conflict_dialog} .extras-actions .button-primary')
            until("chosen saved value and independent draft change saved", lambda: page.js(f"""(() => {{
              const row = state.schedule.entries.find(entry => entry.row_id === 'acceptance-person');
              const independent = state.schedule.entries.find(entry =>
                entry.row_id === {json.dumps(independent_row_id)})?.days[{json.dumps(day)}];
              return !state.scheduleDirty && row?.days[{json.dumps(day)}]?.start === '11:00'
                && independent?.kind === 'work' && independent?.start === '07:00'
                && independent?.end === '15:00'
                && !document.querySelector({json.dumps(conflict_dialog)})?.open;
            }})()"""))
            schedule_merge_expected.update({
                "row_id": "acceptance-person", "day": day,
                "independent_row_id": independent_row_id, "independent_value": independent_value,
            })
            print("WebView: saved-vs-draft conflict choice kept the saved cell and the separate same-week local edit.", flush=True)
            page.click("#schedule-view .format-panel > summary")
            page.click('#schedule-format-tools [data-format-mode]')
            until("schedule format mode enabled", lambda: page.js(
                'document.querySelector("#schedule-format-tools [data-format-mode]")?.checked'))
            source_cell = f'#schedule-table td[data-shift-row="acceptance-person"][data-shift-day="{day}"]'
            target_cell = f'#schedule-table td[data-shift-row="acceptance-person-alfa"][data-shift-day="{day}"]'
            page.click(source_cell)
            page.js("""document.querySelector('#schedule-format-tools [data-format-color]').value = '#123456';
              document.querySelector('#schedule-format-tools [data-format-color]')
                .dispatchEvent(new Event('input', {bubbles: true}));""")
            page.click('#schedule-format-tools [data-format-action="color"]')
            until("source cell fill persisted", lambda: page.js(f"""(() => {{
              const cell = document.querySelector({json.dumps(source_cell)});
              return cell?.style.backgroundColor === 'rgb(18, 52, 86)';
            }})()"""))
            for group in ("font", "alignment", "border"):
                page.click(f'#schedule-format-tools [data-fx-painter-style="{group}"]')
            page.click('#schedule-format-tools [data-fx="painter"]')
            until("format painter armed", lambda: page.js(
                "document.body.classList.contains('format-painter-active')"))
            page.click(target_cell)
            until("fill-only format copied", lambda: page.js(f"""(() => {{
              const target = document.querySelector({json.dumps(target_cell)});
              const start = document.querySelector('#schedule-table input[data-action="shift-time"]'
                + '[data-row="acceptance-person"][data-day="{day}"][data-field="start"]');
              return target?.style.backgroundColor === 'rgb(18, 52, 86)'
                && target.textContent.includes('HAFTA TATİLİ')
                && start?.value === {json.dumps("11:00" if schedule_merge_expected else "09:00")}
                && !document.body.classList.contains('format-painter-active');
            }})()"""))
            format_copy_result.update(json.loads(page.js(f"""JSON.stringify({{
              target_row: document.querySelector({json.dumps(target_cell)}).dataset.cellRow,
              target_col: document.querySelector({json.dumps(target_cell)}).dataset.cellCol
            }})""")))
            print("WebView: fill-only format copy changed appearance without changing shift times or codes.", flush=True)
            page.click(source_cell)
            page.click('#schedule-format-tools [data-format-bold]')
            page.click('#schedule-format-tools [data-format-action="font"]')
            until("source font persisted", lambda: page.js(f"""(() => {{
              const cell = document.querySelector({json.dumps(source_cell)});
              return sheetSettings('schedule').cells[`${{cell.dataset.cellRow}}:${{cell.dataset.cellCol}}`]?.font_bold === true;
            }})()"""))
            choose_option(page, '#schedule-format-tools [data-format-horizontal]', "ArrowDown")
            page.click('#schedule-format-tools [data-format-action="alignment"]')
            until("source alignment persisted", lambda: page.js(f"""(() => {{
              const cell = document.querySelector({json.dumps(source_cell)});
              return sheetSettings('schedule').cells[`${{cell.dataset.cellRow}}:${{cell.dataset.cellCol}}`]?.horizontal === 'right';
            }})()"""))
            page.click('#schedule-format-tools [data-fx="border"]')
            until("source border persisted", lambda: page.js(f"""(() => {{
              const cell = document.querySelector({json.dumps(source_cell)});
              return !!sheetSettings('schedule').cells[`${{cell.dataset.cellRow}}:${{cell.dataset.cellCol}}`]?.border;
            }})()"""))
            page.click(target_cell)
            page.js("""document.querySelector('#schedule-format-tools [data-format-color]').value = '#654321';
              document.querySelector('#schedule-format-tools [data-format-color]')
                .dispatchEvent(new Event('input', {bubbles: true}));""")
            page.click('#schedule-format-tools [data-format-action="color"]')
            until("target distinct fill persisted", lambda: page.js(
                f"document.querySelector({json.dumps(target_cell)})?.style.backgroundColor === 'rgb(101, 67, 33)'"))
            page.click(source_cell)
            page.click('#schedule-format-tools [data-fx-painter-style="fill"]')
            for group in ("font", "alignment", "border"):
                page.click(f'#schedule-format-tools [data-fx-painter-style="{group}"]')
            page.click('#schedule-format-tools [data-fx="painter"]')
            until("combined format painter armed", lambda: page.js(
                "document.body.classList.contains('format-painter-active')"))
            page.click(target_cell)
            until("font alignment border copied without fill", lambda: page.js(f"""(() => {{
              const cell = document.querySelector({json.dumps(target_cell)});
              const style = sheetSettings('schedule').cells[`${{cell.dataset.cellRow}}:${{cell.dataset.cellCol}}`];
              return !document.body.classList.contains('format-painter-active')
                && style?.font_bold === true && style?.horizontal === 'right'
                && !!style?.border && style?.color?.toLowerCase() === '#654321'
                && cell.textContent.includes('HAFTA TATİLİ');
            }})()"""))
            format_copy_result["target_fill"] = "#654321"
            print("WebView: combined font/alignment/border paste retained the target fill and attendance value.", flush=True)
            before_columns = page.js(
                'document.querySelectorAll("#schedule-table thead th[data-format-column]").length')
            page.js("window.__acceptance120Confirm = ''; window.confirm = message => { window.__acceptance120Confirm = message; return true; };")
            page.cdp("Input.dispatchMouseEvent", type="mouseWheel", x=500, y=500,
                     deltaX=0, deltaY=450)
            page.click('#schedule-table thead th[data-format-column="1"]')
            until("format column delete enabled", lambda: page.js(
                '!document.querySelector("#schedule-format-tools [data-format-delete=column]")?.disabled'))
            page.click('#schedule-format-tools [data-format-delete="column"]')
            expected_delete_undo = "Geri al: sütun silme"
            until("structural delete Undo description", lambda: page.js(
                'document.querySelector("#schedule-format-tools [data-format-history=undo]")?.getAttribute("aria-label")')
                == expected_delete_undo)
            after_columns = page.js(
                'document.querySelectorAll("#schedule-table thead th[data-format-column]").length')
            confirm_message = page.js("window.__acceptance120Confirm")
            if after_columns != before_columns - 1 or "Seçili sütun" not in confirm_message:
                raise AssertionError(f"Column deletion was not applied through its confirmation: {before_columns} -> {after_columns}, {confirm_message!r}")
            page.click('#schedule-format-tools [data-format-history="undo"]')
            until("structural delete undone", lambda: page.js(
                f'document.querySelectorAll("#schedule-table thead th[data-format-column]").length === {before_columns}'))
            redo_label = page.js(
                'document.querySelector("#schedule-format-tools [data-format-history=redo]")?.getAttribute("aria-label")')
            if redo_label != "Yinele: sütun silme":
                raise AssertionError(f"Redo did not describe the structural column delete: {redo_label!r}")
            print("WebView: Undo described and restored the structural column deletion; Redo description updated.", flush=True)
            row_number = page.js("""(() => {
              const rows = [...document.querySelectorAll('#schedule-table tbody tr')];
              return rows.find(row => {
                const source = Number(row.querySelector('td[data-source-row]')?.dataset.sourceRow);
                return source === 34 && row.querySelector('th[data-format-row]')?.getClientRects().length;
              })?.querySelector('th[data-format-row]')?.dataset.formatRow || null;
            })()""")
            if not row_number:
                raise AssertionError("No deletable schedule template row was available")
            rows_before = page.js("document.querySelectorAll('#schedule-table tbody tr').length")
            if page.js("!!document.querySelector('#tt-tip [data-tip=\"ok\"]')?.getClientRects().length"):
                page.click('#tt-tip [data-tip="ok"]')
            page.click(f'#schedule-table th[data-format-row="{row_number}"]')
            until("format row delete enabled", lambda: page.js(
                '!document.querySelector("#schedule-format-tools [data-format-delete=row]")?.disabled'))
            page.click('#schedule-format-tools [data-format-delete="row"]')
            until("structural row delete description", lambda: page.js(
                'document.querySelector("#schedule-format-tools [data-format-history=undo]")?.getAttribute("aria-label")')
                == "Geri al: satır silme")
            if page.js("document.querySelectorAll('#schedule-table tbody tr').length") != rows_before - 1 \
                    or "Seçili satır" not in page.js("window.__acceptance120Confirm"):
                raise AssertionError("Selected row deletion did not remove the intended row after confirmation")
            page.click('#schedule-format-tools [data-format-history="undo"]')
            until("structural row delete undone", lambda: page.js(
                f"document.querySelectorAll('#schedule-table tbody tr').length === {rows_before}"))
            if page.js('document.querySelector("#schedule-format-tools [data-format-history=redo]")?.getAttribute("aria-label")') != "Yinele: satır silme":
                raise AssertionError("Redo did not describe row deletion")
            print("WebView: selected row deletion and Undo/Redo descriptions were verified.", flush=True)
            page.click('#schedule-format-tools [data-format-mode]')
            until("schedule format mode disabled for sparse edit", lambda: page.js(
                '!document.querySelector("#schedule-format-tools [data-format-mode]")?.checked'))
            page.js("""window.__acceptance120DeltaRequest = null;
              window.__acceptance120OriginalFetch = window.fetch;
              window.fetch = (input, init) => {
                if (String(input).includes('/api/schedule-months') && init?.method === 'PUT') {
                  window.__acceptance120DeltaRequest = JSON.parse(init.body);
                }
                return window.__acceptance120OriginalFetch(input, init);
              };""")
            type_text(page, start, "10:00")
            press_key(page, "Tab", 9)
            until("sparse schedule change staged", lambda: page.js(
                f"state.scheduleDirty && document.querySelector({json.dumps(start)})?.value === '10:00'"))
            page.click("#save-schedule")
            request_body = until("single-cell schedule delta sent", lambda: (
                page.js("window.__acceptance120DeltaRequest") or False))
            until("sparse schedule change saved", lambda: page.js("!state.scheduleDirty"))
            delta = request_body["months"][page.js("state.scheduleMonth")]["changes"]
            changed_field = delta["day_changes"][0] if len(delta["day_changes"]) == 1 else {}
            if request_body["months"][page.js("state.scheduleMonth")].get("entries") is not None \
                    or delta["upsert_rows"] or delta["remove_row_ids"] or len(delta["day_changes"]) != 1 \
                    or changed_field.get("row_id") != "acceptance-person" or changed_field.get("day") != day \
                    or changed_field.get("value", {}).get("start") != "10:00" \
                    or changed_field.get("value", {}).get("end") != "17:00" \
                    or changed_field.get("value", {}).get("kind") != "work":
                raise AssertionError(f"Single UI cell edit was not saved as one changed cell: {request_body!r}")
            page.js("window.fetch = window.__acceptance120OriginalFetch")
            with Store(db) as store:
                saved_rows = {entry["row_id"]: entry for entry in store.schedule_month(month)["entries"]}
                if saved_rows["acceptance-person"]["days"][day]["start"] != "10:00" \
                        or saved_rows[independent_row_id]["days"][day]["start"] != "07:00":
                    raise AssertionError("Sparse UI save did not persist only its intended field")
                previous_independent = saved_rows[independent_row_id]["days"][day]
            schedule_merge_expected["source_after_delta"] = "10:00"
            print("WebView: one UI-edited time cell sent as a single-field delta; SQLite retained the separate saved edit.", flush=True)
            page.js("""window.__acceptance120Restored = false;
              window.__acceptance120RestoreViewContext = restoreViewContext;
              restoreViewContext = (view, context) => {
                const result = window.__acceptance120RestoreViewContext(view, context);
                if (view === 'schedule') window.__acceptance120Restored = true;
                return result;
              };""")
            page.click('.main-nav [data-view="attendance"]')
            page.click('.main-nav [data-view="schedule"]')
            second_start = f'#schedule-table input[data-action="shift-time"][data-row="{independent_row_id}"][data-day="{day}"][data-field="start"]'
            until("saved edits reloaded from database", lambda: page.js(f"""(() => {{
              return !state.scheduleDirty && document.querySelector({json.dumps(start)})?.value === '10:00'
                && document.querySelector({json.dumps(second_start)})?.value === '07:00'
                && window.__acceptance120Restored;
            }})()"""))
            page.js("restoreViewContext = window.__acceptance120RestoreViewContext")
            page.js("""window.__acceptance120SecondDelta = null;
              window.__acceptance120SecondFetch = window.fetch;
              window.fetch = (input, init) => {
                if (String(input).includes('/api/schedule-months') && init?.method === 'PUT')
                  window.__acceptance120SecondDelta = JSON.parse(init.body);
                return window.__acceptance120SecondFetch(input, init);
              };
              window.__acceptance120RefreshProbe = {
                table: document.querySelector('#schedule-table'),
                unrelated: document.querySelector('#schedule-table td[data-shift-row="acceptance-person"]')
              };""")
            type_text(page, second_start, "08:00")
            press_key(page, "Tab", 9)
            until("second row edited without unrelated redraw", lambda: page.js(f"""(() => {{
              return state.scheduleDirty && document.querySelector({json.dumps(second_start)})?.value === '08:00'
                && document.querySelector('#schedule-table') === window.__acceptance120RefreshProbe.table
                && document.querySelector('#schedule-table td[data-shift-row="acceptance-person"]') === window.__acceptance120RefreshProbe.unrelated;
            }})()"""))
            page.click("#save-schedule")
            second_request = until("second sparse delta sent", lambda: page.js("window.__acceptance120SecondDelta") or False)
            until("second sparse delta saved", lambda: page.js("!state.scheduleDirty"))
            second_delta = second_request["months"][month]["changes"]
            if second_delta["upsert_rows"] or second_delta["remove_row_ids"] \
                    or len(second_delta["day_changes"]) != 1 \
                    or second_delta["day_changes"][0]["row_id"] != independent_row_id \
                    or second_delta["day_changes"][0]["day"] != day \
                    or second_delta["day_changes"][0]["value"]["start"] != "08:00":
                raise AssertionError(f"Second edit after refresh was not a sparse delta: {second_request!r}")
            page.js("window.fetch = window.__acceptance120SecondFetch")
            with Store(db) as store:
                saved_rows = {entry["row_id"]: entry for entry in store.schedule_month(month)["entries"]}
                if saved_rows["acceptance-person"]["days"][day]["start"] != "10:00" \
                        or saved_rows[independent_row_id]["days"][day]["start"] != "08:00":
                    raise AssertionError("Second sparse save did not preserve the first saved change")
                final_independent = saved_rows[independent_row_id]["days"][day]
                expected_independent = dict(previous_independent, start="08:00", fill_color="#e2efda")
                if final_independent != expected_independent:
                    raise AssertionError(f"Second edit changed other shift properties: {final_independent!r} != {expected_independent!r}")
            schedule_merge_expected["independent_value"] = final_independent
            print("WebView: after page refresh a second row edit preserved unrelated DOM and saved only that row's cell.", flush=True)
            with Store(db) as store:
                current = store.schedule_month(month)
                rows = current["entries"]
                rows.append({"row_id": "acceptance-other-section", "employee_id": other_person,
                             "section": "TEMİZLİK", "slot": 1, "days": {day: {"kind": "weekly_off"}}})
                if store.save_schedule_month(month, rows, current["revision"]) is None:
                    raise AssertionError("Could not establish the isolated other-section record")
            page.click('.main-nav [data-view="attendance"]')
            page.click('.main-nav [data-view="schedule"]')
            until("other-section source loaded", lambda: page.js(
                "state.view === 'schedule' && state.schedule?.entries.some(row => row.row_id === 'acceptance-other-section')"))
            until("market filter restored", lambda: page.js(
                "document.querySelector('#schedule-section-filter')?.value === 'MARKET'"))
            page.click("#tools-menu-button")
            type_text(page, "#easy-tools-search", "Otomatik vardiya oluştur")
            page.click('.easy-search-result button[aria-label*="menüdeki yerini göster"]', "Yerini göster")
            until("rotation command revealed for scope test", lambda: page.js(
                "document.activeElement?.closest('.easy-tool-flyout')"
                " && document.activeElement.textContent.includes('Otomatik vardiya oluştur')"))
            press_key(page, " ", 32)
            until("rotation scope tool opened", lambda: page.js(
                "!!document.querySelector('#rotation-dialog[open] #rotation-person')"))
            page.click("#rotation-person")
            press_key(page, "End", 35)
            until("other-section person selected for rotation", lambda: page.js(f"""(() => {{
              const selected = document.querySelector('#rotation-person')?.selectedOptions;
              return selected?.length === 1 && selected[0].value === {json.dumps(str(other_person))};
            }})()"""))
            until("rotation scope follows actual selected section", lambda: page.js("""(() => {
              const scope = document.querySelector('#rotation-dialog [data-extras-scope]')?.textContent || '';
              return scope.includes('TEMİZLİK') && scope.includes('1 seçili personel')
                && !scope.includes('MARKET')
                && document.querySelector('#rotation-dialog [data-extras-scope]')?.getAttribute('aria-label') === 'İşlem kapsamı';
            })()"""))
            page.click("#rotation-palette [data-cell-preset]")
            page.click("#rotation-apply")
            until("rotation preview reflects the selected person and section", lambda: page.js("""(() => {
              const dialog = document.querySelector('#easy-bulk-preview[open]');
              const scope = dialog?.querySelector('[data-extras-scope]')?.textContent || '';
              return scope.includes('TEMİZLİK') && scope.includes('1 etkilenen personel')
                && !scope.includes('MARKET') && dialog.textContent.includes('Acceptance other section');
            })()"""))
            page.click("#easy-bulk-preview button", "Onayla ve taslağa uygula")
            until("rotation applied and returned focus to tools control", lambda: page.js(
                "state.scheduleDirty && !document.querySelector('#easy-bulk-preview')?.open"
                " && document.activeElement === document.querySelector('#tools-menu-button')"))
            page.click("#save-schedule")
            until("rotation saved", lambda: page.js("!state.scheduleDirty"))
            with Store(db) as store:
                rotated = {entry["row_id"]: entry for entry in store.schedule_month(month)["entries"]}
                if rotated["acceptance-other-section"]["days"].get(second_day) is None:
                    raise AssertionError("Rotation did not persist the selected other-section person")
                if any(rotated[row_id]["days"] != saved_rows[row_id]["days"] for row_id in saved_rows):
                    raise AssertionError("Rotation affected a person outside the displayed selected scope")
            page.click("#undo-schedule")
            until("rotation undone", lambda: page.js("state.scheduleDirty"))
            page.click("#save-schedule")
            until("rotation undo saved", lambda: page.js("!state.scheduleDirty"))
            print("WebView: rotation selected and saved only TEMİZLİK while the MARKET view was filtered; preview and SQLite matched.", flush=True)
            if page.js("document.querySelectorAll('#schedule-table td.easy-bulk-selected').length"):
                page.click("#easy-schedule-actions button", "Seçimi temizle")
            page.js("document.querySelector('#easy-schedule-actions [data-schedule-select]')"
                    ".scrollIntoView({block:'center', behavior:'instant'})")
            if not page.js('document.querySelector("#easy-schedule-actions [data-schedule-select]")?.getAttribute("aria-pressed") === "true"'):
                page.click("#easy-schedule-actions [data-schedule-select]")
            page.click(f'#schedule-table td[data-shift-row="acceptance-person"][data-shift-day="{day}"]')
            until("first market cell selected for scoped operation", lambda: page.js(
                f'document.querySelector("#schedule-table td[data-shift-row=\\"acceptance-person\\"][data-shift-day=\\"{day}\\"]")?.classList.contains("easy-bulk-selected")'))
            page.click(f'#schedule-table td[data-shift-row="acceptance-person-alfa"][data-shift-day="{day}"]')
            until("two market cells selected for scoped operation", lambda: page.js(
                "document.querySelectorAll('#schedule-table td.easy-bulk-selected').length === 2"))
            page.click("#easy-schedule-actions button", "Toplu giriş")
            until("bulk editor shows selected market scope", lambda: page.js("""(() => {
              const scope = document.querySelector('#easy-bulk-editor[open] [data-extras-scope]')?.textContent || '';
              return scope.includes('MARKET') && scope.includes('2 seçili personel')
                && !scope.includes('TEMİZLİK');
            })()"""))
            page.click("#easy-bulk-editor button", "Önizlemeyi hazırla")
            until("bulk preview shows affected market scope", lambda: page.js("""(() => {
              const scope = document.querySelector('#easy-bulk-preview[open] [data-extras-scope]')?.textContent || '';
              return scope.includes('MARKET') && scope.includes('2 etkilenen personel')
                && !scope.includes('TEMİZLİK');
            })()"""))
            page.click("#easy-bulk-preview button", "Onayla ve taslağa uygula")
            until("scoped bulk operation applied", lambda: page.js(
                "state.scheduleDirty && !document.querySelector('#easy-bulk-preview')?.open"))
            until("nested preview returns focus to original tool opener", lambda: page.js("""(() => {
              const button = [...document.querySelectorAll('#easy-schedule-actions button')]
                .find(item => item.textContent.trim() === 'Toplu giriş');
              return document.activeElement === button;
            })()"""))
            page.click("#save-schedule")
            until("scoped bulk operation persisted", lambda: page.js("!state.scheduleDirty"))
            with Store(db) as store:
                after = {entry["row_id"]: entry for entry in store.schedule_month(month)["entries"]}
                if after["acceptance-person"]["days"][day] == saved_rows["acceptance-person"]["days"][day] \
                        or after["acceptance-person-alfa"]["days"][day] == saved_rows["acceptance-person-alfa"]["days"][day] \
                        or after["acceptance-other-section"]["days"][day] != {"kind": "weekly_off"}:
                    raise AssertionError("Bulk save did not match the displayed market section and selected people")
            page.click("#undo-schedule")
            until("scoped operation undone", lambda: page.js("state.scheduleDirty"))
            page.click("#save-schedule")
            until("scoped operation rollback saved", lambda: page.js("!state.scheduleDirty"))
            print("WebView: MARKET scope matched two selected and saved people; the other section remained unchanged.", flush=True)
            js_errors = page.js("window.__acceptance120Errors")
            if js_errors:
                raise AssertionError(f"Uncaught browser errors: {js_errors!r}")
            passed.set()
        except Exception as exc:
            failures.append(exc)
            print("Browser at failure:", page.js("""({
                view: document.querySelector('.view.active')?.id,
                schedule: {
                  search: document.querySelector('#schedule-search')?.value,
                  section: document.querySelector('#schedule-section-filter')?.value,
                  sort: document.querySelector('#schedule-sort')?.value,
                  people: [...document.querySelectorAll('#schedule-table select[data-action="person"]')].map(e => e.value),
                  personFiltersOpen: document.querySelector('#schedule-view .easy-filters')?.open,
                  dirty: state.scheduleDirty, revision: state.schedule?.revision,
                  rows: state.schedule?.entries.map(entry => ({id: entry.row_id, days: entry.days})),
                },
                format: {
                  mode: document.querySelector('#schedule-format-tools [data-format-mode]')?.checked,
                  panel: document.querySelector('#schedule-view .format-panel')?.open,
                  columns: document.querySelectorAll('#schedule-table thead th[data-format-column]').length,
                  header: document.querySelector('#schedule-table thead')?.innerHTML.slice(0, 500),
                },
                dialog: document.querySelector('#easy-bulk-editor')?.outerHTML.slice(0, 500),
                dialogState: [...document.querySelectorAll('dialog')].map(e => ({
                  id:e.id, open:e.open, modal:e.matches(':modal'), class:e.className,
                  rect:e.getBoundingClientRect().toJSON(),
                  close:e.querySelector('[data-extras-close]')?.getBoundingClientRect().toJSON(),
                  closeHidden:e.querySelector('[data-extras-close]')?.hidden,
                })),
                active: document.activeElement?.outerHTML.slice(0, 180),
                selectButton: (() => { const e=document.querySelector('#easy-schedule-actions [data-schedule-select]'); if(!e)return null; const r=e.getBoundingClientRect(); return {rect:r.toJSON(), top:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML.slice(0,220), disabled:e.disabled}; })(),
                columnHeader: (() => { const e=document.querySelector('#schedule-table thead th[data-format-column="1"]'); if(!e)return null; const r=e.getBoundingClientRect(); return {rect:r.toJSON(),top:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.outerHTML.slice(0,180)}; })(),
                search: (() => { const e=document.querySelector('#schedule-search'); return e && {
                  rect:e.getBoundingClientRect().toJSON(), hidden:e.closest('details')?.open,
                  parent:e.parentElement?.outerHTML.slice(0,250),
                  top:document.elementFromPoint(e.getBoundingClientRect().x+2,e.getBoundingClientRect().y+2)?.outerHTML.slice(0,100)
                }; })(),
                scroll: [...document.querySelectorAll('#attendance-table, #attendance-table, .attendance-frame, main, body, html')]
                  .map(e => ({tag:e.tagName,id:e.id,clientWidth:e.clientWidth,scrollWidth:e.scrollWidth,
                    clientHeight:e.clientHeight,scrollHeight:e.scrollHeight,scrollLeft:e.scrollLeft,scrollTop:e.scrollTop})),
                toast: document.querySelector('#toast')?.textContent,
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
    with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)) as connection:
        row = connection.execute("SELECT payload FROM attendance_months WHERE period=?", (month,)).fetchone()
    values = json.loads(row[0])["overrides"]
    actual = (values[day][str(person)], values[second_day][str(person)])
    if actual != ("X", "H"):
        raise AssertionError(f"Opening/minimizing the tool unexpectedly changed saved values: {actual!r}")
    if format_copy_result:
        with Store(db) as store:
            target_style = store.workbook_settings()["sheets"][f"schedule:{month}:0"]["cells"][
                f'{format_copy_result["target_row"]}:{format_copy_result["target_col"]}']
            if target_style.get("color", "").lower() != "#654321" \
                    or not target_style.get("font_bold") or target_style.get("horizontal") != "right" \
                    or not target_style.get("border"):
                raise AssertionError(f"Format painter did not persist the selected styles: {target_style!r}")
            entries = store.schedule_month(month)["entries"]
            original = next(item for item in entries if item["row_id"] == "acceptance-person")
            target = next(item for item in entries if item["row_id"] == "acceptance-person-alfa")
            expected_start = schedule_merge_expected.get(
                "source_after_delta", "11:00" if schedule_merge_expected else "09:00",
            )
            if original["days"][day].get("kind") != "work" \
                    or original["days"][day].get("start") != expected_start \
                    or original["days"][day].get("end") != "17:00":
                raise AssertionError(f"Unexpected source schedule value after UI actions: {original['days'][day]!r}")
            if target["days"][day] != {"kind": "weekly_off"}:
                raise AssertionError(f"Format copy changed the target schedule code: {target['days'][day]!r}")
            if schedule_merge_expected:
                merged = next(item for item in entries if item["row_id"] == schedule_merge_expected["row_id"])
                if merged["days"][schedule_merge_expected["day"]]["start"] != schedule_merge_expected.get("source_after_delta", "11:00"):
                    raise AssertionError("Chosen saved conflict value was not retained before the sparse delta")
                independent = next(item for item in entries if item["row_id"] == schedule_merge_expected["independent_row_id"])
                if independent["days"][schedule_merge_expected["day"]] != schedule_merge_expected["independent_value"]:
                    raise AssertionError("Non-conflicting local schedule edit was lost")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Run this standalone acceptance test on Windows with Edge WebView2.")
    with tempfile.TemporaryDirectory(prefix=".ui120-minimize-", dir=ROOT) as directory:
        run(Path(directory))
    print("PASS: selected WebView2 interface scenarios, persisted data, and sparse writes verified.")
