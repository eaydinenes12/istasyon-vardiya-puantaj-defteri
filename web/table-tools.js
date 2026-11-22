"use strict";

/* Tablo araçları: boş satır gizleme, yoğunluk, Ctrl+tekerlek ölçek, sürüklerken otomatik kaydırma,
   kişiselleştirilebilir kısayollar, ipuçları, vardiya türleri, şablon kitaplığı ve personel listesi. */

const TT_DEFAULT_SHIFT_TYPES = [
  { id: "sabah", label: "Sabah", color: "#e2efda", from: "05:00", to: "10:59" },
  { id: "ara", label: "Ara vardiya", color: "#fce4d6", from: "11:00", to: "13:59" },
  { id: "aksam", label: "Akşam", color: "#ddebf7", from: "14:00", to: "19:59" },
  { id: "gece", label: "Gece", color: "#e4dfec", from: "20:00", to: "04:59" },
  { id: "yarim", label: "Yarım gün", color: "#fff2cc", from: "", to: "" },
];

const tt = { zoomTimer: null, zoomPending: null, scroll: null, recording: null, tipShown: new Set() };

function ttSettings() {
  return state.uiSettings || {};
}

function ttActiveKind() {
  return state.view === "schedule" || state.view === "attendance" ? state.view : null;
}

/* ---------- Görünüm ayarları ---------- */

function ttApplySettings() {
  const settings = ttSettings();
  document.body.classList.toggle("density-compact", settings.density === "compact");
  const hide = el("#schedule-hide-empty");
  if (hide) hide.checked = settings.hide_empty_rows !== false;
  if (settings.tips && settings.tips.enabled === false) ttHideTip();
}

async function ttToggleHideEmpty(value) {
  const next = typeof value === "boolean" ? value : !(ttSettings().hide_empty_rows !== false);
  try {
    await saveUiSettings({ hide_empty_rows: next });
    renderSchedule();
    toast(next ? "Boş satırlar gizlendi; ad ekledikçe yeni satır açılır." : "Tüm personel satırları gösteriliyor.");
  } catch (error) {
    toast(error.message, true);
  }
}

async function ttToggleDensity() {
  const next = ttSettings().density === "compact" ? "comfortable" : "compact";
  try {
    await saveUiSettings({ density: next });
    toast(next === "compact" ? "Sıkı görünüm açıldı." : "Rahat görünüm açıldı.");
  } catch (error) {
    toast(error.message, true);
  }
}

/* ---------- Ölçek (Ctrl + tekerlek, Ctrl + +/-/0) ---------- */

function ttCurrentZoom(kind) {
  if (tt.zoomPending && tt.zoomPending.kind === kind) return tt.zoomPending.value;
  return sheetSettings(kind).zoom || (kind === "schedule" ? 55 : 100);
}

function ttShowZoom(value) {
  let badge = el("#zoom-indicator");
  if (!badge) {
    badge = document.createElement("div");
    badge.id = "zoom-indicator";
    badge.className = "zoom-indicator";
    badge.setAttribute("role", "status");
    document.body.append(badge);
  }
  badge.textContent = `Ölçek %${value}`;
  badge.classList.add("visible");
  clearTimeout(badge._timer);
  badge._timer = setTimeout(() => badge.classList.remove("visible"), 1100);
}

function ttSetZoom(kind, value) {
  if (!kind) return;
  const zoom = Math.max(25, Math.min(150, Math.round(value / 5) * 5));
  const table = el(`#${kind}-table`);
  if (table) table.style.zoom = String(zoom / 100);
  const input = document.querySelector(`#${kind}-view [data-format-zoom]`);
  if (input) input.value = zoom;
  tt.zoomPending = { kind, value: zoom, key: sheetKey(kind) };
  ttShowZoom(zoom);
  clearTimeout(tt.zoomTimer);
  tt.zoomTimer = setTimeout(ttPersistZoom, 700);
}

async function ttPersistZoom() {
  const pending = tt.zoomPending;
  if (!pending) return;
  if (state.formattingBusy) {
    tt.zoomTimer = setTimeout(ttPersistZoom, 400);
    return;
  }
  const settings = structuredClone(state.workbookSettings);
  const current = sheetSettings(pending.kind);
  if ((current.zoom || 0) === pending.value || sheetKey(pending.kind) !== pending.key) {
    tt.zoomPending = null;
    return;
  }
  settings.sheets[pending.key] = structuredClone(current);
  settings.sheets[pending.key].zoom = pending.value;
  tt.zoomPending = null;
  await persistWorkbookSettings(settings, { recordHistory: false });
}

function ttZoomStep(direction) {
  const kind = ttActiveKind();
  if (!kind) return;
  ttSetZoom(kind, direction === 0 ? (kind === "schedule" ? 55 : 100) : ttCurrentZoom(kind) + direction * 5);
}

function ttWheel(event) {
  if (!(event.ctrlKey || event.metaKey)) return;
  const frame = event.target.closest?.("#schedule-view .table-frame, #attendance-view .table-frame");
  if (!frame) return;
  event.preventDefault();
  const kind = frame.closest("#schedule-view") ? "schedule" : "attendance";
  ttSetZoom(kind, ttCurrentZoom(kind) + (event.deltaY < 0 ? 5 : -5));
}

/* ---------- Sürüklerken otomatik kaydırma ---------- */

function ttDragMove(event) {
  if (typeof grid === "undefined" || !grid.dragging || !(event.buttons & 1) || !grid.kind) {
    tt.scroll = null;
    return;
  }
  const frame = gridTable(grid.kind)?.closest(".table-frame");
  if (!frame) return;
  const rect = frame.getBoundingClientRect();
  const edge = 36;
  const speed = (distance) => Math.min(28, Math.max(4, Math.round(distance / 2)));
  let dx = 0;
  let dy = 0;
  if (event.clientX < rect.left + edge) dx = -speed(rect.left + edge - event.clientX);
  else if (event.clientX > rect.right - edge) dx = speed(event.clientX - rect.right + edge);
  if (event.clientY < rect.top + edge) dy = -speed(rect.top + edge - event.clientY);
  else if (event.clientY > rect.bottom - edge) dy = speed(event.clientY - rect.bottom + edge);
  const running = Boolean(tt.scroll);
  tt.scroll = dx || dy ? { frame, dx, dy, x: event.clientX, y: event.clientY } : null;
  if (tt.scroll && !running) requestAnimationFrame(ttScrollTick);
}

