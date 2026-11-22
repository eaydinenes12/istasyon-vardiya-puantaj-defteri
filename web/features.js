/* Yeni özellik modülü: veri sağlığı, sürüm/yenilikler, taslak kurtarma.
   app.js'teki küresel işlevleri sarmalayarak çalışır; app.js'e dokunmadan genişletilir. */

const features = { draftTimers: {}, health: null, loading: {} };

/* ---------- Çalışan uygulama ve veri konumu ---------- */

async function openAppInfo() {
  const info = await request("/api/app-info");
  const dialog = extrasDialog("app-info-dialog", "Uygulama bilgisi", "Çalışan uygulama ve etkin veri konumu");
  const body = dialog.querySelector(".extras-body");
  const applicationLabel = info.runtime === "exe" ? "Çalışan EXE" : "Kaynak klasörü";
  const applicationPath = info.runtime === "exe" ? info.executable_path : info.application_path;
  const rows = [
    ["Sürüm", info.version, ""],
    ["Çalışma biçimi", info.runtime === "exe" ? "Kurulu EXE" : "Kaynak kod", ""],
    [applicationLabel, applicationPath, applicationLabel],
    ["Çalışan şube", info.branch.name, ""],
    ["Etkin veritabanı", info.data_path, "Etkin veritabanı"],
  ];
  body.innerHTML = `
    <p class="dialog-help">Bu ekran yalnız kullanılan yolları gösterir; kayıtlarınızı veya ayarlarınızı değiştirmez.</p>
    <dl class="app-info-list">${rows.map(([label, value, copyLabel]) => `
      <div class="app-info-row"><dt>${escapeHtml(label)}</dt><dd>
        <code class="app-info-path">${escapeHtml(value || "—")}</code>
        ${copyLabel ? `<button class="button button-light app-info-copy" type="button"
          data-app-info-copy="${escapeHtml(value)}" aria-label="Kopyala: ${escapeHtml(copyLabel)}">Kopyala</button>` : ""}
      </dd></div>`).join("")}
    </dl>`;
  body.querySelectorAll("[data-app-info-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        if (!navigator.clipboard?.writeText) throw new Error("Bu pencerede panoya erişilemiyor.");
        await navigator.clipboard.writeText(button.dataset.appInfoCopy);
        toast("Yol panoya kopyalandı.");
      } catch (error) {
        toast(`Yol panoya kopyalanamadı: ${error.message}`, true);
      }
    });
  });
  showExtras(dialog);
}

/* ---------- Veri sağlığı (56, 103, 104) ---------- */

function healthLevelLabel(level) {
  return { ok: "Sorun yok", warning: "Uyarı", error: "Hata", info: "Bilgi" }[level] || level;
}

async function openHealth() {
  const dialog = extrasDialog("health-dialog", "Veri sağlığı", "Veritabanı bütünlüğü, sahipsiz kayıtlar ve yedek durumu");
  const body = dialog.querySelector(".extras-body");
  const paint = (health, note = "") => {
    features.health = health;
    const repairable = health.issues.some((item) => item.repairable);
    const rollbackCompatible = health.rollback_available && health.rollback_compatibility?.compatible;
    body.innerHTML = `
      <p class="health-status health-${health.status}"><strong>${healthLevelLabel(health.status)}</strong>
        · ${health.people} personel · ${(health.size_bytes / 1024).toFixed(0)} KB · sürüm ${escapeHtml(health.version || "")}</p>
      ${note ? `<p class="health-note">${escapeHtml(note)}</p>` : ""}
      ${health.issues.length ? `<ul class="extras-list health-list">${health.issues.map((item) =>
        `<li class="health-${item.level}"><span>${escapeHtml(item.message)}</span><small>${item.level === "info" ? "Bilgi" : (item.repairable ? "Onarılabilir" : "Elle düzeltin")}</small></li>`).join("")}</ul>`
        : '<p class="muted">Tüm denetimler başarılı.</p>'}
      <div class="dialog-footer health-actions">
        <button class="button button-light" type="button" id="health-recheck">Yeniden denetle</button>
        <button class="button button-primary" type="button" id="health-repair" ${repairable ? "" : "disabled"}>Önce yedekle, sonra onar</button>
        ${health.rollback_available ? `<button class="button button-outline" type="button" id="health-rollback"${rollbackCompatible ? "" : " disabled"}>Önceki sürüme dön</button>` : ""}
      </div>
      ${health.rollback_available && !rollbackCompatible
        ? `<p class="health-note">Geri dönüş engellendi: ${escapeHtml(health.rollback_compatibility?.reason || "Şema uyumluluğu doğrulanamadı.")} EXE değiştirilmez.</p>` : ""}`;
    body.querySelector("#health-recheck").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => paint(await request("/api/health"))));
    body.querySelector("#health-repair").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
      const result = await request("/api/health/repair", { method: "POST", body: "{}" });
      const fresh = await request("/api/health");
      paint(fresh, result.repaired.length ? `Yapılanlar: ${result.repaired.join(" · ")}` : "Onarılacak bir şey bulunmadı.");
      updateHealthBadge(fresh);
      toast("Veri onarımı tamamlandı.");
    }));
    body.querySelector("#health-rollback")?.addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
      if (!confirm("Uygulama kapanıp bir önceki sürümle yeniden açılacak. Veriler önce yedeklenir. Devam edilsin mi?")) return;
      await request("/api/update/rollback", { method: "POST", body: "{}" });
      toast("Önceki sürüme dönülüyor…");
    }));
  };
  body.innerHTML = '<p class="muted">Denetleniyor…</p>';
  showExtras(dialog);
  paint(await request("/api/health"));
}

