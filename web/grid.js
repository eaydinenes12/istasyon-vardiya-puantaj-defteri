/* Excel benzeri klavye girişi, toplu hücre seçimi ve personel renk etiketleri. */

const grid = {
  kind: null,
  keys: new Set(),
  anchor: null,
  active: null,
  dragging: false,
  dragged: false,
  suppressClick: false,
  pendingFocus: null,
  batching: false,
  tags: {},
  protectedCells: new Set(),
  protectionMonth: "",
  protectionState: "unknown",
};
const gridSelections = new Map();

const GRID_ATTENDANCE_CODES = ["X", "H", "S", "R", "Ü", "G"];
const GRID_ATTENDANCE_SELECTOR = 'input[data-action="attendance-code"]';
const GRID_SCHEDULE_SELECTOR = "td[data-shift-day]";

function gridTable(kind) {
  return el(kind === "attendance" ? "#attendance-table" : "#schedule-table");
}

function gridKey(kind, node) {
  if (!node) return null;
  if (kind === "attendance") {
    const input = node.matches(GRID_ATTENDANCE_SELECTOR) ? node : node.querySelector(GRID_ATTENDANCE_SELECTOR);
    return input ? `${input.dataset.person}|${input.dataset.day}` : null;
  }
  const cell = node.closest(GRID_SCHEDULE_SELECTOR);
  return cell ? `${cell.dataset.shiftDay}|${cell.dataset.shiftSection}|${cell.dataset.shiftSlot}` : null;
}

function gridNode(kind, key) {
  if (!key) return null;
  const parts = key.split("|").map((part) => CSS.escape(part));
  if (kind === "attendance") {
    return gridTable(kind).querySelector(`${GRID_ATTENDANCE_SELECTOR}[data-person="${parts[0]}"][data-day="${parts[1]}"]`);
  }
  return gridTable(kind).querySelector(`td[data-shift-day="${parts[0]}"][data-shift-section="${parts[1]}"][data-shift-slot="${parts[2]}"]`);
}

function gridCellOf(kind, node) {
  if (!node) return null;
  return kind === "attendance" ? node.closest("td") : node.closest(GRID_SCHEDULE_SELECTOR);
}

function gridAllNodes(kind) {
  return [...gridTable(kind).querySelectorAll(kind === "attendance" ? GRID_ATTENDANCE_SELECTOR : GRID_SCHEDULE_SELECTOR)];
}

function gridCenter(node) {
  const box = gridCellOf(grid.kind, node)?.getBoundingClientRect() || node.getBoundingClientRect();
  return { x: box.left + box.width / 2, y: box.top + box.height / 2, box };
}

function gridClearSelection() {
  if (grid.kind) gridSelections.delete(grid.kind);
  grid.keys.clear();
  grid.anchor = null;
  grid.kind = null;
  gridPaint();
}

function gridSelectRange(kind, anchorKey, toKey) {
  const from = gridNode(kind, anchorKey);
  const to = gridNode(kind, toKey);
  if (!from || !to) return;
  grid.kind = kind;
  const a = gridCenter(from).box;
  const b = gridCenter(to).box;
  const left = Math.min(a.left, b.left) - 1;
  const right = Math.max(a.right, b.right) + 1;
  const top = Math.min(a.top, b.top) - 1;
  const bottom = Math.max(a.bottom, b.bottom) + 1;
  grid.keys = new Set(gridAllNodes(kind).filter((node) => {
    const point = gridCenter(node);
    return point.x >= left && point.x <= right && point.y >= top && point.y <= bottom;
  }).map((node) => gridKey(kind, node)));
  gridPaint();
}

function gridPaint() {
  document.querySelectorAll(".grid-selected").forEach((node) => node.classList.remove("grid-selected"));
  if (grid.kind) {
    grid.keys.forEach((key) => gridCellOf(grid.kind, gridNode(grid.kind, key))?.classList.add("grid-selected"));
  }
  gridRenderBar();
}

