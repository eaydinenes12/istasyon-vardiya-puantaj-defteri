"""Exercise safe tool dialogs through real WebView2 keyboard input."""
import json
import os
import sys
import tempfile
import threading
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from acceptance_118 import Browser, until
from acceptance_120_minimize import press_key, type_focused_text


SAFE_TOOLS = [
    ("Personel listesi  (Ctrl+Shift+P)", "personnel-dialog"),
    ("Vardiya türleri ve renkleri", "shift-types-dialog"),
    ("Şablon kitaplığı", "template-library-dialog"),
    ("Otomatik vardiya oluştur (döngü)", "rotation-dialog"),
    ("Vardiya değişimi (takas)", "swap-dialog"),
    ("Tatil takvimi", "holidays-dialog"),
    ("Asgari kadro saatleri", "staffing-windows-dialog"),
    ("Alternatif senaryolar", "schedule-scenarios-dialog"),
    ("Bölümler ve satırlar", "sections-dialog"),
    ("Görünüm: tema ve yazı boyutu", "appearance-dialog"),
    ("Klavye kısayolları ve ipuçları  (F1)", "shortcuts-dialog"),
    ("PDKS ↔ plan mutabakatı", "pdks-dialog"),
    ("Yayınlanan plan farkı", "plan-diff-dialog"),
    ("Puantaj onay akışı", "approval-dialog"),
    ("Yasal uyum ayarları", "compliance-dialog"),
    ("SGK eksik gün ve mesai", "payroll-dialog"),
    ("Rapor (R) belgeleri", "sick-report-dialog"),
    ("Yıllık izin devri ve ücreti", "leave-carry-dialog"),
    ("Adil dağılım raporu", "fairness-dialog"),
    ("İzin talep takvimi", "leave-requests-dialog"),
    ("Yetkinlik ve izin kuralları", "planning-rules-dialog"),
    ("Bölümler arası ödünç personel", "borrowed-dialog"),
    ("Geçmiş puantaj Excel'i içe aktar", "attendance-import-dialog"),
    ("Yönetici özeti (tek sayfa PDF)", "manager-summary-dialog"),
    ("Devamsızlık örüntüsü", "absence-pattern-dialog"),
    ("Tahmini personel maliyeti", "cost-estimate-dialog"),
    ("Yeni ay sihirbazı", "new-month-dialog"),
    ("Şubeler", "branches-dialog"),
    ("Ayarları dışa / içe aktar", "settings-transfer-dialog"),
    ("Uygulama bilgisi", "app-info-dialog"),
    ("Veri sağlığı ve onarım", "health-dialog"),
    ("Grafikler (ay özeti)", "month-charts-dialog"),
    ("Kişisel vardiya kartı (PNG)", "personal-card-dialog"),
    ("Görev panosu ve devir notları", "duty-board-dialog"),
    ("Bağlantılı izinler", "linked-leave-dialog"),
    ("Personel talepleri", "access-requests"),
    ("Personel bağlantıları", "access-links"),
    ("Özel yetkiler", "access-permissions"),
    ("Bildirimler", "access-notifications"),
    ("Personel ana sayfası", "personnel-home-dialog"),
    ("Personel / komut ara  (Ctrl+K)", "search-dialog"),
    ("Personel kartı", "employee-card-dialog"),
    ("Bugünün özeti", "dashboard-dialog"),
    ("Ay sonu kontrol sihirbazı", "month-end-dialog"),
    ("Bordro ekleri (avans, kesinti notu, yemek/yol)", "payroll-extras-dialog"),
    ("Yazdırma sihirbazı", "print-wizard"),
    ("Excel çıktı önizlemesi", "xl-output-preview"),
    ("Denkleştirme raporu", "balancing-report-dialog"),
    ("Çalışma düzenleri", "easy-workspaces"),
    ("Örnek tablo görünümü", "example-tables-dialog"),
    ("Güvenli veri aktarımı", "data-continuity-dialog"),
    ("Seçili yedek kurtarma", "data-continuity-dialog"),
    ("Çevrimdışı çalışma paketi", "data-continuity-dialog"),
    ("PDKS dosyası aktar ve kişileri eşleştir", "data-continuity-dialog"),
    ("Personel kimlik incelemesi", "data-continuity-dialog"),
    ("Kullanıcılar ve parolalar", "users-dialog"),
    ("Kayıt kilidi (ortak klasör)", "lock-dialog"),
    ("Uyarı merkezi", "alert-center"),
    ("Uyarı yönetimi", "warning-rules"),
    ("Başlangıç rehberi", "easy-detailed-guide"),
    ("Göreve göre yönlendirme", "easy-task-flows"),
    ("Kullanım ve hız ölçümü", "easy-usage-metrics"),
    ("Yenilikler", "whats-new-dialog"),
    ("Risksiz örnek çalışma", "easy-training"),
    ("Kısayol halkasını düzenle", "easy-ring-dialog"),
    ("Menüyü özelleştir", "easy-menu-dialog"),
    ("Belge ve tutanak dosyası", "easy-document-workflows"),
    ("İlişkili kayıtlar", "easy-document-workflows"),
    ("Yayımlanmış vardiya paylaşımı", "easy-document-workflows"),
    ("Hesap açıklaması", "easy-document-workflows"),
    ("Çıktı sürüm izleri", "easy-document-workflows"),
    ("Yedeği incele", "easy-document-workflows"),
    ("Vardiya programı PDF", "person-program-dialog"),
    ("E-posta taslağı (Outlook)", "mail-dialog"),
    ("E-posta otomatik gönderim ve adres defteri", "mail-dialog"),
    ("E-posta gönderim geçmişi", "mail-dialog"),
    ("Operasyon merkezi", "operations-center"),
    ("Telefondan görüntüleme (yerel ağ)", "phone-view-dialog"),
    ("Sorun raporu oluştur", "problem-report-dialog"),
    ("Yazdırma önizlemesi", "output-dialog"),
    ("Kaydetmeden önce değişiklikler", "easy-changes"),
    ("Eksik / hatalı gün kontrolü", "period-check-dialog"),
]

