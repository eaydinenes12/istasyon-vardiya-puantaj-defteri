/* Doldurma tutamacı, seçim toplamları, Tab ile öneri kabulü ve biçimi şablona sıfırlama. */

const sheetTools = { filling: false, source: null, kind: null, target: null };

function stMinutes(text) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(String(text || "").trim());
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

function stItemHours(item) {
  if (!item || item.kind !== "work") return 0;
  const start = stMinutes(item.start);
  const end = stMinutes(item.end);
  if (start === null || end === null) return 0;
  let minutes = end - start;
  if (minutes <= 0) minutes += 24 * 60;
  return minutes / 60;
}

function stScheduleItem(cell) {
  const target = scheduleTargetForCell(cell);
  if (!target?.rowId) return null;
  const row = state.schedule.entries.find((entry) => entry.row_id === target.rowId);
  return row ? scheduleRowForDay(row, target.day, false)?.days[target.day] || null : null;
}

function stFormatHours(value) {
  return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/0$/, "").replace(".", ",");
}

/* ---------- Seçim toplamları (durum çubuğu) ---------- */

function stStatusBar() {
  let bar = el("#sheet-status");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "sheet-status";
    bar.className = "sheet-status";
    bar.setAttribute("role", "status");
    bar.setAttribute("aria-live", "polite");
    document.body.append(bar);
  }
  return bar;
}

function stUpdateStatus() {
  const kind = grid.kind;
  const bar = stStatusBar();
  if (!kind || grid.keys.size < 2 || state.view !== kind) {
    bar.hidden = true;
    return;
  }
  const keys = [...grid.keys];
  const parts = [`<strong>${keys.length}</strong> hücre`];
  if (kind === "attendance") {
    const counts = {};
    keys.forEach((key) => {
      const code = String(gridNode(kind, key)?.value || "").trim().toLocaleUpperCase("tr-TR");
      if (code) counts[code] = (counts[code] || 0) + 1;
    });
    const filled = Object.values(counts).reduce((sum, value) => sum + value, 0);
    parts.push(`dolu ${filled}`);
    Object.entries(counts).sort().forEach(([code, count]) => parts.push(`${escapeHtml(code)}: <strong>${count}</strong>`));
  } else {
    let hours = 0;
    let overtime = 0;
    let shifts = 0;
    let rest = 0;
    keys.forEach((key) => {
      const item = stScheduleItem(gridNode(kind, key));
      if (!item) return;
      if (item.kind === "work") {
        const value = stItemHours(item);
        shifts += 1;
        hours += value;
        overtime += Math.max(0, value - 8);
      } else if (item.kind === "weekly_off") rest += 1;
    });
    parts.push(`vardiya ${shifts}`, `toplam <strong>${stFormatHours(hours)}</strong> saat`, `8 saati aşan ${stFormatHours(overtime)} saat`);
    if (rest) parts.push(`hafta tatili ${rest}`);
  }
  bar.innerHTML = parts.join(" · ");
  bar.hidden = false;
}

/* ---------- Doldurma tutamacı ---------- */

function stHandle() {
  let handle = el("#fill-handle");
  if (!handle) {
    handle = document.createElement("div");
    handle.id = "fill-handle";
    handle.className = "fill-handle";
    handle.title = "Sürükleyerek bu hücrenin değerini komşu hücrelere kopyalayın";
    handle.addEventListener("mousedown", stStartFill);
    document.body.append(handle);
  }
  return handle;
}

function stPlaceHandle() {
  const handle = stHandle();
  const kind = grid.kind;
  const key = sheetTools.filling ? sheetTools.source : grid.active;
  const node = kind && key && state.view === kind && !state.formatMode && !document.querySelector("dialog[open]")
    ? gridNode(kind, key) : null;
  const cell = node && gridCellOf(kind, node);
  if (!cell || (!sheetTools.filling && grid.keys.size > 1)) {
    handle.hidden = true;
    return;
  }
  const box = cell.getBoundingClientRect();
  const frame = cell.closest(".table-frame")?.getBoundingClientRect();
  if (!box.width || (frame && (box.right < frame.left || box.bottom < frame.top || box.right > frame.right + 2 || box.bottom > frame.bottom + 2))) {
    handle.hidden = true;
    return;
  }
  handle.style.left = `${Math.round(box.right - 4)}px`;
  handle.style.top = `${Math.round(box.bottom - 4)}px`;
  handle.hidden = false;
}