function updateHealthBadge(health) {
  const button = el("#tools-menu-button");
  if (!button) return;
  button.classList.toggle("has-health-issue", health.status === "error");
  button.title = health.status === "error" ? "Veri sağlığı sorunu var — Araçlar › Veri sağlığı" : "";
}

async function startupHealth() {
  try {
    const health = await request("/api/health");
    updateHealthBadge(health);
    if (health.status === "error") toast("Veri sağlığı denetiminde sorun bulundu. Araçlar › Veri sağlığı.", true);
  } catch (error) { /* açılışı engelleme */ }
}

async function openTechnicalErrors() {
  const dialog = extrasDialog("technical-errors-dialog", "Teknik hata kayıtları",
    "Son 50 hata; uygulama sürümü, hata kodu ve sıralı teknik adımlar tutulur. Kişi adı ve hata metni kaydedilmez.");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = '<p role="status">Kayıtlar okunuyor…</p>';
  showExtras(dialog);
  const result = await request("/api/diagnostics/errors");
  body.innerHTML = result.events.length ? `<div class="mail-history-scroll"><table class="mail-history-table">
    <thead><tr><th scope="col">Zaman (UTC)</th><th scope="col">Sürüm / aşama</th>
    <th scope="col">Hata</th><th scope="col">İşlem adımları</th><th scope="col">İşlem kimliği</th></tr></thead><tbody>
    ${result.events.map((event) => `<tr><td>${escapeHtml(event.occurred_at)}</td>
      <td>${escapeHtml(event.app_version)} · ${escapeHtml(event.stage)}</td>
      <td>${escapeHtml(event.error_code)} · ${escapeHtml(event.error_type)}</td>
      <td>${(event.steps || []).map((step) => escapeHtml(step)).join(" → ")}</td>
      <td>${escapeHtml(event.operation_id)}</td></tr>`).join("")}</tbody></table></div>`
    : '<p class="muted">Henüz teknik hata kaydı yok.</p>';
}

/* ---------- Yenilikler (105) ---------- */

