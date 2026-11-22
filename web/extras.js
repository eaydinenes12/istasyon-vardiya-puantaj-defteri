"use strict";

const PLANNING_CARD_DAYS = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];
const PERSON_TAG_COLORS = [["", "Etiket yok"], ["#d92d20", "Kırmızı"], ["#f79009", "Turuncu"], ["#eaaa08", "Sarı"],
  ["#12b76a", "Yeşil"], ["#0ba5ec", "Mavi"], ["#6172f3", "Lacivert"], ["#9e77ed", "Mor"], ["#ee46bc", "Pembe"], ["#667085", "Gri"]];

function personInitials(name) {
  return String(name || "").trim().split(/\s+/).filter(Boolean).slice(0, 2)
    .map((part) => part[0].toLocaleUpperCase("tr-TR")).join("") || "?";
}

function personPhotoDataUrl(file) {
  return new Promise((resolve, reject) => {
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { reject(new Error("JPEG, PNG veya WEBP fotoğraf seçin.")); return; }
    if (file.size > 15 * 1024 * 1024) { reject(new Error("Fotoğraf 15 MB'tan büyük olamaz.")); return; }
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      const size = 160;
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = size;
      const side = Math.min(image.naturalWidth, image.naturalHeight);
      canvas.getContext("2d").drawImage(image, (image.naturalWidth - side) / 2, (image.naturalHeight - side) / 2, side, side, 0, 0, size, size);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/jpeg", 0.82));
    };
    image.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Fotoğraf okunamadı.")); };
    image.src = url;
  });
}

/* Ek araçlar: özet, arama, personel kartı, döngü, takas, tatil takvimi, görünüm,
   bulut yedeği, ay sonu paketi, kısayollar, güncelleme ve hücre notları. */

const extras = { events: new Map(), environment: null };

function extrasDialog(id, title, subtitle = "") {
  let dialog = document.getElementById(id);
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = id;
    dialog.className = "quick-entry-dialog forms-dialog extras-dialog";
    dialog.setAttribute("aria-labelledby", `${id}-title`);
    document.body.append(dialog);
    dialog.addEventListener("focusin", (event) => {
      if (!event.target.closest("[data-extras-minimize], [data-extras-restore], [data-extras-close]")) {
        dialog._extrasReturnFocus = event.target;
      }
    });
    dialog.addEventListener("click", (event) => {
      const minimize = event.target.closest("[data-extras-minimize]");
      if (minimize) {
        dialog.classList.add("extras-dialog-minimized");
        minimize.hidden = true;
        dialog.querySelector("[data-extras-restore]").hidden = false;
        dialog.querySelector(".extras-body").hidden = true;
        dialog.querySelector(".extras-actions").hidden = true;
        dialog.querySelector(".dialog-close").hidden = true;
        dialog.querySelector("[data-extras-restore]").focus();
        return;
      }
      if (event.target.closest("[data-extras-restore]")) {
        dialog.classList.remove("extras-dialog-minimized");
        dialog.querySelector("[data-extras-minimize]").hidden = false;
        dialog.querySelector("[data-extras-restore]").hidden = true;
        dialog.querySelector(".extras-body").hidden = false;
        dialog.querySelector(".extras-actions").hidden = false;
        dialog.querySelector(".dialog-close").hidden = false;
        const target = dialog._extrasReturnFocus;
        if (target?.isConnected && !target.disabled) target.focus({ preventScroll: true });
        else dialog.querySelector(".extras-body input, .extras-body select, .extras-body textarea, .extras-actions button")?.focus();
      }
    });
    dialog.addEventListener("close", () => {
      const opener = dialog._extrasOpenerFocus;
      const fallback = dialog._extrasFallbackFocus;
      dialog.classList.remove("extras-dialog-minimized");
      dialog._extrasReturnFocus = null;
      dialog._extrasOpenerFocus = null;
      dialog._extrasFallbackFocus = null;
      setTimeout(() => {
        const target = opener?.closest("dialog:not([open])") ? fallback : opener;
        if (target?.isConnected && !target.disabled) {
          const modals = [...document.querySelectorAll("dialog:modal")];
          if (modals.length && !modals[modals.length - 1].contains(target)) return;
          for (let parent = target.parentElement; parent; parent = parent.parentElement) {
            if (parent instanceof HTMLDetailsElement) parent.open = true;
          }
          target.focus({ preventScroll: true });
        }
      }, 0);
    });
  }
  const scope = extrasScopeLabel();
  dialog.innerHTML = `<div class="dialog-heading"><div><h2 id="${id}-title">${escapeHtml(title)}</h2>${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ""}
      ${scope ? `<p class="extras-scope" data-extras-scope role="note" aria-label="Açık görünüm">Görünüm: ${escapeHtml(scope)}</p>` : ""}</div>
    <div class="dialog-window-actions">
      <button class="button button-light dialog-minimize" type="button" data-extras-minimize aria-label="İşlem penceresini küçült" title="Tabloyu görmek için küçült">Küçült</button>
      <button class="button button-light dialog-restore" type="button" data-extras-restore aria-label="İşlem penceresini yeniden aç" hidden>Devam et</button>
      <button class="icon-button dialog-close" type="button" data-extras-close aria-label="Kapat">×</button>
    </div></div>
    <div class="extras-body"></div>
    <div class="dialog-actions extras-actions"><button class="button button-light" type="button" data-extras-close>Kapat</button></div>`;
  dialog.classList.remove("extras-dialog-minimized");
  dialog.querySelectorAll("[data-extras-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
  return dialog;
}

function showExtras(dialog, opener = document.activeElement) {
  if (dialog.open) return;
  dialog._extrasOpenerFocus = opener;
  dialog._extrasFallbackFocus = opener?.closest("dialog")?._extrasOpenerFocus || null;
  dialog.showModal();
  const firstControl = dialog.querySelector(".extras-body [autofocus]:not([disabled])")
    || dialog.querySelector(".extras-body input:not([disabled]), .extras-body select:not([disabled]), "
      + ".extras-body textarea:not([disabled]), .extras-body button:not([disabled]), "
      + ".extras-body a[href], .extras-body [tabindex]:not([tabindex='-1'])")
    || dialog.querySelector(".extras-actions button:not([disabled])");
  firstControl?.focus({ preventScroll: true });
}

async function extrasRun(button, action) {
  if (button) button.disabled = true;
  try { await action(); } catch (error) { toast(error.message, true); } finally { if (button) button.disabled = false; }
}

function extrasPeopleOptions(selected = "", activeOnly = false) {
  return state.people
    .filter((person) => !activeOnly || person.active)
    .sort((a, b) => a.name.localeCompare(b.name, "tr"))
    .map((person) => `<option value="${person.id}"${String(person.id) === String(selected) ? " selected" : ""}>${escapeHtml(person.name)}${person.active ? "" : " · pasif"}</option>`)
    .join("");
}

function extrasItemLabel(item) {
  if (!item) return "Boş";
  if (item.kind === "work") return `${item.start}–${item.end}${item.shift_label ? ` (${item.shift_label})` : ""}`;
  return scheduleKindLabel(item);
}

function extrasActiveMonth() {
  return state.view === "attendance" ? state.attendanceMonth : state.scheduleMonth;
}

function extrasMonthLabel(month) {
  const [year, number] = month.split("-").map(Number);
  return `${MONTH_NAMES[number - 1]} ${year}`;
}

function extrasScopeLabel() {
  const month = extrasActiveMonth();
  if (!/^\d{4}-\d{2}$/.test(month || "")) return "";
  const sectionFilter = state.view === "schedule" ? document.querySelector("#schedule-section-filter") : null;
  const section = sectionFilter?.value
    ? sectionFilter.selectedOptions[0]?.textContent.trim() || sectionFilter.value
    : state.view === "schedule" ? "Tüm bölümler" : "";
  const ids = state.view === "schedule"
    ? (state.schedule?.entries || []).filter((entry) => !sectionFilter?.value || entry.section === sectionFilter.value)
      .map((entry) => String(entry.employee_id))
    : (state.attendance?.rows || []).map((person) => String(person.employee_id));
  const people = new Set(ids.filter((id) => id && id !== "undefined"));
  return [extrasMonthLabel(month), section, `${people.size} personel`].filter(Boolean).join(" · ");
}

/* ---------- Görünüm (tema ve yazı boyutu) ---------- */

function appearancePrefs() {
  try { return JSON.parse(localStorage.getItem("vp-appearance") || "{}"); } catch { return {}; }
}

const APPEARANCE_THEMES = [
  ["light", "Açık", "#18354b", "#edf2f5", "#007d78"],
  ["dark", "Koyu", "#111a22", "#1d2730", "#3fb8b0"],
  ["forest", "Orman", "#174b37", "#edf4ee", "#237b55"],
  ["ocean", "Okyanus", "#174763", "#edf3f7", "#176f8a"],
  ["warm", "Sıcak", "#594733", "#f5f1e9", "#795b32"],
  ["slate", "Arduvaz", "#2f3b48", "#eef1f4", "#4d6a86"],
  ["plum", "Mürdüm", "#43294f", "#f3eff5", "#7a4b8c"],
  ["rose", "Gül kurusu", "#5a2f3a", "#f7eff0", "#a24c5f"],
  ["graphite", "Grafit", "#262a2e", "#f0f0ef", "#56606a"],
  ["terracotta", "Kiremit", "#6a3524", "#f8f0ea", "#b0532f"],
  ["mint", "Nane", "#14504c", "#eef7f4", "#1f8a76"],
  ["midnight", "Gece mavisi", "#14213d", "#eef1f8", "#3a5fb0"],
];
const APPEARANCE_ACCENTS = [
  ["", "Tema varsayılanı", ""],
  ["teal", "Turkuaz", "#007d78"],
  ["blue", "Mavi", "#1f6fb2"],
  ["green", "Yeşil", "#2f7d32"],
  ["orange", "Turuncu", "#b4581a"],
  ["purple", "Mor", "#6d4bb0"],
  ["red", "Kırmızı", "#b3373a"],
];

function applyAppearance(prefs) {
  const themes = APPEARANCE_THEMES.map(([value]) => value);
  const theme = themes.includes(prefs.theme) ? prefs.theme : "light";
  document.documentElement.dataset.theme = theme;
  const accents = APPEARANCE_ACCENTS.map(([value]) => value).filter(Boolean);
  if (accents.includes(prefs.accent)) document.documentElement.dataset.accent = prefs.accent;
  else delete document.documentElement.dataset.accent;
  document.documentElement.classList.toggle("theme-dark", theme === "dark");
  document.documentElement.classList.toggle("contrast-high", prefs.palette === "contrast");
  document.documentElement.classList.toggle("palette-cb", prefs.palette === "colorblind");
  if (prefs.size && prefs.size !== "normal") document.documentElement.dataset.fontSize = prefs.size;
  else delete document.documentElement.dataset.fontSize;
  localStorage.setItem("vp-appearance", JSON.stringify(prefs));
}

function openAppearance() {
  const prefs = appearancePrefs();
  const dialog = extrasDialog("appearance-dialog", "Görünüm", "Tercih bu bilgisayarda saklanır.");
  const currentTheme = APPEARANCE_THEMES.some(([value]) => value === prefs.theme) ? prefs.theme : "light";
  const themeOptions = APPEARANCE_THEMES.map(([value, label, bar, canvas, accent]) => `<label class="theme-swatch">
      <input type="radio" name="vp-theme" value="${value}"${currentTheme === value ? " checked" : ""}>
      <span class="theme-swatch-preview" aria-hidden="true" style="--sw-bar:${bar};--sw-canvas:${canvas};--sw-accent:${accent}"><i></i><b></b></span>
      <span>${escapeHtml(label)}</span></label>`).join("");
  const accentOptions = APPEARANCE_ACCENTS.map(([value, label, color]) => `<label class="accent-swatch">
      <input type="radio" name="vp-accent" value="${value}"${(prefs.accent || "") === value ? " checked" : ""}>
      <span class="accent-dot" aria-hidden="true"${color ? ` style="background:${color}"` : ""}></span>${escapeHtml(label)}</label>`).join("");
  dialog.querySelector(".extras-body").innerHTML = `<div class="extras-form">
    <fieldset class="extras-choice theme-grid"><legend>Tema</legend>${themeOptions}</fieldset>
    <fieldset class="extras-choice accent-grid"><legend>Vurgu rengi</legend>${accentOptions}</fieldset>
    <fieldset class="extras-choice"><legend>Yazı boyutu</legend>
      <label><input type="radio" name="vp-size" value="normal"${!prefs.size || prefs.size === "normal" ? " checked" : ""}> Normal</label>
      <label><input type="radio" name="vp-size" value="large"${prefs.size === "large" ? " checked" : ""}> Büyük</label>
      <label><input type="radio" name="vp-size" value="xlarge"${prefs.size === "xlarge" ? " checked" : ""}> Çok büyük</label>
    </fieldset>
    <fieldset class="extras-choice"><legend>Renk ve okunabilirlik</legend>
      <label><input type="radio" name="vp-palette" value="normal"${!prefs.palette || prefs.palette === "normal" ? " checked" : ""}> Standart</label>
      <label><input type="radio" name="vp-palette" value="contrast"${prefs.palette === "contrast" ? " checked" : ""}> Yüksek karşıtlık</label>
      <label><input type="radio" name="vp-palette" value="colorblind"${prefs.palette === "colorblind" ? " checked" : ""}> Renk körlüğüne uygun</label>
    </fieldset>
    <p class="dialog-help">Excel çıktıları bu ayardan etkilenmez; yalnız ekran görünümü değişir.</p></div>`;
  dialog.addEventListener("change", () => applyAppearance({
    theme: dialog.querySelector('input[name="vp-theme"]:checked')?.value || "light",
    accent: dialog.querySelector('input[name="vp-accent"]:checked')?.value || "",
    size: dialog.querySelector('input[name="vp-size"]:checked')?.value || "normal",
    palette: dialog.querySelector('input[name="vp-palette"]:checked')?.value || "normal",
  }));
  showExtras(dialog);
}

/* ---------- Kısayollar (F1) ---------- */

const EXTRAS_SHORTCUTS = [
  ["F1", "Bu kısayol listesini açar"],
  ["Ctrl + K", "Hızlı personel arama"],
  ["Ctrl + S", "Açık tabloyu kaydeder"],
  ["Ctrl + Z / Ctrl + Y", "Geri al / yinele"],
  ["Ctrl + C / Ctrl + V", "Seçili vardiya hücresini kopyala / yapıştır"],
  ["Delete", "Seçili vardiya hücresini temizler"],
  ["Enter / Boşluk", "Seçili hücrenin hızlı giriş menüsünü açar"],
  ["Ok tuşları", "Hücreler arasında dolaşır"],
  ["Çift tıklama", "Hücrenin hızlı giriş menüsünü açar (not da buradan eklenir)"],
  ["Esc", "Açık pencereyi kapatır"],
];

function openShortcuts() {
  const dialog = extrasDialog("shortcuts-dialog", "Klavye kısayolları");
  dialog.querySelector(".extras-body").innerHTML = `<table class="archive-table extras-table"><tbody>${EXTRAS_SHORTCUTS
    .map(([key, text]) => `<tr><th scope="row"><kbd>${escapeHtml(key)}</kbd></th><td>${escapeHtml(text)}</td></tr>`).join("")}</tbody></table>`;
  showExtras(dialog);
}

/* ---------- Bugünün özeti ---------- */

