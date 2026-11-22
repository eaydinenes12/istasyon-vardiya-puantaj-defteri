"""Opt-in HTTPS service for Android. It never routes to the desktop HTTP API."""
from __future__ import annotations

from datetime import date, datetime, timezone
from html import escape
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import re
import secrets
import ssl
import sqlite3
import socket
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import access_services
import controls
import linked_leave
import operations
import scheduling
from storage import Store
from cryptography import x509
from cryptography.x509.oid import NameOID


_COOKIE = "__Host-vardiya"
_MODES = {"auto": "Otomatik", "phone": "Telefon", "tablet": "Tablet"}
_STYLE = """
*{box-sizing:border-box}body{margin:0;background:#f2f6f3;color:#19332a;font:16px system-ui,sans-serif}
header{background:#163d32;color:#fff;padding:1rem max(1rem,calc((100vw - 64rem)/2))}
header p,h1{margin:.25rem 0}main{max-width:66rem;margin:auto;padding:1rem}h2{font-size:1.2rem;margin:0 0 .85rem}
nav{display:flex;flex-wrap:wrap;gap:.45rem;margin-bottom:1rem}nav a{padding:.65rem .8rem;border:1px solid #6c8378;
border-radius:.5rem;color:#153e33;text-decoration:none;background:#fff}nav [aria-current]{background:#194e3f;color:#fff}
.panels{display:grid;gap:1rem;align-items:start}.panel{padding:1rem;background:#fff;border:1px solid #d5e0d9;
border-radius:.7rem;min-width:0}.panel ul{list-style:none;padding:0;margin:0}.panel li{padding:.7rem 0;border-top:1px solid #d5e0d9;
overflow-wrap:anywhere}.panel p{line-height:1.45}kbd{font:inherit}label{display:block;margin:.7rem 0}
input,select,textarea,button{font:inherit;width:100%;min-height:44px;margin-top:.25rem;padding:.5rem;border:1px solid #6c8378;border-radius:.4rem}
button{background:#194e3f;color:#fff;font-weight:600;cursor:pointer}.status{border:1px solid #6c8378;background:white;padding:.75rem}
form+form{margin-top:1rem}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid #9a5d19;outline-offset:2px}
@media(min-width:37.5rem){.tablet .panels{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(min-width:48rem){.auto .panels{grid-template-columns:repeat(2,minmax(0,1fr))}}
.change-table{border-collapse:collapse;width:100%;background:#fff}
.change-table th,.change-table td{border:1px solid #ccd7cf;padding:.55rem;text-align:left;vertical-align:top}
"""