EXCLUDED = {
    "Puantajı plana döndür (seçim / tümü)": "doğrudan kayıt değiştirir",
    "Ay sonu paketi (ZIP)": "doğrudan dosya üretir",
    "Toplu giriş çalışma alanı": "seçili hücre gerektirir; ayrı WebView senaryosunda sınanır",
    "Biçimi şablona sıfırla": "doğrudan biçimi değiştirir",
    "Sıkı / rahat görünüm  (Ctrl+Shift+D)": "doğrudan görünümü değiştirir",
    "Bulut yedeği ayarı": "pencere yerine arşiv görünümüne geçer",
    "Örnek veri yükle": "onay sonrası veri oluşturur",
    "Uygulama güncelleme": "kurulu uygulamaya erişim bu testin dışında",
    "Kısayol halkasını aç / kapat": "doğrudan görünümü değiştirir",
    "Kısayol halkasını büyüt": "doğrudan kısayol ayarını değiştirir",
    "Kısayol halkasını küçült": "doğrudan kısayol ayarını değiştirir",
}

ADDITIONAL_TOOLS = [
    ("typeof openTechnicalErrors === 'function'", "Teknik hata kayıtları", "technical-errors-dialog"),
    ("typeof openMobileDiagnostics === 'function'", "Güvenli mobil erişim tanılama", "mobile-diagnostics-dialog"),
    ("typeof window.dataContinuity?.openFullDatabaseBackup === 'function'",
     "Şifreli tam veritabanı yedeği", "data-continuity-dialog"),
]


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
    from server import create_server
    from storage import Store

    db = folder / "synthetic.sqlite3"
    month = date.today().strftime("%Y-%m")
    day = month + "-01"
    with Store(db) as store:
        first = store.add_person("Klavye testi bir")["id"]
        second = store.add_person("Klavye testi iki")["id"]
        store.save_schedule_month(month, [
            {"row_id": "keyboard-one", "employee_id": first, "section": "MARKET", "slot": 1,
             "days": {day: {"kind": "work", "start": "09:00", "end": "17:00"}}},
            {"row_id": "keyboard-two", "employee_id": second, "section": "TEMİZLİK", "slot": 1,
             "days": {day: {"kind": "weekly_off"}}},
        ], "")
        store.save_attendance_month(month, {day: {str(first): "X", str(second): "H"}}, {}, "",
                                    roster={"added": [first, second], "removed": [], "order": [first, second]})
        baseline_schedule = store.schedule_month(month)
        baseline_attendance = store.attendance_month(month)

    server = create_server(db, ROOT / "web")
    server.easy_mode = True
    origin = f"http://127.0.0.1:{server.server_port}"
    api = DesktopAPI(origin, server.internal_token)
    api._window = SimpleNamespace(create_file_dialog=lambda *args, **kwargs: None)
    server.save_excel = api.save_excel
    server_thread = threading.Thread(target=server.serve_forever)
    window = webview.create_window("Acceptance 120 keyboard tools", origin, width=1440, height=900, focus=False)
    failures = []
    safe_tools = list(SAFE_TOOLS)

    def drive():
        page = Browser(window)
        try:
            if not window.events.loaded.wait(40):
                raise TimeoutError("WebView2 did not load")
            until("easy UI ready", lambda: page.js(
                "document.body.classList.contains('easy-mode') && !document.querySelector('#app-loader')"))
            page.js("""window.__keyboardErrors = [];
              window.addEventListener('error', event => window.__keyboardErrors.push(event.message));
              window.addEventListener('unhandledrejection', event => window.__keyboardErrors.push(String(event.reason)));
              window.__keyboardWrites = [];
              const originalFetch = window.fetch;
              window.fetch = (input, init) => {
                const method = String(init?.method || input?.method || 'GET').toUpperCase();
                if (!['GET', 'HEAD', 'OPTIONS'].includes(method))
                  window.__keyboardWrites.push({method, url:String(input?.url || input)});
                return originalFetch(input, init);
              };""")
            commands = page.js("[...document.querySelectorAll('#tools-menu [data-tool]')].map(e=>e.textContent.trim())")
            safe_tools.extend((label, dialog_id) for feature, label, dialog_id in ADDITIONAL_TOOLS if page.js(feature))
            actual = set(commands)
            covered = {label for label, _ in safe_tools}
            if len(commands) != len(actual) or covered & set(EXCLUDED) \
                    or actual != covered | set(EXCLUDED):
                raise AssertionError(f"Menu coverage mismatch: missing={(covered | set(EXCLUDED)) - actual!r}, "
                                     f"unexpected={actual - (covered | set(EXCLUDED))!r}, duplicates={len(commands)-len(actual)}")
            print(f"Production menu: {len(commands)} commands; {len(safe_tools)} safe dialogs; "
                  f"excluded: {json.dumps(EXCLUDED, ensure_ascii=False)}", flush=True)
            for label, dialog_id in safe_tools:
                page.js("document.querySelector('#tools-menu-button').focus()")
                press_key(page, " ", 32)
                until("tools menu opened", lambda: page.js("!document.querySelector('#tools-menu').hidden"))
                type_focused_text(page, "#easy-tools-search", label)
                until(f"search result {label}", lambda: page.js(f"""(() => {{
                  const label = {json.dumps(label)};
                  return [...document.querySelectorAll('.easy-search-result > button:first-child')]
                    .some(button => button.textContent.startsWith(label + ' · ') && !button.disabled);
                }})()"""))
                for _ in range(100):
                    press_key(page, "Tab", 9)
                    if page.js(f"document.activeElement?.textContent.startsWith({json.dumps(label + ' · ')})"):
                        break
                else:
                    raise AssertionError(f"Search command not reachable by Tab: {label}")
                press_key(page, " ", 32)
                until(f"dialog opened: {label}", lambda: page.js(
                    f"!!document.querySelector({json.dumps('#' + dialog_id + '[open]')})"))
                if not page.js(f"document.querySelector({json.dumps('#' + dialog_id)})?.contains(document.activeElement)"):
                    raise AssertionError(f"Initial focus outside dialog: {label}")
                press_key(page, "Tab", 9)
                if not page.js(f"document.querySelector({json.dumps('#' + dialog_id)})?.contains(document.activeElement)"):
                    raise AssertionError(f"Tab moved focus outside dialog: {label}")
                if dialog_id == "operations-center":
                    tool = None
                    for _ in range(10):
                        tool = page.js("document.activeElement?.dataset.opsTool")
                        if tool:
                            break
                        press_key(page, "Tab", 9)
                    if not tool:
                        raise AssertionError("Operation-center navigation was not reached by Tab")
                    press_key(page, "Tab", 9)
                    next_tool = page.js("document.activeElement?.dataset.opsTool")
                    if not next_tool or next_tool == tool:
                        raise AssertionError("Operation-center next tool was not reached by Tab")
                    tool = next_tool
                    press_key(page, " ", 32)
                    until("operation-center navigation preserves focus after redraw", lambda: page.js(
                        f"document.activeElement?.dataset.opsTool === {json.dumps(tool)}"
                        " && document.activeElement?.isConnected"))
                press_key(page, "Escape", 27)
                until(f"dialog closed and focus returned: {label}", lambda: page.js(
                    f"!document.querySelector({json.dumps('#' + dialog_id)})?.open"
                    " && document.activeElement === document.querySelector('#tools-menu-button')"))
                time.sleep(0.1)
                print(f"PASS keyboard open/Tab/Escape/focus: {label}", flush=True)
            errors = page.js("window.__keyboardErrors")
            if errors:
                raise AssertionError(f"Uncaught browser errors: {errors!r}")
            writes = page.js("window.__keyboardWrites")
            if writes:
                raise AssertionError(f"Dialog opening unexpectedly sent modifying requests: {writes!r}")
        except Exception as exc:
            failures.append(exc)
            print("Keyboard failure state:", page.js("""({
              active:document.activeElement?.outerHTML.slice(0,250),
              open:[...document.querySelectorAll('dialog[open]')].map(e=>e.id),
              toast:document.querySelector('#toast')?.textContent,
              errors:window.__keyboardErrors,
              search:document.querySelector('#easy-tools-search')?.value,
              results:[...document.querySelectorAll('.easy-search-result')].map(e=>e.textContent),
              tools:[...document.querySelectorAll('#tools-menu [data-tool]')].map(e=>e.textContent.trim())
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
    with Store(db) as store:
        if store.schedule_month(month) != baseline_schedule or store.attendance_month(month) != baseline_attendance:
            raise AssertionError("Opening or cancelling a dialog changed the isolated saved schedule or attendance")
    print(f"PASS: {len(safe_tools)} safe dialogs opened and closed by keyboard without final actions.", flush=True)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="acceptance-120-keyboard-") as directory:
        run(Path(directory))