function ttScrollTick() {
  const scroll = tt.scroll;
  if (!scroll || !grid.dragging) {
    tt.scroll = null;
    return;
  }
  const { frame } = scroll;
  const beforeLeft = frame.scrollLeft;
  const beforeTop = frame.scrollTop;
  frame.scrollLeft += scroll.dx;
  frame.scrollTop += scroll.dy;
  if (frame.scrollLeft !== beforeLeft || frame.scrollTop !== beforeTop) {
    const rect = frame.getBoundingClientRect();
    const x = Math.min(rect.right - 12, Math.max(rect.left + 60, scroll.x));
    const y = Math.min(rect.bottom - 12, Math.max(rect.top + 40, scroll.y));
    const target = document.elementFromPoint(x, y);
    const node = grid.kind === "attendance"
      ? target?.closest("td")?.querySelector(GRID_ATTENDANCE_SELECTOR)
      : target?.closest(GRID_SCHEDULE_SELECTOR);
    const key = node ? gridKey(grid.kind, node) : null;
    if (key && grid.anchor) {
      grid.dragged = true;
      gridSelectRange(grid.kind, grid.anchor, key);
    }
  }
  requestAnimationFrame(ttScrollTick);
}

/* ---------- Kısayollar ---------- */

const TT_ACTIONS = [
  { id: "save", label: "Açık tabloyu kaydet", combo: "Ctrl+S", builtin: true,
    run: () => { const kind = ttActiveKind(); const button = kind && el(`#save-${kind}`); if (button && !button.disabled) button.click(); } },
  { id: "search", label: "Hızlı personel arama", combo: "Ctrl+K", builtin: true, run: () => openSearch() },
  { id: "help", label: "Kısayol ve ipucu sayfası", combo: "F1", builtin: true, run: () => ttOpenShortcuts() },
  { id: "zoom-in", label: "Tabloyu büyüt", combo: "Ctrl+=", run: () => ttZoomStep(1) },
  { id: "zoom-out", label: "Tabloyu küçült", combo: "Ctrl+-", run: () => ttZoomStep(-1) },
  { id: "zoom-reset", label: "Ölçeği varsayılana döndür", combo: "Ctrl+0", run: () => ttZoomStep(0) },
  { id: "view-schedule", label: "Vardiya sayfasına geç", combo: "Alt+1", run: () => switchView("schedule") },
  { id: "view-attendance", label: "Puantaj sayfasına geç", combo: "Alt+2", run: () => switchView("attendance") },
  { id: "view-archive", label: "Arşiv sayfasına geç", combo: "Alt+3", run: () => switchView("archive") },
  { id: "jump", label: "Vardiya ↔ puantaj arasında geçiş", combo: "Ctrl+J", run: () => ttJump() },
  { id: "personnel", label: "Personel listesi", combo: "Ctrl+Shift+P", run: () => ttOpenPersonnel() },
  { id: "hide-empty", label: "Boş satırları gizle / göster", combo: "Ctrl+Shift+E", run: () => ttToggleHideEmpty() },
  { id: "density", label: "Sıkı / rahat görünüm", combo: "Ctrl+Shift+D", run: () => ttToggleDensity() },
  { id: "shift-types", label: "Vardiya türleri ve renkleri", combo: "Ctrl+Shift+T", run: () => ttOpenShiftTypes() },
  { id: "quick-entries", label: "Hızlı giriş ayarları", combo: "Ctrl+Shift+Q", run: () => el("#open-quick-entry-settings")?.click() },
  { id: "tips", label: "İpuçlarını aç / kapat", combo: "Ctrl+Shift+H", run: () => ttToggleTips() },
  { id: "templates", label: "Şablon kitaplığı", combo: "Ctrl+Shift+L", run: () => ttOpenLibrary() },
];

const TT_FIXED_SHORTCUTS = [
  ["Ctrl + Z / Ctrl + Y", "Geri al / yinele"],
  ["Ctrl + C / Ctrl + V", "Seçili vardiya hücresini kopyala / yapıştır"],
  ["Delete", "Seçili hücreleri temizler"],
  ["1 – 9", "Seçili hücrelere sıradaki hızlı girişi uygular"],
  ["X, H, S, R, Ü, G", "Puantajda kodu doğrudan yazar"],
  ["Ok tuşları / Shift + ok", "Hücreler arasında dolaşır / seçimi genişletir"],
  ["Shift + tıklama", "Aralık seçer"],
  ["Ctrl + tıklama", "Hücreyi seçime ekler / çıkarır"],
  ["Fareyle sürükleme", "Toplu seçim; kenara yaklaşınca tablo kendiliğinden kayar"],
  ["Ctrl + fare tekerleği", "Tablo ölçeğini büyütür / küçültür"],
  ["Enter / çift tıklama", "Hücrenin hızlı giriş menüsünü açar"],
  ["Esc", "Açık pencereyi veya seçimi kapatır"],
];

function ttCodeKey(event) {
  const code = event.code || "";
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit\d$/.test(code)) return code.slice(5);
  if (/^Numpad\d$/.test(code)) return code.slice(6);
  if (/^F([1-9]|1[0-2])$/.test(code)) return code;
  const map = {
    Equal: "=", NumpadAdd: "=", Minus: "-", NumpadSubtract: "-", Period: ".", Comma: ",", Slash: "/",
    Semicolon: ";", Quote: "'", BracketLeft: "[", BracketRight: "]", Enter: "Enter", NumpadEnter: "Enter",
    Delete: "Delete", Home: "Home", End: "End", PageUp: "PageUp", PageDown: "PageDown",
  };
  return map[code] || "";
}

function ttEventCombo(event) {
  // Türkçe klavyede + ve - farklı fiziksel tuşlardadır; karakter üzerinden eşleştir.
  if ((event.ctrlKey || event.metaKey) && !event.altKey && (event.key === "+" || event.key === "-")) {
    return `Ctrl+${event.key === "+" ? "=" : "-"}`;
  }
  const key = ttCodeKey(event);
  if (!key) return "";
  return `${event.ctrlKey || event.metaKey ? "Ctrl+" : ""}${event.altKey ? "Alt+" : ""}${event.shiftKey ? "Shift+" : ""}${key}`;
}

function ttEffectiveCombo(action) {
  const custom = ttSettings().shortcuts || {};
  return Object.prototype.hasOwnProperty.call(custom, action.id) ? custom[action.id] : action.combo;
}

function ttKeydown(event) {
  if (tt.recording) return;
  const combo = ttEventCombo(event);
  if (!combo) return;
  if ((document.querySelector("dialog[open]:not(.hub-embedded)") || event.target.closest?.(".hub-embedded"))
      && combo !== "F1") return;
  const typing = editingText(event.target) || event.target.matches?.("input, select, textarea");
  if (typing && !/^(Ctrl|Alt)\+|^F\d/.test(combo)) return;
  const action = TT_ACTIONS.find((item) => ttEffectiveCombo(item) === combo);
  if (action) {
    if (action.builtin && combo === action.combo) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    Promise.resolve().then(action.run).catch((error) => toast(error.message, true));
    return;
  }
  const remapped = TT_ACTIONS.find((item) => item.builtin && item.combo === combo && ttEffectiveCombo(item) !== combo);
  if (remapped) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }
}