async function openDashboard() {
  const today = dateKey(new Date());
  const data = await request(`/api/dashboard?date=${today}`);
  const dialog = extrasDialog("dashboard-dialog", "Bugünün özeti",
    `${dateLabel(new Date(`${data.date}T12:00:00`), "long")} · ${extrasMonthLabel(data.month)}`);
  const list = (items, render, empty) => items.length
    ? `<ul class="extras-list">${items.map(render).join("")}</ul>` : `<p class="archive-empty">${empty}</p>`;
  const counts = data.check.counts || {};
  const backup = data.last_backup ? new Date(data.last_backup.created_at).toLocaleString("tr-TR") : "Yedek yok";
  const category = { official: "Resmî tatil", religious: "Dinî bayram", weekend: "Hafta sonu", weekday: "İş günü" };
  dialog.querySelector(".extras-body").innerHTML = `<div class="dashboard-grid">
    <section class="dashboard-card"><h3>Bugün çalışan <b>${data.working.length}</b></h3>${list(data.working,
      (item) => `<li><span>${escapeHtml(item.name)}</span><small>${escapeHtml(item.start)}–${escapeHtml(item.end)} · ${escapeHtml(item.section)}</small></li>`,
      "Bugün için planlanmış vardiya yok.")}</section>
    <section class="dashboard-card"><h3>İzinli / çalışmayan <b>${data.off.length}</b></h3>${list(data.off,
      (item) => `<li><span>${escapeHtml(item.name)}</span><small>${escapeHtml(item.status || "")}</small></li>`,
      "Bugün izinli kimse yok.")}</section>
    <section class="dashboard-card"><h3>Yarın çalışacak <b>${data.tomorrow.length}</b></h3>${list(data.tomorrow,
      (item) => `<li><span>${escapeHtml(item.name)}</span><small>${escapeHtml(item.start)}–${escapeHtml(item.end)}</small></li>`,
      "Yarın için planlanmış vardiya yok.")}</section>
    <section class="dashboard-card"><h3>Dönem uyarıları <b>${(counts.error || 0) + (counts.warning || 0)}</b></h3>${list(data.check.items,
      (item) => `<li class="severity-${escapeHtml(item.severity)}"><span>${escapeHtml(item.name)}</span><small>${escapeHtml(item.message)}</small></li>`,
      "Hata veya uyarı yok.")}</section>
    <section class="dashboard-card dashboard-facts"><h3>Durum</h3><dl>
      <dt>Gün türü</dt><dd>${escapeHtml(category[data.category] || data.category)}</dd>
      <dt>Etkin personel</dt><dd>${data.people.active}</dd>
      <dt>Bu ay çizelgede</dt><dd>${data.people.planned}</dd>
      <dt>Dönem</dt><dd>${data.locked ? "Kilitli" : "Açık"}</dd>
      <dt>Son yedek</dt><dd>${escapeHtml(backup)}</dd>
      <dt>Yedek kopyası</dt><dd>${data.backup_mirror ? escapeHtml(data.backup_mirror) : "Kapalı"}</dd>
    </dl></section>
    <section class="dashboard-card"><h3>Yıllık izni azalan</h3>${list(data.low_leave,
      (item) => `<li><span>${escapeHtml(item.name)}</span><small>${item.remaining} gün kaldı</small></li>`,
      "İzin bakiyesi 2 günün altına düşen yok.")}</section>
  </div>`;
  showExtras(dialog);
}

async function openPersonnelHome(employeeId = "") {
  const month = extrasActiveMonth();
  const dialog = extrasDialog("personnel-home-dialog", "Personel ana sayfası",
    "Personel özeti, yaklaşan vardiyalar, izinler ve hızlı işlemler.");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = '<p role="status">Personel bilgileri yükleniyor…</p>';
  showExtras(dialog);
  const peopleResult = await request("/api/personnel-overview");
  const people = peopleResult.people || [];
  if (!people.length) {
    body.innerHTML = '<p class="archive-empty">Önce personel ekleyin.</p>';
    return;
  }
  const preferred = Number(employeeId) || people.find((person) => person.active)?.id || people[0].id;
  body.innerHTML = `<div class="personnel-home-controls">
      <label>Personel<select data-personnel-home-person>${people.map((person) =>
        `<option value="${person.id}"${person.id === preferred ? " selected" : ""}>${escapeHtml(person.name)}${person.active ? "" : " · pasif"}</option>`).join("")}</select></label>
      <label>Dönem<input type="month" data-personnel-home-month value="${escapeHtml(month)}"></label>
    </div><div data-personnel-home-content role="status" aria-live="polite"></div>`;
  const content = body.querySelector("[data-personnel-home-content]");
  const render = async () => {
    const id = Number(body.querySelector("[data-personnel-home-person]").value);
    const selectedMonth = body.querySelector("[data-personnel-home-month]").value;
    if (!id || !/^\d{4}-\d{2}$/.test(selectedMonth)) throw new Error("Personel ve geçerli dönem seçin.");
    const [schedule, leaveResult, assetResult] = await Promise.all([
      request(`/api/schedule?month=${encodeURIComponent(selectedMonth)}`),
      request(`/api/linked-leave/requests?month=${encodeURIComponent(selectedMonth)}`),
      request(`/api/personnel-assets?employee_id=${encodeURIComponent(id)}`),
    ]);
    const person = people.find((item) => item.id === id);
    if (!person) throw new Error("Personel bulunamadı.");
    const today = dateKey(new Date());
    const shifts = schedule.entries
      .filter((entry) => entry.employee_id === id)
      .flatMap((entry) => Object.entries(entry.days || {})
        .filter(([, item]) => item?.kind === "work")
        .map(([date, item]) => ({ date, item, section: entry.section })))
      .sort((left, right) => left.date.localeCompare(right.date));
    const nextShifts = shifts.filter((shift) => shift.date >= today).slice(0, 10);
    const displayShifts = nextShifts.length ? nextShifts : shifts.slice(-10);
    const leaves = (leaveResult.requests || []).filter((item) => item.employee_id === id);
    const leaveKinds = { weekly_off: "Hafta tatili", annual_leave: "Yıllık izin", sick_leave: "Raporlu", unpaid_leave: "Ücretsiz izin",
      marriage_leave: "Evlilik izni", quarantine: "Karantina", half_day: "Yarım gün", absent: "İşe gelmedi" };
    const leaveStatuses = { pending: "Bekliyor", approved: "Onaylı", cancelled: "İptal" };
    const missing = person.card_missing || [];
    const requiredCount = Number(person.card_required_count) || 0;
    const completeness = requiredCount
      ? Math.round(((requiredCount - missing.length) / requiredCount) * 100) : 100;
    const sectionDays = new Map();
    for (const entry of schedule.entries.filter((item) => item.employee_id === id && item.section)) {
      const worked = Object.values(entry.days || {}).filter((item) => item?.kind === "work").length;
      if (worked) sectionDays.set(entry.section, (sectionDays.get(entry.section) || 0) + worked);
    }
    const [suggestedSection, suggestedDays] = [...sectionDays.entries()].sort((left, right) => right[1] - left[1])[0] || [];
    const sectionHint = !person.home_section && suggestedSection
      ? `<p class="personnel-home-suggestion">Bu dönem en çok <b>${escapeHtml(suggestedSection)}</b> bölümünde çalıştı (${suggestedDays} gün).
          <button class="button button-light" type="button" data-personnel-home-action="section" data-person="${id}" data-section="${escapeHtml(suggestedSection)}">Bölüm olarak kaydet</button></p>`
      : "";
    const shiftRows = displayShifts.length ? displayShifts.map(({ date, item, section }) =>
      `<tr><td>${escapeHtml(dateLabel(new Date(`${date}T12:00:00`)))}</td>
        <td>${escapeHtml(item.start || "")}–${escapeHtml(item.end || "")}</td>
        <td>${escapeHtml(section || "")}</td>
        <td><button class="button button-light" type="button" data-personnel-home-document="${id}" data-date="${escapeHtml(date)}">Tutanak / belge</button></td></tr>`).join("")
      : `<tr><td colspan="4" class="archive-empty">Bu dönem için kayıtlı çalışma vardiyası yok.</td></tr>`;
    const leaveRows = leaves.length ? leaves.map((item) =>
      `<tr><td>${escapeHtml(item.start)}${item.end !== item.start ? ` – ${escapeHtml(item.end)}` : ""}</td>
        <td>${escapeHtml(leaveKinds[item.kind] || item.kind)}</td><td>${escapeHtml(leaveStatuses[item.status] || item.status)}</td></tr>`).join("")
      : `<tr><td colspan="3" class="archive-empty">Bu dönemde izin kaydı yok.</td></tr>`;
    const assetCategories = { key: "Anahtar", card: "Kart", clothing: "Kıyafet", equipment: "Ekipman", other: "Diğer" };
    const assets = assetResult.assets || [];
    const assetRows = assets.length ? assets.map((asset) =>
      `<tr><td>${escapeHtml(assetCategories[asset.category] || asset.category)} · ${escapeHtml(asset.item)}${asset.note ? `<small>${escapeHtml(asset.note)}</small>` : ""}</td>
        <td>${escapeHtml(asset.issued_at.slice(0, 10))} · ${escapeHtml(asset.issued_by)}</td>
        <td>${asset.returned_at ? `${escapeHtml(asset.returned_at.slice(0, 10))} · ${escapeHtml(asset.returned_by)}` :
          `<span class="asset-open-label">İade bekliyor</span> <button class="button button-light" type="button" data-personnel-asset-return="${asset.id}">İade al</button>`}</td></tr>`).join("")
      : `<tr><td colspan="3" class="archive-empty">Kayıtlı zimmet yok.</td></tr>`;
    content.innerHTML = `<section class="personnel-home-summary">
        <h3>${escapeHtml(person.name)}${person.title ? ` · ${escapeHtml(person.title)}` : ""}</h3>
        <p>${escapeHtml(person.home_section || "Bölüm belirtilmemiş")} · ${person.active ? "Aktif" : "Pasif"}${person.phone ? ` · ${escapeHtml(person.phone)}` : ""}</p>
        ${person.notes ? `<p>${escapeHtml(person.notes)}</p>` : ""}
        <div class="personnel-card-meter" title="${missing.length ? `Eksik: ${escapeHtml(missing.join(", "))}` : "Kart bilgileri tamam"}">
          <span>Kart doluluğu <b>%${completeness}</b></span>
          <meter min="0" max="100" low="60" high="99" optimum="100" value="${completeness}" aria-label="Personel kartı doluluğu"></meter>
          ${missing.length ? `<small>Eksik: ${escapeHtml(missing.join(", "))}</small>` : "<small>Tüm temel bilgiler dolu.</small>"}
        </div>
        ${sectionHint}
        <div class="dialog-actions">
          <button class="button button-primary" type="button" data-personnel-home-action="leave" data-person="${id}">İzin oluştur</button>
          <button class="button button-outline" type="button" data-personnel-home-action="document" data-person="${id}">Tutanak / belge hazırla</button>
          <button class="button button-light" type="button" data-personnel-home-action="card" data-person="${id}">${missing.length ? "Kartı tamamla" : "Personel kartı"}</button>
        </div>
      </section>
      <h3>Yaklaşan vardiyalar</h3>
      <div class="controls-table-wrap"><table class="archive-table extras-table"><thead><tr><th>Tarih</th><th>Saat</th><th>Bölüm</th><th>Hızlı işlem</th></tr></thead><tbody>${shiftRows}</tbody></table></div>
      <h3>İzin ve devamsızlık kayıtları</h3>
      <div class="controls-table-wrap"><table class="archive-table extras-table"><thead><tr><th>Tarih</th><th>Tür</th><th>Durum</th></tr></thead><tbody>${leaveRows}</tbody></table></div>
      <h3>Zimmetler</h3>
      <div class="controls-table-wrap"><table class="archive-table extras-table"><thead><tr><th>Tür ve eşya</th><th>Teslim</th><th>İade</th></tr></thead><tbody>${assetRows}</tbody></table></div>
      ${person.active ? `<form class="personnel-asset-form" data-personnel-asset-form>
        <label>Tür<select name="category"><option value="key">Anahtar</option><option value="card">Kart</option><option value="clothing">Kıyafet</option><option value="equipment">Ekipman</option><option value="other">Diğer</option></select></label>
        <label>Eşya<input name="item" maxlength="80" required></label>
        <label>Açıklama<input name="note" maxlength="250"></label>
        <button class="button button-primary" type="submit">Teslim kaydet</button>
      </form>` : `<p class="archive-empty">Pasif personel için yeni zimmet verilemez; geçmiş kayıtlar korunur.</p>`}`;
  };
  const reportError = (error) => {
    content.textContent = error.message || "Personel bilgileri yüklenemedi.";
    toast(error.message || "Personel bilgileri yüklenemedi.", true);
  };
  body.querySelector("[data-personnel-home-person]").addEventListener("change", () => render().catch(reportError));
  body.querySelector("[data-personnel-home-month]").addEventListener("change", () => render().catch(reportError));
  body.onclick = async (event) => {
    const button = event.target.closest("[data-personnel-home-action], [data-personnel-home-document], [data-personnel-asset-return]");
    if (!button) return;
    const selectedId = Number(button.dataset.person || button.dataset.personnelHomeDocument);
    if (button.dataset.personnelAssetReturn) {
      await extrasRun(button, async () => {
        await request("/api/personnel-assets", {
          method: "POST",
          body: JSON.stringify({ action: "return", asset_id: Number(button.dataset.personnelAssetReturn) }),
        });
        toast("Zimmet iadesi kaydedildi.");
        await render();
      });
      return;
    }
    const back = () => openPersonnelHome(selectedId).catch((error) => toast(error.message, true));
    try {
      if (button.dataset.personnelHomeAction === "section") {
        await extrasRun(button, async () => {
          await request("/api/employee-profile", {
            method: "PUT", body: JSON.stringify({ employee_id: selectedId, profile: { home_section: button.dataset.section } }),
          });
          const person = people.find((item) => item.id === selectedId);
          if (person) {
            person.home_section = button.dataset.section;
            person.card_missing = (person.card_missing || []).filter((label) => label !== "Bölüm");
          }
          toast(`Bölüm ${button.dataset.section} olarak kaydedildi.`);
          await render();
        });
        return;
      }
      dialog.close();
      if (button.dataset.personnelHomeAction === "leave") {
        await window.linkedLeave.open({ employee_id: selectedId, onBack: back, backLabel: "← Personel ana sayfasına dön" });
      } else if (button.dataset.personnelHomeAction === "card") {
        await openEmployeeCard(selectedId);
      } else {
        const date = button.dataset.date || dateKey(new Date());
        await window.documentWorkflows.openSelection([{ employee_id: selectedId, date, code: "" }],
          { editable: true, onBack: back, backLabel: "← Personel ana sayfasına dön" });
      }
    } catch (error) {
      toast(error.message || "İşlem açılamadı.", true);
    }
  };
  body.onsubmit = async (event) => {
    const form = event.target.closest("[data-personnel-asset-form]");
    if (!form) return;
    event.preventDefault();
    const submit = form.querySelector('[type="submit"]');
    await extrasRun(submit, async () => {
      const fields = Object.fromEntries(new FormData(form));
      await request("/api/personnel-assets", {
        method: "POST",
        body: JSON.stringify({ action: "issue", employee_id: Number(body.querySelector("[data-personnel-home-person]").value),
          category: fields.category, item: fields.item, note: fields.note }),
      });
      toast("Zimmet teslimi kaydedildi.");
      await render();
    });
  };
  await render().catch(reportError);
}

window.personnelHome = { open: openPersonnelHome };

/* ---------- Hızlı personel arama (Ctrl+K) ---------- */

async function goToEmployee(name, month) {
  if (state.view !== "schedule") await switchView("schedule");
  if (state.view !== "schedule") return;
  if (month && month !== state.scheduleMonth) {
    const input = el("#schedule-month");
    input.value = month;
    input.dispatchEvent(new Event("change"));
    for (let tries = 0; tries < 40 && state.scheduleMonth !== month; tries += 1) await new Promise((r) => setTimeout(r, 50));
    await new Promise((r) => setTimeout(r, 200));
  }
  const search = el("#schedule-search");
  search.value = name;
  search.dispatchEvent(new Event("input"));
  toast(`${name} için çizelge süzüldü. Filtreyi “Filtreleri temizle” ile kaldırabilirsiniz.`);
}