function gridRenderBar() {
  let bar = el("#grid-selection-bar");
  const size = grid.kind ? grid.keys.size : 0;
  if (size < 2 || state.formatMode || state.view !== grid.kind) {
    bar?.remove();
    return;
  }
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "grid-selection-bar";
    bar.className = "grid-selection-bar";
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", "Seçili hücrelere toplu giriş");
    document.body.append(bar);
    bar.addEventListener("mousedown", (event) => event.preventDefault());
    bar.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-grid-apply], button[data-grid-clear], button[data-grid-close]");
      if (!button) return;
      if (button.dataset.gridClose) { gridClearSelection(); return; }
      gridApply(button.dataset.gridClear ? "" : button.dataset.gridApply).catch((error) => toast(error.message, true));
    });
  }
  const rows = gridSelectedRows();
  const people = new Set(rows.map((row) => row.personId).filter(Boolean));
  const days = new Set(rows.map((row) => row.day).filter(Boolean));
  const empty = rows.filter((row) => row.empty).length;
  const protectedCount = rows.filter((row) => row.protected).length;
  const protectionText = grid.kind === "attendance" ? "0"
    : grid.protectionState === "ready" ? String(protectedCount)
      : grid.protectionState === "error" ? "bilinmiyor" : "yükleniyor";
  const buttons = grid.kind === "attendance"
    ? GRID_ATTENDANCE_CODES.map((code) => `<button type="button" class="grid-bar-chip" data-grid-apply="${code}">${code}</button>`).join("")
    : allQuickEntries().slice(0, 10).map((entry) => `<button type="button" class="grid-bar-chip" data-grid-apply="${escapeHtml(quickEntryKey(entry))}" title="${escapeHtml(entry.label)}" style="--chip:${escapeHtml(entry.color || "#ffffff")}">${escapeHtml(entry.label)}</button>`).join("");
  bar.innerHTML = `<strong>${size} hücre seçili</strong>
    <span class="grid-selection-summary">${people.size} personel · ${days.size} gün · Boş hücre: ${empty} · Korunan hücre: ${protectionText}</span>${buttons}
    <button type="button" class="grid-bar-chip grid-bar-clear" data-grid-clear="1">Temizle</button>
    <button type="button" class="grid-bar-close" data-grid-close="1" aria-label="Seçimi kaldır" title="Seçimi kaldır (Esc)">×</button>`;
  if (grid.kind === "schedule") gridLoadProtection();
}

/* ---------- Toplu uygulama ---------- */

function gridSetAttendanceCode(personId, date, rawCode) {
  const code = String(rawCode || "").trim().toLocaleUpperCase("tr-TR");
  const overrides = state.attendance.overrides;
  const person = state.attendance.rows.find((item) => String(item.employee_id) === String(personId));
  if (!overrides[date]) overrides[date] = {};
  if (code) {
    overrides[date][personId] = code;
    if (person) { person.codes[date] = code; person.sources[date] = "manual"; }
  } else {
    delete overrides[date][personId];
    if (person) {
      const automatic = person.automatic_codes?.[date];
      if (automatic) { person.codes[date] = automatic; person.sources[date] = "schedule"; }
      else { delete person.codes[date]; delete person.sources[date]; }
    }
  }
  if (!Object.keys(overrides[date]).length) delete overrides[date];
}

async function gridApplySchedule(keys, entry) {
  const cells = keys.map((key) => gridNode("schedule", key)).filter(Boolean);
  const targets = cells.map((cell) => scheduleTargetForCell(cell)).filter((target) => target && (target.rowId || target.employeeId));
  if (!targets.length) throw new Error("Seçili satırlarda personel yok. Önce Personel listesinden ad seçin.");
  grid.batching = true;
  state.applyingEditHistory = true;
  let applied = 0;
  try {
    for (const target of targets) {
      if (entry.id === "clear" && !target.rowId) continue;
      const row = resolveScheduleTarget(target);
      if (row && await applyQuickEntryToCell(row, target.day, entry)) applied += 1;
    }
  } finally {
    state.applyingEditHistory = false;
    grid.batching = false;
  }
  if (applied) setScheduleDirty();
  renderSchedule();
  return applied;
}