function ttShortcutRows() {
  return TT_ACTIONS.map((action) => {
    const combo = ttEffectiveCombo(action);
    const changed = combo !== action.combo;
    return `<tr data-shortcut="${action.id}"><td>${escapeHtml(action.label)}</td>
      <td><kbd>${combo ? escapeHtml(combo.replaceAll("+", " + ")) : "Kapalı"}</kbd>${changed ? ' <small class="shortcut-changed">özel</small>' : ""}</td>
      <td class="shortcut-actions"><button class="button button-light" type="button" data-shortcut-record>Değiştir</button>
      <button class="button button-light" type="button" data-shortcut-off${combo ? "" : " disabled"}>Kapat</button>
      <button class="button button-light" type="button" data-shortcut-reset${changed ? "" : " disabled"}>Varsayılan</button></td></tr>`;
  }).join("");
}

function ttRenderShortcuts(dialog) {
  const tips = ttSettings().tips || { enabled: true, dismissed: [] };
  dialog.querySelector(".extras-body").innerHTML = `<div class="shortcut-page">
    <p class="dialog-help">Kısayolu değiştirmek için <b>Değiştir</b>'e basıp yeni tuş birleşimine basın. Esc iptal eder. Ayarlar bu veri dosyasında saklanır.</p>
    <table class="archive-table extras-table shortcut-table"><thead><tr><th>İşlem</th><th>Kısayol</th><th></th></tr></thead><tbody>${ttShortcutRows()}</tbody></table>
    <div class="template-actions"><button class="button button-light" type="button" data-shortcut-reset-all>Tüm kısayolları varsayılana döndür</button></div>
    <h3>Sabit tablo kısayolları</h3>
    <table class="archive-table extras-table"><tbody>${TT_FIXED_SHORTCUTS.map(([key, text]) =>
      `<tr><th scope="row"><kbd>${escapeHtml(key)}</kbd></th><td>${escapeHtml(text)}</td></tr>`).join("")}</tbody></table>
    <h3>İpuçları</h3>
    <label class="compact-toggle"><input type="checkbox" data-tips-enabled${tips.enabled !== false ? " checked" : ""}> İşlem sırasında kısa ipuçları göster</label>
    <p class="dialog-help">${(tips.dismissed || []).length} ipucu "bir daha gösterme" ile kapatıldı.
      <button class="button button-light" type="button" data-tips-restore${(tips.dismissed || []).length ? "" : " disabled"}>Kapatılan ipuçlarını geri getir</button></p>
  </div>`;
}

function ttOpenShortcuts() {
  const dialog = extrasDialog("shortcuts-dialog", "Klavye kısayolları ve ipuçları", "Kısayollar kişiselleştirilebilir");
  dialog.ttListeners?.abort();
  dialog.ttListeners = new AbortController();
  const listen = { signal: dialog.ttListeners.signal };
  ttRenderShortcuts(dialog);
  dialog.addEventListener("click", async (event) => {
    const row = event.target.closest("[data-shortcut]");
    const shortcuts = { ...(ttSettings().shortcuts || {}) };
    try {
      if (event.target.closest("[data-shortcut-reset-all]")) {
        await saveUiSettings({ shortcuts: {} });
      } else if (event.target.closest("[data-tips-restore]")) {
        await saveUiSettings({ tips: { enabled: true, dismissed: [] } });
      } else if (row && event.target.closest("[data-shortcut-off]")) {
        shortcuts[row.dataset.shortcut] = "";
        await saveUiSettings({ shortcuts });
      } else if (row && event.target.closest("[data-shortcut-reset]")) {
        delete shortcuts[row.dataset.shortcut];
        await saveUiSettings({ shortcuts });
      } else if (row && event.target.closest("[data-shortcut-record]")) {
        ttRecordShortcut(dialog, row);
        return;
      } else {
        return;
      }
      ttRenderShortcuts(dialog);
    } catch (error) {
      toast(error.message, true);
    }
  }, listen);
  dialog.addEventListener("change", async (event) => {
    if (!event.target.matches("[data-tips-enabled]")) return;
    const tips = ttSettings().tips || { dismissed: [] };
    try {
      await saveUiSettings({ tips: { enabled: event.target.checked, dismissed: tips.dismissed || [] } });
    } catch (error) {
      toast(error.message, true);
    }
  }, listen);
  showExtras(dialog);
}