function openSearch() {
  const dialog = extrasDialog("search-dialog", "Personel ara", "Ad yazın; kişinin vardiya ve puantajına gidin.");
  dialog.querySelector(".extras-body").innerHTML = `<label class="extras-search"><span>Ad</span>
    <input type="search" id="extras-search-input" maxlength="60" placeholder="ör. Ayşe" autocomplete="off"></label>
    <div id="extras-search-results" aria-live="polite"></div>`;
  const input = dialog.querySelector("#extras-search-input");
  const results = dialog.querySelector("#extras-search-results");
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const text = input.value.trim();
      if (!text) { results.innerHTML = ""; return; }
      try {
        const data = await request(`/api/search?q=${encodeURIComponent(text)}`);
        results.innerHTML = data.results.length ? data.results.map((person) => `<article class="search-result">
          <header><strong>${escapeHtml(person.name)}</strong>${person.active ? "" : " <small>pasif</small>"}</header>
          <div class="search-actions">
            <button class="button button-primary" type="button" data-go-schedule="${escapeHtml(person.name)}">Bu ayın vardiyası</button>
            <button class="button button-light" type="button" data-go-attendance="${escapeHtml(person.name)}">Puantaj</button>
            <button class="button button-light" type="button" data-go-card="${person.id}">Personel kartı</button>
          </div>
          ${person.months.length ? `<p class="search-months">Kayıtlı aylar: ${person.months.map((month) =>
            `<button class="link-button" type="button" data-go-schedule="${escapeHtml(person.name)}" data-month="${escapeHtml(month)}">${escapeHtml(extrasMonthLabel(month))}</button>`).join(" ")}</p>` : ""}
        </article>`).join("") : '<p class="archive-empty">Eşleşen personel yok.</p>';
      } catch (error) { results.innerHTML = `<p class="reports-warning">${escapeHtml(error.message)}</p>`; }
    }, 180);
  });
  results.addEventListener("click", async (event) => {
    const schedule = event.target.closest("[data-go-schedule]");
    const attendance = event.target.closest("[data-go-attendance]");
    const card = event.target.closest("[data-go-card]");
    if (schedule) {
      dialog.close();
      await goToEmployee(schedule.dataset.goSchedule, schedule.dataset.month);
    } else if (attendance) {
      dialog.close();
      if (state.view !== "attendance") await switchView("attendance");
      const search = el("#attendance-search");
      if (search) {
        search.value = attendance.dataset.goAttendance;
        search.dispatchEvent(new Event("input"));
      }
    } else if (card) {
      dialog.close();
      openEmployeeCard(card.dataset.goCard).catch((error) => toast(error.message, true));
    }
  });
  showExtras(dialog);
  input.focus();
}

/* ---------- Personel kartı ---------- */

