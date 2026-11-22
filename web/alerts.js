/* Uyarı merkezi: zil ve ay durumu rozeti, uyarıyı yok sayma / geri açma,
   puantajı plana döndürme ve bölüm başına asgari kadro ayarı.
   Rapor uyarıları ile kayıt doğrulaması ayrı tutulur. */

const ALERTS = { report: null, month: "", timer: 0, locked: false, filter: "all", busy: false };
const ALERT_SCOPES = { once: "Bu seferlik", person: "Bu kişi için", type: "Bu türü kapat" };
const ALERT_SEVERITY = { error: "Hata", warning: "Uyarı", info: "Bilgi" };
const ALERT_TYPE_LABELS = {
  overlap: "Vardiya çakışması",
  conflict: "Çakışan kodlar",
  manual_change: "Plandan farklı elle giriş",
  legal_rest: "Vardiya arası yasal dinlenme",
  legal_weekly_rest: "Haftalık 24 saat dinlenme",
  night_rotation: "Üst üste gece haftası",
  balancing: "Denkleştirme ortalaması",
  special_night: "Özel durum: gece çalışması",
  special_overtime: "Özel durum: fazla çalışma",
  special_daily: "Özel durum: günlük süre",
  min_staffing: "Asgari kadro altında gün",
  leave_request_conflict: "Onaylı izin günü çalışma",
  leave_request_crowd: "Aynı güne çok izin talebi",
  preference: "Personel tercihi / müsaitlik",
  skill: "Beceri gereksinimi",
  after_exit: "Çıkıştan sonra vardiya",
  document_expiry: "Belge süresi",
};

function alertTypeLabel(type) {
  if (ALERT_TYPE_LABELS[type]) return ALERT_TYPE_LABELS[type];
  const group = (typeof REPORT_GROUPS !== "undefined" ? REPORT_GROUPS : []).find(([key]) => key === type);
  return group ? group[1] : type;
}

function alertMonth() {
  return typeof extrasActiveMonth === "function" ? extrasActiveMonth() : state.attendanceMonth;
}

function alertDirty() {
  return state.view === "attendance" ? state.attendanceDirty : state.scheduleDirty;
}

function alertStatus() {
  if (ALERTS.locked) return ["locked", "Kilitli"];
  if (alertDirty()) return ["dirty", "Kaydedilmedi"];
  const counts = ALERTS.report?.counts || {};
  if (!ALERTS.report) return ["idle", "Denetleniyor"];
  if (counts.error) return ["error", "Hatalı"];
  if (counts.warning) return ["warning", "Uyarılı"];
  return ["ok", "Hazır"];
}

function alertsPaint() {
  const bell = el("#alert-bell");
  if (!bell) return;
  const counts = ALERTS.report?.counts || {};
  const total = (counts.error || 0) + (counts.warning || 0);
  const badge = bell.querySelector(".alert-bell-count");
  badge.textContent = total > 99 ? "99+" : String(total);
  badge.hidden = !total;
  bell.dataset.level = counts.error ? "error" : total ? "warning" : "ok";
  bell.title = `${extrasMonthLabel(ALERTS.month || alertMonth())}: ${counts.error || 0} hata, ${counts.warning || 0} uyarı, ${counts.info || 0} bilgi`;
  bell.setAttribute("aria-label", `Uyarı merkezi. ${bell.title}`);
  const [level, label] = alertStatus();
  const chip = el("#month-status");
  chip.dataset.level = level;
  chip.textContent = `${extrasMonthLabel(ALERTS.month || alertMonth())} · ${label}`;
  chip.title = level === "locked" ? "Bu dönem kilitli; Arşiv'den açılabilir."
    : level === "dirty" ? "Kaydedilmemiş değişiklik var."
    : `Ay durumu: ${label}`;
}

