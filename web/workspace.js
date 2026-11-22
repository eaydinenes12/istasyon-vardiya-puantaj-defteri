/* Çalışma alanı: kaldığı yerden devam, işlem göstergesi, komut paleti, özet kartı düzeni,
   yeni ay sihirbazı, ayar aktarımı, sorun raporu, örnek veri, şubeler ve telefon görünümü. */

/* ---------- İşlem göstergesi ve ağ hataları (98, 99) ---------- */

const wsActivity = { pending: 0, timer: null };

function wsProgress() {
  let bar = document.getElementById("ws-progress");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "ws-progress";
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-label", "İşlem sürüyor");
    bar.hidden = true;
    document.body.append(bar);
  }
  return bar;
}

function wsSavedPill() {
  let pill = document.getElementById("ws-saved");
  if (!pill) {
    pill = document.createElement("div");
    pill.id = "ws-saved";
    pill.setAttribute("role", "status");
    pill.setAttribute("aria-live", "polite");
    document.body.append(pill);
  }
  pill.textContent = "✓ Kaydedildi";
  pill.classList.add("visible");
  clearTimeout(pill.hideTimer);
  pill.hideTimer = setTimeout(() => pill.classList.remove("visible"), 1600);
}

(function wsWrapFetch() {
  const previous = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    const method = String(init.method || "GET").toUpperCase();
    const tracked = url.startsWith("/api/") && !url.startsWith("/api/lock");
    if (tracked) {
      wsActivity.pending += 1;
      if (!wsActivity.timer) wsActivity.timer = setTimeout(() => { wsProgress().hidden = false; }, 180);
    }
    try {
      const response = await previous(input, init);
      if (tracked && response.ok && method !== "GET" && !url.includes("save=1") && !url.startsWith("/api/auth")) wsSavedPill();
      return response;
    } catch (error) {
      if (error && error.name === "AbortError") throw error;
      throw new Error("Uygulama sunucusuna ulaşılamadı. Bağlantı kesildi veya uygulama kapanıyor. "
        + "Düzeltme önerisi: Uygulamayı kapatıp yeniden açın; sorun sürerse Araçlar › Sorun raporu oluştur.");
    } finally {
      if (tracked) {
        wsActivity.pending = Math.max(0, wsActivity.pending - 1);
        if (!wsActivity.pending) {
          clearTimeout(wsActivity.timer);
          wsActivity.timer = null;
          wsProgress().hidden = true;
        }
      }
    }
  };
}());

/* ---------- Kaldığı yerden devam (31) ---------- */

const WS_RESUME_KEY = "vp-last-location";

function wsResumeEnabled() { return localStorage.getItem("vp-resume") !== "off"; }

function wsRememberLocation() {
  if (!wsResumeEnabled() || typeof state === "undefined") return;
  if (!["schedule", "attendance"].includes(state.view)) return;
  localStorage.setItem(WS_RESUME_KEY, JSON.stringify({
    view: state.view, scheduleMonth: state.scheduleMonth, attendanceMonth: state.attendanceMonth, weekIndex: state.weekIndex,
  }));
}

async function wsWaitFor(check, tries = 100) {
  for (let index = 0; index < tries && !check(); index += 1) await new Promise((resolve) => setTimeout(resolve, 60));
  return check();
}