async function openEmployeeCard(employeeId = "") {
  const dialog = extrasDialog("employee-card-dialog", "Personel kartı", "İşe giriş tarihi yıllık izin hakkını hesaplar.");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = `<label class="extras-search"><span>Personel</span><select id="card-person"><option value="">Seçin</option>${extrasPeopleOptions(employeeId)}</select></label>
    <div id="card-content"></div>`;
  const select = body.querySelector("#card-person");
  const content = body.querySelector("#card-content");
  const load = async () => {
    if (!select.value) { content.innerHTML = '<p class="archive-empty">Kartını görmek için personel seçin.</p>'; return; }
    const data = await request(`/api/employee-profile?employee_id=${select.value}`);
    const profile = data.profile;
    const balance = data.balance;
    const entitlement = balance?.entitlement;
    content.innerHTML = `<form id="card-form" class="forms-fields">
      <label>Pozisyon / görev <input name="title" maxlength="80" value="${escapeHtml(profile.title)}"></label>
      <label>Telefon <input name="phone" maxlength="80" inputmode="tel" value="${escapeHtml(profile.phone)}"></label>
      <label>İşe giriş tarihi <input name="hire_date" type="date" value="${escapeHtml(profile.hire_date)}"></label>
      <label>Doğum tarihi <input name="birth_date" type="date" value="${escapeHtml(profile.birth_date)}"></label>
      <label>Sicil no (bordro) <input name="payroll_code" maxlength="80" value="${escapeHtml(profile.payroll_code || "")}"></label>
      <label>İşten çıkış tarihi <input name="exit_date" type="date" value="${escapeHtml(profile.exit_date || "")}"></label>
      <label>Aylık brüt ücret (₺) <input name="monthly_wage" type="number" min="0" step="0.01" value="${profile.monthly_wage || ""}"></label>
      <label>Asıl bölüm <input name="home_section" maxlength="60" list="card-section-list" value="${escapeHtml(profile.home_section || "")}"></label>
      <datalist id="card-section-list">${[...new Set((state.schedule?.entries || []).map((entry) => entry.section).filter(Boolean))].map((section) => `<option value="${escapeHtml(section)}">`).join("")}</datalist>
      <label>Yetkinlikler <input name="skills" maxlength="400" placeholder="Kasa, Forklift, İlk yardım" value="${escapeHtml((profile.skills || []).join(", "))}"></label>
      <label>Deneyimli olduğu görevler <input name="experienced_skills" maxlength="400" placeholder="Yalnız doğrulanmış yetkinlikler" value="${escapeHtml((profile.experienced_skills || []).join(", "))}"></label>
      <label>Eşlik etmesi gereken görevler <input name="training_skills" maxlength="400" placeholder="Görev bazında eğitim ihtiyacı" value="${escapeHtml((profile.training_skills || []).join(", "))}"></label>
      <fieldset class="forms-wide special-status"><legend>Bu görev için gerekli kart bilgileri</legend>
        <p class="dialog-help">Yalnız iş için gerekli bölüm, sicil, yetkinlik ve belge alanları kontrol edilir; telefon, doğum tarihi veya ücret zorunlu yapılmaz.</p>
        ${[
          ["home_section", "Bölüm"], ["payroll_code", "Sicil no"],
          ["skills", "Yetkinlik"], ["experienced_skills", "Deneyimli görev"],
          ["training_skills", "Eğitim ihtiyacı"], ["documents", "Belge"],
        ].map(([value, label]) => `<label><input type="checkbox" name="duty_required_field" value="${value}"
          ${(profile.duty_required_fields || []).includes(value) ? "checked" : ""}> ${label}</label>`).join("")}
      </fieldset>
      <label class="forms-wide">Not <input name="notes" maxlength="500" value="${escapeHtml(profile.notes)}"></label>
      <fieldset class="forms-wide special-status person-look"><legend>Fotoğraf ve renk etiketi (yalnız uygulama ekranında görünür)</legend>
        <div class="person-photo-edit">
          <span class="person-avatar person-avatar-large" id="card-photo-preview" style="${profile.color_tag ? `--tag:${escapeHtml(profile.color_tag)}` : ""}">${profile.photo ? `<img src="${escapeHtml(profile.photo)}" alt="">` : escapeHtml(personInitials(select.selectedOptions[0]?.textContent || ""))}</span>
          <label class="button button-light">Fotoğraf seç <input id="card-photo-file" type="file" accept="image/jpeg,image/png,image/webp" hidden></label>
          <button class="button button-light" type="button" id="card-photo-clear" ${profile.photo ? "" : "disabled"}>Fotoğrafı kaldır</button>
        </div>
        <div class="person-color-tags" role="radiogroup" aria-label="Renk etiketi">
          ${PERSON_TAG_COLORS.map(([color, label]) => `<label class="person-color-tag" title="${label}"><input type="radio" name="color_tag" value="${color}" ${(profile.color_tag || "") === color ? "checked" : ""}><span style="${color ? `background:${color}` : ""}">${color ? "" : "Yok"}</span></label>`).join("")}
        </div>
      </fieldset>
      <fieldset class="forms-wide special-status staff-type-fieldset"><legend>Çalışma türü (puantaj)</legend>
        <label><input type="radio" name="staff_type" value="shift" ${profile.staff_type !== "nonshift" ? "checked" : ""}> Vardiyalı (puantaja vardiya listesinden gelir)</label>
        <label><input type="radio" name="staff_type" value="nonshift" ${profile.staff_type === "nonshift" ? "checked" : ""}> Vardiya dışı (müdür, şoför vb. — sabit haftalık düzenle puantaja eklenir)</label>
        <div class="fixed-pattern" ${profile.staff_type === "nonshift" ? "" : "hidden"}>
          ${PLANNING_CARD_DAYS.map((day, index) => {
            const code = (profile.fixed_pattern || ["X", "X", "X", "X", "X", "X", "H"])[index] || "";
            return `<label>${day} <select name="fixed_day" aria-label="${day} sabit kod">${[["X", "X · çalışır"], ["H", "H · hafta tatili"], ["", "Boş"]].map(([value, label]) =>
              `<option value="${value}" ${code === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>`;
          }).join("")}
          <label>Mesai başlangıcı <input name="fixed_start" type="time" value="${escapeHtml(profile.fixed_start || "08:00")}"></label>
          <label>Mesai bitişi <input name="fixed_end" type="time" value="${escapeHtml(profile.fixed_end || "16:00")}"></label>
          <fieldset class="forms-wide special-status">
            <legend>Tek haftalık sabit düzen istisnası</legend>
            <label><input type="checkbox" name="fixed_exception_enabled" ${(profile.fixed_pattern_exceptions || []).length ? "checked" : ""}>
              Bu hafta ana düzenden farklı olsun</label>
            <label>Hafta başlangıcı (pazartesi)
              <input name="fixed_exception_start" type="date" value="${escapeHtml(profile.fixed_pattern_exceptions?.[0]?.start_date || "")}"></label>
            ${PLANNING_CARD_DAYS.map((day, index) => {
              const exceptionCode = profile.fixed_pattern_exceptions?.[0]?.pattern?.[index] ?? (profile.fixed_pattern || ["X", "X", "X", "X", "X", "X", "H"])[index] ?? "";
              return `<label>${day} istisna kodu <select name="fixed_exception_day" aria-label="${day} haftalık istisna kodu">
                ${[["X", "X · çalışır"], ["H", "H · hafta tatili"], ["", "Boş"]].map(([value, label]) =>
                  `<option value="${value}" ${exceptionCode === value ? "selected" : ""}>${label}</option>`).join("")}
              </select></label>`;
            }).join("")}
            <button class="button button-outline" type="button" data-fixed-exception-preview>İstisna haftasını önizle</button>
            <p class="dialog-help" data-fixed-exception-result aria-live="polite"></p>
          </fieldset>
        </div>
      </fieldset>
      <fieldset class="forms-wide special-status"><legend>Kişiye özel fazla mesai kuralı</legend>
        <label>Günlük normal saat <input name="daily_limit_hours" type="number" min="0" max="24" step="0.25" placeholder="Genel kural" value="${profile.daily_limit_hours ? escapeHtml(String(profile.daily_limit_hours)) : ""}"></label>
        <label><input type="checkbox" name="break_exempt" ${profile.break_exempt ? "checked" : ""}> Mola düşülmesin</label>
        <small class="forms-wide">Boş bırakılırsa genel kural (Puantaj › Aylık özet › Fazla mesai kuralı) geçerlidir. Örn. yarı zamanlı için 4 saat.</small>
      </fieldset>
      <fieldset class="forms-wide special-status"><legend>Tercihler (aykırı vardiya dönem kontrolünde uyarılır)</legend>
        ${PLANNING_CARD_DAYS.map((day, index) => `<label><input type="checkbox" name="pref_day" value="${index}" ${(profile.preferences?.unavailable_weekdays || []).includes(index) ? "checked" : ""}> ${day} çalışamaz</label>`).join("")}
        <label><input type="checkbox" name="pref_no_night" ${profile.preferences?.no_night ? "checked" : ""}> Gece çalışmak istemiyor</label>
        <label><input type="checkbox" name="pref_no_weekend" ${profile.preferences?.no_weekend ? "checked" : ""}> Hafta sonu çalışmak istemiyor</label>
        <label class="forms-wide">Tercih notu <input name="pref_note" maxlength="120" value="${escapeHtml(profile.preferences?.note || "")}"></label>
      </fieldset>
      <fieldset class="forms-wide special-status"><legend>Tarih aralığı uygunluğu</legend>
        <p class="dialog-help">Personelin çalışamayacağı günleri belirtin; bu tarihlerdeki vardiyalar dönem kontrolünde uyarılır.</p>
        <div id="availability-ranges"></div>
        <button class="button button-light" type="button" id="availability-range-add">Tarih aralığı ekle</button>
      </fieldset>
      <fieldset class="forms-wide special-status card-documents"><legend>Belgeler ve geçerlilik tarihleri (süresi yaklaşınca uyarılır)</legend>
        <div class="card-doc-list" id="card-doc-list"></div>
        <button class="button button-light" type="button" id="card-doc-add">+ Belge ekle</button>
        <small class="forms-wide">Örn. sağlık raporu, hijyen eğitimi, forklift ehliyeti. Süresi 30 gün içinde dolanlar Uyarı merkezinde görünür.</small>
      </fieldset>
      <fieldset class="forms-wide special-status"><legend>Özel durum (gece/mesai kısıtı kontrol edilir)</legend>
        ${Object.entries({ pregnant: "Hamile", nursing: "Emziren", under18: "18 yaş altı", disabled: "Engelli" }).map(([key, label]) =>
          `<label><input type="checkbox" name="special_status" value="${key}" ${(profile.special_status || []).includes(key) ? "checked" : ""}> ${label}</label>`).join("")}
      </fieldset>
      <div class="forms-wide dialog-footer"><button class="button button-primary" type="submit">Kartı kaydet</button></div>
    </form>
    <dl class="card-facts">
      <dt>Yasal yıllık izin hakkı</dt><dd>${entitlement ? `${entitlement.days} gün <small>(${escapeHtml(entitlement.reason)})</small>` : "İşe giriş tarihi girilmemiş"}</dd>
      <dt>Yıllık izin bakiyesi</dt><dd>${balance?.has_opening ? `${balance.annual_leave_remaining_days} gün (devir ${balance.annual_leave_opening_days}, kullanılan ${balance.annual_leave_used_days})` : "Devir bakiyesi girilmemiş"}</dd>
      <dt>Rapor / ücretsiz izin</dt><dd>${balance ? `${balance.sick_days} gün / ${balance.unpaid_leave_days} gün` : "—"}</dd>
      <dt>Fazla mesai (yıl)</dt><dd>${balance ? `${balance.overtime_tracked_hours} saat` : "—"}</dd>
      <dt>Vardiya döngüsü</dt><dd>${profile.rotation.length ? escapeHtml(profile.rotation.map((key) => quickEntryByKey(key)?.label || "?").join(" → ")) : "Tanımlı değil"}</dd>
      <dt>Kayıtlı aylar</dt><dd>${data.months.length ? data.months.map((month) => escapeHtml(extrasMonthLabel(month))).join(", ") : "—"}</dd>
    </dl>
    ${data.swaps.length ? `<h3 class="extras-subtitle">Son vardiya değişimleri</h3><ul class="extras-list">${data.swaps.map((swap) =>
      `<li><span>${escapeHtml(swap.date)} · ${escapeHtml(swap.first_name)} ↔ ${escapeHtml(swap.second_name)}</span><small>${escapeHtml(swap.reason || "")}</small></li>`).join("")}</ul>` : ""}
    <details class="card-history"${(data.history || []).length ? "" : " hidden"}><summary>Kart geçmişi (${(data.history || []).length} kayıt)</summary>
      <ul class="extras-list">${(data.history || []).map((item) =>
        `<li><span>${escapeHtml(new Date(item.occurred_at).toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" }))} · ${escapeHtml(item.summary)}</span><small>${escapeHtml(item.actor || "")}</small></li>`).join("")}</ul>
    </details>`;
    let availabilityRanges = (profile.preferences?.unavailable_ranges || []).map((item) => ({ ...item }));
    const availabilityList = content.querySelector("#availability-ranges");
    const paintAvailabilityRanges = () => {
      availabilityList.innerHTML = availabilityRanges.length ? availabilityRanges.map((item, index) => `
        <div class="availability-range">
          <label>Başlangıç<input type="date" data-availability="${index}" data-field="start" value="${escapeHtml(item.start)}"></label>
          <label>Bitiş<input type="date" data-availability="${index}" data-field="end" value="${escapeHtml(item.end)}"></label>
          <button class="button button-light danger-button" type="button" data-availability-remove="${index}">Sil</button>
        </div>`).join("") : '<p class="archive-empty">Tarih aralığı eklenmemiş.</p>';
    };
    paintAvailabilityRanges();
    availabilityList.addEventListener("input", (event) => {
      const field = event.target.dataset.field;
      if (field) availabilityRanges[Number(event.target.dataset.availability)][field] = event.target.value;
    });
    availabilityList.addEventListener("click", (event) => {
      const remove = event.target.closest("[data-availability-remove]");
      if (remove) {
        availabilityRanges.splice(Number(remove.dataset.availabilityRemove), 1);
        paintAvailabilityRanges();
      }
    });
    content.querySelector("#availability-range-add").addEventListener("click", () => {
      if (availabilityRanges.length >= 20) { toast("En fazla 20 tarih aralığı eklenebilir.", true); return; }
      const day = dateKey(new Date());
      availabilityRanges.push({ start: day, end: day });
      paintAvailabilityRanges();
    });
    let documents = (profile.documents || []).map((item) => ({
      ...item, id: item.id || crypto.randomUUID().replace(/-/g, ""),
      renewal_stage: item.renewal_stage || "", supersedes: item.supersedes || "",
    }));
    const docList = content.querySelector("#card-doc-list");
    const today = new Date().toISOString().slice(0, 10);
    const soon = new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10);
    const renewalLabels = {
      "": "Yenileme yok", requested: "İstendi", received: "Teslim alındı",
      checked: "Kontrol edildi", renewed: "Yenilendi",
    };
    const paintDocuments = () => {
      docList.innerHTML = documents.length ? documents.map((item, index) => {
        const status = !item.expires ? "" : item.expires < today ? "expired" : item.expires <= soon ? "soon" : "";
        return `<div class="card-doc" data-doc-status="${status}">
          <input data-doc="${index}" data-doc-field="name" maxlength="60" placeholder="Belge adı" aria-label="Belge adı" value="${escapeHtml(item.name || "")}">
          <input data-doc="${index}" data-doc-field="expires" type="date" aria-label="Geçerlilik bitişi" value="${escapeHtml(item.expires || "")}">
          <input data-doc="${index}" data-doc-field="note" maxlength="80" placeholder="Açıklama" aria-label="Belge açıklaması" value="${escapeHtml(item.note || "")}">
          <label>Yenileme aşaması<select data-doc="${index}" data-doc-field="renewal_stage" aria-label="${escapeHtml(item.name || "Belge")} yenileme aşaması">
            ${Object.entries(renewalLabels).map(([value, label]) => `<option value="${value}" ${item.renewal_stage === value ? "selected" : ""}>${label}</option>`).join("")}
          </select></label>
          <span class="card-doc-badge">${item.supersedes ? "Yeni sürüm" : status === "expired" ? "Süresi doldu" : status === "soon" ? "Yaklaşıyor" : ""}
            ${item.supersedes ? " · Eski belgeden ayrı kayıt" : ""}</span>
          ${!item.supersedes && item.renewal_stage !== "renewed"
            ? `<button class="button button-light" type="button" data-doc-renew="${index}" ${item.renewal_stage === "checked" ? "" : "disabled"}>Yeni belge sürümü ekle</button>`
            : ""}
          <button class="icon-button" type="button" data-doc-remove="${index}" aria-label="Belgeyi sil">×</button></div>`;
      }).join("") : '<p class="archive-empty">Belge eklenmemiş.</p>';
    };
    paintDocuments();
    docList.addEventListener("input", (event) => {
      const field = event.target.dataset.docField;
      if (field) documents[Number(event.target.dataset.doc)][field] = event.target.value;
    });
    docList.addEventListener("change", (event) => {
      if (event.target.dataset.docField === "renewal_stage") {
        documents[Number(event.target.dataset.doc)].renewal_stage = event.target.value;
        paintDocuments();
      } else if (event.target.dataset.docField === "expires") paintDocuments();
    });
    docList.addEventListener("click", (event) => {
      const remove = event.target.closest("[data-doc-remove]");
      if (remove) { documents.splice(Number(remove.dataset.docRemove), 1); paintDocuments(); }
      const renew = event.target.closest("[data-doc-renew]");
      if (renew) {
        const previous = documents[Number(renew.dataset.docRenew)];
        if (previous.renewal_stage !== "checked") {
          toast("Yeni belge eklemeden önce eski belgeyi kontrol edildi olarak kaydedin.", true);
          return;
        }
        if (documents.length >= 20) { toast("En fazla 20 belge eklenebilir.", true); return; }
        previous.renewal_stage = "renewed";
        documents.push({
          id: crypto.randomUUID().replace(/-/g, ""), name: previous.name, expires: "",
          note: "", renewal_stage: "", supersedes: previous.id,
        });
        paintDocuments();
      }
    });
    content.querySelector("#card-doc-add").addEventListener("click", () => {
      if (documents.length >= 20) { toast("En fazla 20 belge eklenebilir.", true); return; }
      documents.push({ id: crypto.randomUUID().replace(/-/g, ""), name: "", expires: "", note: "", renewal_stage: "", supersedes: "" });
      paintDocuments();
      docList.querySelector(`[data-doc="${documents.length - 1}"][data-doc-field="name"]`)?.focus();
    });
    let photo = profile.photo || "";
    const preview = content.querySelector("#card-photo-preview");
    const paintPreview = () => {
      preview.innerHTML = photo ? `<img src="${escapeHtml(photo)}" alt="">` : escapeHtml(personInitials(select.selectedOptions[0]?.textContent || ""));
      content.querySelector("#card-photo-clear").disabled = !photo;
    };
    content.querySelector("#card-photo-file").addEventListener("change", (event) => {
      const file = event.target.files[0];
      event.target.value = "";
      if (file) personPhotoDataUrl(file).then((value) => { photo = value; paintPreview(); }).catch((error) => toast(error.message, true));
    });
    content.querySelector("#card-photo-clear").addEventListener("click", () => { photo = ""; paintPreview(); });
    content.querySelectorAll('input[name="staff_type"]').forEach((input) => input.addEventListener("change", () => {
      content.querySelector(".fixed-pattern").hidden = content.querySelector('input[name="staff_type"]:checked')?.value !== "nonshift";
    }));
    content.querySelector("[data-fixed-exception-preview]").addEventListener("click", () => {
      const output = content.querySelector("[data-fixed-exception-result]");
      const startText = content.querySelector('[name="fixed_exception_start"]').value;
      const enabled = content.querySelector('[name="fixed_exception_enabled"]').checked;
      if (!enabled || !startText) {
        output.textContent = "İstisnayı seçin ve hafta başlangıcını girin; ana kural değişmeden kalır.";
        return;
      }
      const start = new Date(`${startText}T12:00:00Z`);
      if (Number.isNaN(start.getTime()) || start.getUTCDay() !== 1) {
        output.textContent = "İstisna haftası pazartesi başlamalıdır.";
        return;
      }
      const codes = [...content.querySelectorAll('[name="fixed_exception_day"]')].map((item) => item.value);
      const dates = codes.map((code, index) => {
        const day = new Date(start);
        day.setUTCDate(day.getUTCDate() + index);
        return `${day.toISOString().slice(0, 10)}: ${code || "boş"}`;
      });
      const returnDate = new Date(start);
      returnDate.setUTCDate(returnDate.getUTCDate() + 7);
      output.textContent = `${dates.join(" · ")}. ${returnDate.toISOString().slice(0, 10)} tarihinden itibaren ana haftalık düzene dönülür. Kaydetmeden önce bu yedi gün etkilenir.`;
    });
    content.querySelectorAll('input[name="color_tag"]').forEach((input) => input.addEventListener("change", () => {
      preview.style.setProperty("--tag", input.value || "transparent");
    }));
    content.querySelector("#card-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      extrasRun(event.currentTarget.querySelector("button[type='submit']"), async () => {
        await request("/api/employee-profile", {
          method: "PUT",
          body: JSON.stringify({
            employee_id: Number(select.value),
            profile: {
              ...Object.fromEntries([...form.entries()].filter(([key]) => !key.startsWith("pref_") &&
                !["fixed_day", "fixed_exception_enabled", "fixed_exception_start", "fixed_exception_day",
                  "daily_limit_hours", "break_exempt", "duty_required_field"].includes(key))),
              fixed_pattern: form.getAll("fixed_day"),
              fixed_pattern_exceptions: form.has("fixed_exception_enabled") && form.get("fixed_exception_start")
                ? [{ start_date: form.get("fixed_exception_start"), pattern: form.getAll("fixed_exception_day") }] : [],
              duty_required_fields: form.getAll("duty_required_field"),
              daily_limit_hours: Number(String(form.get("daily_limit_hours") || "0").replace(",", ".")) || 0,
              break_exempt: form.has("break_exempt"),
              color_tag: form.get("color_tag") || "", photo, documents,
              monthly_wage: Number(form.get("monthly_wage") || 0), special_status: form.getAll("special_status"), rotation: profile.rotation,
              preferences: {
                unavailable_weekdays: form.getAll("pref_day").map(Number), no_night: form.has("pref_no_night"),
                no_weekend: form.has("pref_no_weekend"), note: form.get("pref_note") || "",
                unavailable_ranges: availabilityRanges,
              },
            },
          }),
        });
        toast("Personel kartı kaydedildi.");
        if (typeof gridLoadTags === "function") gridLoadTags();
        await load();
      });
    });
  };
  select.addEventListener("change", () => load().catch((error) => toast(error.message, true)));
  await load();
  showExtras(dialog);
}

/* ---------- Kişiye özel vardiya döngüsü ---------- */

function scheduleRowsForPeople() {
  if (!state.schedule) return [];
  const seen = new Set();
  return state.schedule.entries.filter((entry) => {
    if (!entry.employee_id || seen.has(entry.employee_id)) return false;
    seen.add(entry.employee_id);
    return true;
  });
}

async function openRotation() {
  if (state.view !== "schedule") await switchView("schedule");
  const rows = scheduleRowsForPeople();
  if (!rows.length) throw new Error("Önce vardiya çizelgesine personel ekleyin.");
  const dialog = extrasDialog("rotation-dialog", "Vardiya döngüsü",
    `${extrasMonthLabel(state.scheduleMonth)} · ör. 2 gündüz, 2 gece, 2 izin`);
  const name = (id) => state.people.find((person) => person.id === id)?.name || "Personel";
  const monthStart = `${state.scheduleMonth}-01`;
  const lastDay = dateKey(monthDates(state.scheduleMonth).at(-1));
  dialog.querySelector(".extras-body").innerHTML = `<div class="forms-fields">
      <label>Personel veya ekip <select id="rotation-person" multiple size="4">${rows.map((row, index) => `<option value="${row.employee_id}"${index === 0 ? " selected" : ""}>${escapeHtml(name(row.employee_id))} · ${escapeHtml(row.section)}</option>`).join("")}</select></label>
      <label>Döngünün ilk günü <input id="rotation-anchor" type="date" value="${monthStart}" required></label>
      <label>İzin istisnası sonrası
        <select id="rotation-resume-mode">
          <option value="calendar">Takvim sırasından sürdür</option>
          <option value="continue">İzin günlerini atla, kaldığı adımdan sürdür</option>
        </select>
      </label>
      <label>Başlangıç günü <input id="rotation-start" type="date" min="${monthStart}" max="${lastDay}" value="${monthStart}"></label>
      <label>Bitiş günü <input id="rotation-end" type="date" min="${monthStart}" max="${lastDay}" value="${lastDay}"></label>
      <label class="extras-check"><input id="rotation-empty-only" type="checkbox" checked> Yalnız boş günleri doldur</label>
    </div>
    <h3 class="extras-subtitle">Döngü adımları <small>(sırayla tekrarlanır, en çok 42)</small></h3>
    <ol id="rotation-steps" class="rotation-steps"></ol>
    <div class="dialog-entry-list" id="rotation-palette">${allQuickEntries().map((entry) => quickEntryButton(entry, true)).join("")}</div>
    <div class="dialog-footer"><button class="button button-light" type="button" id="rotation-undo">Son adımı sil</button>
      <button class="button button-light" type="button" id="rotation-clear">Temizle</button>
      <button class="button button-outline" type="button" id="rotation-save">Döngüyü karta kaydet</button>
      <button class="button button-primary" type="button" id="rotation-apply">Önizle</button></div>`;
  let steps = [];
  const stepsNode = dialog.querySelector("#rotation-steps");
  const draw = () => {
    stepsNode.innerHTML = steps.length ? steps.map((key, index) => {
      const entry = quickEntryByKey(key);
      return `<li style="background:${entry?.color || "#fff"};color:${readableColor(entry?.color || "#ffffff")}">${index + 1}. ${escapeHtml(entry?.label || "Silinmiş giriş")}</li>`;
    }).join("") : '<li class="rotation-empty">Aşağıdaki girişlere tıklayarak döngüyü oluşturun.</li>';
  };
  const person = dialog.querySelector("#rotation-person");
  const anchor = dialog.querySelector("#rotation-anchor");
  const resumeMode = dialog.querySelector("#rotation-resume-mode");
  const saveButton = dialog.querySelector("#rotation-save");
  const applyButton = dialog.querySelector("#rotation-apply");
  const editingMonth = state.scheduleMonth;
  const updateScope = () => {
    const selected = new Set([...person.selectedOptions].map((item) => Number(item.value)));
    const sections = [...new Set(rows.filter((row) => selected.has(row.employee_id))
      .map((row) => row.section).filter(Boolean))];
    const scope = dialog.querySelector("[data-extras-scope]");
    scope.setAttribute("aria-label", "İşlem kapsamı");
    scope.textContent = [extrasMonthLabel(editingMonth), ...sections, `${selected.size} seçili personel`].join(" · ");
  };
  let loadVersion = 0;
  const loadSaved = async () => {
    const version = ++loadVersion;
    const chosen = person.value;
    saveButton.disabled = true;
    if (person.selectedOptions.length !== 1) {
      applyButton.disabled = false;
      return;
    }
    applyButton.disabled = true;
    try {
      const data = await request(`/api/employee-profile?employee_id=${chosen}`);
      if (version !== loadVersion) return;
      steps = data.profile.rotation;
      anchor.value = data.profile.rotation_anchor || monthStart;
      resumeMode.value = data.profile.rotation_resume_mode || "calendar";
      draw();
      saveButton.disabled = false;
    } finally {
      if (version === loadVersion) applyButton.disabled = false;
    }
  };
  person.addEventListener("change", () => {
    updateScope();
    loadSaved().catch((error) => toast(error.message, true));
  });
  updateScope();
  dialog.querySelector("#rotation-palette").addEventListener("click", (event) => {
    const button = event.target.closest("[data-cell-preset]");
    if (!button) return;
    if (steps.length >= 42) { toast("Döngü en fazla 42 adım olabilir.", true); return; }
    steps.push(button.dataset.cellPreset);
    draw();
  });
  dialog.querySelector("#rotation-undo").addEventListener("click", () => { steps.pop(); draw(); });
  dialog.querySelector("#rotation-clear").addEventListener("click", () => { steps = []; draw(); });
  dialog.querySelector("#rotation-save").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    if (person.selectedOptions.length !== 1 || !anchor.reportValidity()) return;
    await request("/api/employee-profile", {
      method: "PUT",
      body: JSON.stringify({ employee_id: Number(person.value), profile: {
        rotation: steps, rotation_anchor: anchor.value, rotation_resume_mode: resumeMode.value,
      } }),
    });
    toast("Döngü personel kartına kaydedildi.");
  }));
  applyButton.addEventListener("click", () => extrasRun(applyButton, async () => {
    if (!steps.length || steps.some((key) => !quickEntryByKey(key))) throw new Error("Döngü adımlarını seçin ve silinmiş girişleri kaldırın.");
    const start = dialog.querySelector("#rotation-start").value;
    const end = dialog.querySelector("#rotation-end").value;
    if (!anchor.value || !start || !end || start > end || start < monthStart || end > lastDay ||
        start < anchor.value || state.scheduleMonth !== editingMonth) throw new Error("Tarihleri kontrol edin; döngü ilk gününden önce uygulanamaz.");
    const selected = new Set([...person.selectedOptions].map((item) => Number(item.value)));
    const emptyOnly = dialog.querySelector("#rotation-empty-only").checked;
    const cells = [], values = [];
    const leaveDays = new Map();
    if (resumeMode.value === "continue") {
      const leaveKinds = new Set(["annual_leave", "sick_leave", "unpaid_leave", "marriage_leave", "quarantine"]);
      for (const row of scheduleRowsForPeople()) {
        if (!selected.has(row.employee_id)) continue;
        for (const [day, item] of Object.entries(row.days || {})) {
          if (leaveKinds.has(item.kind)) {
            if (!leaveDays.has(row.employee_id)) leaveDays.set(row.employee_id, new Set());
            leaveDays.get(row.employee_id).add(day);
          }
        }
      }
    }
    const skippedByPerson = new Map();
    for (const row of scheduleRowsForPeople().filter((item) => selected.has(item.employee_id))) {
      for (const day of monthDates(editingMonth).map(dateKey)) {
        if (day < start || day > end || (emptyOnly && row.days[day])) continue;
        const exceptions = leaveDays.get(row.employee_id) || new Set();
        if (exceptions.has(day)) continue;
        const calendarIndex = (Date.parse(day) - Date.parse(anchor.value)) / 86400000;
        const skipped = resumeMode.value === "continue"
          ? [...exceptions].filter((exceptionDay) => exceptionDay >= anchor.value && exceptionDay < day).length
          : 0;
        if (skipped) {
          if (!skippedByPerson.has(row.employee_id)) skippedByPerson.set(row.employee_id, new Set());
          for (const exceptionDay of exceptions) {
            if (exceptionDay >= anchor.value && exceptionDay < day) {
              skippedByPerson.get(row.employee_id).add(exceptionDay);
            }
          }
        }
        const index = calendarIndex - skipped;
        const entry = quickEntryByKey(steps[index % steps.length]);
        const item = scheduleItemForQuickEntry(entry);
        if (!item) continue;
        cells.push({ rowId: row.row_id, employee_id: row.employee_id, section: row.section, date: day });
        values.push({ ...item, fill_color: entry.color || "#ffffff" });
      }
    }
    if (!cells.length) throw new Error("Seçili personel ve aralıkta doldurulacak gün yok.");
    if (resumeMode.value === "continue" && skippedByPerson.size) {
      const names = new Map(state.people.map((item) => [item.id, item.name]));
      const affected = [...skippedByPerson].map(([employeeId, days]) => {
        const sorted = [...days].sort();
        const last = new Date(`${sorted[sorted.length - 1]}T12:00:00`);
        last.setDate(last.getDate() + 1);
        return `${names.get(employeeId) || `Personel ${employeeId}`}: ${sorted.join(", ")} izin günleri atlanıyor; kaldığı adıma ${dateKey(last)} tarihinde dönecek`;
      }).join("\n");
      if (!window.confirm(`İzin istisnası önizlemesi\n${affected}\nİzin günleri korunacak; sonraki günler döngünün kaldığı adımından devam edecek. Aşağıdaki çizelge önizlemesinde etkilenen günleri kontrol edin.`)) return;
    }
    await window.previewWorkbookBulk("schedule", cells, values, applyButton);
    dialog.close();
  }));
  await loadSaved();
  showExtras(dialog);
}