async function alertsRefresh() {
  const month = alertMonth();
  if (!month) return;
  try {
    const [rawReport, archive] = await Promise.all([
      request(`/api/reports/check?month=${encodeURIComponent(month)}`),
      request("/api/archive").catch(() => null),
    ]);
    if (month !== alertMonth()) return;
    const report = window.easyUsability ? window.easyUsability.deduplicateAlerts(rawReport) : rawReport;
    const total = (value) => (value?.counts?.error || 0) + (value?.counts?.warning || 0);
    if (ALERTS.afterSave && ALERTS.report && ALERTS.month === month && total(report) > total(ALERTS.report)) {
      toast(`Kaydedildi. ${total(report) - total(ALERTS.report)} yeni uyarı var; zil simgesinden inceleyebilirsiniz.`);
    }
    ALERTS.afterSave = false;
    ALERTS.report = report;
    ALERTS.month = month;
    ALERTS.locked = Boolean(archive?.months?.find((item) => item.month === month)?.locked);
  } catch (_) {
    ALERTS.report = null;
  }
  alertsPaint();
  const dialog = document.getElementById("alert-center");
  if (dialog?.open) alertsRenderCenter(dialog);
}

function alertsSchedule(delay = 500) {
  clearTimeout(ALERTS.timer);
  ALERTS.timer = setTimeout(() => alertsRefresh(), delay);
}

function alertItemRow(item, index) {
  const when = item.date ? `${item.date.slice(8, 10)}.${item.date.slice(5, 7)}` : "Ay geneli";
  const canGo = item.employee_id && item.date;
  return `<li class="alert-item" data-severity="${item.severity}">
      <span class="alert-dot" aria-label="${ALERT_SEVERITY[item.severity] || item.severity}"></span>
      <div class="alert-text"><strong>${escapeHtml(item.name || "")}</strong> <span class="alert-when">${escapeHtml(when)}</span>
        <div>${escapeHtml(item.message || "")}</div></div>
      <div class="alert-actions">
        ${canGo ? `<button class="button button-light" type="button" data-alert-go="${index}">Git</button>` : ""}
        ${Object.entries(ALERT_SCOPES).map(([scope, label]) =>
          `<button class="button button-light alert-ignore" type="button" data-alert-ignore="${index}" data-scope="${scope}"
             title="${scope === "once" ? "Yalnız bu gün için gizle" : scope === "person" ? "Bu kişi/bölüm için bu kuralı kapat" : "Bu uyarı türünü tamamen kapat"}">${label}</button>`).join("")}
      </div></li>`;
}