async function wsRestoreLocation() {
  if (!wsResumeEnabled()) return;
  let saved;
  try { saved = JSON.parse(localStorage.getItem(WS_RESUME_KEY) || "null"); } catch { saved = null; }
  if (!saved || !/^\d{4}-\d{2}$/.test(saved.scheduleMonth || "")) return;
  await wsWaitFor(() => !document.getElementById("app-loader") && state.schedule);
  if (state.view !== "schedule" || state.scheduleDirty) return;
  if (saved.scheduleMonth !== state.scheduleMonth) {
    const input = el("#schedule-month");
    input.value = saved.scheduleMonth;
    input.dispatchEvent(new Event("change"));
    await wsWaitFor(() => state.scheduleMonth === saved.scheduleMonth && state.schedule);
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  if (Number.isInteger(saved.weekIndex) && saved.weekIndex !== state.weekIndex) {
    const week = el("#schedule-week");
    if (week && [...week.options].some((option) => option.value === String(saved.weekIndex))) {
      week.value = String(saved.weekIndex);
      week.dispatchEvent(new Event("change"));
    }
  }
  if (saved.view === "attendance" && /^\d{4}-\d{2}$/.test(saved.attendanceMonth || "")) {
    state.attendanceMonth = saved.attendanceMonth;
    const input = el("#attendance-month");
    if (input) input.value = saved.attendanceMonth;
    await switchView("attendance");
  }
  if (saved.scheduleMonth !== currentMonth() || saved.view === "attendance") {
    toast(`Kaldığınız yerden devam: ${saved.view === "attendance" ? "Puantaj" : "Vardiya"} · ${extrasMonthLabel(saved.view === "attendance" ? saved.attendanceMonth : saved.scheduleMonth)}`);
  }
}

/* ---------- Komut paleti: Ctrl+K araç komutlarını da arar (57) ---------- */

function wsNormalize(text) {
  return String(text).toLocaleLowerCase("tr-TR").replace(/\s+/g, " ").trim();
}

function wsCommands(query) {
  const words = wsNormalize(query).split(" ").filter(Boolean);
  if (!words.length) return [];
  return EXTRAS_MENU
    .map(([label, action], index) => ({ label, action, index }))
    .filter((item) => item.label !== "-" && typeof item.action === "function")
    .filter((item) => words.every((word) => wsNormalize(item.label).includes(word)))
    .slice(0, 8);
}

const wsOriginalSearch = openSearch;
openSearch = function wsSearchWithCommands() {
  wsOriginalSearch();
  const dialog = document.getElementById("search-dialog");
  if (!dialog || dialog.querySelector("#ws-command-results")) return;
  const heading = dialog.querySelector(".dialog-heading h2");
  if (heading) heading.textContent = "Ara: personel veya komut";
  const input = dialog.querySelector("#extras-search-input");
  input.placeholder = "ör. Ayşe veya “yedek”, “yeni ay”";
  const box = document.createElement("div");
  box.id = "ws-command-results";
  box.className = "ws-commands";
  dialog.querySelector("#extras-search-results").before(box);
  const run = (index) => {
    dialog.close();
    Promise.resolve().then(() => EXTRAS_MENU[index][1]()).catch((error) => toast(error.message, true));
  };
  input.addEventListener("input", () => {
    const items = wsCommands(input.value);
    box.innerHTML = items.length ? `<h3>Komutlar</h3>${items.map((item, position) =>
      `<button type="button" class="ws-command${position === 0 ? " first" : ""}" data-command="${item.index}">› ${escapeHtml(item.label)}</button>`).join("")}` : "";
  });
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const first = box.querySelector("[data-command]");
    if (first) { event.preventDefault(); run(Number(first.dataset.command)); }
  });
  box.addEventListener("click", (event) => {
    const button = event.target.closest("[data-command]");
    if (button) run(Number(button.dataset.command));
  });
};

/* ---------- Bugünün özeti: kart düzeni (114) ---------- */

function wsDashboardPrefs() {
  try { return JSON.parse(localStorage.getItem("vp-dashboard") || "{}"); } catch { return {}; }
}

function wsCardKey(card) {
  return (card.querySelector("h3")?.childNodes[0]?.textContent || "").trim();
}

function wsApplyDashboard(dialog) {
  const grid = dialog.querySelector(".dashboard-grid");
  if (!grid) return;
  const prefs = wsDashboardPrefs();
  const hidden = new Set(prefs.hidden || []);
  const order = prefs.order || [];
  const cards = [...grid.querySelectorAll(".dashboard-card")];
  cards.sort((a, b) => {
    const left = order.indexOf(wsCardKey(a));
    const right = order.indexOf(wsCardKey(b));
    return (left < 0 ? 999 : left) - (right < 0 ? 999 : right);
  }).forEach((card) => {
    card.hidden = hidden.has(wsCardKey(card));
    grid.append(card);
  });
}