function ttRecordShortcut(dialog, row) {
  const cell = row.querySelector("kbd");
  cell.textContent = "Tuşlara basın…";
  row.classList.add("recording");
  const finish = () => {
    document.removeEventListener("keydown", listener, true);
    tt.recording = null;
    row.classList.remove("recording");
  };
  const listener = async (event) => {
    if (["Control", "Shift", "Alt", "Meta"].includes(event.key)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    finish();
    if (event.key === "Escape") { ttRenderShortcuts(dialog); return; }
    const combo = ttEventCombo(event);
    if (!combo || (!/^(Ctrl|Alt)\+/.test(combo) && !/^F\d/.test(combo))) {
      toast("Kısayol Ctrl veya Alt ile başlamalı ya da F1–F12 olmalıdır.", true);
      ttRenderShortcuts(dialog);
      return;
    }
    const clash = TT_ACTIONS.find((item) => item.id !== row.dataset.shortcut && ttEffectiveCombo(item) === combo);
    if (clash) {
      toast(`${combo} zaten "${clash.label}" işlemine atanmış.`, true);
      ttRenderShortcuts(dialog);
      return;
    }
    const shortcuts = { ...(ttSettings().shortcuts || {}) };
    const action = TT_ACTIONS.find((item) => item.id === row.dataset.shortcut);
    if (action.combo === combo) delete shortcuts[action.id]; else shortcuts[action.id] = combo;
    try {
      await saveUiSettings({ shortcuts });
      toast(`${action.label}: ${combo}`);
    } catch (error) {
      toast(error.message, true);
    }
    ttRenderShortcuts(dialog);
  };
  tt.recording = listener;
  document.addEventListener("keydown", listener, true);
}

/* ---------- İpuçları ---------- */

const TT_TIPS = [
  { id: "schedule-drag", view: "schedule", text: "Fareyle sürükleyerek birden çok hücre seçin; 1–9 tuşlarıyla hızlı girişi hepsine birden uygulayın. Kenara yaklaşınca tablo kendiliğinden kayar." },
  { id: "schedule-24", view: "schedule", text: "Saatleri 0700, 7 veya 7:30 diye yazabilirsiniz. Gece yarısı bitişi için 24:00 yazın." },
  { id: "schedule-empty", view: "schedule", text: "Boş satırlar gizli; bölümün son boş satırına ad seçtiğinizde yeni satır kendiliğinden açılır (Ctrl+Shift+E)." },
  { id: "schedule-zoom", view: "schedule", text: "Ctrl + fare tekerleğiyle tabloyu büyütüp küçültebilirsiniz. Ctrl+0 ölçeği sıfırlar." },
  { id: "attendance-codes", view: "attendance", text: "Puantaj hücresine X, H, S, R, Ü veya G yazarak kodu doğrudan girin; ok tuşlarıyla ilerleyin." },
  { id: "attendance-jump", view: "attendance", text: "Ctrl+J ile vardiya ve puantaj sayfaları arasında aynı ayda geçiş yapabilirsiniz." },
  { id: "personnel-list", view: "archive", text: "Ctrl+Shift+P ile personel listesini açıp toplu pasife alma, silme ve bölüm atama yapabilirsiniz." },
  { id: "templates-library", view: "templates", text: "Beğendiğiniz biçimi Şablon kitaplığına kaydedin; favori ve varsayılan şablonu tek tıkla uygulayın (Ctrl+Shift+L)." },
];

function ttHideTip() {
  el("#tt-tip")?.remove();
}

function ttShowTip(view) {
  const settings = ttSettings();
  const tips = settings.tips || { enabled: true, dismissed: [] };
  if (tips.enabled === false || document.querySelector("dialog[open], #ws-sample-offer")) return;
  const tip = TT_TIPS.find((item) => item.view === view && !(tips.dismissed || []).includes(item.id) && !tt.tipShown.has(item.id));
  if (!tip) return;
  tt.tipShown.add(tip.id);
  ttHideTip();
  const box = document.createElement("aside");
  box.id = "tt-tip";
  box.className = "tt-tip";
  box.setAttribute("role", "note");
  box.innerHTML = `<b>İpucu</b><p>${escapeHtml(tip.text)}</p><div>
    <button class="button button-light" type="button" data-tip="ok">Anladım</button>
    <button class="button button-light" type="button" data-tip="never">Bir daha gösterme</button>
    <button class="button button-light" type="button" data-tip="off">Tüm ipuçlarını kapat</button></div>`;
  box.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-tip]");
    if (!button) return;
    ttHideTip();
    const current = ttSettings().tips || { enabled: true, dismissed: [] };
    try {
      if (button.dataset.tip === "never") {
        await saveUiSettings({ tips: { enabled: true, dismissed: [...new Set([...(current.dismissed || []), tip.id])] } });
      } else if (button.dataset.tip === "off") {
        await saveUiSettings({ tips: { enabled: false, dismissed: current.dismissed || [] } });
        toast("İpuçları kapatıldı. F1 sayfasından yeniden açabilirsiniz.");
      }
    } catch (error) {
      toast(error.message, true);
    }
  });
  document.body.append(box);
}

async function ttToggleTips() {
  const tips = ttSettings().tips || { enabled: true, dismissed: [] };
  try {
    await saveUiSettings({ tips: { enabled: tips.enabled === false, dismissed: tips.dismissed || [] } });
    toast(tips.enabled === false ? "İpuçları açıldı." : "İpuçları kapatıldı.");
    if (tips.enabled === false) ttShowTip(state.view);
  } catch (error) {
    toast(error.message, true);
  }
}

async function ttJump() {
  if (state.view === "schedule") {
    await switchView("attendance");
    if (state.attendanceMonth !== state.scheduleMonth) toast(`Puantaj ayı: ${state.attendanceMonth}. Vardiya ayı: ${state.scheduleMonth}.`);
  } else {
    await switchView("schedule");
  }
}

/* ---------- Vardiya türleri ---------- */

function ttShiftTypeRow(item) {
  return `<tr data-type-id="${escapeHtml(item.id)}">
    <td><input type="text" maxlength="30" value="${escapeHtml(item.label)}" data-type-field="label" aria-label="Tür adı"></td>
    <td><input type="color" value="${escapeHtml(item.color)}" data-type-field="color" aria-label="Renk"></td>
    <td><input type="text" maxlength="5" inputmode="numeric" placeholder="—" value="${escapeHtml(item.from || "")}" data-type-field="from" aria-label="Başlangıç aralığı başı"></td>
    <td><input type="text" maxlength="5" inputmode="numeric" placeholder="—" value="${escapeHtml(item.to || "")}" data-type-field="to" aria-label="Başlangıç aralığı sonu"></td>
    <td><button class="button button-light" type="button" data-type-remove>Sil</button></td></tr>`;
}

function ttOpenShiftTypes() {
  const settings = ttSettings();
  const dialog = extrasDialog("shift-types-dialog", "Vardiya türleri ve renkleri",
    "Saat yazarken vardiyanın başlangıcına göre tür ve hücre rengi kendiliğinden seçilir");
  const types = settings.shift_types?.length ? settings.shift_types : TT_DEFAULT_SHIFT_TYPES;
  dialog.querySelector(".extras-body").innerHTML = `<form class="shift-types-form" novalidate>
    <table class="archive-table extras-table shift-type-table"><thead><tr><th>Tür</th><th>Renk</th><th>Başlangıç</th><th>…kadar</th><th></th></tr></thead>
      <tbody>${types.map(ttShiftTypeRow).join("")}</tbody></table>
    <div class="template-actions"><button class="button button-light" type="button" data-type-add>Tür ekle</button>
      <button class="button button-light" type="button" data-type-defaults>Varsayılan türlere dön</button></div>
    <label>Yarım gün sayılan en uzun süre <input type="number" min="0" max="12" step="0.5" value="${Number(settings.half_day_max_hours ?? 5)}" data-half-day> saat</label>
    <label class="compact-toggle"><input type="checkbox" data-auto-color${settings.auto_type_color !== false ? " checked" : ""}> Saat girilince hücreyi türün rengine boya</label>
    <p class="dialog-help">Başlangıç saati aralığa düşen vardiya o türü alır (gece aralığı gece yarısını geçebilir). Süresi yarım gün sınırını aşmayan vardiyalar "Yarım gün" sayılır. 8 saati aşan süre her türde fazla mesai olarak hesaplanır.</p>
    <div class="dialog-actions"><button class="button button-primary" type="submit">Kaydet</button></div></form>`;
  const tbody = dialog.querySelector("tbody");
  dialog.querySelector("[data-type-add]").addEventListener("click", () => {
    tbody.insertAdjacentHTML("beforeend", ttShiftTypeRow({ id: `tur-${Date.now().toString(36)}`, label: "Yeni tür", color: "#ffffff", from: "", to: "" }));
  });
  dialog.querySelector("[data-type-defaults]").addEventListener("click", () => {
    tbody.innerHTML = TT_DEFAULT_SHIFT_TYPES.map(ttShiftTypeRow).join("");
  });
  tbody.addEventListener("click", (event) => {
    if (!event.target.closest("[data-type-remove]")) return;
    if (tbody.rows.length <= 1) { toast("En az bir vardiya türü kalmalıdır.", true); return; }
    event.target.closest("tr").remove();
  });
  dialog.querySelector("form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const shiftTypes = [];
    for (const row of tbody.rows) {
      const value = (field) => row.querySelector(`[data-type-field="${field}"]`).value.trim();
      const from = value("from") ? parseTimeText(value("from")) : "";
      const to = value("to") ? parseTimeText(value("to")) : "";
      if (from === null || to === null) { toast(`${value("label")}: saat aralığı geçersiz.`, true); return; }
      shiftTypes.push({ id: row.dataset.typeId, label: value("label"), color: value("color"), from: from || "", to: to || "" });
    }
    try {
      await saveUiSettings({
        shift_types: shiftTypes,
        half_day_max_hours: Number(dialog.querySelector("[data-half-day]").value || 0),
        auto_type_color: dialog.querySelector("[data-auto-color]").checked,
      });
      if (typeof renderShiftTypeOptions === "function") renderShiftTypeOptions();
      toast("Vardiya türleri kaydedildi.");
      dialog.close();
    } catch (error) {
      toast(error.message, true);
    }
  });
  showExtras(dialog);
}