/* ---------- Vardiya değişimi (takas) ---------- */

async function openSwap() {
  if (state.view !== "schedule") await switchView("schedule");
  const rows = scheduleRowsForPeople();
  if (rows.length < 2) {
    const empty = extrasDialog("swap-dialog", "Vardiya değişimi", "İki personelin aynı gündeki vardiyası yer değiştirir ve kayıt altına alınır.");
    empty.querySelector(".extras-body").innerHTML = `<div class="extras-empty-state" role="status">
      <p><strong>Takas için açık haftada en az iki personel gerekli.</strong></p>
      <p>Şu anda çizelgede ${rows.length ? "yalnızca 1 personel" : "hiç personel"} var. Vardiya planında “Personel seçin” satırlarından en az iki kişi seçip kaydedin, sonra takası yeniden açın.</p>
    </div>
    <div class="dialog-footer"><button class="button button-primary" type="button" data-swap-goto>Vardiya planına git</button></div>
    <h3 class="extras-subtitle">Son değişimler</h3><div id="swap-log"></div>`;
    empty.querySelector("[data-swap-goto]").addEventListener("click", () => {
      empty.close();
      const target = document.querySelector("#schedule-table select[data-action='person-slot']");
      target?.scrollIntoView({ block: "center" });
      target?.focus();
    });
    try {
      const data = await request("/api/swaps");
      empty.querySelector("#swap-log").innerHTML = data.swaps.length
        ? `<ul class="extras-list">${data.swaps.slice(0, 10).map((swap) => `<li>${escapeHtml(swap.date)} · ${escapeHtml(swap.first_name)} ↔ ${escapeHtml(swap.second_name)}</li>`).join("")}</ul>`
        : '<p class="archive-empty">Henüz vardiya değişimi kaydı yok.</p>';
    } catch {
      empty.querySelector("#swap-log").innerHTML = '<p class="archive-empty">Değişim geçmişi okunamadı.</p>';
    }
    showExtras(empty);
    return;
  }
  const dialog = extrasDialog("swap-dialog", "Vardiya değişimi", "İki personelin aynı gündeki vardiyası yer değiştirir ve kayıt altına alınır.");
  const name = (id) => state.people.find((person) => person.id === id)?.name || "Personel";
  const options = rows.map((row) => `<option value="${row.employee_id}">${escapeHtml(name(row.employee_id))}</option>`).join("");
  const monthStart = `${state.scheduleMonth}-01`;
  const lastDay = dateKey(monthDates(state.scheduleMonth).at(-1));
  const today = dateKey(new Date());
  dialog.querySelector(".extras-body").innerHTML = `<div class="forms-fields">
    <label>Gün <input id="swap-date" type="date" min="${monthStart}" max="${lastDay}" value="${today >= monthStart && today <= lastDay ? today : monthStart}"></label>
    <label>Birinci personel <select id="swap-first">${options}</select></label>
    <label>İkinci personel <select id="swap-second">${options}</select></label>
    <label class="forms-wide">Gerekçe <input id="swap-reason" maxlength="200" placeholder="ör. kişisel mazeret, karşılıklı anlaşma"></label>
  </div>
  <p id="swap-preview" class="swap-preview" aria-live="polite"></p>
  <div class="dialog-footer"><button class="button button-primary" type="button" id="swap-apply">Takas taslağına uygula</button></div>
  <h3 class="extras-subtitle">Son değişimler</h3><div id="swap-log"></div>`;
  const second = dialog.querySelector("#swap-second");
  if (second.options.length > 1) second.selectedIndex = 1;
  const rowFor = (id) => state.schedule.entries.find((entry) => entry.employee_id === Number(id));
  const warningKey = (warning) => JSON.stringify([
    warning.type, warning.employeeId, warning.day, warning.previousDay,
    warning.section, warning.previousSection, [...(warning.sections || [])].sort(),
  ]);
  const swapImpact = (day, firstRow, secondRow) => {
    const before = scheduleValidationWarnings();
    const candidate = structuredClone(state.schedule);
    const candidateFirst = candidate.entries.find((entry) => entry.row_id === firstRow.row_id);
    const candidateSecond = candidate.entries.find((entry) => entry.row_id === secondRow.row_id);
    if (!candidateFirst || !candidateSecond) return { added: [], resolved: [] };
    const firstItem = candidateFirst.days[day];
    const secondItem = candidateSecond.days[day];
    if (secondItem) candidateFirst.days[day] = structuredClone(secondItem); else delete candidateFirst.days[day];
    if (firstItem) candidateSecond.days[day] = structuredClone(firstItem); else delete candidateSecond.days[day];
    const after = scheduleValidationWarnings(candidate);
    const beforeKeys = new Set(before.map(warningKey));
    const afterKeys = new Set(after.map(warningKey));
    return {
      added: after.filter((warning) => !beforeKeys.has(warningKey(warning))),
      resolved: before.filter((warning) => !afterKeys.has(warningKey(warning))),
    };
  };
  const preview = () => {
    const day = dialog.querySelector("#swap-date").value;
    const a = rowFor(dialog.querySelector("#swap-first").value);
    const b = rowFor(second.value);
    if (!a || !b || !day || a === b) {
      dialog.querySelector("#swap-preview").textContent = "";
      return { added: [], resolved: [] };
    }
    const impact = swapImpact(day, a, b);
    const warningDescription = (warning) => {
      if (warning.type === "overlap") {
        return `${warning.personName}: ${warning.previousDay} → ${warning.day}, vardiya çakışması`;
      }
      if (warning.type === "rest") {
        return `${warning.personName}: ${warning.previousDay} → ${warning.day}, ${warning.restHours.toLocaleString("tr-TR", { maximumFractionDigits: 2 })} saat dinlenme`;
      }
      return `${warning.personName}: ${warning.day}, birden fazla bölümde kayıt`;
    };
    const details = [
      `${name(a.employee_id)}: ${extrasItemLabel(a.days[day])}  ⇄  ${name(b.employee_id)}: ${extrasItemLabel(b.days[day])}`,
      impact.added.length
        ? `Yeni uyarılar (${impact.added.length}): ${impact.added.slice(0, 3).map(warningDescription).join("; ")}${impact.added.length > 3 ? "; …" : ""}`
        : "Yeni dinlenme/çakışma uyarısı yok.",
      impact.resolved.length
        ? `Giderilecek mevcut uyarı (${impact.resolved.length}): ${impact.resolved.slice(0, 3).map(warningDescription).join("; ")}${impact.resolved.length > 3 ? "; …" : ""}`
        : "",
    ].filter(Boolean);
    const previewNode = dialog.querySelector("#swap-preview");
    previewNode.textContent = details.join("\n");
    previewNode.dataset.impact = impact.added.length ? "warning" : impact.resolved.length ? "resolved" : "safe";
    return impact;
  };
  dialog.querySelector(".forms-fields").addEventListener("change", preview);
  const drawLog = async () => {
    const data = await request("/api/swaps");
    dialog.querySelector("#swap-log").innerHTML = data.swaps.length ? `<table class="archive-table extras-table"><thead><tr><th>Gün</th><th>Personel</th><th>Önceki</th><th>Gerekçe</th><th>Kaydeden</th></tr></thead><tbody>${data.swaps.slice(0, 30).map((swap) =>
      `<tr><td>${escapeHtml(swap.date)}</td><td>${escapeHtml(swap.first_name)} ↔ ${escapeHtml(swap.second_name)}</td><td>${escapeHtml(swap.first_before)} / ${escapeHtml(swap.second_before)}</td><td>${escapeHtml(swap.reason)}</td><td>${escapeHtml(swap.actor || "")}</td></tr>`).join("")}</tbody></table>`
      : '<p class="archive-empty">Henüz vardiya değişimi kaydı yok.</p>';
  };
  dialog.querySelector("#swap-apply").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    const day = dialog.querySelector("#swap-date").value;
    const a = rowFor(dialog.querySelector("#swap-first").value);
    const b = rowFor(second.value);
    if (!a || !b || a === b) throw new Error("İki farklı personel seçin.");
    if (!day || day.slice(0, 7) !== state.scheduleMonth) throw new Error("Gün, açık çizelge ayında olmalı.");
    const before = [extrasItemLabel(a.days[day]), extrasItemLabel(b.days[day])];
    if (!a.days[day] && !b.days[day]) throw new Error("İki personelin de bu günde kaydı yok.");
    const impact = swapImpact(day, a, b);
    if (impact.added.length && !window.confirm(
      `Bu takas yeni bir dinlenme veya vardiya çakışması uyarısı oluşturacak:\n${impact.added.slice(0, 3).map((warning) => `${warning.personName}: ${warning.day}`).join("\n")}\n\nYine de taslağa uygulansın mı?`,
    )) return;
    const first = a.days[day];
    const other = b.days[day];
    if (other) a.days[day] = structuredClone(other); else delete a.days[day];
    if (first) b.days[day] = structuredClone(first); else delete b.days[day];
    setScheduleDirty();
    renderSchedule();
    await request("/api/swaps", {
      method: "POST",
      body: JSON.stringify({
        date: day, first_id: a.employee_id, second_id: b.employee_id,
        reason: dialog.querySelector("#swap-reason").value, first_before: before[0], second_before: before[1],
      }),
    });
    preview();
    await drawLog();
    toast("Vardiyalar takas edildi ve kayıt alındı. Çizelgeyi kaydetmeyi unutmayın.");
  }));
  preview();
  await drawLog();
  showExtras(dialog);
}

/* ---------- Tatil takvimi ---------- */

async function openHolidays(year = new Date().getFullYear()) {
  const data = await request(`/api/holidays?year=${year}`);
  const dialog = extrasDialog("holidays-dialog", "Tatil takvimi",
    "Uygulamadaki resmî ve dinî tatiller. Eklediğiniz gün, aynı tarihteki hazır kaydın yerine geçer.");
  const labels = data.labels;
  const kindOptions = (selected) => Object.entries(labels)
    .map(([value, label]) => `<option value="${value}"${value === selected ? " selected" : ""}>${escapeHtml(label)}</option>`).join("");
  let custom = data.custom.map((item) => ({ ...item }));
  const body = dialog.querySelector(".extras-body");
  const draw = () => {
    const overridden = new Set(custom.map((item) => item.date));
    body.innerHTML = `<label class="extras-search"><span>Yıl</span><input id="holiday-year" type="number" min="2000" max="2100" value="${data.year}"></label>
      ${data.religious_known === false ? `<p class="dialog-help holiday-unknown">${data.year} için dinî bayram tarihleri hazır listede yok (hazır yıllar: ${(data.religious_years || []).join("–")}). Ramazan ve Kurban bayramı günlerini Diyanet takviminden sağdaki tabloya ekleyin.</p>` : ""}
      <div class="holiday-columns">
        <section><h3 class="extras-subtitle">Hazır liste (${data.year})</h3>
          <table class="archive-table extras-table"><tbody>${data.builtin.map((item) =>
            `<tr class="${overridden.has(item.date) ? "holiday-overridden" : ""}"><td>${escapeHtml(item.date)}</td><td>${escapeHtml(labels[item.kind] || item.kind)}</td><td>${escapeHtml(item.name)}</td></tr>`).join("")
            || '<tr><td>Bu yıl için hazır kayıt yok; dinî bayramları sağdan ekleyin.</td></tr>'}</tbody></table></section>
        <section><h3 class="extras-subtitle">Eklenen / düzeltilen günler</h3>
          <table class="archive-table extras-table holiday-edit"><thead><tr><th>Tarih</th><th>Tür</th><th>Ad</th><th></th></tr></thead><tbody>${custom.map((item, index) =>
            `<tr><td><input type="date" data-holiday="${index}" data-field="date" value="${escapeHtml(item.date)}"></td>
              <td><select data-holiday="${index}" data-field="kind">${kindOptions(item.kind)}</select></td>
              <td><input data-holiday="${index}" data-field="name" maxlength="80" value="${escapeHtml(item.name)}"></td>
              <td><button class="button button-light danger-button" type="button" data-holiday-remove="${index}">Sil</button></td></tr>`).join("")}</tbody></table>
          <div class="dialog-footer"><button class="button button-light" type="button" id="holiday-add">Gün ekle</button>
            <button class="button button-primary" type="button" id="holiday-save">Takvimi kaydet</button></div>
          <p class="dialog-help">“Çalışma günü” türü, hazır listedeki bir günü tatil olmaktan çıkarır. Değişiklik puantaj ve tatil mesaisi hesaplarına hemen yansır.</p>
        </section></div>`;
  };
  body.addEventListener("input", (event) => {
    const target = event.target.closest("[data-holiday]");
    if (target) custom[Number(target.dataset.holiday)][target.dataset.field] = target.value;
  });
  body.addEventListener("change", (event) => {
    if (event.target.id === "holiday-year") {
      const value = Number(event.target.value);
      if (value >= 2000 && value <= 2100) openHolidays(value).catch((error) => toast(error.message, true));
    }
  });
  body.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-holiday-remove]");
    if (remove) { custom.splice(Number(remove.dataset.holidayRemove), 1); draw(); return; }
    if (event.target.id === "holiday-add") {
      custom.push({ date: `${data.year}-01-01`, kind: "religious", name: "" });
      draw();
      return;
    }
    if (event.target.id === "holiday-save") {
      extrasRun(event.target, async () => {
        custom = await request("/api/holidays", { method: "PUT", body: JSON.stringify({ holidays: custom }) });
        custom = custom.custom;
        draw();
        if (state.view === "attendance" && !state.attendanceDirty) await loadAttendance();
        toast("Tatil takvimi kaydedildi.");
      });
    }
  });
  draw();
  showExtras(dialog);
}