function wsDashboardEditor(dialog) {
  const grid = dialog.querySelector(".dashboard-grid");
  let editor = dialog.querySelector(".ws-dash-editor");
  if (editor) { editor.remove(); return; }
  editor = document.createElement("div");
  editor.className = "ws-dash-editor";
  const render = () => {
    const keys = [...grid.querySelectorAll(".dashboard-card")].map(wsCardKey);
    const hidden = new Set(wsDashboardPrefs().hidden || []);
    editor.innerHTML = `<p class="dialog-help">Kartları gösterin/gizleyin ve sırasını değiştirin. Tercih bu bilgisayarda saklanır.</p>
      <ol>${keys.map((key, index) => `<li><label><input type="checkbox" data-card="${escapeHtml(key)}"${hidden.has(key) ? "" : " checked"}> ${escapeHtml(key)}</label>
        <span><button type="button" class="icon-button" data-move="${index}" data-dir="-1" aria-label="${escapeHtml(key)} yukarı"${index === 0 ? " disabled" : ""}>↑</button>
        <button type="button" class="icon-button" data-move="${index}" data-dir="1" aria-label="${escapeHtml(key)} aşağı"${index === keys.length - 1 ? " disabled" : ""}>↓</button></span></li>`).join("")}</ol>
      <button type="button" class="button button-light" data-reset>Varsayılan düzen</button>`;
  };
  const save = (prefs) => { localStorage.setItem("vp-dashboard", JSON.stringify(prefs)); wsApplyDashboard(dialog); render(); };
  editor.addEventListener("change", (event) => {
    const box = event.target.closest("[data-card]");
    if (!box) return;
    const prefs = wsDashboardPrefs();
    const hidden = new Set(prefs.hidden || []);
    if (box.checked) hidden.delete(box.dataset.card); else hidden.add(box.dataset.card);
    save({ ...prefs, hidden: [...hidden] });
  });
  editor.addEventListener("click", (event) => {
    if (event.target.closest("[data-reset]")) { localStorage.removeItem("vp-dashboard"); location.reload(); return; }
    const button = event.target.closest("[data-move]");
    if (!button) return;
    const keys = [...grid.querySelectorAll(".dashboard-card")].map(wsCardKey);
    const index = Number(button.dataset.move);
    const target = index + Number(button.dataset.dir);
    [keys[index], keys[target]] = [keys[target], keys[index]];
    save({ ...wsDashboardPrefs(), order: keys });
  });
  render();
  grid.before(editor);
}

const wsOriginalDashboard = openDashboard;
openDashboard = async function wsDashboard() {
  await wsOriginalDashboard();
  const dialog = document.getElementById("dashboard-dialog");
  if (!dialog) return;
  wsApplyDashboard(dialog);
  const actions = dialog.querySelector(".extras-actions");
  if (actions && !actions.querySelector("[data-ws-dash]")) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "button button-light";
    button.dataset.wsDash = "1";
    button.textContent = "Kartları düzenle";
    button.addEventListener("click", () => wsDashboardEditor(dialog));
    actions.prepend(button);
  }
};

/* ---------- Yeni ay sihirbazı (58) ---------- */

function wsNextMonth(month) {
  const [year, number] = month.split("-").map(Number);
  return number === 12 ? `${year + 1}-01` : `${year}-${String(number + 1).padStart(2, "0")}`;
}

function openNewMonthWizard() {
  const month = wsNextMonth(state.scheduleMonth);
  const dialog = extrasDialog("new-month-dialog", "Yeni ay sihirbazı", "Yeni dönemi önceki aydan hazırlayın.");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = `<div class="extras-form ws-wizard">
    <label class="extras-search"><span>Hazırlanacak ay</span><input type="month" id="ws-nm-month" value="${month}"></label>
    <fieldset class="extras-choice"><legend>Ne yapılsın?</legend>
      <label><input type="checkbox" id="ws-nm-roster" checked> Önceki ayın personel ve bölüm dizilimini kopyala (pasif personel atlanır)</label>
      <label><input type="checkbox" id="ws-nm-fill"> Önceki ayın son haftasını tüm aya taslak olarak uygula (haftanın günlerine göre)</label>
      <label><input type="checkbox" id="ws-nm-lock"> Önceki ayı kilitle (sonradan arşivden açılabilir)</label>
    </fieldset>
    <p class="dialog-help" id="ws-nm-note"></p></div>`;
  const note = body.querySelector("#ws-nm-note");
  const refresh = () => {
    const value = body.querySelector("#ws-nm-month").value;
    note.textContent = value ? `${extrasMonthLabel(value)} hazırlanacak. Ay daha önce doldurulmuşsa kopyalama yapılmaz; mevcut plan korunur.` : "";
  };
  body.addEventListener("input", refresh);
  refresh();
  const actions = dialog.querySelector(".extras-actions");
  const go = document.createElement("button");
  go.type = "button";
  go.className = "button button-primary";
  go.textContent = "Ayı hazırla";
  go.addEventListener("click", () => extrasRun(go, async () => {
    const target = body.querySelector("#ws-nm-month").value;
    if (!/^\d{4}-\d{2}$/.test(target)) throw new Error("Hazırlanacak ayı seçin.");
    if (state.scheduleDirty && !window.confirm("Kaydedilmemiş vardiya değişiklikleri var. Yine de devam edilsin mi?")) return;
    const result = await request("/api/workspace/new-month", {
      method: "POST",
      body: JSON.stringify({
        month: target,
        copy_roster: body.querySelector("#ws-nm-roster").checked,
        fill_pattern: body.querySelector("#ws-nm-fill").checked,
        lock_previous: body.querySelector("#ws-nm-lock").checked,
      }),
    });
    dialog.close();
    state.scheduleDirty = false;
    if (state.view !== "schedule") await switchView("schedule");
    const input = el("#schedule-month");
    input.value = target;
    input.dispatchEvent(new Event("change"));
    toast(`${extrasMonthLabel(target)} hazır: ${result.rows} satır, ${result.filled_days} gün taslak${result.locked ? `, ${extrasMonthLabel(result.source)} kilitlendi` : ""}.`);
  }));
  actions.prepend(go);
  showExtras(dialog);
}