function stStartFill(event) {
  if (event.button !== 0 || !grid.kind || !grid.active) return;
  event.preventDefault();
  event.stopPropagation();
  sheetTools.filling = true;
  sheetTools.kind = grid.kind;
  sheetTools.source = grid.active;
  sheetTools.target = grid.active;
  document.body.classList.add("filling");
}

function stFillMove(event) {
  if (!sheetTools.filling) return;
  const kind = sheetTools.kind;
  const under = document.elementFromPoint(event.clientX, event.clientY);
  if (!under || !gridTable(kind)?.contains(under)) return;
  const node = kind === "attendance" ? under.closest("td")?.querySelector(GRID_ATTENDANCE_SELECTOR) : under.closest(GRID_SCHEDULE_SELECTOR);
  const key = gridKey(kind, node);
  if (!key || key === sheetTools.target) return;
  sheetTools.target = key;
  grid.anchor = sheetTools.source;
  gridSelectRange(kind, sheetTools.source, key);
}

async function stFillEnd() {
  if (!sheetTools.filling) return;
  sheetTools.filling = false;
  document.body.classList.remove("filling");
  const { kind, source } = sheetTools;
  const targets = [...grid.keys].filter((key) => key !== source);
  if (!targets.length) { stPlaceHandle(); return; }
  try {
    const count = kind === "attendance" ? stFillAttendance(source, targets) : stFillSchedule(source, targets);
    toast(count ? `${count} hücre dolduruldu.` : "Doldurulacak uygun hücre bulunamadı.", !count);
  } catch (error) {
    toast(error.message, true);
  }
  grid.keys = new Set([source]);
  grid.active = source;
  gridPaint();
}

function stFillAttendance(source, targets) {
  if (!state.attendance) return 0;
  const value = String(gridNode("attendance", source)?.value || "").trim();
  targets.forEach((key) => {
    const [personId, date] = key.split("|");
    gridSetAttendanceCode(personId, date, value);
  });
  setAttendanceDirty();
  renderAttendance();
  return targets.length;
}

function stFillSchedule(source, targets) {
  const item = stScheduleItem(gridNode("schedule", source));
  let count = 0;
  grid.batching = true;
  try {
    for (const key of targets) {
      const target = scheduleTargetForCell(gridNode("schedule", key));
      if (!target || (!target.rowId && !target.employeeId)) continue;
      const row = resolveScheduleTarget(target);
      if (!row) continue;
      const owner = scheduleRowForDay(row, target.day, Boolean(item));
      if (!owner) continue;
      if (item) owner.days[target.day] = structuredClone(item);
      else delete owner.days[target.day];
      if (target.day.slice(0, 7) !== state.scheduleMonth) state.neighborDirty.add(target.day.slice(0, 7));
      count += 1;
    }
  } finally {
    grid.batching = false;
  }
  if (count) setScheduleDirty();
  renderSchedule();
  return count;
}

/* ---------- Akıllı öneri: geçen haftanın aynı günü (Tab ile kabul) ---------- */

function stSuggestion(cell) {
  const target = scheduleTargetForCell(cell);
  if (!target?.rowId || stScheduleItem(cell)) return null;
  const row = state.schedule.entries.find((entry) => entry.row_id === target.rowId);
  const previous = new Date(`${target.day}T12:00:00`);
  previous.setDate(previous.getDate() - 7);
  const day = dateKey(previous);
  const item = row && scheduleRowForDay(row, day, false)?.days[day];
  return item ? { target, item } : null;
}

function stSuggestionLabel(item) {
  if (item.kind === "work") return `${item.start}–${item.end === "00:00" ? "24:00" : item.end}`;
  return scheduleKindLabel(item);
}