def diagnostics(config_path, *, runtime=None, startup_error=""):
    """Read-only mobile HTTPS diagnostics; never weakens TLS or edits configuration."""
    result = {
        "service": {"status": "warning", "message": "Mobil HTTPS hizmeti henüz başlatılmadı."},
        "network": {"status": "warning", "message": "Yerel ağ ve güvenlik duvarı erişimi burada sınanmıyor."},
        "address": {"status": "warning", "message": "Yayımlanmış mobil adres doğrulanamadı."},
        "certificate": {"status": "warning", "message": "Sertifika ve anahtar henüz doğrulanmadı."},
        "addresses": [],
    }
    path = Path(config_path)
    if not path.is_file():
        result["service"] = {"status": "info", "message": "Mobil HTTPS kapalı; mobil-https.json ayar dosyası yok."}
        result["network"] = {"status": "info", "message": "Hizmet kapalı olduğu için yerel ağ denetlenmedi."}
        result["address"] = {"status": "info", "message": "Yayımlanmış HTTPS adresi yok."}
        result["certificate"] = {"status": "info", "message": "Hizmet kapalı; sertifika dosyası aranmadı."}
        return result
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        result["service"] = {"status": "error", "message": "Mobil HTTPS ayar dosyası okunamadı."}
        for key in ("network", "address", "certificate"):
            result[key] = {"status": "warning", "message": "Ayarlar okunamadığı için bu bölüm doğrulanamadı."}
        return result
    fields = {"bind", "port", "origin", "certificate", "key"}
    if not isinstance(config, dict) or set(config) != fields:
        result["service"] = {"status": "error", "message": "Mobil HTTPS ayar alanları geçersiz."}
        for key in ("network", "address", "certificate"):
            result[key] = {"status": "warning", "message": "Ayar biçimi düzeltilmeden doğrulanamadı."}
        return result

    bind, port, origin = config["bind"], config["port"], config["origin"]
    if not isinstance(config["certificate"], str) or not isinstance(config["key"], str):
        result["service"] = {"status": "error", "message": "Sertifika ve anahtar dosyası adları geçersiz."}
        result["certificate"] = {"status": "error", "message": "Sertifika ve anahtar dosyası adları geçersiz."}
        return result
    certificate_path, key_path = path.parent / str(config["certificate"]), path.parent / str(config["key"])
    try:
        parsed = urlsplit(origin) if isinstance(origin, str) else None
        parsed_port = parsed.port if parsed else None
    except ValueError:
        parsed, parsed_port = None, None
    address_ok = bool(parsed and parsed.scheme == "https" and parsed.hostname and not parsed.username
                     and not parsed.path and not parsed.query and not parsed.fragment
                     and type(port) is int and 1 <= port <= 65535
                     and (parsed_port or 443) == port and parsed.netloc == parsed.netloc.lower())
    if address_ok:
        result["address"] = {"status": "ok", "message": f"HTTPS adresi ve port eşleşiyor: {origin}"}
    else:
        result["address"] = {"status": "error", "message": "Adres https:// biçiminde olmalı ve port ayarıyla eşleşmeli."}

    try:
        import workspace_tools
        addresses = workspace_tools.local_addresses()
        result["addresses"] = addresses
        bind_ok = isinstance(bind, str) and bool(bind)
        if bind_ok:
            try:
                ipaddress.ip_address(bind)
            except ValueError:
                socket.getaddrinfo(bind, port if type(port) is int else 443)
        result["network"] = {
            "status": "warning" if bind_ok else "error",
            "message": ("Windows güvenlik duvarı, Wi-Fi istemci yalıtımı ve yönlendirici kuralları ayrı doğrulanmalıdır. "
                        "Güvenliği kapatmayın; telefonun aynı güvenilir ağa bağlı olduğunu ve yayımlanan adresi kullanabildiğini kontrol edin.")
            if bind_ok else "Bağlanma adresi geçersiz.",
        }
    except (OSError, ValueError, TypeError):
        result["network"] = {
            "status": "error",
            "message": "Bağlanma adresi çözümlenemedi. Windows ağ bağdaştırıcısını ve seçilen adresi kontrol edin.",
        }

    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certificate_path, key_path)
        certificate = x509.load_pem_x509_certificate(certificate_path.read_bytes())
        now = datetime.now(timezone.utc)
        if not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc:
            raise ValueError("Sertifika geçerli tarih aralığında değil.")
        hostname = parsed.hostname if parsed else ""
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        try:
            ip = ipaddress.ip_address(hostname)
            matched = ip in names.get_values_for_type(x509.IPAddress)
        except ValueError:
            dns_names = names.get_values_for_type(x509.DNSName)
            common_names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            candidates = dns_names or [item.value for item in common_names]
            matched = any(
                hostname == candidate or (
                    candidate.startswith("*.") and hostname.endswith(candidate[1:])
                    and hostname.count(".") == candidate.count(".")
                ) for candidate in candidates
            )
        if not matched:
            raise ValueError("Sertifika adı HTTPS adresiyle eşleşmiyor.")
        result["certificate"] = {
            "status": "ok",
            "message": f"Sertifika/anahtar eşleşiyor; geçerlilik {certificate.not_valid_after_utc.date().isoformat()} tarihine kadar. "
                       "Telefonun sertifika yetkilisine güvenmesi ayrıca gerekir.",
        }
    except (OSError, ValueError, ssl.SSLError, x509.ExtensionNotFound):
        result["certificate"] = {
            "status": "error",
            "message": "Sertifika süresi, adres adı veya özel anahtar doğrulanamadı; yeni geçerli sertifika/anahtar eşleştirin.",
        }

    if runtime is not None:
        result["service"] = {"status": "ok", "message": "Mobil HTTPS hizmeti çalışıyor."}
    elif startup_error:
        result["service"] = {"status": "error", "message": "Mobil HTTPS başlatılamadı; ayrıntılı neden yukarıdaki bölümlerde."}
    else:
        result["service"] = {"status": "warning", "message": "Ayarlar bulundu; hizmet uygulama başlangıcında etkinleştirilecek."}
    return result