/* ---------- Ayarları dışa / içe aktar (75) ---------- */

function openSettingsTransfer() {
  const dialog = extrasDialog("settings-transfer-dialog", "Ayarları aktar",
    "Bölümler, hızlı girişler, mesai kuralları, görünüm ve Excel ayarları başka bilgisayara taşınabilir. Personel ve puantaj verisi aktarılmaz.");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = `<div class="extras-form">
    <section><h3>Dışa aktar</h3><p class="dialog-help">Tüm ayarlar tek bir .json dosyasına kaydedilir.</p>
      <button type="button" class="button button-primary" id="ws-settings-export">Ayar dosyası kaydet</button></section>
    <section><h3>İçe aktar</h3><p class="dialog-help">Önce otomatik yedek alınır. Geçersiz bir ayar grubu atlanır, diğerleri uygulanır.</p>
      <input type="file" id="ws-settings-file" accept=".json,application/json">
      <div id="ws-settings-preview"></div></section></div>`;
  let parsed = null;
  body.querySelector("#ws-settings-export").addEventListener("click", (event) => extrasRun(event.currentTarget, () =>
    saveReportFile("/api/reports/settings-export", "json", `VardiyaPuantaj_Ayarlar_${dateKey(new Date())}.json`)));
  const preview = body.querySelector("#ws-settings-preview");
  let importCancelled = false;
  let settingsFileRevision = 0;
  body.querySelector("#ws-settings-file").addEventListener("change", async (event) => {
    const revision = ++settingsFileRevision;
    const file = event.target.files[0];
    preview.innerHTML = file ? '<p role="status">Ayar dosyası okunuyor…</p>' : "";
    parsed = null;
    if (!file) return;
    try {
      const contents = await file.text();
      const imported = JSON.parse(contents);
      if (revision !== settingsFileRevision) return;
      if (imported.format !== "vardiya-puantaj-ayarlar" || typeof imported.settings !== "object") throw new Error();
      parsed = imported;
      preview.innerHTML = '<p role="status">Ayar grupları mevcut değerlerle karşılaştırılıyor…</p>';
    } catch {
      if (revision !== settingsFileRevision) return;
      preview.innerHTML = '<p class="reports-warning">Bu dosya Vardiya Puantaj ayar dosyası değil.</p>';
      return;
    }
    let comparison;
    try {
      comparison = await request("/api/workspace/settings-import/preview", {
        method: "POST", body: JSON.stringify({ data: parsed, keys: Object.keys(parsed.settings) }),
      });
    } catch (error) {
      if (revision !== settingsFileRevision) return;
      parsed = null;
      preview.innerHTML = `<p class="reports-warning">${escapeHtml(error.message)}</p>`;
      return;
    }
    if (revision !== settingsFileRevision) return;
    preview.innerHTML = `<p class="dialog-help">Sürüm ${escapeHtml(parsed.app_version || "?")} · ${escapeHtml(parsed.exported_at || "")}
      · ${comparison.changed} grup farklı</p>
      <fieldset class="extras-choice"><legend>Uygulanacak gruplar</legend>${comparison.groups.map((group) =>
        `<label><input type="checkbox" data-key="${escapeHtml(group.key)}"${group.recognized ? " checked" : " disabled"}>
        ${escapeHtml(group.label)} · ${!group.recognized ? "bu sürümde tanınmıyor"
          : group.changed === false ? "zaten aynı" : group.changed === true ? "değişecek" : "mevcut değer okunamadı"}</label>`).join("")}</fieldset>
      <p class="dialog-help" id="ws-settings-progress" role="status">İşlem başlamadan iptal edilebilir. Uygulama sırasında iptal, o anki ayar grubunu güvenle tamamlar ve sıradakileri başlatmaz.</p>
      <div class="dialog-footer"><button type="button" class="button button-primary" id="ws-settings-apply">Seçilenleri içe aktar</button>
      <button type="button" class="button button-outline" id="ws-settings-cancel" hidden>Aşamaları durdur</button></div>
      <div id="ws-settings-report"></div>`;
    preview.querySelector("#ws-settings-apply").addEventListener("click", (click) => extrasRun(click.currentTarget, async () => {
      const chosen = [...preview.querySelectorAll("[data-key]:checked")].map((box) => box.dataset.key);
      if (!chosen.length) throw new Error("En az bir ayar grubu seçin.");
      if (state.scheduleDirty || state.attendanceDirty) throw new Error("Önce kaydedilmemiş vardiya ve puantaj değişikliklerini kaydedin.");
      const importData = parsed;
      importCancelled = false;
      const progress = preview.querySelector("#ws-settings-progress");
      const report = preview.querySelector("#ws-settings-report");
      const cancel = preview.querySelector("#ws-settings-cancel");
      const apply = preview.querySelector("#ws-settings-apply");
      apply.disabled = true;
      body.querySelector("#ws-settings-file").disabled = true;
      for (const box of preview.querySelectorAll("[data-key]")) box.disabled = true;
      cancel.hidden = false;
      cancel.onclick = () => {
        importCancelled = true;
        cancel.disabled = true;
        progress.textContent = "İptal istendi. Devam eden tek ayar grubu tamamlanacak; sonraki gruplar başlatılmayacak.";
      };
      const finished = [];
      let backup = "";
      let failure = "";
      let failedGroup = "";
      try {
        progress.textContent = "Güvenlik yedeği hazırlanıyor ve bütünlüğü doğrulanıyor…";
        const prepared = await request("/api/workspace/settings-import/backup", {
          method: "POST", body: JSON.stringify({}),
        });
        backup = prepared.backup;
        for (let index = 0; index < chosen.length; index += 1) {
          if (importCancelled) break;
          const key = chosen[index];
          const label = WS_SETTING_LABELS[key] || key;
          progress.textContent = `Ayar grubu uygulanıyor: ${label} (${index + 1}/${chosen.length})…`;
          try {
            const result = await request("/api/workspace/settings-import", {
              method: "POST", body: JSON.stringify({ data: importData, keys: [key], backup }),
            });
            if (result.applied.length) finished.push({ label, status: "uygulandı" });
            else if (result.skipped.length) finished.push({
              label, status: `atlanmış: ${result.skipped[0].reason}`,
            });
            else throw new Error("Sunucu bu grup için bir sonuç döndürmedi.");
          } catch (error) {
            failure = `${label}: ${error.message}`;
            failedGroup = label;
            break;
          }
        }
      } catch (error) {
        failure = error.message;
      } finally {
        cancel.hidden = true;
        cancel.disabled = false;
        body.querySelector("#ws-settings-file").disabled = false;
        for (const box of preview.querySelectorAll("[data-key]")) box.disabled = false;
      }
      const attempted = finished.length + (failedGroup ? 1 : 0);
      const remaining = chosen.slice(attempted);
      progress.textContent = importCancelled
        ? "İptal güvenli bir grup sınırında tamamlandı."
        : failure ? "İşlem hata nedeniyle durdu." : "Seçilen grupların işlemi tamamlandı.";
      report.innerHTML = `<p>${finished.filter((item) => item.status === "uygulandı").length} grup uygulandı; ${
        finished.filter((item) => item.status.startsWith("atlanmış")).length} grup atlandı; ${remaining.length} grup başlatılmadı.
        ${backup ? `Güvenlik yedeği: ${escapeHtml(backup)}.` : ""}</p>
        ${finished.length ? `<ul>${finished.map((item) => `<li>${escapeHtml(item.label)} · ${escapeHtml(item.status)}</li>`).join("")}</ul>` : ""}
        ${failure ? `<p class="reports-warning">${failedGroup
          ? `Sonucu doğrulanamayan alt görev: ${escapeHtml(failedGroup)}. Durdurma nedeni: ${escapeHtml(failure)}`
          : `Yedek aşaması tamamlanamadı: ${escapeHtml(failure)}`}</p>` : ""}
        ${remaining.length ? `<p>Başlatılmayanlar: ${remaining.map((key) => escapeHtml(WS_SETTING_LABELS[key] || key)).join(", ")}</p>` : ""}
        ${finished.some((item) => item.status === "uygulandı")
          ? '<button type="button" class="button button-outline" id="ws-settings-reload">Yeni ayarları yükle</button>' : ""}`;
      if (finished.some((item) => item.status === "uygulandı")) {
        report.querySelector("#ws-settings-reload").addEventListener("click", () => location.reload());
      }
      if (!importCancelled && !failure && attempted === chosen.length) {
        progress.textContent += " Tamamlanan alt gruplar ve atlanan gruplar yukarıda listelendi.";
      }
    }));
  });
  showExtras(dialog);
}