function gridSelectedRows() {
  if (!grid.kind) return [];
  return [...grid.keys].map((key) => {
    const node = gridNode(grid.kind, key);
    if (!node) return null;
    if (grid.kind === "attendance") {
      const input = node.matches(GRID_ATTENDANCE_SELECTOR) ? node : node.querySelector(GRID_ATTENDANCE_SELECTOR);
      if (!input) return null;
      const person = state.attendance?.rows.find((item) => String(item.employee_id) === input.dataset.person);
      return {
        personId: input.dataset.person,
        person: person?.name || "Personel",
        day: input.dataset.day,
        current: input.value.trim() || "Boş",
        empty: !input.value.trim(),
        protected: false,
      };
    }
    const target = scheduleTargetForCell(node);
    const label = node.querySelector(".shift-slot-label")?.textContent.trim();
    const times = [...node.querySelectorAll("input[data-time]")].map((input) => input.value).filter(Boolean);
    const current = times.length ? times.join("–") : label || "Boş";
    return {
      personId: target?.employeeId ? String(target.employeeId) : "",
      person: state.people.find((item) => item.id === target?.employeeId)?.name || "Personel seçilmedi",
      day: target?.day || "",
      current,
      empty: !target?.employeeId || !times.length && (!label || ["Hızlı giriş seç", "Vardiya eksik"].includes(label)),
      protected: Boolean(target?.rowId && grid.protectedCells.has(`${target.rowId}|${target.day}`)),
    };
  }).filter(Boolean);
}

function gridLoadProtection() {
  const month = state.scheduleMonth;
  if (!month || grid.protectionMonth === month) return;
  grid.protectionMonth = month;
  grid.protectionState = "loading";
  request(`/api/schedule/protection?month=${encodeURIComponent(month)}`).then(({ cells }) => {
    if (state.scheduleMonth !== month) return;
    grid.protectedCells = new Set((cells || []).map((cell) => `${cell.row_id}|${cell.day}`));
    grid.protectionState = "ready";
    gridRenderBar();
  }).catch((error) => {
    if (state.scheduleMonth !== month) return;
    grid.protectionState = "error";
    gridRenderBar();
    toast(`Korumalı hücreler doğrulanamadı: ${error.message}`, true);
  });
}

function gridOpenBulkPreview(value) {
  const rows = gridSelectedRows();
  const stats = {
    people: new Set(rows.map((row) => row.personId).filter(Boolean)).size,
    days: new Set(rows.map((row) => row.day).filter(Boolean)).size,
    empty: rows.filter((row) => row.empty).length,
    protected: rows.filter((row) => row.protected).length,
  };
  const next = grid.kind === "attendance" ? (value || "Boş")
    : value ? quickEntryByKey(value)?.label || value : "Temizle";
  const dialog = extrasDialog("grid-bulk-preview-dialog", "Toplu işlemi doğrula",
    "Seçili kişi ve günleri büyütülmüş listede gözden geçirin; onay vermeden hiçbir hücre değişmez.");
  const body = dialog.querySelector(".extras-body");
  const protectionUnavailable = grid.kind === "schedule" && grid.protectionState !== "ready";
  body.innerHTML = `<div class="grid-bulk-summary" aria-label="Seçim özeti">
      <strong>${stats.people} personel</strong><strong>${stats.days} gün</strong>
      <strong>${stats.empty} boş hücre</strong><strong>${protectionUnavailable ? "Koruma bilinmiyor" : `${stats.protected} korumalı hücre`}</strong>
    </div>
    <p class="dialog-help">${grid.kind === "attendance" ? "Puantaj kodları çalışma saati toplamı olarak yorumlanmaz." : `Uygulanacak işlem: ${escapeHtml(next)}.`}</p>
    ${stats.protected ? `<p class="controls-warn-text" role="alert">${stats.protected} korumalı hücre seçili. Korumalı hücreleri seçimden çıkarın.</p>` : ""}
    ${protectionUnavailable ? '<p class="controls-warn-text" role="alert">Koruma durumu doğrulanamadı. Güvenli uygulama için işlem kapalı.</p>' : ""}
    <div class="grid-bulk-preview-scroll"><table class="archive-table extras-table grid-bulk-preview-table">
      <thead><tr><th>Personel</th><th>Gün</th><th>Mevcut</th><th>Yeni</th><th>Koruma</th></tr></thead>
      <tbody>${rows.slice(0, 60).map((row) => `<tr><td>${escapeHtml(row.person)}</td><td>${escapeHtml(row.day)}</td>
        <td>${escapeHtml(row.current)}</td><td>${escapeHtml(next)}</td>
        <td>${grid.kind === "attendance" ? "—" : row.protected ? "Korumalı" : protectionUnavailable ? "Bilinmiyor" : "Açık"}</td></tr>`).join("")}</tbody>
    </table>${rows.length > 60 ? `<p class="dialog-help">İlk 60 hücre gösteriliyor; toplam ${rows.length} hücre seçili.</p>` : ""}</div>`;
  const actions = dialog.querySelector(".extras-actions");
  actions.replaceChildren();
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "button button-light";
  cancel.textContent = "Vazgeç";
  cancel.addEventListener("click", () => dialog.close());
  const apply = document.createElement("button");
  apply.type = "button";
  apply.className = "button button-primary";
  apply.textContent = "Onayla ve uygula";
  apply.disabled = protectionUnavailable || !stats.people || stats.protected > 0;
  apply.addEventListener("click", () => {
    dialog.close();
    gridApplyNow(value).catch((error) => toast(error.message, true));
  });
  actions.append(cancel, apply);
  const targets = [...grid.keys].map((key) => gridCellOf(grid.kind, gridNode(grid.kind, key))).filter(Boolean);
  targets.forEach((cell) => cell.classList.add("bulk-preview-target"));
  dialog.addEventListener("close", () => targets.forEach((cell) => cell.classList.remove("bulk-preview-target")), { once: true });
  showExtras(dialog);
}