def _panel(title, content):
    return f'<section class="panel"><h2>{escape(title)}</h2>{content}</section>'


def _page(content, mode, message="", nonce=""):
    nav = "".join(f'<a href="/?mode={key}"{" aria-current=true" if key == mode else ""}>{label}</a>'
                  for key, label in _MODES.items())
    status = f'<p class="status" role="status">{escape(message)}</p>' if message else ""
    monitor = (f"""<script nonce="{escape(nonce, quote=True)}">
      const sessionTimer=setInterval(async()=>{{
        try {{
          const response=await fetch("/session",{{cache:"no-store",credentials:"same-origin"}});
          if(!response.ok) {{
            clearInterval(sessionTimer);
            document.body.replaceChildren();
            const notice=document.createElement("p");
            notice.setAttribute("role","alert");
            notice.textContent="Oturum veya personel bağlantısı iptal edildi. Yeniden giriş yapın.";
            document.body.append(notice);
          }}
        }} catch {{
          clearInterval(sessionTimer);
          document.body.replaceChildren();
          const notice=document.createElement("p");
          notice.setAttribute("role","alert");
          notice.textContent="Güvenli bağlantı doğrulanamadı. Oturum içeriği kapatıldı.";
          document.body.append(notice);
        }}
      }},5000);
    </script>""" if nonce else "")
    return f"""<!doctype html><html lang="tr"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vardiya Mobil</title><style>{_STYLE}</style><body class="{mode}">
<header><h1>Vardiya Mobil</h1><p>Merkezi kayıt · Yerel ağ</p></header>
<main><nav aria-label="Ekran düzeni">{nav}</nav>{status}<div class="panels">{content}</div></main>{monitor}</body></html>"""


def _form(action, mode, contents, *, csrf=""):
    hidden = f'<input type="hidden" name="csrf" value="{escape(csrf)}">' if csrf else ""
    return f'<form method="post" action="/{action}?mode={mode}">{hidden}{contents}</form>'


def _login(mode):
    return _panel("Personel girişi", _form("employee-login", mode,
        '<label>Personel bağlantı kodu<input type="password" name="code" maxlength="43" required autocomplete="off"></label>'
        '<p>Windows uygulamasından verilen personel bağlantısının sonundaki kodu girin.</p><button>Programımı aç</button>')) + _panel(
        "Yönetici girişi", _form("admin-login", mode,
        '<label>Kullanıcı adı<input name="username" maxlength="80" required autocomplete="username"></label>'
        '<label>Parola<input type="password" name="password" maxlength="160" required autocomplete="current-password"></label><button>Yönetici olarak aç</button>'))