/* ---------- Saat aralıklı asgari kadro ---------- */

async function openStaffingWindows() {
  const settings = await request("/api/compliance-settings");
  let windows = (settings.min_staffing_windows || []).map((window) => ({ ...window }));
  const dialog = extrasDialog("staffing-windows-dialog", "Asgari kadro saatleri",
    "Belirli saatlerde aynı bölümde eşzamanlı çalışması gereken en az kişi sayısını tanımlayın.");
  const body = dialog.querySelector(".extras-body");
  const draw = () => {
    body.innerHTML = `<div class="staffing-window-editor">${windows.map((window, index) => `
      <div class="staffing-window-row">
        <label>Bölüm<input data-window="${index}" data-field="section" maxlength="60" value="${escapeHtml(window.section)}"></label>
        <label>Başlangıç<input type="time" step="60" data-window="${index}" data-field="start" value="${escapeHtml(window.start)}"></label>
        <label>Bitiş<input type="time" step="60" data-window="${index}" data-field="end" value="${escapeHtml(window.end)}"></label>
        <label>En az kişi<input type="number" min="1" max="99" step="1" data-window="${index}" data-field="minimum" value="${escapeHtml(String(window.minimum))}"></label>
        <button class="button button-light danger-button" type="button" data-window-remove="${index}">Sil</button>
      </div>`).join("")}</div>
      <div class="dialog-footer">
        <button class="button button-light" type="button" id="staffing-window-add">Aralık ekle</button>
        <button class="button button-primary" type="button" id="staffing-window-save">Kaydet</button>
      </div>
      <p class="dialog-help">Aynı bölümün saat aralıkları birbiriyle çakışamaz. Uyarı, aralık boyunca en düşük eşzamanlı çalışan sayısına göre oluşturulur; kontrol kaydetmeyi engellemez.</p>`;
  };
  body.addEventListener("input", (event) => {
    const target = event.target.closest("[data-window]");
    if (target) windows[Number(target.dataset.window)][target.dataset.field] = target.value;
  });
  body.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-window-remove]");
    if (remove) {
      windows.splice(Number(remove.dataset.windowRemove), 1);
      draw();
      return;
    }
    if (event.target.id === "staffing-window-add") {
      const section = state.schedule?.entries.find((entry) => entry.section)?.section || "MARKET";
      windows.push({ section, start: "09:00", end: "17:00", minimum: 1 });
      draw();
      return;
    }
    if (event.target.id === "staffing-window-save") {
      extrasRun(event.target, async () => {
        const saved = await request("/api/compliance-settings", {
          method: "PUT",
          body: JSON.stringify({ min_staffing_windows: windows }),
        });
        windows = saved.min_staffing_windows;
        draw();
        toast("Saat aralıklı asgari kadro ayarları kaydedildi.");
      });
    }
  });
  draw();
  showExtras(dialog);
}

/* ---------- Alternatif vardiya senaryoları ---------- */

async function openScheduleScenarios() {
  if (state.view !== "schedule") await switchView("schedule");
  if (!state.schedule) throw new Error("Önce bir vardiya ayı açın.");
  const month = state.scheduleMonth;
  const storageKey = "vardiyaPuantaj.scheduleScenarios";
  const loadScenarios = () => {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return {};
    let data;
    try {
      data = JSON.parse(raw);
    } catch (error) {
      throw new Error("Alternatif senaryo kaydı okunamıyor; tarayıcıdaki yerel kayıt bozulmuş.");
    }
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      throw new Error("Alternatif senaryo kaydı beklenen biçimde değil.");
    }
    return data;
  };
  let allScenarios = loadScenarios();
  let scenarios = Array.isArray(allScenarios[month]) ? allScenarios[month] : [];
  let selectedId = scenarios[0]?.id || "";
  const dialog = extrasDialog("schedule-scenarios-dialog", "Alternatif vardiya senaryoları",
    "Ana planı değiştirmeden mevcut çizelgenin yerel bir kopyasını saklayın, farklarını karşılaştırın veya taslağa uygulayın.");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = `<div class="scenario-create">
      <label>Senaryo adı<input id="scenario-name" maxlength="60" placeholder="Örn. hafta sonu kadrosu"></label>
      <button class="button button-light" type="button" id="scenario-save">Mevcut planı kaydet</button>
    </div>
    <div id="scenario-list"></div>
    <div id="scenario-diff" class="scenario-diff" aria-live="polite"></div>
    <p class="dialog-help">Senaryolar bu bilgisayardaki tarayıcıda saklanır, veritabanına veya başka cihazlara eşitlenmez. Uygulamak ana planı yalnızca kaydedilmemiş taslağa kopyalar; geri almak için Ctrl+Z, kalıcılaştırmak için Kaydet kullanın.</p>`;
  const list = body.querySelector("#scenario-list");
  const diffNode = body.querySelector("#scenario-diff");
  const getDiff = (entries) => {
    const currentRows = new Map(state.schedule.entries.map((row) => [row.row_id, row]));
    const savedRows = new Map(entries.map((row) => [row.row_id, row]));
    const rowIds = new Set([...currentRows.keys(), ...savedRows.keys()]);
    const changes = [];
    for (const rowId of rowIds) {
      const current = currentRows.get(rowId);
      const saved = savedRows.get(rowId);
      if (!current || !saved || current.employee_id !== saved.employee_id
          || current.section !== saved.section || current.slot !== saved.slot) {
        const label = (row) => row
          ? `${name(row.employee_id)} (${row.section}, sıra ${row.slot})`
          : "Satır yok";
        changes.push(`${label(current)} → ${label(saved)}`);
      }
      const days = new Set([
        ...Object.keys(current?.days || {}),
        ...Object.keys(saved?.days || {}),
      ]);
      for (const day of days) {
        const before = current?.days?.[day];
        const after = saved?.days?.[day];
        if (JSON.stringify(before || null) !== JSON.stringify(after || null)) {
          changes.push(`${day} · ${name((current || saved).employee_id)} (${(current || saved).section}): ${before ? extrasItemLabel(before) : "Boş"} → ${after ? extrasItemLabel(after) : "Boş"}`);
        }
      }
    }
    return changes;
  };
  const warningKey = (warning) => JSON.stringify([
    warning.type, warning.employeeId, warning.day, warning.previousDay,
    warning.section, warning.previousSection, [...(warning.sections || [])].sort(),
  ]);
  const getWarningImpact = (entries) => {
    const before = scheduleValidationWarnings();
    const after = scheduleValidationWarnings({ ...state.schedule, entries });
    const beforeKeys = new Set(before.map(warningKey));
    const afterKeys = new Set(after.map(warningKey));
    return {
      added: after.filter((warning) => !beforeKeys.has(warningKey(warning))),
      resolved: before.filter((warning) => !afterKeys.has(warningKey(warning))),
    };
  };
  const name = (id) => state.people.find((person) => person.id === id)?.name || "Bilinmeyen personel";
  const draw = () => {
    list.innerHTML = scenarios.length ? scenarios.map((scenario) => `
      <div class="scenario-item" data-scenario="${escapeHtml(scenario.id)}">
        <strong>${escapeHtml(scenario.name)}</strong><small>${escapeHtml(new Date(scenario.created_at).toLocaleString("tr-TR"))}</small>
        <div class="scenario-actions">
          <button class="button button-light" type="button" data-scenario-compare="${escapeHtml(scenario.id)}">Farkları göster</button>
          <button class="button button-light" type="button" data-scenario-apply="${escapeHtml(scenario.id)}">Taslağa uygula</button>
          <button class="button button-light danger-button" type="button" data-scenario-delete="${escapeHtml(scenario.id)}">Sil</button>
        </div>
      </div>`).join("") : '<p class="archive-empty">Bu ay için alternatif senaryo yok.</p>';
    const selected = scenarios.find((scenario) => scenario.id === selectedId);
    if (selected) showDiff(selected);
    else diffNode.textContent = "";
  };
  const showDiff = (scenario) => {
    const changes = getDiff(scenario.entries);
    const impact = getWarningImpact(scenario.entries);
    const warningSummary = `<p>Yeni plan uyarısı: ${impact.added.length} · Giderilen uyarı: ${impact.resolved.length}</p>`;
    diffNode.innerHTML = `${changes.length
      ? `<strong>${changes.length} fark</strong><ul>${changes.slice(0, 30).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>${changes.length > 30 ? `<p>${changes.length - 30} fark daha var.</p>` : ""}`
      : "<strong>Bu senaryo mevcut planla aynı.</strong>"}${warningSummary}`;
  };
  body.querySelector("#scenario-save").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    const scenarioName = body.querySelector("#scenario-name").value.trim();
    if (!scenarioName) throw new Error("Senaryo için bir ad girin.");
    if (scenarios.length >= 20) throw new Error("Bu ay için en fazla 20 senaryo saklanabilir.");
    const scenario = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      name: scenarioName,
      created_at: new Date().toISOString(),
      entries: structuredClone(state.schedule.entries),
    };
    scenarios = [...scenarios, scenario];
    allScenarios[month] = scenarios;
    localStorage.setItem(storageKey, JSON.stringify(allScenarios));
    selectedId = scenario.id;
    body.querySelector("#scenario-name").value = "";
    draw();
    toast("Alternatif senaryo bu tarayıcıya kaydedildi.");
  }));
  list.addEventListener("click", (event) => {
    const compare = event.target.closest("[data-scenario-compare]");
    const apply = event.target.closest("[data-scenario-apply]");
    const remove = event.target.closest("[data-scenario-delete]");
    const scenarioId = compare?.dataset.scenarioCompare || apply?.dataset.scenarioApply || remove?.dataset.scenarioDelete;
    const scenario = scenarios.find((item) => item.id === scenarioId);
    if (!scenario) return;
    selectedId = scenario.id;
    if (compare) { showDiff(scenario); return; }
    if (remove) {
      if (!window.confirm(`“${scenario.name}” senaryosu silinsin mi?`)) return;
      scenarios = scenarios.filter((item) => item.id !== scenario.id);
      allScenarios[month] = scenarios;
      localStorage.setItem(storageKey, JSON.stringify(allScenarios));
      selectedId = scenarios[0]?.id || "";
      draw();
      return;
    }
    if (apply) {
      const impact = getWarningImpact(scenario.entries);
      const message = `“${scenario.name}” mevcut çizelgenin kaydedilmemiş taslağının yerine uygulansın mı?`
        + (impact.added.length ? `\nYeni plan uyarısı: ${impact.added.length}.` : "");
      if (!window.confirm(message)) return;
      const entries = structuredClone(scenario.entries);
      if (JSON.stringify(entries) === JSON.stringify(state.schedule.entries)) {
        toast("Senaryo zaten mevcut çizelgeyle aynı.");
        return;
      }
      state.schedule.entries = entries;
      setScheduleDirty();
      renderSchedule();
      showDiff(scenario);
      toast("Senaryo taslağa uygulandı. Kalıcı olması için vardiya çizelgesini kaydedin.");
    }
  });
  draw();
  showExtras(dialog);
}

/* ---------- Bulut yedeği ---------- */

async function installBackupMirror() {
  const anchor = el("#backup-retention-form");
  if (!anchor || el("#backup-mirror-form")) return;
  const form = document.createElement("form");
  form.id = "backup-mirror-form";
  form.className = "user-name-form backup-retention-form backup-mirror-form";
  form.innerHTML = `<label for="backup-mirror-input">Yedek kopya klasörü</label>
    <input id="backup-mirror-input" type="text" maxlength="400" placeholder="ör. C:\\Users\\...\\VardiyaPuantaj Yedekleri">
    <button class="button button-light" type="button" id="backup-mirror-local">OneDrive dışı öner</button>
    <button class="button button-light" type="button" id="backup-mirror-suggest">OneDrive öner</button>
    <button class="button button-outline" type="submit">Kaydet</button>
    <small id="backup-mirror-help">Her yedek bu klasöre de kopyalanır. Boş bırakıp kaydederseniz kapanır.</small>`;
  anchor.after(form);
  let suggested = "";
  let localSuggested = "";
  const refresh = async () => {
    const data = await request("/api/backup-mirror");
    suggested = data.suggested || "";
    localSuggested = data.local_suggested || "";
    const input = form.querySelector("input");
    if (document.activeElement !== input) input.value = data.folder || "";
    form.querySelector("#backup-mirror-suggest").hidden = !suggested || Boolean(data.database_synced);
    form.querySelector("#backup-mirror-local").hidden = !localSuggested;
    form.querySelector("#backup-mirror-help").textContent = data.database_synced
      ? `Veritabanı zaten OneDrive ile eşitlenen klasörde (${data.database_synced}). Eşitleme çakışmasına karşı yedeklerin ikinci kopyası için OneDrive dışında bir klasör seçin. Boş bırakıp kaydederseniz kapanır.`
      : "Her yedek bu klasöre de kopyalanır. OneDrive klasörü seçerseniz bilgisayar bozulsa bile yedekler bulutta kalır. Boş bırakıp kaydederseniz kapanır.";
  };
  form.querySelector("#backup-mirror-suggest").addEventListener("click", () => {
    form.querySelector("input").value = suggested;
  });
  form.querySelector("#backup-mirror-local").addEventListener("click", () => {
    form.querySelector("input").value = localSuggested;
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    extrasRun(form.querySelector('button[type="submit"]'), async () => {
      const result = await request("/api/backup-mirror", {
        method: "PUT", body: JSON.stringify({ folder: form.querySelector("input").value }),
      });
      await refresh();
      toast(result.folder ? `Yedekler ayrıca “${result.folder}” klasörüne kopyalanacak${result.copied ? ` (${result.copied} yedek kopyalandı)` : ""}.` : "Yedek kopya klasörü kapatıldı.");
    });
  });
  await refresh();
}

/* ---------- Değişiklik geçmişinde arama ---------- */

function installHistorySearch() {
  const list = el("#history-list");
  if (!list || el("#history-search-form")) return;
  const form = document.createElement("form");
  form.id = "history-search-form";
  form.className = "history-search";
  form.innerHTML = `<label><span>Ara</span><input id="history-query" type="search" maxlength="80" placeholder="ad, ay veya işlem"></label>
    <label><span>Tür</span><select id="history-action"><option value="">Tümü</option>
      <option value="schedule.">Vardiya</option><option value="attendance">Puantaj</option>
      <option value="employee">Personel</option><option value="backup">Yedek</option>
      <option value="settings">Ayarlar</option><option value="archive">Arşiv</option></select></label>
    <button class="button button-outline" type="submit">Süz</button>`;
  list.before(form);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    extrasRun(form.querySelector("button"), async () => {
      const query = new URLSearchParams({
        limit: "200", q: form.querySelector("#history-query").value.trim(), action: form.querySelector("#history-action").value,
      });
      state.history = (await request(`/api/history?${query}`)).history;
      renderHistory();
    });
  });
  form.querySelector("#history-action").addEventListener("change", () => form.requestSubmit());
}

