"""Standalone real-WebView2 acceptance scenarios for Workforce acceptance items.

Run on Windows from the project root:
    python -B tests\\acceptance_workforce.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from datetime import date, timedelta
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
          const el = document.querySelector({json.dumps(selector)});
          if (!el || el.disabled || !el.getClientRects().length) return null;
          el.scrollIntoView({{block:'nearest', inline:'nearest', behavior:'instant'}});
          const r = el.getBoundingClientRect(), x = r.x+r.width/2, y = r.y+r.height/2;
          return el.contains(document.elementFromPoint(x,y)) ? {{x,y}} : null;
        }})()"""
        point = until(f"clickable {selector}", lambda: self.js(expression))
        for event in ("mousePressed", "mouseReleased"):
            self.cdp("Input.dispatchMouseEvent", type=event, button="left", clickCount=1, **point)


def save_schedule(store, month, entries):
    import scheduling

    normalized = scheduling.normalize_schedule_entries(month, entries, store.people())
    revision = store.schedule_month(month)["revision"]
    return store.save_schedule_month(month, normalized, revision)


def run(folder):
    profile = folder / "browser"
    os.environ.update(
        TEMP=str(folder), TMP=str(folder), NO_PROXY="127.0.0.1,localhost",
        WEBVIEW2_USER_DATA_FOLDER=str(profile),
        WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=(
            "--disable-background-networking --disable-component-update --disable-sync "
            "--disable-quic --disable-background-timer-throttling --disable-renderer-backgrounding "
            "--proxy-server=http://127.0.0.1:0 --proxy-bypass-list=127.0.0.1;localhost "
            '--host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"'),
    )
    tempfile.tempdir = str(folder)

    import webview
    import access_services
    import document_workflows
    import operations
    import linked_leave
    import schedule_protection
    import personnel_assets
    from server import create_server
    from storage import Store

    db = folder / "synthetic.sqlite3"
    target = date(2026, 11, 1)
    prior = target - timedelta(days=1)
    fixed_effective = date.today() + timedelta(days=14)
    fixed_months = {fixed_effective.strftime("%Y-%m"),
                    (fixed_effective - timedelta(days=1)).strftime("%Y-%m")}
    month, prior_month, day, prior_day = (
        target.strftime("%Y-%m"), prior.strftime("%Y-%m"),
        target.isoformat(), prior.isoformat(),
    )
    with Store(db) as store:
        ada, bora, ceren = [person["id"] for person in store.add_people(
            ["Acceptance Ada", "Acceptance Bora", "Acceptance Ceren"],
        )]
        exit_person = store.add_people(["Acceptance Exit"])[0]
        fixed_person = store.add_people(["Acceptance Fixed Pattern"])[0]
        hour_candidate = store.add_people(["Acceptance Hour Candidate"])[0]["id"]
        fixed_leave_person = store.add_people(["Acceptance Fixed Leave"])[0]["id"]
        match_left, match_right = [person["id"] for person in store.add_people(
            ["Acceptance Match One", "Acceptance Match Two"],
        )]
        leave_move_person = store.add_people(["Acceptance Leave Move"])[0]
        store.save_employee_profile(match_left, {
            "payroll_code": "ACCEPTANCE-22", "home_section": "MARKET", "skills": ["Kasa"],
        })
        store.save_employee_profile(match_right, {
            "payroll_code": "ACCEPTANCE-22", "home_section": "MARKET", "skills": ["Kasa"],
        })
        save_schedule(store, prior_month, [{
            "row_id": "acceptance-overnight", "employee_id": ada, "section": "MARKET",
            "days": {prior_day: {"kind": "work", "start": "22:00", "end": "02:00"}},
        }, {
            "row_id": "acceptance-hour-candidate", "employee_id": hour_candidate, "section": "MARKET",
            "days": {"2026-10-28": {"kind": "work", "start": "08:00", "end": "16:00"}},
        }])
        save_schedule(store, month, [
            {"row_id": "acceptance-relief", "employee_id": bora, "section": "MARKET",
             "days": {day: {"kind": "work", "start": "00:00", "end": "03:00"}}},
            {"row_id": "acceptance-overlap", "employee_id": ceren, "section": "MARKET",
             "days": {
                 day: {"kind": "work", "start": "01:00", "end": "06:00"},
                 "2026-11-02": {"kind": "work", "start": "10:00", "end": "18:00"},
             }},
            {"row_id": "acceptance-rotation", "employee_id": ada, "section": "MARKET",
             "days": {"2026-11-03": {"kind": "annual_leave"}}},
             {"row_id": "acceptance-fixed-leave", "employee_id": fixed_leave_person,
              "section": "MARKET", "days": {day: {"kind": "weekly_off"}}},
        ])
        store.save_compliance_settings({"min_staffing_windows": [{
            "section": "MARKET", "start": "00:00", "end": "06:00", "minimum": 3,
        }]})
        operations.save_rules(store.connection, [{
            "id": "acceptance-protected-replan", "type": "staffing", "section": "MARKET",
            "start_date": "2026-11-02", "end_date": "2026-11-02", "weekdays": [0],
            "start": "00:00", "end": "06:00", "minimum": 3, "skill": "",
            "severity": "warning", "exception_reason": "",
        }])
        store.connection.commit()
        store.save_employee_profile(ceren, {
            "home_section": "MARKET", "training_skills": ["Kasa"],
            "duty_required_fields": ["skills"],
            "documents": [{
                "id": "acceptance-document-old", "name": "Acceptance Hijyen Belgesi",
                "expires": "2026-10-15", "note": "Eski nüsha",
            }],
        })
        store.save_employee_profile(ada, {
            "rotation": ["work-0700", "weekly-off"],
            "rotation_anchor": "2026-11-01",
            "rotation_resume_mode": "continue",
            "experienced_skills": ["Kasa"],
        })
        store.save_employee_profile(exit_person["id"], {
            "title": "Bölüm belge sorumlusu",
            "duty_required_fields": ["documents", "home_section"],
            "staff_type": "nonshift", "fixed_pattern": ["X", "X", "X", "X", "X", "X", "H"],
            "fixed_start": "08:00", "fixed_end": "16:00",
        })
        store.save_employee_profile(fixed_person["id"], {
            "staff_type": "nonshift", "fixed_pattern": ["X"] * 7,
            "fixed_start": "08:00", "fixed_end": "16:00",
        })
        store.save_employee_profile(fixed_leave_person, {"home_section": "MARKET"})
        operations.save_availability(store.connection, {
            "employee_id": ceren, "start_date": "2026-11-01", "end_date": "2026-11-30",
            "unavailable_weekdays": [], "unavailable_ranges": [],
            "no_night": False, "no_weekend": True, "preference": True,
        }, store.people())
        store.connection.commit()
        store.actor = "Acceptance Scheduler"
        schedule_protection.set_lock(store, {
            "month": month, "row_id": "acceptance-overlap", "date": "2026-11-02",
            "revision": store.schedule_month(month)["revision"], "protected": True,
            "reason": "Kritik açılış görevi",
        })
        operations.publish_in_transaction(store, month, store.schedule_month(month)["revision"], "Acceptance Scheduler")
        personnel_assets.initialize(store.connection)
        document_workflows.initialize(store.connection)
        access_services.initialize(store.connection)
        personnel_assets.issue_asset(store, {
            "action": "issue", "employee_id": exit_person["id"], "category": "card",
            "item": "Acceptance access card", "note": "",
        })
        exit_effective = (date.today() + timedelta(days=14)).isoformat()
        exit_created = "2026-10-01T00:00:00+00:00"
        with store.connection:
            store.connection.execute(
                "INSERT INTO workflow_documents "
                "(id,employee_id,person_name,exact_dates,code,template_id,filename,content_type,content,"
                "status,source_revisions,app_version,code_revision,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("acceptance-exit-document", exit_person["id"], exit_person["name"], "[]", "S", "test",
                 "Acceptance pending.pdf", "application/pdf", b"%PDF-test", "draft", "{}", "test", "test",
                 exit_created, exit_created),
            )
            now = int(time.time())
            store.connection.execute(
                "INSERT INTO portal_tokens(token_hash,employee_id,expires_at,revoked_at,created_at) "
                "VALUES (?,?,?,?,?)",
                ("e" * 64, exit_person["id"], now + 86400 * 30, None, now),
            )
            share_expiry = (date.today() + timedelta(days=30)).isoformat() + "T00:00:00+00:00"
            store.connection.execute(
                "INSERT INTO workflow_shares"
                "(id,token_hash,employee_id,person_name,month,snapshot,expires_at,revoked_at,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("acceptance-exit-share", "f" * 64, exit_person["id"], exit_person["name"],
                 exit_effective[:7], "{}", share_expiry, "", exit_created),
            )
        save_schedule(store, exit_effective[:7], [{
            "row_id": "acceptance-exit-future", "employee_id": exit_person["id"], "section": "MARKET",
            "days": {exit_effective: {"kind": "work", "start": "08:00", "end": "16:00"}},
        }])
        known_schedule_months = {month, prior_month, exit_effective[:7]}
        for fixed_month in sorted(fixed_months - known_schedule_months):
            save_schedule(store, fixed_month, [])
        now = "2026-10-01T00:00:00+00:00"
        with store.connection:
            for request_id, employee_id, kind, status in (
                ("acceptance-annual", ceren, "annual_leave", "pending"),
                ("acceptance-sick", ceren, "sick_leave", "pending"),
                ("acceptance-cancelled", ceren, "annual_leave", "cancelled"),
                ("acceptance-pending", ceren, "annual_leave", "pending"),
                ("acceptance-fixed-annual", fixed_leave_person, "annual_leave", "pending"),
            ):
                store.connection.execute(
                    "INSERT INTO linked_leave_requests "
                    "(id,employee_id,pattern,start_date,end_date,weekdays,kind,status,note,revision,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (request_id, employee_id, "single", day, day, "[]", kind, status, "", 1, now, now),
                )
        current_attendance = store.attendance_month(month)
        store.save_attendance_month(
            month, {day: {str(ceren): "S"}}, {}, current_attendance["revision"])
        store.save_balance_opening(ceren, 2026, 20, 0)
        store.save_balance_opening(ceren, 2026, 18, 0)
        operations.save_shift_activity(store, {
            "month": month, "date": day, "section": "MARKET", "kind": "break",
            "start": "02:00", "end": "03:00", "employee_id": ceren, "recipient_id": None,
        })
        before = store.schedule_month(month)
        prior_before = store.schedule_month(prior_month)
        expected_schedule_after_ui = [before]
        activities_before = operations.shift_activity_report(store, target)["items"]
        attendance_before = store.attendance_month(month)

    server = create_server(db, ROOT / "web")
    server.easy_mode = True
    origin = f"http://127.0.0.1:{server.server_port}"
    window = webview.create_window("Workforce acceptance", origin, width=1440, height=900, focus=False)
    failures = []
    passed = threading.Event()

    def drive():
        page = Browser(window)
        try:
            if not window.events.loaded.wait(40):
                raise TimeoutError("WebView2 did not load the local application")
            if page.js(f"state.scheduleMonth !== {json.dumps(month)}"):
                page.js(f"""(() => {{
                  const input = document.querySelector('#schedule-month');
                  input.value = {json.dumps(month)};
                  input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }})()""")
                until("acceptance schedule month loaded", lambda: page.js(
                    f"state.scheduleMonth === {json.dumps(month)} && "
                    f"!!document.querySelector('button[data-hourly-day=\"{day}\"]')"))
            compile_result = page.js("""(async () => {
              const names = ['app.js', 'extras.js', 'linked-leave.js', 'operations.js', 'data-continuity.js'];
              for (const name of names) {
                const response = await fetch('/' + name);
                if (!response.ok) throw new Error(name + ' HTTP ' + response.status);
                try { new Function(await response.text()); }
                catch (error) { throw new Error(name + ': ' + error.message); }
              }
              return true;
            })()""")
            if compile_result is not True:
                raise AssertionError("Changed JavaScript files did not compile in WebView2")
            if page.js("typeof extrasMonthLabel === 'function' && typeof extrasDialog === 'function'") is not True:
                raise AssertionError("The changed extras.js functions were not available after app startup")
            until("schedule table rendered", lambda: page.js(
                "document.querySelector('#schedule-table tbody')?.rows.length > 0"))
            page.js("""(() => {
              window.__workforceRuntimeErrors = [];
              addEventListener('error', event => window.__workforceRuntimeErrors.push(
                String(event.message || event.error || 'window error')));
              addEventListener('unhandledrejection', event => window.__workforceRuntimeErrors.push(
                String(event.reason || 'unhandled rejection')));
            })()""")
            timeline_schedule_before = page.js(f"""fetch('/api/schedule?month={month}')
              .then(response => response.json())
              .then(schedule => ({{revision: schedule.revision, entries: schedule.entries}}))""")
            page.click(f'button[data-hourly-day="{day}"]')
            until("hourly timeline opened", lambda: page.js(
                "document.querySelector('#hourly-day-dialog[open] .extras-body')?.textContent.includes('Vardiya saat örtüşmeleri')"))
            time.sleep(0.1)
            timeline_schedule_after = page.js(f"""fetch('/api/schedule?month={month}')
              .then(response => response.json())
              .then(schedule => ({{revision: schedule.revision, entries: schedule.entries}}))""")
            if timeline_schedule_after != timeline_schedule_before:
                raise AssertionError(
                    "Opening the read-only timeline changed saved schedule cells or revision: "
                    f"before={timeline_schedule_before!r}, after={timeline_schedule_after!r}")
            assertions = page.js(f"""(() => {{
              const dialog = document.querySelector('#hourly-day-dialog[open]');
              const body = dialog?.querySelector('.extras-body');
              const overlapHeading = [...(body?.querySelectorAll('h3') || [])]
                .find(heading => heading.textContent === 'Vardiya saat örtüşmeleri');
              const night = [...(body?.querySelectorAll('.hourly-timeline-bar') || [])]
                .find(bar => bar.title.includes('Acceptance Ada'));
              const plannedBreak = body?.querySelector('.hourly-timeline-bar.is-break');
              return {{
                title: dialog?.querySelector('.extras-title')?.textContent || dialog?.textContent.slice(0, 60),
                nightTitle: night?.title,
                nightLeft: night ? parseFloat(night.style.left) : null,
                nightWidth: night ? parseFloat(night.style.width) : null,
                breakTitle: plannedBreak?.title || '',
                highlighted: body?.querySelectorAll('.hourly-timeline-bar.is-overlap').length || 0,
                overlaps: overlapHeading?.nextElementSibling?.textContent || '',
                uncovered: [...(body?.querySelectorAll('li') || [])].map(item => item.textContent)
                  .find(text => text.includes('MARKET 06:00–24:00')) || '',
                staffingGaps: [...(body?.querySelectorAll('li') || [])].map(item => item.textContent)
                  .filter(text => text.includes('MARKET') && text.includes('kişi eksik')),
                sundayBoundary: body?.textContent.includes('Sonraki pazartesi vardiyası (salt okunur)') &&
                  body.textContent.includes('2026-11-02') &&
                  body.textContent.includes('MARKET · Acceptance Ceren · 10:00–18:00') &&
                  body.textContent.includes('Dinlenme aralığı: 28 saat'),
                errors: window.__workforceRuntimeErrors,
              }};
            }})()""")
            if not assertions["nightTitle"] or "22:00–02:00" not in assertions["nightTitle"]:
                raise AssertionError(f"Overnight shift is missing: {assertions}")
            if assertions["nightLeft"] != 0 or abs(assertions["nightWidth"] - (2 / 24 * 100)) > 0.01:
                raise AssertionError(f"Overnight shift was not clipped to 00:00–02:00: {assertions}")
            if assertions["highlighted"] < 2:
                raise AssertionError(f"Overlapping shifts are not highlighted: {assertions}")
            if not assertions["overlaps"] or "örtüşüyor" not in assertions["overlaps"]:
                raise AssertionError(f"Overlap details are missing: {assertions}")
            if assertions["uncovered"] != "MARKET 06:00–24:00":
                raise AssertionError(f"Uncovered interval is missing: {assertions}")
            if not any("00:00–01:00" in item and "1 kişi eksik" in item
                       for item in assertions["staffingGaps"]):
                raise AssertionError(f"Configured staffing gap is missing: {assertions}")
            if not any("03:00–04:00" in item and "2 kişi eksik" in item
                       for item in assertions["staffingGaps"]):
                raise AssertionError(f"Configured staffing gap is missing: {assertions}")
            if not assertions["sundayBoundary"]:
                raise AssertionError(f"Sunday-to-Monday read-only shift/rest detail is missing: {assertions}")
            if "02:00–03:00" not in assertions["breakTitle"]:
                raise AssertionError(f"Existing break is not on the hourly timeline: {assertions}")
            if assertions["errors"]:
                raise AssertionError(f"JavaScript runtime errors: {assertions['errors']}")
            page.js("""(() => {
              const form = document.querySelector('#hourly-day-dialog [data-break-preview-form]');
              form.elements.start.value = '01:00';
              form.elements.end.value = '02:00';
            })()""")
            page.click('#hourly-day-dialog [data-break-preview-form] button[type="submit"]')
            until("break move preview rendered", lambda: page.js(
                "document.querySelector('[data-break-preview-result]')?.textContent.includes('Taşıma sonrası öngörülen açıklıklar')"))
            break_preview = page.js("document.querySelector('[data-break-preview-result]').textContent")
            if "02:00–03:00: 2 kişi eksik" not in break_preview:
                raise AssertionError(f"Current break coverage is missing from preview: {break_preview}")
            if "01:00–02:00: 1 kişi eksik" not in break_preview:
                raise AssertionError(f"Moved-break coverage preview is missing: {break_preview}")
            if "Yeni oluşacak açıklık" not in break_preview or page.js("window.__workforceRuntimeErrors.length") != 0:
                raise AssertionError(f"Break preview did not expose its new gap cleanly: {break_preview}")
            page.click('#hourly-day-dialog button[data-extras-close]')
            page.click('#schedule-sheet-tabs button[data-week="1"]')
            until("Monday week visible", lambda: page.js(
                "!!document.querySelector('button[data-hourly-day=\"2026-11-02\"]')"))
            page.click('button[data-hourly-day="2026-11-02"]')
            until("Monday boundary timeline opened", lambda: page.js(
                "document.querySelector('#hourly-day-dialog[open] .extras-body')?.textContent.includes('Önceki pazar vardiyası (salt okunur)')"))
            monday_boundary = page.js("""(() => {
              const text = document.querySelector('#hourly-day-dialog[open] .extras-body').textContent;
              return text.includes('2026-11-01') &&
                text.includes('MARKET · Acceptance Bora · 00:00–03:00') &&
                text.includes('MARKET · Acceptance Ceren · 01:00–06:00') &&
                text.includes('Acceptance Ceren: Dinlenme aralığı: 28 saat') &&
                window.__workforceRuntimeErrors.length === 0;
            })()""")
            if not monday_boundary:
                raise AssertionError("Monday did not show the previous Sunday shifts and actual rest interval")
            protection = page.js("""(() => {
              const badge = [...document.querySelectorAll('.schedule-protection-badge')]
                .find(item => (item.getAttribute('aria-label') || '').includes('Kritik açılış görevi'));
              return badge?.getAttribute('aria-label') || '';
            })()""")
            if "Acceptance Scheduler" not in protection:
                raise AssertionError(f"Protected assignment omitted its reason or author: {protection}")
            page.click('#hourly-day-dialog button[data-extras-close]')
            page.js("window.linkedLeave.open(); true")
            until("linked leave dialog opened", lambda: page.js(
                "document.querySelector('#linked-leave-dialog[open] [data-linked-calendar]') !== null"))
            page.js("document.querySelector('#linked-leave-dialog [data-linked-calendar]').click(); true")
            until("monthly leave density rendered", lambda: page.js(
                "document.querySelector('[data-linked-calendar-result]')?.textContent.includes('MARKET')"))
            calendar_text = page.js("document.querySelector('[data-linked-calendar-result]').textContent")
            if day not in calendar_text or "Yıllık izin" not in calendar_text or "Raporlu" not in calendar_text or \
                    "Önceliği yapılandırılmamış çakışan izin türleri" not in calendar_text:
                raise AssertionError(f"Section/day leave density or unresolved type overlap is missing: {calendar_text}")
            calendar_density = page.js("""(() => {
              const rows = [...document.querySelectorAll('[data-linked-calendar-result] table tbody tr')];
              const row = rows.find(item => item.cells[0]?.textContent.trim() === '2026-11-01' &&
                item.cells[1]?.textContent.trim() === 'MARKET');
              return row ? [...row.cells].map(cell => cell.textContent.trim()) : [];
            })()""")
            fixed_overlap = page.js("""(() => [...document.querySelectorAll(
              '[data-linked-calendar-result] ul li')]
              .map(item => item.textContent)
              .find(text => text.includes('Acceptance Fixed Leave')) || '')()""")
            if len(calendar_density) != 4 or calendar_density[2] != "2" or \
                    "Acceptance Ceren" not in calendar_density[3] or \
                    "Acceptance Fixed Leave" not in calendar_density[3]:
                raise AssertionError(f"Two-person section density was not rendered: {calendar_density}")
            if "Hafta tatili (yıllık izinden düşmez)" not in fixed_overlap or \
                    "Yıllık izin" not in fixed_overlap or \
                    "İzin türü önceliği yapılandırılmadı; otomatik tür seçilmedi." not in fixed_overlap:
                raise AssertionError(f"Fixed/annual overlap or unresolved precedence was not shown: {fixed_overlap}")
            until("pending leave request action rendered", lambda: page.js(
                "!!document.querySelector('#linked-leave-dialog [data-linked-action=\"impact\"][data-id=\"acceptance-pending\"]')"))
            page.js("""document.querySelector(
              '#linked-leave-dialog [data-linked-action="impact"][data-id="acceptance-pending"]').click(); true""")
            until("leave impact preview rendered", lambda: page.js(
                "document.querySelector('#linked-leave-dialog [data-linked-alternative-form]') !== null"))
            page.js("""(() => {
              const form = document.querySelector('#linked-leave-dialog [data-linked-alternative-form]');
              const starts = [...form.querySelectorAll('[name="alternative_start"]')];
              const ends = [...form.querySelectorAll('[name="alternative_end"]')];
              starts[0].value = '2026-10-31'; ends[0].value = '2026-10-31';
              starts[1].value = '2026-11-02'; ends[1].value = '2026-11-02';
              form.requestSubmit();
              return true;
            })()""")
            until("alternative leave dates compared", lambda: page.js(
                "document.querySelector('[data-linked-alternatives-result]')?.textContent.includes('Alternatif 2')"))
            alternatives_text = page.js("document.querySelector('[data-linked-alternatives-result]').textContent")
            if "2026-10-31" not in alternatives_text or "2026-11-02" not in alternatives_text or \
                    "Seçili tarihler" not in alternatives_text or "Alternatif 1" not in alternatives_text:
                raise AssertionError(f"Leave alternatives were not compared side by side: {alternatives_text}")
            page.js(f"""(() => {{
              const form = document.querySelector('#linked-leave-dialog [data-linked-candidate-form]');
              form.elements.employee_id.value = {json.dumps(str(ada))};
              form.elements.date.value = {json.dumps(day)};
              form.elements.section.value = 'MARKET';
              form.elements.start.value = '09:00';
              form.elements.end.value = '17:00';
              form.requestSubmit();
              return true;
            }})()""")
            until("candidate checks rendered", lambda: page.js(
                "document.querySelector('[data-linked-candidates]')?.textContent.includes('Kontrol edilen kurallar')"))
            candidate_text = page.js("document.querySelector('[data-linked-candidates]').textContent")
            if "Bölüm için gereken yetkinlikler" not in candidate_text or \
                    "Haftalık saat sınırı yapılandırılmamış" not in candidate_text:
                raise AssertionError(f"Candidate explanation omitted checks or unknowns: {candidate_text}")
            page.js(f"""(() => {{
              const form = document.querySelector('#linked-leave-dialog [data-linked-candidate-form]');
              form.elements.employee_id.value = {json.dumps(str(hour_candidate))};
              form.elements.date.value = {json.dumps(day)};
              form.elements.section.value = 'MARKET';
              form.elements.start.value = '09:00';
              form.elements.end.value = '17:00';
              form.requestSubmit();
              return true;
            }})()""")
            until("candidate hour projection rendered", lambda: page.js(
                "document.querySelector('[data-linked-candidates]')?.textContent.includes('Acceptance Hour Candidate') && "
                "document.querySelector('[data-linked-candidates]')?.textContent.includes('16 saat')"))
            candidate_hours = page.js("""(() => {
              const candidate = [...document.querySelectorAll('[data-linked-candidates] li')]
                .find(item => item.textContent.includes('Acceptance Hour Candidate'));
              return {
                projection: candidate?.querySelector('.candidate-hours')?.textContent || '',
                unknowns: candidate?.querySelector('.candidate-hours small')?.textContent || '',
              };
            })()""")
            if "2026-10-26–2026-11-01: 8 → 16 saat" not in candidate_hours["projection"] or \
                    "Yalnız kaydedilmiş vardiya çizelgesi ve önerilen ek vardiya hesaba katıldı; kaydedilmemiş taslaklar dahil değil." not in candidate_hours["unknowns"] or \
                    "Vardiya saat aralıkları toplandı; mola süresi düşülmedi." not in candidate_hours["unknowns"] or \
                    "Haftalık saat sınırı yapılandırılmamış; sınır değerlendirmesi yapılmadı." not in candidate_hours["unknowns"]:
                raise AssertionError(f"Candidate projection or individual unknowns are missing: {candidate_hours}")
            page.js(f"""(() => {{
              const form = document.querySelector('#linked-leave-dialog [data-hourly-leave-form]');
              form.elements.employee_id.value = {json.dumps(str(ceren))};
              form.elements.date.value = '2026-11-02';
              form.elements.start.value = '12:00';
              form.elements.end.value = '14:00';
              form.requestSubmit();
              return true;
            }})()""")
            until("hourly leave preview rendered", lambda: page.js(
                "document.querySelector('[data-hourly-leave-result]')?.textContent.includes('120 dakika')"))
            hourly_preview = page.js("document.querySelector('[data-hourly-leave-result]').textContent")
            if "10:00–12:00" not in hourly_preview or "14:00–18:00" not in hourly_preview or \
                    "yapılandırılmamış" not in hourly_preview or "değişmedi" not in hourly_preview:
                raise AssertionError(f"Hourly leave did not show remaining shift and unconfigured attendance rule: {hourly_preview}")
            page.js("""(() => {
              const form = document.querySelector('#linked-leave-dialog [data-hourly-rule-form]');
              form.elements.description.value = 'Kurumca açıkça tanımlanan örnek puantaj notu';
              form.elements.effective_date.value = '2026-11-01';
              form.requestSubmit();
              return true;
            })()""")
            until("institution rule save finished", lambda: page.js(
                "document.querySelector('#toast')?.textContent.includes('Kurumun saatlik izin puantaj açıklaması kaydedildi')"))
            page.js("""(() => {
              document.querySelector('#linked-leave-dialog [data-hourly-leave-form]').requestSubmit();
              return true;
            })()""")
            until("configured hourly rule rendered", lambda: page.js(
                "document.querySelector('[data-hourly-leave-result]')?.textContent.includes('Kurumca açıkça tanımlanan örnek puantaj notu')"))
            page.click("#linked-leave-dialog button[data-extras-close]")
            page.js("window.operationsTools.open('lifecycle')")
            until("lifecycle form rendered", lambda: page.js(
                "!!document.querySelector('[data-ops-lifecycle]')"))
            page.js(f"""(() => {{
              const form = document.querySelector('[data-ops-lifecycle]');
              form.elements.employee_id.value = {json.dumps(str(exit_person['id']))};
              form.elements.action.value = 'exit';
              form.elements.effective_date.value = {json.dumps(exit_effective)};
              form.requestSubmit();
              return true;
            }})()""")
            until("offboarding checklist rendered", lambda: page.js(
                "document.querySelector('[data-lifecycle-result]')?.textContent.includes('Acceptance access card')"))
            exit_preview = page.js("""(() => {
              const text = document.querySelector('[data-lifecycle-result]')?.textContent || '';
              return {
                text,
                errors: window.__workforceRuntimeErrors,
                apply: !!document.querySelector('[data-lifecycle-apply]'),
              };
            })()""")
            for expected in (
                "Acceptance Exit", "Acceptance access card", "Acceptance pending.pdf",
                "Personel portalı", "Paylaşım bağlantısı",
                date.fromisoformat(exit_effective).strftime("%d.%m.%Y"),
            ):
                if expected not in exit_preview["text"]:
                    raise AssertionError(f"Offboarding preview omitted {expected!r}: {exit_preview}")
            if not exit_preview["apply"] or exit_preview["errors"]:
                raise AssertionError(f"Offboarding preview was not usable in the UI: {exit_preview}")
            fixed_target = fixed_effective
            page.js(f"""(() => {{
              const form = document.querySelector('#operations-center [data-ops-lifecycle]');
              form.elements.employee_id.value = {json.dumps(str(fixed_person['id']))};
              form.elements.employee_id.dispatchEvent(new Event('change', {{ bubbles: true }}));
              form.elements.action.value = 'fixed_pattern';
              form.elements.action.dispatchEvent(new Event('change', {{ bubbles: true }}));
              form.elements.effective_date.value = {json.dumps(fixed_effective.isoformat())};
              form.querySelector('[data-lifecycle-fixed-day="{fixed_target.weekday()}"]').value = 'H';
              form.requestSubmit();
              return true;
            }})()""")
            until("future fixed-pattern impact rendered", lambda: page.js(
                f"[...document.querySelectorAll('[data-lifecycle-result] tbody tr')].some(row => "
                f"row.cells[0]?.textContent.trim() === {json.dumps(fixed_target.strftime('%d.%m.%Y'))} && "
                "row.textContent.includes('X') && row.textContent.includes('H'))"))
            fixed_preview = page.js(f"""(() => {{
              const result = document.querySelector('[data-lifecycle-result]');
              const row = [...(result?.querySelectorAll('tbody tr') || [])]
                .find(item => item.cells[0]?.textContent.trim() === {json.dumps(fixed_target.strftime('%d.%m.%Y'))});
              return {{
                text: result?.textContent || '',
                row: row ? [...row.cells].map(cell => cell.textContent.trim()) : [],
                apply: !!result?.querySelector('[data-lifecycle-apply]'),
                errors: window.__workforceRuntimeErrors,
              }};
            }})()""")
            if fixed_preview["row"] != [fixed_target.strftime("%d.%m.%Y"), "X", "H"] or \
                    not fixed_preview["apply"] or "geçmiş vardiyalar yeniden yazılmaz" not in fixed_preview["text"].casefold() or \
                    fixed_preview["errors"]:
                raise AssertionError(f"Future fixed-pattern preview omitted old/new codes or preservation: {fixed_preview}")
            page.js("window.confirm = () => true; true")
            page.click("#operations-center [data-lifecycle-apply]")
            until("future fixed-pattern change applied", lambda: page.js(
                "document.querySelector('#toast')?.textContent.includes('Personel yaşam döngüsü güncellendi')"))
            fixed_codes = page.js(f"""Promise.all([
              fetch('/api/attendance?month={fixed_target.strftime('%Y-%m')}').then(response => response.json()),
              fetch('/api/employee-profile?employee_id={fixed_person['id']}').then(response => response.json()),
            ]).then(([data, profile]) => {{
                const row = data.attendance.rows.find(item => item.employee_id === {fixed_person['id']});
                return {{
                  current: row?.codes[{json.dumps(fixed_target.isoformat())}],
                  prior: row?.codes[{json.dumps((fixed_target - timedelta(days=1)).isoformat())}],
                  changes: profile.profile.fixed_pattern_changes,
                }};
              }})""")
            if fixed_codes["current"] != "H" or fixed_codes["prior"] != "X" or \
                    fixed_codes["changes"][-1]["effective_date"] != fixed_effective.isoformat():
                raise AssertionError(f"Fixed leave was not applied only from its future date: {fixed_codes}")
            page.js("window.operationsTools.open('assignments')")
            until("temporary assignment form rendered", lambda: page.js(
                "!!document.querySelector('#operations-center[open] [data-temporary-assignment-form]')"))
            page.js(f"""(() => {{
              const form = document.querySelector('#operations-center [data-temporary-assignment-form]');
              form.elements.employee_id.value = {json.dumps(str(ceren))};
              form.elements.location_type.value = 'section';
              form.elements.location.value = 'TEMİZLİK';
              form.elements.start_date.value = '2026-11-01';
              form.elements.end_date.value = '2026-11-01';
              form.elements.start_time.value = '02:00';
              form.elements.end_time.value = '03:00';
              form.requestSubmit();
              return true;
            }})()""")
            until("conflicting assignment preview rendered", lambda: page.js(
                "document.querySelector('[data-assignment-preview-result]')?.textContent.includes('kaydedilemez')"))
            assignment_conflict = page.js("""(() => {
              const result = document.querySelector('[data-assignment-preview-result]');
              return {
                text: result?.textContent || '',
                save: !!result?.querySelector('[data-assignment-save]'),
                errors: window.__workforceRuntimeErrors,
              };
            })()""")
            if assignment_conflict["save"] or "MARKET" not in assignment_conflict["text"] or \
                    "02:00–03:00" not in assignment_conflict["text"] or assignment_conflict["errors"]:
                raise AssertionError(f"Concurrent section assignment was not blocked with its overlap: {assignment_conflict}")
            page.js("""(() => {
              const form = document.querySelector('#operations-center [data-temporary-assignment-form]');
              form.elements.start_time.value = '10:00';
              form.elements.end_time.value = '11:00';
              form.requestSubmit();
              return true;
            })()""")
            until("clear assignment preview rendered", lambda: page.js(
                "document.querySelector('[data-assignment-preview-result] [data-assignment-save]') !== null"))
            page.click('#operations-center [data-assignment-save]')
            until("temporary assignment saved and listed", lambda: page.js(
                "document.querySelector('#operations-center[open] .extras-body')?.textContent.includes('TEMİZLİK') && "
                "document.querySelector('#operations-center[open] .extras-body')?.textContent.includes('Acceptance Ceren')"))
            assignment_saved = page.js("""({
              text: document.querySelector('#operations-center[open] .extras-body')?.textContent || '',
              toast: document.querySelector('#toast')?.textContent || '',
              errors: window.__workforceRuntimeErrors,
            })""")
            if "Geçici görevlendirme kaydedildi" not in assignment_saved["toast"] or \
                    "10:00–11:00" not in assignment_saved["text"] or \
                    "Acceptance Ceren" not in assignment_saved["text"] or assignment_saved["errors"]:
                raise AssertionError(f"Saved temporary assignment was not retained in its dated list: {assignment_saved}")
            page.click("#operations-center button[data-extras-close]")
            page.js("openRotation()")
            until("rotation dialog opened", lambda: page.js(
                "!!document.querySelector('#rotation-dialog[open] #rotation-person')"))
            page.js(f"""(() => {{
              const person = document.querySelector('#rotation-person');
              for (const option of person.options) option.selected = option.value === {json.dumps(str(ada))};
              person.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }})()""")
            until("rotation person loaded", lambda: page.js(
                "document.querySelector('#rotation-dialog[open] #rotation-steps')?.textContent.includes('07:00–15:00')"))
            page.js("""(() => {
              window.__rotationBulk = null;
              window.__rotationConfirm = '';
              window.confirm = message => { window.__rotationConfirm = message; return true; };
              window.previewWorkbookBulk = async (kind, cells, values) => {
                window.__rotationBulk = { kind, cells, values };
              };
              document.querySelector('#rotation-dialog[open] #rotation-resume-mode').value = 'continue';
            })()""")
            page.click("#rotation-dialog[open] #rotation-apply")
            until("continue-mode cycle preview captured", lambda: page.js("!!window.__rotationBulk"))
            continued = page.js("""({
              prompt: window.__rotationConfirm,
              preview: window.__rotationBulk,
              errors: window.__workforceRuntimeErrors,
            })""")
            if "2026-11-03" not in continued["prompt"] or "2026-11-04" not in continued["prompt"]:
                raise AssertionError(f"Rotation exception/resume dates were not disclosed: {continued}")
            continue_day = next((index for index, cell in enumerate(continued["preview"]["cells"])
                                 if cell["date"] == "2026-11-04"), None)
            if continue_day is None or continued["preview"]["values"][continue_day].get("kind") != "work" or continued["errors"]:
                raise AssertionError(f"Continue mode did not resume the cycle step after leave: {continued}")

            page.js("openRotation()")
            until("calendar-mode cycle dialog opened", lambda: page.js(
                "!!document.querySelector('#rotation-dialog[open] #rotation-resume-mode')"))
            page.js(f"""(() => {{
              const person = document.querySelector('#rotation-person');
              for (const option of person.options) option.selected = option.value === {json.dumps(str(ada))};
              person.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }})()""")
            until("calendar-mode profile loaded", lambda: page.js(
                "document.querySelector('#rotation-dialog[open] #rotation-steps')?.textContent.includes('07:00–15:00')"))
            page.js("""(() => {
              window.__rotationBulk = null;
              document.querySelector('#rotation-dialog[open] #rotation-resume-mode').value = 'calendar';
            })()""")
            page.click("#rotation-dialog[open] #rotation-apply")
            until("calendar-mode cycle preview captured", lambda: page.js("!!window.__rotationBulk"))
            calendar_rotation = page.js("""({
              preview: window.__rotationBulk,
              errors: window.__workforceRuntimeErrors,
            })""")
            calendar_day = next((index for index, cell in enumerate(calendar_rotation["preview"]["cells"])
                                 if cell["date"] == "2026-11-04"), None)
            if calendar_day is None or calendar_rotation["preview"]["values"][calendar_day].get("kind") != "weekly_off" or calendar_rotation["errors"]:
                raise AssertionError(f"Calendar mode did not retain the calendar step after leave: {calendar_rotation}")
            page.js("window.dataContinuity.openDuplicates()")
            until("duplicate identity review rendered", lambda: page.js(
                "document.querySelector('#data-continuity-dialog[open]')?.textContent.includes('Acceptance Match One')"))
            page.js("""(() => {
              const decision = document.querySelector('#data-continuity-dialog select[id^="continuity-match-decision-"]');
              decision.value = 'different';
              const reason = document.querySelector('#data-continuity-dialog input[id^="continuity-match-reason-"]');
              reason.value = 'Ayrı sicil kaydı doğrulandı.';
            })()""")
            page.click('#data-continuity-dialog button[id^="continuity-match-save-"]')
            until("identity decision saved or an error shown", lambda: page.js(
                "document.querySelector('#data-continuity-dialog[open]')?.textContent.includes('Ayrı sicil kaydı doğrulandı.') || "
                "(() => { const status = document.querySelector('#data-continuity-dialog .continuity-status')?.textContent || ''; "
                "return !!status && status !== 'İşlem sürüyor…'; })()"))
            identity_result = page.js(f"""(() => ({{
              text: document.querySelector('#data-continuity-dialog[open]')?.textContent || '',
              status: document.querySelector('#data-continuity-dialog .continuity-status')?.textContent || '',
              peopleRetained: [{match_left}, {match_right}].every(id => state.people.some(person => person.id === id)),
              errors: window.__workforceRuntimeErrors,
            }}))()""")
            if "Farklı kişiler" not in identity_result["text"] or "MARKET" not in identity_result["text"] or \
                    not identity_result["peopleRetained"] or identity_result["errors"]:
                raise AssertionError(f"Identity decision/history or profile details are missing: {identity_result}")
            page.click('#data-continuity-dialog button[aria-label="Kapat"]')
            page.js("window.operationsTools.open('quality')")
            until("plan-quality tool opened", lambda: page.js(
                "!!document.querySelector('#operations-center[open] [data-plan-snapshot=\"b\"]')"))
            quality_text = page.js("document.querySelector('#operations-center[open] .extras-body').textContent")
            if "Acceptance Ceren · kasa" not in quality_text or \
                    "deneyimli çalışanla örtüşmeyen saatler: 02:00–06:00" not in quality_text:
                raise AssertionError(f"Mentorship was not based on shared task-hours: {quality_text}")
            if "Karşılanamayan tercih; planı kaydetmeyi engellemez" not in quality_text or \
                    "hafta sonu kısıtı" not in quality_text:
                raise AssertionError(f"Unsatisfied preference did not include its reason: {quality_text}")
            with Store(db) as replan_store:
                original_compliance = replan_store.compliance_settings()
                replan_store.save_compliance_settings({"min_staffing_windows": []})
            page.js(f"""window.easyUsability.previewBulk("schedule", [
              {{rowId: "acceptance-rotation", employee_id: {ada}, section: "MARKET", date: "2026-11-02"}},
              {{rowId: "acceptance-relief", employee_id: {bora}, section: "MARKET", date: "2026-11-02"}},
              {{rowId: "acceptance-overlap", employee_id: {ceren}, section: "MARKET", date: "2026-11-02"}}
            ], Array.from({{length: 3}}, () => ({{kind: "work", start: "00:00", end: "06:00"}})))""")
            until("protected infeasible replan preview rendered", lambda: page.js(
                "document.querySelector('#easy-bulk-preview[open]')?.textContent.includes('plan bulgusu var')"))
            protected_replan = page.js("""(() => {
              const dialog = document.querySelector('#easy-bulk-preview[open]');
              const text = dialog?.textContent || '';
              const changes = [...(dialog?.querySelectorAll('tbody tr') || [])].map(row =>
                [...row.cells].map(cell => cell.textContent.trim()));
              return {
                text,
                changes,
                errors: window.__workforceRuntimeErrors,
              };
            })()""")
            if "1 korunan hücre atlandı" not in protected_replan["text"] or \
                    "Acceptance Ceren · MARKET · 2026-11-02" not in protected_replan["text"] or \
                    not any("Acceptance Bora" in row[0] for row in protected_replan["changes"]) or \
                    not any("Acceptance Ada" in row[0] for row in protected_replan["changes"]) or \
                    "MARKET: 02.11 00:00–06:00 aralığında 2; en az 3 çalışan gerekir." \
                    not in protected_replan["text"] or \
                    protected_replan["errors"]:
                raise AssertionError(f"Protected replan did not expose the remaining staffing constraint: {protected_replan}")
            protected_replan_saved = page.js(f"""fetch('/api/schedule?month={month}')
              .then(response => response.json())
              .then(schedule => ({{
                revision: schedule.revision,
                protectedShift: schedule.entries.find(row => row.row_id === 'acceptance-overlap')
                  ?.days['2026-11-02'],
              }}))""")
            expected_protected_shift = next(
                row["days"]["2026-11-02"] for row in before["entries"]
                if row["row_id"] == "acceptance-overlap"
            )
            if protected_replan_saved["revision"] != before["revision"] or \
                    protected_replan_saved["protectedShift"] != expected_protected_shift:
                raise AssertionError(f"Protected replan preview changed the saved assignment: {protected_replan_saved}")
            page.js("document.querySelector('#easy-bulk-preview')?.close(); true")
            with Store(db) as replan_store:
                replan_store.save_compliance_settings(original_compliance)
            page.js("""(() => {
              const row = state.schedule.entries.find(item => item.employee_id === state.people
                .find(person => person.name === 'Acceptance Bora')?.id);
              if (!row) throw new Error('Bora schedule row is missing');
              row.days['2026-11-05'] = {kind: 'work', start: '08:00', end: '16:00'};
              return true;
            })()""")
            page.click('#operations-center [data-plan-snapshot="b"]')
            page.js("""(() => {
              const row = state.schedule.entries.find(item => item.employee_id === state.people
                .find(person => person.name === 'Acceptance Bora')?.id);
              delete row.days['2026-11-05'];
              return true;
            })()""")
            page.click('#operations-center [data-plan-compare]')
            until("A/B publication impact rendered", lambda: page.js(
                "document.querySelector('[data-plan-results]')?.textContent.includes('2026-11-05')"))
            plan_impact = page.js("""(() => {
              const rows = [...document.querySelectorAll('[data-plan-results] tbody tr')];
              const affected = rows.find(row => row.textContent.includes('Yayımlanmış vardiyası değişecek kişi'));
              const earliest = rows.find(row => row.textContent.includes('En yakın değişiklik tarihi'));
              const days = rows.find(row => row.textContent.includes('En yakın değişikliğe kalan / geçen gün'));
              return {
                text: document.querySelector('[data-plan-results]').textContent,
                affected: affected ? [...affected.cells].slice(1).map(cell => cell.textContent.trim()) : [],
                earliest: earliest ? [...earliest.cells].slice(1).map(cell => cell.textContent.trim()) : [],
                days: days ? [...days.cells].slice(1).map(cell => cell.textContent.trim()) : [],
                errors: window.__workforceRuntimeErrors,
              };
            })()""")
            if plan_impact["affected"] != ["0", "1"] or plan_impact["earliest"][1] != "2026-11-05" or \
                    not plan_impact["days"][1].startswith("-") or plan_impact["errors"]:
                raise AssertionError(f"A/B comparison omitted affected people/date proximity: {plan_impact}")
            page.click('#operations-center button[data-extras-close]')
            move_request = page.js(f"""(async () => {{
              const createdResponse = await fetch('/api/linked-leave/requests', {{
                method: 'POST', headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{
                  employee_id: {leave_move_person['id']}, pattern: 'single',
                  start: '2026-11-05', end: '2026-11-05',
                  weekdays: [], kind: 'annual_leave', note: 'Taşıma kabul senaryosu',
                }}),
              }});
              const created = await createdResponse.json();
              if (!createdResponse.ok) throw new Error(created.error || 'leave request creation failed');
              const approvalResponse = await fetch(
                `/api/linked-leave/requests/${{encodeURIComponent(created.request.id)}}/approve`, {{
                  method: 'POST', headers: {{'Content-Type': 'application/json'}},
                  body: JSON.stringify({{revision: created.request.revision, status: 'approved'}}),
                }});
              const approval = await approvalResponse.json();
              if (!approvalResponse.ok) throw new Error(approval.error || 'leave request approval failed');
              return created.request.id;
            }})()""")
            page.js("window.linkedLeave.open(); true")
            until("approved move request listed", lambda: page.js(
                f"!!document.querySelector('#linked-leave-dialog [data-linked-action=\"move\"][data-id={json.dumps(move_request)}]')"))
            page.click(f'#linked-leave-dialog [data-linked-action="move"][data-id="{move_request}"]')
            until("atomic leave move form opened", lambda: page.js(
                "!!document.querySelector('#linked-leave-dialog [data-linked-move-form]')"))
            page.js("""(() => {
              const form = document.querySelector('#linked-leave-dialog [data-linked-move-form]');
              form.elements.start.value = '2026-11-07';
              form.elements.end.value = '2026-11-07';
              form.requestSubmit();
              return true;
            })()""")
            until("old and new leave dates compared", lambda: page.js(
                "document.querySelector('[data-linked-move-result]')?.textContent.includes('2026-11-07') && "
                "document.querySelector('[data-linked-move-result] [data-linked-move-apply]') !== null"))
            move_preview = page.js("""({
              text: document.querySelector('[data-linked-move-result]')?.textContent || '',
              canApply: !!document.querySelector('[data-linked-move-result] [data-linked-move-apply]'),
              errors: window.__workforceRuntimeErrors,
            })""")
            if "2026-11-05" not in move_preview["text"] or "2026-11-07" not in move_preview["text"] or \
                    "Eski ilişkili gün: 1" not in move_preview["text"] or \
                    "Yeni ilişkili gün: 1" not in move_preview["text"] or \
                    not move_preview["canApply"] or move_preview["errors"]:
                raise AssertionError(f"Leave move preview did not compare both changes: {move_preview}")
            page.js("window.confirm = () => true; true")
            page.click("#linked-leave-dialog [data-linked-move-apply]")
            until("approved leave moved in the UI", lambda: page.js(
                "document.querySelector('#toast')?.textContent.includes('İzin tarihleri tek işlemde taşındı')"))
            moved_leave = page.js(f"""Promise.all([
              fetch('/api/schedule?month=2026-11').then(response => response.json()),
              fetch('/api/linked-leave/requests?month=2026-11').then(response => response.json()),
            ]).then(([schedule, requests]) => {{
              const row = schedule.entries.find(item => item.employee_id === {leave_move_person['id']});
              const request = requests.requests.find(item => item.id === {json.dumps(move_request)});
              return {{
                oldCell: row?.days?.['2026-11-05'] || null,
                newCell: row?.days?.['2026-11-07'] || null,
                dates: [request?.start, request?.end, request?.status],
                errors: window.__workforceRuntimeErrors,
              }};
            }})""")
            if moved_leave["oldCell"] is not None or moved_leave["newCell"] != {"kind": "annual_leave"} or \
                    moved_leave["dates"] != ["2026-11-07", "2026-11-07", "approved"] or moved_leave["errors"]:
                raise AssertionError(f"Leave move left both or neither cell, or lost its approval: {moved_leave}")
            if page.js("window.__workforceRuntimeErrors.length") != 0:
                raise AssertionError(f"JavaScript runtime errors during leave move: {page.js('window.__workforceRuntimeErrors')}")
            page.click("#linked-leave-dialog button[data-extras-close]")
            page.js(f"window.personnelHome.open({exit_person['id']})")
            until("personnel asset panel rendered", lambda: page.js(
                "document.querySelector('#personnel-home-dialog[open]')?.textContent.includes('Acceptance access card')"))
            exit_card_gaps = page.js("""document.querySelector(
              '#personnel-home-dialog .personnel-card-meter small')?.textContent || ''""")
            if exit_card_gaps != "Eksik: Belge, Bölüm" or any(
                    private_label in exit_card_gaps for private_label in ("Telefon", "Doğum", "Ücret", "Yetkinlik")):
                raise AssertionError(f"Document/section duty showed wrong required fields: {exit_card_gaps}")
            page.click('#personnel-home-dialog [data-personnel-asset-return]')
            until("asset return recorded", lambda: page.js(
                "!document.querySelector('#personnel-home-dialog[open] [data-personnel-asset-return]')"))
            page.js("""(() => {
              const form = document.querySelector('#personnel-home-dialog [data-personnel-asset-form]');
              form.elements.namedItem('item').value = 'Acceptance headset';
              form.elements.namedItem('note').value = 'Sentetik kabul kaydı';
              form.requestSubmit();
              return true;
            })()""")
            until("asset issue retained with prior return", lambda: page.js(
                "document.querySelector('#personnel-home-dialog[open]')?.textContent.includes('Acceptance headset') && "
                "document.querySelector('#personnel-home-dialog[open]')?.textContent.includes('Acceptance access card')"))
            asset_text = page.js("document.querySelector('#personnel-home-dialog[open]').textContent")
            if "İade bekliyor" not in asset_text or page.js("window.__workforceRuntimeErrors.length") != 0:
                raise AssertionError(f"Asset issue/return history did not remain visible: {asset_text}")
            page.click('#personnel-home-dialog button[aria-label="Kapat"]')
            page.click('button[data-view="attendance"]')
            until("attendance balances rendered", lambda: page.js(
                "document.querySelector('#attendance-balance-report [data-balance-ledger]') !== null"))
            page.js(f"""(() => {{
              const button = document.querySelector('#attendance-balance-report [data-balance-ledger="{ceren}"]');
              if (!button) throw new Error('Ceren balance ledger button is missing');
              button.click();
              return true;
            }})()""")
            until("leave movement ledger rendered", lambda: page.js(
                "document.querySelector('#balance-ledger-result')?.textContent.includes('Açılış bakiyesi düzeltmesi')"))
            ledger_text = page.js("document.querySelector('#balance-ledger-result').textContent")
            if "20 → 18 gün" not in ledger_text or "Elle puantaj düzeltmesi" not in ledger_text or \
                    day not in ledger_text or "İptal (bilgi)" not in ledger_text or \
                    "Hesaplanan kapanış: 17" not in ledger_text or page.js("window.__workforceRuntimeErrors.length") != 0:
                raise AssertionError(f"Leave ledger omitted source history, usage, cancellation, or running balance: {ledger_text}")
            page.js(f"window.personnelHome.open({ceren}); true")
            until("document-renewal personnel panel opened", lambda: page.js(
                "document.querySelector('#personnel-home-dialog[open] [data-personnel-home-action=\"card\"]') !== null"))
            personnel_home_text = page.js("document.querySelector('#personnel-home-dialog[open]').textContent")
            ceren_card_gaps = page.js("""document.querySelector(
              '#personnel-home-dialog .personnel-card-meter small')?.textContent || ''""")
            if ceren_card_gaps != "Eksik: Görev, Yetkinlik" or \
                    any(private_label in ceren_card_gaps for private_label in ("Telefon", "Doğum", "Ücret", "Belge")) or \
                    "Yetkinlik" not in personnel_home_text:
                raise AssertionError(f"Role-specific missing field was not surfaced on the personnel home: {personnel_home_text}")
            page.js("""document.querySelector(
              '#personnel-home-dialog [data-personnel-home-action="card"]').click(); true""")
            until("document renewal editor opened", lambda: page.js(
                "document.querySelector('#employee-card-dialog[open] [data-doc-field=\"renewal_stage\"]') !== null"))
            required_field_state = page.js("""(() => {
              const inputs = [...document.querySelectorAll('#employee-card-dialog [name="duty_required_field"]')];
              const personalInputs = ['phone', 'birth_date', 'monthly_wage'].map(name =>
                document.querySelector(`#employee-card-dialog [name="${name}"]`));
              return {
                selected: inputs.filter(input => input.checked).map(input => input.value).sort(),
                offered: inputs.map(input => input.value),
                explanation: document.querySelector('#employee-card-dialog fieldset .dialog-help')?.textContent || '',
                personalInputsOptional: personalInputs.every(input => !!input && !input.required),
              };
            })()""")
            requirement_explanation = required_field_state["explanation"].casefold()
            if required_field_state["selected"] != ["skills"] or \
                    any(field in required_field_state["offered"] for field in ("phone", "birth_date", "monthly_wage")) or \
                    not all(value.casefold() in requirement_explanation
                            for value in ("Telefon", "doğum tarihi", "ücret")) or \
                    not required_field_state["personalInputsOptional"]:
                raise AssertionError(f"Task requirements included personal fields or lacked an explanation: {required_field_state}")
            for stage in ("requested", "received", "checked"):
                page.js(f"""(() => {{
                  const select = document.querySelector('#employee-card-dialog [data-doc-field="renewal_stage"]');
                  select.value = {json.dumps(stage)};
                  select.dispatchEvent(new Event('change', {{ bubbles: true }}));
                  document.querySelector('#employee-card-dialog #card-form').requestSubmit();
                  return true;
                }})()""")
                time.sleep(0.25)
                until(f"document renewal stage {stage} saved", lambda: page.js(
                    "!!document.querySelector('#employee-card-dialog[open] [data-doc-field=\"renewal_stage\"]') && "
                    "!document.querySelector('#employee-card-dialog[open] #card-form button[type=\"submit\"]').disabled"))
            page.js("""(() => {
              const add = document.querySelector('#employee-card-dialog [data-doc-renew="0"]');
              if (!add) throw new Error('Checked document did not offer a new version');
              add.click();
              const replacement = [...document.querySelectorAll('#employee-card-dialog [data-doc-field="expires"]')][1];
              replacement.value = '2027-10-15';
              replacement.dispatchEvent(new Event('input', { bubbles: true }));
              return true;
            })()""")
            page.js("""document.querySelector('#employee-card-dialog #card-form').requestSubmit(); true""")
            time.sleep(0.25)
            until("new certificate version saved", lambda: page.js(
                f"fetch('/api/employee-profile?employee_id={ceren}').then(r => r.json()).then(data => "
                "data.profile.documents.length === 2 && data.profile.documents.some(item => "
                "item.supersedes === 'acceptance-document-old' && item.expires === '2027-10-15'))"))
            renewal_text = page.js("document.querySelector('#employee-card-dialog #card-doc-list').textContent")
            if "Süresi doldu" not in renewal_text or "Yeni sürüm" not in renewal_text or \
                    "Eski belgeden ayrı kayıt" not in renewal_text or page.js("window.__workforceRuntimeErrors.length") != 0:
                raise AssertionError(f"Expired and renewed document copies were conflated: {renewal_text}")
            page.click('#employee-card-dialog button[aria-label="Kapat"]')
            page.js(f"window.personnelHome.open({exit_person['id']}); true")
            until("weekly exception profile opened", lambda: page.js(
                "document.querySelector('#personnel-home-dialog[open] [data-personnel-home-action=\"card\"]') !== null"))
            page.js("""document.querySelector(
              '#personnel-home-dialog [data-personnel-home-action="card"]').click(); true""")
            until("weekly exception editor opened", lambda: page.js(
                "document.querySelector('#employee-card-dialog[open] [name=\"fixed_exception_start\"]') !== null"))
            page.js("""(() => {
              const form = document.querySelector('#employee-card-dialog #card-form');
              form.elements.namedItem('fixed_exception_enabled').checked = true;
              form.elements.namedItem('fixed_exception_start').value = '2026-11-02';
              const days = [...form.querySelectorAll('[name=\"fixed_exception_day\"]')];
              days[2].value = 'H';
              document.querySelector('[data-fixed-exception-preview]').click();
              return true;
            })()""")
            exception_text = page.js("document.querySelector('[data-fixed-exception-result]').textContent")
            if "2026-11-02" not in exception_text or "2026-11-04: H" not in exception_text or \
                    "2026-11-09 tarihinden itibaren ana haftalık düzene dönülür" not in exception_text:
                raise AssertionError(f"Weekly exception preview omitted affected days or return date: {exception_text}")
            page.js("""document.querySelector('#employee-card-dialog #card-form').requestSubmit(); true""")
            time.sleep(0.25)
            exception_persisted = page.js(f"""fetch('/api/employee-profile?employee_id={exit_person['id']}')
              .then(r => r.json()).then(data => data.profile.fixed_pattern_exceptions?.[0]?.start_date === '2026-11-02')""")
            if not exception_persisted:
                raise AssertionError("Weekly fixed-pattern exception was not saved")
            exception_codes = page.js(f"""fetch('/api/attendance?month=2026-11').then(r => r.json()).then(data => {{
              const row = data.attendance.rows.find(item => item.employee_id === {exit_person['id']});
              return row?.codes['2026-11-04'] === 'H' && row?.codes['2026-11-11'] === 'X';
            }})""")
            if not exception_codes:
                raise AssertionError("The exception week did not apply once and revert to the base pattern")
            page.click('#employee-card-dialog button[aria-label="Kapat"]')
            with Store(db) as store:
                expected_schedule_after_ui[0] = store.schedule_month(month)
            print(f"#20-22, #31 verified for {day}: timeline, break overlay, staffing-impact preview and "
                  f"temporary assignment conflict/record flow; "
                  f"Sunday/Monday boundary and rest shown read-only. #17-19, #24-25, #27, #28-30, #33-34, "
                  f"#36-40 verified via the real UI.",
                  flush=True)
            passed.set()
        except Exception as exc:
            failures.append(exc)
            try:
                print("Browser at failure:", page.js("""({
                  dialog: document.querySelector('dialog[open]')?.id,
                  toast: document.querySelector('#toast')?.textContent,
                  errors: window.__workforceRuntimeErrors || [],
                  openDialogs: [...document.querySelectorAll('dialog[open]')].map(dialog => dialog.id),
                  loaderVisible: !!document.querySelector('#app-loader:not(.done)'),
                  month: state.scheduleMonth,
                })"""), flush=True)
            except Exception:
                pass
        finally:
            window.destroy()

    webview.settings.update(ALLOW_FILE_URLS=False, OPEN_EXTERNAL_LINKS_IN_BROWSER=False)
    server_thread = threading.Thread(target=server.serve_forever)
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
    with Store(db) as store:
        if store.schedule_month(month) != expected_schedule_after_ui[0] or \
                store.schedule_month(prior_month) != prior_before:
            raise AssertionError("The WebView2 session changed a saved schedule after its expected UI updates")
        if operations.shift_activity_report(store, target)["items"] != activities_before:
            raise AssertionError("Previewing a moved break changed the saved break plan")
        if store.attendance_month(month) != attendance_before:
            raise AssertionError("Previewing the break changed attendance or payroll data")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Run this standalone acceptance test on Windows with Edge WebView2.")
    with tempfile.TemporaryDirectory(prefix=".ui-workforce-", dir=ROOT) as directory:
        run(Path(directory))
    print("PASS #17-25, #27, #29-30, #34, #36, #38-40: real WebView2 timeline, linked-leave, staffing, personnel, "
          "plan-quality, and balance-ledger scenarios verified.")