def _employee(store, employee_id, mode, csrf, token):
    plans = store._setting("published_plans", {})
    shifts = []
    for period in sorted(plans, reverse=True)[:12]:
        for row in plans[period].get("entries", ()):
            if row["employee_id"] != employee_id:
                continue
            for day, shift in row.get("days", {}).items():
                if shift.get("kind") == "work":
                    shifts.append((day, f'{shift["start"]}–{shift["end"]} · {row["section"]}'))
                elif shift.get("kind") == "weekly_off":
                    shifts.append((day, "Hafta tatili"))
    rows = "".join(f'<li><time>{escape(day)}</time> · {escape(label)}</li>' for day, label in sorted(shifts)[:370])
    changes = access_services._employee_plan_changes(plans, employee_id)
    change_rows = "".join(
        f'<tr><td>{escape(item["date"])}</td><td>{escape(item["section"])}</td>'
        f'<td>{escape(item["before"] or "Yeni vardiya")}</td>'
        f'<td>{escape(item["after"] or "Kaldırıldı")}</td></tr>'
        for item in changes[:80]
    )
    rendered_change_rows = change_rows or '<tr><td colspan="4">Yayımlanmış değişiklik yok.</td></tr>'
    receipts = {row["month"]: row for row in store.connection.execute(
        "SELECT month,revision FROM portal_plan_views WHERE token_hash=? AND confirmed=1",
        (access_services._token_hash(token),),
    )}
    confirmations = []
    for period in sorted(plans, reverse=True)[:12]:
        plan = plans[period]
        if not isinstance(plan, dict) or not any(
            isinstance(row, dict) and row.get("employee_id") == employee_id
            for row in plan.get("entries", ())
        ):
            continue
        revision = plan.get("source_revision")
        if not isinstance(revision, str) or not revision:
            continue
        receipt = receipts.get(period)
        if receipt and receipt["revision"] == revision:
            confirmations.append(f'<p role="status">{escape(period)} yayınının bu sürümünü okudunuz.</p>')
        else:
            confirmations.append(_form("plan-read", mode,
                f'<input type="hidden" name="month" value="{escape(period)}">'
                f'<input type="hidden" name="revision" value="{escape(revision)}">'
                '<button>Bu yayın sürümünü okudum</button>', csrf=csrf))
    history = store.connection.execute(
        "SELECT kind,start_date,end_date,status FROM portal_requests WHERE employee_id=? ORDER BY id DESC LIMIT 50",
        (employee_id,)).fetchall()
    labels = {"leave": "İzin", "swap": "Takas", "availability": "Uygunluk",
              "pending": "Bekliyor", "accepted": "İşlem bekliyor", "rejected": "Reddedildi"}
    requests = "".join(f'<li>{labels[row[0]]} · {escape(row[1])}–{escape(row[2])} · {labels[row[3]]}</li>' for row in history)
    swaps = []
    for item in operations.mutual_swaps(store, employee_id=employee_id):
        if item["status"] != "pending":
            continue
        index = item["employees"].index(employee_id)
        old, new = (item["pair"][i]["shift"] for i in (index, 1 - index))
        summary = escape(f"{item['date']} · {old['start']}–{old['end']} → {new['start']}–{new['end']}")
        accepted = employee_id in item["accepted"]
        response = ('<p>Yanıtınız kaydedildi.</p>' if accepted else '<button name="decision" value="accept">Kabul et</button>')
        response += '<button name="decision" value="decline">Reddet / kabulü geri çek</button>'
        swaps.append(f'<li>{summary}' + _form("swap-response", mode,
            f'<input type="hidden" name="id" value="{escape(item["id"])}">{response}', csrf=csrf) + '</li>')
    request_form = _form("request", mode,
        '<label>Tür<select name="kind"><option value="leave">İzin</option><option value="swap">Takas</option>'
        '<option value="availability">Uygunluk</option></select></label>'
        '<label>Başlangıç<input type="date" name="start_date" required></label>'
        '<label>Bitiş<input type="date" name="end_date" required></label>'
        '<label>Açıklama<textarea name="details" maxlength="300"></textarea></label><button>Talebi gönder</button>',
        csrf=csrf)
    return _panel("Yayımlanan programım", f'<ul>{rows or "<li>Yayımlanan vardiya yok.</li>"}</ul>') + _panel(
        "Programımdaki son değişiklikler",
        f'<p>Eski ve yeni yayınlanan vardiya bilgisi.</p>'
        '<table class="change-table"><thead><tr><th>Tarih</th><th>Bölüm</th>'
        '<th>Önceki vardiya</th><th>Yeni yayınlanan vardiya</th></tr></thead><tbody>'
        f'{rendered_change_rows}'
        '</tbody></table><h3>Yayın sürümü onayı</h3>'
        f'<p>Her yeni yayın sürümü için ayrı okuma onayı gerekir.</p>{"".join(confirmations)}') + _panel(
        "Taleplerim", request_form + f'<ul>{requests or "<li>Henüz talep yok.</li>"}</ul><h3>Karşılıklı takas</h3>'
        f'<ul>{"".join(swaps) or "<li>Bekleyen takas yok.</li>"}</ul>')


