"use strict";
/* Grup G: kullanıcı oturumu, yetki rozeti, paylaşılan klasör kilidi, Outlook taslağı ve WhatsApp paylaşımı. */

const accessState = { enabled: false, user: null, lock: null, overlay: false, ready: false };

function accessCan(permission) {
  if (!accessState.ready) return false;
  if (!accessState.enabled || accessState.user?.role === "admin") return true;
  return accessState.user?.role === "operator" && Array.isArray(accessState.user.permissions)
    && accessState.user.permissions.includes(permission);
}

const accessEditSelectors = {
  "schedule.read": "#open-import, #open-import-mapping",
  "schedule.write": "#schedule-table, #shift-palette .palette-options, #shift-palette [data-action], #shift-palette [data-free-edit], #open-bulk-schedule-dialog, #copy-previous-week, #copy-previous-month, #suggest-week, #save-schedule, #undo-schedule, #redo-schedule, #open-extra-personnel, #apply-schedule-template, #open-quick-entry-settings, #apply-import, #undo-import, #save-import-profile, #delete-import-profile",
  "attendance.write": "#attendance-table, #save-attendance, #undo-attendance, #redo-attendance, #open-attendance-roster, #attendance-roster-dialog",
};
const accessAdminOnlySelectors = "#open-bulk-person-dialog, #add-person-form, #deactivate-person, #activate-person, #delete-person, #schedule-template-form, #delete-schedule-template, #save-overtime-settings, #open-attendance-events";

function applyAccessPermissions() {
  document.querySelectorAll(accessAdminOnlySelectors).forEach((node) => {
    if (!node.matches("button")) return;
    if (accessCanAdmin()) node.removeAttribute("aria-disabled");
    else node.setAttribute("aria-disabled", "true");
  });
  for (const [permission, selector] of Object.entries(accessEditSelectors)) {
    const allowed = accessCan(permission);
    document.querySelectorAll(selector).forEach((node) => {
      if (node.matches("table")) node.setAttribute("aria-readonly", String(!allowed));
      else if (node.matches("button")) {
        if (allowed) node.removeAttribute("aria-disabled");
        else node.setAttribute("aria-disabled", "true");
      }
    });
    const view = document.getElementById(permission.startsWith("schedule.") ? "schedule-view" : "attendance-view");
    if (!view) continue;
    let message = view.querySelector(":scope > [data-access-view-status]");
    if (!allowed && !message) {
      message = document.createElement("p");
      message.dataset.accessViewStatus = "";
      message.className = "extras-note";
      message.setAttribute("role", "status");
      view.prepend(message);
    }
    if (message) {
      message.hidden = allowed;
      message.textContent = accessState.ready ? "Bu görünümde düzenleme yetkiniz yok." : "Yetkiler kontrol ediliyor…";
    }
  }
}

function accessEditBlocked(target) {
  if (!target?.closest) return false;
  if (target.closest("[data-close-dialog], [data-extras-close], .dialog-close")) return false;
  if (target.closest('[data-action="attendance-deduction"]') && !accessCan("payroll")) return true;
  if (target.closest(accessAdminOnlySelectors) && !accessCanAdmin()) return true;
  return Object.entries(accessEditSelectors).some(([permission, selector]) =>
    target.closest(selector) && !accessCan(permission));
}

function accessCanAdmin() {
  return accessState.ready && (!accessState.enabled || accessState.user?.role === "admin");
}

function accessFingerprint(enabled, user) {
  if (!enabled) return "disabled";
  if (!user) return "anonymous";
  const permissions = Array.isArray(user.permissions) ? [...user.permissions].sort() : [];
  return JSON.stringify([user.username, user.role, permissions]);
}

function maskAndReloadForAccessChange() {
  const overlay = document.createElement("div");
  overlay.className = "login-overlay";
  overlay.innerHTML = '<p class="login-card" role="status">Yetkiler değişti. Ekran güncelleniyor…</p>';
  document.body.replaceChildren(overlay);
  location.reload();
}

function acceptAccessStatus(status) {
  const previous = accessFingerprint(accessState.enabled, accessState.user);
  const changed = accessState.ready
    && previous !== accessFingerprint(status.enabled, status.user);
  Object.assign(accessState, { enabled: status.enabled, user: status.user, lock: status.lock, ready: true });
  if (changed) {
    maskAndReloadForAccessChange();
    return true;
  }
  renderUserBadge();
  renderLockBanner();
  return false;
}

async function refreshAccessStatus() {
  const status = await request("/api/auth/status", { cache: "no-store" });
  return acceptAccessStatus(status);
}

function maskAfterAccessCheckFailure() {
  const overlay = document.createElement("div");
  overlay.className = "login-overlay";
  overlay.innerHTML = '<p class="login-card" role="alert">Yetkiler doğrulanamadı. Bu ekran güvenlik için kapatıldı; bağlantıdan sonra yeniden yükleyin.</p>';
  document.body.replaceChildren(overlay);
}

// Capture before workbook handlers so a read-only view cannot create an unsavable draft.
for (const type of ["click", "dblclick", "beforeinput", "paste", "cut", "drop", "dragstart", "submit", "keydown", "wheel"]) {
  document.addEventListener(type, (event) => {
    const target = event.target;
    let blocked = accessEditBlocked(target);
    if (type === "keydown" && !blocked && (event.ctrlKey || event.metaKey)
        && ["s", "z", "y"].includes(event.key.toLowerCase())
        && !document.querySelector("dialog[open]")) {
      const current = typeof state !== "undefined" ? state.view : "";
      blocked = (current === "schedule" || current === "attendance") && !accessCan(`${current}.write`);
    }
    if (!blocked) return;
    if (type === "wheel" && !target.matches?.("input[type=number], input[type=time]")) return;
    if (type === "keydown" && (event.key === "Tab" || event.key === "Escape"
        || (event.key.startsWith("Arrow") && !target.matches?.("select, input[type=number], input[type=time], input[type=date]"))
        || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c"))) return;
    const quickEntryButton = target.closest("#schedule-table .quick-entry-open");
    const cell = quickEntryButton?.closest("td[data-shift-day]");
    if (cell && typeof showScheduleCellNotice === "function") {
      showScheduleCellNotice(cell, accessState.ready
        ? "Bu işlem için yetkiniz yok."
        : "Yetkiler kontrol ediliyor…");
    }
    event.preventDefault();
    event.stopImmediatePropagation();
  }, { capture: true, passive: false });
}

(function wrapFetchForAccess() {
  const original = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const response = await original(input, init);
    const url = typeof input === "string" ? input : input?.url || "";
    if (url.includes("/api/") && !url.includes("/api/auth/")) {
      if (response.status === 401) showLoginOverlay();
      if (response.status === 403) {
        try {
          const statusResponse = await original("/api/auth/status");
          if (statusResponse.ok) {
            const status = await statusResponse.json();
            acceptAccessStatus(status);
          } else {
            maskAfterAccessCheckFailure();
          }
        } catch (error) {
          console.warn("Yetki durumu doğrulanamadı.", error);
          maskAfterAccessCheckFailure();
        }
      }
      if (response.status === 423) refreshLockState().catch(() => {});
    }
    return response;
  };
})();

function accessRoleLabel(role) {
  return role === "admin" ? "Yönetici" : role === "operator" ? "Özel yetkili" : "Yalnız görüntüleme";
}

function showLoginOverlay(message = "") {
  if (accessState.overlay) return;
  accessState.overlay = true;
  el("#app-loader")?.remove();
  const overlay = document.createElement("div");
  overlay.id = "login-overlay";
  overlay.className = "login-overlay";
  overlay.innerHTML = `<form class="login-card" autocomplete="on">
      <span class="brand-mark" aria-hidden="true">VP</span>
      <h1>Vardiya Planı &amp; Puantaj</h1>
      <p>Bu kayıtlar parola ile korunuyor. Devam etmek için oturum açın.</p>
      <label>Kullanıcı adı <input name="username" autocomplete="username" required maxlength="40"></label>
      <label>Parola <input name="password" type="password" autocomplete="current-password" required maxlength="128"></label>
      <p class="login-error" role="alert">${escapeHtml(message)}</p>
      <button class="button button-primary" type="submit">Oturum aç</button>
      <small>Parolayı unuttuysanız: veritabanının bulunduğu klasöre (varsayılan olarak VardiyaPuantaj.exe'nin yanındaki <code>data</code> klasörü) <code>reset_users.txt</code> adlı boş bir dosya koyup uygulamayı yeniden başlatın.</small>
    </form>`;
  document.body.append(overlay);
  const form = overlay.querySelector("form");
  form.username.focus();
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      await request("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: form.username.value, password: form.password.value }),
      });
      location.reload();
    } catch (error) {
      form.querySelector(".login-error").textContent = error.message.split(" Düzeltme önerisi")[0];
      form.password.select();
      button.disabled = false;
    }
  });
}