/* ---------- Şablon kitaplığı: varsayılan, favori, sık kullanılan ---------- */

function ttTemplateSheets(kind) {
  const prefix = `${kind}:template:`;
  const sheets = {};
  for (const [key, value] of Object.entries(state.workbookSettings.sheets || {})) {
    if (key.startsWith(prefix)) sheets[key.slice(prefix.length)] = structuredClone(value);
  }
  return sheets;
}

async function ttApplyLibraryItem(item) {
  const settings = structuredClone(state.workbookSettings);
  const prefix = `${item.kind}:template:`;
  for (const key of Object.keys(settings.sheets)) if (key.startsWith(prefix)) delete settings.sheets[key];
  if (item.name !== "__original__") {
    for (const [index, sheet] of Object.entries(item.sheets)) settings.sheets[`${prefix}${index}`] = structuredClone(sheet);
  }
  const saved = await persistWorkbookSettings(settings, { recordHistory: false });
  if (!saved) return false;
  const prefs = structuredClone(ttSettings().templates || { favorites: [], default: "", usage: {} });
  const usageKey = `${item.kind}:${item.name}`;
  prefs.usage = { ...(prefs.usage || {}), [usageKey]: ((prefs.usage || {})[usageKey] || 0) + 1 };
  await saveUiSettings({ templates: prefs }).catch(() => {});
  if (typeof loadTemplateStatus === "function") loadTemplateStatus().catch(() => {});
  return true;
}