const WS_SETTING_LABELS = {
  ui_settings: "Görünüm ve vardiya türleri", template_library: "Şablon kitaplığı",
  ignored_warnings: "Görmezden gelinen uyarılar", overtime_settings: "Fazla mesai kuralları",
  schedule_validation_settings: "Vardiya doğrulama", workbook_settings: "Excel çıktı ayarları",
  quick_entries: "Hızlı girişler", schedule_sections: "Bölümler", custom_holidays: "Özel tatiller",
  compliance_settings: "Yasal uyum", planning_settings: "Planlama kuralları", backup_settings: "Yedek saklama",
};

/* ---------- Sorun raporu (76) ---------- */

async function saveProblemReport() {
  const path = "/api/reports/problem-report";
  const { files, review } = await request(`${path}?preview=1`);
  const dialog = extrasDialog("problem-report-dialog", "Sorun raporunu incele",
    "Dosyaları açarak içeriğini kontrol edin. Paket yalnız seçtiğiniz konuma kaydedilir; kimseye gönderilmez.");
  const body = dialog.querySelector(".extras-body");
  body.replaceChildren();
  for (const [name, content] of Object.entries(files)) {
    const details = document.createElement("details");
    const title = document.createElement("summary");
    const text = document.createElement("pre");
    title.textContent = name;
    text.textContent = content;
    details.append(title, text);
    body.append(details);
  }
  const save = document.createElement("button");
  save.type = "button";
  save.className = "button button-outline";
  save.textContent = "İncelediğim paketi kaydet";
  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      if (await saveReportFile(`${path}?review=${encodeURIComponent(review)}`, "zip",
        `Sorun_Raporu_${dateKey(new Date())}.zip`)) dialog.close();
    } catch (error) { toast(error.message, true); }
    finally { save.disabled = false; }
  });
  body.append(save);
  showExtras(dialog);
}