async function gridApplyNow(value) {
  if (!grid.kind || !grid.keys.size) return;
  const keys = [...grid.keys];
  if (grid.kind === "attendance") {
    if (!state.attendance) return;
    keys.forEach((key) => {
      const [personId, date] = key.split("|");
      gridSetAttendanceCode(personId, date, value);
    });
    setAttendanceDirty();
    renderAttendance();
    toast(value ? `${keys.length} hücreye ${value.toLocaleUpperCase("tr-TR")} yazıldı.` : `${keys.length} hücre temizlendi.`);
    return;
  }
  const entry = value ? quickEntryByKey(value) : { id: "clear" };
  if (!entry) throw new Error("Vardiya seçeneği bulunamadı.");
  const applied = await gridApplySchedule(keys, entry);
  toast(entry.id === "clear" ? `${applied} hücre temizlendi.` : `${applied} hücreye “${entry.label}” uygulandı.`);
}

async function gridApply(value) {
  if (!grid.kind || !grid.keys.size) return;
  if (grid.keys.size > 1) {
    gridOpenBulkPreview(value);
    return;
  }
  return gridApplyNow(value);
}

/* ---------- Puantaj: Excel gibi klavye ---------- */

function gridAttendanceNeighbor(input, rowStep, columnStep) {
  const rows = [...gridTable("attendance").querySelectorAll("tr")]
    .map((row) => [...row.querySelectorAll(GRID_ATTENDANCE_SELECTOR)])
    .filter((inputs) => inputs.length);
  const rowIndex = rows.findIndex((inputs) => inputs.includes(input));
  if (rowIndex < 0) return null;
  let column = rows[rowIndex].indexOf(input) + columnStep;
  let row = rowIndex + rowStep;
  if (columnStep && (column < 0 || column >= rows[rowIndex].length)) {
    row = rowIndex + (column < 0 ? -1 : 1);
    if (row < 0 || row >= rows.length) return null;
    column = column < 0 ? rows[row].length - 1 : 0;
  }
  if (row < 0 || row >= rows.length) return null;
  return rows[row][Math.min(column, rows[row].length - 1)] || null;
}

function gridFocusAttendance(key) {
  const input = gridNode("attendance", key);
  if (!input) return null;
  input.focus({ preventScroll: false });
  input.select();
  grid.active = key;
  return input;
}