function stShowSuggestion(cell) {
  document.querySelectorAll(".st-suggested").forEach((node) => node.classList.remove("st-suggested"));
  const suggestion = cell && stSuggestion(cell);
  if (!suggestion) return;
  cell.classList.add("st-suggested");
  cell.dataset.suggestion = `Öneri: ${stSuggestionLabel(suggestion.item)} · Tab`;
}

function stScheduleKeydown(event) {
  if (event.key !== "Tab" || event.shiftKey || event.ctrlKey || event.altKey || state.formatMode) return;
  const cell = event.target.closest?.(GRID_SCHEDULE_SELECTOR);
  if (!cell || event.target !== cell) return;
  const suggestion = stSuggestion(cell);
  if (!suggestion) return;
  const row = resolveScheduleTarget(suggestion.target);
  const owner = row && scheduleRowForDay(row, suggestion.target.day, true);
  if (!owner) return;
  owner.days[suggestion.target.day] = structuredClone(suggestion.item);
  setScheduleDirty();
  renderSchedule();
  toast(`Öneri uygulandı: ${stSuggestionLabel(suggestion.item)}`);
}

/* ---------- Biçimi şablona sıfırla ---------- */

async function stResetFormat(kind = state.view) {
  if (kind !== "schedule" && kind !== "attendance") {
    toast("Önce Vardiya veya Puantaj sayfasını açın.", true);
    return;
  }
  const key = sheetKey(kind);
  const isTemplate = state.templateEdit === kind;
  if (!state.workbookSettings.sheets[key]) {
    toast(isTemplate ? "Şablon zaten orijinal biçiminde." : "Bu sayfada şablondan farklı bir biçim yok.");
    return;
  }
  const question = isTemplate
    ? "Şablondaki tüm biçim değişiklikleri (renk, kenarlık, birleştirme, boyut) orijinal Excel şablonuna döndürülecek. Devam edilsin mi?"
    : "Bu sayfaya özel biçimler silinecek ve sayfa şablon biçimine dönecek. Veriler (vardiya / puantaj) değişmez. Devam edilsin mi?";
  if (!window.confirm(question)) return;
  const settings = structuredClone(state.workbookSettings);
  delete settings.sheets[key];
  if (await persistWorkbookSettings(settings)) {
    if (kind === "schedule") renderSchedule(); else renderAttendance();
    toast("Biçim şablona döndürüldü. Geri almak için Ctrl+Z kullanabilirsiniz.");
  }
}

/* ---------- Kurulum ---------- */

function startSheetTools() {
  const originalPaint = gridPaint;
  gridPaint = function sheetToolsPaint(...args) {
    const result = originalPaint.apply(this, args);
    stUpdateStatus();
    stPlaceHandle();
    return result;
  };
  document.addEventListener("mousemove", stFillMove);
  document.addEventListener("mouseup", () => { stFillEnd(); });
  document.addEventListener("scroll", stPlaceHandle, true);
  window.addEventListener("resize", stPlaceHandle);
  document.addEventListener("focusin", (event) => {
    const cell = event.target.closest?.(GRID_SCHEDULE_SELECTOR);
    if (cell && event.target === cell) {
      grid.active = gridKey("schedule", cell);
      if (!grid.keys.size) { grid.kind = "schedule"; grid.keys = new Set([grid.active]); }
      stShowSuggestion(cell);
    } else {
      stShowSuggestion(null);
    }
    requestAnimationFrame(stPlaceHandle);
  });
  el("#schedule-table")?.addEventListener("keydown", stScheduleKeydown, true);
  ["#schedule-table", "#attendance-table"].forEach((selector) => {
    const table = el(selector);
    if (table) new MutationObserver(() => requestAnimationFrame(stPlaceHandle)).observe(table, { childList: true });
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-reset-format]")) stResetFormat();
  });
}

const stSectionsIndex = EXTRAS_MENU.findIndex(([label]) => label.startsWith("Bölümler ve satırlar"));
EXTRAS_MENU.splice(stSectionsIndex >= 0 ? stSectionsIndex + 1 : EXTRAS_MENU.length, 0,
  ["Biçimi şablona sıfırla", () => stResetFormat()]);
document.addEventListener("DOMContentLoaded", startSheetTools);