/* ---------- Örnek veri (77) ---------- */

async function loadSampleData() {
  if (state.people.length) throw new Error("Örnek veri yalnızca hiç personel yokken yüklenebilir.");
  if (!window.confirm("Uygulamayı denemek için 6 örnek personel ve iki haftalık vardiya eklensin mi? Daha sonra personel listesinden silebilirsiniz.")) return;
  const result = await request("/api/workspace/sample-data", { method: "POST", body: JSON.stringify({ month: state.scheduleMonth }) });
  toast(`Örnek veri yüklendi: ${result.people} personel, ${result.rows} satır.`);
  location.reload();
}

function wsOfferSample() {
  if (state.people.length || localStorage.getItem("vp-sample-offer") === "off") return;
  if (document.getElementById("ws-sample-offer")) return;
  const box = document.createElement("div");
  box.id = "ws-sample-offer";
  box.className = "ws-offer";
  box.innerHTML = `<p><strong>Henüz personel yok.</strong> Uygulamayı denemek için örnek veri yükleyebilirsiniz.</p>
    <button type="button" class="button button-primary" data-sample>Örnek veri yükle</button>
    <button type="button" class="button button-light" data-dismiss>Gösterme</button>`;
  box.addEventListener("click", (event) => {
    if (event.target.closest("[data-sample]")) extrasRun(event.target, loadSampleData);
    if (event.target.closest("[data-dismiss]")) {
      localStorage.setItem("vp-sample-offer", "off");
      box.remove();
      document.dispatchEvent(new Event("vp-sample-offer-dismissed"));
    }
  });
  document.body.append(box);
}

/* ---------- Şubeler (55) ---------- */