/* ---------- Raporlar: kişiye özel program ve ay sonu paketi ---------- */

function installReportBlocks() {
  const footer = el("#reports-dialog .dialog-actions");
  if (!footer || el("#report-program-block")) return;
  const block = document.createElement("section");
  block.id = "report-program-block";
  block.className = "report-block";
  block.innerHTML = `<h3>Kişiye özel vardiya programı</h3>
    <p class="reports-note">Her çalışana kendi aylık programını PDF olarak verin (seçilmezse herkes ayrı sayfada).</p>
    <div class="report-actions"><label class="report-person"><span>Personel</span><select id="report-program-person"></select></label>
      <button class="button button-primary" type="button" id="report-program-pdf">Program PDF kaydet</button></div>`;
  const pack = document.createElement("section");
  pack.className = "report-block";
  pack.innerHTML = `<h3>Ay sonu paketi (ZIP)</h3>
    <p class="reports-note">Puantaj ve vardiya Excel’i, bordro (Excel + CSV), personel özetleri, programlar ve dönem kontrolü tek dosyada.</p>
    <div class="report-actions"><button class="button button-outline" type="button" id="report-month-package">ZIP kaydet</button></div>`;
  footer.before(block, pack);
  block.querySelector("#report-program-pdf").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    const person = el("#report-program-person").value;
    await saveReportFile(
      `/api/reports/person-program?month=${encodeURIComponent(state.attendanceMonth)}${person ? `&employee_id=${person}` : ""}`,
      "pdf", `VARDIYA_PROGRAMI_${state.attendanceMonth}.pdf`,
    );
  }));
  pack.querySelector("#report-month-package").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    if (state.attendanceDirty || state.scheduleDirty) toast("Kaydedilmemiş değişiklikler pakete girmez; önce kaydedin.", true);
    await saveReportFile(`/api/reports/month-package?month=${encodeURIComponent(state.attendanceMonth)}`,
      "zip", `AY_SONU_PAKETI_${state.attendanceMonth}.zip`);
  }));
  new MutationObserver(() => {
    const dialog = el("#reports-dialog");
    if (!dialog.open) return;
    const select = el("#report-program-person");
    const people = [...(state.attendance?.rows || [])].sort((a, b) => a.name.localeCompare(b.name, "tr"));
    select.innerHTML = `<option value="">Tüm personel</option>${people.map((row) => `<option value="${row.employee_id}">${escapeHtml(row.name)}</option>`).join("")}`;
  }).observe(el("#reports-dialog"), { attributes: true, attributeFilter: ["open"] });
}

async function saveMonthPackage() {
  const month = extrasActiveMonth();
  if (state.attendanceDirty || state.scheduleDirty) toast("Kaydedilmemiş değişiklikler pakete girmez; önce kaydedin.", true);
  await saveReportFile(`/api/reports/month-package?month=${encodeURIComponent(month)}`, "zip", `AY_SONU_PAKETI_${month}.zip`);
}

async function saveProgramPdf() {
  const month = extrasActiveMonth();
  const dialog = extrasDialog("person-program-dialog", "Vardiya programı PDF",
    "Seçili ayın programını tüm personel veya tek personel için okunaklı PDF olarak dışa aktarın.");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = `<div class="forms-fields">
      <label>Dönem <input id="program-month" type="month" value="${escapeHtml(month)}"></label>
      <label>Personel <select id="program-person">
        <option value="">Tüm personel</option>
        ${state.people.map((person) => `<option value="${person.id}">${escapeHtml(person.name)}${person.active ? "" : " · pasif"}</option>`).join("")}
      </select></label>
    </div>
    <div class="dialog-footer"><button class="button button-primary" type="button" id="program-export">PDF oluştur</button></div>`;
  body.querySelector("#program-export").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    const selectedMonth = body.querySelector("#program-month").value;
    if (!/^\d{4}-\d{2}$/.test(selectedMonth)) throw new Error("Dönem seçimi geçersiz.");
    const employeeId = body.querySelector("#program-person").value;
    const query = new URLSearchParams({ month: selectedMonth });
    if (employeeId) query.set("employee_id", employeeId);
    await saveReportFile(
      `/api/reports/person-program?${query}`,
      "pdf",
      `VARDIYA_PROGRAMI_${selectedMonth}${employeeId ? `_PERSONEL_${employeeId}` : ""}.pdf`,
    );
    dialog.close();
  }));
  showExtras(dialog);
}

/* ---------- İzin kayıtlarını vardiyaya işleme ---------- */

function installEventApply() {
  const list = el("#attendance-events-list");
  if (!list) return;
  const decorate = async () => {
    const buttons = [...list.querySelectorAll("[data-event-delete]")];
    if (!buttons.length || list.querySelector("[data-event-apply]")) return;
    buttons.forEach((button) => {
      const apply = document.createElement("button");
      apply.type = "button";
      apply.className = "button button-outline";
      apply.dataset.eventApply = button.dataset.eventDelete;
      apply.textContent = "Vardiyaya işle";
      apply.title = "Kayıt tarihlerini vardiya çizelgesine izin/rapor olarak yazar";
      button.before(apply);
    });
  };
  new MutationObserver(() => decorate()).observe(list, { childList: true });
  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-event-apply]");
    if (!button) return;
    if (state.scheduleDirty) { toast("Önce vardiya çizelgesindeki değişiklikleri kaydedin.", true); return; }
    if (!window.confirm("Bu kaydın tarihleri vardiya çizelgesine yazılacak; o günlerdeki mevcut vardiyalar değişecek. Devam edilsin mi?")) return;
    extrasRun(button, async () => {
      const result = await request("/api/attendance-events/apply", {
        method: "POST", body: JSON.stringify({ id: button.dataset.eventApply }),
      });
      await loadAttendance();
      toast(`${result.applied_days} gün vardiyaya işlendi${result.skipped_months.length ? `; ${result.skipped_months.join(", ")} ayında personel satırı olmadığı için atlandı` : ""}.`);
    });
  });
}

/* ---------- Hücre notları ---------- */

function scheduleItemAt(rowId, day) {
  const row = state.schedule?.entries.find((entry) => entry.row_id === rowId);
  if (!row) return null;
  if (day.slice(0, 7) === state.scheduleMonth) return row.days[day] || null;
  const data = state.neighborSchedules?.[day.slice(0, 7)];
  const match = data?.entries.find((item) => item.employee_id === row.employee_id && item.section === row.section);
  return match?.days[day] || null;
}

function markScheduleNotes() {
  const table = el("#schedule-table");
  if (!table) return;
  table.querySelectorAll("td[data-shift-day]").forEach((cell) => {
    const item = cell.dataset.shiftRow ? scheduleItemAt(cell.dataset.shiftRow, cell.dataset.shiftDay) : null;
    const note = item?.note || "";
    cell.classList.toggle("has-cell-note", Boolean(note));
    if (note) cell.title = `Not: ${note}`;
    else if (cell.title?.startsWith("Not: ")) cell.removeAttribute("title");
  });
}

function installCellNotes() {
  const dialog = el("#cell-quick-entry-dialog");
  const footer = dialog?.querySelector(".clipboard-footer");
  if (!footer) return;
  const box = document.createElement("form");
  box.className = "cell-note-form";
  box.innerHTML = `<label for="cell-note-input">Hücre notu</label>
    <input id="cell-note-input" type="text" maxlength="200" placeholder="ör. rapor getirecek">
    <button class="button button-outline" type="submit">Notu kaydet</button>
    <small id="cell-note-help"></small>`;
  footer.before(box);
  const input = box.querySelector("input");
  const help = box.querySelector("small");
  new MutationObserver(() => {
    if (!dialog.open) return;
    const target = state.quickDialogTarget;
    const item = target?.rowId ? scheduleItemAt(target.rowId, target.day) : null;
    input.value = item?.note || "";
    input.disabled = !item;
    box.querySelector("button").disabled = !item;
    help.textContent = item ? "Not Excel çıktısında hücre açıklaması olarak görünür." : "Not eklemek için önce bu güne bir vardiya veya durum girin.";
  }).observe(dialog, { attributes: true, attributeFilter: ["open"] });
  box.addEventListener("submit", (event) => {
    event.preventDefault();
    const target = state.quickDialogTarget;
    const row = target?.rowId && state.schedule.entries.find((entry) => entry.row_id === target.rowId);
    const owner = row && scheduleRowForDay(row, target.day, false);
    const item = owner?.days[target.day];
    if (!item) return;
    const note = input.value.trim().slice(0, 200);
    if (note) item.note = note; else delete item.note;
    if (target.day.slice(0, 7) !== state.scheduleMonth) state.neighborDirty.add(target.day.slice(0, 7));
    setScheduleDirty();
    renderSchedule();
    dialog.close();
    toast(note ? "Not eklendi. Değişiklikleri kaydedin." : "Not silindi. Değişiklikleri kaydedin.");
  });
  const table = el("#schedule-table");
  if (table) {
    let pending = false;
    new MutationObserver(() => {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => { pending = false; markScheduleNotes(); });
    }).observe(table, { childList: true, subtree: true });
  }
}

/* ---------- Uygulama güncelleme ---------- */

async function openUpdate() {
  const dialog = extrasDialog("update-dialog", "Uygulama güncelleme",
    "Yeni EXE’yi bir klasöre koyun; uygulama oradan tek tıkla güncellenir.");
  const body = dialog.querySelector(".extras-body");
  const draw = (info) => {
    if (!info.supported) {
      body.innerHTML = '<p class="archive-empty">Güncelleme yalnız kurulu EXE sürümünde kullanılabilir.</p>';
      return;
    }
    body.innerHTML = `<dl class="card-facts"><dt>Kurulu sürüm</dt><dd>${escapeHtml(info.current.path)}<br><small>${escapeHtml(info.current.modified)} · ${Math.round(info.current.size / 1048576)} MB</small></dd>
      <dt>Yeni sürüm</dt><dd>${info.candidate ? `${escapeHtml(info.candidate.path)}<br><small>${escapeHtml(info.candidate.modified)} · ${Math.round(info.candidate.size / 1048576)} MB</small>` : "Klasörde bulunamadı"}</dd></dl>
      <form id="update-form" class="user-name-form backup-retention-form"><label for="update-folder">Güncelleme klasörü</label>
        <input id="update-folder" type="text" maxlength="400" value="${escapeHtml(info.folder)}" placeholder="ör. C:\\Users\\...\\OneDrive\\VardiyaGuncelleme">
        <button class="button button-outline" type="submit">Kaydet ve denetle</button></form>
      <div class="dialog-footer"><button class="button button-primary" type="button" id="update-apply"${info.available && info.schema_compatibility?.compatible ? "" : " disabled"}>Güncelle ve yeniden başlat</button></div>
      <p class="dialog-help">${info.available
        ? (info.schema_compatibility?.compatible
          ? "EXE’ler veri şemasıyla uyumlu. Güncelleme öncesi yedek doğrulanır; eski EXE “.onceki” uzantısıyla saklanır."
          : `Güncelleme durduruldu: ${escapeHtml(info.schema_compatibility?.reason || "Şema uyumluluğu doğrulanamadı.")} EXE değiştirilmeyecek.`)
        : "Klasörde daha yeni bir VardiyaPuantaj.exe yok."}</p>`;
    body.querySelector("#update-form").addEventListener("submit", (event) => {
      event.preventDefault();
      extrasRun(event.currentTarget.querySelector("button"), async () => {
        draw(await request("/api/update", { method: "PUT", body: JSON.stringify({ folder: body.querySelector("#update-folder").value }) }));
      });
    });
    body.querySelector("#update-apply").addEventListener("click", (event) => {
      if (state.scheduleDirty || state.attendanceDirty) { toast("Önce kaydedilmemiş değişiklikleri kaydedin.", true); return; }
      if (!window.confirm("Uygulama kapanıp yeni sürümle yeniden açılacak. Devam edilsin mi?")) return;
      extrasRun(event.currentTarget, async () => {
        await request("/api/update/apply", { method: "POST", body: JSON.stringify({}) });
        toast("Güncelleniyor; uygulama birkaç saniye içinde yeniden açılacak.");
      });
    });
  };
  draw(await request("/api/update"));
  showExtras(dialog);
}

/* ---------- Bölümler ve satırlar ---------- */

async function openSectionsDialog() {
  if (state.scheduleDirty) {
    toast("Önce vardiya çizelgesindeki değişiklikleri kaydedin.", true);
    return;
  }
  const payload = await request("/api/schedule-sections");
  const limits = payload.limits || SECTION_SETTINGS;
  let draft = payload.sections.map((item) => ({ ...item, original: item.name }));
  const dialog = extrasDialog("sections-dialog", "Bölümler ve satırlar",
    "Her bölümün Excel'de kaç personel satırı olacağını belirleyin ve yeni bölüm ekleyin.");
  const body = dialog.querySelector(".extras-body");
  const usage = new Map();
  for (const entry of state.schedule?.entries || []) {
    usage.set(entry.section, Math.max(usage.get(entry.section) || 0, Number(entry.slot) || 1));
  }
  const render = () => {
    const customCount = draft.filter((item) => item.custom).length;
    body.innerHTML = `<table class="sections-table"><thead><tr><th scope="col">Bölüm</th><th scope="col">Personel satırı</th><th scope="col">Bilgi</th><th scope="col" aria-label="İşlem"></th></tr></thead><tbody>
      ${draft.map((item, index) => {
        const minimum = item.custom ? 1 : item.template_rows.length;
        const used = usage.get(item.original) || 0;
        const info = item.custom
          ? "Yeni bölüm · Excel'de ÖSSE'nin altına eklenir"
          : `Şablonda ${item.template_rows.length} satır${item.rows > item.template_rows.length ? ` · ${item.rows - item.template_rows.length} satır eklenecek` : ""}`;
        return `<tr>
          <td>${item.custom
            ? `<input type="text" maxlength="24" value="${escapeHtml(item.name)}" data-section-name="${index}" aria-label="Bölüm adı">`
            : `<strong>${escapeHtml(item.name)}</strong>`}</td>
          <td><div class="section-stepper">
            <button type="button" class="button button-light" data-section-step="${index}" data-delta="-1" aria-label="${escapeHtml(item.name)} satır azalt"${item.rows <= Math.max(minimum, used) ? " disabled" : ""}>−</button>
            <input type="number" min="${minimum}" max="${limits.max_rows}" value="${item.rows}" data-section-rows="${index}" aria-label="${escapeHtml(item.name)} satır sayısı">
            <button type="button" class="button button-light" data-section-step="${index}" data-delta="1" aria-label="${escapeHtml(item.name)} satır artır"${item.rows >= limits.max_rows ? " disabled" : ""}>+</button>
          </div></td>
          <td class="section-info">${escapeHtml(info)}${used ? ` · bu ay ${used}. satıra kadar dolu` : ""}</td>
          <td>${item.custom ? `<button type="button" class="button button-light" data-section-delete="${index}">Sil</button>` : ""}</td>
        </tr>`;
      }).join("")}
      </tbody></table>
      <form class="section-add-form" data-section-add>
        <label>Yeni bölüm adı <input type="text" maxlength="24" placeholder="Örn. KASA" ${customCount >= limits.max_custom ? "disabled" : ""}></label>
        <button type="submit" class="button button-light"${customCount >= limits.max_custom ? " disabled" : ""}>Bölüm ekle</button>
      </form>
      <p class="controls-hint">Satır sayısı Excel çıktısındaki sabit personel satırlarıdır; boş kalsa da çıktıda görünür. Çizelgedeki “Ek personel satırı ekle” ile her bölüme ${limits.extra_rows} geçici satır daha açılabilir. Ayda en fazla ${limits.max_personnel} farklı personel olabilir. Personeli olan bir bölüm silinemez ve dolu satırların altına inilemez.</p>`;
  };
  render();
  body.addEventListener("click", (event) => {
    const step = event.target.closest("[data-section-step]");
    if (step) {
      const item = draft[Number(step.dataset.sectionStep)];
      item.rows = Math.min(limits.max_rows, Math.max(item.custom ? 1 : item.template_rows.length, item.rows + Number(step.dataset.delta)));
      render();
      return;
    }
    const remove = event.target.closest("[data-section-delete]");
    if (remove) {
      const item = draft[Number(remove.dataset.sectionDelete)];
      if (usage.get(item.original) && !window.confirm(`${item.name} bölümünde bu ay personel var. Silinemezse kaydederken uyarı alırsınız. Yine de silmeyi deneyelim mi?`)) return;
      draft = draft.filter((candidate) => candidate !== item);
      render();
    }
  });
  body.addEventListener("change", (event) => {
    if (event.target.dataset.sectionRows !== undefined) {
      const item = draft[Number(event.target.dataset.sectionRows)];
      const value = Number.parseInt(event.target.value, 10);
      if (Number.isInteger(value)) item.rows = value;
      render();
    } else if (event.target.dataset.sectionName !== undefined) {
      draft[Number(event.target.dataset.sectionName)].name = event.target.value;
    }
  });
  body.addEventListener("submit", (event) => {
    event.preventDefault();
    const input = event.target.querySelector("input");
    const name = input.value.trim().toLocaleUpperCase("tr-TR");
    if (!name) { input.focus(); return; }
    if (draft.some((item) => item.name.toLocaleUpperCase("tr-TR") === name)) {
      toast(`${name} bölümü zaten var.`, true);
      return;
    }
    draft.push({ name, rows: 1, template_rows: [], custom: true, original: null });
    render();
  });
  const actions = dialog.querySelector(".extras-actions");
  const save = document.createElement("button");
  save.type = "button";
  save.className = "button";
  save.textContent = "Kaydet";
  actions.prepend(save);
  save.addEventListener("click", () => extrasRun(save, async () => {
    if (state.scheduleDirty) throw new Error("Önce vardiya çizelgesindeki değişiklikleri kaydedin.");
    const result = await request("/api/schedule-sections", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sections: draft.map((item) => ({ name: item.name.trim(), rows: item.rows })) }),
    });
    applyScheduleSections({ ...result, limits });
    if (state.schedule) renderSchedule();
    dialog.close();
    toast("Bölümler kaydedildi. Excel çıktısı yeni satırlarla alınacak.");
  }));
  showExtras(dialog);
}