async function startWhatsNewTour(tour, newsDialog) {
  newsDialog.close();
  const menu = document.getElementById("tools-menu");
  const menuButton = document.getElementById("tools-menu-button");
  const panel = document.createElement("aside");
  panel.id = "whats-new-tour-panel";
  panel.className = "whats-new-tour-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Yeni araçların kısa tanıtımı");
  document.body.append(panel);
  let stepIndex = 0;
  let highlighted = null;
  const clearHighlight = () => {
    highlighted?.classList.remove("easy-guide-target");
    highlighted = null;
  };
  const closeMenu = () => {
    if (menu && !menu.hidden) {
      menu.hidden = true;
      menuButton?.setAttribute("aria-expanded", "false");
    }
    const flyout = document.querySelector(".easy-tool-flyout");
    if (flyout) flyout.hidden = true;
  };
  const closeTour = () => {
    clearHighlight();
    panel.remove();
    document.removeEventListener("keydown", handleEscape, true);
  };
  const handleEscape = (event) => {
    if (event.key === "Escape") closeTour();
  };
  document.addEventListener("keydown", handleEscape, true);

  const render = async () => {
    clearHighlight();
    const step = tour[stepIndex];
    let target = null;
    let note = "";
    if (step.action === "tool-search") {
      if (menu?.hidden) menuButton?.click();
      const search = document.getElementById("easy-tools-search");
      if (!search) throw new Error("Araç araması açılamadı.");
      search.value = step.query;
      search.dispatchEvent(new Event("input", { bubbles: true }));
      target = [...menu.querySelectorAll(".easy-search-result .easy-search-pin")]
        .find((button) => button.textContent.trim() === "Yerini göster");
      if (!target) throw new Error("Arama sonucu için “Yerini göster” bulunamadı.");
    } else if (step.action === "view-control" && step.view === "attendance"
        && step.target === "undo-attendance") {
      closeMenu();
      if (state.scheduleDirty || state.attendanceDirty || state.neighborDirty?.size) {
        note = "Kaydedilmemiş taslak açık. Bu adım taslağı değiştirmez; puantaj ekranına geçmek için önce taslağı kaydedin.";
      } else {
        if (state.view !== "attendance") await switchView("attendance");
        target = document.getElementById("undo-attendance");
      }
    } else {
      throw new Error("Bu yenilik turu adımı desteklenmiyor.");
    }
    if (target) {
      highlighted = target;
      target.scrollIntoView({ block: "center", inline: "nearest" });
      target.classList.add("easy-guide-target");
      target.focus({ preventScroll: true });
    }
    panel.replaceChildren();
    const heading = document.createElement("h3");
    heading.textContent = step.title;
    const description = document.createElement("p");
    description.textContent = note || step.description;
    const progress = document.createElement("small");
    progress.textContent = `${stepIndex + 1}/${tour.length} · Yalnız bu sürümde değişen araçlar`;
    const actions = document.createElement("div");
    actions.className = "whats-new-tour-actions";
    if (stepIndex < tour.length - 1) {
      const next = document.createElement("button");
      next.type = "button";
      next.className = "button button-primary";
      next.textContent = "Sonraki yenilik";
      next.addEventListener("click", () => {
        stepIndex += 1;
        render().catch((error) => { closeTour(); toast(error.message, true); });
      });
      actions.append(next);
    }
    const close = document.createElement("button");
    close.type = "button";
    close.className = "button button-light";
    close.dataset.whatsNewTourClose = "true";
    close.textContent = stepIndex === tour.length - 1 ? "Turu bitir" : "Turu kapat";
    close.addEventListener("click", closeTour);
    actions.append(close);
    panel.append(heading, description, progress, actions);
  };
  await render();
}

async function openWhatsNew(markSeen = true) {
  const data = await request("/api/whats-new");
  const dialog = extrasDialog("whats-new-dialog", "Yenilikler", `Sürüm ${data.version}`);
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = data.items.map((release) =>
    `<h3 class="extras-subtitle">Sürüm ${escapeHtml(release.version)}</h3><ul class="whats-new-list">${release.items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`).join("");
  const currentRelease = data.items.find((release) => release.version === data.version);
  if (Array.isArray(currentRelease?.tour) && currentRelease.tour.length) {
    const start = document.createElement("button");
    start.type = "button";
    start.className = "button button-primary";
    start.dataset.whatsNewTourStart = "true";
    start.textContent = "Değişen araçları yerinde göster";
    start.addEventListener("click", () => startWhatsNewTour(currentRelease.tour, dialog)
      .catch((error) => toast(error.message, true)));
    body.append(start);
  }
  showExtras(dialog);
  if (markSeen && data.unseen) request("/api/whats-new/seen", { method: "POST", body: "{}" }).catch(() => {});
}

async function startupWhatsNew() {
  try {
    const data = await request("/api/whats-new");
    if (data.unseen && !document.querySelector("dialog[open]")) await openWhatsNew(true);
  } catch (error) { /* sessizce geç */ }
}

/* ---------- Taslak kurtarma (24) ---------- */

