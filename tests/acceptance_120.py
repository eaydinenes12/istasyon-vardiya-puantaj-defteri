"""#81/#82/#83/#85/#86/#87/#89/#90/#91/#92/#95/#99/#100/#101/#115 local WebView2 acceptance.

From the delivery copy on Windows with Edge WebView2 installed:
    python -B tests\\acceptance_120.py

The app and employee portal run on loopback with a disposable SQLite database
and private browser profile. No phone, production data, or external service is used.
"""
import base64
import hashlib
import io
import json
import os
import secrets
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EMPLOYEE = "Acceptance Synthetic Employee"
ADMIN_PASSWORD = "acceptance-120-test"


def until(label, probe, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"Timed out: {label}")


class Browser:
    """Drive this WebView through CDP, without OS input or a debug port."""

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

    def js_async(self, expression):
        result = self.cdp("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
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
        previous = None

        def stable_point():
            nonlocal previous
            point = self.js(expression)
            stable = point == previous
            previous = point
            return point if stable else None

        point = until(f"clickable {selector} {text or ''}", stable_point)
        for event in ("mousePressed", "mouseReleased"):
            self.cdp("Input.dispatchMouseEvent", type=event, button="left", clickCount=1, **point)

    def fill(self, selector, value):
        self.click(selector)
        self.cdp("Input.insertText", text=value)

    def navigate(self, url):
        self.cdp("Page.navigate", url=url)

    def reload(self):
        self.cdp("Page.reload", ignoreCache=True)

    def set_file(self, selector, path):
        self.cdp("DOM.enable")
        document = self.cdp("DOM.getDocument", depth=-1)
        node = self.cdp("DOM.querySelector", nodeId=document["root"]["nodeId"], selector=selector)["nodeId"]
        if not node:
            raise AssertionError(f"File input not found: {selector}")
        self.cdp("DOM.setFileInputFiles", nodeId=node, files=[str(path)])
        self.js(f"""(() => {{
          const input = document.querySelector({json.dumps(selector)});
          input.dispatchEvent(new Event('input', {{bubbles:true}}));
          input.dispatchEvent(new Event('change', {{bubbles:true}}));
          return true;
        }})()""")


def api_json(origin, path, method="GET", payload=None, cookie=""):
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    try:
        response = urlopen(Request(origin + path, data=body, method=method, headers=headers), timeout=15)
    except HTTPError as error:
        response = error
    return response.status, response.headers, json.loads(response.read())


def api_bytes(origin, path, method="POST", payload=None, cookie="", headers=None):
    request_headers = {"Content-Type": "application/json"}
    if cookie:
        request_headers["Cookie"] = cookie
    request_headers.update(headers or {})
    if isinstance(payload, bytes):
        body = payload
    elif payload is None:
        body = None
    else:
        body = json.dumps(payload).encode("utf-8")
    try:
        response = urlopen(Request(origin + path, data=body, method=method, headers=request_headers), timeout=30)
    except HTTPError as error:
        response = error
    return response.status, response.headers, response.read()


def login(page, username):
    until("login overlay", lambda: page.js("!!document.querySelector('#login-overlay')"))
    page.fill('#login-overlay input[name="username"]', username)
    page.fill('#login-overlay input[name="password"]', ADMIN_PASSWORD)
    page.click("#login-overlay button", "Oturum aç")
    until(f"{username} logged in", lambda: page.js(
        "!!document.querySelector('#user-badge') && !document.querySelector('#login-overlay')"))


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
    synthetic_sync_root = folder / "synthetic-OneDrive"
    synthetic_sync_root.mkdir()
    os.environ["OneDrive"] = str(synthetic_sync_root)

    import webview
    import access_services
    import mail_automation
    import operations
    import scheduling
    import workspace_tools
    from server import ScheduleRequestHandler, create_server
    from storage import Store

    database = synthetic_sync_root / "synthetic.sqlite3"
    month = date.today().strftime("%Y-%m")
    next_month = date.today().replace(day=1)
    if next_month.month == 12:
        next_month = next_month.replace(year=next_month.year + 1, month=1)
    else:
        next_month = next_month.replace(month=next_month.month + 1)
    next_period = next_month.strftime("%Y-%m")
    day = month + "-01"
    token = secrets.token_urlsafe(32)
    mail_job_ids = {}
    device_session_ids = []
    with Store(database) as store:
        person = store.add_person(EMPLOYEE)["id"]
        store.save_employee_profile(person, {"home_section": "MARKET"})
        inactive_person = store.connection.execute(
            "INSERT INTO employees(name,active) VALUES(?,0)",
            ("Acceptance Inactive Recipient",),
        ).lastrowid
        store.save_employee_profile(inactive_person, {"home_section": "MARKET"})
        old_revision = ""
        for period in (month, next_period):
            entries = scheduling.normalize_schedule_entries(period, [{
                "row_id": "acceptance-120-row", "employee_id": person, "section": "MARKET",
                "days": {
                    work_day.isoformat(): {"kind": "work", "start": "08:00", "end": "16:00"}
                    for work_day in scheduling.month_dates(period)
                },
            }], store.people())
            revision = store.save_schedule_month(period, entries, "")
            if period == month:
                operations.publish_in_transaction(store, period, revision)
                old_revision = revision
        store.save_user("Admin", ADMIN_PASSWORD, "admin")
        store.save_user("Operator", ADMIN_PASSWORD, "operator")
        mail_automation.initialize(store.connection)
        mail_automation.save_recipient_permission(store, {
            "email": "active@example.test", "employee_id": person,
            "section_scope": "MARKET", "all_sections": False,
        })
        mail_automation.save_recipient_permission(store, {
            "email": "inactive@example.test", "employee_id": inactive_person,
            "section_scope": "MARKET", "all_sections": False,
        })
        mail_settings = {
            "host": "smtp.invalid", "port": 587, "username": "synthetic",
            "from_address": "reports@example.test", "use_ssl": False,
            "enabled": True, "password": "synthetic-mail-password",
        }
        mail_jobs = [
            {"id": "", "kind": "schedule", "cadence": "daily", "weekday": None, "month_day": None,
             "hour": 23, "minute": 59, "to": "active@example.test", "subject": "", "body": "",
             "enabled": True},
            {"id": "", "kind": "month-package", "cadence": "daily", "weekday": None, "month_day": None,
             "hour": 23, "minute": 59, "to": "active@example.test", "subject": "", "body": "",
             "enabled": True},
            {"id": "", "kind": "schedule", "cadence": "daily", "weekday": None, "month_day": None,
             "hour": 23, "minute": 59, "to": "inactive@example.test", "subject": "", "body": "",
             "enabled": True},
        ]
        from unittest.mock import patch
        with patch.object(mail_automation, "_protect_password", return_value=b"synthetic-cipher"):
            mail_configuration = mail_automation.save_configuration(
                store, {"settings": mail_settings, "jobs": mail_jobs},
            )
        for job in mail_configuration["jobs"]:
            mail_job_ids[(job["kind"], job["to"])] = job["id"]
        import_data = workspace_tools.export_settings(store)
        settings = import_data["settings"]
        settings["overtime_settings"]["daily_limit_hours"] = 11
        settings["workbook_settings"]["quick_colors"]["acceptance-cancel"] = "#336699"
        import_data["settings"] = {
            key: settings[key] for key in ("overtime_settings", "workbook_settings", "quick_entries")
        }
        with store.connection:
            store.connection.execute(
                "INSERT INTO portal_tokens(token_hash,employee_id,expires_at,revoked_at,created_at) "
                "VALUES(?,?,?,NULL,?)",
                (access_services._token_hash(token), person, access_services._now() + 3600,
                 access_services._now()),
            )
        device_expires = access_services._now() + 3600
        for _ in range(2):
            session_id = secrets.token_hex(12)
            access_services._record_device_session(
                store.connection, session_id, secrets.token_urlsafe(32), token, person, device_expires,
            )
            device_session_ids.append(session_id)
        store.connection.commit()
        backup_password = "synthetic-safe-backup-passphrase"
    conflict_copy = database.with_name("synthetic-conflict.sqlite3")
    conflict_copy_bytes = b"synthetic conflict-copy marker; never treat as a database"
    conflict_copy.write_bytes(conflict_copy_bytes)
    settings_file = folder / "synthetic-settings.json"
    settings_file.write_text(json.dumps(import_data, ensure_ascii=False), encoding="utf-8")

    server = create_server(database, ROOT / "web")
    server.mobile_diagnostics = {
        "service": {"status": "ok", "message": "Sentetik HTTPS hizmeti erişilebilir."},
        "network": {"status": "warning", "message": "Sentetik yerel ağ uyarısı."},
        "address": {"status": "error", "message": "Sentetik HTTPS adresi doğrulanamadı."},
        "certificate": {"status": "warning", "message": "Sentetik sertifika zinciri uyarısı."},
        "addresses": ["127.0.0.1"],
    }
    origin = f"http://127.0.0.1:{server.server_port}"
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    apply_lock = threading.Lock()
    apply_calls = 0
    second_apply_started = threading.Event()
    release_second_apply = threading.Event()
    original_extra_write = ScheduleRequestHandler._extra_write

    def gate_second_settings_group(handler, method, path):
        nonlocal apply_calls
        if method == "POST" and path == "/api/workspace/settings-import":
            with apply_lock:
                apply_calls += 1
                call_number = apply_calls
            if call_number == 2:
                second_apply_started.set()
                if not release_second_apply.wait(20):
                    raise TimeoutError("Second synthetic settings group was not released.")
        return original_extra_write(handler, method, path)

    ScheduleRequestHandler._extra_write = gate_second_settings_group
    failures = []
    passed = threading.Event()
    try:
        status, headers, result = api_json(origin, "/api/auth/login", "POST", {
            "username": "Admin", "password": ADMIN_PASSWORD,
        })
        if status != 200:
            raise AssertionError(f"Admin API login failed: {status} {result}")
        admin_cookie = headers["Set-Cookie"].split(";")[0]
        status, _, result = api_json(origin, "/api/access/permissions", "PUT", {
            "username": "Operator", "permissions": ["schedule.read", "schedule.write"],
            "confirm_sensitive": False,
        }, admin_cookie)
        if status != 200:
            raise AssertionError(f"Initial operator permission setup failed: {status} {result}")
        status, _, envelope = api_bytes(
            origin, "/api/continuity/database-backup/export", "POST",
            {"passphrase": backup_password}, admin_cookie,
        )
        if status != 200 or not envelope:
            raise AssertionError(f"Synthetic full database backup export failed: {status}")
        database_backup_file = folder / "synthetic-full-database.vpsql"
        database_backup_file.write_bytes(envelope)
        backup_headers = {
            "Content-Type": "application/octet-stream",
            "X-VP-Backup-Passphrase": base64.b64encode(backup_password.encode("utf-8")).decode("ascii"),
        }
        status, _, preview_body = api_bytes(
            origin, "/api/continuity/database-backup/preview", "POST", envelope, admin_cookie, backup_headers,
        )
        if status != 200:
            raise AssertionError(f"Synthetic full database backup preview failed: {preview_body!r}")
        backup_preview = json.loads(preview_body)
        restore_headers = {
            **backup_headers,
            "X-VP-Backup-Preview": backup_preview["preview_id"],
            "X-VP-Confirm-Restore": "yes",
        }
        status, _, restored_body = api_bytes(
            origin, "/api/continuity/database-backup/restore", "POST", envelope, admin_cookie, restore_headers,
        )
        if status != 200:
            raise AssertionError(f"Synthetic full database backup restore failed: {restored_body!r}")
        restored = json.loads(restored_body)
        if restored["restored"]["row_counts"]["employees"] < 2:
            raise AssertionError("Full database restore did not retain the synthetic employee rows.")
        status, headers, result = api_json(origin, "/api/auth/login", "POST", {
            "username": "Admin", "password": ADMIN_PASSWORD,
        })
        if status != 200:
            raise AssertionError(f"Admin API login after synthetic restore failed: {status} {result}")
        admin_cookie = headers["Set-Cookie"].split(";")[0]
        status, _, schedule_result = api_json(
            origin, "/api/schedule?month=" + month, cookie=admin_cookie,
        )
        if status != 200 or not schedule_result.get("entries"):
            raise AssertionError(f"Synthetic schedule seed missing: {status}")
        window = webview.create_window(
            "Acceptance 120", origin + "/employee-portal/" + token, width=1440, height=900, focus=False,
        )
    except Exception:
        server.shutdown()
        server_thread.join()
        server.server_close()
        server.lock.release()
        raise

    def scenario():
        page = Browser(window)
        try:
            if not window.events.loaded.wait(40):
                raise TimeoutError("WebView2 did not load the local portal")
            until("initial publication and confirmation action", lambda: page.js(
                "document.body.innerText.includes('Bu yayın sürümünü okudum')"))
            page.click("button", "Bu yayın sürümünü okudum")
            until("initial read confirmation", lambda: page.js(
                "document.body.innerText.includes('yayınının bu sürümünü okudunuz')"))

            with Store(database) as store, store.connection:
                current = store.schedule_month(month)
                revised = current["entries"]
                revised[0]["days"][day]["start"] = "09:00"
                new_revision = store.save_schedule_month(month, revised, current["revision"])
                operations.publish_in_transaction(store, month, new_revision)
                receipt = access_services._plan_version_status(
                    store, access_services._token_hash(token), person,
                )["versions"][0]["viewed_revision"]
            if receipt != old_revision:
                raise AssertionError(f"Republishing changed the old read receipt: {receipt}")

            page.reload()
            until("old and new hours shown side by side", lambda: page.js(
                "document.querySelector('.change-table')?.innerText.includes('Önceki vardiya')"
                " && document.querySelector('.change-table')?.innerText.includes('08:00–16:00')"
                " && document.querySelector('.change-table')?.innerText.includes('09:00–16:00')"))
            if page.js("!document.body.innerText.includes('Bu yayın sürümünü okudum')"):
                raise AssertionError("Republishing did not request a new read confirmation")
            with Store(database) as store:
                receipt = access_services._plan_version_status(
                    store, access_services._token_hash(token), person,
                )["versions"][0]["viewed_revision"]
            if receipt != old_revision:
                raise AssertionError("Opening the republished schedule counted as a read confirmation")
            page.click("button", "Bu yayın sürümünü okudum")
            until("new read confirmation", lambda: page.js(
                "document.body.innerText.includes('yayınının bu sürümünü okudunuz')"))
            with Store(database) as store:
                receipt = access_services._plan_version_status(
                    store, access_services._token_hash(token), person,
                )["versions"][0]["viewed_revision"]
            if receipt != new_revision:
                raise AssertionError("Read confirmation was not bound to the current publication revision")
            print("#90/#91: WebView2 old/new table and revision-specific confirmation verified.", flush=True)

            page.navigate(origin)
            login(page, "Operator")
            page.click('.main-nav [data-view="schedule"]')
            until("synthetic schedule loaded", lambda: page.js(
                "(() => { const table=document.querySelector('#schedule-table'); return !!table"
                " && table.innerText.includes(" + json.dumps(EMPLOYEE) + ")"
                " && [...table.querySelectorAll('input')].some(input => input.value === '09:00'); })()"))

            status, _, result = api_json(origin, "/api/access/permissions", "PUT", {
                "username": "Operator", "permissions": [], "confirm_sensitive": False,
            }, admin_cookie)
            if status != 200:
                raise AssertionError(f"Operator permission downgrade failed: {status} {result}")
            until("revoked schedule content cleared from the open screen", lambda: page.js(
                "!document.querySelector('#schedule-table')?.innerText.includes("
                + json.dumps(EMPLOYEE) + ")"), timeout=15)
            if page.js("document.body.innerText.includes(" + json.dumps(EMPLOYEE) + ")"):
                raise AssertionError("Previously rendered schedule remained visible after permission revocation")
            print("#92: WebView2 cleared and re-rendered an already-open screen after revocation.", flush=True)

            page.click("#user-badge button")
            login(page, "Admin")
            page.click("#tools-menu-button")
            page.click("#tools-menu button", "Özel yetkiler")
            until("permission preview", lambda: page.js(
                "!!document.querySelector('[data-permission-preview]')"))
            page.click('input[name="permission"][value="payroll"]')
            preview = until("representative page, field and output preview", lambda: (
                page.js("document.querySelector('[data-permission-preview]')?.innerText") or ""
            ) if all(value in (page.js("document.querySelector('[data-permission-preview]')?.innerText") or "")
                     for value in ("Bordro ve ücret", "ücret", "çıktı")) else None)
            if "henüz uygulanmadı" not in preview:
                raise AssertionError(f"Preview did not state that the change remains unapplied: {preview}")
            with Store(database) as store:
                operator = next(user for user in store.users() if user["username"] == "Operator")
            if "payroll" in operator["permissions"]:
                raise AssertionError("Permission preview was applied before the user saved the change")
            print("#101: WebView2 showed page, field, output effects before applying.", flush=True)
            page.js("document.querySelector('#access-permissions')?.close()")

            page.click("#tools-menu-button")
            page.click("#tools-menu button", "Güvenli mobil erişim tanılama")
            until("mobile diagnostics dialog", lambda: page.js(
                "document.querySelector('#mobile-diagnostics-dialog[open]')?.innerText.includes("
                "'Sertifika ve özel anahtar')"))
            diagnostics = page.js("document.querySelector('#mobile-diagnostics-dialog')?.innerText || ''")
            for expected in (
                "Windows uygulamasındaki HTTPS hizmeti · ok",
                "Yerel ağ ve güvenlik duvarı · warning",
                "Telefonun kullanacağı HTTPS adresi · error",
                "Sertifika ve özel anahtar · warning",
                "Sentetik HTTPS hizmeti erişilebilir.",
                "Sentetik yerel ağ uyarısı.",
                "Sentetik HTTPS adresi doğrulanamadı.",
                "Sentetik sertifika zinciri uyarısı.",
                "127.0.0.1",
                "Bu tanılama gerçek telefon bağlantısını veya ağ geçişini sınamaz.",
                "HTTP’ye düşmeyin",
            ):
                if expected not in diagnostics:
                    raise AssertionError(f"Mobile diagnostics UI omitted {expected!r}: {diagnostics}")
            print("#89: WebView2 showed separate service/network/address/certificate findings and safe guidance.", flush=True)
            page.js("document.querySelector('#mobile-diagnostics-dialog').close()")

            page.click("#tools-menu-button")
            page.click("#tools-menu button", "Ayarları dışa / içe aktar")
            until("settings transfer dialog", lambda: page.js(
                "!!document.querySelector('#settings-transfer-dialog[open] #ws-settings-file')"))
            page.set_file("#settings-transfer-dialog #ws-settings-file", settings_file)
            until("settings import preview", lambda: page.js(
                "!!document.querySelector('#ws-settings-preview #ws-settings-apply')"))
            page.click("#ws-settings-apply")
            if not second_apply_started.wait(15):
                raise TimeoutError("Second settings group was not reached through the UI.")
            if not page.js("document.querySelector('#ws-settings-progress')?.textContent.includes('(2/3)')"):
                raise AssertionError("UI did not expose the active second settings group.")
            try:
                page.click("#ws-settings-cancel", "Aşamaları durdur")
                until("safe cancellation requested", lambda: page.js(
                    "document.querySelector('#ws-settings-progress')?.textContent.includes("
                    "'İptal istendi. Devam eden tek ayar grubu tamamlanacak')"))
            finally:
                release_second_apply.set()
            until("completed subtask report", lambda: page.js(
                "document.querySelector('#ws-settings-report')?.innerText.includes('2 grup uygulandı')"
                " && document.querySelector('#ws-settings-report')?.innerText.includes('1 grup başlatılmadı')"))
            report = page.js("document.querySelector('#ws-settings-report').innerText")
            if "Fazla mesai kuralları · uygulandı" not in report \
                    or "Excel çıktı ayarları · uygulandı" not in report \
                    or "Hızlı girişler" not in report:
                raise AssertionError(f"Completed and unstarted groups were not reported: {report}")
            with Store(database) as store:
                if store.overtime_settings()["daily_limit_hours"] != 11 \
                        or store.workbook_settings()["quick_colors"].get("acceptance-cancel") != "#336699":
                    raise AssertionError("Valid completed settings groups were not applied to the synthetic DB.")
            if apply_calls != 2:
                raise AssertionError(f"Cancellation started an unexpected number of groups: {apply_calls}")
            print("#115: WebView2 completed the active group, reported both completed groups, and left the third unstarted.", flush=True)

            page.js("document.querySelector('#settings-transfer-dialog')?.close()")
            page.click("#tools-menu-button")
            page.click("#tools-menu button", "E-posta otomatik gönderim ve adres defteri")
            until("mail recipient permissions", lambda: page.js(
                "document.querySelector('#mail-dialog [data-mail-recipient-list]')?.innerText.includes("
                "'Pasif personel · gönderim engelli')"))
            inactive_option = page.js("""(() => {
              const option=[...document.querySelectorAll('#mail-dialog [name="employee_id"] option')]
                .find(item=>item.textContent.includes('Acceptance Inactive Recipient'));
              return option ? {disabled:option.disabled,text:option.textContent} : null;
            })()""")
            if not inactive_option or not inactive_option["disabled"] or "pasif" not in inactive_option["text"]:
                raise AssertionError(f"Inactive recipient was not visibly blocked in the UI: {inactive_option}")

            schedule_id = mail_job_ids[("schedule", "active@example.test")]
            schedule_card = page.js(f"""(() => {{
              const card=[...document.querySelectorAll('#mail-dialog .mail-automation-job')]
                .find(item=>item.querySelector('[data-mail-preview="{schedule_id}"]'));
              if (!card) return null;
              card.querySelector('.mail-job-preview').open=true;
              return card.querySelector('[data-mail-preview]').dataset.mailPreview;
            }})()""")
            if schedule_card != schedule_id:
                raise AssertionError("Active schedule preview job was not present in the mail UI.")
            page.click(f'#mail-dialog [data-mail-preview="{schedule_id}"]', "Ek içeriğini yükle")
            preview_text = until("real generated XLSX attachment preview", lambda: (
                page.js("document.querySelector('#mail-dialog [data-mail-recipient-preview]')?.innerText || ''") or ""
            ) if all(value in (
                page.js("document.querySelector('#mail-dialog [data-mail-recipient-preview]')?.innerText || ''") or ""
            )
                     for value in (EMPLOYEE, "08:00")) else None)
            attachment_b64 = page.js_async("""(async () => {
              const link=document.querySelector('#mail-dialog [data-mail-recipient-preview] a[download]');
              if (!link) return null;
              const bytes=new Uint8Array(await (await fetch(link.href)).arrayBuffer());
              let binary='';
              for (let offset=0; offset<bytes.length; offset+=32768)
                binary+=String.fromCharCode(...bytes.subarray(offset,offset+32768));
              return btoa(binary);
            })()""")
            if not attachment_b64:
                raise AssertionError("Preview did not expose a downloadable generated attachment.")
            from openpyxl import load_workbook
            attachment_bytes = base64.b64decode(attachment_b64, validate=True)
            workbook = load_workbook(io.BytesIO(attachment_bytes), read_only=True, data_only=True)
            try:
                workbook_values = "\n".join(
                    str(value) for sheet in workbook.worksheets
                    for row in sheet.iter_rows(values_only=True) for value in row if value is not None
                )
            finally:
                workbook.close()
            if EMPLOYEE not in workbook_values or "08:00" not in workbook_values:
                raise AssertionError("Downloaded preview attachment did not contain the expected generated XLSX cells.")
            digest_text = page.js(
                "document.querySelector('#mail-dialog [data-mail-recipient-preview] .mail-attachment-preview-item small')?.innerText || ''") or ""
            if hashlib.sha256(attachment_bytes).hexdigest() not in digest_text:
                raise AssertionError("Displayed attachment digest did not match the generated XLSX bytes.")
            if EMPLOYEE not in preview_text:
                raise AssertionError("Rendered attachment excerpt did not show the generated recipient data.")
            print("#81: WebView2 showed recipient-specific XLSX cells and the downloaded preview bytes matched its SHA-256.", flush=True)

            retry_job_id = mail_job_ids[("schedule", "active@example.test")]
            retry_now = datetime.now().astimezone()
            retry_at = retry_now + timedelta(minutes=5)
            retry_reason = "Sentetik ek boyutu aştı; eki kaldırın veya planı ayırıp yeniden deneyin."
            with Store(database) as store, store.connection:
                store.connection.execute(
                    "UPDATE mail_automation_jobs SET kind='attendance',kinds='attendance,manager-summary',"
                    "hour=?,minute=0 WHERE id=?",
                    ((retry_now.hour + 3) % 24, retry_job_id),
                )
                retry_job = store.connection.execute(
                    "SELECT * FROM mail_automation_jobs WHERE id=?", (retry_job_id,),
                ).fetchone()
                occurrence_at = mail_automation._latest_occurrence(
                    retry_job, mail_automation._local(retry_now),
                )
                occurrence, pending_month, pending_week = mail_automation._occurrence_details(
                    retry_job, occurrence_at,
                )
                store.connection.execute(
                    "UPDATE mail_automation_jobs SET last_status='retry',last_error=?,attempts=2,"
                    "last_attempt_at=?,next_retry_at=?,pending_occurrence=?,pending_month=?,pending_week=? "
                    "WHERE id=?",
                    (
                        retry_reason, mail_automation._stamp(retry_now), mail_automation._stamp(retry_at),
                        occurrence, pending_month, pending_week, retry_job_id,
                    ),
                )

            page.js("document.querySelector('#mail-dialog')?.close()")
            page.click("#tools-menu-button")
            page.click("#tools-menu button", "E-posta otomatik gönderim ve adres defteri")
            until("updated oversized plan in mail UI", lambda: page.js(
                f"!!document.querySelector('#mail-dialog [data-mail-edit=\"{retry_job_id}\"]')"))
            page.click("#mail-dialog [data-mail-rehearsal] [data-load]", "7 günlük provayı göster")
            rehearsal = mail_automation.schedule_rehearsal(
                database, ScheduleRequestHandler.MAIL_KINDS, retry_now,
            )["plans"][retry_job_id]["retry"]
            last_attempt_text = page.js(
                f"new Date({json.dumps(rehearsal['last_attempt_at'])}).toLocaleString('tr-TR',"
                "{dateStyle:'short',timeStyle:'short'})")
            next_retry_text = page.js(
                f"new Date({json.dumps(rehearsal['next_retry_at'])}).toLocaleString('tr-TR',"
                "{dateStyle:'short',timeStyle:'short'})")
            retry_ui = until("retry reason/count/times together in mail rehearsal", lambda: (
                page.js("document.querySelector('#mail-dialog [data-mail-rehearsal] [data-attempts]')?.innerText || ''")
                or ""
            ) if retry_reason in (
                page.js("document.querySelector('#mail-dialog [data-mail-rehearsal] [data-attempts]')?.innerText || ''")
                or ""
            ) else None)
            if "Otomatik deneme: 2" not in retry_ui or "Son deneme:" not in retry_ui \
                    or "Sonraki otomatik deneme:" not in retry_ui \
                    or last_attempt_text not in retry_ui or next_retry_text not in retry_ui:
                raise AssertionError(f"Retry reason, count, and times were not together in the UI: {retry_ui}")
            print("#86: WebView2 showed retry reason, attempt count, last attempt, and next retry time together.", flush=True)

            page.js(f"""document.querySelector('#mail-dialog [data-mail-edit="{retry_job_id}"]').click()""")
            page.js("""(() => {
              const remove = document.querySelector('#mail-dialog [data-mail-auto-job] input[name="kinds"][value="manager-summary"]');
              if (!remove) return false;
              remove.checked = false;
              remove.dispatchEvent(new Event('change', {bubbles:true}));
              return true;
            })()""")
            page.js("""(() => {
              window.confirm=()=>true;
              setTimeout(()=>document.querySelector('#mail-dialog [data-mail-auto-job] button[type=submit]').click(),0);
              return true;
            })()""")
            def reduced_plan_kinds():
                value = page.js(f"""(() => {{
                  const card=[...document.querySelectorAll('#mail-dialog .mail-automation-job')]
                    .find(item=>item.querySelector('[data-mail-edit="{retry_job_id}"]'));
                  return card?.querySelector('strong')?.innerText || "";
                }})()""") or ""
                return value if "Puantaj (Excel)" in value \
                    and "Yönetici özeti (PDF)" not in value else None

            remaining_kinds = until("oversized attachment removed from plan", reduced_plan_kinds)
            if "Yönetici özeti (PDF)" in remaining_kinds:
                raise AssertionError(f"Oversized attachment remained selected after editing: {remaining_kinds}")
            with patch.object(
                mail_automation, "_deliver", return_value=mail_automation._DeliveryResult("sent"),
            ) as synthetic_delivery:
                page.js(f"""setTimeout(()=>document.querySelector('#mail-dialog [data-mail-send-now="{retry_job_id}"]').click(),0);true""")
                until("reduced attachment plan sent in local UI", lambda: (
                    page.js("document.querySelector('#mail-dialog [data-mail-auto-status]')?.innerText || ''") or ""
                ) if "Puantaj (Excel) gönderildi" in (
                    page.js("document.querySelector('#mail-dialog [data-mail-auto-status]')?.innerText || ''") or ""
                ) else None)
            if not synthetic_delivery.called:
                raise AssertionError("Reduced attachment plan did not reach the isolated synthetic delivery.")
            with Store(database) as store:
                row = store.connection.execute(
                    "SELECT kinds,last_status FROM mail_automation_jobs WHERE id=?", (retry_job_id,),
                ).fetchone()
                if tuple(row) != ("attendance", "sent"):
                    raise AssertionError(f"Reduced report plan was not marked sent: {tuple(row)}")
            print("#87: WebView2 removed an oversized report from the plan and completed a synthetic retry without SMTP.", flush=True)

            rollover_job_id = mail_job_ids[("schedule", "active@example.test")]
            rollover_first = datetime(2026, 10, 31, 9, 0).astimezone()
            rollover_open = datetime(2026, 11, 1, 9, 5).astimezone()
            stable_revision = '{"synthetic":"unchanged"}'
            with Store(database) as store, store.connection:
                store.connection.execute(
                    "UPDATE mail_automation_jobs SET cadence='daily',weekday=NULL,hour=9,minute=0,"
                    "last_status='retry',last_occurrence='daily:2026-10-31',"
                    "pending_occurrence='daily:2026-10-31',pending_month='2026-10',pending_week=0,"
                    "source_revisions=?,attempts=1,next_retry_at=?,last_attempt_at=?,last_error=? WHERE id=?",
                    (
                        stable_revision, mail_automation._stamp(rollover_first + timedelta(minutes=5)),
                        mail_automation._stamp(rollover_first), "Sentetik SMTP kesintisi",
                        rollover_job_id,
                    ),
                )
            with patch.object(
                mail_automation, "_source_revision_snapshot", return_value=stable_revision,
            ), patch.object(
                mail_automation, "_deliver", return_value=mail_automation._DeliveryResult("sent"),
            ) as rollover_delivery:
                if mail_automation.process_due(server, rollover_open) != 1:
                    raise AssertionError("Current-period plan did not run after the prior-period retry expired.")
            if rollover_delivery.call_count != 1 or rollover_delivery.call_args.args[2] != "2026-11":
                raise AssertionError("Scheduler sent an old report or blocked the new-period plan.")
            with Store(database) as store:
                rollover_config = mail_automation.get_configuration(store, rollover_open)
            rollover_job = [
                job for job in rollover_config["jobs"] if job["id"] == rollover_job_id
            ][0]
            rollover_entry = [
                entry for entry in rollover_config["history"]
                if entry["source"] == "retry-expired" and entry["job_id"] == rollover_job_id
            ][0]
            if rollover_job["last_status"] != "sent" or rollover_entry["status"] != "expired" \
                    or rollover_entry["month"] != "2026-10" \
                    or "dönem değişimi nedeniyle iptal edildi" not in rollover_entry["error"]:
                raise AssertionError("Prior-period retry outcome was not retained with its reason.")
            print("#83: Unchanged-source October retry expired on reopen; November plan ran and the reason was retained.", flush=True)
            page.js("document.querySelector('#mail-dialog')?.close()")
            page.click("#tools-menu-button")
            page.click("#tools-menu button", "E-posta otomatik gönderim ve adres defteri")
            expired_row = until("prior-period expiry reason visible in mail history", lambda: (
                page.js("""(() => {
                  const row=document.querySelector('#mail-dialog [data-mail-history] tr[data-mail-history-status="expired"]');
                  return row?.innerText.includes('Dönem geçişinde iptal edilen deneme')
                    && row.innerText.includes('2026-10')
                    && row.innerText.includes('dönem değişimi nedeniyle iptal edildi') ? row.innerText : null;
                })()""")
            ))
            if not expired_row:
                raise AssertionError("Mail history did not show why the previous-period retry was cancelled.")
            print("#83: WebView2 mail history displayed the cancellation reason for the old-period retry.", flush=True)

            smtp_attempts = []

            def forbid_smtp(*args, **kwargs):
                smtp_attempts.append((args, kwargs))
                raise AssertionError("Synthetic acceptance must not connect to SMTP.")

            page.js("window.confirm=()=>true")
            package_id = mail_job_ids[("month-package", "active@example.test")]
            package_button = f'#mail-dialog [data-mail-send-now="{package_id}"]'
            with patch.object(mail_automation, "_MailSMTP", side_effect=forbid_smtp), \
                    patch.object(mail_automation, "_MailSMTPSSL", side_effect=forbid_smtp):
                page.click(package_button, "Şimdi gönder")
                scope_error = until("section-scope denial visible in send UI", lambda: (
                    page.js("document.querySelector('#mail-dialog [data-mail-auto-status]')?.innerText || ''") or ""
                ) if "MARKET bölümüne yetkili" in
                (page.js("document.querySelector('#mail-dialog [data-mail-auto-status]')?.innerText || ''") or "") else None)
                if "tüm bölümleri içeren ekleri ayırın" not in scope_error:
                    raise AssertionError(f"Section-scope denial was not actionable in the send UI: {scope_error}")
                if smtp_attempts:
                    raise AssertionError("Section-scope denial reached the SMTP connector.")
                inactive_id = mail_job_ids[("schedule", "inactive@example.test")]
                page.click(f'#mail-dialog [data-mail-send-now="{inactive_id}"]', "Şimdi gönder")
                inactive_error = until("inactive recipient denial visible in send UI", lambda: (
                    page.js("document.querySelector('#mail-dialog [data-mail-auto-status]')?.innerText || ''") or ""
                ) if "pasif personele bağlı" in (
                    page.js("document.querySelector('#mail-dialog [data-mail-auto-status]')?.innerText || ''") or ""
                ) else None)
                if "gönderim engellendi" not in inactive_error:
                    raise AssertionError(f"Inactive recipient denial was not visible in the send UI: {inactive_error}")
                if smtp_attempts:
                    raise AssertionError("Inactive recipient denial reached the SMTP connector.")
            print("#82/#85: send UI showed section and inactive-recipient denials before any SMTP connection; inactive recipient was visibly disabled.", flush=True)

            page.js("document.querySelector('#mail-dialog')?.close()")
            page.click("#tools-menu-button")
            page.click("#tools-menu button", "Personel bağlantıları")
            until("admin device-session list", lambda: page.js(
                "!!document.querySelector('#access-links [data-device-sessions] table')"))
            session_text = page.js("document.querySelector('#access-links [data-device-sessions]')?.innerText || ''") or ""
            if not all(session_id in session_text for session_id in device_session_ids):
                raise AssertionError(f"Admin UI omitted a synthetic device session: {session_text}")
            page.js("window.confirm=()=>true")
            page.click(f'#access-links [data-device-revoke="{device_session_ids[0]}"]', "Oturumu kapat")
            revoked_row = until("revoked session shown in admin UI", lambda: page.js(
                f"""(() => {{
                  const row=[...document.querySelectorAll('#access-links [data-device-sessions] tr')]
                    .find(item=>item.innerText.includes("{device_session_ids[0]}"));
                  return row?.innerText.includes("İptal") ? row.innerText : null;
                }})()"""))
            remaining_row = page.js(
                f"""(() => {{
                  const row=[...document.querySelectorAll('#access-links [data-device-sessions] tr')]
                    .find(item=>item.innerText.includes("{device_session_ids[1]}"));
                  return row?.innerText || "";
                }})()""")
            if "Etkin" not in remaining_row:
                raise AssertionError(f"Revoking one session changed the other session: {remaining_row}")
            status, _, sessions_result = api_json(
                origin, "/api/access/device-sessions", cookie=admin_cookie,
            )
            if status != 200 or sum(bool(item["active"]) for item in sessions_result["sessions"]) != 1:
                raise AssertionError("Admin UI revocation did not leave exactly one active device session.")
            if "Oturumu kapat" in revoked_row:
                raise AssertionError(f"Revoked device session still exposed a close action: {revoked_row}")
            print("#95: WebView2 listed both device sessions, revoked one, and kept the other active.", flush=True)

            page.js("document.querySelector('#access-links')?.close()")
            page.click("#tools-menu-button")
            page.click("#tools-menu button", "Veri sağlığı ve onarım")
            health_text = until("safe OneDrive conflict guidance in health UI", lambda: (
                page.js("document.querySelector('#health-dialog .extras-body')?.innerText || ''") or ""
            ) if all(value in (
                page.js("document.querySelector('#health-dialog .extras-body')?.innerText || ''") or ""
            ) for value in ("synthetic-OneDrive", "benzer adlı veritabanı dosyaları var",
                            "Dosyaları birleştirmeyin veya silmeyin",
                            "normal şekilde kapattıktan sonra", "kilidi tek başına eşitleme hatası")) else None)
            if conflict_copy.read_bytes() != conflict_copy_bytes:
                raise AssertionError("Health UI modified the synthetic conflict copy.")
            print("#99: WebView2 showed backup/close/inspect guidance for a synthetic OneDrive conflict copy without merging it.", flush=True)
            page.js("document.querySelector('#health-dialog')?.close()")

            page.js("""(() => {
              const script=document.createElement('script');
              script.src='/data-continuity.js';
              document.head.append(script);
              return true;
            })()""")
            until("encrypted backup UI module", lambda: page.js(
                "!!window.dataContinuity?.openFullDatabaseBackup"))
            page.js("window.dataContinuity.openFullDatabaseBackup()")
            until("encrypted backup UI", lambda: page.js(
                "!!document.querySelector('#data-continuity-dialog[open] #continuity-db-file')"))
            lost_password_guidance = page.js(
                "document.querySelector('#data-continuity-dialog .continuity-body')?.innerText || ''") or ""
            if "Parola uygulamada saklanmaz." not in lost_password_guidance \
                    or "Parola unutulursa yedek geri getirilemez." not in lost_password_guidance:
                raise AssertionError("Open encrypted-backup panel omitted the unrecoverable lost-password outcome.")
            print("#100: Open WebView2 backup panel disclosed that a forgotten password makes the backup unrecoverable.", flush=True)
            page.set_file("#continuity-db-file", database_backup_file)
            page.fill("#continuity-db-passphrase", backup_password)
            page.click("#continuity-db-preview", "Dosyayı doğrula ve önizle")
            until("correct password verification in UI", lambda: page.js(
                "document.querySelector('#data-continuity-dialog #continuity-db-preview-result')?.innerText"
                ".includes('Doğrulandı')"))
            page.fill("#continuity-db-passphrase", "incorrect-synthetic-password")
            page.click("#continuity-db-preview", "Dosyayı doğrula ve önizle")
            wrong_password = until("wrong-password warning in WebView2", lambda: (
                page.js("document.querySelector('#data-continuity-dialog .continuity-status[role=alert]')?.innerText || ''") or ""
            ) if "Parola yanlış" in
            (page.js("document.querySelector('#data-continuity-dialog .continuity-status[role=alert]')?.innerText || ''") or "") else None)
            if page.js("!!document.querySelector('#data-continuity-dialog #continuity-db-restore')"):
                raise AssertionError("Wrong-password verification left a stale restore action visible.")
            with Store(database) as store:
                if not any(item["name"] == EMPLOYEE for item in store.people()):
                    raise AssertionError("Wrong-password UI attempt changed the synthetic database.")
            print(f"#100: WebView2 showed '{wrong_password}' after a successful encrypted backup round trip; stale restore action cleared.", flush=True)
            page.js("document.querySelector('#data-continuity-dialog')?.close()")
            print("#115 UI scenario used a synthetic CDP file input; no native OS file picker was tested.", flush=True)
            passed.set()
        except Exception as exc:
            failures.append(exc)
            try:
                print("Browser at failure:", page.js("""({
                    url: location.href,
                    view: document.querySelector('.view.active')?.id,
                    dialog: document.querySelector('dialog[open]')?.id,
                    body: document.body.innerText.slice(0,1200),
                    shifts: [...document.querySelectorAll('#schedule-table input[data-action="shift-time"]')]
                      .map(input => [input.value, input.dataset.day, input.dataset.field])
                })"""), flush=True)
            except Exception:
                pass
        finally:
            window.destroy()

    webview.settings.update(ALLOW_FILE_URLS=False, OPEN_EXTERNAL_LINKS_IN_BROWSER=False)
    try:
        webview.start(scenario, gui="edgechromium", private_mode=True, storage_path=str(profile))
    finally:
        ScheduleRequestHandler._extra_write = original_extra_write
        release_second_apply.set()
        server.shutdown()
        server_thread.join()
        server.server_close()
        server.lock.release()
    if failures:
        raise failures[0]
    if not passed.is_set():
        raise AssertionError("WebView2 closed before the scenario finished")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Run this standalone acceptance test on Windows with Edge WebView2.")
    with tempfile.TemporaryDirectory(prefix=".ui120-", dir=ROOT) as directory:
        run(Path(directory))
    print("PASS #81/#82/#83/#85/#86/#87/#89/#90/#91/#92/#95/#99/#100/#101/#115: synthetic WebView2 acceptance completed.")