async function openBranches() {
  const dialog = extrasDialog("branches-dialog", "Şubeler",
    "Her şubenin personeli, vardiyası, puantajı ve yedekleri ayrı veri dosyasında tutulur.");
  const body = dialog.querySelector(".extras-body");
  const render = (data) => {
    body.innerHTML = `${data.restart_required ? '<p class="reports-warning">Seçilen şube uygulama yeniden açıldığında yüklenecek.</p>' : ""}
      <table class="archive-table extras-table"><thead><tr><th>Şube</th><th>Durum</th><th></th></tr></thead><tbody>${data.branches.map((item) => `<tr>
        <th scope="row">${escapeHtml(item.name)}</th>
        <td>${item.id === data.running ? "Açık" : item.id === data.active ? "Sonraki açılışta" : item.exists ? "Hazır" : "Yeni (boş)"}</td>
        <td class="ws-row-actions">${item.id !== data.running ? `<button type="button" class="button button-primary" data-switch="${escapeHtml(item.id)}">Bu şubeye geç</button>` : ""}
          <button type="button" class="button button-light" data-rename="${escapeHtml(item.id)}" data-name="${escapeHtml(item.name)}">Yeniden adlandır</button>
          ${item.id !== "ana" && item.id !== data.running && item.id !== data.active ? `<button type="button" class="button button-light" data-remove="${escapeHtml(item.id)}">Listeden kaldır</button>` : ""}</td></tr>`).join("")}</tbody></table>
      <form class="ws-inline-form" id="ws-branch-add"><label><span>Yeni şube adı</span><input maxlength="60" required placeholder="ör. Merkez İstasyon"></label>
        <button class="button button-primary" type="submit">Şube ekle</button></form>
      <p class="dialog-help">Listeden kaldırılan şubenin veri dosyası silinmez; aynı adla yeniden eklemek yeni boş dosya açar.</p>`;
  };
  const send = async (payload) => render(await request("/api/workspace/branches", { method: "POST", body: JSON.stringify(payload) }));
  body.onsubmit = (event) => {
    event.preventDefault();
    const input = event.target.querySelector("input");
    extrasRun(event.target.querySelector("button"), () => send({ action: "add", name: input.value }));
  };
  body.onclick = (event) => {
    const target = event.target.closest("button");
    if (!target) return;
    if (target.dataset.switch) {
      extrasRun(target, async () => {
        if ((state.scheduleDirty || state.attendanceDirty) && !window.confirm("Kaydedilmemiş değişiklikler kaybolacak. Devam edilsin mi?")) return;
        const result = await request("/api/workspace/branches", {
          method: "POST", body: JSON.stringify({ action: "switch", id: target.dataset.switch, restart: true }),
        });
        render(result);
        toast(result.restarting ? "Uygulama seçilen şube ile yeniden açılıyor…" : "Şube seçildi. Uygulamayı kapatıp açtığınızda yüklenecek.");
      });
    } else if (target.dataset.rename) {
      const name = window.prompt("Yeni şube adı", target.dataset.name);
      if (name) extrasRun(target, () => send({ action: "rename", id: target.dataset.rename, name }));
    } else if (target.dataset.remove) {
      if (window.confirm("Şube listeden kaldırılsın mı? Veri dosyası silinmez.")) extrasRun(target, () => send({ action: "remove", id: target.dataset.remove }));
    }
  };
  render(await request("/api/workspace/branches"));
  showExtras(dialog);
}

/* ---------- Telefondan görüntüleme (51) ---------- */

async function openPhoneView() {
  const dialog = extrasDialog("phone-view-dialog", "Telefondan vardiya görüntüle",
    "Aynı Wi-Fi ağındaki telefonlar haftalık vardiyayı salt okunur görebilir. Varsayılan kapalıdır.");
  const body = dialog.querySelector(".extras-body");
  const render = (data) => {
    body.innerHTML = data.running ? `<p><strong>Açık.</strong> Telefonun tarayıcısına aşağıdaki adreslerden birini yazın:</p>
      <ul class="extras-list">${data.urls.map((url) => `<li><code class="ws-url">${escapeHtml(url)}</code>
        <button type="button" class="button button-light" data-copy="${escapeHtml(url)}">Kopyala</button></li>`).join("")}</ul>
      <p class="dialog-help">Adresteki gizli anahtar olmadan sayfa açılmaz. Kapatıp açınca anahtar değişir. Windows güvenlik duvarı izin isteyebilir.</p>
      <button type="button" class="button button-light" data-phone="stop">Kapat</button>`
      : `<p>Paylaşım kapalı. Açtığınızda yalnızca haftalık vardiya tablosu görüntülenir; değişiklik yapılamaz ve puantaj, maaş gibi bilgiler paylaşılmaz.</p>
      <button type="button" class="button button-primary" data-phone="start">Paylaşımı aç</button>`;
  };
  body.onclick = (event) => {
    const target = event.target.closest("button");
    if (!target) return;
    if (target.dataset.copy) {
      navigator.clipboard?.writeText(target.dataset.copy).then(() => toast("Adres kopyalandı."), () => toast("Kopyalanamadı.", true));
    } else if (target.dataset.phone) {
      extrasRun(target, async () => render(await request("/api/workspace/phone", {
        method: "POST", body: JSON.stringify({ action: target.dataset.phone }),
      })));
    }
  };
  render(await request("/api/workspace/phone"));
  showExtras(dialog);
}