function renderUserBadge() {
  let badge = el("#user-badge");
  const topbar = el(".topbar");
  applyAccessPermissions();
  if (!topbar) return;
  if (!accessState.enabled || !accessState.user) { badge?.remove(); document.body.classList.remove("role-viewer"); return; }
  if (!badge) {
    badge = document.createElement("div");
    badge.id = "user-badge";
    badge.className = "user-badge";
    const anchor = el("#save-state");
    if (anchor?.parentNode) anchor.parentNode.insertBefore(badge, anchor);
    else topbar.append(badge);
  }
  const viewer = accessState.user.role === "viewer";
  document.body.classList.toggle("role-viewer", viewer);
  badge.innerHTML = `<span class="user-badge-name" title="${escapeHtml(accessRoleLabel(accessState.user.role))}">${escapeHtml(accessState.user.username)}${viewer ? ' <em>· görüntüleme</em>' : ""}</span>
    <button type="button" class="user-badge-out" title="Oturumu kapat">Çıkış</button>`;
  badge.querySelector("button").addEventListener("click", async () => {
    await request("/api/auth/logout", { method: "POST", body: "{}" }).catch(() => {});
    location.reload();
  });
}

function renderLockBanner() {
  let banner = el("#lock-banner");
  const lock = accessState.lock;
  if (!lock?.read_only) { banner?.remove(); document.body.classList.remove("records-locked"); return; }
  document.body.classList.add("records-locked");
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "lock-banner";
    banner.className = "lock-banner";
    banner.setAttribute("role", "alert");
    el(".app-shell")?.prepend(banner);
  }
  const holder = lock.holder || {};
  banner.innerHTML = `<span><strong>Salt okunur:</strong> kayıtlar şu anda <b>${escapeHtml(holder.user || "başka bir kullanıcı")}</b>
    (${escapeHtml(holder.host || "başka bilgisayar")}) tarafından düzenleniyor. Değişiklikleriniz kaydedilmez.</span>
    <button type="button" class="button button-light">Düzenlemeyi devral</button>`;
  banner.querySelector("button").addEventListener("click", takeOverLock);
}

async function refreshLockState() {
  accessState.lock = await request("/api/lock");
  renderLockBanner();
  return accessState.lock;
}

async function takeOverLock() {
  const holder = accessState.lock?.holder || {};
  if (!confirm(`${holder.user || "Diğer kullanıcı"} (${holder.host || "?"}) hâlâ düzenliyor olabilir. Devralırsanız onun kaydetmediği değişiklikler kaydedilemez. Devralınsın mı?`)) return;
  accessState.lock = await request("/api/lock/take-over", { method: "POST", body: "{}" });
  renderLockBanner();
  toast("Düzenleme kilidi bu bilgisayara alındı. Güncel veriler için sayfa yenileniyor.");
  setTimeout(() => location.reload(), 800);
}

async function openLockStatus() {
  const lock = await refreshLockState();
  const dialog = extrasDialog("lock-dialog", "Kayıt kilidi", "Veri paylaşılan klasördeyse aynı anda yalnız bir bilgisayar değişiklik yapabilir.");
  const holder = lock.holder || {};
  dialog.querySelector(".extras-body").innerHTML = lock.read_only
    ? `<p class="lock-state lock-state-busy">Kayıtlar <b>${escapeHtml(holder.user)}</b> (${escapeHtml(holder.host)}) tarafından düzenleniyor${holder.since ? `, son sinyal ${escapeHtml(holder.since)}` : ""}.</p>
       <p>Diğer bilgisayar kapanırsa kilit 2 dakika içinde kendiliğinden düşer. Acil durumda devralabilirsiniz.</p>
       <button type="button" class="button button-primary" data-take>Düzenlemeyi devral</button>`
    : `<p class="lock-state lock-state-ok">Düzenleme kilidi bu bilgisayarda (${escapeHtml(holder.host)} · ${escapeHtml(holder.user)}).</p>
       <p>Başka bir bilgisayar aynı veri klasörünü açarsa onu salt okunur modda görür. Veri klasörünü ortak klasöre taşımak için uygulamayı <code>VARDIYA_PUANTAJ_DATA_DIR</code> ortam değişkeniyle başlatın.</p>`;
  dialog.querySelector("[data-take]")?.addEventListener("click", () => takeOverLock().catch((error) => toast(error.message, true)));
  showExtras(dialog);
}

async function openUsers() {
  const dialog = extrasDialog("users-dialog", "Kullanıcılar ve parolalar",
    "Yönetici her şeyi değiştirebilir; yalnız görüntüleme hesabı tabloları görür, rapor ve çıktı alır ama kaydedemez.");
  const body = dialog.querySelector(".extras-body");
  const render = (users) => {
    body.innerHTML = `${users.length ? "" : `<p class="extras-note">Şu anda parola koruması kapalı. İlk eklediğiniz hesap <b>yönetici</b> olmalıdır; ekledikten sonra uygulama her açılışta parola ister.</p>`}
      ${users.length ? `<table class="extras-table"><thead><tr><th>Kullanıcı</th><th>Rol</th><th></th></tr></thead><tbody>
        ${users.map((user) => `<tr><td>${escapeHtml(user.username)}${user.username === accessState.user?.username ? " <em>(siz)</em>" : ""}</td>
          <td>${escapeHtml(accessRoleLabel(user.role))}</td>
          <td><button type="button" class="button button-light" data-edit="${escapeHtml(user.username)}" data-role="${user.role}">Düzenle</button>
          <button type="button" class="button button-light" data-delete="${escapeHtml(user.username)}">Sil</button></td></tr>`).join("")}
      </tbody></table>` : ""}
      <form class="users-form forms-fields" autocomplete="off">
        <label>Kullanıcı adı <input name="username" required maxlength="40"></label>
        <label>Parola <input name="password" type="password" maxlength="128" autocomplete="new-password" placeholder="En az 6 karakter"></label>
        <label>Rol <select name="role"><option value="admin">Yönetici</option><option value="viewer"${users.length ? " selected" : ""}>Yalnız görüntüleme</option><option value="operator">Özel yetkili</option></select></label>
        <button class="button button-primary" type="submit">Kaydet</button>
      </form>
      <p class="dialog-help">Var olan hesabı düzenlerken parolayı boş bırakırsanız eski parola korunur. Son yönetici silinemez. Tüm hesapları silerseniz parola koruması kapanır.</p>`;
    const form = body.querySelector("form");
    body.querySelectorAll("[data-edit]").forEach((button) => button.addEventListener("click", () => {
      form.username.value = button.dataset.edit;
      form.role.value = button.dataset.role;
      form.password.value = "";
      form.password.focus();
    }));
    body.querySelectorAll("[data-delete]").forEach((button) => button.addEventListener("click", () => extrasRun(button, async () => {
      if (!confirm(`“${button.dataset.delete}” hesabı silinsin mi?`)) return;
      const result = await request("/api/users/delete", { method: "POST", body: JSON.stringify({ username: button.dataset.delete }) });
      if (button.dataset.delete === accessState.user?.username || !result.users.length) { location.reload(); return; }
      toast("Hesap silindi.");
      render(result.users);
    })));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const current = users.find((user) => user.username.toLocaleLowerCase("tr-TR") === form.username.value.trim().toLocaleLowerCase("tr-TR"));
      const needsAdminConfirmation = users.length && form.role.value === "admin" && current?.role !== "admin";
      if (needsAdminConfirmation &&
          !confirm("Yönetici tüm kayıtlara ve yetkilere erişebilir. Yönetici yetkisi verilsin mi?")) return;
      extrasRun(form.querySelector("button"), async () => {
        const wasEnabled = accessState.enabled;
        const result = await request("/api/users", { method: "PUT", body: JSON.stringify({
          username: form.username.value, password: form.password.value, role: form.role.value,
          ...(needsAdminConfirmation ? { confirm_admin: true } : {}),
        }) });
        toast("Hesap kaydedildi.");
        if (!wasEnabled) { location.reload(); return; }
        render(result.users);
      });
    });
  };
  render((await request("/api/users")).users);
  showExtras(dialog);
}