async function ttOpenLibrary() {
  const dialog = extrasDialog("template-library-dialog", "Şablon kitaplığı",
    "Tablo biçimlerini adlandırıp saklayın; varsayılan, favori ve sık kullanılanları tek tıkla uygulayın");
  dialog.ttListeners?.abort();
  dialog.ttListeners = new AbortController();
  const listen = { signal: dialog.ttListeners.signal };
  let items = [];
  let ready = [];
  const readyBlock = (kind) => {
    const list = ready.map((item, index) => ({ item, index })).filter(({ item }) => item.kind === kind);
    if (!list.length) return "";
    return `<details class="library-ready" open><summary>Hazır şablonlar <small>(salt okunur örnekler)</small></summary><ul class="library-list">
      ${list.map(({ item, index }) => `<li class="library-item library-ready-item" data-ready-index="${index}">
        <span class="library-name">${escapeHtml(item.name)}<small>${escapeHtml(item.description || "")}</small></span>
        <span class="library-actions"><button class="button button-primary" type="button" data-ready="apply">Uygula</button>
        <button class="button button-light" type="button" data-ready="copy">Kitaplığa kopyala</button></span></li>`).join("")}
      </ul></details>`;
  };
  const render = () => {
    const prefs = ttSettings().templates || { favorites: [], default: "", usage: {} };
    const usage = prefs.usage || {};
    const block = (kind, title) => {
      const own = items.filter((item) => item.kind === kind)
        .map((item) => ({ ...item, key: `${kind}:${item.name}` }))
        .sort((a, b) => (prefs.favorites.includes(b.key) - prefs.favorites.includes(a.key))
          || ((usage[b.key] || 0) - (usage[a.key] || 0)) || a.name.localeCompare(b.name, "tr"));
      const original = { name: "__original__", kind, sheets: {}, key: `${kind}:__original__` };
      const row = (item) => {
        const label = item.name === "__original__" ? "Özgün Excel şablonu (varsayılan biçim)" : item.name;
        const favorite = prefs.favorites.includes(item.key);
        const isDefault = prefs.default === item.key || (!prefs.default && item.name === "__original__");
        return `<li class="library-item${favorite ? " favorite" : ""}" data-library-key="${escapeHtml(item.key)}">
          <button class="icon-button library-star" type="button" data-library="favorite" aria-pressed="${favorite}" title="${favorite ? "Favorilerden çıkar" : "Favorilere ekle"}">${favorite ? "★" : "☆"}</button>
          <span class="library-name">${escapeHtml(label)}${isDefault ? ' <small class="template-badge">Varsayılan</small>' : ""}
            <small>${usage[item.key] ? `${usage[item.key]} kez kullanıldı` : "Henüz kullanılmadı"}${item.created ? ` · ${escapeHtml(new Date(item.created).toLocaleDateString("tr-TR"))}` : ""}</small></span>
          <span class="library-actions"><button class="button button-primary" type="button" data-library="apply">Uygula</button>
          <button class="button button-light" type="button" data-library="default"${isDefault ? " disabled" : ""}>Varsayılan yap</button>
          ${item.name === "__original__" ? "" : `<button class="button button-light" type="button" data-library="overwrite">Güncelle</button>
          <button class="button button-light" type="button" data-library="delete">Sil</button>`}</span></li>`;
      };
      return `<section class="library-block"><h3>${title}</h3><ul class="library-list">${[original, ...own].map(row).join("")}</ul>
        ${readyBlock(kind)}
        <form class="library-save" data-library-kind="${kind}"><input type="text" maxlength="60" placeholder="Şu anki şablon biçimine ad verin" aria-label="${title} şablon adı" required>
        <button class="button button-outline" type="submit">Şu anki biçimi kaydet</button></form></section>`;
    };
    dialog.querySelector(".extras-body").innerHTML = `${block("schedule", "Vardiya listesi")}${block("attendance", "Puantaj listesi")}
      <p class="dialog-help">"Uygula" şablon biçimini değiştirir; ayrı biçimlendirilmiş sayfalar kendi biçimlerini korur. Kayıtlı vardiya ve puantaj verileri değişmez.</p>`;
  };
  const persist = async (next) => {
    items = (await request("/api/template-library", { method: "PUT", body: JSON.stringify({ items: next }) })).items;
  };
  try {
    const library = await request("/api/template-library");
    items = library.items;
    ready = Array.isArray(library.ready) ? library.ready : [];
  } catch (error) {
    toast(error.message, true);
  }
  render();
  dialog.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-ready]");
    if (!button) return;
    const item = ready[Number(button.closest("[data-ready-index]").dataset.readyIndex)];
    if (!item) return;
    button.disabled = true;
    try {
      if (button.dataset.ready === "apply") {
        const current = ttTemplateSheets(item.kind);
        if (Object.keys(current).length && !confirm(`"${item.name}" uygulanınca şu anki şablon biçimi değişir. Kaydetmediyseniz önce "Şu anki biçimi kaydet" ile saklayın. Devam edilsin mi?`)) return;
        if (await ttApplyLibraryItem({ name: item.name, kind: item.kind, sheets: item.sheets })) toast(`"${item.name}" uygulandı. Kayıtlı vardiya ve puantaj verileri değişmedi.`);
      } else {
        let name = item.name.replace(/^Hazır · /, "");
        let suffix = 2;
        while (items.some((entry) => entry.kind === item.kind && entry.name.toLocaleLowerCase("tr") === name.toLocaleLowerCase("tr"))) {
          name = `${item.name.replace(/^Hazır · /, "")} (${suffix++})`;
        }
        await persist([...items, { name, kind: item.kind, sheets: structuredClone(item.sheets), created: new Date().toISOString() }]);
        toast(`"${name}" kitaplığa kopyalandı; artık güncelleyip varsayılan yapabilirsiniz.`);
        render();
      }
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }, listen);
  dialog.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target.closest("[data-library-kind]");
    const kind = form.dataset.libraryKind;
    const name = form.querySelector("input").value.trim();
    const sheets = ttTemplateSheets(kind);
    if (!Object.keys(sheets).length) { toast("Bu tablonun şablonunda henüz biçim değişikliği yok; önce şablonu tabloda düzenleyin.", true); return; }
    try {
      await persist([...items, { name, kind, sheets, created: new Date().toISOString() }]);
      toast(`"${name}" şablon kitaplığına eklendi.`);
      render();
    } catch (error) {
      toast(error.message, true);
    }
  }, listen);
  dialog.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-library]");
    if (!button) return;
    const key = button.closest("[data-library-key]").dataset.libraryKey;
    const [kind, ...rest] = key.split(":");
    const name = rest.join(":");
    const item = name === "__original__" ? { name, kind, sheets: {} } : items.find((entry) => entry.kind === kind && entry.name === name);
    const prefs = structuredClone(ttSettings().templates || { favorites: [], default: "", usage: {} });
    button.disabled = true;
    try {
      if (button.dataset.library === "apply") {
        if (await ttApplyLibraryItem(item)) toast(`${name === "__original__" ? "Özgün şablon" : `"${name}"`} uygulandı.`);
      } else if (button.dataset.library === "favorite") {
        prefs.favorites = prefs.favorites.includes(key) ? prefs.favorites.filter((entry) => entry !== key) : [...prefs.favorites, key];
        await saveUiSettings({ templates: prefs });
      } else if (button.dataset.library === "default") {
        prefs.default = key;
        await saveUiSettings({ templates: prefs });
        toast("Varsayılan şablon seçildi. Uygulamak için Uygula'ya basın.");
      } else if (button.dataset.library === "overwrite") {
        const sheets = ttTemplateSheets(kind);
        if (!Object.keys(sheets).length) throw new Error("Şablonda kaydedilecek biçim yok.");
        if (!confirm(`"${name}" şu anki şablon biçimiyle güncellensin mi?`)) return;
        await persist(items.map((entry) => entry === item ? { ...entry, sheets } : entry));
        toast(`"${name}" güncellendi.`);
      } else if (button.dataset.library === "delete") {
        if (!confirm(`"${name}" kitaplıktan silinsin mi?`)) return;
        await persist(items.filter((entry) => entry !== item));
        prefs.favorites = prefs.favorites.filter((entry) => entry !== key);
        if (prefs.default === key) prefs.default = "";
        await saveUiSettings({ templates: prefs });
      }
      render();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }, listen);
  showExtras(dialog);
}

function ttInstallTemplateButton() {
  const cards = el("#templates-view .template-cards");
  if (!cards || el("#open-template-library")) return;
  const bar = document.createElement("div");
  bar.className = "template-actions template-library-bar";
  bar.innerHTML = '<button class="button button-primary" id="open-template-library" type="button">Şablon kitaplığı: varsayılan, favori, sık kullanılan</button>';
  cards.before(bar);
  bar.querySelector("button").addEventListener("click", () => ttOpenLibrary().catch((error) => toast(error.message, true)));
}

/* ---------- Personel listesi ---------- */

const TT_PERSONNEL_COLUMNS = [
  ["title", "Görev"], ["home_section", "Bölüm"], ["staff_type", "Tür"], ["payroll_code", "Sicil"],
  ["phone", "Telefon"], ["hire_date", "Giriş"], ["exit_date", "Çıkış"], ["color_tag", "Renk"],
];