async function openMobileDiagnostics() {
  const dialog = extrasDialog("mobile-diagnostics-dialog", "Güvenli mobil erişim tanılama",
    "Hizmet, yerel ağ, adres ve sertifika ayrı değerlendirilir. Güvenliği kapatmak önerilmez.");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = '<p role="status">Tanılama okunuyor…</p>';
  try {
    const data = await request("/api/workspace/mobile-diagnostics", { cache: "no-store" });
    const labels = {
      service: "Windows uygulamasındaki HTTPS hizmeti",
      network: "Yerel ağ ve güvenlik duvarı",
      address: "Telefonun kullanacağı HTTPS adresi",
      certificate: "Sertifika ve özel anahtar",
    };
    const items = Object.entries(labels).map(([key, label]) => {
      const item = data[key] || { status: "warning", message: "Tanılama bilgisi yok." };
      return `<section class="extras-note" data-status="${escapeHtml(item.status)}">
        <strong>${escapeHtml(label)} · ${escapeHtml(item.status)}</strong><p>${escapeHtml(item.message)}</p>
        ${key === "network" && data.addresses?.length
          ? `<p>Bu bilgisayardaki yerel adresler: ${data.addresses.map(escapeHtml).join(" · ")}</p>` : ""}
      </section>`;
    }).join("");
    body.innerHTML = `${items}<p>Bu tanılama gerçek telefon bağlantısını veya ağ geçişini sınamaz.
      Sertifika uyarısında HTTP’ye düşmeyin; geçerli sertifika, adres adı ve telefondaki güven zincirini düzeltin.
      Güvenlik duvarı değişikliklerini yalnız güvenilir yerel ağ için gözden geçirin.</p>`;
  } catch (error) {
    body.innerHTML = `<p role="alert">${escapeHtml(error.message)}</p>`;
  }
  showExtras(dialog);
}

/* ---------- Menü ve açılış ---------- */

if (typeof EXTRAS_MENU !== "undefined" && !EXTRAS_MENU.some((item) => item[0] === "Yeni ay sihirbazı")) {
  const dashboardIndex = EXTRAS_MENU.findIndex(([label]) => label === "Bugünün özeti");
  if (dashboardIndex >= 0) EXTRAS_MENU[dashboardIndex] = ["Bugünün özeti", () => openDashboard()];
  const searchIndex = EXTRAS_MENU.findIndex(([label]) => label.startsWith("Personel ara"));
  if (searchIndex >= 0) EXTRAS_MENU[searchIndex] = ["Personel / komut ara  (Ctrl+K)", () => openSearch()];
  EXTRAS_MENU.push(
    ["-"],
    ["Yeni ay sihirbazı", openNewMonthWizard],
    ["Şubeler", () => openBranches()],
    ["Telefondan görüntüleme (yerel ağ)", () => openPhoneView()],
    ["Güvenli mobil erişim tanılama", () => openMobileDiagnostics()],
    ["Ayarları dışa / içe aktar", openSettingsTransfer],
    ["Sorun raporu oluştur", saveProblemReport],
    ["Örnek veri yükle", loadSampleData],
  );
}

document.addEventListener("DOMContentLoaded", () => {
  window.addEventListener("beforeunload", wsRememberLocation);
  document.addEventListener("visibilitychange", wsRememberLocation);
  setInterval(wsRememberLocation, 5000);
  wsRestoreLocation().catch(() => {}).finally(async () => {
    await wsWaitFor(() => !document.getElementById("app-loader"));
    wsOfferSample();
  });
});