function gridMoveAttendance(input, rowStep, columnStep, extend) {
  const next = gridAttendanceNeighbor(input, rowStep, columnStep);
  if (!next) return;
  const fromKey = gridKey("attendance", input);
  const toKey = gridKey("attendance", next);
  if (input.value !== input.defaultValue) {
    grid.pendingFocus = { kind: "attendance", key: toKey };
    handleAttendanceEdit({ target: input });
    grid.pendingFocus = null;
  }
  gridFocusAttendance(toKey);
  if (extend) {
    grid.anchor = grid.anchor || fromKey;
    gridSelectRange("attendance", grid.anchor, toKey);
  } else if (grid.keys.size) {
    gridClearSelection();
  }
}

function gridAttendanceKeydown(event) {
  if (state.formatMode) return;
  const input = event.target.closest?.(GRID_ATTENDANCE_SELECTOR);
  if (!input) return;
  const { key } = event;
  const mod = event.ctrlKey || event.metaKey;
  if (key === "Escape" && grid.keys.size) {
    event.preventDefault();
    gridClearSelection();
    return;
  }
  if (mod && key === "Enter" && grid.kind === "attendance" && grid.keys.size > 1) {
    event.preventDefault();
    event.stopPropagation();
    gridApply(input.value).catch((error) => toast(error.message, true));
    return;
  }
  if ((key === "Delete") && grid.kind === "attendance" && grid.keys.size > 1) {
    event.preventDefault();
    event.stopPropagation();
    gridApply("").catch((error) => toast(error.message, true));
    return;
  }
  if (mod || event.altKey) return;
  const code = input.value.trim().toLocaleUpperCase("tr-TR");
  if (key === "Enter" && !event.shiftKey && ["G", "S", "Ü"].includes(code) && input.value === input.defaultValue) return;
  let move = null;
  if (key === "Enter") move = event.shiftKey ? [-1, 0] : [1, 0];
  else if (key === "Tab") move = event.shiftKey ? [0, -1] : [0, 1];
  else if (key === "ArrowUp") move = [-1, 0];
  else if (key === "ArrowDown") move = [1, 0];
  else if (key === "ArrowLeft" || key === "ArrowRight") {
    const whole = input.selectionStart === 0 && input.selectionEnd === input.value.length;
    const atEdge = key === "ArrowLeft" ? input.selectionStart === 0 && input.selectionEnd === 0 : input.selectionStart === input.value.length;
    if (!whole && !atEdge && !event.shiftKey) return;
    move = [0, key === "ArrowLeft" ? -1 : 1];
  }
  if (!move) return;
  event.preventDefault();
  event.stopPropagation();
  const extend = event.shiftKey && key.startsWith("Arrow");
  gridMoveAttendance(input, move[0], move[1], extend);
}

/* ---------- Vardiya: harfle hızlı giriş ---------- */

function gridScheduleKeydown(event) {
  if (state.formatMode || document.querySelector("dialog[open]")) return;
  const cell = event.target.closest?.(GRID_SCHEDULE_SELECTOR);
  if (!cell || event.target !== cell) return;
  const mod = event.ctrlKey || event.metaKey;
  const { key } = event;
  if (key === "Escape" && grid.keys.size) {
    event.preventDefault();
    gridClearSelection();
    return;
  }
  if (event.shiftKey && key.startsWith("Arrow") && !mod) {
    const before = gridKey("schedule", cell);
    const fake = { key, target: cell, preventDefault: () => event.preventDefault() };
    if (moveWorkbookFocus(fake, GRID_SCHEDULE_SELECTOR)) {
      event.stopPropagation();
      grid.anchor = grid.kind === "schedule" && grid.anchor ? grid.anchor : before;
      grid.active = gridKey("schedule", document.activeElement);
      gridSelectRange("schedule", grid.anchor, grid.active);
    }
    return;
  }
  const multi = grid.kind === "schedule" && grid.keys.size > 1;
  if (!mod && (key === "Delete" || key === "Backspace") && multi) {
    event.preventDefault();
    event.stopPropagation();
    gridApply("").catch((error) => toast(error.message, true));
    return;
  }
  if (key.startsWith("Arrow") && !event.shiftKey && grid.keys.size) gridClearSelection();
  if (mod || event.altKey || key.length !== 1 || key === " ") return;
  const entries = allQuickEntries();
  let matches = [];
  if (/^[1-9]$/.test(key)) matches = entries[Number(key) - 1] ? [entries[Number(key) - 1]] : [];
  else {
    const letter = key.toLocaleUpperCase("tr-TR");
    matches = entries.filter((entry) => String(entry.code || "").toLocaleUpperCase("tr-TR") === letter);
  }
  if (!matches.length) return;
  event.preventDefault();
  event.stopPropagation();
  if (matches.length > 1) {
    openCellQuickEntries(cell);
    return;
  }
  const keys = multi ? [...grid.keys] : [gridKey("schedule", cell)];
  grid.active = gridKey("schedule", cell);
  gridApplySchedule(keys, matches[0])
    .then((applied) => { if (multi) toast(`${applied} hücreye “${matches[0].label}” uygulandı.`); })
    .catch((error) => toast(error.message, true));
}

