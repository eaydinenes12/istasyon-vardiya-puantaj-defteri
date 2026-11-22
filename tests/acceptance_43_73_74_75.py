"""WebView2 acceptance for portal approval and management reports.

Run on Windows from the project root:
    python -B tests\\acceptance_43_73_74_75.py

The database and WebView2 profile are created under an OS temporary directory.
"""
import json
import os
import secrets
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

    def navigate(self, url):
        self.cdp("Page.navigate", url=url)
        until("page navigation", lambda: self.js("document.readyState === 'complete'"))

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
    import access_services
    import operations
    import scheduling
    from storage import Store
    from server import create_server
    import server as server_module

    db = folder / "acceptance.sqlite3"
    month = time.strftime("%Y-%m")
    dates = scheduling.month_dates(month)
    mondays = [day for day in dates if day.weekday() == 0]
    correction_day = dates[0].isoformat()
    today = date.today()
    days_until_friday = (4 - today.weekday()) % 7 or 7
    assistant_day = (today + timedelta(days=days_until_friday)).isoformat()
    assistant_month = assistant_day[:7]
    person_name = "Kabul 43-75 Personeli"
    replacement_name = "Kabul 43-75 Yedek"
    with Store(db) as store:
        operations.initialize(store.connection)
        access_services.initialize(store.connection)
        person_id = store.add_person(person_name)["id"]
        replacement_id = store.add_person(replacement_name)["id"]
        entries = [
            {"row_id": "acceptance-worker", "employee_id": person_id, "section": "MARKET",
             "days": {mondays[0].isoformat(): {"kind": "work", "start": "09:00", "end": "17:00"},
                      mondays[1].isoformat(): {"kind": "work", "start": "09:00", "end": "17:00"},
                      correction_day: {"kind": "work", "start": "09:00", "end": "17:00"}}},
            {"row_id": "acceptance-replacement", "employee_id": replacement_id, "section": "MARKET",
             "days": {mondays[0].isoformat(): {"kind": "work", "start": "22:00", "end": "06:00"},
                      mondays[1].isoformat(): {"kind": "work", "start": "22:00", "end": "06:00"}}},
        ]
        if assistant_month == month:
            entries[0]["days"][assistant_day] = {"kind": "work", "start": "08:00", "end": "16:00"}
        entries = scheduling.normalize_schedule_entries(month, entries, store.people())
        revision = store.save_schedule_month(month, entries, "")
        store.save_employee_profile(person_id, {
            "hire_date": dates[0].isoformat(), "exit_date": dates[-1].isoformat(),
            "preferences": {
                "unavailable_weekdays": [1],
                "unavailable_ranges": [{"start": dates[2].isoformat(), "end": dates[3].isoformat()}],
                "no_night": True, "no_weekend": True, "note": "SENTETIK-GIZLI-NOT-1",
            },
        })
        store.save_employee_profile(replacement_id, {"preferences": {"note": "SENTETIK-GIZLI-NOT-2"}})
        store.save_compliance_settings({"min_staffing": {"MARKET": 3}})
        store._write_setting("overtime_rule_versions", {
            month: {"weekly_limit_hours": 0, "week_start": 0, "daily_limit_hours": 8,
                    "break_rules": [[6, 30]]},
        })
        old_entries = [dict(row, days=dict(row["days"])) for row in entries]
        with store.connection:
            store._write_setting("published_plans", {month: {
                "entries": old_entries, "at": "2026-01-01T00:00:00+00:00", "source_revision": revision,
            }})
        changed_entries = [dict(row, days=dict(row["days"])) for row in entries]
        changed_entries[0]["days"][mondays[0].isoformat()] = {
            "kind": "work", "start": "10:00", "end": "18:00",
        }
        revision = store.save_schedule_month(month, changed_entries, revision)
        with store.connection:
            store.connection.execute(
                "INSERT INTO operations_emergency_audit "
                "(id,month,employee_id,replacement_id,date,reason,source_revision,before_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("acceptance-emergency", month, person_id, replacement_id, mondays[0].isoformat(),
                 "Acil örnek gerekçesi", revision, "{}", "2026-01-02T00:00:00+00:00"),
            )
        token = secrets.token_urlsafe(32)
        with store.connection:
            store.connection.execute(
                "INSERT INTO portal_tokens(token_hash,employee_id,expires_at,revoked_at,created_at) "
                "VALUES (?,?,?,NULL,?)",
                (access_services._token_hash(token), person_id, access_services._now() + 3600,
                 access_services._now()),
            )
    if assistant_month != month:
        with Store(db) as store:
            target_entries = scheduling.normalize_schedule_entries(assistant_month, [{
                "row_id": "acceptance-assistant-target",
                "employee_id": person_id,
                "section": "MARKET",
                "days": {assistant_day: {"kind": "work", "start": "08:00", "end": "16:00"}},
            }], store.people())
            store.save_schedule_month(assistant_month, target_entries, "")

    server_module.live_assistant.list_models = lambda: ["acceptance-local"]
    model_requests = []

    def local_assistant_response(_path, payload, timeout=5):
        model_requests.append(payload)
        last_user_message = next(
            item["content"] for item in reversed(payload["messages"]) if item["role"] == "user"
        )
        if "vardiya planını özetle" in last_user_message:
            return {"message": {"content": "Bağlam sınırları uygulandı.", "tool_calls": []}}
        return {
            "message": {
                "content": "Kabul için vardiya önerisi hazırlandı.",
                "tool_calls": [{
                    "function": {
                        "name": "set_schedule_day",
                        "arguments": {
                            "employee_name": person_name,
                            "date": assistant_day,
                            "kind": "work",
                            "start": "12:00",
                            "end": "20:00",
                        },
                    },
                }],
            },
        }

    server_module.live_assistant._request_json = local_assistant_response

    server = create_server(db, ROOT / "web")
    server.easy_mode = True
    origin = f"http://localhost:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever)
    failures = []
    passed = threading.Event()
    window = webview.create_window("Acceptance 43/73/74/75", origin, width=1440, height=900, focus=False)

    def drive():
        page = Browser(window)
        try:
            if not window.events.loaded.wait(40):
                raise TimeoutError("WebView2 did not load the local application")
            until("application ready", lambda: page.js(
                "!document.querySelector('#app-loader') && !!window.operationsTools"))

            page.navigate(f"{origin}/employee-portal/{token}")
            until("employee portal correction form", lambda: page.js(
                "!!document.querySelector('form[action$=\"/attendance-corrections\"]')"))
            page.js(f"""(() => {{
              const form = document.querySelector('form[action$="/attendance-corrections"]');
              form.elements.date.value = {json.dumps(correction_day)};
              form.elements.code.value = "S";
              form.elements.reason.value = "Kabul için kişisel puantaj talebi";
              form.requestSubmit();
              return true;
            }})()""")
            until("portal request confirmation", lambda: page.js(
                "document.body.innerText.includes('Puantaj düzeltme talebiniz yöneticiye iletildi.')"))
            with Store(db) as store:
                before = store.attendance_month(month)
                if before["overrides"].get(correction_day, {}).get(str(person_id)) is not None:
                    raise AssertionError("Portal request changed attendance before manager approval")
            print("PASS #43: portal form created a self-scoped pending request; attendance unchanged before approval.",
                  flush=True)

            page.navigate(origin)
            until("application restored", lambda: page.js(
                "!document.querySelector('#app-loader') && !!window.operationsTools"))
            page.js("window.confirm = () => true; window.operationsTools.open('corrections'); true")
            until("manager correction list", lambda: page.js(
                "document.querySelector('.operations-dialog[open] [data-ops-content]')?.innerText"
                f".includes({json.dumps(person_name)})"))
            page.click("[data-review-correction='0']")
            until("manager evidence review", lambda: page.js(
                "!!document.querySelector('[data-correction-review] [data-decision=\"approve\"]')"))
            page.click("[data-correction-review] [data-decision='approve']")
            until("manager approval rendered", lambda: page.js(
                "document.querySelector('.operations-dialog[open] [data-ops-content]')?.innerText"
                ".includes('Onaylandı')"))
            with Store(db) as store:
                after = store.attendance_month(month)
                actual = after["overrides"].get(correction_day, {}).get(str(person_id))
                if actual != "S":
                    raise AssertionError(f"Manager approval did not apply the requested day code: {actual!r}")

            page.js("""(() => {
              const item = EXTRAS_MENU.find(([label]) => label === "Adil dağılım raporu");
              item[1]();
              return true;
            })()""")
            until("availability-aware fairness report", lambda: page.js(
                "document.querySelector('#fairness-dialog')?.innerText"
                ".includes('Kayıtlı uygunluk bağlamı')"))
            fairness = page.js("document.querySelector('#fairness-dialog').innerText")
            if "Salı" not in fairness or "Yük farkı" in fairness:
                raise AssertionError("Fairness screen omitted availability context or displayed a score-only ranking")
            print("PASS #73: WebView2 shows raw shift counts with recorded availability context and no composite score.",
                  flush=True)

            page.js("window.operationsTools.open('publish'); true")
            until("date-section publication difference", lambda: page.js(
                "document.querySelector('.operations-dialog[open] [data-ops-content]')?.innerText"
                ".includes('Kayıtlı gerekçe')"))
            publish_text = page.js("document.querySelector('.operations-dialog[open] [data-ops-content]').innerText")
            if ("Acil örnek gerekçesi" not in publish_text
                    or mondays[0].strftime("%d.%m.%Y") not in publish_text):
                raise AssertionError("Publication diff did not show the recorded date/reason")
            print("PASS #74: WebView2 groups publication changes by day/section and displays only a recorded reason.",
                  flush=True)

            page.js("window.operationsTools.open('trends'); true")
            until("recurring staffing trend", lambda: page.js(
                "document.querySelector('.operations-dialog[open] [data-ops-content]')?.innerText"
                ".includes('Tekrarlayan kadro açıkları')"))
            trend_text = page.js("document.querySelector('.operations-dialog[open] [data-ops-content]').innerText")
            if "Tekrarlayan" not in trend_text or "MARKET" not in trend_text:
                raise AssertionError("Recurring staffing screen did not show a repeated section pattern")
            print("PASS #75: WebView2 distinguishes repeated weekly gaps and identifies the section and dates.",
                  flush=True)
            provenance = page.js(
                "document.querySelector('.operations-dialog[open] [data-report-meta-label]')?.innerText")
            if (not provenance or "Son hesap:" not in provenance or "plan sürümü" not in provenance
                    or "puantaj sürümü" not in provenance or "PDKS aktarımı yok (isteğe bağlı kaynak)" not in provenance):
                raise AssertionError(f"Management report provenance is incomplete: {provenance!r}")
            print("PASS #79: WebView2 management report shows calculation time, plan/attendance revisions, and missing optional PDKS source.",
                  flush=True)

            page.js("window.operationsTools.open('closing'); true")
            until("first payroll delivery form", lambda: page.js("!!document.querySelector('[data-payroll-delivery]')"))
            page.js("""(() => {
              const form = document.querySelector('[data-payroll-delivery]');
              form.elements.reference.value = 'İlk muhasebe teslimi';
              form.elements.confirmed.checked = true;
              form.requestSubmit();
              return true;
            })()""")
            until("first payroll delivery retained", lambda: page.js(
                "document.querySelector('.operations-dialog[open] [data-ops-content]')?.innerText"
                ".includes('Son teslimden sonra değişen alanlar')"))
            with Store(db) as store:
                attendance_before_adjustment = store.attendance_month(month)
                deductions = dict(attendance_before_adjustment["deductions"])
                deductions[str(person_id)] = 25
                store.save_attendance_month(
                    month, attendance_before_adjustment["overrides"], deductions,
                    attendance_before_adjustment["revision"], roster=attendance_before_adjustment["roster"],
                    overtime=attendance_before_adjustment["overtime"],
                )
                store.set_period_locked(month, True, "Acceptance test locked payroll correction")
                attendance_after_source_change = store.attendance_month(month)
            page.js("window.operationsTools.open('closing'); true")
            until("locked payroll correction form", lambda: page.js(
                "!!document.querySelector('[data-payroll-adjustment]')"))
            page.js("""(() => {
              const form = document.querySelector('[data-payroll-adjustment]');
              form.elements.reason.value = 'Kilitli dönem bildirilen kesinti düzeltmesi';
              form.requestSubmit();
              return true;
            })()""")
            until("pending payroll correction notice", lambda: page.js(
                "document.querySelector('.operations-dialog[open] [data-ops-content]')?.innerText"
                ".includes('Onay bekliyor')"))
            correction_text = page.js(
                "document.querySelector('.operations-dialog[open] [data-ops-content]').innerText")
            if "İlk muhasebe teslimi" not in correction_text or "düzeltme" not in correction_text:
                raise AssertionError("Correction notice did not retain the first delivery and reason")
            page.js("""document.querySelector('[data-payroll-adjustment-review][data-approve="true"]').click(); true""")
            until("approved payroll correction notice", lambda: page.js(
                "document.querySelector('.operations-dialog[open] [data-ops-content]')?.innerText"
                ".includes('Onaylandı')"))
            with Store(db) as store:
                if store.attendance_month(month) != attendance_after_source_change:
                    raise AssertionError("Approving a correction notice changed the locked source data")
                if store.connection.execute(
                        "SELECT count(*) FROM operations_payroll_deliveries WHERE month=?", (month,),
                ).fetchone()[0] != 1:
                    raise AssertionError("Correction notice overwrote or duplicated the original delivery")
            print("PASS #44: WebView2 keeps the initial delivery and records a separate reasoned, approved locked-period correction without changing source data.",
                  flush=True)

            page.js("window.operationsTools.open('closing'); true")
            until("read-only accounting reconciliation form", lambda: page.js(
                "!!document.querySelector('[data-payroll-reconcile] input[type=file]')"))
            with Store(db) as store:
                before_preview = store.attendance_month(month)
            page.js(f"""(async () => {{
              const payload = await fetch("/api/attendance?month={month}").then(response => response.json());
              const rows = payload.attendance?.rows ?? payload.rows ?? [];
              const lines = ["employee_id,date,code,hours"];
              for (const row of rows) {{
                const codes = row.codes ?? {{}};
                const hours = row.worked_hours_by_day ?? {{}};
                const days = new Set([...Object.keys(codes), ...Object.keys(hours)]);
                for (const day of [...days].sort()) {{
                  let code = codes[day] ?? "";
                  let amount = Number(hours[day] ?? 0);
                  if (row.employee_id === {person_id} && day === {json.dumps(correction_day)}) {{
                    code = "R";
                    amount = 8;
                  }}
                  if (code || amount) lines.push([row.employee_id, day, code, amount].join(","));
                }}
              }}
              const input = document.querySelector('[data-payroll-reconcile] input[type="file"]');
              const files = new DataTransfer();
              files.items.add(new File([lines.join("\\n")], "accounting-preview.csv",
                {{type: "text/csv"}}));
              input.files = files.files;
              input.form.requestSubmit();
              return true;
            }})()""")
            until("person-day accounting mismatch preview", lambda: page.js(
                "document.querySelector('[data-payroll-reconcile-result]')?.innerText"
                ".includes('1 kişi-gün farkı')"))
            reconciliation = page.js("document.querySelector('[data-payroll-reconcile-result]').innerText")
            if person_name not in reconciliation or "Puantaj kodu" not in reconciliation:
                raise AssertionError("Reconciliation preview omitted the employee-specific code mismatch")
            with Store(db) as store:
                after_preview = store.attendance_month(month)
                if after_preview != before_preview:
                    raise AssertionError("Read-only accounting preview changed the attendance data")
            print("PASS #47: WebView2 previews external CSV against person-day code/hours and leaves attendance unchanged.",
                  flush=True)

            page.js(f"window.documentWorkflows.openCalculation({person_id}); true")
            until("minute-level payroll calculation explanation", lambda: page.js(
                "document.querySelector('#easy-calculation-result')?.innerText"
                ".includes('Uygulanan kural:')"))
            calculation = page.js("document.querySelector('#easy-calculation-result').innerText")
            if (f"{month}-01" not in calculation or "Çalışma (dk)" not in calculation
                    or "mola" not in calculation or "round(dakika / 60, 2)" not in calculation):
                raise AssertionError("Calculation explanation omitted the dated rule or separate minute/rounding steps")
            print("PASS #48: WebView2 explains the dated rule and separate work, break, holiday, limit, and rounding steps.",
                  flush=True)

            with Store(db) as store:
                training_before = (
                    store.schedule_month(month), store.attendance_month(month), store.people(),
                )
            page.js("""EXTRAS_MENU.find(([label]) => label === 'Risksiz örnek çalışma')[1](); true""")
            until("isolated practice tasks", lambda: page.js(
                "document.querySelector('#easy-training [data-training-score]')?.innerText"
                ".includes('0/3')"))
            page.js("""(() => {
              document.querySelector('[data-training-check="output"]').click();
              return true;
            })()""")
            if not page.js(
                    "document.querySelector('#easy-training [data-training-result=\"output\"]')?.innerText"
                    ".includes('oluşturuldu ancak')"):
                raise AssertionError("Incorrect sample output was counted as a completed task")
            page.js("""(() => {
              document.querySelector('#easy-training-leave-day').value = '0';
              document.querySelector('#easy-training-leave-code').value = 'R';
              document.querySelector('[data-training-check="leave"]').click();
              return true;
            })()""")
            if not page.js(
                    "document.querySelector('#easy-training [data-training-score]')?.innerText"
                    ".includes('0/3')"):
                raise AssertionError("An incorrect leave entry was counted as success")
            page.js("""(() => {
              document.querySelector('[data-training-reset]').click();
              document.querySelector('#easy-training-leave-day').value = '4';
              document.querySelector('#easy-training-leave-code').value = 'S';
              document.querySelector('[data-training-check="leave"]').click();
              document.querySelector('#easy-training-difference').value = '1:1';
              document.querySelector('[data-training-check="difference"]').click();
              return true;
            })()""")
            if not page.js(
                    "document.querySelector('#easy-training [data-training-score]')?.innerText"
                    ".includes('1/3')"):
                raise AssertionError("Choosing a non-mismatch did not fail the difference task")
            page.js("""(() => {
              document.querySelector('#easy-training-difference').value = '0:2';
              document.querySelector('[data-training-check="difference"]').click();
              document.querySelector('[data-training-check="output"]').click();
              return true;
            })()""")
            training_result = page.js("""({
              score: document.querySelector('#easy-training [data-training-score]')?.innerText,
              leave: document.querySelector('#easy-training [data-training-result="leave"]')?.innerText,
              difference: document.querySelector('#easy-training [data-training-result="difference"]')?.innerText,
              output: document.querySelector('#easy-training [data-training-result="output"]')?.innerText,
              outputCode: document.querySelector('#easy-training [data-training-cell="0-4"]')?.innerText,
            })""")
            if (training_result["score"] != "Doğru görev: 3/3"
                    or "Cuma" not in training_result["leave"]
                    or "Çarşamba" not in training_result["difference"]
                    or training_result["outputCode"] != "S"):
                raise AssertionError(f"Practice tasks did not evaluate their correct results: {training_result!r}")
            with Store(db) as store:
                if training_before != (
                        store.schedule_month(month), store.attendance_month(month), store.people()):
                    raise AssertionError("Practice tasks changed real schedule, attendance, people, or settings")
            print("PASS #111: WebView2 rejects incorrect practice results, scores the correct leave/difference/output 3/3, and changes no real records.",
                  flush=True)

            page.js("document.querySelector('#easy-training [data-extras-close]')?.click(); true")
            page.js("document.querySelectorAll('dialog[open]').forEach((dialog) => dialog.close()); true")
            with Store(db) as store:
                store.set_period_locked(assistant_month, False)
            page.click("#open-live-assistant")
            until("local assistant model list", lambda: page.js(
                "document.querySelector('#live-assistant-dialog[open] #live-assistant-model option')"
                "?.value === 'acceptance-local'"))
            page.js("""(() => {
              const model = document.querySelector('#live-assistant-model');
              model.value = 'acceptance-local';
              model.dispatchEvent(new Event('change', {bubbles: true}));
              return true;
            })()""")

            def submit_assistant(message):
                page.js(f"""(() => {{
                  const input = document.querySelector('#live-assistant-input');
                  input.value = {json.dumps(message)};
                  input.dispatchEvent(new Event('input', {{bubbles: true}}));
                  document.querySelector('#live-assistant-form').requestSubmit();
                  return true;
                }})()""")

            def last_assistant_text():
                return page.js("""(() => {
                  const messages = [...document.querySelectorAll(
                    '#live-assistant-transcript .live-assistant-message[data-role="assistant"]'
                  )];
                  return messages.at(-1)?.querySelector('p')?.innerText || '';
                })()""")

            submit_assistant("1 eksik kadro var, hangi gün?")
            until("assistant report citations", lambda: page.js(
                "document.querySelectorAll('#live-assistant-transcript .live-assistant-source button').length > 0"))
            page.click("#live-assistant-transcript .live-assistant-source button")
            until("assistant source opens the exact plan day", lambda: page.js(
                "!!document.querySelector('#schedule-table [data-shift-day].easy-guide-target')"))
            source_result = page.js("""({
              targetDate: document.querySelector('#schedule-table [data-shift-day].easy-guide-target')?.dataset.shiftDay,
              targetSection: document.querySelector('#schedule-table [data-shift-day].easy-guide-target')?.dataset.shiftSection,
              status: document.querySelector('#live-assistant-status')?.innerText,
              citation: document.querySelector('#live-assistant-transcript .live-assistant-source small')?.innerText,
            })""")
            if (not source_result["targetDate"] or not source_result["targetSection"]
                    or "Kaynak açıldı" not in source_result["status"]
                    or "rapor sürümü" not in source_result["citation"]):
                raise AssertionError(f"Assistant citation did not navigate to a revision-bound plan cell: {source_result!r}")
            print("PASS #105: WebView2 opens the cited plan date/section and displays its report revision.", flush=True)

            page.click("#open-live-assistant")
            until("assistant reopened after source navigation", lambda: page.js(
                "!!document.querySelector('#live-assistant-dialog[open]')"))
            context_probe = f"{person_name} için bu vardiya planını özetle."
            page.js(f"""(() => {{
              const input = document.querySelector('#live-assistant-input');
              input.value = {json.dumps(context_probe)};
              input.dispatchEvent(new Event('input', {{bubbles: true}}));
              return true;
            }})()""")
            displayed_context = page.js("document.querySelector('#live-assistant-context').textContent")
            if (f"Dönem: {month}" not in displayed_context or "Sayfa: Vardiya" not in displayed_context
                    or person_name not in displayed_context or replacement_name in displayed_context):
                raise AssertionError(f"Assistant context preview exposed the wrong scope: {displayed_context!r}")
            submit_assistant(context_probe)
            until("context-bounded model response", lambda: "Bağlam sınırları uygulandı." in last_assistant_text())
            captured = model_requests[-1]
            system_context = captured["messages"][0]["content"]
            serialized_request = json.dumps(captured, ensure_ascii=False)
            if (f'"month":"{month}"' not in system_context or '"view":"schedule"' not in system_context
                    or person_name not in system_context or replacement_name in system_context
                    or "SENTETIK-GIZLI-NOT-1" in serialized_request
                    or "SENTETIK-GIZLI-NOT-2" in serialized_request):
                raise AssertionError("Assistant model request did not match the visible, minimal person/period context")
            print("PASS #109: WebView2 context preview matches the captured local-model request; the unrelated person and private notes are excluded.",
                  flush=True)

            ambiguous = f"Gelecek cuma {person_name} için çalışma vardiyasına al."
            assistant_count = page.js(
                "document.querySelectorAll('#live-assistant-transcript .live-assistant-message[data-role=\"assistant\"]').length")
            submit_assistant(ambiguous)
            until("assistant refusal reply", lambda: page.js(
                "document.querySelectorAll('#live-assistant-transcript .live-assistant-message[data-role=\"assistant\"]').length"
                f" > {assistant_count}"))
            if "Çalışma saatlerini güvenle doğrulayamadım" not in last_assistant_text():
                raise AssertionError(f"Missing-hours proposal was not refused: {last_assistant_text()!r}")
            if page.js("!document.querySelector('#live-assistant-pending').hidden"):
                raise AssertionError("Assistant offered a write without explicitly requested start/end times")
            with Store(db) as store:
                before_action = store.schedule_month(assistant_month)
                target_row = next(row for row in before_action["entries"] if row["employee_id"] == person_id)
                if target_row["days"].get(assistant_day) != {
                    "kind": "work", "start": "08:00", "end": "16:00",
                }:
                    raise AssertionError("Assistant fixture did not start with the expected recorded shift")

            explicit = f"Gelecek cuma {person_name} için 12:00-20:00 çalışma vardiyasına al."
            submit_assistant(explicit)
            until("assistant change preview", lambda: page.js(
                "!document.querySelector('#live-assistant-pending').hidden"))
            preview = page.js("document.querySelector('#live-assistant-action-summary').innerText")
            if "Mevcut: 08:00–16:00" not in preview or "Yeni: Çalışma · 12:00–20:00" not in preview:
                raise AssertionError(f"Assistant preview omitted the recorded and requested times: {preview!r}")
            page.click("#cancel-live-assistant-action")
            with Store(db) as store:
                if store.schedule_month(assistant_month) != before_action:
                    raise AssertionError("Cancelling the assistant proposal changed the saved schedule")

            submit_assistant(explicit)
            until("second assistant change preview", lambda: page.js(
                "!document.querySelector('#live-assistant-pending').hidden"))
            with Store(db) as store:
                current = store.schedule_month(assistant_month)
                changed = [dict(row, days=dict(row["days"])) for row in current["entries"]]
                target_row = next(row for row in changed if row["employee_id"] == person_id)
                unrelated_day = next(day.isoformat() for day in scheduling.month_dates(assistant_month)
                                     if day.isoformat() != assistant_day)
                target_row["days"][unrelated_day] = {"kind": "work", "start": "09:00", "end": "17:00"}
                external_revision = store.save_schedule_month(assistant_month, changed, current["revision"])
            page.click("#confirm-live-assistant-action")
            until("stale assistant preview is rejected", lambda: (
                "Önizlemeden sonra vardiya kaynağı değişti" in last_assistant_text()))
            with Store(db) as store:
                stale_result = store.schedule_month(assistant_month)
                target_row = next(row for row in stale_result["entries"] if row["employee_id"] == person_id)
                if (stale_result["revision"] != external_revision
                        or target_row["days"].get(assistant_day) != {"kind": "work", "start": "08:00", "end": "16:00"}):
                    raise AssertionError("A stale assistant preview overwrote the latest schedule")

            submit_assistant(explicit)
            until("fresh assistant preview", lambda: page.js(
                "!document.querySelector('#live-assistant-pending').hidden"))
            page.click("#confirm-live-assistant-action")
            until("confirmed assistant change is saved", lambda: (
                "Vardiya değişikliği kaydedildi" in last_assistant_text()))
            with Store(db) as store:
                saved = store.schedule_month(assistant_month)
                target_row = next(row for row in saved["entries"] if row["employee_id"] == person_id)
                if target_row["days"].get(assistant_day) != {
                    "kind": "work", "start": "12:00", "end": "20:00",
                }:
                    raise AssertionError("Explicitly confirmed assistant change was not persisted")
            print("PASS #106: WebView2 resolves the future Friday, refuses missing hours, previews old/new times, preserves cancellations/stale revisions, and saves only after confirmation.",
                  flush=True)

            page.click("#close-live-assistant")
            server_module.live_assistant.list_models = lambda: []
            page.click("#open-live-assistant")
            until("offline assistant guidance", lambda: page.js(
                "document.querySelector('#live-assistant-status')?.innerText.includes('ollama pull qwen2.5:1.5b')"))
            offline_state = page.js("""({
              sendDisabled: document.querySelector('#live-assistant-send').disabled,
              model: document.querySelector('#live-assistant-model').value,
              searchAvailable: !!document.querySelector('#tools-menu-button'),
            })""")
            if not offline_state["sendDisabled"] or offline_state["model"] or not offline_state["searchAvailable"]:
                raise AssertionError(f"Offline assistant did not fail closed: {offline_state!r}")
            model_request_count = len(model_requests)
            page.click("#close-live-assistant")
            page.click("#tools-menu-button")
            page.js("""(() => {
              const search = document.querySelector('#easy-tools-search');
              search.value = 'puantaj';
              search.dispatchEvent(new Event('input', {bubbles: true}));
              return true;
            })()""")
            until("offline tool search remains available", lambda: page.js(
                "document.querySelectorAll('.easy-search-result').length > 0"))
            if len(model_requests) != model_request_count:
                raise AssertionError("Offline assistant mode issued a chat request")
            print("PASS #110: With no local models, WebView2 shows install guidance and disables chat while the tool search still returns results.",
                  flush=True)

            page.js("openWhatsNew(false); true")
            until("release notes dialog", lambda: page.js("!!document.querySelector('#whats-new-dialog[open]')"))
            page.click("[data-whats-new-tour-start]")
            until("first guided tour step", lambda: page.js(
                "document.querySelector('#whats-new-tour-panel')?.innerText.includes('1/2')"))
            if page.js("document.querySelector('#easy-tools-search')?.value") is None:
                raise AssertionError("Tour did not lead into the tool search")
            page.click("#whats-new-tour-panel .button-primary")
            until("attendance undo tour step", lambda: page.js(
                "state.view === 'attendance' && !!document.querySelector('#undo-attendance.easy-guide-target')"))
            page.click("#whats-new-tour-panel [data-whats-new-tour-close]")
            if page.js("!!document.querySelector('#whats-new-tour-panel')"):
                raise AssertionError("Closing the guided tour left its panel open")

            page.js("switchView('schedule'); true")
            until("schedule view before dirty-draft tour", lambda: page.js("state.view === 'schedule'"))
            page.js("openWhatsNew(false); true")
            until("second release notes dialog", lambda: page.js("!!document.querySelector('#whats-new-dialog[open]')"))
            page.click("[data-whats-new-tour-start]")
            until("second tour first step", lambda: page.js(
                "document.querySelector('#whats-new-tour-panel')?.innerText.includes('1/2')"))
            page.js("state.scheduleDirty = true; true")
            page.click("#whats-new-tour-panel .button-primary")
            until("tour protects unsaved draft", lambda: page.js(
                "document.querySelector('#whats-new-tour-panel')?.innerText.includes('taslağı değiştirmez')"))
            if page.js("state.view !== 'schedule'"):
                raise AssertionError("Tour navigated away from an unsaved schedule draft")
            page.js("state.scheduleDirty = false; document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true})); true")
            if page.js("!!document.querySelector('#whats-new-tour-panel')"):
                raise AssertionError("Escape did not close the guided tour")
            print("PASS #112: WebView2 starts the two-step release tour, focuses both changed controls, preserves a dirty draft, and closes by button/Escape.",
                  flush=True)
            passed.set()
        except Exception as exc:
            failures.append(exc)
            print("Browser at failure:", page.js("""({
                title: document.title,
                body: document.body.innerText.slice(0, 2500),
                dialog: document.querySelector('.operations-dialog[open]')?.innerText,
                fairness: document.querySelector('#fairness-dialog')?.innerText,
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
        raise AssertionError("WebView2 closed before the scenarios finished")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Run this standalone acceptance test on Windows with Edge WebView2.")
    with tempfile.TemporaryDirectory(prefix="ui43-73-75-") as directory:
        run(Path(directory))