async function ttOpenPersonnel() {
  const dialog = extrasDialog("personnel-dialog", "Personel listesi",
    "Tüm personel; süzme, kayıtlı listeler ve toplu işlemler");
  dialog.classList.add("personnel-dialog");
  // The dialog element is reused; drop listeners from the previous opening so actions do not fire twice.
  dialog.ttListeners?.abort();
  dialog.ttListeners = new AbortController();
  const listen = { signal: dialog.ttListeners.signal };
  const view = { search: "", status: "active", section: "", staff: "", columns: ["title", "home_section", "staff_type", "payroll_code"] };
  const selected = new Set();
  let people = [];
  const load = async () => {
    people = (await request("/api/personnel-overview")).people;
    for (const id of [...selected]) if (!people.some((person) => person.id === id)) selected.delete(id);
  };
  const sections = () => [...new Set([...SECTIONS, ...people.map((person) => person.home_section).filter(Boolean)])];
  const filtered = () => {
    const query = view.search.toLocaleLowerCase("tr-TR");
    return people.filter((person) => (view.status === "all" || (view.status === "active") === person.active)
      && (!view.section || person.home_section === view.section)
      && (!view.staff || (person.staff_type || "shift") === view.staff)
      && (!query || [person.name, person.title, person.payroll_code, person.phone]
        .some((value) => String(value || "").toLocaleLowerCase("tr-TR").includes(query))));
  };
  const cell = (person, field) => {
    if (field === "staff_type") return (person.staff_type || "shift") === "nonshift" ? "Vardiya dışı" : "Vardiyalı";
    if (field === "color_tag") return person.color_tag ? `<span class="entry-swatch" style="background:${escapeHtml(person.color_tag)}"></span>` : "";
    return escapeHtml(person[field] || "");
  };
  const render = () => {
    const rows = filtered();
    const views = ttSettings().personnel_views || [];
    const columns = TT_PERSONNEL_COLUMNS.filter(([field]) => view.columns.includes(field));
    dialog.querySelector(".extras-body").innerHTML = `<div class="personnel-tools">
      <input type="search" placeholder="Ad, görev, sicil veya telefon ara" value="${escapeHtml(view.search)}" data-p="search" aria-label="Personel ara">
      <select data-p="status" aria-label="Durum"><option value="active"${view.status === "active" ? " selected" : ""}>Aktif</option>
        <option value="passive"${view.status === "passive" ? " selected" : ""}>Pasif</option><option value="all"${view.status === "all" ? " selected" : ""}>Tümü</option></select>
      <select data-p="section" aria-label="Bölüm"><option value="">Tüm bölümler</option>${sections().map((name) =>
        `<option${view.section === name ? " selected" : ""}>${escapeHtml(name)}</option>`).join("")}</select>
      <select data-p="staff" aria-label="Personel türü"><option value="">Tüm türler</option>
        <option value="shift"${view.staff === "shift" ? " selected" : ""}>Vardiyalı</option><option value="nonshift"${view.staff === "nonshift" ? " selected" : ""}>Vardiya dışı</option></select>
      <select data-p="saved" aria-label="Kayıtlı listeler"><option value="">Kayıtlı listeler…</option>${views.map((item, index) =>
        `<option value="${index}">${escapeHtml(item.name)}</option>`).join("")}</select>
      <button class="button button-light" type="button" data-p-act="save-view">Bu listeyi kaydet</button>
      <button class="button button-light" type="button" data-p-act="delete-view"${views.length ? "" : " disabled"}>Kayıtlı listeyi sil</button>
    </div>
    <details class="personnel-columns"><summary>Sütunlar</summary>${TT_PERSONNEL_COLUMNS.map(([field, label]) =>
      `<label><input type="checkbox" data-p-col="${field}"${view.columns.includes(field) ? " checked" : ""}> ${label}</label>`).join("")}</details>
    <div class="personnel-bulk" aria-live="polite"><b>${selected.size}</b> seçili ·
      <button class="button button-light" type="button" data-p-bulk="activate">Aktif yap</button>
      <button class="button button-light" type="button" data-p-bulk="deactivate">Pasife al</button>
      <select data-p-bulk-section aria-label="Bölüm ata"><option value="">Bölüm ata…</option>${sections().map((name) => `<option>${escapeHtml(name)}</option>`).join("")}<option value="__clear__">Bölümü temizle</option></select>
      <select data-p-bulk-staff aria-label="Tür ata"><option value="">Tür ata…</option><option value="shift">Vardiyalı</option><option value="nonshift">Vardiya dışı</option></select>
      <label class="personnel-color">Renk <input type="color" value="#ffd966" data-p-bulk-color></label>
      <button class="button button-light" type="button" data-p-bulk="color">Renk ver</button>
      <button class="button button-light" type="button" data-p-bulk="uncolor">Rengi kaldır</button>
      <button class="button button-danger" type="button" data-p-bulk="delete">Sil</button>
    </div>
    <div class="controls-table-wrap personnel-table-wrap"><table class="archive-table extras-table personnel-table">
      <thead><tr><th><input type="checkbox" data-p-all aria-label="Tümünü seç"${rows.length && rows.every((person) => selected.has(person.id)) ? " checked" : ""}></th>
        <th>Ad soyad</th><th>Durum</th>${columns.map(([, label]) => `<th>${label}</th>`).join("")}<th></th></tr></thead>
      <tbody>${rows.map((person) => `<tr data-person-id="${person.id}" class="${person.active ? "" : "is-passive"}">
        <td><input type="checkbox" data-p-pick${selected.has(person.id) ? " checked" : ""} aria-label="${escapeHtml(person.name)} seç"></td>
        <td>${escapeHtml(person.name)}${person.card_missing?.length
          ? ` <small class="card-missing-badge" title="Eksik kart bilgisi: ${escapeHtml(person.card_missing.join(", "))}">Kart eksik (${person.card_missing.length})</small>` : ""}</td><td>${person.active ? "Aktif" : "Pasif"}</td>
        ${columns.map(([field]) => `<td>${cell(person, field)}</td>`).join("")}
        <td><div class="personnel-row-actions">
          <button class="button button-light" type="button" data-p-home>Ana sayfa</button>
          <button class="button button-light" type="button" data-p-leave>İzin</button>
          <button class="button button-light" type="button" data-p-record>Tutanak</button>
          <button class="button button-light" type="button" data-p-card>Kart</button>
        </div></td></tr>`).join("")
        || `<tr><td colspan="${columns.length + 4}" class="archive-empty">Bu süzgece uyan personel yok.</td></tr>`}</tbody></table></div>
    <p class="dialog-help">${rows.length} kişi listeleniyor (toplam ${people.length}). Pasif personel vardiya ve puantaj listelerinde görünmez; geçmiş kayıtlar korunur.</p>`;
  };
  const bulk = async (action, value) => {
    const ids = [...selected];
    if (!ids.length) { toast("Önce listeden personel seçin.", true); return; }
    let force = false;
    if (action === "delete") {
      if (!confirm(`${ids.length} personel silinsin mi? Kaydı olmayanlar hemen silinir.`)) return;
    }
    let result = await request("/api/personnel/bulk", { method: "POST", body: JSON.stringify({ ids, action, value }) });
    if (action === "delete" && result.blocked.length) {
      const names = people.filter((person) => result.blocked.includes(person.id)).map((person) => person.name).join(", ");
      if (confirm(`${names} için kayıtlı vardiya/puantaj var. Bu kayıtlarla birlikte kalıcı olarak silinsin mi? (Hayır derseniz pasife alınabilir.)`)) {
        force = true;
        const second = await request("/api/personnel/bulk", { method: "POST", body: JSON.stringify({ ids: result.blocked, action, force }) });
        result = { done: [...result.done, ...second.done], blocked: second.blocked };
      }
    }
    toast(`${result.done.length} personel güncellendi.${result.blocked.length ? ` ${result.blocked.length} kişi kayıtları nedeniyle silinmedi.` : ""}`);
    result.done.forEach((id) => { if (action === "delete") selected.delete(id); });
    await load();
    await loadPeople();
    if (typeof gridLoadTags === "function" && (action === "color" || action === "uncolor")) gridLoadTags();
    if (action !== "color" && action !== "uncolor" && action !== "section" && action !== "staff_type") {
      renderSchedule();
      renderAttendance();
    }
    render();
  };
  dialog.addEventListener("input", (event) => {
    if (!event.target.matches('[data-p="search"]')) return;
    view.search = event.target.value;
    const caret = event.target.selectionStart;
    render();
    const input = dialog.querySelector('[data-p="search"]');
    input.focus();
    input.setSelectionRange(caret, caret);
  }, listen);
  dialog.addEventListener("change", async (event) => {
    const target = event.target;
    try {
      if (target.matches('[data-p="status"], [data-p="section"], [data-p="staff"]')) {
        view[target.dataset.p] = target.value;
      } else if (target.matches('[data-p="saved"]')) {
        const saved = (ttSettings().personnel_views || [])[Number(target.value)];
        if (saved) {
          Object.assign(view, { search: "", status: "active", section: "", staff: "" }, saved.filters);
          if (saved.columns?.length) view.columns = [...saved.columns];
        }
      } else if (target.matches("[data-p-col]")) {
        view.columns = target.checked ? [...view.columns, target.dataset.pCol] : view.columns.filter((field) => field !== target.dataset.pCol);
      } else if (target.matches("[data-p-all]")) {
        filtered().forEach((person) => { if (target.checked) selected.add(person.id); else selected.delete(person.id); });
      } else if (target.matches("[data-p-pick]")) {
        const id = Number(target.closest("[data-person-id]").dataset.personId);
        if (target.checked) selected.add(id); else selected.delete(id);
      } else if (target.matches("[data-p-bulk-section]") && target.value) {
        await bulk("section", target.value === "__clear__" ? "" : target.value);
        return;
      } else if (target.matches("[data-p-bulk-staff]") && target.value) {
        await bulk("staff_type", target.value);
        return;
      } else {
        return;
      }
      render();
    } catch (error) {
      toast(error.message, true);
      render();
    }
  }, listen);
  dialog.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-p-act], [data-p-bulk], [data-p-card], [data-p-home], [data-p-leave], [data-p-record]");
    if (!button) return;
    try {
      if (button.matches("[data-p-card], [data-p-home], [data-p-leave], [data-p-record]")) {
        const personId = Number(button.closest("[data-person-id]").dataset.personId);
        dialog.close();
        if (button.matches("[data-p-home]")) await window.personnelHome.open(personId);
        else if (button.matches("[data-p-leave]")) await window.linkedLeave.open({ employee_id: personId, onBack: ttOpenPersonnel });
        else if (button.matches("[data-p-record]")) {
          const today = dateKey(new Date());
          await window.documentWorkflows.openSelection([{ employee_id: personId, date: today, code: "" }],
            { editable: true, onBack: ttOpenPersonnel, backLabel: "← Personel listesine dön" });
        } else await openEmployeeCard(personId);
      } else if (button.dataset.pAct === "save-view") {
        const name = prompt("Liste adı (ör. Vardiya dışı personel):", "");
        if (!name?.trim()) return;
        const views = (ttSettings().personnel_views || []).filter((item) => item.name !== name.trim());
        views.push({ name: name.trim(), filters: { search: view.search, status: view.status, section: view.section, staff: view.staff }, columns: view.columns });
        await saveUiSettings({ personnel_views: views });
        toast(`"${name.trim()}" listesi kaydedildi.`);
        render();
      } else if (button.dataset.pAct === "delete-view") {
        const select = dialog.querySelector('[data-p="saved"]');
        const views = ttSettings().personnel_views || [];
        const index = select.value === "" ? -1 : Number(select.value);
        if (index < 0) { toast("Silmek için önce kayıtlı listelerden birini seçin.", true); return; }
        if (!confirm(`"${views[index].name}" kayıtlı listesi silinsin mi?`)) return;
        await saveUiSettings({ personnel_views: views.filter((_, position) => position !== index) });
        render();
      } else if (button.dataset.pBulk === "color") {
        await bulk("color", dialog.querySelector("[data-p-bulk-color]").value);
      } else if (button.dataset.pBulk === "uncolor") {
        await bulk("color", "");
      } else if (button.dataset.pBulk) {
        await bulk(button.dataset.pBulk);
      }
    } catch (error) {
      toast(error.message, true);
    }
  }, listen);
  try {
    await load();
  } catch (error) {
    toast(error.message, true);
  }
  render();
  showExtras(dialog);
}