function draftKey(kind) {
  const month = kind === "schedule" ? state.scheduleMonth : state.attendanceMonth;
  return `vp-draft:${kind}:${month}`;
}

function writeDraft(kind) {
  clearTimeout(features.draftTimers[kind]);
  features.draftTimers[kind] = setTimeout(() => {
    try {
      const data = kind === "schedule" ? state.schedule : state.attendance;
      if (!data) return;
      localStorage.setItem(draftKey(kind), JSON.stringify({
        at: new Date().toISOString(), revision: data.revision || "", snapshot: editSnapshot(kind),
      }));
    } catch (error) { /* depolama dolu olabilir */ }
  }, 600);
}

function clearDraft(kind) {
  clearTimeout(features.draftTimers[kind]);
  try { localStorage.removeItem(draftKey(kind)); } catch (error) { /* yok say */ }
}

function offerDraft(kind) {
  let draft;
  try { draft = JSON.parse(localStorage.getItem(draftKey(kind)) || "null"); } catch (error) { draft = null; }
  if (!draft?.snapshot) return;
  const current = editSnapshot(kind);
  if (JSON.stringify(current) === JSON.stringify(draft.snapshot)) { clearDraft(kind); return; }
  const data = kind === "schedule" ? state.schedule : state.attendance;
  const stale = draft.revision && data?.revision && draft.revision !== data.revision;
  const label = kind === "schedule" ? "vardiya listesi" : "puantaj";
  const when = new Date(draft.at).toLocaleString("tr-TR");
  const dialog = extrasDialog(`draft-${kind}-dialog`, "Kaydedilmemiş taslak bulundu",
    `${label} · ${when}`);
  dialog.querySelector(".extras-body").innerHTML = `
    <p>Bu ay için kaydedilmeden kalmış değişiklikler var (uygulama beklenmedik kapanmış olabilir).</p>
    ${stale ? '<p class="health-warning">Dikkat: taslaktan sonra bu ay başka bir yerden kaydedilmiş. Geri yüklerseniz o değişikliklerin üzerine yazılabilir.</p>' : ""}
    <div class="dialog-footer"><button class="button button-light" type="button" data-draft="discard">Taslağı sil</button>
    <button class="button button-primary" type="button" data-draft="restore">Taslağı geri yükle</button></div>`;
  dialog.querySelector('[data-draft="discard"]').addEventListener("click", () => { clearDraft(kind); dialog.close(); });
  dialog.querySelector('[data-draft="restore"]').addEventListener("click", () => {
    const history = state.editHistory[kind];
    history.undo.push(editSnapshot(kind));
    applyEditSnapshot(kind, draft.snapshot);
    history.lastSnapshot = editSnapshot(kind);
    updateEditHistoryControls(kind);
    dialog.close();
    toast("Taslak geri yüklendi. Kalıcı olması için Kaydet'e basın.");
  });
  showExtras(dialog);
}

function installDrafts() {
  const wrapDirty = (name, kind) => {
    const original = window[name];
    if (typeof original !== "function") return;
    window[name] = function wrappedDirty(dirty = true, ...rest) {
      const result = original.call(this, dirty, ...rest);
      if (dirty) writeDraft(kind); else if (!features.loading[kind]) clearDraft(kind);
      return result;
    };
  };
  const wrapLoad = (name, kind) => {
    const original = window[name];
    if (typeof original !== "function") return;
    window[name] = async function wrappedLoad(...args) {
      features.loading[kind] = true;
      try {
        const result = await original.apply(this, args);
        setTimeout(() => offerDraft(kind), 0);
        return result;
      } finally {
        features.loading[kind] = false;
      }
    };
  };
  wrapDirty("setScheduleDirty", "schedule");
  wrapDirty("setAttendanceDirty", "attendance");
  wrapLoad("loadSchedule", "schedule");
  wrapLoad("loadAttendance", "attendance");
}

function startFeatures() {
  installDrafts();
  setTimeout(() => { startupHealth(); startupWhatsNew(); }, 1500);
}

EXTRAS_MENU.push(
  ["-"],
  ["Uygulama bilgisi", openAppInfo],
  ["Veri sağlığı ve onarım", openHealth],
  ["Teknik hata kayıtları", openTechnicalErrors],
  ["Yenilikler", () => openWhatsNew(true)],
);

startFeatures();