/* ---------- Fare ile seçim ---------- */

function gridKindForTable(table) {
  return table?.id === "attendance-table" ? "attendance" : table?.id === "schedule-table" ? "schedule" : null;
}

function gridMouseDown(event) {
  if (state.formatMode || event.button !== 0) return;
  const kind = gridKindForTable(event.currentTarget);
  const node = kind === "attendance" ? event.target.closest("td")?.querySelector(GRID_ATTENDANCE_SELECTOR) : event.target.closest(GRID_SCHEDULE_SELECTOR);
  if (!node || event.target.matches("input[type='time'], input[data-time], select, button")) return;
  const key = gridKey(kind, node);
  grid.dragged = false;
  if (event.shiftKey && grid.kind === kind && grid.anchor) {
    event.preventDefault();
    grid.suppressClick = true;
    gridSelectRange(kind, grid.anchor, key);
    return;
  }
  if (event.ctrlKey || event.metaKey) {
    event.preventDefault();
    grid.suppressClick = true;
    if (grid.kind !== kind) { grid.keys.clear(); grid.kind = kind; }
    if (!grid.keys.size && grid.active && gridNode(kind, grid.active)) grid.keys.add(grid.active);
    if (grid.keys.has(key)) grid.keys.delete(key); else grid.keys.add(key);
    grid.anchor = key;
    gridPaint();
    return;
  }
  grid.kind = kind;
  grid.anchor = key;
  grid.active = key;
  grid.keys = new Set([key]);
  grid.dragging = true;
  if (kind === "attendance") grid.pendingFocus = { kind, key };
  gridPaint();
  setTimeout(() => { grid.pendingFocus = null; }, 0);
}

function gridMouseOver(event) {
  if (!grid.dragging || !(event.buttons & 1)) return;
  const kind = gridKindForTable(event.currentTarget);
  if (kind !== grid.kind) return;
  const node = kind === "attendance" ? event.target.closest("td")?.querySelector(GRID_ATTENDANCE_SELECTOR) : event.target.closest(GRID_SCHEDULE_SELECTOR);
  const key = gridKey(kind, node);
  if (!key || (key === grid.anchor && !grid.dragged)) return;
  grid.dragged = true;
  window.getSelection()?.removeAllRanges();
  gridSelectRange(kind, grid.anchor, key);
}

function gridMouseUp() {
  if (grid.dragging && grid.dragged) grid.suppressClick = true;
  grid.dragging = false;
}

function gridClickCapture(event) {
  if (!grid.suppressClick) return;
  grid.suppressClick = false;
  event.preventDefault();
  event.stopImmediatePropagation();
}

/* ---------- Renk etiketi ---------- */

async function gridLoadTags() {
  try {
    grid.tags = (await request("/api/person-tags")).tags || {};
  } catch {
    grid.tags = {};
  }
  gridDecorate();
}