/* ---------- Kurulum ---------- */

function startTableTools() {
  document.addEventListener("ui-settings-changed", ttApplySettings);
  document.addEventListener("keydown", ttKeydown, true);
  document.addEventListener("wheel", ttWheel, { passive: false });
  document.addEventListener("mousemove", ttDragMove);
  document.addEventListener("mouseup", () => { tt.scroll = null; });
  el("#schedule-hide-empty")?.addEventListener("change", (event) => ttToggleHideEmpty(event.target.checked));
  ttInstallTemplateButton();
  const quickSettings = el("#quick-entry-settings-dialog .custom-entry-section");
  if (quickSettings && !el("#open-shift-types")) {
    const button = document.createElement("button");
    button.id = "open-shift-types";
    button.type = "button";
    button.className = "button button-light";
    button.textContent = "Vardiya türleri ve renkleri…";
    button.addEventListener("click", () => { el("#quick-entry-settings-dialog").close(); ttOpenShiftTypes(); });
    quickSettings.prepend(button);
  }
  const originalSwitch = window.switchView;
  window.switchView = async function switchViewWithTips(view) {
    const result = await originalSwitch.call(this, view);
    setTimeout(() => ttShowTip(view), 600);
    return result;
  };
  document.addEventListener("vp-sample-offer-dismissed", () => {
    setTimeout(() => ttShowTip(state.view), 250);
  });
  window.openShortcuts = ttOpenShortcuts;
  const index = EXTRAS_MENU.findIndex(([label]) => label.startsWith("Klavye kısayolları"));
  if (index >= 0) EXTRAS_MENU[index] = ["Klavye kısayolları ve ipuçları  (F1)", ttOpenShortcuts];
  const sectionsIndex = EXTRAS_MENU.findIndex(([label]) => label.startsWith("Bölümler ve satırlar"));
  EXTRAS_MENU.splice(sectionsIndex >= 0 ? sectionsIndex : EXTRAS_MENU.length, 0,
    ["Personel listesi  (Ctrl+Shift+P)", ttOpenPersonnel],
    ["Vardiya türleri ve renkleri", ttOpenShiftTypes],
    ["Şablon kitaplığı", ttOpenLibrary]);
  const appearance = EXTRAS_MENU.findIndex(([label]) => label.startsWith("Görünüm"));
  EXTRAS_MENU.splice(appearance >= 0 ? appearance + 1 : EXTRAS_MENU.length, 0,
    ["Sıkı / rahat görünüm  (Ctrl+Shift+D)", ttToggleDensity]);
  setTimeout(() => ttShowTip(state.view), 2500);
}

startTableTools();