def _manager(store, mode, csrf, selected_day):
    rows = store.connection.execute(
        "SELECT r.id,r.kind,r.start_date,r.end_date,r.details,e.name FROM portal_requests r "
        "JOIN employees e ON e.id=r.employee_id WHERE r.status='pending' ORDER BY r.id DESC LIMIT 50").fetchall()
    requests = []
    labels = {"leave": "İzin", "swap": "Takas", "availability": "Uygunluk"}
    for row in rows:
        description = escape(f"{row['name']} · {labels[row['kind']]} · {row['start_date']}–{row['end_date']}")
        review = _form("review", mode, f'<input type="hidden" name="id" value="{row["id"]}">'
                       '<button name="decision" value="accepted">Kabul et</button>'
                       '<button name="decision" value="rejected">Reddet</button>', csrf=csrf)
        requests.append(f'<li><strong>{description}</strong><p>{escape(row["details"])}</p>{review}</li>')
    month = selected_day[:7]
    saved = store.schedule_month(month)
    names = {person["id"]: person["name"] for person in store.people(active_only=True)}
    shifts = [row for row in saved["entries"] if row["employee_id"] in names
              and row["days"].get(selected_day, {}).get("kind") == "work"]
    selector = (f'<form method="get" action="/"><input type="hidden" name="mode" value="{mode}">'
                f'<label>Gün<input type="date" name="day" value="{escape(selected_day)}" required></label>'
                '<button>Günü göster</button></form>')
    entries = []
    for row in shifts:
        shift = row["days"][selected_day]
        title = escape(f'{names[row["employee_id"]]} · {row["section"]}')
        fields = "".join(f'<input type="hidden" name="{key}" value="{escape(value)}">' for key, value in
                         (("day", selected_day), ("row_id", row["row_id"]), ("revision", saved["revision"])))
        fields += (f'<label>Başlangıç<input type="time" name="start" value="{escape(shift["start"])}" required></label>'
                   f'<label>Bitiş (SS:DD)<input name="end" value="{escape(shift["end"])}" maxlength="5" required></label>'
                   '<button>Bu vardiyayı kaydet</button>')
        entries.append(f'<li><strong>{title}</strong>{_form("edit", mode, fields, csrf=csrf)}</li>')
    result = "".join(entries) or "<li>Düzenlenebilir kayıtlı çalışma vardiyası yok.</li>"
    return _panel("Bekleyen talepler", '<p>Kabul, talebi işleme hazırlar; vardiyaya izin yazmaz.</p>'
                  f'<ul>{"".join(requests) or "<li>Bekleyen talep yok.</li>"}</ul>') + _panel(
        "Tek gün düzenle", selector + '<p>Yalnız kayıtlı çalışma saatleri değişir. Personel görünümü için Windows’tan yeniden yayımlayın.</p>'
        f'<ul>{result}</ul>')


def _edit_day(store, fields, authorize):
    if set(fields) != {"day", "row_id", "revision", "start", "end"} or not re.fullmatch(r"[a-f0-9]{32}", fields["revision"]):
        raise ValueError("Gün düzenleme isteği geçersiz.")
    day = date.fromisoformat(fields["day"]).isoformat()
    month = scheduling.validate_month(day[:7])

    def prepare():
        authorize()
        current = store.schedule_month(month)["entries"]
        entries = operations._shift_copy(current)
        row = next((row for row in entries if row["row_id"] == fields["row_id"]), None)
        shift = row["days"].get(day) if row else None
        if not shift or shift.get("kind") != "work" or row["employee_id"] not in {
                person["id"] for person in store.people(active_only=True)}:
            raise ValueError("Bu gün için düzenlenebilir çalışma vardiyası bulunamadı.")
        old_end = scheduling._time_interval(day, shift["start"], shift["end"])[1]
        shift.update(start=fields["start"], end=fields["end"])
        entries = scheduling.normalize_schedule_entries(month, entries, store.people())
        normalized = next(row for row in entries if row["row_id"] == fields["row_id"])["days"][day]
        new_end = scheduling._time_interval(day, normalized["start"], normalized["end"])[1]
        store.assert_date_range_unlocked(day, max(old_end, new_end).date().isoformat())
        neighbors = [entry for period in scheduling.adjacent_months(month) if period != month
                     for entry in store.schedule_month(period)["entries"]]
        names = {person["id"]: person["name"] for person in store.people()}
        controls.assert_no_new_overlaps(current + neighbors, entries + neighbors, names)
        prepared = linked_leave.prepare_schedule_save(store, month, entries)
        if prepared["entries"] != entries or any(
                item.get("date") == day and item.get("employee_id") == row["employee_id"]
                for item in prepared["conflicts"]):
            raise ValueError("Bağlı izin nedeniyle saat değişikliği kaydedilmedi.")
        return entries

    if not store._save_month("schedule_months", month, None, fields["revision"], "schedule.mobile_edit",
                             f"Mobil vardiya saati düzenlendi: {day}", prepare_payload=prepare):
        raise ValueError("Vardiya başka bir kullanıcı tarafından değiştirildi; güncel günü yeniden açın.")
    return day


class MobileServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 12

    def __init__(self, address, data_path, origin, context):
        super().__init__(address, MobileHandler, bind_and_activate=False)
        self.data_path = data_path
        self.tls = context
        self.origin = origin
        self.sessions = {}
        self.guard = threading.Lock()
        self.capacity = threading.BoundedSemaphore(16)
        try:
            self.server_bind()
            self.server_activate()
        except OSError:
            self.server_close()
            raise

    def process_request(self, request, client_address):
        if not self.capacity.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.capacity.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            try:
                request.settimeout(8)
                secured = self.tls.wrap_socket(request, server_side=True)
            except OSError:
                self.shutdown_request(request)
                return
            super().process_request_thread(secured, client_address)
        finally:
            self.capacity.release()


class MobileHandler(BaseHTTPRequestHandler):
    def log_request(self, code="-", size="-"):
        pass

    def _send(self, status, body, cookie=None, csp_nonce=""):
        content = body.encode("utf-8")
        self.send_response(status)
        csp = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        if csp_nonce:
            csp += f"; script-src 'nonce-{csp_nonce}'"
        for name, value in (
            ("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(content))),
            ("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer"), ("X-Content-Type-Options", "nosniff"),
            ("Content-Security-Policy", csp),
        ):
            self.send_header(name, value)
        if cookie is not None:
            ttl = 1800 if cookie else 0
            self.send_header("Set-Cookie", f"{_COOKIE}={cookie}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age={ttl}")
        self.end_headers()
        self.wfile.write(content)

    def _session(self, store):
        jar = SimpleCookie(self.headers.get("Cookie", ""))
        raw = jar[_COOKIE].value if _COOKIE in jar else ""
        with self.server.guard:
            record = self.server.sessions.get(raw)
        if not record or record["expires"] < time.monotonic():
            return raw, None
        if record["role"] == "employee":
            valid = access_services._valid_token(store.connection, record["credential"])
            device = access_services._valid_device_session(
                store.connection, raw, record["credential"], record["identity"],
            ) if valid and valid[0] == record["identity"] else None
            return (raw, record) if device else (raw, None)
        record_now = next((user for user in store._user_records() if user["username"] == record["identity"]), {})
        return (raw, record) if record_now.get("role") == "admin" and record_now.get("hash") == record["credential"] else (raw, None)

    def _login(self, store, path, fields):
        with store.connection:
            allowed = access_services._rate(store.connection, "mobile-login:" + self.client_address[0], 10, 300)
        if not allowed:
            raise ValueError("Çok fazla giriş denemesi. Birkaç dakika sonra tekrar deneyin.")
        if path == "/employee-login" and set(fields) == {"code"}:
            code = fields["code"].strip()
            person = access_services._valid_token(store.connection, code)
            if person:
                return "employee", person[0], code
        if path == "/admin-login" and set(fields) == {"username", "password"} \
                and len(fields["username"]) <= 80 and len(fields["password"]) <= 160:
            user = store.authenticate(fields["username"], fields["password"])
            if user and user["role"] == "admin":
                record = next(row for row in store._user_records() if row["username"] == user["username"])
                return "admin", user["username"], record["hash"]
        raise ValueError("Giriş bilgileri doğrulanamadı.")

    def _dispatch(self, write):
        parsed = urlsplit(self.path)
        routes = {"/employee-login", "/admin-login", "/logout", "/request", "/review", "/edit",
                  "/swap-response", "/plan-read"}
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc or parsed.netloc \
                or parsed.path not in (routes if write else {"/", "/session", "/plan-read"}):
            self._send(404, "Sayfa bulunamadı.")
            return
        mode = "auto"
        try:
            query = parse_qs(parsed.query, max_num_fields=4)
            mode = query.get("mode", ["auto"])[0]
            if mode not in _MODES:
                mode = "auto"
            day = date.fromisoformat(query.get("day", [date.today().isoformat()])[0]).isoformat()
            with Store(self.server.data_path) as store:
                raw, record = self._session(store)
                if record and record["role"] == "employee":
                    with store.connection:
                        access_services._record_portal_activity(store.connection, record["credential"])
                if not write and parsed.path == "/session":
                    self._send(204 if record else 401, "")
                    return
                message, cookie = "", None
                if write:
                    if self.headers.get("Origin") != self.server.origin or self.headers.get("Content-Type", "").split(";")[0] != "application/x-www-form-urlencoded":
                        self._send(403, "İstek doğrulanamadı.")
                        return
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 2048:
                        self._send(413, "İstek çok büyük veya boş.")
                        return
                    pairs = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True,
                                     max_num_fields=10, strict_parsing=True)
                    if any(len(values) != 1 for values in pairs.values()):
                        raise ValueError("İstek alanları geçersiz.")
                    fields = {key: values[0] for key, values in pairs.items()}
                    if parsed.path in {"/employee-login", "/admin-login"}:
                        role, identity, credential = self._login(store, parsed.path, fields)
                        record = {"role": role, "identity": identity, "credential": credential,
                                  "csrf": secrets.token_urlsafe(24), "expires": time.monotonic() + 1800}
                        cookie = secrets.token_urlsafe(32)
                        if role == "employee":
                            with store.connection:
                                access_services._record_device_session(
                                    store.connection, secrets.token_hex(12), cookie, credential,
                                    identity, access_services._now() + 1800,
                                )
                        with self.server.guard:
                            self.server.sessions = {key: value for key, value in self.server.sessions.items()
                                                    if key != raw and value["expires"] >= time.monotonic()}
                            if len(self.server.sessions) >= 500:
                                raise ValueError("Oturum kapasitesine ulaşıldı.")
                            self.server.sessions[cookie] = record
                    elif not record or not secrets.compare_digest(fields.pop("csrf", "").encode(), record["csrf"].encode()):
                        self._send(403, "Oturum geçersiz. Yeniden giriş yapın.")
                        return
                    elif parsed.path == "/logout":
                        if record["role"] == "employee":
                            with store.connection:
                                store.connection.execute(
                                    "UPDATE portal_device_sessions SET revoked_at=? WHERE session_hash=? "
                                    "AND revoked_at IS NULL",
                                    (access_services._now(), access_services._token_hash(raw)),
                                )
                        with self.server.guard:
                            self.server.sessions.pop(raw, None)
                        record, cookie = None, ""
                    else:
                        message, selected = self._write(store, record, parsed.path, fields)
                        day = selected or day
                if not record:
                    content = _login(mode)
                else:
                    args = (store, record["identity"], mode, record["csrf"], record["credential"])
                    content = (_employee(*args) if record["role"] == "employee"
                               else _manager(store, mode, record["csrf"], day))
                    content += _form("logout", mode, "<button>Oturumu kapat</button>", csrf=record["csrf"])
                nonce = secrets.token_urlsafe(18) if record else ""
                self._send(200, _page(content, mode, message, nonce), cookie, nonce)
        except (ValueError, UnicodeError) as exc:
            self._send(400, _page(_panel("İşlem yapılmadı", '<p><a href="/">Güncel ekrana dön</a></p>'), mode, str(exc)))
        except sqlite3.IntegrityError:
            self._send(409, _page(_panel("Kayıt değişmedi", '<p><a href="/">Güncel ekrana dön</a></p>'), mode,
                                  "Vardiya korumalı veya kayıt eş zamanlı değişti."))

    def _write(self, store, record, path, fields):
        if record["role"] == "employee" and path in ("/request", "/swap-response", "/plan-read"):
            store.connection.execute("BEGIN IMMEDIATE")
            with store.connection:
                valid = access_services._valid_token(store.connection, record["credential"])
                if not valid or valid[0] != record["identity"]:
                    raise ValueError("Personel bağlantısı artık geçerli değil.")
                if path == "/plan-read":
                    if set(fields) != {"month", "revision"}:
                        raise ValueError("Yayın onayı geçersiz.")
                    if not access_services._rate(
                        store.connection, "mobile-read:" + access_services._token_hash(record["credential"]),
                        120, 86400,
                    ):
                        raise ValueError("Okuma onayı sınırına ulaşıldı.")
                    if not access_services._confirm_plan_read(
                        store.connection, record["credential"], record["identity"],
                        fields["month"], fields["revision"], store._setting("published_plans", {}),
                    ):
                        raise ValueError("Yayın sürümü değişti; güncel programı yeniden açın.")
                    return "Yayın sürümü okundu olarak kaydedildi.", None
                if not access_services._rate(store.connection, "mobile-post:" + str(record["identity"]), 30, 86400):
                    raise ValueError("Günlük talep sınırına ulaşıldı.")
                if path == "/swap-response":
                    if set(fields) != {"id", "decision"} or not re.fullmatch(r"[a-f0-9]{24}", fields["id"]):
                        raise ValueError("Takas yanıtı geçersiz.")
                    operations.respond_mutual_swap(store, record["identity"], fields["id"], fields["decision"])
                    return "Takas yanıtınız kaydedildi.", None
                data = access_services._request_data(fields)
                store.connection.execute(
                    "INSERT INTO portal_requests(employee_id,kind,start_date,end_date,details,created_at) VALUES(?,?,?,?,?,?)",
                    (record["identity"], *data, access_services._now()))
                access_services._notify(store, access_services._recipients(store, "portal.review"), "request",
                                        "Yeni mobil personel talebi incelenmeyi bekliyor.")
            return "Talebiniz kaydedildi.", None
        if record["role"] == "admin" and path in ("/review", "/edit"):
            store.actor = record["identity"]
            def authorize():
                user = next((user for user in store._user_records() if user["username"] == store.actor), {})
                if user.get("role") != "admin" or user.get("hash") != record["credential"]:
                    raise ValueError("Yönetici oturumu artık geçerli değil.")
            if path == "/edit":
                return "Vardiya kaydedildi.", _edit_day(store, fields, authorize)
            if set(fields) != {"id", "decision"} or not re.fullmatch(r"[0-9]{1,18}", fields["id"]) \
                    or fields["decision"] not in ("accepted", "rejected"):
                raise ValueError("Talep kararı geçersiz.")
            store.connection.execute("BEGIN IMMEDIATE")
            with store.connection:
                authorize()
                changed = store.connection.execute(
                    "UPDATE portal_requests SET status=?,reviewed_at=?,reviewer=? WHERE id=? AND status='pending'",
                    (fields["decision"], access_services._now(), record["identity"], int(fields["id"]))).rowcount
                if not changed:
                    raise ValueError("Talep bulunamadı veya daha önce incelendi.")
                store.record_change("access.portal.review", f"Mobil talep incelendi: {fields['id']}",
                                    {"decision": fields["decision"]})
            return "Talep kararı kaydedildi.", None
        raise ValueError("Bu işlem için yetkiniz yok.")

    def do_GET(self):
        self._dispatch(False)

    def do_POST(self):
        self._dispatch(True)


def start(data_path, host, port, origin, certificate, key):
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.path or parsed.query or parsed.fragment \
            or (parsed.port or 443) != port or parsed.netloc != parsed.netloc.lower():
        raise ValueError("Mobil HTTPS adresi ve portu geçersiz.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, key)
    server = MobileServer((host, port), data_path, origin, context)
    threading.Thread(target=server.serve_forever, daemon=True, name="vardiya-mobile-https").start()
    return server