function alertsRenderCenter(dialog) {
  const body = dialog.querySelector(".extras-body");
  const report = ALERTS.report;
  if (!report) {
    body.innerHTML = `<p class="period-check-clear">Kontrol raporu alınamadı. “Yenile” ile tekrar deneyin.</p>
      <div class="alert-toolbar"><button class="button button-light" type="button" data-alert-refresh>Yenile</button></div>`;
    body.querySelector("[data-alert-refresh]").addEventListener("click", () => alertsRefresh());
    return;
  }
  const priority = { error: 0, warning: 1, info: 2 };
  const items = report.items.map((item, index) => ({ item, index }))
    .sort((left, right) => (priority[left.item.severity] ?? 3) - (priority[right.item.severity] ?? 3))
    .filter(({ item }) => ALERTS.filter === "all" || item.severity === ALERTS.filter);
  const groups = new Map();
  for (const entry of items) {
    const key = `${entry.item.severity}:${entry.item.type}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(entry);
  }
  const mismatches = state.attendance && state.attendanceMonth === ALERTS.month
    ? attendanceScheduleMismatches(state.attendance.rows).length : 0;
  const [level, label] = alertStatus();
  const counts = report.counts || {};
  body.innerHTML = `<div class="alert-toolbar">
      <span class="month-status" data-level="${level}">${escapeHtml(extrasMonthLabel(ALERTS.month))} · ${escapeHtml(label)}</span>
      <div class="alert-filters" role="group" aria-label="Önem süzgeci">
        ${[["all", `Hepsi (${report.items.length})`], ["error", `Hata (${counts.error || 0})`], ["warning", `Uyarı (${counts.warning || 0})`], ["info", `Bilgi (${counts.info || 0})`]]
          .map(([key, text]) => `<button type="button" class="alert-chip${ALERTS.filter === key ? " active" : ""}" data-alert-filter="${key}" aria-pressed="${ALERTS.filter === key}">${text}</button>`).join("")}
      </div>
      <span class="alert-spacer"></span>
      ${mismatches ? `<button class="button button-light" type="button" data-alert-revert>Elle değişiklikleri plana döndür (${mismatches})</button>` : ""}
      <button class="button button-light" type="button" data-alert-rules>Uyarı yönetimi${report.ignored ? ` (${report.ignored} gizli)` : ""}</button>
      <button class="button button-light" type="button" data-alert-refresh>Yenile</button>
    </div>
    <p class="alert-note">Bu merkezdeki rapor uyarıları kayıttan ayrıdır; ayrıca tanımlanan hata düzeyindeki plan kuralları kaydı engelleyebilir. Rapor uyarıları bu seferlik, kişi veya tür bazında gizlenebilir; gizlemek gerçek sorunu çözmez.</p>
    ${groups.size ? [...groups.values()].map((entries) => `<section class="alert-group">
        <h3>${escapeHtml(ALERT_SEVERITY[entries[0].item.severity])} · ${escapeHtml(alertTypeLabel(entries[0].item.type))} <span>${entries.length}</span></h3>
        <ul>${entries.map(({ item, index }) => alertItemRow(item, index)).join("")}</ul></section>`).join("")
      : `<p class="period-check-clear">${ALERTS.filter === "all" ? "Bu ay için açık uyarı yok." : "Bu süzgeçte uyarı yok."}</p>`}`;
  body.querySelectorAll("[data-alert-filter]").forEach((button) => button.addEventListener("click", () => {
    ALERTS.filter = button.dataset.alertFilter;
    alertsRenderCenter(dialog);
  }));
  body.querySelector("[data-alert-refresh]").addEventListener("click", (event) => extrasRun(event.currentTarget, alertsRefresh));
  body.querySelector("[data-alert-rules]").addEventListener("click", () => openWarningRules().catch((error) => toast(error.message, true)));
  body.querySelector("[data-alert-revert]")?.addEventListener("click", () => { dialog.close(); alertsRevertToPlan(true).catch((error) => toast(error.message, true)); });
  body.querySelectorAll("[data-alert-ignore]").forEach((button) => button.addEventListener("click", () => extrasRun(button, async () => {
    const item = report.items[Number(button.dataset.alertIgnore)];
    await request("/api/warning-rules/ignore", { method: "POST", body: JSON.stringify({ scope: button.dataset.scope, item }) });
    toast(`${ALERT_SCOPES[button.dataset.scope]} gizlendi. “Uyarı yönetimi”nden geri açabilirsiniz.`);
    await alertsRefresh();
  })));
  body.querySelectorAll("[data-alert-go]").forEach((button) => button.addEventListener("click", async () => {
    const item = report.items[Number(button.dataset.alertGo)];
    dialog.close();
    if (window.easyUsability) {
      try { await window.easyUsability.goToFinding(item, ALERTS.month); }
      catch (error) { toast(error.message, true); }
      return;
    }
    if (state.view !== "attendance") await switchView("attendance");
    if (state.attendanceMonth !== ALERTS.month) {
      toast(`Önce puantajda ${extrasMonthLabel(ALERTS.month)} ayını açın.`, true);
      return;
    }
    const target = document.createElement("button");
    target.dataset.checkTarget = "attendance";
    target.dataset.person = String(item.employee_id);
    target.dataset.day = item.date;
    await handlePeriodCheckClick({ target });
  }));
}

function openAlertCenter() {
  const dialog = extrasDialog("alert-center", "Uyarı merkezi",
    "Seçili ayın kontrol raporu: yasal sınırlar, boş günler, çakışmalar, izinler ve asgari kadro.");
  dialog.classList.add("alert-center-dialog");
  alertsRenderCenter(dialog);
  showExtras(dialog);
  alertsRefresh();
}

function alertRuleLabel(scope, key) {
  const [type, person, date] = key.split("|");
  const found = state.people.find((candidate) => String(candidate.id) === person);
  const who = found ? found.name : person;
  return [alertTypeLabel(type), scope !== "type" ? who : "", date ? `${date.slice(8, 10)}.${date.slice(5, 7)}.${date.slice(0, 4)}` : ""]
    .filter(Boolean).join(" · ");
}

async function openWarningRules() {
  const dialog = extrasDialog("warning-rules", "Uyarı yönetimi",
    "Gizlediğiniz uyarılar. “Geri aç” ile uyarı tekrar raporda ve zilde görünür.");
  const body = dialog.querySelector(".extras-body");
  const render = (rules) => {
    const total = Object.values(rules).reduce((sum, list) => sum + list.length, 0);
    body.innerHTML = total ? `${Object.entries(ALERT_SCOPES).map(([scope, label]) => rules[scope].length ? `<section class="alert-group">
        <h3>${label} <span>${rules[scope].length}</span></h3>
        <ul>${rules[scope].map((key, index) => `<li class="alert-item"><div class="alert-text">${escapeHtml(alertRuleLabel(scope, key))}</div>
          <div class="alert-actions"><button class="button button-light" type="button" data-rule-scope="${scope}" data-rule-index="${index}">Geri aç</button></div></li>`).join("")}</ul>
      </section>` : "").join("")}
      <div class="dialog-footer"><button class="button button-light" type="button" data-rules-clear>Tümünü geri aç</button></div>`
      : `<p class="period-check-clear">Gizlenmiş uyarı yok.</p>`;
    body.querySelectorAll("[data-rule-scope]").forEach((button) => button.addEventListener("click", () => extrasRun(button, async () => {
      const next = { ...rules, [button.dataset.ruleScope]: rules[button.dataset.ruleScope].filter((_, index) => index !== Number(button.dataset.ruleIndex)) };
      render((await request("/api/warning-rules", { method: "PUT", body: JSON.stringify(next) })).rules);
      alertsRefresh();
    })));
    body.querySelector("[data-rules-clear]")?.addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
      if (!window.confirm("Gizlenen bütün uyarılar yeniden gösterilsin mi?")) return;
      render((await request("/api/warning-rules", { method: "PUT", body: JSON.stringify({ once: [], person: [], type: [] }) })).rules);
      alertsRefresh();
    }));
  };
  render((await request("/api/warning-rules")).rules);
  showExtras(dialog);
}

/* Puantajda elle değiştirilmiş hücreleri vardiya planındaki koda geri döndürür. */
async function alertsRevertToPlan(all = false) {
  if (state.view !== "attendance" && !await switchView("attendance")) return;
  if (!state.attendance) {
    toast("Puantaj yüklenemedi; bağlantıyı kontrol edip yeniden deneyin.", true);
    return;
  }
  const mismatches = attendanceScheduleMismatches(state.attendance.rows);
  let targets;
  if (!all && grid.kind === "attendance" && grid.keys.size) {
    const selected = new Set(grid.keys);
    targets = mismatches.filter((item) => selected.has(`${item.employee_id}|${item.date}`));
    if (!targets.length) {
      toast("Seçili hücrelerde plandan farklı elle giriş yok.");
      return;
    }
  } else {
    targets = mismatches;
    if (!targets.length) {
      toast("Puantaj vardiya planıyla aynı; döndürülecek elle giriş yok.");
      return;
    }
    if (!window.confirm(`${targets.length} elle değiştirilmiş puantaj hücresi vardiya planındaki koda döndürülecek. Devam edilsin mi?`)) return;
  }
  for (const item of targets) gridSetAttendanceCode(String(item.employee_id), item.date, "");
  setAttendanceDirty();
  renderAttendance();
  toast(`${targets.length} hücre plana döndürüldü. Kalıcı olması için kaydedin.`);
}

/* Yasal uyum penceresine bölüm başına asgari kadro alanları ekler. */
async function alertsInjectStaffing(form) {
  const box = document.createElement("fieldset");
  box.id = "staffing-fieldset";
  box.className = "forms-wide staffing-fieldset";
  box.innerHTML = `<legend>Bölüm başına günlük asgari kadro</legend><p class="alert-note">Gün içinde bölümde çalışan sayısı bunun altına düşerse uyarı verilir. 0 = denetim yok.</p><div class="staffing-grid"></div>
    <button class="button button-light" type="button" data-staffing-save>Asgari kadroyu kaydet</button>`;
  form.after(box);
  const data = await request("/api/compliance-settings");
  const values = data.min_staffing || {};
  box.querySelector(".staffing-grid").innerHTML = SECTIONS.map((section) =>
    `<label>${escapeHtml(section)} <input type="number" min="0" max="99" step="1" data-staffing-section="${escapeHtml(section)}" value="${values[section] || 0}"></label>`).join("");
  box.querySelector("[data-staffing-save]").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    const min_staffing = Object.fromEntries([...box.querySelectorAll("[data-staffing-section]")]
      .map((input) => [input.dataset.staffingSection, Number(input.value || 0)]));
    await request("/api/compliance-settings", { method: "PUT", body: JSON.stringify({ min_staffing }) });
    toast("Asgari kadro kaydedildi.");
    alertsSchedule(100);
  }));
}

function installAlerts() {
  const help = el("#open-guide");
  if (help && !el("#alert-bell")) {
    help.insertAdjacentHTML("beforebegin", `<span class="month-status" id="month-status" data-level="idle" role="status"></span>
      <button class="topbar-bell" id="alert-bell" type="button" aria-haspopup="dialog" data-level="ok">
        <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><path fill="currentColor" d="M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22Zm7-6V11a7 7 0 0 0-5.5-6.84V3.5a1.5 1.5 0 0 0-3 0v.66A7 7 0 0 0 5 11v5l-2 2v1h18v-1Z"/></svg>
        <span class="alert-bell-count" hidden>0</span>
      </button>`);
    el("#alert-bell").addEventListener("click", openAlertCenter);
  }
  for (const name of ["setScheduleDirty", "setAttendanceDirty"]) {
    const original = window[name];
    if (typeof original !== "function") continue;
    window[name] = function wrappedDirty(dirty = true, ...rest) {
      const result = original.call(this, dirty, ...rest);
      alertsPaint();
      if (!dirty) {
        ALERTS.afterSave = true;
        alertsSchedule(700);
      }
      return result;
    };
  }
  new MutationObserver(() => {
    const form = document.getElementById("compliance-form");
    if (form && !document.getElementById("staffing-fieldset")) alertsInjectStaffing(form).catch((error) => toast(error.message, true));
  }).observe(document.body, { childList: true, subtree: true });
  let lastMonth = "";
  let lastView = "";
  setInterval(() => {
    const month = alertMonth();
    if (month !== lastMonth || state.view !== lastView) {
      lastMonth = month;
      lastView = state.view;
      alertsSchedule(300);
    }
  }, 1500);
  alertsPaint();
}

// Menü controls.js içinde startExtras() ile kurulur; öğeler ondan önce eklenmeli.
if (typeof EXTRAS_MENU !== "undefined" && !EXTRAS_MENU.some((item) => item[0] === "Uyarı merkezi")) {
  EXTRAS_MENU.push(["Uyarı merkezi", openAlertCenter], ["Uyarı yönetimi", () => openWarningRules().catch((error) => toast(error.message, true))],
    ["Puantajı plana döndür (seçim / tümü)", () => alertsRevertToPlan(false).catch((error) => toast(error.message, true))]);
}
document.addEventListener("DOMContentLoaded", installAlerts);