function gridDecorate() {
  const tagFor = (id) => grid.tags[String(id)]?.color || "";
  const attendance = el("#attendance-table");
  if (attendance && state.attendance) {
    attendance.querySelectorAll("tr").forEach((row) => {
      const input = row.querySelector(GRID_ATTENDANCE_SELECTOR);
      if (!input) return;
      const person = state.attendance.rows.find((item) => String(item.employee_id) === input.dataset.person);
      const nameCell = person && [...row.children].find((cell) => !cell.querySelector("input") && cell.textContent.trim() === person.name);
      if (!nameCell) return;
      const color = tagFor(input.dataset.person);
      nameCell.classList.toggle("person-tagged", Boolean(color));
      if (color) nameCell.style.setProperty("--tag", color); else nameCell.style.removeProperty("--tag");
    });
  }
  const schedule = el("#schedule-table");
  if (schedule) {
    schedule.querySelectorAll("select.employee-name-editor").forEach((select) => {
      const cell = select.closest("td");
      const color = select.value ? tagFor(select.value) : "";
      cell?.classList.toggle("person-tagged", Boolean(color));
      if (color) cell?.style.setProperty("--tag", color); else cell?.style.removeProperty("--tag");
    });
  }
}

/* ---------- Yeniden çizimde odak ve seçim korunur ---------- */

function gridWrapRender(kind, original) {
  return function wrappedRender(...args) {
    if (kind === "schedule" && grid.batching) return undefined;
    const activeKey = grid.pendingFocus?.kind === kind
      ? grid.pendingFocus.key
      : gridTable(kind)?.contains(document.activeElement) ? gridKey(kind, document.activeElement) : null;
    const result = original.apply(this, args);
    if (activeKey) {
      const node = gridNode(kind, activeKey);
      if (node && document.activeElement !== node && !document.querySelector("dialog[open]")) {
        node.focus({ preventScroll: true });
        if (node.matches("input")) node.select();
      }
    }
    if (grid.kind === kind) {
      grid.keys = new Set([...grid.keys].filter((key) => gridNode(kind, key)));
      gridPaint();
    }
    gridDecorate();
    return result;
  };
}

function startGrid() {
  renderAttendance = gridWrapRender("attendance", renderAttendance);
  renderSchedule = gridWrapRender("schedule", renderSchedule);
  const originalSwitchView = switchView;
  switchView = async function gridSwitchView(...args) {
    if (grid.kind && grid.keys.size) {
      gridSelections.set(grid.kind, {
        keys: [...grid.keys],
        anchor: grid.anchor,
        active: grid.active,
      });
    }
    const view = args[0];
    const result = await originalSwitchView.apply(this, args);
    const saved = result && gridSelections.get(view);
    if (saved && gridTable(view)) {
      const keys = saved.keys.filter((key) => gridNode(view, key));
      if (keys.length) {
        grid.kind = view;
        grid.keys = new Set(keys);
        grid.anchor = keys.includes(saved.anchor) ? saved.anchor : keys[0];
        grid.active = keys.includes(saved.active) ? saved.active : grid.anchor;
        gridPaint();
        if (!document.querySelector("dialog[open]")) gridNode(view, grid.active)?.focus({ preventScroll: true });
      } else {
        gridSelections.delete(view);
      }
    }
    return result;
  };
  ["#attendance-table", "#schedule-table"].forEach((selector) => {
    const table = el(selector);
    if (!table) return;
    table.addEventListener("mousedown", gridMouseDown);
    table.addEventListener("mouseover", gridMouseOver);
    table.addEventListener("click", gridClickCapture, true);
  });
  document.addEventListener("mouseup", gridMouseUp);
  el("#attendance-table")?.addEventListener("keydown", gridAttendanceKeydown, true);
  el("#attendance-table")?.addEventListener("focusin", (event) => {
    const input = event.target.closest?.(GRID_ATTENDANCE_SELECTOR);
    if (!input || state.formatMode) return;
    grid.active = gridKey("attendance", input);
    input.select();
    grid.justFocused = input;
    requestAnimationFrame(() => { if (document.activeElement === input) input.select(); });
  });
  el("#attendance-table")?.addEventListener("mouseup", (event) => {
    if (grid.justFocused && event.target === grid.justFocused) event.preventDefault();
    grid.justFocused = null;
  });
  el("#schedule-table")?.addEventListener("keydown", gridScheduleKeydown, true);
  el("#schedule-table")?.addEventListener("focusin", (event) => {
    const cell = event.target.closest?.(GRID_SCHEDULE_SELECTOR);
    if (cell && event.target === cell) grid.active = gridKey("schedule", cell);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && grid.keys.size > 1 && !document.querySelector("dialog[open]")) gridClearSelection();
  });
  gridLoadTags();
}

startGrid();