function mailStatusLabel(status) {
  const labels = {
    sent: "Gönderildi", error: "Başarısız", unknown: "Gönderim sonucu belirsiz",
    partial: "Kısmen kabul edildi", sending: "Gönderiliyor", retry: "Yeniden denenecek",
    revised: "Plan değişti", expired: "Eski dönem denemesi iptal edildi",
  };
  return labels[status] || labels.unknown;
}

async function openMailDraft(options = {}) {
  const month = state.view === "attendance" ? state.attendanceMonth : state.scheduleMonth;
  const draftKindLabels = {
    attendance: "Puantaj (Excel)", schedule: "Vardiya listesi (Excel, seçili hafta)",
    "manager-summary": "Yönetici özeti (PDF)", "month-package": "Ay sonu paketi (ZIP)",
  };
  const jobKindLabels = {
    attendance: "Puantaj (Excel)", schedule: "Vardiya listesi (Excel)",
    "manager-summary": "Yönetici özeti (PDF)", "month-package": "Ay sonu paketi (ZIP)",
  };
  const kindChecks = (labels, checkedKind) => Object.entries(labels).map(([value, label]) =>
    `<label class="extras-check"><input name="kinds" type="checkbox" value="${value}"${value === checkedKind ? " checked" : ""}> ${escapeHtml(label)}</label>`).join("");
  const dialog = extrasDialog("mail-dialog", "E-posta taslağı (Outlook)",
    "Seçtiğiniz dosyalar ekli bir taslak oluşturulur ve varsayılan posta programında açılır; göndermeden önce kontrol edebilirsiniz.");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = `<form class="mail-form forms-fields">
      <fieldset class="mail-wide mail-kinds">
        <legend>Ekler <small>(birden fazla seçilebilir, en fazla 4)</small></legend>
        ${kindChecks(draftKindLabels, state.view === "attendance" ? "attendance" : "schedule")}
      </fieldset>
      <label>Ay <input name="month" type="month" value="${escapeHtml(month)}" required></label>
      <label class="mail-wide">Alıcı(lar) <input name="to" type="text" placeholder="ik@firma.com; muhasebe@firma.com" maxlength="500"></label>
      <label class="mail-wide">Adres defteri <span class="mail-contact-row">
        <select name="contact"><option value="">Kayıtlı adres seçin</option></select>
        <input name="contact-name" type="text" maxlength="80" placeholder="Yeni kişi adı">
        <input name="contact-email" type="email" maxlength="254" placeholder="Yeni e-posta adresi">
        <button class="button button-light" type="button" data-mail-save-contact>Adresi kaydet</button>
      </span></label>
      <label class="mail-wide">Konu <input name="subject" type="text" maxlength="200" placeholder="Boş bırakılırsa otomatik yazılır"></label>
      <label class="mail-wide">Mesaj <textarea name="body" rows="5" maxlength="5000" placeholder="Boş bırakılırsa kısa bir açıklama yazılır"></textarea></label>
      <div class="mail-wide mail-preview"><strong>E-posta önizlemesi</strong><pre data-mail-preview></pre></div>
      <p class="mail-wide mail-batch-note">Birden fazla eki aynı e-postaya ekleyebilirsiniz; “Ay sonu paketi (ZIP)” ayın tüm raporlarını tek dosyada toplar.</p>
      <button class="button button-primary" type="submit">Taslağı oluştur ve aç</button>
    </form>
    <p class="extras-note" data-result role="status">Taslak gönderilmez; son gönderim posta programında sizin onayınızla yapılır.</p>
    <details class="mail-automation-panel">
      <summary>SMTP hesabı ve otomatik rapor gönderimi</summary>
      <p class="extras-note">Plan etkinleştirildiğinde uygulama açık olduğu sürece rapor seçilen aralıkta gönderilir. Parola bu Windows kullanıcı hesabıyla şifrelenerek saklanır.</p>
      <p class="mail-automation-warning" role="note">Otomatik gönderimler yalnız uygulama açıkken çalışır; uygulama kapalıyken kaçan son gönderim uygulama açıldığında bir kez gönderilir.</p>
      <p class="extras-note" data-mail-retry-note>Sonucu belirsiz veya kısmen kabul edilen bir gönderim otomatik olarak tekrarlanmaz.</p>
      <form class="mail-settings forms-fields" data-mail-settings>
        <label>SMTP sunucusu <input name="host" type="text" maxlength="255" placeholder="smtp.ornek.com" autocomplete="off"></label>
        <label>Bağlantı noktası <input name="port" type="number" min="1" max="65535" value="587" required></label>
        <label>Kullanıcı adı <input name="username" type="text" maxlength="254" autocomplete="username"></label>
        <label>Gönderen adresi <input name="from_address" type="email" maxlength="254" autocomplete="email"></label>
        <label>Uygulama parolası <input name="password" type="password" maxlength="500" autocomplete="new-password" placeholder="Kayıtlıysa boş bırakın"></label>
        <label class="extras-check"><input name="use_ssl" type="checkbox"> SSL/TLS ile doğrudan bağlan (465)</label>
        <label class="extras-check"><input name="enabled" type="checkbox"> SMTP hesabını etkinleştir</label>
        <button class="button button-light" type="button" data-mail-save-settings>SMTP ayarlarını kaydet</button>
      </form>
      <section class="mail-recipient-permissions" aria-labelledby="mail-recipient-title">
        <h3 id="mail-recipient-title">Sunucu alıcı izinleri</h3>
        <p class="extras-note">Otomatik gönderim yalnız burada etkin personelle eşleştirilmiş adreslere yapılır. Bölüm yetkisi sunucuda uygulanır; pasif personel engellenir.</p>
        <form class="mail-recipient-form forms-fields" data-mail-recipient>
          <label>Personel <select name="employee_id" required><option value="">Yükleniyor…</option></select></label>
          <label>E-posta adresi <input name="email" type="email" maxlength="254" required></label>
          <label data-mail-recipient-section>Bölüm kapsamı <select name="section_scope" required></select></label>
          <label class="extras-check"><input name="all_sections" type="checkbox"> Tüm bölümlere izin ver</label>
          <button class="button button-light" type="submit">Alıcı yetkisini kaydet</button>
        </form>
        <div class="mail-recipient-list" data-mail-recipient-list role="status" aria-live="polite">Sunucu izinleri yükleniyor…</div>
      </section>
      <form class="mail-automation-form forms-fields" data-mail-auto-job>
        <input name="id" type="hidden">
        <fieldset class="mail-wide mail-kinds">
          <legend>Rapor ekleri <small>(birden fazla seçilebilir)</small></legend>
          ${kindChecks(jobKindLabels, "attendance")}
        </fieldset>
        <label>Sıklık <select name="cadence">
          <option value="hourly">Saatlik</option><option value="daily">Günlük</option>
          <option value="weekly">Haftalık</option><option value="monthly">Aylık</option>
        </select></label>
        <label data-mail-hourly-minute hidden>Her saatin dakikası <select name="minute">${Array.from({ length: 60 }, (_, minute) =>
          `<option value="${minute}">${String(minute).padStart(2, "0")}</option>`).join("")}</select></label>
        <label data-mail-weekday>Haftanın günü <select name="weekday">
          <option value="0">Pazartesi</option><option value="1">Salı</option><option value="2">Çarşamba</option>
          <option value="3">Perşembe</option><option value="4">Cuma</option><option value="5">Cumartesi</option><option value="6">Pazar</option>
        </select></label>
        <label data-mail-month-day hidden>Ayın günü <input name="month_day" type="number" min="1" max="31" value="1">
          <small class="mail-field-hint">29–31 seçilirse kısa aylarda ayın son günü gönderilir.</small></label>
        <label data-mail-send-time>Gönderim saati <input name="time" type="time" value="09:00" required></label>
        <label class="mail-wide">Alıcı(lar) <input name="to" type="text" maxlength="500" placeholder="ik@firma.com; muhasebe@firma.com" required></label>
        <label class="mail-wide">Konu <input name="subject" type="text" maxlength="200" placeholder="Boş bırakılırsa otomatik yazılır"></label>
        <label class="mail-wide">Mesaj <textarea name="body" rows="3" maxlength="5000"></textarea></label>
        <label class="mail-wide extras-check"><input name="enabled" type="checkbox" checked> Bu planı etkinleştirip otomatik gönder</label>
        <p class="mail-wide mail-automation-preview" data-mail-auto-preview></p>
        <div class="mail-wide dialog-actions"><button class="button button-primary" type="submit">Gönderim planını kaydet</button>
          <button class="button button-light" type="button" data-mail-cancel-edit hidden>Düzenlemeyi bırak</button></div>
      </form>
      <div class="mail-automation-jobs" data-mail-auto-jobs role="status" aria-live="polite">E-posta ayarları yükleniyor…</div>
      <div class="mail-attachment-preview" data-mail-recipient-preview hidden role="status" aria-live="polite"></div>
      <p class="extras-note" data-mail-auto-status role="status"></p>
      <section class="mail-history">
        <h3>Gönderim geçmişi</h3>
        <div class="mail-history-scroll" data-mail-history><p class="archive-empty">Yükleniyor…</p></div>
        <button class="button button-light" type="button" data-mail-history-toggle hidden>Tümünü göster</button>
      </section>
    </details>`;
  const form = body.querySelector("form");
  const validEmail = /^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$/;
  const readContacts = () => {
    try {
      const contacts = JSON.parse(localStorage.getItem("vp-mail-address-book") || "[]");
      return Array.isArray(contacts) ? contacts.filter((contact) =>
        contact && typeof contact.name === "string" && typeof contact.email === "string"
        && validEmail.test(contact.email)) : [];
    } catch { return []; }
  };
  const renderContacts = () => {
    form.contact.innerHTML = '<option value="">Kayıtlı adres seçin</option>'
      + readContacts().map((contact) => `<option value="${escapeHtml(contact.email)}">${escapeHtml(contact.name)} · ${escapeHtml(contact.email)}</option>`).join("");
  };
  const selectedDraftKinds = () => [...form.querySelectorAll('input[name="kinds"]:checked')].map((input) => input.value);
  const renderMailPreview = () => {
    const names = selectedDraftKinds().map((kind) => draftKindLabels[kind]);
    const recipient = form.to.value.trim() || "Alıcı belirtilmedi";
    const subject = form.subject.value.trim()
      || `${names.map((name) => name.split(" (")[0]).join(", ") || "Rapor"} - ${form.month.value}`;
    const message = form.body.value.trim() || (names.length > 1
      ? `${form.month.value} dönemine ait aşağıdaki dosyalar ektedir:\n${names.map((name) => `- ${name}`).join("\n")}`
      : `${form.month.value} dönemine ait ${(names[0] || "rapor").toLocaleLowerCase("tr-TR")} ektedir.`);
    const attachments = names.length
      ? names.map((name) => `• ${name} · ${form.month.value}`).join("\n")
      : "Ek seçilmedi; en az bir ek seçin.";
    body.querySelector("[data-mail-preview]").textContent =
      `Kime: ${recipient}\nKonu: ${subject}\n\n${message}\n\nEkler (${names.length}):\n${attachments}`;
  };
  try {
    const saved = JSON.parse(localStorage.getItem("vp-mail-to") || "\"\"");
    if (typeof saved === "string") form.to.value = saved;
  } catch (error) { /* kayıtlı alıcı okunamadı */ }
  renderContacts();
  renderMailPreview();
  form.addEventListener("input", renderMailPreview);
  form.addEventListener("change", (event) => {
    if (event.target === form.contact && form.contact.value) {
      const addresses = form.to.value.split(/[;,]/).map((value) => value.trim()).filter(Boolean);
      if (!addresses.some((value) => value.toLocaleLowerCase("en-US") === form.contact.value.toLocaleLowerCase("en-US"))) {
        const next = [...addresses, form.contact.value].join("; ");
        if (next.length <= 500) form.to.value = next;
        else toast("Alıcı alanı 500 karakter sınırını aşar; önce bazı adresleri kaldırın.", true);
      }
    }
    renderMailPreview();
  });
  form.querySelector("[data-mail-save-contact]").addEventListener("click", () => {
    const name = form["contact-name"].value.trim();
    const email = form["contact-email"].value.trim();
    if (!name || !validEmail.test(email)) {
      toast("Adres defterine eklemek için kişi adı ve geçerli bir e-posta adresi girin.", true);
      return;
    }
    const contacts = readContacts();
    if (contacts.length >= 100 && !contacts.some((contact) => contact.email.toLowerCase() === email.toLowerCase())) {
      toast("Adres defterinde en fazla 100 kişi saklanabilir.", true);
      return;
    }
    const updated = contacts.filter((contact) => contact.email.toLowerCase() !== email.toLowerCase());
    updated.push({ name, email });
    localStorage.setItem("vp-mail-address-book", JSON.stringify(updated));
    renderContacts();
    form.contact.value = email;
    form.contact.dispatchEvent(new Event("change", { bubbles: true }));
    form["contact-name"].value = "";
    form["contact-email"].value = "";
    toast("Adres defterine kaydedildi.");
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const kinds = selectedDraftKinds();
    if (!kinds.length) {
      toast("En az bir e-posta eki seçin.", true);
      return;
    }
    extrasRun(form.querySelector('[type="submit"]'), async () => {
      localStorage.setItem("vp-mail-to", JSON.stringify(form.to.value));
      const result = await request("/api/mail-draft", { method: "POST", body: JSON.stringify({
        kinds, month: form.month.value, week: state.weekIndex || 0,
        to: form.to.value, subject: form.subject.value, body: form.body.value,
      }) });
      const files = (result.attachments || [result.attachment]).join(", ");
      body.querySelector("[data-result]").textContent = result.opened
        ? `Taslak açıldı (ekler: ${files}). Açılmadıysa dosya: ${result.path}`
        : `Taslak dosyası hazır (ekler: ${files}): ${result.path}`;
    });
  });
  const settingsForm = body.querySelector("[data-mail-settings]");
  const jobForm = body.querySelector("[data-mail-auto-job]");
  const jobsList = body.querySelector("[data-mail-auto-jobs]");
  const autoStatus = body.querySelector("[data-mail-auto-status]");
  const recipientForm = body.querySelector("[data-mail-recipient]");
  const recipientList = body.querySelector("[data-mail-recipient-list]");
  const recipientPreview = body.querySelector("[data-mail-recipient-preview]");
  let recipientState = null;
  const loadRecipientPermissions = async () => {
    const result = await request("/api/mail-automation/recipients");
    recipientState = result;
    const employeeSelect = recipientForm.elements.namedItem("employee_id");
    employeeSelect.innerHTML = '<option value="">Personel seçin</option>' + result.people.map((person) =>
      `<option value="${person.id}" data-section="${escapeHtml(person.home_section || "")}"${person.active ? "" : " disabled"}>${escapeHtml(person.name)}${person.active ? "" : " · pasif"}</option>`).join("");
    const sectionSelect = recipientForm.elements.namedItem("section_scope");
    sectionSelect.innerHTML = '<option value="">Bölüm seçin</option>' + result.sections.map((section) =>
      `<option value="${escapeHtml(section)}">${escapeHtml(section)}</option>`).join("");
    recipientList.innerHTML = result.recipients.length ? result.recipients.map((recipient) =>
      `<article class="mail-recipient-permission" data-mail-recipient-active="${recipient.active}">
        <div><strong>${escapeHtml(recipient.name)}</strong><span>${escapeHtml(recipient.email)}</span>
          <small>${recipient.active ? "Etkin personel" : "Pasif personel · gönderim engelli"} · ${
            recipient.all_sections ? "Tüm bölümler" : `${escapeHtml(recipient.section_scope)} bölümü`}</small></div>
        <button class="button button-light" type="button" data-mail-recipient-remove="${escapeHtml(recipient.email)}">Yetkiyi kaldır</button>
      </article>`).join("") : '<p class="archive-empty">Henüz sunucu alıcı yetkisi tanımlanmadı.</p>';
  };
  recipientForm.addEventListener("change", (event) => {
    if (event.target === recipientForm.elements.namedItem("employee_id")) {
      const option = event.target.selectedOptions[0];
      if (option?.dataset.section) recipientForm.elements.namedItem("section_scope").value = option.dataset.section;
    }
    if (event.target === recipientForm.elements.namedItem("all_sections")) {
      const allSections = event.target.checked;
      const scope = recipientForm.elements.namedItem("section_scope");
      scope.disabled = allSections;
      body.querySelector("[data-mail-recipient-section]").hidden = allSections;
      if (allSections) scope.value = "";
    }
  });
  recipientForm.addEventListener("submit", (event) => {
    event.preventDefault();
    extrasRun(recipientForm.querySelector('[type="submit"]'), async () => {
      const allSections = recipientForm.elements.namedItem("all_sections").checked;
      await request("/api/mail-automation/recipients", {
        method: "PUT",
        body: JSON.stringify({
          email: recipientForm.elements.namedItem("email").value.trim(),
          employee_id: Number(recipientForm.elements.namedItem("employee_id").value),
          section_scope: allSections ? "" : recipientForm.elements.namedItem("section_scope").value,
          all_sections: allSections,
        }),
      });
      recipientForm.reset();
      const scope = recipientForm.elements.namedItem("section_scope");
      scope.disabled = false;
      body.querySelector("[data-mail-recipient-section]").hidden = false;
      await loadRecipientPermissions();
      toast("Sunucu alıcı yetkisi kaydedildi.");
    });
  });
  recipientList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-mail-recipient-remove]");
    if (!button) return;
    extrasRun(button, async () => {
      const email = button.dataset.mailRecipientRemove;
      if (!window.confirm(`${email} adresinin sunucu alıcı yetkisi kaldırılsın mı?`)) return;
      await request("/api/mail-automation/recipients/remove", {
        method: "POST", body: JSON.stringify({ email }),
      });
      await loadRecipientPermissions();
      toast("Sunucu alıcı yetkisi kaldırıldı.");
    });
  });
  const saveSettingsButton = settingsForm.querySelector("[data-mail-save-settings]");
  const saveJobButton = jobForm.querySelector('[type="submit"]');
  saveSettingsButton.disabled = true;
  saveJobButton.disabled = true;
  const cancelEdit = body.querySelector("[data-mail-cancel-edit]");
  let mailAutomation = null;
  const weekdayNames = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"];
  let editingJobId = "";
  let historyExpanded = false;
  const historyPreviewRows = 15;
  const selectedJobKinds = () => [...jobForm.querySelectorAll('input[name="kinds"]:checked')].map((input) => input.value);
  const jobKindText = (kinds) => kinds.map((kind) => jobKindLabels[kind] || kind).join(" + ");
  const formatMailTime = (value) => {
    if (!value) return "";
    const moment = new Date(value);
    return Number.isNaN(moment.getTime()) ? "" : moment.toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" });
  };
  const updateCadenceFields = () => {
    const cadence = jobForm.elements.namedItem("cadence").value;
    body.querySelector("[data-mail-hourly-minute]").hidden = cadence !== "hourly";
    body.querySelector("[data-mail-weekday]").hidden = cadence !== "weekly";
    body.querySelector("[data-mail-month-day]").hidden = cadence !== "monthly";
    body.querySelector("[data-mail-send-time]").hidden = cadence === "hourly";
  };
  const updateAutomationPreview = () => {
    const cadence = jobForm.elements.namedItem("cadence").value;
    const kind = jobKindText(selectedJobKinds()) || "Ek seçilmedi";
    const repeat = cadence === "hourly"
        ? `Her saat ${String(jobForm.elements.namedItem("minute").value).padStart(2, "0")}. dakikada`
        : cadence === "weekly"
          ? `Her ${weekdayNames[Number(jobForm.elements.namedItem("weekday").value)]}`
        : cadence === "monthly"
          ? `Her ayın ${jobForm.elements.namedItem("month_day").value}. günü`
          : "Her gün";
    body.querySelector("[data-mail-auto-preview]").textContent =
        `${kind} · ${repeat}${cadence === "hourly" ? "" : ` · ${jobForm.elements.namedItem("time").value || "09:00"}`} · ${jobForm.elements.namedItem("to").value || "Alıcı belirtilmedi"}`;
  };
  const historySources = {
    scheduled: "Planlı", "catch-up": "Kaçan gönderim", retry: "Yeniden deneme",
    "retry-expired": "Dönem geçişinde iptal edilen deneme", manual: "Elle",
  };
  const renderHistory = () => {
    const history = mailAutomation.history || [];
    const container = body.querySelector("[data-mail-history]");
    const toggle = body.querySelector("[data-mail-history-toggle]");
    toggle.hidden = history.length <= historyPreviewRows;
    toggle.textContent = historyExpanded ? `Son ${historyPreviewRows} kaydı göster` : `Tümünü göster (${history.length})`;
    if (!history.length) {
      container.innerHTML = '<p class="archive-empty">Henüz otomatik veya elle gönderim yapılmadı.</p>';
      return;
    }
    const jobsById = new Map(mailAutomation.jobs.map((job) => [job.id, job]));
    const newestByJob = new Map();
    history.forEach((entry) => { if (!newestByJob.has(entry.job_id)) newestByJob.set(entry.job_id, entry.id); });
    const rows = (historyExpanded ? history : history.slice(0, historyPreviewRows)).map((entry) => {
      const job = jobsById.get(entry.job_id);
      const canResend = ["error", "unknown", "partial"].includes(entry.status) && job?.can_resend
        && newestByJob.get(entry.job_id) === entry.id && entry.attempted_at === job.last_attempt_at;
      const source = (historySources[entry.source] || entry.source) + (entry.source === "retry" ? ` (${entry.attempt}. deneme)` : "");
      return `<tr data-mail-history-status="${escapeHtml(entry.status)}">
        <td>${escapeHtml(formatMailTime(entry.attempted_at))}</td>
        <td>${escapeHtml(jobKindText(entry.kinds))}${job ? "" : " <small>(silinmiş plan)</small>"}</td>
        <td>${escapeHtml(entry.month)}</td>
        <td>${Number(entry.recipients) || 0}</td>
        <td>${escapeHtml(source)}</td>
        <td><span class="mail-history-status">${escapeHtml(mailStatusLabel(entry.status))}</span>${entry.error ? `<small>${escapeHtml(entry.error)}</small>` : ""}</td>
        <td>${canResend ? `<button class="button button-light" type="button" data-mail-send-now="${escapeHtml(entry.job_id)}" data-mail-resend>Yeniden gönder</button>` : ""}</td>
      </tr>`;
    }).join("");
    container.innerHTML = `<table class="mail-history-table">
      <thead><tr><th scope="col">Zaman</th><th scope="col">Ekler</th><th scope="col">Dönem</th><th scope="col">Alıcı</th>
        <th scope="col">Tür</th><th scope="col">Durum</th><th scope="col"><span class="sr-only">İşlem</span></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  };
  const renderAutomation = () => {
    const settings = mailAutomation.settings;
    settingsForm.elements.namedItem("host").value = settings.host || "";
    settingsForm.elements.namedItem("port").value = String(settings.port || 587);
    settingsForm.elements.namedItem("username").value = settings.username || "";
    settingsForm.elements.namedItem("from_address").value = settings.from_address || "";
    settingsForm.elements.namedItem("use_ssl").checked = Boolean(settings.use_ssl);
    settingsForm.elements.namedItem("enabled").checked = Boolean(settings.enabled);
    const policy = mailAutomation.retry_policy;
    if (policy) {
      body.querySelector("[data-mail-retry-note]").textContent =
        `Yeniden denenebilir hatalarda bekleme süreleri (dakika): ${policy.delays_minutes.join(", ")}. `
        + "Sonucu belirsiz veya kısmen kabul edilen bir gönderim otomatik olarak tekrarlanmaz.";
    }
    jobsList.innerHTML = mailAutomation.jobs.length ? mailAutomation.jobs.map((job) => {
        const repeat = job.cadence === "hourly" ? `Saatlik · dakika ${String(job.minute).padStart(2, "0")}`
          : job.cadence === "weekly" ? `Haftalık · ${weekdayNames[job.weekday]}`
            : job.cadence === "monthly" ? `Aylık · gün ${job.month_day}` : "Günlük";
        const status = job.last_status === "retry"
          ? `Son gönderim başarısız (deneme ${job.attempts}/${job.max_attempts}); otomatik olarak yeniden denenecek.`
          : job.last_status ? mailStatusLabel(job.last_status) : (job.enabled ? "Etkin; henüz gönderim yapılmadı." : "Kapalı");
        const times = [];
        if (job.next_run_at) {
          times.push(job.last_status === "retry"
            ? `Yeniden deneme: ${formatMailTime(job.next_run_at)}`
            : `Sonraki gönderim: ${job.due_now ? "şimdi (sırada)" : formatMailTime(job.next_run_at)}`);
        } else {
          times.push(job.enabled ? "Sonraki gönderim: SMTP hesabı etkin değil" : "Plan kapalı");
        }
        if (job.last_attempt_at) times.push(`Son deneme: ${formatMailTime(job.last_attempt_at)}`);
        if (job.last_sent_at) times.push(`Son başarılı: ${formatMailTime(job.last_sent_at)}`);
        const sendButton = job.can_resend
          ? `<button class="button button-primary" type="button" data-mail-send-now="${escapeHtml(job.id)}" data-mail-resend>Yeniden gönder</button>`
          : job.can_send_now
            ? `<button class="button button-light" type="button" data-mail-send-now="${escapeHtml(job.id)}">Şimdi gönder</button>` : "";
        const jobRecipients = job.to.split(/[;,]/).map((value) => value.trim()).filter(Boolean);
        return `<article class="mail-automation-job" data-mail-status="${escapeHtml(job.last_status || "")}">
          <div><strong>${escapeHtml(jobKindText(job.kinds?.length ? job.kinds : [job.kind]))}</strong><span>${escapeHtml(repeat)}${job.cadence === "hourly" ? "" : ` · ${String(job.hour).padStart(2, "0")}:${String(job.minute).padStart(2, "0")}`} · ${escapeHtml(job.to)}</span>
            <small class="mail-job-status">${escapeHtml(status)}${job.last_error ? ` · ${escapeHtml(job.last_error)}` : ""}</small>
            <small>${escapeHtml(times.join(" · "))}</small></div>
          <details class="mail-job-preview">
            <summary>Alıcı ekinin gerçek içeriğini önizle</summary>
            <label>Alıcı <select data-mail-preview-recipient="${escapeHtml(job.id)}">${jobRecipients.map((email) =>
              `<option value="${escapeHtml(email)}">${escapeHtml(email)}</option>`).join("")}</select></label>
            <label>Dönem <input type="month" data-mail-preview-month="${escapeHtml(job.id)}" value="${escapeHtml(month)}"></label>
            <button class="button button-light" type="button" data-mail-preview="${escapeHtml(job.id)}">Ek içeriğini yükle</button>
          </details>
          <div class="dialog-actions">${sendButton}<button class="button button-light" type="button" data-mail-edit="${escapeHtml(job.id)}">Düzenle</button>
            <button class="button button-light" type="button" data-mail-delete="${escapeHtml(job.id)}">Sil</button></div>
        </article>`;
    }).join("") : '<p class="archive-empty">Otomatik rapor planı yok.</p>';
    renderHistory();
    autoStatus.textContent = settings.configured
        ? "SMTP parolası bu Windows kullanıcı hesabıyla şifrelenmiş durumda."
        : "Otomatik gönderim için önce SMTP sunucusu ve uygulama parolası tanımlayın.";
  };
  const readMailSettings = () => ({
    host: settingsForm.elements.namedItem("host").value.trim(),
    port: Number(settingsForm.elements.namedItem("port").value),
    username: settingsForm.elements.namedItem("username").value.trim(),
    from_address: settingsForm.elements.namedItem("from_address").value.trim(),
    use_ssl: settingsForm.elements.namedItem("use_ssl").checked,
    enabled: settingsForm.elements.namedItem("enabled").checked,
    password: settingsForm.elements.namedItem("password").value,
  });
  const saveAutomation = async (jobs) => {
    const cleanJobs = jobs.map((job) => {
        const kinds = job.kinds?.length ? job.kinds : [job.kind];
        return {
          id: job.id || "", kind: kinds[0], kinds, cadence: job.cadence,
          weekday: job.weekday ?? null, month_day: job.month_day ?? null,
          hour: job.hour, minute: job.minute, to: job.to,
          subject: job.subject, body: job.body, enabled: Boolean(job.enabled),
        };
    });
    const result = await request("/api/mail-automation", {
        method: "PUT", body: JSON.stringify({ settings: readMailSettings(), jobs: cleanJobs }),
    });
    mailAutomation = result;
    settingsForm.elements.namedItem("password").value = "";
    renderAutomation();
    return result;
  };
  const readJob = () => {
    const cadence = jobForm.elements.namedItem("cadence").value;
    const [selectedHour, selectedMinute] = jobForm.elements.namedItem("time").value.split(":").map(Number);
    const kinds = selectedJobKinds();
    return {
        id: editingJobId,
        kind: kinds[0] || "",
        kinds,
        cadence,
        weekday: cadence === "weekly" ? Number(jobForm.elements.namedItem("weekday").value) : null,
        month_day: cadence === "monthly" ? Number(jobForm.elements.namedItem("month_day").value) : null,
        hour: cadence === "hourly" ? 0 : selectedHour,
        minute: cadence === "hourly" ? Number(jobForm.elements.namedItem("minute").value) : selectedMinute,
        to: jobForm.elements.namedItem("to").value,
        subject: jobForm.elements.namedItem("subject").value,
        body: jobForm.elements.namedItem("body").value,
        enabled: jobForm.elements.namedItem("enabled").checked,
    };
  };
  const resetJobForm = () => {
    jobForm.reset();
    jobForm.elements.namedItem("id").value = "";
    jobForm.elements.namedItem("time").value = "09:00";
    jobForm.elements.namedItem("month_day").value = "1";
    jobForm.elements.namedItem("minute").value = "0";
    editingJobId = "";
    cancelEdit.hidden = true;
    jobForm.querySelector('[type="submit"]').textContent = "Gönderim planını kaydet";
    updateCadenceFields();
    updateAutomationPreview();
  };
  saveSettingsButton.addEventListener("click", () => extrasRun(
    saveSettingsButton,
    async () => {
        await saveAutomation(mailAutomation.jobs);
        autoStatus.textContent = "SMTP ayarları kaydedildi. Parola okunabilir biçimde saklanmıyor.";
    },
  ));
  jobForm.addEventListener("input", updateAutomationPreview);
  jobForm.addEventListener("change", () => {
    updateCadenceFields();
    updateAutomationPreview();
  });
  jobForm.addEventListener("submit", (event) => {
    event.preventDefault();
    extrasRun(jobForm.querySelector('[type="submit"]'), async () => {
        const job = readJob();
        if (!job.kinds.length) {
          toast("En az bir rapor eki seçin.", true);
          return;
        }
        const cadenceLabel = job.cadence === "hourly" ? "her saat"
          : job.cadence === "daily" ? "her gün"
            : job.cadence === "weekly" ? "her hafta" : "her ay";
        if (job.enabled && !window.confirm(`${job.to} adresine ${jobKindText(job.kinds)} ${cadenceLabel} otomatik gönderilecek. Bu planı etkinleştiriyor musunuz?`)) return;
        const jobs = mailAutomation.jobs.filter((item) => item.id !== editingJobId);
        jobs.push(job);
        await saveAutomation(jobs);
        resetJobForm();
        autoStatus.textContent = "Otomatik rapor planı kaydedildi.";
    });
  });
  cancelEdit.addEventListener("click", resetJobForm);
  jobsList.addEventListener("click", (event) => {
    const edit = event.target.closest("[data-mail-edit]");
    const remove = event.target.closest("[data-mail-delete]");
    if (edit) {
        const job = mailAutomation.jobs.find((item) => item.id === edit.dataset.mailEdit);
        if (!job) return;
        editingJobId = job.id;
        jobForm.elements.namedItem("id").value = job.id;
        const kinds = job.kinds?.length ? job.kinds : [job.kind];
        jobForm.querySelectorAll('input[name="kinds"]').forEach((input) => { input.checked = kinds.includes(input.value); });
        jobForm.elements.namedItem("cadence").value = job.cadence;
        jobForm.elements.namedItem("weekday").value = String(job.weekday ?? 0);
        jobForm.elements.namedItem("month_day").value = String(job.month_day ?? 1);
        jobForm.elements.namedItem("time").value = job.cadence === "hourly"
          ? "09:00" : `${String(job.hour).padStart(2, "0")}:${String(job.minute).padStart(2, "0")}`;
        jobForm.elements.namedItem("minute").value = String(job.minute);
        jobForm.elements.namedItem("to").value = job.to;
        jobForm.elements.namedItem("subject").value = job.subject;
        jobForm.elements.namedItem("body").value = job.body;
        jobForm.elements.namedItem("enabled").checked = job.enabled;
        cancelEdit.hidden = false;
        jobForm.querySelector('[type="submit"]').textContent = "Değişiklikleri kaydet";
        updateCadenceFields();
        updateAutomationPreview();
        return;
    }
    if (!remove) return;
    extrasRun(remove, async () => {
        if (!window.confirm("Bu otomatik rapor planı silinsin mi?")) return;
        await saveAutomation(mailAutomation.jobs.filter((item) => item.id !== remove.dataset.mailDelete));
        autoStatus.textContent = "Otomatik rapor planı silindi.";
    });
  });
  const missingManualReply = new Set();
  const previewObjectUrls = [];
  const renderRecipientPreview = (result) => {
    previewObjectUrls.splice(0).forEach((url) => URL.revokeObjectURL(url));
    recipientPreview.replaceChildren();
    recipientPreview.hidden = false;
    const heading = document.createElement("h3");
    heading.textContent = `Gerçek ek önizlemesi · ${result.recipient}`;
    recipientPreview.append(heading);
    const subject = document.createElement("p");
    subject.textContent = `Konu: ${result.subject}`;
    recipientPreview.append(subject);
    const message = document.createElement("pre");
    message.textContent = result.body;
    recipientPreview.append(message);
    result.attachments.forEach((attachment) => {
      const card = document.createElement("article");
      card.className = "mail-attachment-preview-item";
      const title = document.createElement("strong");
      title.textContent = `${attachment.filename} · ${Number(attachment.size).toLocaleString("tr-TR")} bayt`;
      const digest = document.createElement("small");
      digest.textContent = `SHA-256: ${attachment.sha256}`;
      const excerpt = document.createElement("pre");
      excerpt.textContent = attachment.content_preview;
      const bytes = Uint8Array.from(atob(attachment.content_base64), (character) => character.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
      previewObjectUrls.push(url);
      const download = document.createElement("a");
      download.className = "button button-light";
      download.href = url;
      download.download = attachment.filename;
      download.textContent = "Gerçek eki indir";
      card.append(title, digest, excerpt, download);
      recipientPreview.append(card);
    });
    recipientPreview.scrollIntoView({ block: "nearest" });
  };
  body.querySelector(".mail-automation-panel").addEventListener("click", (event) => {
    if (event.target.closest("[data-mail-history-toggle]")) {
      historyExpanded = !historyExpanded;
      renderHistory();
      return;
    }
    const preview = event.target.closest("[data-mail-preview]");
    if (preview) {
      const job = mailAutomation?.jobs.find((item) => item.id === preview.dataset.mailPreview);
      if (!job) return;
      const recipient = jobsList.querySelector(`[data-mail-preview-recipient="${job.id}"]`)?.value;
      const reportMonth = jobsList.querySelector(`[data-mail-preview-month="${job.id}"]`)?.value;
      extrasRun(preview, async () => {
        recipientPreview.hidden = false;
        recipientPreview.textContent = "Alıcıya ait ekler hazırlanıyor…";
        const result = await request("/api/mail-automation/preview-recipient", {
          method: "POST",
          body: JSON.stringify({
            job_id: job.id, recipient, month: reportMonth, week: state.weekIndex || 0,
          }),
        });
        renderRecipientPreview(result);
      });
      return;
    }
    const send = event.target.closest("[data-mail-send-now]");
    if (!send || !mailAutomation) return;
    const job = mailAutomation.jobs.find((item) => item.id === send.dataset.mailSendNow);
    if (!job) return;
    const resend = send.hasAttribute("data-mail-resend");
    extrasRun(send, async () => {
        const kinds = jobKindText(job.kinds?.length ? job.kinds : [job.kind]);
        const previous = missingManualReply.has(job.id) ? "unknown" : job.last_status;
        const question = ["unknown", "partial"].includes(previous)
          ? `${mailStatusLabel(previous)}. Yeniden gönderim mükerrer ileti oluşturabilir. Tüm alıcılara (${job.to}) yeniden gönderilsin mi?`
          : resend
          ? `Son deneme başarısız. Daha önceki bir deneme kabul edildiyse yeniden gönderim mükerrer ileti oluşturabilir. ${job.pending_month || "Güncel dönem"} raporu ${job.to} adresine yeniden gönderilsin mi?`
          : `${kinds} şimdi ${job.to} adresine gönderilsin mi?`;
        if (!window.confirm(question)) return;
        autoStatus.textContent = "Rapor hazırlanıp gönderiliyor…";
        let result;
        try {
          result = await request("/api/mail-automation/send-now", {
            method: "POST", body: JSON.stringify({ job_id: job.id }),
          });
        } catch (error) {
          missingManualReply.add(job.id);
          autoStatus.textContent = `${error.message || "Gönderim sonucu alınamadı."} Yeniden göndermeden önce geçmişi kontrol edin; mükerrer ileti oluşabilir.`;
          toast(autoStatus.textContent, true);
          return;
        }
        missingManualReply.delete(job.id);
        mailAutomation = result;
        renderAutomation();
        const delivery = result.delivery || {};
        if (delivery.status === "sent") {
          autoStatus.textContent = `${kinds} gönderildi (${delivery.month} dönemi).`;
          toast("Rapor e-postası gönderildi.");
        } else {
          const detail = delivery.error || (delivery.status === "error" ? "" : "Yeniden gönderim mükerrer ileti oluşturabilir.");
          autoStatus.textContent = [mailStatusLabel(delivery.status), detail].filter(Boolean).join(" · ");
          toast(autoStatus.textContent, true);
        }
    });
  });
  updateCadenceFields();
  updateAutomationPreview();
  request("/api/mail-automation").then((result) => {
    mailAutomation = result;
    renderAutomation();
    saveSettingsButton.disabled = false;
    saveJobButton.disabled = false;
  }).catch((error) => {
    jobsList.textContent = error.message || "E-posta otomasyon ayarları yüklenemedi.";
    autoStatus.textContent = "Ayarlar kaydedilemedi.";
  });
  loadRecipientPermissions().catch((error) => {
    recipientList.textContent = error.message || "Sunucu alıcı izinleri yüklenemedi.";
  });
  showExtras(dialog);
  const section = options && options.section;
  if (section === "automation" || section === "history") {
    const panel = body.querySelector(".mail-automation-panel");
    panel.open = true;
    const target = section === "history" ? body.querySelector(".mail-history") : panel;
    requestAnimationFrame(() => target?.scrollIntoView({ block: "start" }));
  }
}

let outputKindForShare = "schedule";
document.addEventListener("click", (event) => {
  const button = event.target.closest?.("[data-output-kind]");
  if (button) outputKindForShare = button.dataset.outputKind;
}, true);

async function jpegToPngBlob(dataUrl) {
  const image = new Image();
  image.src = dataUrl;
  await image.decode();
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  canvas.getContext("2d").drawImage(image, 0, 0);
  return new Promise((resolve, reject) => canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("Görüntü hazırlanamadı.")), "image/png"));
}

async function shareOutputToWhatsApp(button) {
  const message = el("#output-message");
  button.disabled = true;
  try {
    message.textContent = "Görüntü hazırlanıyor…";
    const kind = outputKindForShare;
    const simple = kind === "archive" || kind === "import";
    const output = simple
      ? outputSimpleTableImage(el(kind === "archive" ? "#archive-list table" : "#import-preview table"), el("#output-start").value, el("#output-end").value, kind)
      : outputTableImage(kind, el("#output-start").value, el("#output-end").value);
    if (!navigator.clipboard?.write || typeof ClipboardItem === "undefined") {
      throw new Error("Bu pencere panoya görüntü kopyalamayı desteklemiyor; “JPEG kaydet” ile kaydedip WhatsApp'a ekleyin.");
    }
    await navigator.clipboard.write([new ClipboardItem({ "image/png": await jpegToPngBlob(output.data) })]);
    const label = kind === "attendance"
      ? `Puantaj ${extrasMonthLabel(state.attendanceMonth)}`
      : `Vardiya planı ${extrasMonthLabel(state.scheduleMonth)} · ${state.weekIndex + 1}. hafta`;
    const result = await request("/api/open-whatsapp", { method: "POST", body: JSON.stringify({ text: label }) });
    message.textContent = result.opened === "web"
      ? "Görüntü panoya kopyalandı; WhatsApp Web açıldı. Sohbeti seçip Ctrl+V ile yapıştırın."
      : "Görüntü panoya kopyalandı; WhatsApp açıldı. Sohbeti seçip Ctrl+V ile yapıştırın.";
  } catch (error) {
    message.textContent = error.message.split(" Düzeltme önerisi")[0];
  } finally {
    button.disabled = false;
  }
}

async function startAccess() {
  el("#output-whatsapp")?.addEventListener("click", (event) => shareOutputToWhatsApp(event.currentTarget));
  try {
    const status = await request("/api/auth/status");
    acceptAccessStatus(status);
    if (status.enabled && !status.user) { showLoginOverlay(); return; }
  } catch (error) {
    applyAccessPermissions();
    toast("Yetkiler doğrulanamadı; düzenleme kapalı. Bağlantıyı kontrol edin.", true);
  }
  setInterval(() => {
    if (accessState.overlay) return;
    refreshAccessStatus().catch((error) => {
      console.warn("Yetki durumu doğrulanamadı.", error);
      maskAfterAccessCheckFailure();
    });
    refreshLockState().catch(() => {});
  }, 5000);
}

EXTRAS_MENU.push(
  ["-"],
  ["Kullanıcılar ve parolalar", openUsers],
  ["Kayıt kilidi (ortak klasör)", openLockStatus],
  ["E-posta taslağı (Outlook)", openMailDraft],
  ["E-posta otomatik gönderim ve adres defteri", () => openMailDraft({ section: "automation" })],
  ["E-posta gönderim geçmişi", () => openMailDraft({ section: "history" })],
);

applyAccessPermissions();
startAccess();

// The mail dialog is rebuilt on every open. Only mount when its automation section is used.
document.addEventListener("toggle", function openMailRehearsal(event) {
  const panel = event.target;
  if (!panel.matches?.("#mail-dialog .mail-automation-panel") || !panel.open
      || panel.querySelector("[data-mail-rehearsal]")) return;
  const section = document.createElement("section");
  section.dataset.mailRehearsal = "";
  section.innerHTML = `<h3>Gönderim provası</h3>
    <p class="extras-note">Kayıtlı planların önümüzdeki 7 takvim gününü inceleyin. Prova dosya hazırlamaz, e-posta göndermez ve planı değiştirmez.</p>
    <button type="button" class="button button-light" data-load>7 günlük provayı göster</button>
    <p class="extras-note" role="status" data-summary></p><div data-results></div>`;
  panel.querySelector("summary").after(section);
  const load = section.querySelector("[data-load]");
  const summary = section.querySelector("[data-summary]");
  const results = section.querySelector("[data-results]");
  const kinds = { attendance: "Puantaj (Excel)", schedule: "Vardiya listesi (Excel)",
    "manager-summary": "Yönetici özeti (PDF)", "month-package": "Ay sonu paketi (ZIP)" };
  const reports = (plan) => plan.kinds.map((kind) => kinds[kind] || kind).join(", ");
  const stamp = (value) => value ? new Date(value).toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" }) : "—";
  const wallTime = (iso) => {
    const [day, time] = iso.split("T");
    return `${day.split("-").reverse().join(".")} ${time}`;
  };
  load.addEventListener("click", async () => {
    load.disabled = true;
    results.replaceChildren();
    summary.textContent = "Kayıtlı planlar okunuyor…";
    try {
      const data = await request("/api/mail-automation?view=rehearsal", { cache: "no-store" });
      const plans = Object.values(data.plans);
      summary.textContent = `${stamp(data.as_of)} itibarıyla ${data.slots.length} olağan plan saati. `
        + (data.ready ? "" : "SMTP etkin veya yapılandırılmış değil. ")
        + `${data.disabled_plans} kapalı plan dahil edilmedi. Kaçan gönderimler ve yeniden denemeler takvim satırı değildir.`;
      const addresses = new Map();
      try {
        const contacts = JSON.parse(localStorage.getItem("vp-mail-address-book") || "[]");
        if (Array.isArray(contacts)) for (const entry of contacts) {
          if (typeof entry?.email === "string" && entry.email && typeof entry.name === "string") {
            addresses.set(entry.email.toLowerCase(), `${entry.name} · ${entry.email}`);
          }
        }
      } catch { /* The rehearsal works without the local address book. */ }
      for (const plan of plans) for (const email of plan.recipients) {
        if (!addresses.has(email.toLowerCase())) addresses.set(email.toLowerCase(), email);
      }
      results.innerHTML = `<label>Alıcı <select data-recipient><option value="">Tüm alıcılar</option></select></label>
        <p class="extras-note">Bölüm yetkisi ve personelin aktifliği adreslerden doğrulanamaz. Gönderimden önce alıcıları ve ekleri karşılaştırın.</p>
        <div data-attempts></div><p role="status" data-count></p><div data-slots></div>
        <div class="dialog-actions"><button type="button" class="button button-light" data-prev>Önceki</button>
        <button type="button" class="button button-light" data-next>Sonraki</button></div>`;
      const select = results.querySelector("[data-recipient]");
      for (const [value, label] of [...addresses].sort((a, b) => a[0].localeCompare(b[0]))) {
        select.add(new Option(label, value));
      }
      const prev = results.querySelector("[data-prev]");
      const next = results.querySelector("[data-next]");
      let offset = 0;
      const size = 20;
      function renderMailSlots() {
        const includesRecipient = (plan) => !select.value || plan.recipients.some((e) => e.toLowerCase() === select.value);
        const active = plans.filter(includesRecipient);
        const slots = data.slots.filter((slot) => includesRecipient(data.plans[slot.job_id]));
        const attempts = active.filter((plan) => plan.retry).map((plan) => {
          const retry = plan.retry;
          return `<li>${escapeHtml(plan.recipients.join("; "))} · ${escapeHtml(reports(plan))}<br>
            ${escapeHtml(mailStatusLabel(retry.status))}${retry.reason ? ` · ${escapeHtml(retry.reason)}` : ""} · Otomatik deneme: ${Number(retry.automatic_attempts)}.
            Son deneme: ${escapeHtml(stamp(retry.last_attempt_at))}.
            Sonraki otomatik deneme: ${escapeHtml(retry.next_retry_at ? stamp(retry.next_retry_at) : "planlanmadı")}.</li>`;
        });
        results.querySelector("[data-attempts]").innerHTML = attempts.length
          ? `<h4>Son gönderim ve denemeler</h4><ul>${attempts.join("")}</ul>` : "";
        results.querySelector("[data-count]").textContent = `${active.length} etkin plan · ` + (slots.length
          ? `${offset + 1}–${Math.min(offset + size, slots.length)} / ${slots.length} plan saati`
          : "Bu alıcı seçimi için prova döneminde plan saati yok.");
        const rows = slots.slice(offset, offset + size).map((slot) => {
          const plan = data.plans[slot.job_id];
          const message = plan.messages[slot.month];
          const to = plan.recipients.join("; ");
          const period = slot.month + (plan.kinds.includes("schedule") ? ` · Vardiya: ${slot.week + 1}. hafta` : "");
          const address = plan.recipients.find((e) => e.toLowerCase() === select.value);
          return `<tr><td>${escapeHtml(wallTime(slot.local_time))}</td><td>${escapeHtml(period)}</td>
            <td>${escapeHtml(to)}</td><td>${escapeHtml(reports(plan))}
            <details class="mail-preview"><summary>İleti önizlemesi</summary>
            <pre>${escapeHtml(`${address ? `Seçilen alıcı: ${address}\n` : ""}Kime: ${to}\nKonu: ${message.subject}\n\n${message.body}\n\nEkler: ${reports(plan)} · ${period}`)}</pre>
            ${plan.recipients.length > 1 ? "Bu plan tek ortak ileti hazırlar; ekler alıcıya göre süzülmez." : ""}</details></td></tr>`;
        });
        results.querySelector("[data-slots]").innerHTML = rows.length ? `<div class="mail-history-scroll">
          <table class="mail-history-table"><thead><tr><th scope="col">Yerel saat</th><th scope="col">Rapor dönemi</th>
          <th scope="col">Kime</th><th scope="col">Hazırlanacak raporlar</th></tr></thead><tbody>${rows.join("")}</tbody></table></div>` : "";
        prev.hidden = next.hidden = slots.length <= size;
        prev.disabled = offset === 0;
        next.disabled = offset + size >= slots.length;
      }
      select.onchange = () => { offset = 0; renderMailSlots(); };
      prev.onclick = () => { offset -= size; renderMailSlots(); };
      next.onclick = () => { offset += size; renderMailSlots(); };
      renderMailSlots();
      load.textContent = "Provayı yenile";
    } catch (error) {
      results.replaceChildren();
      summary.textContent = error.message || "Prova okunamadı. Yeniden deneyin.";
    } finally { load.disabled = false; }
  });
}, true);