/* ---------- Ay sonu kontrol sihirbazı ---------- */

const MONTH_END_STATUS = { ok: ["Tamam", "✓"], warning: ["Kontrol edin", "!"], error: ["Düzeltin", "×"] };

async function monthEndGoTo(kind, month) {
  if (state.view !== kind && !(await switchView(kind))) return false;
  const current = kind === "schedule" ? state.scheduleMonth : state.attendanceMonth;
  if (current === month) return true;
  if (!(await resolveUnsaved(kind, "Dönem değiştirilmeden"))) return false;
  if (kind === "schedule") {
    state.scheduleMonth = month;
    el("#schedule-month").value = month;
    await loadSchedule();
  } else {
    state.attendanceMonth = month;
    el("#attendance-month").value = month;
    const label = el("#attendance-period-label");
    if (label) label.textContent = extrasMonthLabel(month);
    await loadAttendance();
  }
  return true;
}

async function openMonthEndCheck(initialMonth) {
  let month = /^\d{4}-\d{2}$/.test(initialMonth || "") ? initialMonth : (state.attendanceMonth || extrasActiveMonth());
  const dialog = extrasDialog("month-end-dialog", "Ay sonu kontrol sihirbazı",
    "Dönemi kapatmadan önce plan, puantaj, izin ve yedek durumunu sırayla denetleyin; sonra çıktıları alıp dönemi kilitleyin.");
  dialog.classList.add("month-end-dialog");
  const body = dialog.querySelector(".extras-body");
  const back = () => openMonthEndCheck(month);
  const actions = {
    health: [["Veri sağlığını aç", async () => { dialog.close(); await openHealth(); }]],
    schedule: [["Vardiya planını aç", async () => { if (await monthEndGoTo("schedule", month)) dialog.close(); }]],
    "attendance-people": [["Puantajı aç ve ekle", async () => { if (await monthEndGoTo("attendance", month)) dialog.close(); }]],
    "attendance-cells": [["Puantajı aç", async () => { if (await monthEndGoTo("attendance", month)) dialog.close(); }],
      ["Eksik gün kontrolü", async () => {
        if (!(await monthEndGoTo("attendance", month))) return;
        dialog.close();
        el("#open-attendance-check")?.click();
      }]],
    leave: [["İzinleri aç", async () => {
      dialog.close();
      if (window.linkedLeave) await window.linkedLeave.open({ onBack: back, backLabel: "← Ay sonu kontrolüne dön" });
      else await openLeaveRequests();
    }]],
    backup: [["Şimdi yedek al", async () => {
      await request("/api/backups", { method: "POST", body: JSON.stringify({}) });
      toast("Yedek alındı.");
      await draw();
    }]],
    lock: [["Dönemi kilitle", async () => lockPeriod()]],
  };
  let data = null;
  const lockPeriod = async () => {
    if (!data?.can_lock) throw new Error(data?.locked ? "Dönem zaten kilitli." : "Bu ay için kayıt olmadığından dönem kilitlenemez.");
    if (data.errors) throw new Error("Önce kırmızı işaretli sorunları düzeltin; hatalı veriyle dönem kilitlenemez.");
    const open = data.steps.filter((item) => item.status === "warning" && item.id !== "lock").map((item) => `• ${item.title}: ${item.detail}`);
    const question = open.length
      ? `${extrasMonthLabel(month)} döneminde hâlâ uyarılar var:\n${open.join("\n")}\n\nYine de dönem kilitlensin mi? Kilit, Arşiv sayfasından gerekçeyle açılabilir.`
      : `${extrasMonthLabel(month)} dönemi kilitlensin mi? Kilitli dönemde vardiya ve puantaj değiştirilemez; Arşiv sayfasından gerekçeyle açılabilir.`;
    if (!confirm(question)) return;
    await request("/api/archive/period-lock", { method: "POST", body: JSON.stringify({ month, locked: true }) });
    toast(`${extrasMonthLabel(month)} dönemi kilitlendi.`);
    if (typeof loadArchive === "function" && state.view === "archive") loadArchive().catch(() => {});
    await draw();
  };
  body.innerHTML = `<div class="month-end-toolbar">
      <label>Dönem <input type="month" id="month-end-month" value="${escapeHtml(month)}"></label>
      <button class="button button-light" type="button" id="month-end-refresh">Yeniden denetle</button>
      <div class="month-end-progress" id="month-end-progress" aria-live="polite"></div>
    </div>
    ${state.scheduleDirty || state.attendanceDirty ? '<p class="month-end-dirty">Kaydedilmemiş değişiklikler var. Denetim kayıtlı verilere göre yapılır; önce kaydetmeniz önerilir.</p>' : ""}
    <ol class="month-end-steps" id="month-end-steps"><li class="month-end-loading">Denetleniyor…</li></ol>
    <section class="month-end-outputs"><h3>Çıktılar</h3>
      <p>Kontroller tamamlanınca dönemin dosyalarını alın.</p>
      <div class="month-end-output-buttons">
        <button class="button button-light" type="button" data-month-end-output="attendance">Puantaj Excel</button>
        <button class="button button-light" type="button" data-month-end-output="schedule">Vardiya Excel</button>
        <button class="button button-light" type="button" data-month-end-output="package">Ay sonu paketi (ZIP)</button>
      </div></section>`;
  const draw = async () => {
    const list = body.querySelector("#month-end-steps");
    list.innerHTML = '<li class="month-end-loading">Denetleniyor…</li>';
    data = await request(`/api/month-end-check?month=${encodeURIComponent(month)}`);
    const done = data.steps.filter((item) => item.status === "ok").length;
    body.querySelector("#month-end-progress").innerHTML = `<meter min="0" max="${data.steps.length}" value="${done}"></meter>
      <span><b>${done}/${data.steps.length}</b> adım tamam${data.errors ? ` · <b class="month-end-error-text">${data.errors} hata</b>` : ""}${data.locked ? " · dönem kilitli" : ""}</span>`;
    list.innerHTML = data.steps.map((item, index) => {
      const [label, mark] = MONTH_END_STATUS[item.status] || MONTH_END_STATUS.warning;
      const buttons = (item.status === "ok" && item.id !== "backup") ? [] : (actions[item.id] || []);
      const lockDisabled = item.id === "lock" && !data.can_lock;
      return `<li class="month-end-step is-${item.status}" data-step="${escapeHtml(item.id)}">
        <span class="month-end-mark" aria-hidden="true">${mark}</span>
        <div class="month-end-text"><h4>${index + 1}. ${escapeHtml(item.title)} <small class="month-end-chip">${label}</small></h4>
          <p>${escapeHtml(item.detail)}</p>
          ${item.items.length ? `<details><summary>Ayrıntılar (${item.items.length})</summary><ul>${item.items.map((text) => `<li>${escapeHtml(text)}</li>`).join("")}</ul></details>` : ""}
        </div>
        <div class="month-end-actions">${buttons.map(([text], buttonIndex) => `<button class="button ${buttonIndex ? "button-light" : "button-outline"}" type="button"
          data-month-end-action="${escapeHtml(item.id)}" data-index="${buttonIndex}"${lockDisabled ? " disabled" : ""}>${escapeHtml(text)}</button>`).join("")}</div>
      </li>`;
    }).join("");
  };
  body.querySelector("#month-end-month").addEventListener("change", (event) => {
    if (!/^\d{4}-\d{2}$/.test(event.target.value)) return;
    month = event.target.value;
    draw().catch((error) => toast(error.message, true));
  });
  body.querySelector("#month-end-refresh").addEventListener("click", (event) => extrasRun(event.currentTarget, draw));
  body.addEventListener("click", (event) => {
    const action = event.target.closest("[data-month-end-action]");
    if (action) {
      const entry = actions[action.dataset.monthEndAction]?.[Number(action.dataset.index)];
      if (entry) extrasRun(action, entry[1]);
      return;
    }
    const output = event.target.closest("[data-month-end-output]");
    if (!output) return;
    extrasRun(output, async () => {
      const kind = output.dataset.monthEndOutput;
      if (kind === "package") {
        await saveReportFile(`/api/reports/month-package?month=${encodeURIComponent(month)}`, "zip", `AY_SONU_PAKETI_${month}.zip`);
      } else {
        await downloadExcel(kind, { month, week: 0 });
      }
    });
  });
  showExtras(dialog);
  await draw();
}

/* ---------- Araçlar menüsü ---------- */

const EXTRAS_MENU = [
  ["Bugünün özeti", openDashboard],
  ["Personel ana sayfası", () => openPersonnelHome()],
  ["Personel ara  (Ctrl+K)", openSearch],
  ["Personel kartı", () => openEmployeeCard()],
  ["Ay sonu kontrol sihirbazı", () => openMonthEndCheck()],
  ["-"],
  ["Otomatik vardiya oluştur (döngü)", openRotation],
  ["Bağlantılı izinler", () => window.linkedLeave.open()],
  ["Vardiya değişimi (takas)", openSwap],
  ["Alternatif senaryolar", openScheduleScenarios],
  ["Eksik / hatalı gün kontrolü", async () => {
    const kind = state.view === "attendance" ? "attendance" : "schedule";
    if (state.view !== kind) await switchView(kind);
    el(`#open-${kind}-check`).click();
  }],
  ["Yazdırma önizlemesi", async () => {
    const kind = state.view === "attendance" ? "attendance" : "schedule";
    if (state.view !== kind) await switchView(kind);
    el(`#${kind}-view [data-output-kind="${kind}"]`).click();
  }],
  ["-"],
  ["Vardiya programı PDF", saveProgramPdf],
  ["Ay sonu paketi (ZIP)", saveMonthPackage],
  ["-"],
  ["Tatil takvimi", () => openHolidays()],
  ["Asgari kadro saatleri", openStaffingWindows],
  ["Bölümler ve satırlar", () => openSectionsDialog()],
  ["Bulut yedeği ayarı", async () => {
    if (state.view !== "archive") await switchView("archive");
    el("#backup-mirror-input")?.focus();
    el("#backup-mirror-form")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }],
  ["Görünüm: tema ve yazı boyutu", openAppearance],
  ["Klavye kısayolları  (F1)", openShortcuts],
  ["Başlangıç rehberi", () => el("#open-guide").click()],
  ["Uygulama güncelleme", openUpdate],
];

function installToolsMenu() {
  const nav = el(".main-nav");
  if (!nav || el("#tools-menu-button")) return;
  const button = document.createElement("button");
  button.id = "tools-menu-button";
  button.type = "button";
  button.className = "nav-tab tools-tab";
  button.setAttribute("aria-haspopup", "menu");
  button.setAttribute("aria-expanded", "false");
  button.textContent = "Araçlar ▾";
  nav.append(button);
  const menu = document.createElement("div");
  menu.id = "tools-menu";
  menu.className = "tools-menu";
  menu.setAttribute("role", "menu");
  menu.hidden = true;
  menu.innerHTML = EXTRAS_MENU.map(([label], index) => label === "-"
    ? '<hr role="separator">'
    : `<button type="button" role="menuitem" data-tool="${index}">${escapeHtml(label)}</button>`).join("");
  document.body.append(menu);
  const close = () => { menu.hidden = true; button.setAttribute("aria-expanded", "false"); };
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    if (!menu.hidden) { close(); return; }
    const rect = button.getBoundingClientRect();
    menu.style.left = `${Math.max(6, Math.min(rect.left, window.innerWidth - 290))}px`;
    menu.style.top = `${rect.bottom + 4}px`;
    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    menu.querySelector("button")?.focus();
  });
  menu.addEventListener("click", (event) => {
    const item = event.target.closest("[data-tool]");
    if (!item) return;
    close();
    Promise.resolve().then(() => EXTRAS_MENU[Number(item.dataset.tool)][1]()).catch((error) => toast(error.message, true));
  });
  menu.addEventListener("keydown", (event) => {
    const items = [...menu.querySelectorAll("button")];
    const index = items.indexOf(document.activeElement);
    if (event.key === "ArrowDown") { event.preventDefault(); items[(index + 1) % items.length].focus(); }
    if (event.key === "ArrowUp") { event.preventDefault(); items[(index - 1 + items.length) % items.length].focus(); }
    if (event.key === "Escape") { close(); button.focus(); }
  });
  document.addEventListener("click", (event) => { if (!menu.contains(event.target)) close(); });
  window.addEventListener("resize", close);
}

function extrasShortcuts(event) {
  if (event.key === "F1") {
    event.preventDefault();
    if (!document.querySelector("dialog[open]:not(.hub-embedded)")) openShortcuts();
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase("en-US") === "k") {
    event.preventDefault();
    if (!document.querySelector("dialog[open]:not(.hub-embedded)")) openSearch();
  }
}

function startExtras() {
  installToolsMenu();
  installCellNotes();
  installHistorySearch();
  installReportBlocks();
  installEventApply();
  installBackupMirror().catch(() => {});
  document.addEventListener("keydown", extrasShortcuts);
  setTimeout(() => {
    const loader = el("#app-loader");
    if (loader) { loader.classList.add("done"); setTimeout(() => loader.remove(), 400); }
  }, 15000);
}

// startExtras() controls.js sonunda çağrılır (menü öğeleri eklendikten sonra).
