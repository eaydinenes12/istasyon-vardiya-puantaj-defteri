"use strict";

// Section lists are filled from /api/schedule-sections and mutated in place.
const SECTIONS = ["MARKET", "TEMİZLİK", "ÖSSE"];
const SECTION_LIMITS = { MARKET: 9, "TEMİZLİK": 4, "ÖSSE": 14 };
const SECTION_ROW_COUNTS = { MARKET: 6, "TEMİZLİK": 1, "ÖSSE": 11 };
const SECTION_TEMPLATE_ROWS = {
  MARKET: [6, 7, 8, 9, 10, 11],
  "TEMİZLİK": [17],
  "ÖSSE": [23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33],
};
// Custom sections copy the TEMİZLİK block: spacer row 18, then header rows 13-16 and data row 17.
const CUSTOM_SECTION_HEADER_SOURCES = [18, 13, 14, 15, 16];
const SECTION_SETTINGS = { max_rows: 40, max_custom: 6, extra_rows: 3, max_personnel: 60 };
const ATTENDANCE_LEGEND_SLOTS = {
  X: [37, 5], S: [38, 5], H: [39, 5], "Ü": [40, 5], R: [41, 5], "G.Ç": [42, 5],
  G: [37, 12], "İST.": [38, 12], E: [39, 12], K: [40, 12], Y: [41, 12],
};
const SHIFT_OPTIONS = [
  ["", "— Seçin —"],
  ["work", "Çalışma"],
  ["weekly_off", "Hafta tatili · H"],
  ["overnight_exit", "Geceden çıkış · G.Ç"],
  ["annual_leave", "Senelik izin · S"],
  ["sick_leave", "Rapor · R"],
  ["unpaid_leave", "Ücretsiz izin · Ü"],
  ["marriage_leave", "Evlilik izni · E"],
  ["quarantine", "Karantina · K"],
  ["half_day", "Yarım gün · Y"],
  ["absent", "İşe gelmedi · G"],
  ["resignation", "İstifa · İST."],
  ["custom", "Özel vardiya kodu"],
];
const DEFAULT_QUICK_ENTRIES = [
  { id: "work-0700", label: "07:00–15:00", code: "X", kind: "work", start: "07:00", end: "15:00", style: "bg-shift-1" },
  { id: "work-1500", label: "15:00–23:00", code: "X", kind: "work", start: "15:00", end: "23:00", style: "bg-shift-2" },
  { id: "work-night", label: "22:30–07:00", code: "X", kind: "work", start: "22:30", end: "07:00", style: "bg-shift-3" },
  { id: "work-middle", label: "Ara vardiya", code: "X", kind: "work", start: "11:00", end: "19:00", style: "bg-shift-ara" },
  { id: "weekly-off", label: "Hafta tatili", code: "H", kind: "weekly_off", style: "bg-leave-yellow" },
  { id: "annual-leave", label: "Yıllık izin", code: "S", kind: "annual_leave", style: "bg-leave-yellow" },
  { id: "sick-leave", label: "Rapor", code: "R", kind: "sick_leave", style: "bg-p-R" },
  { id: "unpaid-leave", label: "Ücretsiz izin", code: "Ü", kind: "unpaid_leave", style: "bg-p-U" },
  { id: "marriage-leave", label: "Evlilik izni", code: "E", kind: "marriage_leave", style: "bg-event" },
  { id: "quarantine", label: "Karantina", code: "K", kind: "quarantine", style: "bg-event" },
  { id: "half-day", label: "Yarım gün", code: "Y", kind: "half_day", style: "bg-event" },
  { id: "absent", label: "İşe gelmedi", code: "G", kind: "absent", style: "bg-event" },
  { id: "resignation", label: "İstifa", code: "İST.", kind: "resignation", style: "bg-event" },
  { id: "overnight-exit", label: "Geceden çıkış", code: "G.Ç", kind: "overnight_exit", style: "bg-event" },
];
const ATTENDANCE_CODES = ["", "X", "H", "G.Ç", "S", "R", "Ü", "G", "İST.", "E", "K", "Y"];
const MONTH_NAMES = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];
const ATTENDANCE_CATEGORY_COLORS = {
  weekday: "#F1F6F1",
  weekend: "#E8EEF4",
  official: "#FBEBDD",
  religious: "#F2EAF5",
};

const el = (selector) => document.querySelector(selector);
const state = {
  view: "schedule",
  people: [],
  schedule: null,
  scheduleSavedEntries: [],
  neighborSchedules: {},
  neighborSavedEntries: {},
  scheduleProtections: {},
  neighborDirty: new Set(),
  attendance: null,
  balanceSummary: null,
  attendanceFilter: "all",
  attendanceQuery: "",
  attendanceComparisonRequestId: 0,
  periodCheckFlow: null,
  templates: { schedule: {}, attendance: {} },
  scheduleDirty: false,
  attendanceDirty: false,
  importUndoSnapshot: null,
  scheduleMonth: currentMonth(),
  attendanceMonth: currentMonth(),
  weekIndex: 0,
  viewContexts: {},
  brush: null,
  quickEntries: [],
  quickEntryColorDraft: null,
  scheduleTemplates: [],
  scheduleFilters: { query: "", section: "", kind: "", sort: "slot" },
  scheduleValidationSettings: { minimum_rest_hours: 0 },
  overtimeSettings: { weekly_limit_hours: 0, week_start: 0, daily_limit_hours: 8 },
  displayScheduleAssignments: null,
  workbookSettings: { sheets: {}, quick_colors: {} },
  templateEdit: null,
  tableTemplates: null,
  formatMode: false,
  selectedCell: null,
  formattingBusy: false,
  quickDialogTarget: null,
  scheduleClipboard: null,
  importPreview: null,
  archiveMonths: [],
  archiveKind: "schedule",
  archiveMatchMonths: null,
  archiveRevision: null,
  editHistory: {
    schedule: { undo: [], redo: [], baseline: null, lastSnapshot: null },
    attendance: { undo: [], redo: [], baseline: null, lastSnapshot: null },
  },
  workbookHistory: {},
  applyingEditHistory: false,
  backups: [],
  history: [],
  formTemplates: [],
  formCell: null,
  formPreviewSequence: 0,
  formOpening: false,
  toastTimer: null,
  uiSettings: {
    shift_types: [
      { id: "sabah", label: "Sabah", color: "#e2efda", from: "05:00", to: "10:59" },
      { id: "ara", label: "Ara vardiya", color: "#fce4d6", from: "11:00", to: "13:59" },
      { id: "aksam", label: "Akşam", color: "#ddebf7", from: "14:00", to: "19:59" },
      { id: "gece", label: "Gece", color: "#e4dfec", from: "20:00", to: "04:59" },
      { id: "yarim", label: "Yarım gün", color: "#fff2cc", from: "", to: "" },
    ],
    half_day_max_hours: 5,
    auto_type_color: true,
    shortcuts: {},
    tips: { enabled: true, dismissed: [] },
    templates: { favorites: [], default: "", usage: {} },
    density: "comfortable",
    hide_empty_rows: true,
    personnel_views: [],
  },
};

function parseTimeText(raw) {
  const text = String(raw ?? "").trim().replace(/[.,;\s]/g, ":");
  if (!text) return null;
  let hour;
  let minute = 0;
  let match = text.match(/^(\d{1,2}):(\d{1,2})$/);
  if (match) {
    hour = Number(match[1]);
    minute = Number(match[2]);
  } else if ((match = text.match(/^\d{1,4}$/))) {
    if (text.length <= 2) hour = Number(text);
    else {
      hour = Number(text.slice(0, -2));
      minute = Number(text.slice(-2));
    }
  } else return null;
  if (hour === 24 && minute === 0) return "00:00";
  if (hour > 23 || minute > 59) return null;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function displayTime(value, isEnd = false) {
  return isEnd && value === "00:00" ? "24:00" : (value || "");
}

function timeMinutes(value) {
  const [hour, minute] = String(value).split(":").map(Number);
  return hour * 60 + minute;
}

function shiftTypeById(id) {
  return (state.uiSettings.shift_types || []).find((item) => item.id === id) || null;
}

function classifyShift(start, end) {
  if (!start || !end) return "";
  const begin = timeMinutes(start);
  const duration = ((timeMinutes(end) - begin + 1440) % 1440) || 1440;
  const types = state.uiSettings.shift_types || [];
  const limit = Number(state.uiSettings.half_day_max_hours || 0);
  if (limit && duration <= limit * 60 && types.some((item) => item.id === "yarim")) return "yarim";
  const found = types.find((item) => {
    if (!item.from) return false;
    const low = timeMinutes(item.from);
    const high = timeMinutes(item.to);
    return low <= high ? begin >= low && begin <= high : begin >= low || begin <= high;
  });
  return found?.id || "";
}

async function saveUiSettings(patch) {
  const result = await request("/api/ui-settings", { method: "PUT", body: JSON.stringify(patch) });
  state.uiSettings = result.settings;
  document.dispatchEvent(new CustomEvent("ui-settings-changed"));
  return state.uiSettings;
}

function currentMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function previousMonth(month) {
  const [year, monthNumber] = month.split("-").map(Number);
  const previous = new Date(year, monthNumber - 2, 1);
  return `${previous.getFullYear()}-${String(previous.getMonth() + 1).padStart(2, "0")}`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function localDate(year, monthIndex, day) {
  return new Date(year, monthIndex, day, 12);
}

function dateKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function monthDates(month) {
  const [year, monthNumber] = month.split("-").map(Number);
  const lastDay = new Date(year, monthNumber, 0).getDate();
  return Array.from({ length: lastDay }, (_, index) => localDate(year, monthNumber - 1, index + 1));
}

function weekStarts(month) {
  const dates = monthDates(month);
  const first = dates[0];
  const monday = localDate(first.getFullYear(), first.getMonth(), first.getDate() - ((first.getDay() + 6) % 7));
  const last = dates[dates.length - 1];
  const finalSunday = localDate(last.getFullYear(), last.getMonth(), last.getDate() + (6 - ((last.getDay() + 6) % 7)));
  const result = [];
  for (let cursor = monday; cursor <= finalSunday; cursor = localDate(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + 7)) {
    result.push(cursor);
  }
  return result;
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  const result = contentType.includes("application/json") ? await response.json() : await response.blob();
  if (!response.ok) {
    const message = result.error || `İstek başarısız (${response.status}).`;
    const correction = errorCorrection(message);
    const trace = result.operation_id ? ` (Hata kodu ${result.error_code}; işlem ${result.operation_id})` : "";
    const error = new Error(`${message}${trace}${correction ? ` Düzeltme önerisi: ${correction}` : ""}`);
    error.errorCode = result.error_code || "";
    error.operationId = result.operation_id || "";
    if (response.status === 409 && Array.isArray(result.schedule_conflicts)) {
      error.scheduleConflicts = result.schedule_conflicts;
    }
    if (response.status === 409 && Array.isArray(result.attendance_conflicts)) {
      error.attendanceConflicts = result.attendance_conflicts;
    }
    throw error;
  }
  return result;
}

function errorCorrection(message) {
  const text = message.toLocaleLowerCase("tr-TR");
  if (text.includes("istek tamamlanamadı")) {
    return "Beklenmeyen bir sunucu hatası oluştu. İşlemi bir kez daha deneyin; sürerse Araçlar › Sorun raporu oluştur ile paket hazırlayın.";
  }
  if (text.includes("dönem kilitli")) {
    return "Arşivden ilgili dönemi açıp kilidini kaldırın; sonra işlemi yeniden deneyin.";
  }
  if (text.includes("değişmiş veya silinmiş") || text.includes("kayıt değişmiş")) {
    return "Arşivi yenileyip son kayıt durumunu kontrol edin ve işlemi tekrar deneyin.";
  }
  if (text.includes("personel bulunamadı") || text.includes("personel seçin")) {
    return "Personel listesinden doğru ve etkin çalışanı seçtiğinizi kontrol edin.";
  }
  if (text.includes("uzantısı") || text.includes("dosya biçimi") || text.includes("dosya türü")) {
    return "Şablon veya çıktı için desteklenen dosya türünü ve uzantısını kullanın.";
  }
  if (text.includes("konum seçimini desteklemiyor")) {
    return "Dosya konumu seçmek için masaüstü uygulamasını kullanın.";
  }
  if (text.includes("ay geçersiz") || text.includes("ayı geçersiz")
      || text.includes("tarih geçersiz") || text.includes("ay biçimi")) {
    return "Geçerli bir tarih seçin; ay alanlarını YYYY-AA biçiminde kullanın.";
  }
  if (text.includes("geçersiz") || text.includes("olmalıdır") || text.includes("arasında olmalıdır")) {
    return "Girilen değerleri kontrol edip belirtilen biçimde yeniden girin.";
  }
  return "";
}

function friendlyErrorText(message) {
  const raw = String(message ?? "").trim();
  if (!raw) return "Beklenmeyen bir sorun oluştu. İşlemi yeniden deneyin.";
  const text = raw.toLocaleLowerCase("tr-TR");
  if (/failed to fetch|networkerror|load failed|network request failed|err_connection/.test(text)) {
    return "Uygulama sunucusuna ulaşılamadı. Uygulama penceresi açık mı kontrol edin; sürerse uygulamayı kapatıp yeniden açın.";
  }
  if (/aborterror|the operation was aborted|timed? ?out/.test(text)) {
    return "İşlem zaman aşımına uğradı. Bir kez daha deneyin.";
  }
  if (/unexpected token|json\.parse|is not valid json|unexpected end of json/.test(text)) {
    return "Sunucudan beklenmeyen bir yanıt geldi. Sayfayı yenileyip (F5) işlemi tekrar deneyin.";
  }
  if (/quotaexceeded|exceeded the quota/.test(text)) {
    return "Tarayıcı depolama alanı doldu. Ayarlar › Görünüm bölümünden yerel tercihleri sıfırlayıp tekrar deneyin.";
  }
  if (/^(typeerror|referenceerror|rangeerror)\b|cannot read propert|is not a function|is not defined|undefined is not|null is not/.test(text)) {
    return "Ekranda beklenmeyen bir hata oluştu. Sayfayı yenileyip (F5) tekrar deneyin; sürerse Araçlar › Sorun raporu oluştur ile bildirin.";
  }
  return raw;
}

function toast(message, error = false, action = null) {
  const shown = error ? friendlyErrorText(message) : message;
  if (error && window.easyAssistant) window.easyAssistant.reportError(shown);
  const node = el("#toast");
  node.replaceChildren(document.createTextNode(shown));
  if (action?.label && typeof action.run === "function") {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "toast-action";
    retry.textContent = action.label;
    retry.addEventListener("click", async () => {
      retry.disabled = true;
      try {
        await action.run();
      } finally {
        retry.disabled = false;
      }
    });
    node.append(retry);
  }
  if (error && shown !== String(message ?? "")) node.title = `Teknik ayrıntı: ${message}`;
  else node.removeAttribute("title");
  node.classList.toggle("error", error);
  node.classList.add("visible");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => node.classList.remove("visible"), action ? 15000 : error ? 6500 : 3300);
}

function newRowId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
  return `row-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function clearImportUndo() {
  state.importUndoSnapshot = null;
  const button = el("#undo-import");
  if (button) button.hidden = true;
}

function setButtonDisabled(button, disabled, reason) {
  if (!button) return;
  if (!Object.prototype.hasOwnProperty.call(button.dataset, "enabledTitle")) {
    button.dataset.enabledTitle = button.getAttribute("title") || "";
  }
  button.disabled = disabled;
  if (disabled && reason) {
    button.title = reason;
    button.setAttribute("aria-description", reason);
  } else {
    button.title = button.dataset.enabledTitle;
    button.removeAttribute("aria-description");
  }
}

function setScheduleDirty(dirty = true, preserveImportUndo = false) {
  if (!preserveImportUndo) clearImportUndo();
  if (dirty) recordEditChange("schedule");
  state.scheduleDirty = dirty;
  setButtonDisabled(el("#save-schedule"), !dirty, "Kaydetmek için henüz değişiklik yok.");
  el("#schedule-count").textContent = dirty ? "Kaydedilmemiş değişiklik" : `${state.schedule?.entries.length || 0} vardiya satırı`;
  updateSaveState(dirty);
}

function setAttendanceDirty(dirty = true) {
  if (dirty) recordEditChange("attendance");
  state.attendanceDirty = dirty;
  setButtonDisabled(el("#save-attendance"), !dirty, "Kaydetmek için henüz değişiklik yok.");
  el("#attendance-count").textContent = dirty ? "Kaydedilmemiş puantaj değişikliği" : `${state.attendance?.rows.length || 0} personel`;
  updateSaveState(dirty);
}

function editSnapshot(kind) {
  if (kind === "schedule") {
    return {
      entries: structuredClone(state.schedule?.entries || []),
      neighborEntries: Object.fromEntries(Object.entries(state.neighborSchedules).map(([month, data]) =>
        [month, structuredClone(data.entries || [])])),
    };
  }
  return {
    overrides: structuredClone(state.attendance?.overrides || {}),
    deductions: structuredClone(state.attendance?.deductions || {}),
    overtime: structuredClone(state.attendance?.overtime || {}),
  };
}

function updateEditHistoryControls(kind) {
  const history = state.editHistory[kind];
  const undoDescription = history.undo.length
    ? describeEditHistoryAction(kind, history.undo[history.undo.length - 1], editSnapshot(kind))
    : "";
  const redoDescription = history.redo.length
    ? describeEditHistoryAction(kind, history.redo[history.redo.length - 1], editSnapshot(kind))
    : "";
  setButtonDisabled(el(`#undo-${kind}`), history.undo.length === 0, "Geri alınacak değişiklik yok.");
  setButtonDisabled(el(`#redo-${kind}`), history.redo.length === 0, "Yinelenecek değişiklik yok.");
  const undo = el(`#undo-${kind}`);
  const redo = el(`#redo-${kind}`);
  const label = el(`#${kind}-history-action`);
  if (undoDescription) {
    undo.title = `Geri al: ${undoDescription}`;
    undo.setAttribute("aria-label", `Geri al: ${undoDescription}`);
  } else {
    undo.setAttribute("aria-label", `${kind === "schedule" ? "Vardiya" : "Puantaj"} değişikliğini geri al`);
  }
  if (redoDescription) {
    redo.title = `Yinele: ${redoDescription}`;
    redo.setAttribute("aria-label", `Yinele: ${redoDescription}`);
  } else {
    redo.setAttribute("aria-label", `${kind === "schedule" ? "Vardiya" : "Puantaj"} değişikliğini yinele`);
  }
  if (label) label.textContent = undoDescription ? `Geri al: ${undoDescription}` : "";
}

function describeEditHistoryAction(kind, before, after) {
  const values = (snapshot) => {
    const result = new Map();
    if (kind === "schedule") {
      for (const row of [snapshot.entries, ...Object.values(snapshot.neighborEntries || {})].flat()) {
        for (const [day, value] of Object.entries(row.days || {})) {
          result.set(`${row.row_id}:${day}`, value);
        }
      }
    } else {
      for (const [day, people] of Object.entries(snapshot.overrides || {})) {
        for (const [person, value] of Object.entries(people || {})) {
          result.set(`${person}:${day}`, value);
        }
      }
    }
    return result;
  };
  const oldValues = values(before);
  const newValues = values(after);
  const changed = [...new Set([...oldValues.keys(), ...newValues.keys()])]
    .filter((key) => JSON.stringify(oldValues.get(key) ?? null) !== JSON.stringify(newValues.get(key) ?? null));
  if (!changed.length) return kind === "schedule" ? "çizelge yapısı değişikliği" : "puantaj ayarı değişikliği";
  if (kind === "schedule" && changed.every((key) => {
    const oldValue = oldValues.get(key);
    const newValue = newValues.get(key);
    return oldValue?.kind === "work" && newValue?.kind === "work"
      && (oldValue.start !== newValue.start || oldValue.end !== newValue.end);
  })) return `${changed.length} hücrenin saat değişikliği`;
  return kind === "schedule"
    ? `${changed.length} hücrenin vardiya değişikliği`
    : `${changed.length} hücrenin puantaj kodu değişikliği`;
}

function resetEditHistory(kind) {
  const snapshot = editSnapshot(kind);
  const history = state.editHistory[kind];
  history.undo = [];
  history.redo = [];
  history.baseline = structuredClone(snapshot);
  history.lastSnapshot = structuredClone(snapshot);
  updateEditHistoryControls(kind);
}

function rebaseEditHistory(kind) {
  const snapshot = editSnapshot(kind);
  const history = state.editHistory[kind];
  history.baseline = structuredClone(snapshot);
  history.lastSnapshot = structuredClone(snapshot);
  updateEditHistoryControls(kind);
}

function recordEditChange(kind) {
  if (state.applyingEditHistory) return;
  const history = state.editHistory[kind];
  const snapshot = editSnapshot(kind);
  if (!history.lastSnapshot) {
    history.baseline = structuredClone(snapshot);
    history.lastSnapshot = structuredClone(snapshot);
    updateEditHistoryControls(kind);
    return;
  }
  if (JSON.stringify(snapshot) !== JSON.stringify(history.lastSnapshot)) {
    history.undo.push(history.lastSnapshot);
    if (history.undo.length > 50) history.undo.shift();
    history.redo = [];
    history.lastSnapshot = structuredClone(snapshot);
  }
  updateEditHistoryControls(kind);
}

function applyEditSnapshot(kind, snapshot) {
  const history = state.editHistory[kind];
  state.applyingEditHistory = true;
  try {
    if (kind === "schedule") {
      state.schedule.entries = structuredClone(snapshot.entries);
      for (const [month, entries] of Object.entries(snapshot.neighborEntries)) {
        if (state.neighborSchedules[month]) {
          state.neighborSchedules[month].entries = structuredClone(entries);
        }
      }
      state.neighborDirty = new Set(Object.keys(state.neighborSchedules).filter((month) =>
        JSON.stringify(state.neighborSchedules[month].entries || [])
        !== JSON.stringify(history.baseline.neighborEntries[month] || [])));
      const dirty = JSON.stringify(state.schedule.entries) !== JSON.stringify(history.baseline.entries)
        || state.neighborDirty.size > 0;
      renderSchedule();
      setScheduleDirty(dirty);
    } else {
      state.attendance.overrides = structuredClone(snapshot.overrides);
      state.attendance.deductions = structuredClone(snapshot.deductions);
      state.attendance.overtime = structuredClone(snapshot.overtime || {});
      state.attendance.rows.forEach((person) => {
        const personId = String(person.employee_id);
        person.codes = {};
        person.sources = {};
        state.attendance.dates.forEach((day) => {
          const manual = state.attendance.overrides[day]?.[personId];
          const automatic = person.automatic_codes[day];
          if (manual !== undefined) {
            person.codes[day] = manual;
            person.sources[day] = "manual";
          } else if (automatic) {
            person.codes[day] = automatic;
            person.sources[day] = "schedule";
          }
        });
        person.deduction_amount = state.attendance.payrollRedacted ? null : state.attendance.deductions[personId] || 0;
        applyAttendanceOvertime(person);
      });
      renderAttendance();
      setAttendanceDirty(JSON.stringify(snapshot) !== JSON.stringify(history.baseline));
    }
  } finally {
    state.applyingEditHistory = false;
  }
  updateEditHistoryControls(kind);
}

function moveEditHistory(kind, direction) {
  const history = state.editHistory[kind];
  const source = direction === "undo" ? history.undo : history.redo;
  const destination = direction === "undo" ? history.redo : history.undo;
  if (!source.length) return;
  destination.push(editSnapshot(kind));
  if (destination.length > 50) destination.shift();
  applyEditSnapshot(kind, source.pop());
  history.lastSnapshot = editSnapshot(kind);
  updateEditHistoryControls(kind);
}

function updateSaveState(dirty) {
  const stateNode = el("#save-state");
  if (!stateNode) return;
  stateNode.classList.toggle("unsaved", dirty);
  el("#save-state-label").textContent = dirty ? "Kaydedilmemiş değişiklik" : "Bu bilgisayarda kayıtlı";
}

function renderSummary(container, items) {
  el(container).innerHTML = items.map(([label, value]) =>
    `<div class="summary-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
}

const STAFFING_DAY_NAMES = ["Paz", "Pzt", "Sal", "Çar", "Per", "Cum", "Cmt"];

function renderDailyStaffing(weekDays) {
  const container = el("#schedule-staffing");
  if (!container || !state.schedule) return;
  const days = weekDays.map(dateKey);
  const sections = new Map();
  for (const entry of state.schedule.entries) {
    const name = entry.section || "Diğer";
    if (!sections.has(name)) sections.set(name, days.map(() => 0));
    days.forEach((day, index) => {
      if (scheduleRowForDay(entry, day, false)?.days[day]?.kind === "work") sections.get(name)[index] += 1;
    });
  }
  if (!sections.size) {
    container.hidden = true;
    container.innerHTML = "";
    return;
  }
  const totals = days.map((_, index) => [...sections.values()].reduce((sum, counts) => sum + counts[index], 0));
  const max = Math.max(...totals);
  const outside = days.map((day) => !day.startsWith(state.scheduleMonth));
  const cell = (value, total, index) => `<td class="${outside[index] ? "staffing-outside" : value === 0 ? "staffing-zero" : ""}${total && value === max && max > 0 ? " staffing-peak" : ""}">${outside[index] && !value ? "–" : value}</td>`;
  container.hidden = false;
  container.innerHTML = `<table class="staffing-table" aria-label="Günlük çalışan sayısı">
    <caption>Günlük kadro <small>(çalışma vardiyası olan kişi sayısı)</small></caption>
    <thead><tr><th scope="col">Bölüm</th>${weekDays.map((date) =>
      `<th scope="col">${STAFFING_DAY_NAMES[date.getDay()]} ${date.getDate()}</th>`).join("")}</tr></thead>
    <tbody>${[...sections.entries()].sort(([left], [right]) => left.localeCompare(right, "tr")).map(([name, counts]) =>
      `<tr><th scope="row">${escapeHtml(name)}</th>${counts.map((value, index) => cell(value, false, index)).join("")}</tr>`).join("")}</tbody>
    <tfoot><tr><th scope="row">Toplam</th>${totals.map((value, index) => cell(value, true, index)).join("")}</tr></tfoot>
  </table>`;
}

function isExceptionCode(code) {
  return Boolean(code) && code !== "X";
}

async function loadPeople() {
  const result = await request("/api/people");
  state.people = result.people;
  renderPersonSelectors();
}

function renderPersonSelectors() {
  const select = el("#existing-person");
  const selected = Number(select.value) || 0;
  const active = state.people.filter((person) => person.active);
  const passive = state.people.filter((person) => !person.active);
  select.innerHTML = `<option value="">Personel seçin</option>${active.map((person) =>
    `<option value="${person.id}">${escapeHtml(person.name)}</option>`).join("")}${passive.length
    ? `<optgroup label="Pasif personel (arşiv)">${passive.map((person) =>
      `<option value="${person.id}">${escapeHtml(person.name)}</option>`).join("")}</optgroup>` : ""}`;
  if (state.people.some((person) => person.id === selected)) select.value = String(selected);
  updatePersonActions();
}

function updatePersonActions() {
  const person = state.people.find((item) => item.id === Number(el("#existing-person").value));
  el("#deactivate-person").hidden = Boolean(person && !person.active);
  el("#deactivate-person").disabled = !person;
  el("#activate-person").hidden = !person || person.active;
  el("#delete-person").disabled = !person;
}

async function deleteSelectedPerson() {
  const personId = Number(el("#existing-person").value);
  const person = state.people.find((item) => item.id === personId);
  if (!person) {
    toast("Önce silinecek personeli seçin.", true);
    return;
  }
  if (!window.confirm(`${person.name} kalıcı olarak silinsin mi? Bu işlem geri alınamaz.`)) return;
  try {
    let response = await fetch(`/api/people/${personId}/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force: false }),
    });
    let body = await response.json().catch(() => ({}));
    if (response.status === 409 && body.has_records) {
      const records = body.records || {};
      const labels = { schedule_months: "Vardiya ayı", attendance_months: "Puantaj ayı", events: "İzin/devamsızlık kaydı", openings: "Devir bakiyesi" };
      const details = Object.entries(records).filter(([, count]) => count)
        .map(([name, count]) => `• ${labels[name] || name}: ${count}`).join("\n");
      if (!window.confirm(`${person.name} için kayıtlı veriler var:\n${details}\n\nPersonel ve bu kayıtları silinsin mi? Geçmiş ayların puantajı değişebilir. Kayıtları korumak için “Vazgeç” deyip “Pasife al”ı kullanın.`)) return;
      response = await fetch(`/api/people/${personId}/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force: true }),
      });
      body = await response.json().catch(() => ({}));
    }
    if (!response.ok) throw new Error(body.error || "Personel silinemedi.");
    state.people = state.people.filter((item) => item.id !== personId);
    el("#existing-person").value = "";
    renderPersonSelectors();
    toast(`${person.name} silindi.`);
    if (state.view === "attendance") await loadAttendance();
    else if (state.schedule && !state.scheduleDirty) await loadSchedule();
    else renderSchedule();
  } catch (error) {
    toast(error.message, true);
  }
}

async function ensureTemplate(kind, sheetIndex = 0) {
  const sourceIndex = kind === "schedule" && sheetIndex >= 5 ? 1 : sheetIndex;
  if (!state.templates[kind][sourceIndex]) {
    state.templates[kind][sourceIndex] = await request(
      `/api/template?kind=${kind}&sheet=${sourceIndex}`,
    );
  }
  return state.templates[kind][sourceIndex];
}

function excelColumnName(number) {
  let name = "";
  while (number > 0) {
    number -= 1;
    name = String.fromCharCode(65 + (number % 26)) + name;
    number = Math.floor(number / 26);
  }
  return name;
}

function dateLabel(date, style = "short") {
  if (style === "full") {
    return new Intl.DateTimeFormat("tr-TR", {
      weekday: "long", day: "2-digit", month: "long", year: "numeric",
    }).format(date);
  }
  if (style === "compact") {
    const weekday = new Intl.DateTimeFormat("tr-TR", { weekday: "short" }).format(date);
    return `${weekday} ${String(date.getDate()).padStart(2, "0")}`;
  }
  return `${String(date.getDate()).padStart(2, "0")}/${String(date.getMonth() + 1).padStart(2, "0")}/${date.getFullYear()}`;
}

function weekRangeLabel(monday) {
  const sunday = localDate(monday.getFullYear(), monday.getMonth(), monday.getDate() + 6);
  const format = new Intl.DateTimeFormat("tr-TR", {
    day: "2-digit", month: "short", year: "numeric",
  });
  return `${format.format(monday)} – ${format.format(sunday)}`;
}

function weekTabRangeLabel(monday) {
  const sunday = localDate(monday.getFullYear(), monday.getMonth(), monday.getDate() + 6);
  const format = new Intl.DateTimeFormat("tr-TR", { day: "2-digit", month: "short" });
  return `${format.format(monday)}–${format.format(sunday)}`;
}

function currentSheetName(monday, index) {
  const [year, monthNumber] = state.scheduleMonth.split("-").map(Number);
  const monthStart = localDate(year, monthNumber - 1, 1);
  if (monday.getFullYear() === year && monday.getMonth() === monthNumber - 1) {
    const firstMonday = localDate(year, monthNumber - 1, monthStart.getDate() + ((8 - monthStart.getDay()) % 7));
    const week = Math.floor((monday - firstMonday) / (7 * 86400000)) + 1;
    return `${MONTH_NAMES[monthNumber - 1].toLocaleUpperCase("tr-TR")} ${week}`;
  }
  const previousMonth = localDate(monday.getFullYear(), monday.getMonth(), 1);
  const previousMonday = localDate(
    previousMonth.getFullYear(),
    previousMonth.getMonth(),
    previousMonth.getDate() - ((previousMonth.getDay() + 6) % 7),
  );
  const week = Math.floor((monday - previousMonday) / (7 * 86400000)) + 1;
  return `${MONTH_NAMES[monday.getMonth()].toLocaleUpperCase("tr-TR")} ${week}`;
}

function renderWeeks() {
  const weeks = weekStarts(state.scheduleMonth);
  state.weekIndex = Math.max(0, Math.min(state.weekIndex, weeks.length - 1));
  const [year, monthNumber] = state.scheduleMonth.split("-").map(Number);
  el("#schedule-period-label").textContent = `${MONTH_NAMES[monthNumber - 1]} ${year} · ${state.weekIndex + 1}. hafta · ${weekRangeLabel(weeks[state.weekIndex])}`;
  el("#schedule-week").innerHTML = weeks.map((monday, index) =>
    `<option value="${index}">${escapeHtml(weekRangeLabel(monday))}</option>`).join("");
  el("#schedule-week").value = String(state.weekIndex);
  setButtonDisabled(el("#previous-week"), state.weekIndex === 0, "Bu ayın ilk haftasındasınız.");
  setButtonDisabled(el("#next-week"), state.weekIndex === weeks.length - 1, "Bu ayın son haftasındasınız.");
  setButtonDisabled(el("#copy-previous-week"), state.weekIndex === 0, "İlk haftaya kopyalanacak önceki hafta yok.");
  el("#schedule-sheet-tabs").innerHTML = weeks.map((monday, index) => {
    const name = currentSheetName(monday, index);
    const range = weekRangeLabel(monday);
    const label = `${index + 1}. hafta · ${weekTabRangeLabel(monday)}`;
    return `<button class="sheet-tab" type="button" role="tab" aria-label="${escapeHtml(`${label}, ${range}; Excel sayfası ${name}`)}" title="${escapeHtml(`${range} · Excel sayfası ${name}`)}" aria-selected="${index === state.weekIndex}" data-week="${index}">${escapeHtml(label)}</button>`;
  }).join("");
  el("#schedule-sheet-name").textContent = `Excel sayfası: ${currentSheetName(weeks[state.weekIndex], state.weekIndex)}`;
  renderSchedule();
}

function presetOverride(id) {
  return state.quickEntries.find((entry) => entry.preset === id) || null;
}

function allQuickEntries({ includeHidden = false } = {}) {
  const presets = DEFAULT_QUICK_ENTRIES.map((preset) => {
    const override = presetOverride(preset.id);
    if (!override) return { ...preset };
    const merged = { ...preset, label: override.label, hidden: Boolean(override.hidden), overridden: true };
    if (preset.kind === "work") Object.assign(merged, { start: override.start, end: override.end, code: override.code });
    if (override.shift_type) merged.shift_type = override.shift_type;
    return merged;
  });
  const customs = state.quickEntries.filter((entry) => !entry.preset).map((entry) => ({ ...entry, custom: true }));
  return [...presets, ...customs]
    .filter((entry) => includeHidden || !entry.hidden)
    .map((entry) => ({ ...entry, color: state.quickEntryColorDraft?.[quickEntryKey(entry)]
      || state.workbookSettings.quick_colors[quickEntryKey(entry)] || presetDefaultColor(entry) }));
}

function presetDefaultColor(entry) {
  const colors = { "work-0700": "#e2efda", "work-1500": "#ddebf7", "work-night": "#e4dfec",
    "work-middle": "#fce4d6", "weekly-off": "#fff2cc", "annual-leave": "#fff2cc",
    "sick-leave": "#f8d7da", "unpaid-leave": "#eadcf0" };
  return colors[entry.id] || shiftTypeById(entry.shift_type || classifyShift(entry.start, entry.end))?.color || "#ffffff";
}

function quickEntryKey(entry) {
  return entry.custom ? `custom:${entry.id}` : entry.id;
}

function quickEntryByKey(key) {
  return allQuickEntries({ includeHidden: true }).find((entry) => quickEntryKey(entry) === key);
}

function quickEntryTimeText(entry) {
  return entry.start ? `${displayTime(entry.start)}–${displayTime(entry.end, true)}` : "saat yok";
}

function quickEntryButton(entry, dialog = false) {
  const key = quickEntryKey(entry);
  const className = `shift-chip ${entry.style || "custom-shift-chip"}`;
  const targetAttribute = dialog ? `data-cell-preset="${escapeHtml(key)}"` : `data-preset-id="${escapeHtml(key)}" draggable="true"`;
  return `<button class="${className}" style="background:${entry.color};color:${readableColor(entry.color)}" type="button" ${targetAttribute} title="${escapeHtml(entry.label)} · Puantaj: ${escapeHtml(entry.code)}"><span>${escapeHtml(entry.label)}</span><b class="chip-code">${escapeHtml(entry.code)}</b></button>`;
}

function renderQuickEntryPalette() {
  el("#quick-entry-options").innerHTML = allQuickEntries()
    .map((entry) => quickEntryButton(entry)).join("");
}

function renderShiftTypeOptions(selected = "") {
  const select = el("#quick-entry-shift-type");
  if (!select) return;
  select.innerHTML = `<option value="">Saate göre otomatik</option>` + (state.uiSettings.shift_types || [])
    .map((item) => `<option value="${escapeHtml(item.id)}"${item.id === selected ? " selected" : ""}>${escapeHtml(item.label)}</option>`).join("");
}

function renderQuickEntrySettings() {
  const colors = [
    ["#E5F1E7", "Açık yeşil"], ["#E2F1F5", "Açık mavi"], ["#F7E8C8", "Açık sarı"],
    ["#F7DEDD", "Açık kırmızı"], ["#E8E3F3", "Açık mor"], ["#E5EAED", "Açık gri"],
  ];
  el("#quick-entry-color-list").innerHTML = allQuickEntries().map((entry) => {
    const key = quickEntryKey(entry);
    return `<div class="preset-color-row"><span>${escapeHtml(entry.label)}</span>
      <div class="preset-color-options">${colors.map(([color, label]) =>
        `<button class="preset-color-swatch" type="button" data-preset-color-pick="${escapeHtml(key)}" data-color="${color}"
          aria-label="${escapeHtml(entry.label)}: ${label}" aria-pressed="${entry.color.toLowerCase() === color.toLowerCase()}"
          style="--preset-color:${color}"></button>`).join("")}
        <label class="preset-color-custom" title="Özel renk"><span>Özel</span>
          <input type="color" aria-label="${escapeHtml(entry.label)} özel rengi" data-preset-color="${escapeHtml(key)}" value="${entry.color}">
        </label>
      </div><div class="quick-entry-color-preview" data-color-preview="${escapeHtml(key)}"
        style="background:${entry.color};color:${readableColor(entry.color)}"><span>Örnek hücre</span>
        <strong>${escapeHtml(quickEntryTimeText(entry))} · ${escapeHtml(entry.code)}</strong></div></div>`;
  }).join("") + `<div class="quick-entry-color-actions">
    <output role="status" aria-live="polite">${state.quickEntryColorDraft && JSON.stringify(state.quickEntryColorDraft)
      !== JSON.stringify(state.workbookSettings.quick_colors) ? "Kaydedilmemiş renk önizlemesi." : "Renk değişikliği yok."}</output>
    <button class="button button-primary" type="button" data-save-quick-entry-colors
      ${state.quickEntryColorDraft && JSON.stringify(state.quickEntryColorDraft) !== JSON.stringify(state.workbookSettings.quick_colors) ? "" : "disabled"}>Renkleri kaydet</button>
    <button class="button button-light" type="button" data-cancel-quick-entry-colors
      ${state.quickEntryColorDraft && JSON.stringify(state.quickEntryColorDraft) !== JSON.stringify(state.workbookSettings.quick_colors) ? "" : "disabled"}>Vazgeç</button>
  </div>`;
  renderShiftTypeOptions(el("#quick-entry-shift-type")?.value || "");
  const list = el("#custom-quick-entry-list");
  list.innerHTML = allQuickEntries({ includeHidden: true }).map((entry) => {
    const key = quickEntryKey(entry);
    const type = shiftTypeById(entry.shift_type || classifyShift(entry.start, entry.end));
    const badge = entry.custom ? "Özel" : entry.overridden ? "Hazır · düzenlendi" : "Hazır";
    const actions = entry.custom
      ? `<button class="button button-light danger-button" type="button" data-delete-quick-entry="${escapeHtml(entry.id)}">Sil</button>`
      : `<button class="button button-light" type="button" data-toggle-quick-entry="${escapeHtml(entry.id)}">${entry.hidden ? "Göster" : "Gizle"}</button>`
        + (entry.overridden ? `<button class="button button-light" type="button" data-reset-quick-entry="${escapeHtml(entry.id)}">Varsayılana dön</button>` : "");
    return `<div class="custom-entry-row${entry.hidden ? " is-hidden-entry" : ""}"><span class="entry-swatch" style="background:${entry.color}"></span><div><strong>${escapeHtml(entry.label)}</strong><span>${escapeHtml(entry.code)} · ${escapeHtml(quickEntryTimeText(entry))}${type ? ` · ${escapeHtml(type.label)}` : ""} · ${badge}${entry.hidden ? " · gizli" : ""}</span></div><div class="custom-entry-actions"><button class="button button-light" type="button" data-edit-quick-entry="${escapeHtml(key)}">Düzenle</button>${actions}</div></div>`;
  }).join("");
}

async function persistQuickEntries(entries, message) {
  const result = await request("/api/quick-entries", { method: "PUT", body: JSON.stringify({ entries }) });
  state.quickEntries = result.entries.map((entry) => ({ ...entry, custom: !entry.preset }));
  if (state.brush) state.brush = quickEntryByKey(quickEntryKey(state.brush)) || null;
  renderQuickEntryPalette();
  renderQuickEntrySettings();
  if (message) toast(message);
}

function previewQuickEntryColor(key, color) {
  state.quickEntryColorDraft ||= { ...state.workbookSettings.quick_colors };
  state.quickEntryColorDraft[key] = color;
  renderQuickEntryPalette();
  renderQuickEntrySettings();
  renderSchedule();
}

async function saveQuickEntryColorDraft() {
  if (!state.quickEntryColorDraft
    || JSON.stringify(state.quickEntryColorDraft) === JSON.stringify(state.workbookSettings.quick_colors)) return;
  const settings = structuredClone(state.workbookSettings);
  settings.quick_colors = { ...settings.quick_colors, ...state.quickEntryColorDraft };
  if (!await persistWorkbookSettings(settings)) return;
  state.quickEntryColorDraft = null;
  renderQuickEntryPalette();
  renderQuickEntrySettings();
  renderSchedule();
}

function discardQuickEntryColorDraft() {
  if (!state.quickEntryColorDraft) return;
  state.quickEntryColorDraft = null;
  renderQuickEntryPalette();
  renderQuickEntrySettings();
  renderSchedule();
  if (state.brush) state.brush = quickEntryByKey(quickEntryKey(state.brush)) || state.brush;
}

function storedQuickEntries() {
  return state.quickEntries.map(({ custom, ...entry }) => entry);
}

async function togglePresetHidden(id) {
  const preset = DEFAULT_QUICK_ENTRIES.find((item) => item.id === id);
  if (!preset) return;
  const current = presetOverride(id);
  const base = current ? { ...current } : { id: `preset-${id}`, preset: id, label: preset.label, code: preset.code, start: preset.start || "", end: preset.end || "" };
  base.hidden = !current?.hidden;
  const entries = storedQuickEntries().filter((entry) => entry.preset !== id);
  entries.push(Object.fromEntries(Object.entries(base).filter(([key]) => key !== "custom")));
  try {
    await persistQuickEntries(entries, base.hidden ? `“${preset.label}” paletten gizlendi.` : `“${preset.label}” yeniden gösteriliyor.`);
  } catch (error) {
    const label = view === "attendance" ? "Puantaj" : view === "schedule" ? "Vardiya" : "Sayfa";
    toast(`${label} yüklenemedi. ${friendlyErrorText(error.message)}`, true);
  }
}

async function resetPresetEntry(id) {
  try {
    await persistQuickEntries(storedQuickEntries().filter((entry) => entry.preset !== id), "Hazır giriş varsayılan haline döndü.");
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadQuickEntries() {
  const result = await request("/api/quick-entries");
  state.quickEntries = result.entries.map((entry) => ({ ...entry, custom: !entry.preset }));
  renderQuickEntryPalette();
  renderQuickEntrySettings();
}

function renderScheduleValidationStatus() {
  const hours = state.scheduleValidationSettings.minimum_rest_hours;
  el("#schedule-validation-status").textContent = hours > 0
    ? `${hours.toLocaleString("tr-TR")} saatlik asgari dinlenme denetleniyor.`
    : "Asgari dinlenme denetimi kapalı; vardiya çakışmaları yine denetlenir.";
}

async function saveScheduleValidationSettings() {
  const input = el("#schedule-min-rest-hours");
  const status = el("#schedule-validation-status");
  const previous = state.scheduleValidationSettings;
  input.disabled = true;
  status.textContent = "Denetim ayarı kaydediliyor.";
  try {
    state.scheduleValidationSettings = await request("/api/schedule-validation-settings", {
      method: "PUT",
      body: JSON.stringify({ minimum_rest_hours: Number(input.value) }),
    });
    input.value = String(state.scheduleValidationSettings.minimum_rest_hours);
    renderScheduleValidationStatus();
    renderSchedule();
  } catch (error) {
    input.value = String(previous.minimum_rest_hours);
    renderScheduleValidationStatus();
    toast(error.message, true);
  } finally {
    input.disabled = false;
  }
}

function scheduleItemForQuickEntry(entry) {
  if (!entry || entry.id === "clear") return null;
  if (entry.start && entry.end) {
    const item = { kind: "work", start: entry.start, end: entry.end };
    if (entry.code !== "X") item.attendance_code = entry.code;
    if (entry.custom) item.shift_label = entry.label;
    const type = entry.shift_type || classifyShift(entry.start, entry.end);
    if (type) item.shift_type = type;
    return item;
  }
  if (entry.kind) return { kind: entry.kind };
  return { kind: "custom", custom_code: entry.code, custom_label: entry.label };
}

async function applyQuickEntryToCell(row, day, entry) {
  if (!row || !day) return false;
  const assignment = [...(state.displayScheduleAssignments || scheduleRowsByTemplate()).entries()]
    .find(([, slot]) => slot.entry === row);
  const weeks = weekStarts(state.scheduleMonth);
  const offset = Math.round((new Date(`${day}T12:00:00`) - weeks[state.weekIndex]) / 86400000);
  if (assignment && offset >= 0 && offset <= 6) {
    const settings = structuredClone(state.workbookSettings);
    const cells = settings.sheets[sheetKey("schedule")]?.cells;
    let changed = false;
    for (const column of [4 + offset * 2, 5 + offset * 2]) {
      const key = `${assignment[0]}:${column}`;
      if (cells?.[key]?.color) {
        delete cells[key].color;
        if (!Object.keys(cells[key]).length) delete cells[key];
        changed = true;
      }
    }
    if (changed && !await persistWorkbookSettings(settings)) return false;
  }
  const item = scheduleItemForQuickEntry(entry);
  if (item) item.fill_color = entry.color || "#ffffff";
  const owner = scheduleRowForDay(row, day, Boolean(item));
  if (!owner) {
    if (!item) return true;
    return false;
  }
  if (item) owner.days[day] = item;
  else delete owner.days[day];
  if (day.slice(0, 7) !== state.scheduleMonth) state.neighborDirty.add(day.slice(0, 7));
  setScheduleDirty();
  renderSchedule();
  return true;
}

function showScheduleCellNotice(cell, message) {
  const container = cell?.querySelector(".special-entry-wrap") || cell?.querySelector(".work-pair");
  if (!container) return;
  let notice = container.querySelector(".cell-action-notice");
  if (!notice) {
    notice = document.createElement("span");
    notice.className = "cell-action-notice";
    notice.setAttribute("role", "status");
    notice.setAttribute("aria-live", "polite");
    container.append(notice);
  }
  notice.textContent = message;
}

function openCellQuickEntries(cell) {
  const target = scheduleTargetForCell(cell);
  if (!target) return;
  if (!target.employeeId) {
    showScheduleCellNotice(cell, "Önce personel seçin.");
    return;
  }
  if (typeof accessCan === "function" && !accessCan("schedule.write")) {
    showScheduleCellNotice(cell, accessState?.ready
      ? "Bu işlem için yetkiniz yok."
      : "Yetkiler kontrol ediliyor…");
    return;
  }
  const recordState = el("#easy-schedule-record-state")?.textContent || "";
  if (target.day.slice(0, 7) === state.scheduleMonth && recordState.includes("Dönem kilitli")) {
    showScheduleCellNotice(cell, "Dönem kilitli.");
    return;
  }
  state.quickDialogTarget = target;
  const person = state.people.find((item) => item.id === target.employeeId);
  el("#cell-quick-entry-date").textContent =
    `${person?.name || "Personel"} · ${dateLabel(new Date(`${target.day}T12:00:00`))}`;
  el("#cell-quick-entry-options").innerHTML = allQuickEntries()
    .map((entry) => quickEntryButton(entry, true)).join("")
    + `<button class="shift-chip clear-chip" type="button" data-cell-preset="clear">Hücreyi temizle</button>`;
  const row = target.rowId
    ? state.schedule.entries.find((entry) => entry.row_id === target.rowId)
    : null;
  const current = row && scheduleRowForDay(row, target.day, false)?.days[target.day];
  el("#copy-schedule-day").disabled = !current;
  el("#paste-schedule-day").disabled = state.scheduleClipboard?.type !== "day";
  el("#copy-schedule-week").disabled = !row;
  el("#paste-schedule-week").disabled = state.scheduleClipboard?.type !== "week";
  const dialog = el("#cell-quick-entry-dialog");
  const protection = el("#protect-schedule-day");
  protection.hidden = true;
  protection.onclick = null;
  if (current && accessCanAdmin() && !state.scheduleDirty && !state.neighborDirty.size) {
    const period = target.day.slice(0, 7);
    const source = period === state.scheduleMonth ? state.schedule : state.neighborSchedules[period];
    const savedRow = scheduleRowForDay(row, target.day, false);
    request(`/api/schedule/protection?month=${period}`).then(({ cells }) => {
      if (!dialog.open || state.quickDialogTarget !== target) return;
      const protectedCell = cells.find((cell) => cell.row_id === savedRow.row_id && cell.day === target.day);
      const protectedDay = Boolean(protectedCell);
      protection.hidden = false;
      protection.disabled = false;
      protection.textContent = protectedDay ? "Vardiya korumasını kaldır" : "Kesinleşen vardiyayı koru";
      protection.title = protectedCell
        ? `Gerekçe: ${protectedCell.reason || "Eski kayıtta belirtilmemiş"} · Ekleyen: ${protectedCell.protected_by || "Bilinmiyor"}`
        : "";
      protection.onclick = async () => {
        if (state.scheduleDirty || state.neighborDirty.size) {
          toast("Önce vardiya taslağını kaydedin veya atın.", true);
          return;
        }
        const reason = protectedDay ? "" : (window.prompt(`${target.day} vardiyasını neden koruyorsunuz?`, "") || "").trim();
        if (!protectedDay && (!reason || reason.length < 5)) {
          toast("Koruma gerekçesi en az 5 karakter olmalıdır.", true);
          return;
        }
        if (!window.confirm(`${target.day}: ${protectedDay ? "Vardiya koruması kaldırılsın mı?" : `“${reason}” gerekçesiyle vardiya korunsun mu?`}`)) return;
        protection.disabled = true;
        try {
          await request("/api/schedule/protection", { method: "PUT", body: JSON.stringify({
            month: period, row_id: savedRow.row_id, date: target.day,
            revision: source.revision, protected: !protectedDay, ...(protectedDay ? {} : { reason }),
          }) });
          dialog.close();
          toast(protectedDay ? "Vardiya koruması kaldırıldı." : "Vardiya korumaya alındı.");
          await loadSchedule();
        } catch (error) {
          toast(error.message, true);
        } finally {
          protection.disabled = false;
        }
      };
    }).catch((error) => { if (dialog.open && state.quickDialogTarget === target) toast(error.message, true); });
  }
  if (window.easyUsability) window.easyUsability.showCellPanel(dialog, target);
  else if (!dialog.open) dialog.showModal();
}

function copyScheduleDay() {
  const target = state.quickDialogTarget;
  const row = target?.rowId && state.schedule.entries.find((entry) => entry.row_id === target.rowId);
  const item = row && scheduleRowForDay(row, target.day, false)?.days[target.day];
  if (!item) {
    toast("Seçili personelin bu gününde kopyalanacak vardiya veya durum yok.", true);
    return;
  }
  state.scheduleClipboard = { type: "day", item: structuredClone(item) };
  el("#paste-schedule-day").disabled = false;
  toast("Günlük giriş panoya kopyalandı; başka bir hücrede yapıştırabilirsiniz.");
}

function pasteScheduleDay() {
  const target = state.quickDialogTarget;
  if (!target || state.scheduleClipboard?.type !== "day") return;
  const row = resolveScheduleTarget(target);
  if (!row) return;
  const current = scheduleRowForDay(row, target.day, false)?.days[target.day];
  if (current && !window.confirm("Hedef gündeki kayıt değiştirilecek. Devam edilsin mi?")) return;
  const owner = scheduleRowForDay(row, target.day, true);
  if (!owner) return;
  owner.days[target.day] = structuredClone(state.scheduleClipboard.item);
  if (target.day.slice(0, 7) !== state.scheduleMonth) state.neighborDirty.add(target.day.slice(0, 7));
  setScheduleDirty();
  renderSchedule();
  state.quickDialogTarget = null;
  el("#cell-quick-entry-dialog").close();
  toast("Günlük giriş yapıştırıldı. Değişiklikleri kaydedin.");
}

function copyScheduleWeek() {
  const target = state.quickDialogTarget;
  const row = target?.rowId && state.schedule.entries.find((entry) => entry.row_id === target.rowId);
  if (!row) return;
  const monday = weekStarts(state.scheduleMonth)[state.weekIndex];
  const days = Array.from({ length: 7 }, (_, offset) => {
    const day = dateKey(localDate(monday.getFullYear(), monday.getMonth(), monday.getDate() + offset));
    return scheduleRowForDay(row, day, false)?.days[day]
      ? structuredClone(scheduleRowForDay(row, day, false).days[day])
      : null;
  });
  if (!days.some(Boolean)) {
    toast("Seçili personelin bu haftasında kopyalanacak vardiya veya durum yok.", true);
    return;
  }
  state.scheduleClipboard = { type: "week", days };
  el("#paste-schedule-week").disabled = false;
  toast("Haftalık personel satırı panoya kopyalandı; başka bir satırda yapıştırabilirsiniz.");
}

function pasteScheduleWeek() {
  const target = state.quickDialogTarget;
  if (!target || state.scheduleClipboard?.type !== "week") return;
  const row = resolveScheduleTarget(target);
  if (!row) return;
  const monday = weekStarts(state.scheduleMonth)[state.weekIndex];
  const targetDays = Array.from({ length: 7 }, (_, offset) =>
    dateKey(localDate(monday.getFullYear(), monday.getMonth(), monday.getDate() + offset)));
  const targetHasData = targetDays.some((day) => scheduleRowForDay(row, day, false)?.days[day]);
  if (targetHasData && !window.confirm("Hedef personelin bu haftadaki kayıtları değiştirilecek. Devam edilsin mi?")) return;

  const requiredPeriods = new Set(state.scheduleClipboard.days.flatMap((item, offset) => {
    const day = targetDays[offset];
    return item && day.slice(0, 7) !== state.scheduleMonth ? [day.slice(0, 7)] : [];
  }));
  for (const period of requiredPeriods) {
    const data = state.neighborSchedules[period];
    if (!data) {
      toast(`${period} komşu ayın vardiya verisi yüklenemedi.`, true);
      return;
    }
    const exists = data.entries.some((entry) =>
      entry.employee_id === row.employee_id && entry.section === row.section);
    const sectionCount = data.entries.filter((entry) => entry.section === row.section).length;
    if (!exists && sectionCount >= SECTION_LIMITS[row.section]) {
      toast(`${period} ${row.section} bölümünde haftayı yapıştırmak için boş satır yok.`, true);
      return;
    }
  }

  for (let offset = 0; offset < targetDays.length; offset += 1) {
    const day = targetDays[offset];
    const item = state.scheduleClipboard.days[offset];
    const owner = scheduleRowForDay(row, day, Boolean(item));
    if (!owner) continue;
    if (item) owner.days[day] = structuredClone(item);
    else delete owner.days[day];
    if (day.slice(0, 7) !== state.scheduleMonth) state.neighborDirty.add(day.slice(0, 7));
  }
  setScheduleDirty();
  renderSchedule();
  state.quickDialogTarget = null;
  el("#cell-quick-entry-dialog").close();
  toast("Haftalık vardiya satırı yapıştırıldı. Değişiklikleri kaydedin.");
}

function setQuickEntryFormMode(entry) {
  const preset = entry && !entry.custom;
  const workPreset = preset && entry.kind === "work";
  el("#quick-entry-code").disabled = preset && !workPreset;
  el("#quick-entry-start").disabled = preset && !workPreset;
  el("#quick-entry-end").disabled = preset && !workPreset;
  const hint = el("#quick-entry-preset-hint");
  if (hint) hint.hidden = !preset;
}

function resetQuickEntryForm() {
  el("#quick-entry-form").reset();
  el("#quick-entry-id").value = "";
  el("#save-quick-entry").textContent = "Hızlı girişi ekle";
  setQuickEntryFormMode(null);
  renderShiftTypeOptions("");
  updateQuickEntryTypePreview();
}

function updateQuickEntryTypePreview() {
  const output = el("#quick-entry-type-preview");
  if (!output) return;
  const start = parseTimeText(el("#quick-entry-start").value);
  const end = parseTimeText(el("#quick-entry-end").value);
  const chosen = el("#quick-entry-shift-type").value;
  const type = shiftTypeById(chosen || classifyShift(start, end));
  if (!start || !end) {
    output.textContent = "Saat girilirse vardiya türü ve rengi otomatik önerilir.";
    output.style.removeProperty("--type-color");
    return;
  }
  const minutes = ((timeMinutes(end) - timeMinutes(start) + 1440) % 1440) || 1440;
  const hours = minutes / 60;
  const daily = Number(state.overtimeSettings?.daily_limit_hours || 8);
  output.style.setProperty("--type-color", type?.color || "#ffffff");
  output.textContent = `${type ? type.label : "Tür yok"} · ${hours.toLocaleString("tr-TR", { maximumFractionDigits: 2 })} saat`
    + (hours > daily ? ` · ${(hours - daily).toLocaleString("tr-TR", { maximumFractionDigits: 2 })} saat fazla mesai olarak sayılır` : "");
}

function editQuickEntry(key) {
  const entry = quickEntryByKey(key);
  if (!entry) return;
  el("#quick-entry-id").value = key;
  el("#quick-entry-label").value = entry.label;
  el("#quick-entry-code").value = entry.code;
  el("#quick-entry-start").value = displayTime(entry.start);
  el("#quick-entry-end").value = displayTime(entry.end, true);
  renderShiftTypeOptions(entry.shift_type || "");
  setQuickEntryFormMode(entry);
  el("#save-quick-entry").textContent = "Hızlı girişi güncelle";
  updateQuickEntryTypePreview();
  el("#quick-entry-label").focus();
}

async function saveQuickEntry(event) {
  event.preventDefault();
  const key = el("#quick-entry-id").value;
  const existing = key ? quickEntryByKey(key) : null;
  const label = el("#quick-entry-label").value.trim();
  const code = el("#quick-entry-code").value.trim().toLocaleUpperCase("tr-TR");
  const startText = el("#quick-entry-start").value.trim();
  const endText = el("#quick-entry-end").value.trim();
  const start = startText ? parseTimeText(startText) : "";
  const end = endText ? parseTimeText(endText) : "";
  if (start === null || end === null) {
    toast("Saatleri 07:00, 0700, 7 veya 24:00 biçiminde yazın.", true);
    return;
  }
  if (Boolean(start) !== Boolean(end)) {
    toast("Vardiya giriş ve çıkış saatlerini birlikte girin.", true);
    return;
  }
  const shiftType = el("#quick-entry-shift-type").value;
  let entries = storedQuickEntries();
  if (existing && !existing.custom) {
    const preset = DEFAULT_QUICK_ENTRIES.find((item) => item.id === existing.id);
    const work = preset.kind === "work";
    const override = {
      id: presetOverride(preset.id)?.id || `preset-${preset.id}`,
      preset: preset.id,
      label,
      code: work ? code : preset.code,
      start: work ? start : "",
      end: work ? end : "",
    };
    if (shiftType) override.shift_type = shiftType;
    if (existing.hidden) override.hidden = true;
    entries = entries.filter((entry) => entry.preset !== preset.id).concat(override);
  } else {
    const updated = { id: existing?.id || newRowId(), label, code, start, end };
    if (shiftType) updated.shift_type = shiftType;
    entries = existing ? entries.map((entry) => entry.id === existing.id ? updated : entry) : [...entries, updated];
  }
  try {
    await persistQuickEntries(entries, existing ? "Hızlı giriş güncellendi." : "Hızlı giriş eklendi.");
    resetQuickEntryForm();
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteQuickEntry(id) {
  const entry = state.quickEntries.find((item) => item.id === id && !item.preset);
  if (!entry || !window.confirm(`“${entry.label}” hızlı girişi seçeneklerden silinsin mi? Kaydedilmiş vardiyalar korunur.`)) return;
  try {
    await persistQuickEntries(storedQuickEntries().filter((item) => item.id !== id),
      "Hızlı giriş seçeneklerden silindi; kayıtlı vardiyalar korundu.");
  } catch (error) {
    toast(error.message, true);
  }
}
async function selectScheduleWeek(index) {
  const previousIndex = state.weekIndex;
  const weeks = weekStarts(state.scheduleMonth);
  state.weekIndex = Math.max(0, Math.min(index, weeks.length - 1));
  if (state.weekIndex === previousIndex) return;
  try {
    await ensureTemplate("schedule", state.weekIndex);
    renderWeeks();
  } catch (error) {
    state.weekIndex = previousIndex;
    renderWeeks();
    toast(error.message, true);
  }
}

const PATTERN_KINDS = new Set(["work", "weekly_off", "overnight_exit"]);

function shiftDateKey(day, offset) {
  const [year, month, date] = day.split("-").map(Number);
  return dateKey(localDate(year, month - 1, date + offset));
}

function scheduleMonthDays(month) {
  const [year, number] = month.split("-").map(Number);
  const count = new Date(year, number, 0).getDate();
  return Array.from({ length: count }, (_, index) => dateKey(localDate(year, number - 1, index + 1)));
}

function currentScheduleRow(source) {
  let row = state.schedule.entries.find((entry) =>
    entry.employee_id === source.employee_id && entry.section === source.section);
  if (row) return { row, created: false };
  const sameSection = state.schedule.entries.filter((entry) => entry.section === source.section);
  if (sameSection.length >= SECTION_LIMITS[source.section]) return { row: null, created: false };
  const used = new Set(sameSection.map((entry) => entry.slot).filter(Number.isInteger));
  const free = Array.from({ length: SECTION_LIMITS[source.section] }, (_, index) => index + 1)
    .filter((slot) => !used.has(slot));
  const slot = free.includes(source.slot) ? source.slot : free[0];
  row = { row_id: newRowId(), employee_id: source.employee_id, section: source.section, slot, days: {} };
  return { row, created: true };
}

function copyPreviousMonth() {
  if (!state.schedule) return;
  const [year, month] = state.scheduleMonth.split("-").map(Number);
  const previous = dateKey(localDate(year, month - 2, 1)).slice(0, 7);
  const source = state.neighborSchedules[previous];
  const activeIds = new Set(state.people.filter((person) => person.active).map((person) => person.id));
  const sourceRows = (source?.entries || []).filter((entry) =>
    activeIds.has(entry.employee_id) && Object.keys(entry.days || {}).some((day) => day.startsWith(previous))
    && !Object.values(entry.days || {}).some((item) => item.kind === "resignation"));
  if (!sourceRows.length) {
    toast(`${MONTH_NAMES[(month + 10) % 12]} ayında kopyalanacak vardiya yok.`, true);
    return;
  }
  if (!window.confirm(`${MONTH_NAMES[(month + 10) % 12]} ayının vardiya düzeni 4 haftalık döngüyle (aynı haftanın günü) bu aya kopyalanacak. Yalnız boş hücreler doldurulur; çalışma, hafta tatili ve geceden çıkış kopyalanır, izin/rapor gibi tek seferlik durumlar kopyalanmaz. Devam edilsin mi?`)) return;
  const days = scheduleMonthDays(state.scheduleMonth);
  let filled = 0;
  let skipped = 0;
  const additions = [];
  for (const sourceRow of sourceRows) {
    const { row, created } = currentScheduleRow(sourceRow);
    if (!row) { skipped += 1; continue; }
    let changed = 0;
    for (const day of days) {
      if (row.days[day]) continue;
      const from = shiftDateKey(day, -28);
      const item = from.startsWith(previous) ? sourceRow.days[from] : row.days[from];
      if (!item || !PATTERN_KINDS.has(item.kind)) continue;
      row.days[day] = structuredClone(item);
      changed += 1;
    }
    if (created && changed) {
      state.schedule.entries.push(row);
      additions.push(row);
    }
    filled += changed;
  }
  if (!filled) {
    toast(skipped ? "Bölümlerde boş satır olmadığından kopyalama yapılamadı." : "Doldurulacak boş hücre bulunamadı.", true);
    return;
  }
  setScheduleDirty();
  renderSchedule();
  toast(`${filled} hücre önceki aydan dolduruldu${additions.length ? `, ${additions.length} satır eklendi` : ""}${skipped ? `; ${skipped} kişi için bölümde yer yok` : ""}. Kaydetmeyi unutmayın.`);
}

function suggestionFor(row, day) {
  for (const offset of [-7, -14, -21, -28]) {
    const from = shiftDateKey(day, offset);
    const owner = from.startsWith(state.scheduleMonth) ? row : (state.neighborSchedules[from.slice(0, 7)]?.entries || [])
      .find((entry) => entry.employee_id === row.employee_id && entry.section === row.section);
    const item = owner?.days?.[from];
    if (item && PATTERN_KINDS.has(item.kind)) return structuredClone(item);
  }
  const counts = new Map();
  for (const [day, item] of Object.entries(row.days || {})) {
    if (!day.startsWith(state.scheduleMonth) || item.kind !== "work") continue;
    const key = JSON.stringify(item);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const best = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
  return best ? JSON.parse(best[0]) : null;
}

async function suggestWeek() {
  if (!state.schedule) return;
  const monday = weekStarts(state.scheduleMonth)[state.weekIndex];
  const days = Array.from({ length: 7 }, (_, offset) =>
    dateKey(localDate(monday.getFullYear(), monday.getMonth(), monday.getDate() + offset)))
    .filter((day) => day.startsWith(state.scheduleMonth));
  const candidates = [];
  for (const row of state.schedule.entries) {
    for (const day of days) {
      if (row.days[day]) continue;
      const item = suggestionFor(row, day);
      if (item) candidates.push([row, day, item]);
    }
  }
  if (!candidates.length) {
    toast("Bu haftada öneri yapılabilecek boş hücre yok.", true);
    return;
  }
  const employeeIds = [...new Set(candidates.map(([row]) => row.employee_id))];
  const [profiles, leaveCalendar, complianceSettings] = await Promise.all([
    Promise.all(employeeIds.map(async (employeeId) => {
      const data = await request(`/api/employee-profile?employee_id=${employeeId}`);
      return [employeeId, data.profile || {}];
    })),
    request(`/api/leave-requests?month=${encodeURIComponent(state.scheduleMonth)}`),
    request("/api/compliance-settings"),
  ]);
  const profileById = new Map(profiles);
  const approvedRequests = leaveCalendar.requests.filter((item) => item.status === "approved");
  const rejected = new Map();
  const accepted = [];
  const candidateSchedule = structuredClone(state.schedule);
  const baselineWarnings = scheduleValidationWarnings(candidateSchedule, complianceSettings.legal_rest_hours);
  const warningKey = (warning) => JSON.stringify([
    warning.type, warning.employeeId, warning.day, warning.previousDay,
    warning.section, warning.previousSection, [...(warning.sections || [])].sort(),
  ]);
  const baselineKeys = new Set(baselineWarnings.map(warningKey));
  const reject = (reason) => rejected.set(reason, (rejected.get(reason) || 0) + 1);
  for (const [row, day, item] of candidates) {
    const candidateRow = candidateSchedule.entries.find((entry) => entry.row_id === row.row_id);
    if (!candidateRow) {
      reject("çizelge satırı değişti");
      continue;
    }
    if (item.kind !== "work") {
      candidateRow.days[day] = item;
      accepted.push([row, day, item]);
      continue;
    }
    const profile = profileById.get(row.employee_id) || {};
    const preferences = profile.preferences || {};
    const shiftDate = new Date(`${day}T12:00:00`);
    const startMinutes = timeMinutes(item.start);
    const rawEndMinutes = timeMinutes(item.end);
    const overnight = rawEndMinutes <= startMinutes;
    const endMinutes = rawEndMinutes + (overnight ? 1440 : 0);
    const shiftDays = [day];
    if (overnight && rawEndMinutes > 0) shiftDays.push(shiftDateKey(day, 1));
    const nightMinutes = Math.max(0, Math.min(endMinutes, 360) - Math.max(startMinutes, -240))
      + Math.max(0, Math.min(endMinutes, 1800) - Math.max(startMinutes, 1200));
    let reason = "";
    if (shiftDays.some((shiftDay) =>
      (preferences.unavailable_weekdays || []).includes((new Date(`${shiftDay}T12:00:00`).getDay() + 6) % 7))) {
      reason = "çalışılamayan haftalık tercih";
    }
    if (!reason && (preferences.unavailable_ranges || []).some((range) =>
      shiftDays.some((shiftDay) => shiftDay >= range.start && shiftDay <= range.end))) {
      reason = "çalışılamayan tarih aralığı";
    }
    if (!reason && preferences.no_night && nightMinutes >= 180) reason = "gece vardiyası tercihi";
    if (!reason && preferences.no_weekend && shiftDays.some((shiftDay) =>
      new Date(`${shiftDay}T12:00:00`).getDay() === 0 || new Date(`${shiftDay}T12:00:00`).getDay() === 6)) {
      reason = "hafta sonu tercihi";
    }
    if (!reason && approvedRequests.some((requestItem) =>
      requestItem.employee_id === row.employee_id
      && shiftDays.some((shiftDay) => shiftDay >= requestItem.start && shiftDay <= requestItem.end))) {
      reason = "onaylı izin talebi";
    }
    if (reason) {
      reject(reason);
      continue;
    }
    candidateRow.days[day] = item;
    const warning = scheduleValidationWarnings(candidateSchedule, complianceSettings.legal_rest_hours)
      .find((entry) => entry.employeeId === row.employee_id
        && ["overlap", "rest"].includes(entry.type)
        && !baselineKeys.has(warningKey(entry)));
    if (warning) {
      delete candidateRow.days[day];
      reject(warning.type === "overlap" ? "vardiya çakışması" : "yetersiz dinlenme");
      continue;
    }
    accepted.push([row, day, item]);
  }
  if (!accepted.length) {
    toast(`Kısıtlarla uyumlu öneri bulunamadı. ${[...rejected.entries()].map(([reason, count]) => `${count} ${reason}`).join(", ")}.`, true);
    return;
  }
  const people = new Set(accepted.map(([row]) => row.employee_id)).size;
  const skipped = [...rejected.entries()].map(([reason, count]) => `${count} ${reason}`).join(", ");
  const planCheck = await request("/api/schedule/suggestion-check", {
    method: "POST",
    body: JSON.stringify({ month: state.scheduleMonth, entries: candidateSchedule.entries }),
  });
  const prompt = `${people} personelin ${accepted.length} boş hücresi önceki haftaların aynı gününe (yoksa kişinin en sık vardiyasına) göre önerilecek. Tercihler, onaylı izinler ve yasal dinlenme/çakışma denetlenir.\nSaatlik kadro uyarıları: ${planCheck.baseline.staffing} → ${planCheck.proposed.staffing}; yetkinlik kapsamı uyarıları: ${planCheck.baseline.skill_coverage} → ${planCheck.proposed.skill_coverage}.${skipped ? `\nAtlanan: ${skipped}.` : ""}\nDevam edilsin mi?`;
  if (!window.confirm(prompt)) return;
  for (const [row, day, item] of accepted) row.days[day] = item;
  setScheduleDirty();
  renderSchedule();
  toast(`${accepted.length} hücre önerildi. Kontrol edip kaydedin; geri almak için Ctrl+Z.`);
}

function copyPreviousWeek() {
  if (!state.schedule || state.weekIndex === 0) return;
  const weeks = weekStarts(state.scheduleMonth);
  const sourceMonday = weeks[state.weekIndex - 1];
  const targetMonday = weeks[state.weekIndex];
  const daysToCopy = Array.from({ length: 7 }, (_, offset) => {
    const source = dateKey(localDate(sourceMonday.getFullYear(), sourceMonday.getMonth(), sourceMonday.getDate() + offset));
    const target = dateKey(localDate(targetMonday.getFullYear(), targetMonday.getMonth(), targetMonday.getDate() + offset));
    return [source, target];
  });
  const sourceHasData = state.schedule.entries.some((entry) =>
    daysToCopy.some(([source]) => scheduleRowForDay(entry, source, false)?.days[source]));
  if (!sourceHasData) {
    toast("Önceki haftada kopyalanacak vardiya veya durum yok.", true);
    return;
  }
  const targetHasData = state.schedule.entries.some((entry) =>
    daysToCopy.some(([, target]) => scheduleRowForDay(entry, target, false)?.days[target]));
  if (targetHasData && !window.confirm("Önceki haftadaki kayıtlar seçili haftanın aynı günlerine kopyalanacak. Hedefteki bu günlerin mevcut kayıtları değiştirilsin mi?")) return;
  for (const [period, data] of Object.entries(state.neighborSchedules)) {
    for (const section of SECTIONS) {
      const missing = state.schedule.entries.filter((entry) => entry.section === section
        && !data.entries.some((item) => item.employee_id === entry.employee_id && item.section === section)
        && daysToCopy.some(([source, target]) => target.startsWith(`${period}-`)
          && scheduleRowForDay(entry, source, false)?.days[source])).length;
      if (data.entries.filter((entry) => entry.section === section).length + missing > SECTION_LIMITS[section]) {
        toast(`${period} ${section} bölümünde kopyalama için yeterli boş satır yok.`, true);
        return;
      }
    }
  }
  for (const entry of state.schedule.entries) {
    for (const [source, target] of daysToCopy) {
      const sourceItem = scheduleRowForDay(entry, source, false)?.days[source];
      const owner = scheduleRowForDay(entry, target, Boolean(sourceItem));
      if (!owner) continue;
      if (sourceItem) owner.days[target] = structuredClone(sourceItem);
      else delete owner.days[target];
      if (target.slice(0, 7) !== state.scheduleMonth) state.neighborDirty.add(target.slice(0, 7));
    }
  }
  setScheduleDirty();
  renderSchedule();
  toast("Önceki hafta kopyalandı. Değişiklikleri kaydetmeyi unutmayın.");
}

function renderScheduleTemplateOptions(selectedId = el("#schedule-template-select").value) {
  const select = el("#schedule-template-select");
  select.innerHTML = `<option value="">Şablon seçin</option>${state.scheduleTemplates
    .map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")}`;
  select.value = state.scheduleTemplates.some((item) => item.id === selectedId) ? selectedId : "";
  const enabled = Boolean(select.value);
  el("#apply-schedule-template").disabled = !enabled;
  el("#delete-schedule-template").disabled = !enabled;
}

async function loadScheduleTemplates() {
  const result = await request("/api/schedule-templates");
  state.scheduleTemplates = result.templates;
  renderScheduleTemplateOptions("");
}

async function saveScheduleTemplate(event) {
  event.preventDefault();
  const nameInput = el("#schedule-template-name");
  const name = nameInput.value.trim();
  if (!name || !state.schedule) {
    toast("Şablon için bir ad girin ve vardiya çizelgesinin yüklenmesini bekleyin.", true);
    return;
  }
  const weeks = weekStarts(state.scheduleMonth);
  const monday = weeks[state.weekIndex];
  const days = Array.from({ length: 7 }, (_, offset) =>
    dateKey(localDate(monday.getFullYear(), monday.getMonth(), monday.getDate() + offset)));
  const entries = state.schedule.entries.map((entry) => {
    const shifts = {};
    days.forEach((day, offset) => {
      const owner = scheduleRowForDay(entry, day, false);
      if (owner?.days[day]) shifts[String(offset)] = structuredClone(owner.days[day]);
    });
    return {
      employee_id: entry.employee_id,
      section: entry.section,
      slot: entry.slot,
      days: shifts,
    };
  }).filter((entry) => Object.keys(entry.days).length);
  if (!entries.length) {
    toast("Seçili haftada kaydedilecek vardiya veya durum bulunmuyor.", true);
    return;
  }
  const submit = el("#save-schedule-template");
  submit.disabled = true;
  try {
    const template = { id: newRowId(), name, entries };
    const result = await request("/api/schedule-templates", {
      method: "PUT",
      body: JSON.stringify({ templates: [...state.scheduleTemplates, template] }),
    });
    state.scheduleTemplates = result.templates;
    renderScheduleTemplateOptions(template.id);
    nameInput.value = "";
    el("#schedule-template-status").textContent = `“${name}” şablonu kaydedildi.`;
    toast("Haftalık vardiya şablonu kaydedildi.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    submit.disabled = false;
  }
}

function applyScheduleTemplate() {
  const template = state.scheduleTemplates.find(
    (item) => item.id === el("#schedule-template-select").value,
  );
  if (!template || !state.schedule) return;
  const missingEmployees = template.entries.filter((saved) =>
    !state.schedule.entries.some((entry) =>
      entry.employee_id === saved.employee_id && entry.section === saved.section));
  const currentCounts = Object.fromEntries(SECTIONS.map((section) => [
    section, state.schedule.entries.filter((entry) => entry.section === section).length,
  ]));
  const missingBySection = Object.fromEntries(SECTIONS.map((section) => [
    section, missingEmployees.filter((entry) => entry.section === section).length,
  ]));
  for (const section of SECTIONS) {
    if (currentCounts[section] + missingBySection[section] > SECTION_LIMITS[section]) {
      toast(`${section} bölümünde şablondaki personeller için yeterli boş satır yok.`, true);
      return;
    }
  }
  const employeeIds = new Set(state.schedule.entries.map((entry) => entry.employee_id));
  for (const saved of missingEmployees) employeeIds.add(saved.employee_id);
  if (employeeIds.size > SECTION_SETTINGS.max_personnel) {
    toast(`Bir ayda en fazla ${SECTION_SETTINGS.max_personnel} farklı personel olabilir.`, true);
    return;
  }

  const weeks = weekStarts(state.scheduleMonth);
  const monday = weeks[state.weekIndex];
  const targetDays = Array.from({ length: 7 }, (_, offset) =>
    dateKey(localDate(monday.getFullYear(), monday.getMonth(), monday.getDate() + offset)));
  const targetHasData = template.entries.some((saved) => {
    const entry = state.schedule.entries.find((item) =>
      item.employee_id === saved.employee_id && item.section === saved.section);
    return entry && targetDays.some((day) => scheduleRowForDay(entry, day, false)?.days[day]);
  });
  if (targetHasData && !window.confirm(
    "Şablon, seçili haftadaki kayıtlı hücreleri değiştirecek. Devam edilsin mi?",
  )) return;

  const neighborNeeded = {};
  for (const saved of template.entries) {
    for (let offset = 0; offset < targetDays.length; offset += 1) {
      const day = targetDays[offset];
      if (day.slice(0, 7) === state.scheduleMonth || !saved.days[String(offset)]) continue;
      const period = day.slice(0, 7);
      const data = state.neighborSchedules[period];
      if (!data) {
        toast(`${period} komşu ayın vardiya verisi yüklenemedi.`, true);
        return;
      }
      const exists = data.entries.some((entry) =>
        entry.employee_id === saved.employee_id && entry.section === saved.section);
      if (!exists) {
        neighborNeeded[period] ||= new Set();
        neighborNeeded[period].add(`${saved.employee_id}:${saved.section}`);
      }
    }
  }
  for (const [period, needed] of Object.entries(neighborNeeded)) {
    const data = state.neighborSchedules[period];
    for (const section of SECTIONS) {
      const used = data.entries.filter((entry) => entry.section === section).length;
      const additional = [...needed].filter((key) => key.endsWith(`:${section}`)).length;
      if (used + additional > SECTION_LIMITS[section]) {
        toast(`${period} ${section} bölümünde şablon için yeterli boş satır yok.`, true);
        return;
      }
    }
  }

  for (const saved of missingEmployees) {
    const usedSlots = new Set(state.schedule.entries
      .filter((entry) => entry.section === saved.section)
      .map((entry) => entry.slot));
    const slot = Array.from({ length: SECTION_LIMITS[saved.section] }, (_, index) => index + 1)
      .find((candidate) => !usedSlots.has(candidate));
    if (!slot) {
      toast(`${saved.section} bölümünde boş personel satırı kalmadı.`, true);
      return;
    }
    state.schedule.entries.push({
      row_id: newRowId(),
      employee_id: saved.employee_id,
      section: saved.section,
      slot,
      days: {},
    });
  }

  for (const saved of template.entries) {
    const row = state.schedule.entries.find((entry) =>
      entry.employee_id === saved.employee_id && entry.section === saved.section);
    for (let offset = 0; offset < targetDays.length; offset += 1) {
      const day = targetDays[offset];
      const item = saved.days[String(offset)];
      const owner = scheduleRowForDay(row, day, Boolean(item));
      if (owner) {
        if (item) owner.days[day] = structuredClone(item);
        else delete owner.days[day];
        if (day.slice(0, 7) !== state.scheduleMonth) state.neighborDirty.add(day.slice(0, 7));
      }
    }
  }
  setScheduleDirty();
  renderSchedule();
  toast("Şablon seçili haftaya uygulandı. Değişiklikleri kaydedin.");
}

async function deleteScheduleTemplate() {
  const id = el("#schedule-template-select").value;
  const template = state.scheduleTemplates.find((item) => item.id === id);
  if (!template || !window.confirm(`“${template.name}” şablonu silinsin mi? Çizelge kayıtları etkilenmez.`)) return;
  try {
    const result = await request("/api/schedule-templates", {
      method: "PUT",
      body: JSON.stringify({ templates: state.scheduleTemplates.filter((item) => item.id !== id) }),
    });
    state.scheduleTemplates = result.templates;
    renderScheduleTemplateOptions("");
    el("#schedule-template-status").textContent = `“${template.name}” şablonu silindi.`;
    toast("Şablon silindi; kayıtlı vardiyalar korundu.");
  } catch (error) {
    toast(error.message, true);
  }
}

function renderBulkSchedulePeople() {
  const people = state.people.filter((person) => person.active);
  const list = el("#bulk-schedule-people");
  if (!people.length) {
    list.innerHTML = `<p class="bulk-employee-empty">Önce personel listesine çalışan ekleyin.</p>`;
  } else {
    list.innerHTML = people.map((person) =>
      `<label><input type="checkbox" name="bulk-schedule-person" value="${person.id}"><span>${escapeHtml(person.name)}</span></label>`
    ).join("");
  }
  updateBulkScheduleSelectAll();
}

function updateBulkScheduleSelectAll() {
  const boxes = [...document.querySelectorAll('input[name="bulk-schedule-person"]')];
  const selected = boxes.filter((box) => box.checked).length;
  el("#bulk-schedule-select-all").textContent =
    boxes.length && selected === boxes.length ? "Seçimi kaldır" : "Tümünü seç";
}

function renderBulkScheduleEntries() {
  el("#bulk-schedule-entry").innerHTML = `<option value="">Vardiya veya durum seçin</option>${allQuickEntries()
    .map((entry) => `<option value="${escapeHtml(quickEntryKey(entry))}">${escapeHtml(entry.label)} · ${escapeHtml(entry.code)}</option>`)
    .join("")}`;
}

function bulkScheduleDraft() {
  const section = el("#bulk-schedule-section").value;
  const start = el("#bulk-schedule-start").value;
  const end = el("#bulk-schedule-end").value;
  const employeeIds = [...document.querySelectorAll('input[name="bulk-schedule-person"]:checked')]
    .map((box) => Number(box.value));
  const preset = quickEntryByKey(el("#bulk-schedule-entry").value);
  if (!SECTIONS.includes(section) || !start || !end || !employeeIds.length || !preset) {
    return { error: "Bölüm, tarih aralığı, personel ve vardiya/durum seçin." };
  }
  if (start > end || start.slice(0, 7) !== state.scheduleMonth || end.slice(0, 7) !== state.scheduleMonth) {
    return { error: "Başlangıç ve bitiş tarihleri seçili ay içinde olmalıdır." };
  }
  const firstDay = new Date(`${start}T12:00:00`);
  const lastDay = new Date(`${end}T12:00:00`);
  const dayCount = Math.round((lastDay - firstDay) / 86400000) + 1;
  if (dayCount < 1 || dayCount > 31) {
    return { error: "Bir toplu atamada en fazla 31 gün seçilebilir." };
  }
  const days = Array.from({ length: dayCount }, (_, index) =>
    dateKey(localDate(firstDay.getFullYear(), firstDay.getMonth(), firstDay.getDate() + index)));
  const missing = employeeIds.filter((employeeId) =>
    !state.schedule.entries.some((entry) =>
      entry.employee_id === employeeId && entry.section === section));
  const sectionCount = state.schedule.entries.filter((entry) => entry.section === section).length;
  if (sectionCount + missing.length > SECTION_LIMITS[section]) {
    return { error: `${section} bölümünde seçilen personeller için yeterli boş satır yok.` };
  }
  const uniqueEmployees = new Set(state.schedule.entries.map((entry) => entry.employee_id));
  employeeIds.forEach((employeeId) => uniqueEmployees.add(employeeId));
  if (uniqueEmployees.size > SECTION_SETTINGS.max_personnel) {
    return { error: `Bir ayda en fazla ${SECTION_SETTINGS.max_personnel} farklı personel çizelgede yer alabilir.` };
  }
  const overwriteCount = employeeIds.reduce((total, employeeId) => {
    const row = state.schedule.entries.find((entry) =>
      entry.employee_id === employeeId && entry.section === section);
    return total + days.filter((day) => row?.days[day]).length;
  }, 0);
  return {
    section, start, end, employeeIds, preset, days, dayCount, missing, overwriteCount,
    names: employeeIds.map((id) => state.people.find((person) => person.id === id)?.name || "Bilinmeyen personel"),
  };
}

function renderBulkSchedulePreview() {
  const preview = el("#bulk-schedule-preview");
  if (!preview) return;
  const draft = bulkScheduleDraft();
  if (draft.error) {
    preview.innerHTML = `<p class="bulk-preview-muted">${escapeHtml(draft.error)}</p>`;
    return;
  }
  const changeSummary = draft.overwriteCount
    ? `<strong>${draft.overwriteCount}</strong> mevcut hücre değiştirilecek.`
    : "Mevcut dolu hücre değişmeyecek.";
  const newRows = draft.missing.length
    ? ` Yeni personel satırı eklenecek: ${draft.missing.length}.`
    : "";
  const names = draft.names.map(escapeHtml).join(", ");
  preview.innerHTML = `<strong>Önizleme</strong><p>${draft.names.length} personel · ${draft.section} · ${dateLabel(new Date(`${draft.start}T12:00:00`))} – ${dateLabel(new Date(`${draft.end}T12:00:00`))} · ${draft.preset.label}</p><p>${changeSummary}${newRows}</p><p class="bulk-preview-muted">${names}</p>`;
}

function openBulkScheduleDialog() {
  if (!state.schedule) {
    toast("Vardiya çizelgesi yüklenmeden toplu atama yapılamaz.", true);
    return;
  }
  const weeks = weekStarts(state.scheduleMonth);
  const weekStart = weeks[state.weekIndex] || weeks[0];
  const weekEnd = localDate(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() + 6);
  const visibleMonthDays = monthDates(state.scheduleMonth)
    .filter((day) => day >= weekStart && day <= weekEnd);
  const defaultDays = visibleMonthDays.length ? visibleMonthDays : monthDates(state.scheduleMonth);
  el("#bulk-schedule-start").value = dateKey(defaultDays[0]);
  el("#bulk-schedule-end").value = dateKey(defaultDays[defaultDays.length - 1]);
  el("#bulk-schedule-section").value = SECTIONS[0];
  renderBulkSchedulePeople();
  renderBulkScheduleEntries();
  renderBulkSchedulePreview();
  el("#bulk-schedule-dialog").showModal();
}

function applyBulkScheduleAssignment(event) {
  event.preventDefault();
  const draft = bulkScheduleDraft();
  if (draft.error) {
    toast(draft.error, true);
    renderBulkSchedulePreview();
    return;
  }
  const { section, employeeIds, preset, days, dayCount, missing, overwriteCount } = draft;
  if (overwriteCount && !window.confirm(
    `${employeeIds.length} personel için ${dayCount} gün seçildi; ${overwriteCount} dolu hücre değiştirilecek. Devam edilsin mi?`,
  )) return;

  const item = scheduleItemForQuickEntry(preset);
  if (!item) {
    toast("Seçilen hızlı giriş uygulanamıyor.", true);
    return;
  }
  item.fill_color = preset.color || "#ffffff";
  for (const employeeId of missing) {
    const usedSlots = new Set(state.schedule.entries
      .filter((entry) => entry.section === section)
      .map((entry) => entry.slot));
    const slot = Array.from({ length: SECTION_LIMITS[section] }, (_, index) => index + 1)
      .find((candidate) => !usedSlots.has(candidate));
    if (!slot) {
      toast(`${section} bölümünde boş personel satırı kalmadı.`, true);
      return;
    }
    state.schedule.entries.push({
      row_id: newRowId(),
      employee_id: employeeId,
      section,
      slot,
      days: {},
    });
  }
  for (const employeeId of employeeIds) {
    const row = state.schedule.entries.find((entry) =>
      entry.employee_id === employeeId && entry.section === section);
    for (const day of days) row.days[day] = structuredClone(item);
  }
  setScheduleDirty();
  renderSchedule();
  el("#bulk-schedule-dialog").close();
  toast(`${employeeIds.length} personele ${dayCount} günlük vardiya atandı. Kaydetmeyi unutmayın.`);
}

const FORMAT_FONT_NAMES = ["Arial", "Calibri", "Segoe UI", "Times New Roman", "Verdana", "Tahoma",
  "Courier New", "Georgia", "Cambria", "Trebuchet MS", "Arial Narrow", "Consolas"];
const GHOST_ROWS = 6;
const GHOST_COLS = 4;
const BORDER_CSS = {
  thin: "1px solid", medium: "2px solid", thick: "3px solid", double: "3px double",
  dashed: "1px dashed", dotted: "1px dotted", hair: "1px dotted",
};

function ghostMargin(kind, settings) {
  if (kind === "attendance" && (state.attendanceQuery || state.attendanceFilter !== "all")) return { rows: 0, cols: 0 };
  return {
    rows: Math.max(0, Math.min(GHOST_ROWS, 50 - (settings.extra_rows || 0))),
    cols: Math.max(0, Math.min(GHOST_COLS, 20 - (settings.extra_cols || 0))),
  };
}

// Cells edited in the blank margin around the sheet become real extra rows/columns.
function fitGhostExtents(kind, sheet, previous) {
  const table = el(`#${kind}-table`);
  const realRows = Number(table?.dataset.realRows);
  const realCols = Number(table?.dataset.realCols);
  if (!realRows || !realCols) return;
  let maxRow = 0;
  let maxCol = 0;
  for (const [address, value] of Object.entries(sheet.cells || {})) {
    if (JSON.stringify(previous?.cells?.[address]) === JSON.stringify(value)) continue;
    const [row, col] = address.split(":").map(Number);
    maxRow = Math.max(maxRow, row);
    maxCol = Math.max(maxCol, col);
  }
  const previousMerges = new Set((previous?.merges_add || []).map((merge) => JSON.stringify(merge)));
  for (const merge of sheet.merges_add || []) {
    if (previousMerges.has(JSON.stringify(merge))) continue;
    maxRow = Math.max(maxRow, merge.row + merge.rowspan - 1);
    maxCol = Math.max(maxCol, merge.column + merge.colspan - 1);
  }
  for (const [position, size] of Object.entries(sheet.row_heights || {})) {
    if (previous?.row_heights?.[position] !== size) maxRow = Math.max(maxRow, Number(position));
  }
  for (const [position, size] of Object.entries(sheet.column_widths || {})) {
    if (previous?.column_widths?.[position] !== size) maxCol = Math.max(maxCol, Number(position));
  }
  if (maxRow > realRows) sheet.extra_rows = Math.min(50, (sheet.extra_rows || 0) + maxRow - realRows);
  if (maxCol > realCols) sheet.extra_cols = Math.min(20, (sheet.extra_cols || 0) + maxCol - realCols);
}

function textDecorationStyle(custom) {
  const lines = [];
  if (custom.font_underline) lines.push("underline");
  if (custom.font_strike) lines.push("line-through");
  if (custom.font_underline === undefined && custom.font_strike === undefined) return "";
  return `;text-decoration:${lines.length ? lines.join(" ") : "none"}`;
}

function borderStyle(border) {
  if (!border) return "";
  return Object.entries(border).map(([side, value]) => value.style === "none"
    ? `;border-${side}:1px solid transparent`
    : `;border-${side}:${BORDER_CSS[value.style] || "1px solid"} ${value.color}`).join("");
}

function rotatedContent(html, rotation) {
  if (rotation === 255) return `<span class="stacked-cell-value">${html}</span>`;
  const degrees = rotation <= 90 ? -rotation : rotation - 90;
  return `<span class="custom-rotated-value" style="transform:rotate(${degrees}deg)">${html}</span>`;
}

function mergeMap(template, unmerged = new Set()) {
  const anchors = new Map();
  const covered = new Set();
  for (const merge of template.merges) {
    const key = `${merge.row}:${merge.column}`;
    if (unmerged.has(key)) continue;
    anchors.set(key, merge);
    for (let row = merge.row; row < merge.row + merge.rowspan; row += 1) {
      for (let column = merge.column; column < merge.column + merge.colspan; column += 1) {
        if (row !== merge.row || column !== merge.column) covered.add(`${row}:${column}`);
      }
    }
  }
  return { anchors, covered };
}

function excelWidthPixels(width) {
  return Math.max(1, Math.round(width * 7 + 5));
}

function renderTemplateGuides(kind) {
  const table = el(`#${kind}-table`);
  const frame = table?.closest(".table-frame");
  if (!table || !frame) return;
  table.querySelectorAll(
    ".template-guide-header, .template-guide-person, .template-guide-signature-cell, "
      + ".template-guide-page-break, .template-guide-row-overflow",
  )
    .forEach((cell) => cell.classList.remove(
      "template-guide-header", "template-guide-person", "template-guide-signature-cell",
      "template-guide-page-break", "template-guide-row-overflow",
    ));
  table.querySelectorAll("[data-template-page-break]").forEach((cell) => {
    cell.removeAttribute("data-template-page-break");
  });
  frame.querySelector(".template-guide-signature")?.remove();
  if (state.templateEdit !== kind) return;

  const cells = [...table.querySelectorAll("tbody td[data-source-row]")];
  const personCells = cells.filter((cell) => cell.classList.contains("attendance-person-name")
    || cell.querySelector(".employee-name-editor"));
  personCells.forEach((cell) => cell.classList.add("template-guide-person"));
  const firstPersonRow = personCells[0]?.closest("tr");
  const firstPersonSourceRow = Number(firstPersonRow?.dataset.layoutRow || 0);
  const headerRows = [...table.tBodies[0].rows].filter((row) => {
    const sourceRow = Number(row.dataset.layoutRow || 0);
    return sourceRow > 0 && (firstPersonSourceRow ? sourceRow < firstPersonSourceRow
      : sourceRow <= (kind === "schedule" ? 5 : 3));
  });
  headerRows.forEach((row) => row.querySelectorAll("td[data-source-row]")
    .forEach((cell) => cell.classList.add("template-guide-header")));

  const visibleRows = [...table.tBodies[0].rows]
    .filter((row) => row.getClientRects().length);
  const pageHeight = table.getBoundingClientRect().width * 194 / 281;
  const pageBreaks = [];
  const overflowingRows = [];
  let pageUsed = 0;
  let previousRow = null;
  visibleRows.forEach((row, index) => {
    const height = row.getBoundingClientRect().height;
    if (previousRow && pageUsed + height > pageHeight) {
      pageBreaks.push(previousRow);
      pageUsed = 0;
    }
    if (height > pageHeight) overflowingRows.push(row);
    pageUsed += height;
    previousRow = row;
  });
  pageBreaks.forEach((row, index) => {
    const cell = row.querySelector("td[data-source-row]");
    if (!cell) return;
    cell.classList.add("template-guide-page-break");
    cell.dataset.templatePageBreak = `Sayfa ${index + 1} sonu`;
  });
  overflowingRows.forEach((row) => row.querySelectorAll("td[data-source-row]")
    .forEach((cell) => cell.classList.add("template-guide-row-overflow")));

  const signaturePattern = /imza|hazırlayan|kontrol eden|onaylayan/i;
  cells.filter((cell) => signaturePattern.test(cell.textContent || ""))
    .forEach((cell) => cell.classList.add("template-guide-signature-cell"));
  const signature = document.createElement("div");
  signature.className = "template-guide-signature";
  signature.setAttribute("role", "note");
  signature.innerHTML = `<strong>İmza satırı</strong><span>${escapeHtml(
    sheetSettings(kind).page_setup?.footer_text?.trim() || "Sayfa alt bilgisinde düzenlenir",
  )}</span><small>Kesik çizgi yalnız şablon düzenleme kılavuzudur; çıktıya eklenmez.</small>`;
  table.after(signature);
  const signatureOverflow = Boolean(pageHeight && pageUsed + signature.getBoundingClientRect().height > pageHeight);
  if (signatureOverflow || overflowingRows.length) {
    signature.classList.add("template-guide-overflow");
    signature.querySelector("small").textContent = signatureOverflow
      ? "Uyarı: imza alanı bu sayfaya sığmıyor; sonraki sayfaya taşabilir. Kılavuz çıktıya eklenmez."
      : "Uyarı: en az bir tablo satırı yazdırılabilir sayfadan uzun. Kılavuz çıktıya eklenmez.";
  }
  signature.dataset.pageBreakCount = String(pageBreaks.length);
  signature.dataset.overflow = String(signatureOverflow || overflowingRows.length > 0);
}

function renderWorkbookTable(
  template,
  tableId,
  valueForCell,
  unmerged = new Set(),
  rowVisible = () => true,
  styleForCell = () => null,
  extraSlots = [],
) {
  const table = el(tableId);
  const kind = tableId === "#schedule-table" ? "schedule" : "attendance";
  const savedSettings = sheetSettings(kind);
  const ghost = state.formatMode ? ghostMargin(kind, savedSettings) : { rows: 0, cols: 0 };
  const settings = ghost.rows || ghost.cols
    ? { ...savedSettings, extra_rows: (savedSettings.extra_rows || 0) + ghost.rows,
      extra_cols: (savedSettings.extra_cols || 0) + ghost.cols }
    : savedSettings;
  const originalTemplate = template;
  const sourceMerges = new Map(originalTemplate.merges.map((merge) => [`${merge.row}:${merge.column}`, merge]));
  if (settings.extra_rows || settings.extra_cols || settings.insert_rows?.length
    || settings.insert_cols?.length || settings.delete_rows?.length || settings.delete_cols?.length) {
    const baseWidth = template.columns.length;
    const extraCols = settings.extra_cols || 0;
    const blankCell = () => ({ value: null, style: 0, type: "text", format: "General", rotation: 0 });
    const cells = template.cells.map((row) => [
      ...row, ...Array.from({ length: extraCols }, blankCell),
    ]);
    const rows = [...template.rows];
    for (let index = 0; index < (settings.extra_rows || 0); index += 1) {
      cells.push(Array.from({ length: baseWidth + extraCols }, blankCell));
      rows.push({ height: 15, hidden: false });
    }
    template = {
      ...template, rows, cells,
      columns: [...template.columns, ...Array.from({ length: extraCols }, () => ({ width: 12, hidden: false }))],
    };
  }
  let rowOrigins = template.rows.map((_, index) => index + 1);
  let colOrigins = template.columns.map((_, index) => index + 1);
  let rowPositions = rowOrigins.map((_, index) => index + 1);
  let colPositions = colOrigins.map((_, index) => index + 1);
  let rowInsertionIndexes = rowOrigins.map(() => null);
  let colInsertionIndexes = colOrigins.map(() => null);
  if (settings.insert_rows?.length || settings.insert_cols?.length
    || settings.delete_rows?.length || settings.delete_cols?.length) {
    template = { ...template, rows: [...template.rows], columns: [...template.columns],
      cells: template.cells.map((row) => [...row]), merges: template.merges.map((merge) => ({ ...merge })) };
    const blankCell = () => ({ value: null, style: 0, type: "text", format: "General", rotation: 0 });
    for (const [operationIndex, position] of (settings.insert_rows || []).entries()) {
      template.rows.splice(position - 1, 0, { height: 15, hidden: false });
      template.cells.splice(position - 1, 0, Array.from({ length: template.columns.length }, blankCell));
      rowOrigins.splice(position - 1, 0, null);
      rowPositions = rowPositions.map((value) => value >= position ? value + 1 : value);
      rowPositions.splice(position - 1, 0, position);
      rowInsertionIndexes.splice(position - 1, 0, operationIndex);
      template.merges.forEach((merge) => {
        if (merge.row >= position) merge.row += 1;
        else if (position < merge.row + merge.rowspan) merge.rowspan += 1;
      });
    }
    for (const [operationIndex, position] of (settings.insert_cols || []).entries()) {
      template.columns.splice(position - 1, 0, { width: 12, hidden: false });
      template.cells.forEach((row) => row.splice(position - 1, 0, blankCell()));
      colOrigins.splice(position - 1, 0, null);
      colPositions = colPositions.map((value) => value >= position ? value + 1 : value);
      colPositions.splice(position - 1, 0, position);
      colInsertionIndexes.splice(position - 1, 0, operationIndex);
      template.merges.forEach((merge) => {
        if (merge.column >= position) merge.column += 1;
        else if (position < merge.column + merge.colspan) merge.colspan += 1;
      });
    }
    for (const sourceRow of settings.delete_rows || []) {
      const position = rowOrigins.indexOf(sourceRow);
      if (position < 0) continue;
      template.rows.splice(position, 1);
      template.cells.splice(position, 1);
      rowOrigins.splice(position, 1);
      rowPositions.splice(position, 1);
      rowInsertionIndexes.splice(position, 1);
      template.merges = template.merges.filter((merge) =>
        !(merge.row <= position + 1 && merge.row + merge.rowspan > position + 1))
        .map((merge) => merge.row > position + 1 ? { ...merge, row: merge.row - 1 } : merge);
    }
    for (const sourceColumn of settings.delete_cols || []) {
      const position = colOrigins.indexOf(sourceColumn);
      if (position < 0) continue;
      template.columns.splice(position, 1);
      template.cells.forEach((row) => row.splice(position, 1));
      colOrigins.splice(position, 1);
      colPositions.splice(position, 1);
      colInsertionIndexes.splice(position, 1);
      template.merges = template.merges.filter((merge) =>
        !(merge.column <= position + 1 && merge.column + merge.colspan > position + 1))
        .map((merge) => merge.column > position + 1 ? { ...merge, column: merge.column - 1 } : merge);
    }
  }
  const removedMerges = new Set(settings.merges_remove || []);
  template = {
    ...template,
    merges: template.merges
      .filter((merge) => !removedMerges.has(`${merge.row}:${merge.column}`))
      .map((merge) => ({ ...merge, layoutAnchor: `${merge.row}:${merge.column}` })),
  };
  if (extraSlots.length) {
    template = { ...template, rows: [...template.rows], cells: [...template.cells] };
    const anchorIndex = (after) => {
      let index = rowOrigins.indexOf(after);
      for (let row = after; index < 0 && row > 0; row -= 1) index = rowOrigins.indexOf(row);
      return index < 0 ? template.rows.length - 1 : index;
    };
    extraSlots.forEach(({ row, after, source, cloneValues }) => {
      const sourceCells = originalTemplate.cells[source - 1] || [];
      const position = anchorIndex(after) + 1;
      template.rows.splice(position, 0, {
        height: originalTemplate.rows[source - 1]?.height ?? 22, hidden: false,
      });
      template.cells.splice(position, 0, colOrigins.map((column) => {
        const origin = column && sourceCells[column - 1] ? sourceCells[column - 1] : { style: 0 };
        return cloneValues ? { ...origin } : { ...origin, value: null, type: "text", rotation: 0 };
      }));
      rowOrigins.splice(position, 0, row);
      rowPositions.splice(position, 0, null);
      rowInsertionIndexes.splice(position, 0, null);
      template.merges.forEach((merge) => {
        if (merge.row >= position + 1) merge.row += 1;
        else if (position + 1 < merge.row + merge.rowspan) merge.rowspan += 1;
      });
    });
    const headerSources = CUSTOM_SECTION_HEADER_SOURCES.slice(1);
    for (const item of extraSlots.filter((slot) => slot.header === 1)) {
      const top = rowOrigins.indexOf(item.row) + 1;
      for (const merge of originalTemplate.merges) {
        if (merge.row < headerSources[0] || merge.row + merge.rowspan - 1 > headerSources.at(-1)) continue;
        const first = colOrigins.indexOf(merge.column);
        const last = colOrigins.indexOf(merge.column + merge.colspan - 1);
        if (first < 0 || last < first) continue;
        template.merges.push({
          row: top + merge.row - headerSources[0], column: first + 1,
          rowspan: merge.rowspan, colspan: last - first + 1,
          layoutAnchor: `${top + merge.row - headerSources[0]}:${first + 1}`,
        });
      }
    }
  }
  template = {
    ...template,
    columns: template.columns.map((column, index) => ({
      ...column, width: settings.column_widths?.[String(index + 1)] ?? column.width,
    })),
    rows: template.rows.map((row, index) => ({
      ...row, height: settings.row_heights?.[String(index + 1)] ?? row.height,
    })),
  };
  if (settings.merges_add?.length) {
    template = { ...template, merges: [...template.merges, ...settings.merges_add.map((merge) => ({ ...merge, custom: true }))] };
  }
  const merges = mergeMap(template, unmerged);
  const realRowCount = template.rows.length - ghost.rows;
  const realColCount = template.columns.length - ghost.cols;
  const columns = `<col style="width:35px">${template.columns.map((column) =>
    `<col style="width:${column.hidden ? 0 : excelWidthPixels(column.width)}px">`).join("")}`;
  const tableWidth = 35 + template.columns.reduce(
    (total, column) => total + (column.hidden ? 0 : excelWidthPixels(column.width)),
    0,
  );
  const headers = `<thead><tr class="excel-column-headers"><th class="excel-corner"></th>${template.columns.map((_, index) =>
    `<th scope="col" data-format-column="${index + 1}" data-layout-column="${colPositions[index] ?? ""}"${colInsertionIndexes[index] === null ? "" : ` data-insert-operation-index="${colInsertionIndexes[index]}"`}>${excelColumnName(index + 1)}</th>`).join("")}</tr></thead>`;
  let hiddenRun = 0;
  const body = template.cells.map((row, rowIndex) => {
    const rowNumber = rowIndex + 1;
    const cells = [];
    const dynamicCovered = new Set();
    for (let columnIndex = 0; columnIndex < row.length; columnIndex += 1) {
      const columnNumber = columnIndex + 1;
      const key = `${rowNumber}:${columnNumber}`;
      if (merges.covered.has(key) || dynamicCovered.has(key)) continue;
      const cell = row[columnIndex];
      const merge = merges.anchors.get(key);
      const sourceRow = rowOrigins[rowIndex];
      const sourceCol = colOrigins[columnIndex];
      const sourceMerge = sourceRow && sourceCol
        ? sourceMerges.get(`${sourceRow}:${sourceCol}`) : null;
      const rendered = sourceRow && sourceCol
        ? valueForCell(sourceRow, sourceCol, cell, sourceMerge) : { html: "" };
      const rowSpan = merge?.rowspan || 1;
      const columnSpan = rendered.columnSpan || merge?.colspan || 1;
      if (rendered.columnSpan > 1) {
        for (let coveredColumn = columnNumber + 1; coveredColumn < columnNumber + rendered.columnSpan; coveredColumn += 1) {
          dynamicCovered.add(`${rowNumber}:${coveredColumn}`);
        }
      }
      let style = rendered.style ?? (sourceRow && sourceCol ? styleForCell(sourceRow, sourceCol, cell) : null) ?? template.styles[String(cell.style)] ?? "";
      const custom = settings.cells[`${rowNumber}:${columnNumber}`] || {};
      const color = custom.color || rendered.fillColor || "#ffffff";
      style = style.replace(/(^|;)(background-color|color):[^;]*/g, "$1")
        + `;background-color:${color};color:${custom.font_color || readableColor(color)}`
        + (custom.font_size ? `;font-size:${custom.font_size}pt` : "")
        + (custom.font_name ? `;font-family:${custom.font_name}` : "")
        + (custom.font_bold !== undefined ? `;font-weight:${custom.font_bold ? "bold" : "normal"}` : "")
        + (custom.font_italic !== undefined ? `;font-style:${custom.font_italic ? "italic" : "normal"}` : "")
        + (custom.horizontal ? `;text-align:${custom.horizontal}` : "")
        + (custom.vertical ? `;vertical-align:${custom.vertical === "center" ? "middle" : custom.vertical}` : "")
        + (custom.wrap_text !== undefined ? `;white-space:${custom.wrap_text ? "normal" : "nowrap"}` : "")
        + textDecorationStyle(custom)
        + borderStyle(custom.border);
      const ghostCell = rowNumber > realRowCount || columnNumber > realColCount;
      const classes = [rendered.className, ghostCell ? "ghost-cell" : "", merge?.custom ? "custom-merge" : ""].filter(Boolean).join(" ");
      const className = classes ? ` class="${escapeHtml(classes)}"` : "";
      const mergeAttr = merge ? ` data-merge-anchor="${escapeHtml(merge.layoutAnchor || "")}"${merge.custom ? ' data-custom-merge="true"' : ""}` : "";
      const shiftData = rendered.shiftDay && rendered.shiftSection && rendered.shiftSlot
        ? `${rendered.shiftRow ? ` data-shift-row="${escapeHtml(rendered.shiftRow)}"` : ""} data-shift-day="${escapeHtml(rendered.shiftDay)}" data-shift-section="${escapeHtml(rendered.shiftSection)}" data-shift-slot="${escapeHtml(rendered.shiftSlot)}"`
        : "";
      const shiftFocus = rendered.shiftDay && rendered.shiftSection && rendered.shiftSlot && !state.formatMode
        ? ` tabindex="0" aria-label="${escapeHtml(`${rendered.shiftSection} ${rendered.shiftSlot}. sıra ${rendered.shiftDay} vardiya hücresi`)}"`
        : "";
      const html = Object.prototype.hasOwnProperty.call(custom, "text") ? escapeHtml(custom.text) : rendered.html;
      const content = custom.text_rotation && html
        ? rotatedContent(html, custom.text_rotation)
        : cell.rotation === 90 && html
          ? `<span class="rotated-cell-value">${html}</span>`
          : html;
      const titleAttr = rendered.title ? ` title="${escapeHtml(rendered.title)}"` : "";
      const ariaLabelAttr = rendered.ariaLabel && !state.formatMode
        ? ` aria-label="${escapeHtml(rendered.ariaLabel)}"`
        : "";
      cells.push(`<td${className}${mergeAttr}${titleAttr}${ariaLabelAttr}${custom.horizontal ? ' data-custom-align="true"' : ""}${custom.shrink_to_fit ? ' data-shrink-to-fit="true"' : ""} data-cell-row="${rowNumber}" data-cell-col="${columnNumber}" data-source-row="${sourceRow || 0}" data-source-col="${sourceCol || 0}"${state.formatMode ? ` tabindex="0" aria-label="${excelColumnName(columnNumber)}${rowNumber}"` : shiftFocus}${shiftData}${rowSpan > 1 ? ` rowspan="${rowSpan}"` : ""}${columnSpan > 1 ? ` colspan="${columnSpan}"` : ""} style="${escapeHtml(style)}">${content}</td>`);
    }
    const height = Math.round(template.rows[rowIndex].height * 96 / 72);
    const sourceRow = rowOrigins[rowIndex];
    const visible = sourceRow ? rowVisible(sourceRow) : true;
    const hidden = (kind === "schedule" && sourceRow && !visible)
      || (template.rows[rowIndex].hidden && !(sourceRow && visible))
      || (settings.hide_notes && sourceRow && sourceRow >= noteStart(kind));
    // Excel keeps row numbers when rows are hidden; mark the jump so the gap is explained.
    let rowHeaderExtra = "";
    if (hidden) hiddenRun += 1;
    else if (hiddenRun) {
      const tip = kind === "schedule"
        ? `Üstte ${hiddenRun} boş satır gizli. Göstermek için "Boş satırları gizle" seçeneğini kapatın.`
        : `Üstte ${hiddenRun} satır gizli.`;
      rowHeaderExtra = ` data-hidden-before="${hiddenRun}" title="${escapeHtml(tip)}"`;
      hiddenRun = 0;
    }
    return `<tr style="height:${height}px"${hidden ? " hidden" : ""} data-layout-row="${rowPositions[rowIndex] ?? ""}"${rowInsertionIndexes[rowIndex] === null ? "" : ` data-insert-operation-index="${rowInsertionIndexes[rowIndex]}"`}><th class="excel-row-header" scope="row" data-format-row="${rowNumber}"${rowHeaderExtra}>${rowNumber}</th>${cells.join("")}</tr>`;
  }).join("");
  table.style.zoom = String(settings.zoom / 100);
  table.style.width = `${tableWidth}px`;
  table.dataset.realRows = String(realRowCount);
  table.dataset.realCols = String(realColCount);
  table.innerHTML = `<colgroup>${columns}</colgroup>${headers}<tbody>${body}</tbody>`;
  renderTemplateGuides(kind);
  const context = document.createElement("canvas").getContext("2d");
  if (context) table.querySelectorAll("td[data-shrink-to-fit]").forEach((cell) => {
    const editor = cell.querySelector("input, select");
    const text = editor ? (editor.tagName === "SELECT" ? editor.selectedOptions[0]?.textContent : editor.value) : cell.textContent;
    const style = getComputedStyle(cell);
    const size = parseFloat(style.fontSize);
    context.font = `${style.fontStyle} ${style.fontWeight} ${size}px ${style.fontFamily}`;
    const available = cell.clientWidth - (editor?.tagName === "SELECT" ? 30 : 4);
    const measured = context.measureText(text || "").width;
    if (available > 0 && measured > available) cell.style.fontSize = `${Math.max(4, size * available / measured)}px`;
  });
  updateFormatToolbar(kind);
}

function scheduleRowsByTemplate() {
  const assigned = new Map();
  for (const section of SECTIONS) {
    const rows = sectionTemplateRows(section);
    const entries = state.schedule.entries
      .filter((entry) => entry.section === section)
      .sort((a, b) => a.employee_id - b.employee_id || a.row_id.localeCompare(b.row_id));
    const taken = new Set();
    for (const entry of entries) {
      if (!Number.isInteger(entry.slot) || entry.slot < 1 || entry.slot > rows.length) continue;
      if (taken.has(entry.slot)) continue;
      taken.add(entry.slot);
      assigned.set(rows[entry.slot - 1], {
        entry,
        ordinal: entry.slot,
        section,
        slot: entry.slot,
      });
    }
    const freeSlots = rows
      .map((_, index) => index + 1)
      .filter((slot) => !taken.has(slot));
    const legacyEntries = entries.filter((entry) => !Number.isInteger(entry.slot))
      .slice(0, freeSlots.length);
    legacyEntries.forEach((entry, index) => {
      const slot = freeSlots[index];
      assigned.set(rows[slot - 1], { entry, ordinal: slot, section, slot });
    });
    for (const entry of entries) {
      if (!Number.isInteger(entry.slot) || entry.slot <= rows.length) continue;
      if (entry.slot > (SECTION_LIMITS[section] || 0) || taken.has(entry.slot)) continue;
      taken.add(entry.slot);
      assigned.set(virtualScheduleRow(section, entry.slot), { entry, ordinal: entry.slot, section, slot: entry.slot });
    }
  }
  return assigned;
}

function sectionTemplateRows(section) {
  return SECTION_TEMPLATE_ROWS[section] || [];
}

function isCustomSection(section) {
  return !SECTION_TEMPLATE_ROWS[section];
}

function applyScheduleSections(payload) {
  const sections = Array.isArray(payload?.sections) ? payload.sections : [];
  if (!sections.length) return;
  SECTIONS.splice(0, SECTIONS.length, ...sections.map((item) => item.name));
  for (const store of [SECTION_LIMITS, SECTION_ROW_COUNTS]) {
    Object.keys(store).forEach((key) => { delete store[key]; });
  }
  sections.forEach((item) => {
    SECTION_LIMITS[item.name] = item.capacity;
    SECTION_ROW_COUNTS[item.name] = item.rows;
  });
  Object.assign(SECTION_SETTINGS, payload.limits || {});
  refreshSectionSelects();
}

async function loadScheduleSections() {
  applyScheduleSections(await request("/api/schedule-sections"));
}

function refreshSectionSelects() {
  const options = (selected) => SECTIONS.map((section) =>
    `<option value="${escapeHtml(section)}"${section === selected ? " selected" : ""}>${escapeHtml(section)}</option>`).join("");
  for (const [selector, placeholder] of [
    ["#extra-personnel-section", null],
    ["#schedule-section-filter", "Tüm bölümler"],
    ["#map-default-section", "Seçin"],
    ["#bulk-schedule-section", null],
  ]) {
    const select = document.querySelector(selector);
    if (!select) continue;
    const current = select.value;
    const keep = placeholder === null && !SECTIONS.includes(current) ? SECTIONS[0] : current;
    select.innerHTML = (placeholder === null ? "" : `<option value=""${keep ? "" : " selected"}>${placeholder}</option>`)
      + options(keep);
  }
}

function virtualScheduleRow(section, slot) {
  return -(SECTIONS.indexOf(section) * 100 + slot);
}

function customHeaderForRow(rowNumber) {
  if (!(rowNumber < 0)) return null;
  const code = -rowNumber;
  const index = code % 100;
  const section = SECTIONS[Math.floor(code / 100)];
  return index >= 90 && section ? { section, index: index - 90 } : null;
}

const openedExtraSlots = new Set();

// Rows shown beyond the Excel template: section rows inside their own block plus whole custom-section blocks.
function visibleExtraSlots() {
  const occupied = new Set((state.schedule?.entries || [])
    .filter((entry) => Number.isInteger(entry.slot))
    .map((entry) => `${entry.section}:${entry.slot}`));
  const items = [];
  let chainEnd = 33;
  for (const [sectionIndex, section] of SECTIONS.entries()) {
    const custom = isCustomSection(section);
    const templateRows = sectionTemplateRows(section);
    const rows = SECTION_ROW_COUNTS[section] ?? templateRows.length;
    let after = custom ? chainEnd : templateRows[templateRows.length - 1];
    if (custom) {
      CUSTOM_SECTION_HEADER_SOURCES.forEach((source, index) => {
        const row = -(sectionIndex * 100 + 90 + index);
        items.push({ row, after, source, cloneValues: true, section, header: index });
        after = row;
      });
    }
    // Boş satır gizleme açıkken bölüm dolunca bir sonraki ek satır kendiliğinden açılır.
    let autoGrow = scheduleHideEmptyRows()
      && templateRows.every((_, index) => occupied.has(`${section}:${index + 1}`));
    for (let slot = templateRows.length + 1; slot <= (SECTION_LIMITS[section] || 0); slot += 1) {
      const filled = occupied.has(`${section}:${slot}`);
      if (slot > rows && !filled
        && !openedExtraSlots.has(`${state.scheduleMonth}:${section}:${slot}`)) {
        if (!autoGrow) continue;
        autoGrow = false;
      } else if (!filled && slot > templateRows.length) autoGrow = false;
      const row = virtualScheduleRow(section, slot);
      items.push({ row, after, source: custom ? 17 : templateRows[templateRows.length - 1], section, slot });
      after = row;
    }
    if (custom || section === "ÖSSE") chainEnd = after;
  }
  return items;
}

function openExtraScheduleSlot(section) {
  const visible = new Set(visibleExtraSlots().filter((item) => item.slot).map((item) => item.row));
  const templateCount = sectionTemplateRows(section).length;
  for (let slot = templateCount + 1; slot <= (SECTION_LIMITS[section] || 0); slot += 1) {
    const row = virtualScheduleRow(section, slot);
    if (visible.has(row)) continue;
    openedExtraSlots.add(`${state.scheduleMonth}:${section}:${slot}`);
    return row;
  }
  return null;
}

function scheduleSlotForRow(rowNumber) {
  if (rowNumber < 0) {
    const code = -rowNumber;
    const section = SECTIONS[Math.floor(code / 100)];
    const slot = code % 100;
    return section && slot >= 1 && slot <= (SECTION_LIMITS[section] || 0) ? { section, slot } : null;
  }
  for (const section of SECTIONS) {
    const slot = sectionTemplateRows(section).indexOf(rowNumber);
    if (slot !== -1) return { section, slot: slot + 1 };
  }
  return null;
}

function scheduleEntryMatchesFilters(entry, weekDays) {
  const filters = state.scheduleFilters;
  const person = state.people.find((item) => item.id === entry.employee_id);
  if (filters.query && !person?.name.toLocaleLowerCase("tr-TR").includes(filters.query)) return false;
  if (filters.section && entry.section !== filters.section) return false;
  if (!filters.kind) return true;
  const knownKinds = new Set([
    "work", "weekly_off", "overnight_exit", "annual_leave", "sick_leave", "unpaid_leave", "absent",
  ]);
  if (filters.kind === "missing") {
    return weekDays.some((date) => {
      const day = dateKey(date);
      return !scheduleRowForDay(entry, day, false)?.days[day];
    });
  }
  return weekDays.some((date) => {
    const day = dateKey(date);
    const kind = scheduleRowForDay(entry, day, false)?.days[day]?.kind;
    return filters.kind === "other"
      ? Boolean(kind && !knownKinds.has(kind))
      : kind === filters.kind;
  });
}

function scheduleAssignmentsForView(assignments) {
  if (state.scheduleFilters.sort !== "name") {
    return { assignments, slotDefinitions: new Map() };
  }
  const sortedAssignments = new Map();
  const slotDefinitions = new Map();
  for (const section of SECTIONS) {
    const rows = [...assignments.entries()]
      .filter(([, assignment]) => assignment.section === section)
      .sort(([, left], [, right]) => {
        const leftName = state.people.find((person) => person.id === left.entry.employee_id)?.name || "";
        const rightName = state.people.find((person) => person.id === right.entry.employee_id)?.name || "";
        return leftName.localeCompare(rightName, "tr") || left.slot - right.slot;
      });
    const extraRows = visibleExtraSlots()
      .filter((item) => item.section === section && item.slot)
      .map((item) => item.row);
    const positions = [...sectionTemplateRows(section), ...extraRows];
    const usedSlots = new Set(rows.map(([, assignment]) => assignment.slot));
    const freeSlots = Array.from({ length: SECTION_LIMITS[section] }, (_, index) => index + 1)
      .filter((slot) => !usedSlots.has(slot));
    positions.forEach((rowNumber, index) => {
      const assigned = rows[index]?.[1];
      const slot = assigned?.slot ?? freeSlots[index - rows.length];
      slotDefinitions.set(rowNumber, { section, slot, displaySlot: index + 1 });
      if (assigned) sortedAssignments.set(rowNumber, assigned);
    });
  }
  return { assignments: sortedAssignments, slotDefinitions };
}

function scheduleTargetForCell(cell) {
  if (!state.schedule || !cell?.dataset.shiftDay) return null;
  const section = cell.dataset.shiftSection;
  const slot = Number(cell.dataset.shiftSlot);
  const row = state.schedule.entries.find((entry) => entry.row_id === cell.dataset.shiftRow);
  if (row) return {
    rowId: row.row_id,
    employeeId: row.employee_id,
    section,
    slot,
    day: cell.dataset.shiftDay,
  };
  const personSelect = [...el("#schedule-table").querySelectorAll('select[data-action="person-slot"]')]
    .find((select) => select.dataset.section === section && Number(select.dataset.slot) === slot);
  return {
    rowId: "",
    employeeId: Number(personSelect?.value) || 0,
    section,
    slot,
    day: cell.dataset.shiftDay,
  };
}

function resolveScheduleTarget(target) {
  if (!target || !state.schedule) return null;
  if (target.rowId) {
    const row = state.schedule.entries.find((entry) => entry.row_id === target.rowId);
    if (!row) return null;
    if (row.slot !== target.slot) {
      row.slot = target.slot;
      setScheduleDirty();
    }
    return row;
  }
  if (!target.employeeId) return null;
  const existing = state.schedule.entries.find((entry) =>
    entry.section === target.section && entry.slot === target.slot);
  if (existing) return existing.employee_id === target.employeeId ? existing : null;
  if (!addScheduleRow(target.employeeId, target.section, target.slot)) return null;
  return state.schedule.entries.find((entry) =>
    entry.section === target.section && entry.slot === target.slot) || null;
}

function scheduleKindLabel(item) {
  if (!item) return "Hızlı giriş seç";
  if (item.kind === "custom") return item.custom_label
    ? `${item.custom_label} · ${item.custom_code}`
    : item.custom_code;
  const labels = {
    weekly_off: "HAFTA TATİLİ",
    overnight_exit: "GECE ÇIKIŞI",
    annual_leave: "YILLIK İZİN",
    sick_leave: "RAPOR",
    unpaid_leave: "ÜCRETSİZ İZİN",
    marriage_leave: "EVLİLİK İZNİ",
    quarantine: "KARANTİNA",
    half_day: "YARIM GÜN",
    absent: "DEVAMSIZ",
    resignation: "İSTİFA",
  };
  return labels[item.kind] || "Hızlı giriş seç";
}

function scheduleValidationWarnings(
  scheduleData = state.schedule,
  thresholdHours = state.scheduleValidationSettings.minimum_rest_hours,
) {
  const [year, month] = state.scheduleMonth.split("-").map(Number);
  const firstBoundaryDay = dateKey(new Date(year, month - 1, 0));
  const lastBoundaryDay = dateKey(new Date(year, month, 1));
  const intervals = new Map();
  for (const schedule of [scheduleData, ...Object.values(state.neighborSchedules)]) {
    for (const entry of schedule?.entries || []) {
      for (const [day, item] of Object.entries(entry.days || {})) {
        if (day < firstBoundaryDay || day > lastBoundaryDay || item.kind !== "work") continue;
        const [startHour, startMinute] = (item.start || "09:00").split(":").map(Number);
        const [endHour, endMinute] = (item.end || "18:00").split(":").map(Number);
        const [shiftYear, shiftMonth, shiftDay] = day.split("-").map(Number);
        const start = new Date(shiftYear, shiftMonth - 1, shiftDay, startHour, startMinute).getTime();
        let end = new Date(shiftYear, shiftMonth - 1, shiftDay, endHour, endMinute).getTime();
        if (end <= start) end += 24 * 60 * 60 * 1000;
        const employeeIntervals = intervals.get(entry.employee_id) || [];
        employeeIntervals.push({
          rowId: entry.row_id,
          section: entry.section,
          day,
          start,
          end,
        });
        intervals.set(entry.employee_id, employeeIntervals);
      }
    }
  }

  const warnings = [];
  for (const [employeeId, employeeIntervals] of intervals) {
    employeeIntervals.sort((left, right) => left.start - right.start || left.end - right.end);
    let previous = null;
    for (const current of employeeIntervals) {
      if (!previous) {
        previous = current;
        continue;
      }
      const relevant = previous.day.startsWith(state.scheduleMonth)
        || current.day.startsWith(state.scheduleMonth);
      const overlap = current.start < previous.end;
      const restHours = (current.start - previous.end) / (60 * 60 * 1000);
      if (relevant && (overlap || (thresholdHours > 0 && restHours < thresholdHours))) {
        warnings.push({
          type: overlap ? "overlap" : "rest",
          employeeId,
          personName: state.people.find((person) => person.id === employeeId)?.name || "Bilinmeyen personel",
          previousDay: previous.day,
          previousSection: previous.section,
          previousRowId: previous.rowId,
          day: current.day,
          section: current.section,
          rowId: current.rowId,
          restHours,
        });
      }
      if (current.end > previous.end) previous = current;
    }
  }
  const assignmentsByDay = new Map();
  for (const entry of scheduleData.entries) {
    for (const [day, item] of Object.entries(entry.days || {})) {
      if (!day.startsWith(state.scheduleMonth) || !item) continue;
      const key = `${entry.employee_id}:${day}`;
      const sections = assignmentsByDay.get(key) || new Map();
      sections.set(entry.section, entry.row_id);
      assignmentsByDay.set(key, sections);
    }
  }
  for (const [key, sections] of assignmentsByDay) {
    if (sections.size < 2) continue;
    const [employeeId, day] = key.split(":");
    warnings.push({
      type: "duplicate",
      employeeId: Number(employeeId),
      personName: state.people.find((person) => person.id === Number(employeeId))?.name || "Bilinmeyen personel",
      day,
      sections: [...sections.keys()],
    });
  }
  return warnings;
}

function renderSchedule() {
  if (!state.schedule) return;
  const weeks = weekStarts(state.scheduleMonth);
  const week = weeks[state.weekIndex] || weeks[0];
  const sourceIndex = state.weekIndex >= 5 ? 1 : state.weekIndex;
  const template = state.templates.schedule[sourceIndex];
  if (!week || !template) return;

  const weekDays = Array.from({ length: 7 }, (_, index) =>
    localDate(week.getFullYear(), week.getMonth(), week.getDate() + index));
  const validationWarnings = scheduleValidationWarnings();
  const warningCells = new Map();
  const warningPriority = { duplicate: 1, rest: 2, overlap: 3 };
  const markWarningCell = (employeeId, section, day, type) => {
    const key = `${employeeId}:${section}:${day}`;
    const existing = warningCells.get(key);
    if (!existing || warningPriority[type] > warningPriority[existing]) warningCells.set(key, type);
  };
  for (const warning of validationWarnings) {
    if (warning.type === "duplicate") {
      warning.sections.forEach((section) => markWarningCell(warning.employeeId, section, warning.day, warning.type));
    } else {
      markWarningCell(warning.employeeId, warning.previousSection, warning.previousDay, warning.type);
      markWarningCell(warning.employeeId, warning.section, warning.day, warning.type);
    }
  }
  const warningBox = el("#schedule-validation-warnings");
  if (validationWarnings.length) {
    const items = validationWarnings.map((warning) => {
      if (warning.type === "duplicate") {
        const date = dateLabel(new Date(`${warning.day}T12:00:00`));
        return `<li><strong>${escapeHtml(warning.personName)}</strong> · ${date}: aynı güne birden çok bölümde kayıt eklenmiş (${warning.sections.map(escapeHtml).join(", ")}).</li>`;
      }
      const previousDate = dateLabel(new Date(`${warning.previousDay}T12:00:00`));
      const date = dateLabel(new Date(`${warning.day}T12:00:00`));
      const detail = warning.type === "overlap"
        ? "vardiyalar üst üste geliyor"
        : `${warning.restHours.toLocaleString("tr-TR", { maximumFractionDigits: 2 })} saat ara var; belirlenen en az süreye ulaşmıyor`;
      return `<li><strong>${escapeHtml(warning.personName)}</strong> · ${previousDate} (${escapeHtml(warning.previousSection)}) → ${date} (${escapeHtml(warning.section)}): ${detail}.</li>`;
    }).join("");
    warningBox.innerHTML = `<strong>${validationWarnings.length} vardiya planı uyarısı</strong><ul>${items}</ul>`;
    warningBox.hidden = false;
  } else {
    warningBox.hidden = true;
    warningBox.textContent = "";
  }
  const viewAssignments = scheduleAssignmentsForView(scheduleRowsByTemplate());
  const assignments = viewAssignments.assignments;
  const slotDefinitions = viewAssignments.slotDefinitions;
  state.displayScheduleAssignments = assignments;
  const weekKeys = new Set(weekDays.map(dateKey));
  const entries = state.schedule.entries.map((entry) => ({
    employee_id: entry.employee_id,
    items: [...weekKeys].map((day) => scheduleRowForDay(entry, day, false)?.days[day]).filter(Boolean),
  })).filter((entry) => entry.items.length);
  const plannedShifts = entries.reduce((total, entry) => total + entry.items.filter((item) => item.kind === "work").length, 0);
  const eventCount = entries.reduce((total, entry) => total + entry.items.filter((item) => item.kind !== "work").length, 0);
  const employees = new Set(entries.map((entry) => entry.employee_id));
  const matchingEntries = state.schedule.entries.filter((entry =>
    scheduleEntryMatchesFilters(entry, weekDays)));
  const matchingRows = matchingEntries.length;
  const missingCells = state.scheduleFilters.kind === "missing"
    ? matchingEntries.reduce((total, entry) => total + weekDays.filter((date) => {
      const day = dateKey(date);
      return !scheduleRowForDay(entry, day, false)?.days[day];
    }).length, 0)
    : 0;
  const filtersActive = Boolean(
    state.scheduleFilters.query || state.scheduleFilters.section || state.scheduleFilters.kind,
  );
  el("#schedule-filter-count").textContent = filtersActive
    ? state.scheduleFilters.kind === "missing"
      ? `${matchingRows} personel satırı · ${missingCells} boş personel-gün hücresi`
      : `${matchingRows} eşleşen personel satırı`
    : `${state.schedule.entries.length} personel satırı`;
  const scheduleEmpty = el("#schedule-empty");
  scheduleEmpty.hidden = matchingRows > 0;
  scheduleEmpty.innerHTML = filtersActive
    ? 'Bu ölçütlerle eşleşen vardiya satırı yok.<br><button class="button button-outline" type="button" data-empty-action="clear-schedule-filters">Filtreleri temizle</button>'
    : state.schedule.entries.length
      ? "Bu haftada kayıtlı vardiya yok. Başka bir haftayı seçin veya personelin bu haftaki günlerini planlayın."
      : 'Henüz kayıtlı vardiya personeli yok.<br><button class="button button-outline" type="button" data-empty-action="add-person">Personel ekle</button>';
  renderSummary("#schedule-summary", [
    ["Hafta", weekRangeLabel(week)],
    ["Planlanan vardiya", String(plannedShifts)],
    ["Görevli personel", String(employees.size)],
    ["İzin / diğer durum", String(eventCount)],
    ["Plan uyarısı", String(validationWarnings.length)],
  ]);
  renderDailyStaffing(weekDays);
  const totalsRows = new Set();
  const totalsPeople = new Set();
  const formatHours = (value) => new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 2 }).format(value);
  const valueForCell = (rowNumber, columnNumber, cell, merge) => {
    const slot = assignments.get(rowNumber);
    const templateSlot = slotDefinitions.get(rowNumber) || scheduleSlotForRow(rowNumber);

    const customHeader = customHeaderForRow(rowNumber);
    if (customHeader) {
      if (customHeader.index === 1 && columnNumber === 3) return { html: escapeHtml(customHeader.section) };
      if (customHeader.index === 1 && columnNumber >= 4 && columnNumber <= 16 && columnNumber % 2 === 0) {
        return { html: dateLabel(weekDays[(columnNumber - 4) / 2]), className: "easy-schedule-date" };
      }
      return { html: cell.value === null || cell.type === "formula" ? "" : escapeHtml(cell.value) };
    }
    if (templateSlot && columnNumber === 2) {
      return { html: String(templateSlot.displaySlot ?? templateSlot.slot) };
    }
    if (templateSlot && columnNumber === 3) {
      const selectedId = slot?.entry.employee_id || 0;
      const emptyLabel = rowNumber < 0 && templateSlot.slot > (SECTION_ROW_COUNTS[templateSlot.section] ?? 0)
        ? `${templateSlot.section} · Ek personel seçin` : "Personel seçin";
      const options = `<option value="" ${selectedId ? "" : "selected"}>${escapeHtml(emptyLabel)}</option>${state.people.filter((option) => option.active || option.id === selectedId).map((option) => {
        const alreadyAssigned = state.schedule.entries.some((entry) =>
          entry.section === templateSlot.section
          && entry.employee_id === option.id
          && entry.row_id !== slot?.entry.row_id);
        const disabled = (!option.active && option.id !== selectedId) || alreadyAssigned;
        const suffix = !option.active ? " · pasif" : alreadyAssigned ? " · çizelgede" : "";
        return `<option value="${option.id}" ${option.id === selectedId ? "selected" : ""} ${disabled ? "disabled" : ""}>${escapeHtml(option.name + suffix)}</option>`;
      }).join("")}`;
      return {
        html: slot
          ? `<div class="employee-cell-wrap"><select class="workbook-cell-editor employee-name-editor" aria-label="${templateSlot.section} personel" title="${templateSlot.section}" data-action="person" data-row="${escapeHtml(slot.entry.row_id)}" data-section="${templateSlot.section}" data-slot="${templateSlot.slot}">${options}</select><button class="row-action workbook-row-action" type="button" aria-label="Personeli çizelgeden çıkar" title="Satırı kaldır" data-action="remove-row" data-row="${escapeHtml(slot.entry.row_id)}">×</button></div>`
          : `<select class="workbook-cell-editor employee-name-editor" aria-label="${templateSlot.section} boş satırda personel seçin" title="${templateSlot.section}" data-action="person-slot" data-section="${templateSlot.section}" data-slot="${templateSlot.slot}">${options}</select>`,
      };
    }

    if (templateSlot && columnNumber >= 4 && columnNumber <= 17) {
      const dayIndex = Math.floor((columnNumber - 4) / 2);
      const day = dateKey(weekDays[dayIndex]);
      const item = slot ? scheduleRowForDay(slot.entry, day, false)?.days[day] : null;
      const protectedCell = slot && state.scheduleProtections[`${day.slice(0, 7)}|${slot.entry.row_id}|${day}`];
      const protectionLabel = protectedCell
        ? `Vardiya korumalı. Gerekçe: ${protectedCell.reason || "Eski kayıtta belirtilmemiş"}. Korumayı ekleyen: ${protectedCell.protected_by || "Bilinmiyor"}.`
        : "";
      const protectionBadge = protectedCell
        ? `<span class="schedule-protection-badge" title="${escapeHtml(protectionLabel)}" aria-label="${escapeHtml(protectionLabel)}">🔒</span>`
        : "";
      const missing = Boolean(slot && !item && state.scheduleFilters.kind === "missing");
      const warningType = slot && item
        ? warningCells.get(`${slot.entry.employee_id}:${templateSlot.section}:${day}`)
        : "";
      const warningClass = warningType === "overlap"
        ? " schedule-overlap-cell"
        : warningType === "rest" ? " schedule-rest-cell"
          : warningType === "duplicate" ? " schedule-duplicate-cell" : "";
      const shiftTarget = {
        shiftRow: slot?.entry.row_id,
        shiftDay: day,
        shiftSection: templateSlot.section,
        shiftSlot: templateSlot.slot,
        fillColor: item?.fill_color,
      };
      if (slot && item?.kind === "work") {
        return {
          className: `time-cell${warningClass}`,
          title: protectionLabel,
          ...shiftTarget,
          columnSpan: 2,
          html: `<div class="work-pair">${protectionBadge}<input class="workbook-cell-editor" type="text" inputmode="numeric" maxlength="5" autocomplete="off" data-time="start" aria-label="${day} giriş saati" value="${escapeHtml(displayTime(item.start || "09:00"))}" data-action="shift-time" data-field="start" data-row="${escapeHtml(slot.entry.row_id)}" data-day="${day}"><span class="time-separator" aria-hidden="true">–</span><input class="workbook-cell-editor" type="text" inputmode="numeric" maxlength="5" autocomplete="off" data-time="end" aria-label="${day} çıkış saati" value="${escapeHtml(displayTime(item.end || "18:00", true))}" data-action="shift-time" data-field="end" data-row="${escapeHtml(slot.entry.row_id)}" data-day="${day}"><button class="quick-entry-open" type="button" aria-label="${day} hızlı girişleri aç" title="Hızlı girişleri seç">⋯</button></div>`,
        };
      }
      if (columnNumber % 2 === 0
        && (merge?.colspan === 2 || !merge)) {
        return {
          className: `shift-slot-cell${missing ? " missing-shift-cell" : ""}${warningClass}`,
          title: protectionLabel,
          html: `<div class="special-entry-wrap">${protectionBadge}<span class="shift-slot-label${!missing && !slot ? " empty-row-hint" : ""}"${missing ? ' title="Bu personel-gün hücresinde vardiya veya durum girilmemiş."' : ""}>${escapeHtml(missing ? "Vardiya eksik" : slot ? scheduleKindLabel(item) : "Önce personel seçin")}</span><button class="quick-entry-open" type="button" aria-label="${day} hızlı girişleri aç" title="Hızlı girişleri seç">⋯</button></div>`,
          ...shiftTarget,
          columnSpan: 2,
        };
      }
    }

    if ((rowNumber === 2 || rowNumber === 13 || rowNumber === 19)
      && columnNumber >= 4 && columnNumber <= 16 && columnNumber % 2 === 0) {
      const dayIndex = (columnNumber - 4) / 2;
      const day = dateKey(weekDays[dayIndex]);
      return {
        html: `<button type="button" class="hourly-day-header-button" data-hourly-day="${day}" aria-label="${day} saatlik vardiya görünümünü aç">${escapeHtml(dateLabel(weekDays[dayIndex]))}</button>`,
        className: "easy-schedule-date",
      };
    }
    const totalIndex = { 18: 0, 20: 1, 21: 2, 22: 3 }[columnNumber];
    if (templateSlot && totalIndex !== undefined && rowNumber !== 2 && rowNumber !== 13 && rowNumber !== 19) {
      if (!slot) return { html: "" };
      const employeeId = String(slot.entry.employee_id);
      if (columnNumber === 18 && !totalsPeople.has(employeeId)) {
        totalsPeople.add(employeeId);
        totalsRows.add(slot.entry.row_id);
      }
      if (!totalsRows.has(slot.entry.row_id)) return { html: "" };
      const [week, previous] = state.schedule.week_totals?.[employeeId]?.[dateKey(weekDays[0])] || [0, 0];
      const leave = state.schedule.leave_remaining?.[employeeId];
      const values = [week, previous, week + previous, leave];
      const titles = [
        "Bu haftanın fazla mesaisi (kaydedilmiş vardiyaya göre)",
        "Önceki haftaların toplam fazla mesaisi",
        "Toplam fazla mesai",
        "Kalan yıllık izin (gün)",
      ];
      const value = values[totalIndex];
      return {
        className: "schedule-total-cell",
        html: value === undefined || value === null
          ? ""
          : `<span title="${escapeHtml(titles[totalIndex])}">${escapeHtml(formatHours(value))}</span>`,
      };
    }
    if (cell.type === "formula") {
      if (/^=[A-Z]+\d+$/.test(cell.value)) {
        const dayIndex = (columnNumber - 4) / 2;
        if ((rowNumber === 13 || rowNumber === 19) && dayIndex >= 0 && dayIndex < 7) {
          return { html: dateLabel(weekDays[dayIndex]) };
        }
      }
      if (cell.value.startsWith("=SUM(")) return { html: "0" };
    }
    if (cell.type === "date" && cell.value) {
      return { html: dateLabel(new Date(`${cell.value.slice(0, 10)}T12:00:00`)) };
    }
    return { html: cell.value === null ? "" : escapeHtml(cell.value) };
  };

  const extraSlots = visibleExtraSlots();
  const addRows = compactScheduleAddRows(assignments, extraSlots);
  renderWorkbookTable(
    template,
    "#schedule-table",
    valueForCell,
    new Set(),
    (rowNumber) => {
      const assignment = assignments.get(rowNumber);
      const templateSlot = slotDefinitions.get(rowNumber) || scheduleSlotForRow(rowNumber);
      const customHeader = customHeaderForRow(rowNumber);
      if (customHeader) return !state.scheduleFilters.section || customHeader.section === state.scheduleFilters.section;
      if (!templateSlot) return true;
      if (state.scheduleFilters.section && templateSlot.section !== state.scheduleFilters.section) return false;
      if (filtersActive) return Boolean(assignment && scheduleEntryMatchesFilters(assignment.entry, weekDays));
      if (addRows) return Boolean(assignment) || addRows.has(rowNumber);
      return true;
    },
    undefined,
    extraSlots,
  );
  el("#schedule-table").classList.toggle("compact-rows", Boolean(addRows));
}

function populateScheduleComparisonOptions() {
  const first = el("#schedule-compare-first");
  const second = el("#schedule-compare-second");
  const previous = [first.value, second.value];
  const entries = [...(state.schedule?.entries || [])].sort((left, right) =>
    left.section.localeCompare(right.section, "tr")
    || left.slot - right.slot
    || left.row_id.localeCompare(right.row_id, "tr"));
  const peopleById = new Map(state.people.map((person) => [String(person.id), person]));
  const options = entries.map((entry) => {
    const person = peopleById.get(String(entry.employee_id));
    const label = `${person?.name || `Personel ${entry.employee_id}`} · ${entry.section} · ${entry.slot}. satır`;
    return `<option value="${escapeHtml(entry.row_id)}">${escapeHtml(label)}</option>`;
  }).join("");
  first.innerHTML = `<option value="">Personel seçin</option>${options}`;
  second.innerHTML = `<option value="">Personel seçin</option>${options}`;
  first.value = entries.some((entry) => entry.row_id === previous[0])
    ? previous[0] : entries[0]?.row_id || "";
  second.value = entries.some((entry) => entry.row_id === previous[1] && entry.row_id !== first.value)
    ? previous[1] : entries.find((entry) => entry.row_id !== first.value)?.row_id || "";
}

function renderScheduleComparison() {
  const firstId = el("#schedule-compare-first").value;
  const secondId = el("#schedule-compare-second").value;
  const first = state.schedule?.entries.find((entry) => entry.row_id === firstId);
  const second = state.schedule?.entries.find((entry) => entry.row_id === secondId);
  const status = el("#schedule-compare-status");
  const tables = el("#schedule-comparison-tables");
  if (!first || !second) {
    status.textContent = "Karşılaştırmak için iki personel seçin.";
    tables.hidden = true;
    return;
  }
  if (first.row_id === second.row_id || first.employee_id === second.employee_id) {
    status.textContent = "Karşılaştırma için farklı personeller seçin.";
    tables.hidden = true;
    return;
  }
  const week = weekStarts(state.scheduleMonth)[state.weekIndex];
  if (!week) {
    status.textContent = "Seçili haftanın tarihleri yüklenemedi.";
    tables.hidden = true;
    return;
  }
  const days = Array.from({ length: 7 }, (_, index) =>
    localDate(week.getFullYear(), week.getMonth(), week.getDate() + index));
  const peopleById = new Map(state.people.map((person) => [String(person.id), person]));
  const renderPersonTable = (entry) => {
    const person = peopleById.get(String(entry.employee_id));
    const name = person?.name || `Personel ${entry.employee_id}`;
    const rows = days.map((date) => {
      const day = dateKey(date);
      const item = scheduleRowForDay(entry, day, false)?.days[day];
      const value = !item ? "Boş"
        : item.kind === "work" ? `${displayTime(item.start)}–${displayTime(item.end, true)}`
          : scheduleKindLabel(item);
      return `<tr data-day="${escapeHtml(day)}"><th scope="row">${escapeHtml(dateLabel(date))}</th>
        <td>${escapeHtml(value)}</td></tr>`;
    }).join("");
    return `<section class="schedule-comparison-table-wrap" data-comparison-row-id="${escapeHtml(entry.row_id)}"
      data-comparison-person-id="${escapeHtml(String(entry.employee_id))}">
      <h3>${escapeHtml(name)} · ${escapeHtml(entry.section)} · ${entry.slot}. satır</h3>
      <div class="schedule-comparison-scroll" tabindex="0" aria-label="${escapeHtml(name)} haftalık karşılaştırma tablosu">
        <table class="schedule-comparison-table">
          <thead><tr><th scope="col">Gün</th><th scope="col">Vardiya / durum</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </section>`;
  };
  tables.innerHTML = `${renderPersonTable(first)}${renderPersonTable(second)}`;
  tables.hidden = false;
  status.textContent = `${MONTH_NAMES[week.getMonth()]} ${week.getFullYear()} · ${weekRangeLabel(week)}`;
  const scrolls = [...tables.querySelectorAll(".schedule-comparison-scroll")];
  let synchronizing = false;
  scrolls.forEach((source, index) => source.addEventListener("scroll", () => {
    if (synchronizing) return;
    synchronizing = true;
    const target = scrolls[1 - index];
    if (target) target.scrollTop = source.scrollTop;
    requestAnimationFrame(() => { synchronizing = false; });
  }, { passive: true }));
}

function scheduleHideEmptyRows() {
  return Boolean(state.uiSettings.hide_empty_rows) && !state.formatMode;
}

// Boş satır gizleme açıkken her bölümde dolu satırlar ve yalnızca bir boş "ekleme" satırı görünür.
function compactScheduleAddRows(assignments, extraSlots) {
  if (!scheduleHideEmptyRows()) return null;
  const rows = new Set();
  for (const section of SECTIONS) {
    const positions = [
      ...sectionTemplateRows(section),
      ...extraSlots.filter((item) => item.section === section && item.slot).map((item) => item.row),
    ];
    const free = positions.find((rowNumber) => !assignments.has(rowNumber));
    if (free !== undefined) rows.add(free);
  }
  return rows;
}

async function loadSchedule(weekIndex = 0) {
  const period = state.scheduleMonth;
  const [year, month] = state.scheduleMonth.split("-").map(Number);
  const previous = dateKey(localDate(year, month - 2, 1)).slice(0, 7);
  const next = dateKey(localDate(year, month, 1)).slice(0, 7);
  const [result, before, after, protections, beforeProtections, afterProtections] = await Promise.all([
    ...[state.scheduleMonth, previous, next].map((period) => request(`/api/schedule?month=${encodeURIComponent(period)}`)),
    ...[state.scheduleMonth, previous, next].map((period) => request(`/api/schedule/protection?month=${encodeURIComponent(period)}`)),
  ]);
  const selectedWeek = Math.max(0, Math.min(weekIndex, weekStarts(period).length - 1));
  await ensureTemplate("schedule", selectedWeek);
  if (period !== state.scheduleMonth) return false;
  state.schedule = result;
  state.neighborSchedules = { [previous]: before, [next]: after };
  state.scheduleSavedEntries = structuredClone(result.entries);
  state.neighborSavedEntries = {
    [previous]: structuredClone(before.entries),
    [next]: structuredClone(after.entries),
  };
  state.scheduleProtections = Object.fromEntries(
    [[period, protections], [previous, beforeProtections], [next, afterProtections]]
      .flatMap(([month, value]) => (value.cells || []).map((cell) =>
        [`${month}|${cell.row_id}|${cell.day}`, cell])),
  );
  state.neighborDirty = new Set();
  state.weekIndex = selectedWeek;
  resetEditHistory("schedule");
  setScheduleDirty(false);
  renderWeeks();
}

function scheduleRowForDay(row, day, create) {
  const period = day.slice(0, 7);
  if (period === state.scheduleMonth) return row;
  const data = state.neighborSchedules[period];
  if (!data) {
    toast("Komşu ayın vardiya verisi yüklenemedi.", true);
    return null;
  }
  let match = data.entries.find((item) => item.employee_id === row.employee_id && item.section === row.section);
  if (!match && create) {
    const sameSection = data.entries.filter((item) => item.section === row.section);
    if (sameSection.length >= SECTION_LIMITS[row.section]) {
      toast(`${period} ${row.section} bölümünde boş personel satırı yok.`, true);
      return null;
    }
    const used = new Set(sameSection.map((item) => item.slot).filter(Number.isInteger));
    const available = Array.from({ length: SECTION_LIMITS[row.section] }, (_, index) => index + 1)
      .filter((slot) => !used.has(slot));
    // Reserve slots for older records that have not yet been assigned one.
    const free = available.slice(sameSection.filter((item) => !Number.isInteger(item.slot)).length);
    const slot = free.includes(row.slot) ? row.slot : free[0];
    match = { row_id: newRowId(), employee_id: row.employee_id, section: row.section, slot, days: {} };
    data.entries.push(match);
  }
  return match || null;
}

function updateDay(row, day, kind, value, field) {
  const original = state.schedule.entries.find((entry) => entry.row_id === row);
  if (!original) return;
  const target = scheduleRowForDay(original, day, Boolean(kind));
  if (!target) return;
  if (kind === "") {
    delete target.days[day];
  } else if (kind === "work") {
    const existing = target.days[day] || {};
    target.days[day] = { kind, start: existing.start || "09:00", end: existing.end || "18:00" };
  } else if (kind === "custom") {
    const existing = target.days[day] || {};
    target.days[day] = { kind, custom_code: existing.custom_code || "" };
  } else {
    target.days[day] = { kind };
  }
  if (field) target.days[day][field] = value;
  if (day.slice(0, 7) !== state.scheduleMonth) state.neighborDirty.add(day.slice(0, 7));
  setScheduleDirty();
  renderSchedule();
}

function handleScheduleChange(event) {
  const control = event.target;
  const action = control.dataset.action;
  const rowId = control.dataset.row;
  if (action === "shift-kind") {
    updateDay(rowId, control.dataset.day, control.value);
  } else if (action === "shift-time" || action === "shift-custom") {
    const row = state.schedule.entries.find((entry) => entry.row_id === rowId);
    if (!row) return;
    const owner = scheduleRowForDay(row, control.dataset.day, false);
    const item = owner?.days[control.dataset.day];
    if (!item) return;
    const field = action === "shift-custom" ? "custom_code" : control.dataset.field;
    if (action === "shift-time") {
      const parsed = parseTimeText(control.value);
      if (!parsed) {
        toast("Saati 07:00, 0700, 7 veya 24:00 biçiminde yazın.", true);
        control.value = displayTime(item[field], field === "end");
        return;
      }
      if (parsed === (field === "start" ? item.end : item.start)) {
        toast("Giriş ve çıkış saati aynı olamaz.", true);
        control.value = displayTime(item[field], field === "end");
        return;
      }
      item[field] = parsed;
      control.value = displayTime(parsed, field === "end");
      if (state.uiSettings.auto_type_color) {
        const type = shiftTypeById(classifyShift(item.start, item.end));
        if (type) {
          item.shift_type = type.id;
          item.fill_color = type.color;
          const cell = control.closest("td");
          if (cell) cell.style.backgroundColor = type.color;
        } else delete item.shift_type;
      }
    } else item[field] = control.value;
    if (control.dataset.day.slice(0, 7) !== state.scheduleMonth) state.neighborDirty.add(control.dataset.day.slice(0, 7));
    setScheduleDirty();
  } else if (action === "person") {
    const row = state.schedule.entries.find((entry) => entry.row_id === rowId);
    const employeeId = Number(control.value);
    if (row && employeeId) {
      const duplicate = state.schedule.entries.some((entry) =>
        entry.row_id !== row.row_id
        && entry.section === row.section
        && entry.employee_id === employeeId);
      if (duplicate) {
        toast("Bu personel aynı bölümde zaten çizelgede.", true);
        renderSchedule();
        return;
      }
      row.employee_id = employeeId;
      row.slot = Number(control.dataset.slot);
      setScheduleDirty();
      renderSchedule();
    }
  } else if (action === "person-slot") {
    const employeeId = Number(control.value);
    if (employeeId && !addScheduleRow(employeeId, control.dataset.section, Number(control.dataset.slot))) {
      renderSchedule();
    }
  } else if (action === "section") {
    const row = state.schedule.entries.find((entry) => entry.row_id === rowId);
    if (row) row.section = control.value;
    setScheduleDirty();
    renderSchedule();
  }
}

function removeSchedulePerson(rowId) {
  const row = state.schedule?.entries.find((entry) => entry.row_id === rowId);
  if (!row) return;
  const name = state.people.find((person) => person.id === row.employee_id)?.name || "Personel";
  const filled = Object.keys(row.days || {}).filter((day) => day.startsWith(state.scheduleMonth)).length;
  const others = state.schedule.entries.filter((entry) =>
    entry.employee_id === row.employee_id && entry.row_id !== rowId);
  let message = `${name} ${row.section} bölümünden çıkarılsın mı?`;
  message += filled ? `\n\nBu satırdaki ${filled} günlük vardiya/izin kaydı da silinecek.` : "";
  if (others.length) {
    message += `\n\n${name} bu ay ${others.map((entry) => entry.section).join(", ")} bölümünde de var.`
      + "\nArdından diğer bölümlerden de çıkarılıp çıkarılmayacağı ayrıca sorulacak.";
  }
  if (!window.confirm(message)) return;
  const removed = new Set([rowId]);
  const otherFilled = others.reduce((total, entry) =>
    total + Object.keys(entry.days || {}).filter((day) => day.startsWith(state.scheduleMonth)).length, 0);
  if (others.length && window.confirm(`${name} diğer bölümlerden de (${others.map((entry) => entry.section).join(", ")}) çıkarılsın mı?${otherFilled ? `\n\nOralardaki ${otherFilled} günlük kayıt da silinecek.` : ""}`)) {
    others.forEach((entry) => removed.add(entry.row_id));
  }
  for (const entry of state.schedule.entries.filter((item) => removed.has(item.row_id))) {
    openedExtraSlots.delete(`${state.scheduleMonth}:${entry.section}:${entry.slot}`);
  }
  state.schedule.entries = state.schedule.entries.filter((entry) => !removed.has(entry.row_id));
  setScheduleDirty();
  renderSchedule();
  toast(removed.size > 1
    ? `${name} ${removed.size} bölümden çıkarıldı. Kaydetmeyi unutmayın.`
    : `${name} ${row.section} bölümünden çıkarıldı. Kaydetmeyi unutmayın.`);
}

function handleScheduleClick(event) {
  if (state.formatMode) return;
  const hourlyDay = event.target.closest("[data-hourly-day]");
  if (hourlyDay) {
    event.preventDefault();
    openHourlyDayTimeline(hourlyDay.dataset.hourlyDay).catch((error) => toast(error.message, true));
    return;
  }
  const control = event.target.closest('[data-action="remove-row"]');
  if (control) {
    removeSchedulePerson(control.dataset.row);
    return;
  }
  const openButton = event.target.closest(".quick-entry-open");
  if (openButton) {
    const cell = openButton.closest("td[data-shift-day]");
    if (cell) openCellQuickEntries(cell);
    return;
  }
  const cell = event.target.closest("td[data-shift-day]");
  if (!cell || event.target.matches("input[type='time'], input[data-time]")) return;
  const target = scheduleTargetForCell(cell);
  if (!target) return;
  if (state.brush) {
    if (state.brush.id === "clear" && !target.rowId) return;
    if (!target.rowId && !target.employeeId) {
      toast("Önce bu satırdaki Personel listesinden bir ad seçin.", true);
      return;
    }
    const row = resolveScheduleTarget(target);
    if (row) applyQuickEntryToCell(row, target.day, state.brush);
    return;
  }
  openCellQuickEntries(cell);
}

async function openHourlyDayTimeline(day) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new Error("Gün seçimi geçersiz.");
  const dialog = extrasDialog("hourly-day-dialog", "Saatlik vardiya görünümü",
    `${day}: vardiya hücrelerindeki kayıtlar ve tanımlı saatlik kadro aralıkları; planı değiştirmez.`);
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = '<p role="status">Saatlik görünüm hazırlanıyor…</p>';
  showExtras(dialog);
  try {
    const month = day.slice(0, 7);
    const query = new URLSearchParams({ month, date: day });
    const [report, activityReport] = await Promise.all([
      request(`/api/operations/hourly-staffing?${query}`),
      request(`/api/operations/shift-activities?${query}`),
    ]);
    const startOfDay = new Date(`${day}T12:00:00`);
    startOfDay.setDate(startOfDay.getDate() - 1);
    const priorDay = dateKey(startOfDay);
    const entriesFor = (date) => {
      const period = date.slice(0, 7);
      return (period === state.scheduleMonth ? state.schedule : state.neighborSchedules[period])?.entries || [];
    };
    const names = new Map(state.people.map((person) => [person.id, person.name]));
    const minutes = (value) => {
      const [hour, minute] = String(value || "").split(":").map(Number);
      return hour * 60 + minute;
    };
    const shifts = [];
    for (const shiftDay of [priorDay, day]) {
      for (const row of entriesFor(shiftDay)) {
        const item = row.days?.[shiftDay];
        if (item?.kind !== "work") continue;
        const start = minutes(item.start);
        let end = minutes(item.end);
        if (end <= start) end += 1440;
        const from = shiftDay === priorDay ? Math.max(0, start - 1440) : start;
        const to = shiftDay === priorDay ? end - 1440 : Math.min(end, 1440);
        if (to > from && from < 1440 && to > 0) {
          shifts.push({ employee_id: row.employee_id, name: names.get(row.employee_id) || `Personel ${row.employee_id}`,
            section: row.section || "Diğer", start: Math.max(0, from), end: Math.min(1440, to),
            original_day: shiftDay, original_start: item.start, original_end: item.end });
        }
      }
    }
    const overlaps = [];
    const overlappingShifts = new Set();
    shifts.forEach((left, index) => shifts.slice(index + 1).forEach((right, offset) => {
      if (left.start < right.end && right.start < left.end) {
        overlappingShifts.add(index);
        overlappingShifts.add(index + offset + 1);
        overlaps.push(`${left.name} (${left.section}) ve ${right.name} (${right.section}) saatleri örtüşüyor.`);
      }
    }));
    const bars = shifts.map((item, index) => {
      const left = item.start / 1440 * 100;
      const width = (item.end - item.start) / 1440 * 100;
      const shiftLabel = `${item.original_start}–${item.original_end}`;
      return `<div class="hourly-timeline-row"><strong>${escapeHtml(item.section)} · ${escapeHtml(item.name)}</strong>
        <div class="hourly-timeline-track"><span class="hourly-timeline-bar${overlappingShifts.has(index) ? " is-overlap" : ""}"
          style="left:${left}%;width:${width}%" title="${escapeHtml(`${item.name} · ${item.section} · ${shiftLabel}`)}">${escapeHtml(shiftLabel)}</span></div></div>`;
    }).join("");
    const breakItems = (activityReport.items || []).filter((item) => item.kind === "break");
    const breakBars = breakItems.map((item) => {
      const start = minutes(item.start);
      const end = minutes(item.end);
      const left = start / 1440 * 100;
      const width = Math.max(0, end - start) / 1440 * 100;
      const name = names.get(item.employee_id) || `Personel ${item.employee_id}`;
      return `<div class="hourly-timeline-row hourly-break-row"><strong>${escapeHtml(item.section)} · Mola · ${escapeHtml(name)}</strong>
        <div class="hourly-timeline-track"><span class="hourly-timeline-bar is-break"
          style="left:${left}%;width:${width}%" title="${escapeHtml(`${item.start}–${item.end} · ${name} · mola`)}">${escapeHtml(`${item.start}–${item.end}`)}</span></div></div>`;
    }).join("");
    const ticks = [0, 6, 12, 18, 24].map((hour) =>
      `<span style="left:${hour / 24 * 100}%">${String(hour).padStart(2, "0")}</span>`).join("");
    const timeLabel = (minute) => `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
    const sections = [...new Set([...SECTIONS, ...shifts.map((item) => item.section)])];
    const uncovered = sections.flatMap((section) => {
      const intervals = shifts.filter((item) => item.section === section)
        .map((item) => [item.start, item.end]).sort((left, right) => left[0] - right[0]);
      const gaps = [];
      let cursor = 0;
      for (const [start, end] of intervals) {
        if (start > cursor) gaps.push([cursor, start]);
        cursor = Math.max(cursor, end);
      }
      if (cursor < 1440) gaps.push([cursor, 1440]);
      return gaps.map(([start, end]) => `${section} ${timeLabel(start)}–${timeLabel(end)}`);
    });
    const gaps = report.rows.flatMap((row) => row.gaps.map((gap) =>
      `${row.section} ${gap.start}–${gap.end}: ${gap.shortfall} kişi eksik`));
    const breakPreviewForm = breakItems.length ? `<form class="hourly-break-preview" data-break-preview-form>
        <label>Mola kaydı<select name="break_id">${breakItems.map((item) =>
    `<option value="${escapeHtml(item.id)}">${escapeHtml(item.section)} · ${escapeHtml(names.get(item.employee_id) || `Personel ${item.employee_id}`)} · ${escapeHtml(item.start)}–${escapeHtml(item.end)}</option>`).join("")}</select></label>
        <label>Yeni başlangıç<input name="start" type="time" required value="${escapeHtml(breakItems[0].start)}"></label>
        <label>Yeni bitiş<input name="end" type="time" required value="${escapeHtml(breakItems[0].end)}"></label>
        <button type="submit" class="button button-light">Mola taşıma etkisini önizle</button>
      </form><div class="hourly-break-preview-result" data-break-preview-result role="status" aria-live="polite"></div>`
      : '<p>Bu gün için planlanmış mola yok.</p>';
    const staffingPreviewGaps = (items) => report.rows.flatMap((row) => {
      const start = minutes(row.start);
      const end = minutes(row.end);
      const sectionShifts = shifts.filter((item) => item.section === row.section);
      const sectionBreaks = items.filter((item) => item.section === row.section);
      const boundaries = [start, end];
      for (const shift of sectionShifts) {
        if (shift.start < end && shift.end > start) boundaries.push(Math.max(start, shift.start), Math.min(end, shift.end));
      }
      for (const item of sectionBreaks) {
        const breakStart = minutes(item.start);
        const breakEnd = minutes(item.end);
        if (breakStart < end && breakEnd > start) boundaries.push(Math.max(start, breakStart), Math.min(end, breakEnd));
      }
      const points = [...new Set(boundaries)].sort((left, right) => left - right);
      return points.slice(0, -1).flatMap((begin, index) => {
        const finish = points[index + 1];
        if (finish <= begin) return [];
        const scheduled = new Set(sectionShifts.filter((item) => item.start < finish && item.end > begin)
          .map((item) => item.employee_id));
        const onBreak = new Set(sectionBreaks.filter((item) =>
          minutes(item.start) <= begin && minutes(item.end) >= finish).map((item) => item.employee_id));
        const available = [...scheduled].filter((employeeId) => !onBreak.has(employeeId)).length;
        const shortfall = Number(row.required) - available;
        return shortfall > 0 ? [{
          section: row.section, start: timeLabel(begin), end: timeLabel(finish), shortfall,
        }] : [];
      });
    });
    const weekday = new Date(`${day}T12:00:00`).getDay();
    const boundaryDay = weekday === 1 || weekday === 0
      ? (() => {
        const adjacent = new Date(`${day}T12:00:00`);
        adjacent.setDate(adjacent.getDate() + (weekday === 1 ? -1 : 1));
        return dateKey(adjacent);
      })()
      : "";
    const workShiftsFor = (date) => entriesFor(date).flatMap((row) => {
      const item = row.days?.[date];
      if (item?.kind !== "work") return [];
      const start = minutes(item.start);
      let end = minutes(item.end);
      if (end <= start) end += 1440;
      const base = Date.parse(`${date}T00:00:00Z`) / 60000;
      return [{
        employee_id: row.employee_id,
        name: names.get(row.employee_id) || `Personel ${row.employee_id}`,
        section: row.section || "Diğer",
        start: item.start,
        end: item.end,
        startAt: base + start,
        endAt: base + end,
      }];
    });
    const currentBoundaryShifts = workShiftsFor(day);
    const neighborBoundaryShifts = boundaryDay ? workShiftsFor(boundaryDay) : [];
    const mondayBoundary = weekday === 1;
    const earlierBoundaryShifts = mondayBoundary ? neighborBoundaryShifts : currentBoundaryShifts;
    const laterBoundaryShifts = mondayBoundary ? currentBoundaryShifts : neighborBoundaryShifts;
    const neighborShiftRows = neighborBoundaryShifts.map((item) =>
      `${item.section} · ${item.name} · ${item.start}–${item.end}`);
    const boundaryPeople = [...new Set(earlierBoundaryShifts.map((item) => item.employee_id))]
      .filter((employeeId) => laterBoundaryShifts.some((item) => item.employee_id === employeeId));
    const durationLabel = (value) => {
      const absolute = Math.abs(value);
      const duration = `${Math.floor(absolute / 60)} saat${absolute % 60 ? ` ${absolute % 60} dk` : ""}`;
      return value < 0 ? `Vardiyalar ${duration} çakışıyor` : `Dinlenme aralığı: ${duration}`;
    };
    const boundaryRest = boundaryPeople.map((employeeId) => {
      const earlier = earlierBoundaryShifts.filter((item) => item.employee_id === employeeId)
        .sort((left, right) => right.endAt - left.endAt)[0];
      const later = laterBoundaryShifts.filter((item) => item.employee_id === employeeId)
        .sort((left, right) => left.startAt - right.startAt)[0];
      return `${earlier.name}: ${durationLabel(later.startAt - earlier.endAt)}`;
    });
    const boundaryTitle = weekday === 1 ? "Önceki pazar vardiyası (salt okunur)"
      : "Sonraki pazartesi vardiyası (salt okunur)";
    body.innerHTML = `<p>Gece vardiyasının bu güne düşen bölümü dahil edilir. Saatlik kadro açığı yalnız tanımlı asgari kadro aralıklarında hesaplanır; vardiya toplamı veya puantaj değişmez.</p>
      <div class="hourly-timeline-axis" aria-hidden="true">${ticks}</div>
      ${bars || '<p class="archive-empty">Bu güne düşen kayıtlı vardiya yok.</p>'}
      ${breakBars ? `<h3>Planlı mola aralıkları</h3>${breakBars}` : ""}
      ${boundaryDay ? `<h3>${boundaryTitle}</h3><p>${escapeHtml(boundaryDay)}: komşu gün planı salt okunur gösterilir; bu görünüm planı değiştirmez.</p>
        ${neighborShiftRows.length ? `<ul>${neighborShiftRows.map(escapeHtml).map((text) => `<li>${text}</li>`).join("")}</ul>` : "<p>Komşu günde kayıtlı çalışma vardiyası yok.</p>"}
        <h4>İki gündeki vardiyalar arası süre</h4>${boundaryRest.length
          ? `<ul>${boundaryRest.map(escapeHtml).map((text) => `<li>${text}</li>`).join("")}</ul>`
          : "<p>Aynı personelin iki günde de vardiyası yok; dinlenme aralığı hesaplanamadı.</p>"}` : ""}
      <h3>Vardiya saat örtüşmeleri</h3>${overlaps.length ? `<ul>${overlaps.map(escapeHtml).map((text) => `<li>${text}</li>`).join("")}</ul>` : "<p>Vardiya saatleri arasında örtüşme görünmüyor.</p>"}
      <h3>Kayıtlı vardiya olmayan aralıklar</h3><p>Bu liste yalnızca çizelgede vardiya bulunmayan saatleri gösterir; tek başına asgari personel ihtiyacı anlamına gelmez.</p>
      ${uncovered.length ? `<ul>${uncovered.map(escapeHtml).map((text) => `<li>${text}</li>`).join("")}</ul>` : "<p>Kapsanan aralık dışında gün içi boşluk yok.</p>"}
      <h3>Tanımlı kadro aralıklarındaki açıklıklar</h3>${gaps.length ? `<ul>${gaps.map(escapeHtml).map((text) => `<li>${text}</li>`).join("")}</ul>`
        : report.rows.length ? "<p>Tanımlı asgari kadro aralıklarında açıklık bulunmuyor.</p>"
          : "<p>Bu bölüm ve gün için saatlik asgari kadro kuralı tanımlı değil; açıklık sonucu çıkarılmadı.</p>"}
      <h3>Mola taşıma önizlemesi</h3>
      <p>Önizleme kaydedilmez ve puantajı değiştirmez. Yalnızca yapılandırılmış asgari kadro aralıkları hesaplanır; ayrıca tanımlanmamış görev noktası veya beceri gereksinimi varsayılmaz.</p>
      ${breakPreviewForm}`;
    const previewForm = body.querySelector("[data-break-preview-form]");
    previewForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      const selected = breakItems.find((item) => item.id === previewForm.elements.break_id.value);
      const start = minutes(previewForm.elements.start.value);
      const end = minutes(previewForm.elements.end.value);
      const result = body.querySelector("[data-break-preview-result]");
      if (!selected || !Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
        result.textContent = "Mola aralığını denetleyin; bitiş başlangıçtan sonra olmalıdır.";
        return;
      }
      if (!shifts.some((item) => item.employee_id === selected.employee_id
        && item.section === selected.section && item.start <= start && item.end >= end)) {
        result.textContent = "Önerilen mola bu kişinin kayıtlı vardiyasının dışında; taşıma geçerli değil.";
        return;
      }
      if (!report.rows.length) {
        result.textContent = "Saatlik asgari kadro aralığı tanımlı değil; görev açığı için sonuç hesaplanamadı.";
        return;
      }
      const before = staffingPreviewGaps(breakItems);
      const moved = breakItems.map((item) => item.id === selected.id
        ? { ...item, start: previewForm.elements.start.value, end: previewForm.elements.end.value }
        : item);
      const after = staffingPreviewGaps(moved);
      const beforeKeys = new Set(before.map((item) =>
        `${item.section}|${item.start}|${item.end}|${item.shortfall}`));
      const newGaps = after.filter((item) =>
        !beforeKeys.has(`${item.section}|${item.start}|${item.end}|${item.shortfall}`));
      const lines = (items) => items.length
        ? `<ul>${items.map((item) => `<li>${escapeHtml(`${item.section} ${item.start}–${item.end}: ${item.shortfall} kişi eksik`)}</li>`).join("")}</ul>`
        : "<p>Tanımlı asgari kadro aralıklarında açıklık yok.</p>";
      result.innerHTML = `<h4>Mevcut mola planındaki açıklıklar</h4>${lines(before)}
        <h4>Taşıma sonrası öngörülen açıklıklar (kaydedilmedi)</h4>${lines(after)}
        <p>${newGaps.length ? `Yeni oluşacak açıklık: ${newGaps.map((item) =>
          escapeHtml(`${item.section} ${item.start}–${item.end}: ${item.shortfall} kişi eksik`)).join("; ")}`
          : "Bu öngörüde mevcut açıklıklara ek yeni açıklık yok."}</p>`;
    });
  } catch (error) {
    body.textContent = error.message || "Saatlik görünüm yüklenemedi.";
    throw error;
  }
}

function moveWorkbookFocus(event, selector) {
  const current = event.target.closest(selector);
  if (!current) return false;
  const directions = {
    ArrowLeft: [-1, 0],
    ArrowRight: [1, 0],
    ArrowUp: [0, -1],
    ArrowDown: [0, 1],
  };
  const direction = directions[event.key];
  if (!direction) return false;
  if (current.matches("input") && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
    const selection = current.selectionStart;
    const selectionEnd = current.selectionEnd;
    if (selection !== selectionEnd
      || (event.key === "ArrowLeft" && selection > 0)
      || (event.key === "ArrowRight" && selection < current.value.length)) return false;
  }
  const center = (node) => {
    const box = node.getBoundingClientRect();
    return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
  };
  const origin = center(current);
  const [horizontal, vertical] = direction;
  const candidates = [...current.closest("table").querySelectorAll(selector)]
    .filter((node) => node !== current && node.offsetWidth && node.offsetHeight)
    .map((node) => {
      const point = center(node);
      const dx = point.x - origin.x;
      const dy = point.y - origin.y;
      const primary = horizontal ? dx * horizontal : dy * vertical;
      const secondary = horizontal ? Math.abs(dy) : Math.abs(dx);
      return { node, score: primary > 0 ? primary + secondary * 3 : Infinity };
    })
    .sort((left, right) => left.score - right.score);
  if (!candidates.length || !Number.isFinite(candidates[0].score)) return false;
  event.preventDefault();
  candidates[0].node.focus();
  if (candidates[0].node.matches("input")) candidates[0].node.select();
  return true;
}

function editingText(target) {
  if (!(target instanceof Element)) return false;
  if (target.matches("textarea, select, [contenteditable='true']")) return true;
  return target.matches("input") && !["checkbox", "radio", "button"].includes(target.type)
    && target.value !== target.defaultValue;
}

function scheduleCellAction(cell, action) {
  const target = scheduleTargetForCell(cell);
  if (!target) return;
  const current = target.rowId
    ? scheduleRowForDay(state.schedule.entries.find((entry) => entry.row_id === target.rowId), target.day, false)?.days[target.day]
    : null;
  if (action === "copy") {
    if (!current) { toast("Bu hücrede kopyalanacak kayıt yok.", true); return; }
    state.scheduleClipboard = { type: "day", item: structuredClone(current) };
    toast("Hücre kopyalandı. Başka bir hücrede Ctrl+V ile yapıştırın.");
    return;
  }
  if (action === "clear" && !current) return;
  if (action === "paste" && state.scheduleClipboard?.type !== "day") {
    toast("Panoda günlük kayıt yok. Önce bir hücrede Ctrl+C kullanın.", true);
    return;
  }
  const row = resolveScheduleTarget(target);
  if (!row) {
    toast("Önce bu satırdaki Personel listesinden bir ad seçin.", true);
    return;
  }
  const owner = scheduleRowForDay(row, target.day, action === "paste");
  if (!owner) return;
  if (action === "paste") owner.days[target.day] = structuredClone(state.scheduleClipboard.item);
  else delete owner.days[target.day];
  if (target.day.slice(0, 7) !== state.scheduleMonth) state.neighborDirty.add(target.day.slice(0, 7));
  setScheduleDirty();
  renderSchedule();
  const selector = `td[data-shift-day="${target.day}"][data-shift-section="${target.section}"][data-shift-slot="${target.slot}"]`;
  el("#schedule-table").querySelector(selector)?.focus();
}

function handleGlobalShortcuts(event) {
  if (document.querySelector("dialog[open]")) return;
  const kind = state.view === "schedule" || state.view === "attendance" ? state.view : null;
  if (!kind) return;
  const key = event.key.toLocaleLowerCase("en-US");
  const mod = event.ctrlKey || event.metaKey;
  if (mod && key === "s") {
    event.preventDefault();
    const button = el(`#save-${kind}`);
    if (!button.disabled) button.click();
    return;
  }
  if (mod && (key === "z" || key === "y")) {
    if (editingText(event.target)) return;
    const direction = key === "y" || event.shiftKey ? "redo" : "undo";
    event.preventDefault();
    if (state.formatMode) applyWorkbookHistory(kind, direction);
    else moveEditHistory(kind, direction);
    return;
  }
  if (kind !== "schedule" || state.formatMode) return;
  const cell = event.target.closest?.("td[data-shift-day]");
  if (!cell || event.target !== cell) return;
  if (mod && (key === "c" || key === "v")) {
    event.preventDefault();
    scheduleCellAction(cell, key === "c" ? "copy" : "paste");
  } else if (!mod && (event.key === "Delete" || event.key === "Backspace")) {
    event.preventDefault();
    scheduleCellAction(cell, "clear");
  }
}

function handleScheduleKeyboard(event) {
  if (state.formatMode || event.target.matches("input, select, button")) return;
  const cell = event.target.closest("td[data-shift-day]");
  if (!cell) return;
  if (moveWorkbookFocus(event, "td[data-shift-day]")) return;
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    openCellQuickEntries(cell);
  }
}

function handleScheduleDoubleClick(event) {
  if (state.formatMode) return;
  const cell = event.target.closest("td[data-shift-day]");
  if (!cell || event.target.closest(".quick-entry-open")) return;
  if (event.target.matches("input[type='time'], input[data-time]")) return;
  openCellQuickEntries(cell);
}

function handleScheduleDrop(event) {
  const cell = event.target.closest("td[data-shift-day]");
  if (!cell) return;
  event.preventDefault();
  cell.classList.remove("drag-target");
  const entry = quickEntryByKey(event.dataTransfer.getData("text/plain"));
  if (!entry) return;
  const target = scheduleTargetForCell(cell);
  if (!target.rowId && !target.employeeId) {
    toast("Önce bu satırdaki Personel listesinden bir ad seçin.", true);
    return;
  }
  const row = resolveScheduleTarget(target);
  if (row) applyQuickEntryToCell(row, target.day, entry);
}

function activeAssignments() {
  return new Set(state.schedule.entries.map((row) => `${row.employee_id}:${row.section}`));
}

function addScheduleRow(employeeId, section, requestedSlot) {
  if (!state.schedule) return;
  if (!SECTIONS.includes(section)) {
    toast("Vardiya bölümü geçersiz.", true);
    return false;
  }
  const sectionRows = sectionTemplateRows(section);
  const occupied = state.schedule.entries.filter((row) => row.section === section).length;
  if (occupied >= SECTION_LIMITS[section]) {
    toast(`${section} bölümünde en fazla ${SECTION_LIMITS[section]} satır olabilir. Satır sayısını Araçlar › Bölümler ve satırlar'dan artırabilirsiniz.`, true);
    return false;
  }
  const personSection = `${employeeId}:${section}`;
  if (activeAssignments().has(personSection)) {
    toast("Bu personel bu bölümde zaten çizelgede.", true);
    return false;
  }
  if (!state.schedule.entries.some((row) => row.employee_id === employeeId)
      && new Set(state.schedule.entries.map((row) => row.employee_id)).size >= SECTION_SETTINGS.max_personnel) {
    toast(`Bir ayda en fazla ${SECTION_SETTINGS.max_personnel} personel olabilir.`, true);
    return false;
  }
  const assignedSlots = new Set(
    [...scheduleRowsByTemplate().values()]
      .filter((item) => item.section === section)
      .map((item) => item.slot),
  );
  const slot = requestedSlot || Array.from({ length: SECTION_LIMITS[section] }, (_, index) => index + 1)
    .find((candidate) => !assignedSlots.has(candidate));
  if (!Number.isInteger(slot) || slot < 1 || slot > SECTION_LIMITS[section] || assignedSlots.has(slot)) {
    toast("Bu çizelge satırı artık kullanılıyor. Lütfen başka bir boş satır seçin.", true);
    return false;
  }
  state.schedule.entries.push({
    row_id: newRowId(),
    employee_id: employeeId,
    section,
    slot,
    days: {},
  });
  setScheduleDirty();
  renderSchedule();
  return true;
}

function scheduleEntryChanges(baseline, entries) {
  const before = new Map((baseline || []).map((row) => [row.row_id, row]));
  const current = new Map((entries || []).map((row) => [row.row_id, row]));
  const changes = { upsert_rows: [], remove_row_ids: [], day_changes: [] };
  for (const [rowId] of before) {
    if (!current.has(rowId)) changes.remove_row_ids.push(rowId);
  }
  for (const [rowId, row] of current) {
    const saved = before.get(rowId);
    if (!saved) {
      changes.upsert_rows.push(row);
      continue;
    }
    const savedMetadata = { ...saved };
    const editedMetadata = { ...row };
    delete savedMetadata.days;
    delete editedMetadata.days;
    if (JSON.stringify(savedMetadata) !== JSON.stringify(editedMetadata)) {
      changes.upsert_rows.push(row);
      continue;
    }
    const savedDays = saved.days || {};
    const editedDays = row.days || {};
    for (const day of new Set([...Object.keys(savedDays), ...Object.keys(editedDays)])) {
      if (JSON.stringify(savedDays[day]) !== JSON.stringify(editedDays[day])) {
        changes.day_changes.push({ row_id: rowId, day, value: editedDays[day] ?? null });
      }
    }
  }
  return changes;
}

async function saveSchedule(options = {}) {
  if (!state.scheduleDirty) return;
  if (window.easyAssistant && !options.assistantConfirmed &&
      !(await window.easyAssistant.confirmChanges("schedule"))) return;
  const button = el("#save-schedule");
  button.disabled = true;
  const saveStarted = performance.now();
  try {
    const months = { [state.scheduleMonth]: {
      revision: state.schedule.revision,
      changes: scheduleEntryChanges(state.scheduleSavedEntries, state.schedule.entries),
    } };
    for (const month of state.neighborDirty) {
      const data = state.neighborSchedules[month];
      months[month] = {
        revision: data.revision,
        changes: scheduleEntryChanges(state.neighborSavedEntries[month] || [], data.entries),
      };
    }
    const editingMonth = state.scheduleMonth;
    const draftBeforeSave = editSnapshot("schedule");
    const result = await request("/api/schedule-months", {
      method: "PUT",
      body: JSON.stringify({ months, ...(window.easyUsability ? { merge_independent: true } : {}) }),
    });
    if (state.scheduleMonth !== editingMonth || JSON.stringify(editSnapshot("schedule")) !== JSON.stringify(draftBeforeSave)) {
      button.disabled = !state.scheduleDirty;
      toast("Gönderilen sürüm kaydedildi. Bu sırada açık çizelge değişti; sonraki düzenlemeleriniz korunuyor.", true);
      return;
    }
    state.schedule.revision = result.revisions[state.scheduleMonth];
    state.schedule.entries = result.entries[state.scheduleMonth];
    state.scheduleSavedEntries = structuredClone(result.entries[state.scheduleMonth]);
    for (const month of state.neighborDirty) {
      state.neighborSchedules[month].revision = result.revisions[month];
      state.neighborSchedules[month].entries = result.entries[month];
      state.neighborSavedEntries[month] = structuredClone(result.entries[month]);
    }
    state.neighborDirty.clear();
    rebaseEditHistory("schedule");
    setScheduleDirty(false);
    toast(result.merged_months?.length
      ? "Vardiya kaydedildi. Diğer oturumun çakışmayan değişiklikleri korundu."
      : "Vardiya listesi kaydedildi. Puantaj eşitlendi.");
    try {
      const fresh = await request(`/api/schedule?month=${encodeURIComponent(editingMonth)}`);
      if (state.scheduleMonth === editingMonth && state.schedule.revision === fresh.revision) {
        state.schedule.week_totals = fresh.week_totals;
        state.schedule.source_revisions = fresh.source_revisions;
        state.schedule.leave_remaining = fresh.leave_remaining;
      }
    } catch (_) { /* toplamlar bir sonraki yüklemede yenilenir */ }
    if (state.view === "attendance") await loadAttendance();
    else renderSchedule();
    if (result.linked_leave_conflicts?.length) {
      const dialog = extrasDialog("schedule-linked-leave-conflicts", "Aktarılamayan izinler",
        "Dolu veya çakışan kayıtlar korunmuştur. Bağlantılı izinler aracından inceleyin.");
      dialog.querySelector(".extras-body").innerHTML = `<ul>${result.linked_leave_conflicts.map((item) =>
          `<li>${escapeHtml(state.people.find((person) => person.id === item.employee_id)?.name || `Personel ${item.employee_id}`)} · ${escapeHtml(item.date)}: ${escapeHtml(item.reason)}</li>`
        ).join("")}</ul>`;
      showExtras(dialog);
    }
  } catch (error) {
    button.disabled = false;
    if (error.scheduleConflicts?.length) showScheduleSaveConflicts(error.scheduleConflicts);
    toast(error.message, true);
  } finally {
    window.easyUsability?.recordSave("schedule", saveStarted);
  }
}

function scheduleConflictValue(value) {
  if (!value) return "Boş";
  if (value.kind === "work") return `${value.start}–${displayTime(value.end, true)}`;
  if (value.kind === "custom") return value.custom_code || "Özel kod";
  return SHIFT_OPTIONS.find(([key]) => key === value.kind)?.[1] || value.kind;
}

function mergeScheduleConflictChoices(baseline, saved, draft, choices) {
  const rows = (items) => new Map(items.map((item) => [item.row_id, item]));
  const [beforeRows, savedRows, draftRows] = [baseline, saved, draft].map(rows);
  const merged = [];
  for (const rowId of new Set([...savedRows.keys(), ...draftRows.keys(), ...beforeRows.keys()])) {
    const before = beforeRows.get(rowId);
    const onDisk = savedRows.get(rowId);
    const inDraft = draftRows.get(rowId);
    if (JSON.stringify(inDraft ?? null) === JSON.stringify(before ?? null)
        || JSON.stringify(inDraft ?? null) === JSON.stringify(onDisk ?? null)) {
      if (onDisk) merged.push(structuredClone(onDisk));
      continue;
    }
    if (JSON.stringify(onDisk ?? null) === JSON.stringify(before ?? null)) {
      if (inDraft) merged.push(structuredClone(inDraft));
      continue;
    }
    const metadata = (entry) => entry && Object.fromEntries(
      Object.entries(entry).filter(([key]) => key !== "days"),
    );
    if (!before || !onDisk || !inDraft
        || JSON.stringify(metadata(before)) !== JSON.stringify(metadata(onDisk))
        || JSON.stringify(metadata(before)) !== JSON.stringify(metadata(inDraft))) {
      throw new Error("Satır yerleşimi değişmiş; bu çakışma hücre seçimiyle güvenle çözülemez.");
    }
    const row = { ...onDisk, days: {} };
    const days = new Set([
      ...Object.keys(before.days || {}), ...Object.keys(onDisk.days || {}), ...Object.keys(inDraft.days || {}),
    ]);
    for (const day of days) {
      const original = before.days?.[day] ?? null;
      const latest = onDisk.days?.[day] ?? null;
      const local = inDraft.days?.[day] ?? null;
      let value;
      if (JSON.stringify(local) === JSON.stringify(original)
          || JSON.stringify(local) === JSON.stringify(latest)) value = latest;
      else if (JSON.stringify(latest) === JSON.stringify(original)) value = local;
      else {
        const choice = choices.get(`${rowId}|${day}`);
        if (!choice) throw new Error(`${day} hücresi için kayıtlı veya taslak değer seçin.`);
        value = choice === "saved" ? latest : local;
      }
      if (value !== null) row.days[day] = structuredClone(value);
    }
    merged.push(row);
  }
  return merged;
}

function showScheduleSaveConflicts(conflicts) {
  const dialog = extrasDialog("schedule-sharing-conflicts", "Çakışan vardiya değişiklikleri",
    "Hiçbir ay kaydedilmedi. Her çakışan hücre için kayıtlı veya taslak değeri seçin. Çakışmayan değişiklikler korunur.");
  const resolvable = conflicts.length <= 100 && !state.neighborDirty.size
    && conflicts.every((item) => !item.reason && item.row_id && item.date
      && (!item.month || item.month === state.scheduleMonth));
  dialog.querySelector(".extras-body").innerHTML = `<div class="controls-table-wrap"><table class="extras-table">
    <thead><tr><th>Dönem / gün</th><th>Personel</th><th>Kayıtlı</th><th>Taslak</th><th>Karar</th></tr></thead>
    <tbody>${conflicts.slice(0, 100).map((item) => `<tr><td>${escapeHtml(item.date || item.month)}</td>
    <td>${escapeHtml(state.people.find((person) => person.id === item.employee_id)?.name || "—")}</td>
    ${item.reason ? `<td colspan="2">${escapeHtml(item.reason)}</td><td>Elle inceleyin</td>`
      : `<td>${escapeHtml(scheduleConflictValue(item.saved))}</td><td>${escapeHtml(scheduleConflictValue(item.draft))}</td>
        <td><select data-conflict-choice="${escapeHtml(item.row_id)}|${escapeHtml(item.date)}"
          aria-label="${escapeHtml(item.date)} için ${escapeHtml(state.people.find((person) => person.id === item.employee_id)?.name || "personel")} çakışma kararı">
          <option value="">Seçin</option><option value="saved">Kayıtlıyı kullan</option><option value="draft">Taslağı kullan</option></select></td>`}</tr>`).join("")}</tbody>
    </table>${conflicts.length > 100 ? `<p>${conflicts.length - 100} ek çakışma var.</p>` : ""}</div>`;
  const status = document.createElement("p");
  status.className = "extras-note";
  status.setAttribute("role", "status");
  status.textContent = resolvable
    ? "Her satırda bir karar seçin; diğer düzenlemeleriniz korunarak yeniden kaydedilir."
    : "Satır yerleşimi, başka ay veya çok sayıda çakışma var. Bu değişiklikler otomatik birleştirilemez; taslağı indirip güncel kayıtla karşılaştırın.";
  dialog.querySelector(".extras-body").append(status);
  const resolve = document.createElement("button");
  resolve.type = "button";
  resolve.className = "button button-primary";
  resolve.textContent = "Seçimleri uygula ve yeniden kaydet";
  resolve.disabled = !resolvable || !dialog.querySelectorAll("[data-conflict-choice]").length;
  dialog.querySelector(".extras-actions").prepend(resolve);
  const updateResolve = () => {
    resolve.disabled = !resolvable || [...dialog.querySelectorAll("[data-conflict-choice]")]
      .some((select) => !select.value);
  };
  dialog.querySelectorAll("[data-conflict-choice]").forEach((select) =>
    select.addEventListener("change", updateResolve));
  resolve.addEventListener("click", () => extrasRun(resolve, async () => {
    const month = state.scheduleMonth;
    const fresh = await request(`/api/schedule?month=${encodeURIComponent(month)}`);
    const choices = new Map([...dialog.querySelectorAll("[data-conflict-choice]")].map((select) =>
      [select.dataset.conflictChoice, select.value]));
    const baseline = state.editHistory.schedule.baseline?.entries;
    if (!baseline || state.scheduleMonth !== month) {
      throw new Error("Çizelge başlangıç sürümü bulunamadı; taslağınız açık bırakıldı.");
    }
    const entries = mergeScheduleConflictChoices(
      baseline, fresh.entries, state.schedule.entries, choices,
    );
    state.schedule.revision = fresh.revision;
    state.schedule.entries = entries;
    state.editHistory.schedule.baseline.entries = structuredClone(fresh.entries);
    setScheduleDirty(true);
    dialog.close();
    renderSchedule();
    await saveSchedule({ assistantConfirmed: true });
  }));
  showExtras(dialog);
}

function showAttendanceSaveConflicts(conflicts) {
  const dialog = extrasDialog("attendance-sharing-conflicts", "Çakışan puantaj değişiklikleri",
    "Hiçbir değişiklik kaydedilmedi. Taslağınız açık kalır. Çakışan kodu kayıtlı değerle aynı yapıp yeniden deneyebilirsiniz.");
  dialog.querySelector(".extras-body").innerHTML = `<div class="controls-table-wrap"><table class="extras-table">
    <thead><tr><th>Dönem / gün</th><th>Personel</th><th>Kayıtlı</th><th>Taslak</th></tr></thead>
    <tbody>${conflicts.slice(0, 100).map((item) => `<tr><td>${escapeHtml(item.date || item.month)}</td>
    <td>${escapeHtml(state.people.find((person) => person.id === item.employee_id)?.name || "—")}</td>
    ${item.reason ? `<td colspan="2">${escapeHtml(item.reason)}</td>`
      : `<td>${escapeHtml(item.saved ?? "Otomatik")}</td><td>${escapeHtml(item.draft ?? "Otomatik")}</td>`}</tr>`).join("")}</tbody>
    </table>${conflicts.length > 100 ? `<p>${conflicts.length - 100} ek çakışma var.</p>` : ""}</div>`;
  showExtras(dialog);
}

const ATTENDANCE_HOUR_HEADERS = { 34: "RESMİ TATİL MESAİ", 35: "FAZLA MESAİ" };

function renderAttendance() {
  if (!state.attendance) return;
  const template = state.templates.attendance[0];
  if (!template) return;
  const { dates, rows, warnings } = state.attendance;
  const [year, monthNumber] = state.attendanceMonth.split("-").map(Number);
  const workbookName = `PUANTAJ ${MONTH_NAMES[monthNumber - 1].toLocaleUpperCase("tr-TR")} ${year}`;
  el("#attendance-sheet-name").textContent = workbookName;
  el("#attendance-workbook-name").textContent = workbookName;
  const byDay = new Map(dates.map((day, index) => [index, day]));
  const weekdayColumns = new Map();
  const mismatches = attendanceScheduleMismatches(rows);
  const mismatchesByCell = new Map(mismatches.map((item) =>
    [`${item.employee_id}:${item.date}`, item]));
  const visibleRows = getVisibleAttendanceRows(rows, warnings, mismatches);
  el("#attendance-filter-count").textContent = `${visibleRows.length} / ${rows.length} personel`;
  el("#attendance-empty").hidden = visibleRows.length > 0;
  const attendanceFiltersActive = Boolean(state.attendanceQuery || state.attendanceFilter !== "all");
  const emptyMessages = {
    all: "Bu aramayla eşleşen personel yok.",
    events: "İzin veya özel puantaj kodu olan personel bulunmuyor.",
    manual: "Elle düzeltme veya kesintisi olan personel bulunmuyor.",
    conflicts: "Çakışması olan personel bulunmuyor.",
    mismatches: "Vardiya planından farklı elle düzeltme bulunmuyor.",
  };
  el("#attendance-empty").innerHTML = rows.length
    ? `${escapeHtml(emptyMessages[state.attendanceFilter] || emptyMessages.all)}${attendanceFiltersActive
      ? '<br><button class="button button-outline" type="button" data-empty-action="clear-filters">Arama ve filtreyi temizle</button>'
      : ""}`
    : 'Bu ay için puantaj satırı bulunmuyor. Puantaj personeli, kaydedilmiş vardiya kayıtlarından oluşur. Vardiya sayfasında personel ekleyip çizelgeyi kaydedin.<br><button class="button button-outline" type="button" data-empty-action="add-person">Vardiya sayfasına geç ve personel ekle</button>';
  updateAttendanceSummary(rows, warnings, mismatches);
  template.cells[2].forEach((cell, index) => {
    if (index < 2 || index > 32 || !cell.value) return;
    const sourceDate = new Date(`${cell.value.slice(0, 10)}T12:00:00`);
    if (sourceDate.getDate() === 28 || sourceDate.getDate() === 29) return;
    if (!weekdayColumns.has(sourceDate.getDay())) weekdayColumns.set(sourceDate.getDay(), index + 1);
  });
  // People after the 21st are shown in extra rows that copy template row 24.
  const extraPeople = Math.max(0, visibleRows.length - 21);
  const attendanceExtraRows = Array.from({ length: extraPeople }, (_, index) =>
    ({ row: -(index + 1), after: index ? -index : 24, source: 22 }));
  const personIndexForRow = (rowNumber) => {
    if (rowNumber >= 4 && rowNumber <= 24) return rowNumber - 4;
    return rowNumber < 0 ? 20 - rowNumber : null;
  };
  // Template rows 23-24 are unbordered spacers; people placed there (or below) borrow row 22's look.
  const styleSourceRow = (row) => {
    if (row >= 0 && row < 23) return row;
    const index = personIndexForRow(row);
    return index !== null && visibleRows[index] ? 22 : row;
  };
  const styleForCell = (row, columnNumber) => {
    const rowNumber = styleSourceRow(row);
    if (rowNumber < 0) return null;
    if (rowNumber !== row && (columnNumber < 3 || columnNumber > 33)) {
      return template.styles[String(template.cells[21][columnNumber - 1]?.style)] || "";
    }
    if (rowNumber < 2 || rowNumber > 24 || columnNumber < 3 || columnNumber > 33) return null;
    const day = byDay.get(columnNumber - 3);
    const sourceDate = template.cells[2][columnNumber - 1]?.value;
    let sourceColumn = columnNumber;
    if (!day || sourceDate?.slice(0, 10) !== day) {
      const weekday = day ? new Date(`${day}T12:00:00`).getDay() : 1;
      sourceColumn = weekdayColumns.get(weekday);
    }
    if (!sourceColumn) return null;
    const sourceCell = template.cells[rowNumber - 1][sourceColumn - 1];
    let style = template.styles[String(sourceCell.style)] || "";
    if (!day && rowNumber === 3) style = style.replace(/(^|;)background-color:[^;]*/g, "$1");
    return style;
  };
  const valueForCell = (rowNumber, columnNumber, cell) => {
    if (rowNumber === 1 && columnNumber === 1) {
      return { html: `İSTASYON ${MONTH_NAMES[monthNumber - 1].toLocaleUpperCase("tr-TR")} ${year} PUANTAJ` };
    }
    if (rowNumber === 2 && columnNumber >= 3 && columnNumber <= 33) {
      const dayIndex = columnNumber - 3;
      const day = byDay.get(dayIndex);
      return {
        html: dayIndex < dates.length ? String(dayIndex + 1) : "",
        fillColor: day ? ATTENDANCE_CATEGORY_COLORS[state.attendance.calendar?.[day]] : null,
      };
    }
    if (rowNumber === 3 && columnNumber >= 3 && columnNumber <= 33) {
      const dayIndex = columnNumber - 3;
      const day = byDay.get(dayIndex);
      const fullDate = day ? dateLabel(new Date(`${day}T12:00:00`), "full") : "";
      return {
        html: day ? dateLabel(new Date(`${day}T12:00:00`), "compact") : "",
        title: fullDate,
        ariaLabel: fullDate,
        fillColor: day ? ATTENDANCE_CATEGORY_COLORS[state.attendance.calendar?.[day]] : null,
      };
    }
    if (rowNumber === 3 && ATTENDANCE_HOUR_HEADERS[columnNumber]) {
      return { html: ATTENDANCE_HOUR_HEADERS[columnNumber] };
    }
    const personIndex = personIndexForRow(rowNumber);
    if (personIndex !== null) {
      const person = visibleRows[personIndex];
      if (!person) return { html: "" };
      if (columnNumber === 1) return { html: String(personIndex + 1) };
      if (columnNumber === 2) return { html: escapeHtml(person.name), className: "attendance-person-name",
        ariaLabel: `${person.name} personel adı` };
      if (columnNumber >= 3 && columnNumber <= 33) {
        const day = byDay.get(columnNumber - 3);
        if (!day) return { html: "" };
        const code = person.codes[day] || "";
        const manual = person.sources[day] === "manual";
        const mismatch = mismatchesByCell.get(`${person.employee_id}:${day}`);
        const sourceLabel = manual ? "Elle puantaj düzeltmesi"
          : person.sources[day] === "schedule" ? "Vardiya planından hesaplandı" : "Kaynak kaydı yok";
        const mismatchTitle = mismatch
          ? `Vardiya planı: ${mismatch.automatic || "boş"} · Elle puantaj: ${mismatch.manual}`
          : "";
        const title = mismatch ? `${sourceLabel} · ${mismatchTitle}` : sourceLabel;
        return {
          className: mismatch ? "attendance-mismatch-cell" : "",
          fillColor: person.colors?.[day],
          html: `<input class="workbook-cell-editor attendance-code-editor${manual ? " manual" : ""}${mismatch ? " mismatch" : ""}" type="text" maxlength="8" aria-label="${escapeHtml(person.name)} ${day} puantaj kodu · ${escapeHtml(sourceLabel)}" title="${escapeHtml(title)}" value="${escapeHtml(code)}" data-action="attendance-code" data-person="${person.employee_id}" data-day="${day}">`,
        };
      }
      if (columnNumber === 34) {
        return {
          html: window.easyAssistant
            ? `<button type="button" class="easy-calculation-link" data-explain-person="${person.employee_id}" title="Gün gün hesap açıklaması" aria-label="${escapeHtml(person.name)} resmî tatil mesaisi hesap açıklaması">${Number(person.holiday_hours || 0).toFixed(2)}</button>`
            : Number(person.holiday_hours || 0).toFixed(2),
          className: "attendance-auto-cell",
        };
      }
      if (columnNumber === 35) {
        const manual = person.overtime_manual;
        const computed = Number(person.overtime_computed_hours || 0).toFixed(2).replace(".", ",");
        const value = Number(person.overtime_hours || 0);
        const title = manual
          ? `Elle girildi. Vardiyadan hesaplanan: ${computed} saat. Silerseniz hesaplanan değere döner.`
          : "Vardiyadan otomatik hesaplandı (günlük sınırı aşan süre). Değiştirmek için yazın.";
        const explain = `${title}\nÇift tıklayın: gün gün hesap dökümü.`;
        return {
          html: `<input class="workbook-cell-editor attendance-overtime-editor${manual ? " manual" : ""}" type="text" maxlength="8" inputmode="decimal" aria-label="${escapeHtml(person.name)} fazla mesai saati" title="${escapeHtml(explain)}" value="${value ? value.toFixed(2).replace(".", ",") : ""}" placeholder="0,00" data-action="attendance-overtime" data-person="${person.employee_id}">`,
        };
      }
      if (columnNumber === 36) {
        const amount = state.attendance.deductions[String(person.employee_id)];
        return {
          html: `<input class="workbook-cell-editor attendance-deduction-editor" type="text" maxlength="12" inputmode="decimal" aria-label="${escapeHtml(person.name)} kesinti tutarı, TL" value="${amount ? Number(amount).toFixed(2).replace(".", ",") : ""}"${state.attendance.payrollRedacted ? ' disabled title="Bordro yetkisi gereklidir; tutar gizlidir."' : ""} data-action="attendance-deduction" data-person="${person.employee_id}">`,
        };
      }
    }
    if (rowNumber >= 26 && rowNumber <= 35) {
      const movement = (state.attendance.movements || [])[rowNumber - 26];
      const formatDate = (value) => value ? value.split("-").reverse().join(".") : "";
      const auto = (html) => ({ html, className: html ? "attendance-auto-cell" : "" });
      if (columnNumber === 2) return auto(movement ? escapeHtml(movement.name) : "");
      if (columnNumber === 3) return auto(formatDate(movement?.hire_date));
      if (columnNumber === 9) return auto(formatDate(movement?.exit_date));
      if (columnNumber === 13) return auto(formatDate(movement?.resignation_date));
    }
    if (rowNumber >= 37 && rowNumber <= 42) {
      const legend = state.attendance.legend || [];
      const byCode = new Map(legend.map((item) => [item.code, item]));
      const custom = legend.filter((item) => !item.standard);
      const slots = ATTENDANCE_LEGEND_SLOTS;
      const slot = Object.entries(slots).find(([, [r, c]]) => r === rowNumber && (c === columnNumber || c + 1 === columnNumber));
      const days = (count) => count ? `${count} gün` : "";
      if (slot) {
        const item = byCode.get(slot[0]);
        if (columnNumber === slot[1][1]) return { html: escapeHtml(slot[0]), fillColor: item?.color || null };
        return { html: days(item?.count), className: item ? "attendance-auto-cell" : "" };
      }
      if (rowNumber === 42 && columnNumber === 7) return { html: custom.length ? "ÖZEL KODLAR" : "" };
      if (rowNumber === 42 && columnNumber === 12) {
        return {
          html: escapeHtml(custom.map((item) => item.code).join(", ")),
          fillColor: custom.length === 1 ? custom[0].color : null,
        };
      }
      if (rowNumber === 42 && columnNumber === 13) {
        return { html: days(custom.reduce((sum, item) => sum + item.count, 0)), className: custom.length ? "attendance-auto-cell" : "" };
      }
    }
    return { html: cell.value === null ? "" : escapeHtml(cell.value) };
  };
  renderWorkbookTable(template, "#attendance-table", valueForCell, new Set(), () => true, styleForCell,
    attendanceExtraRows);
  const overflow = state.attendance.movement_overflow || 0;
  el("#attendance-table").title = "";

  const warningBox = el("#attendance-warnings");
  const warningGroups = [];
  if (warnings.length) {
    const unique = new Set(warnings.map((warning) => `${warning.employee_id}:${warning.date}`));
    const items = warnings.map((item) => {
      const person = rows.find((candidate) => Number(candidate.employee_id) === Number(item.employee_id));
      const codes = Array.isArray(item.codes) ? item.codes.join(", ") : "";
      return `<li><button type="button" class="warning-link" data-attendance-warning data-person="${item.employee_id}" data-day="${escapeHtml(item.date)}">${escapeHtml(person?.name || "Personel")} · ${dateLabel(new Date(`${item.date}T12:00:00`))}: ${escapeHtml(codes)}</button></li>`;
    }).join("");
    warningGroups.push(`<div class="warning-group"><strong>İncelenmesi gereken çakışma: ${unique.size}</strong> personel/gün için birden fazla vardiya durumu girilmiş.<ul>${items}</ul></div>`);
  }
  if (mismatches.length) {
    const items = mismatches.map((item) =>
      `<li><button type="button" class="warning-link" data-attendance-warning data-person="${item.employee_id}" data-day="${escapeHtml(item.date)}"><strong>${escapeHtml(item.name)}</strong> · ${dateLabel(new Date(`${item.date}T12:00:00`))}: vardiya planı ${escapeHtml(item.automatic || "boş")}, elle girilen ${escapeHtml(item.manual)}.</button></li>`,
    ).join("");
    warningGroups.push(`<div class="warning-group"><strong>${mismatches.length} elle düzeltme vardiya planından farklı</strong><ul>${items}</ul></div>`);
  }
  const missingPeople = state.attendance.missingPeople || [];
  if (missingPeople.length) {
    const names = missingPeople.slice(0, 6).map((person) => escapeHtml(person.name)).join(", ");
    const more = missingPeople.length > 6 ? ` ve ${missingPeople.length - 6} kişi daha` : "";
    warningGroups.push(`<div class="warning-group attendance-missing-group"><strong>Puantajda olmayan ${missingPeople.length} aktif personel</strong> · ${names}${more}. Bu ay vardiyaları yoksa da puantajda görünmeleri gerekiyorsa tek tıkla ekleyin.
      <span class="warning-actions"><button type="button" class="button button-light" data-attendance-add-missing>Hepsini puantaja ekle</button>
      <button type="button" class="button button-light" data-attendance-open-roster>Listeyi düzenle</button></span></div>`);
  }
  if (warningGroups.length || overflow) {
    if (overflow) warningGroups.push(`<div class="warning-group"><strong>İşe giriş/çıkış tablosu dolu.</strong> Excel şablonundaki 10 satıra sığmayan ${overflow} hareket var.</div>`);
    warningBox.innerHTML = warningGroups.join("");
    warningBox.hidden = false;
  } else {
    warningBox.hidden = true;
    warningBox.textContent = "";
  }
}

const ATTENDANCE_EVENT_LABELS = {
  annual_leave: "Yıllık izin",
  sick_leave: "Rapor",
  unpaid_leave: "Ücretsiz izin",
  absence: "İşe gelmeme",
  marriage_leave: "Evlilik izni",
  other: "Diğer",
};

function renderAttendanceEvents(events) {
  const list = el("#attendance-events-list");
  if (!events.length) {
    list.innerHTML = '<p class="archive-empty">Bu ayla kesişen kayıt bulunmuyor.</p>';
    return;
  }
  list.innerHTML = `<div class="attendance-events-table-wrap"><table class="archive-table attendance-events-table">
    <thead><tr><th>Personel</th><th>Tür</th><th>Tarih aralığı</th><th>Belge / açıklama</th><th></th></tr></thead>
    <tbody>${events.map((event) => {
      const person = state.people.find((item) => item.id === event.employee_id);
      const range = event.start === event.end ? event.start : `${event.start} – ${event.end}`;
      const details = [event.document, event.notes].filter(Boolean).join(" · ");
      return `<tr><th scope="row">${escapeHtml(person?.name || "Bilinmeyen personel")}</th>
        <td>${ATTENDANCE_EVENT_LABELS[event.kind] || "Diğer"}</td><td>${escapeHtml(range)}</td>
        <td>${escapeHtml(details)}</td>
        <td><button class="button button-light danger-button" type="button" data-event-delete="${escapeHtml(event.id)}">Sil</button></td></tr>`;
    }).join("")}</tbody></table></div>`;
}

async function loadAttendanceEvents() {
  const result = await request(`/api/attendance-events?month=${encodeURIComponent(state.attendanceMonth)}`);
  renderAttendanceEvents(result.events);
}

const ROSTER_SOURCE_LABELS = {
  schedule: "Vardiyadan",
  nonshift: "Vardiya dışı",
  added: "Elle eklendi",
  records: "Kayıtlı veri",
};

const rosterDraft = { items: [], removed: new Set(), added: new Set() };

function renderAttendanceRoster() {
  const list = el("#attendance-roster-list");
  if (!rosterDraft.items.length) {
    list.innerHTML = '<li class="roster-empty">Bu ay puantajda personel yok. Vardiya listesine personel ekleyin veya yukarıdan seçin.</li>';
  } else {
    list.innerHTML = rosterDraft.items.map((item, index) => {
      const locked = item.source === "schedule";
      return `<li data-roster-id="${item.id}">
        <span class="roster-index">${index + 1}</span>
        <span class="roster-name">${escapeHtml(item.name)}</span>
        <span class="roster-badge roster-${escapeHtml(item.source)}">${escapeHtml(ROSTER_SOURCE_LABELS[item.source] || item.source)}</span>
        <span class="roster-actions">
          <button class="icon-button" type="button" data-roster-move="-1" aria-label="${escapeHtml(item.name)} yukarı taşı" ${index === 0 ? "disabled" : ""}>↑</button>
          <button class="icon-button" type="button" data-roster-move="1" aria-label="${escapeHtml(item.name)} aşağı taşı" ${index === rosterDraft.items.length - 1 ? "disabled" : ""}>↓</button>
          <button class="button button-light danger-button" type="button" data-roster-remove ${locked ? 'disabled title="Vardiya listesinde olduğu için çıkarılamaz; önce vardiyadan kaldırın."' : ""}>Çıkar</button>
        </span>
      </li>`;
    }).join("");
  }
  const shown = new Set(rosterDraft.items.map((item) => item.id));
  const options = state.people.filter((person) => person.active && !shown.has(person.id));
  el("#attendance-roster-add").innerHTML = options.length
    ? options.map((person) => `<option value="${person.id}">${escapeHtml(person.name)}</option>`).join("")
    : '<option value="">Eklenebilecek aktif personel yok</option>';
  el("#attendance-roster-add-button").disabled = !options.length;
  const addAll = el("#attendance-roster-add-all");
  addAll.disabled = !options.length;
  addAll.textContent = options.length ? `Eksiklerin tümünü ekle (${options.length})` : "Eksik personel yok";
}

function openAttendanceRoster() {
  if (!state.attendance) return;
  const roster = state.attendance.roster || {};
  const sources = state.attendance.rosterSources || {};
  rosterDraft.items = state.attendance.rows.map((row) => ({
    id: Number(row.employee_id),
    name: row.name,
    source: sources[String(row.employee_id)] || "records",
  }));
  rosterDraft.removed = new Set(roster.removed || []);
  rosterDraft.added = new Set(roster.added || []);
  el("#attendance-roster-period").textContent = state.attendanceMonth;
  renderAttendanceRoster();
  el("#attendance-roster-dialog").showModal();
}

async function persistAttendanceRoster(roster) {
  const result = await request("/api/attendance", {
    method: "PUT",
    body: JSON.stringify({
      month: state.attendanceMonth,
      revision: state.attendance.revision,
      overrides: state.attendance.overrides,
      deductions: state.attendance.deductions,
      overtime: state.attendance.overtime || {},
      roster,
    }),
  });
  state.attendance.revision = result.revision;
  await loadAttendance();
}

async function addMissingAttendancePeople(button) {
  const missing = state.attendance?.missingPeople || [];
  if (!missing.length) return;
  if (state.attendanceDirty
      && !window.confirm("Kaydedilmemiş puantaj düzeltmeleri de bu işlemle kaydedilecek. Devam edilsin mi?")) return;
  const roster = state.attendance.roster || {};
  const ids = missing.map((person) => Number(person.id));
  const order = (roster.order || []).length ? [...roster.order.filter((id) => !ids.includes(Number(id))), ...ids] : [];
  if (button) button.disabled = true;
  try {
    await persistAttendanceRoster({
      added: [...new Set([...(roster.added || []).map(Number), ...ids])],
      removed: (roster.removed || []).filter((id) => !ids.includes(Number(id))),
      order,
    });
    toast(`${ids.length} personel puantaja eklendi. Bu ay vardiyası olmayan günler boş kalır; kodları hücrelerden girebilirsiniz.`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (button) button.disabled = false;
  }
}

async function saveAttendanceRoster(resetOrder = false) {
  const button = el("#attendance-roster-save");
  button.disabled = true;
  try {
    const roster = {
      added: [...rosterDraft.added].filter((id) => !rosterDraft.removed.has(id)),
      removed: [...rosterDraft.removed],
      order: resetOrder ? [] : rosterDraft.items.map((item) => item.id),
    };
    await persistAttendanceRoster(roster);
    el("#attendance-roster-dialog").close();
    toast("Puantaj personel listesi kaydedildi.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function registerAttendanceRosterEvents() {
  el("#open-attendance-roster").addEventListener("click", openAttendanceRoster);
  el("#attendance-roster-add-button").addEventListener("click", () => {
    const id = Number(el("#attendance-roster-add").value);
    const person = state.people.find((item) => item.id === id);
    if (!person) return;
    rosterDraft.removed.delete(id);
    rosterDraft.added.add(id);
    rosterDraft.items.push({ id, name: person.name, source: "added" });
    renderAttendanceRoster();
  });
  el("#attendance-roster-add-all").addEventListener("click", () => {
    const shown = new Set(rosterDraft.items.map((item) => item.id));
    state.people.filter((person) => person.active && !shown.has(person.id)).forEach((person) => {
      rosterDraft.removed.delete(person.id);
      rosterDraft.added.add(person.id);
      rosterDraft.items.push({ id: person.id, name: person.name, source: "added" });
    });
    renderAttendanceRoster();
  });
  el("#attendance-roster-call").addEventListener("click", async () => {
    if (state.attendanceDirty
        && !window.confirm("Kaydedilmemiş puantaj düzeltmeleri de bu işlemle kaydedilecek. Devam edilsin mi?")) return;
    rosterDraft.removed = new Set();
    await saveAttendanceRoster(true);
  });
  el("#attendance-roster-save").addEventListener("click", () => {
    if (state.attendanceDirty
        && !window.confirm("Kaydedilmemiş puantaj düzeltmeleri de bu işlemle kaydedilecek. Devam edilsin mi?")) return;
    saveAttendanceRoster(false);
  });
  el("#attendance-roster-list").addEventListener("click", (event) => {
    const item = event.target.closest("[data-roster-id]");
    if (!item) return;
    const index = rosterDraft.items.findIndex((entry) => entry.id === Number(item.dataset.rosterId));
    const move = event.target.closest("[data-roster-move]");
    if (move) {
      const target = index + Number(move.dataset.rosterMove);
      if (target < 0 || target >= rosterDraft.items.length) return;
      [rosterDraft.items[index], rosterDraft.items[target]] = [rosterDraft.items[target], rosterDraft.items[index]];
      renderAttendanceRoster();
      el(`#attendance-roster-list [data-roster-id="${item.dataset.rosterId}"] [data-roster-move="${move.dataset.rosterMove}"]`)?.focus();
      return;
    }
    if (event.target.closest("[data-roster-remove]")) {
      const [removed] = rosterDraft.items.splice(index, 1);
      if (removed.source === "records"
          && !window.confirm(`${removed.name} için bu ay girilmiş puantaj verisi var. Kişi puantajdan gizlenir, girilen veriler silinmez. Devam edilsin mi?`)) {
        rosterDraft.items.splice(index, 0, removed);
        return;
      }
      rosterDraft.added.delete(removed.id);
      rosterDraft.removed.add(removed.id);
      renderAttendanceRoster();
    }
  });
}

function registerAttendanceEventEvents() {
  el("#open-attendance-events").addEventListener("click", async () => {
    const dialog = el("#attendance-events-dialog");
    const select = el("#attendance-event-person");
    select.innerHTML = `<option value="">Personel seçin</option>${state.people.map((person) =>
      `<option value="${person.id}">${escapeHtml(person.name)}${person.active ? "" : " · pasif"}</option>`).join("")}`;
    el("#attendance-events-period").textContent = state.attendanceMonth;
    el("#attendance-event-start").value = "";
    el("#attendance-event-end").value = "";
    try {
      await loadAttendanceEvents();
      dialog.showModal();
    } catch (error) {
      toast(error.message, true);
    }
  });
  el("#attendance-event-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await request("/api/attendance-events", {
        method: "POST",
        body: JSON.stringify({
          employee_id: Number(el("#attendance-event-person").value),
          kind: el("#attendance-event-kind").value,
          start: el("#attendance-event-start").value,
          end: el("#attendance-event-end").value,
          document: el("#attendance-event-document").value,
          notes: el("#attendance-event-notes").value,
        }),
      });
      form.reset();
      await loadAttendanceEvents();
      toast("İzin/devamsızlık kaydı eklendi.");
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
  el("#attendance-events-list").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-event-delete]");
    if (!button || !window.confirm("Bu izin/devamsızlık kaydı silinsin mi?")) return;
    button.disabled = true;
    try {
      await request("/api/attendance-events/delete", {
        method: "POST",
        body: JSON.stringify({ id: button.dataset.eventDelete }),
      });
      await loadAttendanceEvents();
      toast("Kayıt silindi.");
    } catch (error) {
      toast(error.message, true);
    }
  });
}

function handleAttendanceWarningClick(event) {
  if (event.target.closest("[data-attendance-open-roster]")) {
    openAttendanceRoster();
    return;
  }
  if (event.target.closest("[data-attendance-add-missing]")) {
    addMissingAttendancePeople(event.target.closest("button"));
    return;
  }
  const warning = event.target.closest("[data-attendance-warning]");
  if (!warning) return;
  state.attendanceFilter = "all";
  state.attendanceQuery = "";
  el("#attendance-filter").value = "all";
  el("#attendance-search").value = "";
  renderAttendance();
  const target = [...el("#attendance-table").querySelectorAll('input[data-action="attendance-code"]')]
    .find((control) => control.dataset.person === warning.dataset.person
      && control.dataset.day === warning.dataset.day);
  if (!target) {
    toast("İlgili puantaj hücresi tabloda bulunamadı.", true);
    return;
  }
  target.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
  target.focus({ preventScroll: true });
}

function periodCheckButton(label, detail, attributes) {
  return `<li><button type="button" class="warning-link" data-check-target="${attributes.target}"${attributes.sequence ? ` data-check-sequence="${attributes.sequence}"` : ""}${attributes.person ? ` data-person="${attributes.person}"` : ""}${attributes.day ? ` data-day="${escapeHtml(attributes.day)}"` : ""}${attributes.row ? ` data-row="${escapeHtml(attributes.row)}"` : ""}${attributes.section ? ` data-section="${escapeHtml(attributes.section)}"` : ""}>${escapeHtml(label)}${detail ? `: ${escapeHtml(detail)}` : ""}</button></li>`;
}

function openPeriodCheck(kind) {
  return openPeriodCheckAsync(kind).catch((error) => toast(error.message, true));
}

const REPORT_GROUPS = [
  ["rest", "Kısa dinlenme / vardiya çakışması", true],
  ["negative_leave", "Eksi yıllık izin bakiyesi", true],
  ["no_records", "Bu ay hiç kaydı olmayan personel", true],
  ["after_exit", "İşten çıkış tarihinden sonra vardiya", true],
  ["document_expiry", "Süresi dolan / dolmak üzere olan belgeler", true],
  ["empty_days", "Boş puantaj günleri", true],
  ["weekly_rest", "6 günden uzun hafta tatilsiz çalışma", true],
  ["overtime", "Fazla mesai", false],
  ["holiday_work", "Resmî tatilde çalışma", false],
  ["legal_weekly", "Yasal sınır: haftalık 45 saat aşımı", true],
  ["legal_night", "Yasal sınır: 7,5 saati aşan gece çalışması", true],
  ["legal_yearly_overtime", "Yasal sınır: yıllık 270 saat fazla mesai", true],
];

async function serverCheckGroups(month) {
  const report = await request(`/api/reports/check?month=${encodeURIComponent(month)}`);
  const groups = [];
  let issues = 0;
  for (const [type, title, warning] of REPORT_GROUPS) {
    const items = report.items.filter((item) => item.type === type || (type === "rest" && item.type === "overlap"));
    if (!items.length) continue;
    if (warning) issues += items.length;
    const list = items.map((item) => periodCheckButton(
      item.name,
      `${item.date ? `${dateLabel(new Date(`${item.date}T12:00:00`))} · ` : ""}${item.message}`,
      { target: "attendance", person: item.employee_id, day: item.date || `${month}-01` },
    )).join("");
    groups.push(`<section class="period-check-group${warning ? " period-check-warning" : ""}"><h3>${escapeHtml(title)} (${items.length})</h3><ul>${list}</ul></section>`);
  }
  return { groups, issues };
}

async function openPeriodCheckAsync(kind) {
  const content = el("#period-check-content");
  const summary = el("#period-check-summary");
  const groups = [];
  let issueCount = 0;
  if (kind === "schedule") {
    if (!state.schedule) {
      toast("Vardiya çizelgesi yüklenmeden dönem kontrolü yapılamaz.", true);
      return;
    }
    const [year, month] = state.scheduleMonth.split("-").map(Number);
    const weekStart = weekStarts(state.scheduleMonth)[state.weekIndex];
    const weekDays = Array.from({ length: 7 }, (_, offset) =>
      dateKey(localDate(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() + offset)));
    const missing = [];
    const sectionStats = new Map();
    for (const entry of state.schedule.entries) {
      const person = state.people.find((candidate) => candidate.id === entry.employee_id);
      const sectionName = entry.section || "Diğer";
      if (!sectionStats.has(sectionName)) {
        sectionStats.set(sectionName, { rows: 0, work: 0, weeklyOff: 0, other: 0, missing: 0 });
      }
      const section = sectionStats.get(sectionName);
      section.rows += 1;
      for (const day of weekDays) {
        const shift = entry.days?.[day];
        if (!shift) {
          section.missing += 1;
        } else if (shift.kind === "work") {
          section.work += 1;
        } else if (shift.kind === "weekly_off") {
          section.weeklyOff += 1;
        } else {
          section.other += 1;
        }
        if (day.startsWith(state.scheduleMonth) && !shift) {
          missing.push(periodCheckButton(
            person?.name || "Bilinmeyen personel",
            `${dateLabel(new Date(`${day}T12:00:00`))} · ${entry.section} bölümünde boş vardiya hücresi`,
            { target: "schedule", row: entry.row_id, section: entry.section, day, sequence: "schedule-missing" },
          ));
        }
      }
    }
    if (sectionStats.size) {
      const items = [...sectionStats.entries()].sort(([left], [right]) => left.localeCompare(right, "tr"));
      groups.push(`<section class="period-check-group"><h3>Bölüm bazında vardiya dağılımı</h3>
        <div class="period-check-table-wrap"><table class="period-check-table">
          <thead><tr><th>Bölüm</th><th>Personel satırı</th><th>Çalışma</th><th>Hafta tatili</th><th>İzin / diğer</th><th>Boş hücre</th></tr></thead>
          <tbody>${items.map(([name, counts]) => `<tr><th scope="row">${escapeHtml(name)}</th><td>${counts.rows}</td><td>${counts.work}</td><td>${counts.weeklyOff}</td><td>${counts.other}</td><td>${counts.missing}</td></tr>`).join("")}</tbody>
        </table></div><p>Adetler seçili haftadaki personel satırı-gün hücrelerini sayar; boş hücreler otomatik hata değildir.</p></section>`);
    }
    if (missing.length) {
      issueCount += missing.length;
      groups.push(`<section class="period-check-group"><h3>Kontrol edilmesi önerilen boş hücreler (${missing.length})</h3><p>Boş hücreler otomatik hata sayılmaz; haftalık izin veya vardiyasız gün olarak bırakılmış olabilir. Bir hücreye gittiğinizde sonraki eksik hücreye tablonun yanındaki akıştan ilerleyebilirsiniz.</p><ul>${missing.join("")}</ul></section>`);
    }
    const warnings = scheduleValidationWarnings();
    const duplicates = warnings.filter((item) => item.type === "duplicate");
    const shifts = warnings.filter((item) => item.type === "overlap" || item.type === "rest");
    if (duplicates.length) {
      issueCount += duplicates.length;
      const items = duplicates.map((item) => {
        const row = state.schedule.entries.find((entry) =>
          entry.employee_id === item.employeeId && entry.section === item.sections[0]);
        return periodCheckButton(
          item.personName,
          `${dateLabel(new Date(`${item.day}T12:00:00`))} · birden fazla bölümde kayıt`,
          { target: "schedule", row: row?.row_id, section: row?.section, day: item.day },
        );
      }).join("");
      groups.push(`<section class="period-check-group period-check-warning"><h3>Birden fazla bölümde aynı gün (${duplicates.length})</h3><ul>${items}</ul></section>`);
    }
    if (shifts.length) {
      issueCount += shifts.length;
      const items = shifts.map((item) => {
        const useCurrent = item.day.startsWith(state.scheduleMonth);
        const day = useCurrent ? item.day : item.previousDay;
        const row = useCurrent ? item.rowId : item.previousRowId;
        const section = useCurrent ? item.section : item.previousSection;
        return periodCheckButton(
          item.personName,
          `${dateLabel(new Date(`${item.day}T12:00:00`))} · ${item.type === "overlap" ? "vardiya saatleri çakışıyor" : `${item.restHours.toFixed(1)} saat dinlenme`}`,
          { target: "schedule", row, section, day },
        );
      }).join("");
      groups.push(`<section class="period-check-group period-check-warning"><h3>Çakışma / kısa dinlenme (${shifts.length})</h3><ul>${items}</ul></section>`);
    }
    el("#period-check-subtitle").textContent =
      `${MONTH_NAMES[month - 1]} ${year} · ${weekRangeLabel(weekStart)} · Vardiya Listesi`;
  } else {
    if (!state.attendance) {
      toast("Puantaj yüklenmeden dönem kontrolü yapılamaz.", true);
      return;
    }
    const { rows, warnings } = state.attendance;
    const mismatches = attendanceScheduleMismatches(rows);
    const conflicts = new Map();
    warnings.forEach((item) => conflicts.set(`${item.employee_id}:${item.date}`, item));
    if (conflicts.size) {
      issueCount += conflicts.size;
      const items = [...conflicts.values()].map((item) => {
        const person = rows.find((candidate) => Number(candidate.employee_id) === Number(item.employee_id));
        return periodCheckButton(
          person?.name || "Personel",
          `${dateLabel(new Date(`${item.date}T12:00:00`))} · ${Array.isArray(item.codes) ? item.codes.join(", ") : "çakışan kodlar"}`,
          { target: "attendance", person: item.employee_id, day: item.date },
        );
      }).join("");
      groups.push(`<section class="period-check-group period-check-warning"><h3>İncelenecek puantaj çakışmaları (${conflicts.size})</h3><ul>${items}</ul></section>`);
    }
    if (mismatches.length) {
      issueCount += mismatches.length;
      const items = mismatches.map((item) => periodCheckButton(
        item.name,
        `${dateLabel(new Date(`${item.date}T12:00:00`))} · plan ${item.automatic || "boş"}, puantaj ${item.manual}`,
        { target: "attendance", person: item.employee_id, day: item.date },
      )).join("");
      groups.push(`<section class="period-check-group period-check-warning"><h3>Vardiya planından farklı elle düzeltmeler (${mismatches.length})</h3><ul>${items}</ul></section>`);
    }
    const server = await serverCheckGroups(state.attendanceMonth);
    issueCount += server.issues;
    groups.push(...server.groups);
    if (state.attendanceDirty) {
      groups.push(`<p class="period-check-note">Sunucu kontrolleri kaydedilmiş puantajdan hazırlanır; kaydedilmemiş düzeltmeler dahil değildir.</p>`);
    }
    el("#period-check-subtitle").textContent =
      `${state.attendanceMonth} · Puantaj Listesi`;
  }
  summary.classList.toggle("has-issues", issueCount > 0);
  summary.textContent = issueCount
    ? `${issueCount} kontrol noktası bulundu. Kaydetmeden veya dönemi kapatmadan önce gözden geçirin.`
    : "Bu dönem için mevcut kontrollerde sorun bulunmadı.";
  content.innerHTML = groups.length
    ? groups.join("")
    : `<p class="period-check-clear">Kontrol tamamlandı. Bu görünümde incelenecek bir uyarı yok.</p>`;
  el("#period-check-dialog").showModal();
}

function periodCheckFlowTargets(sequence) {
  return [...el("#period-check-content").querySelectorAll("[data-check-target]")]
    .filter((button) => button.dataset.checkSequence === sequence)
    .map((button) => ({
      target: button.dataset.checkTarget,
      person: button.dataset.person,
      day: button.dataset.day,
      row: button.dataset.row,
      section: button.dataset.section,
    }));
}

function renderPeriodCheckFlow() {
  for (const kind of ["schedule", "attendance"]) {
    const root = el(`#${kind}-period-check-flow`);
    const flow = state.periodCheckFlow;
    root.hidden = !flow || flow.kind !== kind;
    if (!root.hidden) {
      root.querySelector("[data-period-check-position]").textContent =
        `${flow.index + 1}/${flow.targets.length}`;
      root.querySelector('[data-period-check-step="-1"]').disabled = flow.index === 0;
      root.querySelector('[data-period-check-step="1"]').disabled = flow.index === flow.targets.length - 1;
    }
  }
}

function closePeriodCheckFlow() {
  state.periodCheckFlow = null;
  renderPeriodCheckFlow();
}

async function navigateToPeriodCheckTarget(target) {
  if (target.target === "attendance") {
    state.attendanceFilter = "all";
    state.attendanceQuery = "";
    el("#attendance-filter").value = "all";
    el("#attendance-search").value = "";
    renderAttendance();
    const control = [...el("#attendance-table").querySelectorAll('input[data-action="attendance-code"]')]
      .find((item) => item.dataset.person === target.person && item.dataset.day === target.day);
    if (!control) {
      toast("İlgili puantaj hücresi tabloda bulunamadı.", true);
      return;
    }
    control.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
    control.focus({ preventScroll: true });
    control.classList.add("period-check-target");
    window.setTimeout(() => control.classList.remove("period-check-target"), 2200);
    return;
  }
  state.scheduleFilters = { query: "", section: "", kind: "", sort: "slot" };
  el("#schedule-search").value = "";
  el("#schedule-section-filter").value = "";
  el("#schedule-kind-filter").value = "";
  el("#schedule-sort").value = "slot";
  const weekIndex = weekStarts(state.scheduleMonth).findIndex((weekStart) => {
    const days = Array.from({ length: 7 }, (_, offset) =>
      dateKey(localDate(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() + offset)));
    return days.includes(target.day);
  });
  if (weekIndex >= 0 && weekIndex !== state.weekIndex) await selectScheduleWeek(weekIndex);
  else renderSchedule();
  const cell = [...el("#schedule-table").querySelectorAll("td[data-shift-row]")]
    .find((item) => item.dataset.shiftRow === target.row
      && item.dataset.shiftDay === target.day
      && item.dataset.shiftSection === target.section);
  if (!cell) {
    toast("İlgili vardiya hücresi tabloda bulunamadı.", true);
    return;
  }
  cell.tabIndex = -1;
  cell.classList.add("period-check-target");
  cell.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
  cell.focus({ preventScroll: true });
  window.setTimeout(() => cell.classList.remove("period-check-target"), 2200);
}

async function handlePeriodCheckClick(event) {
  const target = event.target.closest("[data-check-target]");
  if (!target) return;
  const sequence = target.dataset.checkSequence;
  if (sequence) {
    const targets = periodCheckFlowTargets(sequence);
    const index = targets.findIndex((item) =>
      item.target === target.dataset.checkTarget && item.row === target.dataset.row
      && item.section === target.dataset.section && item.day === target.dataset.day);
    if (targets.length && index >= 0) {
      state.periodCheckFlow = { kind: target.dataset.checkTarget, targets, index };
      el("#period-check-dialog").close();
      renderPeriodCheckFlow();
      await navigateToPeriodCheckTarget(targets[index]);
      return;
    }
  }
  el("#period-check-dialog").close();
  await navigateToPeriodCheckTarget({
    target: target.dataset.checkTarget,
    person: target.dataset.person,
    day: target.dataset.day,
    row: target.dataset.row,
    section: target.dataset.section,
  });
}

async function handlePeriodCheckFlowClick(event) {
  const button = event.target.closest("[data-period-check-step], [data-period-check-close]");
  if (!button || !state.periodCheckFlow) return;
  if (button.hasAttribute("data-period-check-close")) {
    closePeriodCheckFlow();
    return;
  }
  const next = state.periodCheckFlow.index + Number(button.dataset.periodCheckStep);
  if (next < 0 || next >= state.periodCheckFlow.targets.length) return;
  state.periodCheckFlow.index = next;
  renderPeriodCheckFlow();
  await navigateToPeriodCheckTarget(state.periodCheckFlow.targets[next]);
}

async function loadAttendance(saveContext = null) {
  const month = state.attendanceMonth;
  const result = await request(`/api/attendance?month=${encodeURIComponent(month)}`);
  await ensureTemplate("attendance");
  if (state.attendanceMonth !== month || (saveContext &&
      (state.attendance !== saveContext.table || JSON.stringify(editSnapshot("attendance")) !== saveContext.draft))) return false;
  state.attendance = result.attendance;
  state.attendance.source_revisions = result.source_revisions;
  state.attendance.calendar = result.calendar;
  state.attendance.revision = result.revision;
  state.attendance.overrides = result.overrides;
  state.attendance.deductions = result.deductions || {};
  state.attendance.payrollRedacted = Boolean(result.payroll_redacted);
  state.attendance.overtime = result.overtime || {};
  state.attendance.roster = result.roster || { added: [], removed: [], order: [] };
  state.attendance.availablePeople = result.available_people || [];
  state.attendance.missingPeople = result.missing_people || [];
  state.attendance.rosterSources = result.roster_sources || {};
  state.overtimeSettings = result.overtime_settings;
  el("#overtime-weekly-limit").value = String(state.overtimeSettings.weekly_limit_hours);
  el("#overtime-daily-limit").value = String(state.overtimeSettings.daily_limit_hours ?? 8);
  el("#overtime-week-start").value = String(state.overtimeSettings.week_start);
  el("#overtime-break-rules").value = formatBreakRules(state.overtimeSettings.break_rules);
  state.attendance.rows.forEach((person) => {
    person.baseCodes = { ...person.automatic_codes };
  });
  // Undo snapshots replace the whole draft; never carry them across a merge or a later revision.
  if (saveContext && !saveContext.saved.merged && result.revision === saveContext.saved.revision) {
    rebaseEditHistory("attendance");
  } else {
    resetEditHistory("attendance");
  }
  setAttendanceDirty(false);
  renderAttendance();
  const balances = await request(`/api/balances?month=${encodeURIComponent(month)}`);
  if (state.attendanceMonth !== month) return false;
  state.balanceSummary = balances;
  renderBalanceSummary();
}

function formatBreakRules(rules) {
  return (rules || []).map(([hours, minutes]) => `${String(hours).replace(".", ",")}=${minutes}`).join("; ");
}

function parseBreakRules(text) {
  const value = String(text || "").trim();
  if (!value) return [];
  return value.split(/[;\n]+/).map((part) => part.trim()).filter(Boolean).map((part) => {
    const match = part.match(/^(\d+(?:[.,]\d+)?)\s*(?:=|:|sa(?:at)?\s*>)\s*(\d+)\s*(?:dk)?$/i);
    if (!match) throw new Error(`Mola kuralı anlaşılamadı: “${part}”. Örnek: 4=30; 7,5=60`);
    return [Number(match[1].replace(",", ".")), Number(match[2])];
  });
}

function overtimeExplanation(person) {
  const details = person.overtime_details || {};
  const days = Object.keys(details).sort();
  const hours = (value) => Number(value || 0).toFixed(2).replace(".", ",");
  const lines = days.map((day) => {
    const item = details[day];
    const parts = [`${day.split("-").reverse().slice(0, 2).join(".")}: çalışma ${hours(item.worked)}`];
    if (item.break) parts.push(`− mola ${hours(item.break)}`);
    if (item.holiday) parts.push(`− resmî tatil ${hours(item.holiday)}`);
    parts.push(`− sınır ${hours(item.limit)} = FM ${hours(item.overtime)}`);
    return parts.join(" ");
  });
  const head = `${person.name} · fazla mesai dökümü (günlük sınır ${hours(person.daily_limit_hours)} sa)`;
  const total = `Hesaplanan toplam: ${hours(person.overtime_computed_hours)} sa${person.overtime_manual ? ` · puantaja elle yazılan: ${hours(person.overtime_hours)} sa` : ""}`;
  const splits = (person.cross_month_shifts || []).map((shift) => {
    const label = (stamp) => `${stamp.slice(0, 10).split("-").reverse().join(".")} ${stamp.slice(11)}`;
    return `${label(shift.start)} → ${label(shift.end)}: ${shift.start.slice(0, 7)} içinde ${shift.before_minutes} dk + ${shift.end.slice(0, 7)} içinde ${shift.after_minutes} dk = ${shift.total_minutes} dk`;
  });
  return [head, ...(lines.length ? lines : ["Bu ay vardiyadan gelen çalışma yok."]), total,
    ...(splits.length ? ["", "İki aya yayılan vardiyalar (mola düşülmeden):", ...splits] : [])].join("\n");
}

function showOvertimeExplanation(employeeId) {
  const person = state.attendance?.rows?.find((row) => String(row.employee_id) === String(employeeId));
  if (!person) return;
  let dialog = document.getElementById("overtime-explain-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "overtime-explain-dialog";
    dialog.className = "overtime-explain-dialog";
    dialog.innerHTML = '<form method="dialog"><h2></h2><pre></pre><div class="dialog-footer"><button class="button button-primary" value="close">Kapat</button></div></form>';
    document.body.append(dialog);
  }
  const [head, ...rest] = overtimeExplanation(person).split("\n");
  dialog.querySelector("h2").textContent = head;
  dialog.querySelector("pre").textContent = rest.join("\n");
  dialog.showModal();
}

async function saveOvertimeSettings() {
  if (state.attendanceDirty) {
    toast("Önce puantaj düzeltmelerini kaydedin; sonra fazla mesai kuralını değiştirin.", true);
    return;
  }
  const button = el("#save-overtime-settings");
  button.disabled = true;
  try {
    const rawLimit = el("#overtime-weekly-limit").value.trim().replace(",", ".");
    if (!rawLimit) throw new Error("Haftalık normal saat sınırını girin; hesabı kapatmak için 0 kullanın.");
    const rawDaily = el("#overtime-daily-limit").value.trim().replace(",", ".");
    if (!rawDaily || !Number.isFinite(Number(rawDaily)) || Number(rawDaily) < 0 || Number(rawDaily) > 24) {
      throw new Error("Günlük normal saat 0 ile 24 arasında olmalıdır; günlük hesabı kapatmak için 0 kullanın.");
    }
    const month = el("#overtime-effective-month").value;
    if (!month) throw new Error("Yeni kural için bir yürürlük ayı seçin.");
    const input = {
      month,
      settings: {
        weekly_limit_hours: Number(rawLimit),
        week_start: Number(el("#overtime-week-start").value),
        daily_limit_hours: Number(el("#overtime-daily-limit").value.trim().replace(",", ".") || 0),
        break_rules: parseBreakRules(el("#overtime-break-rules").value),
      },
    };
    const route = "/api/overtime-settings/transition";
    const preview = await request(route, { method: "POST", body: JSON.stringify(input) });
    if (!window.confirm(`${preview.effective_date} ve sonrası · Bu ay için hesaplanan mesai: ${preview.previous_hours} → ${preview.proposed_hours} saat.\nÖnceki ayların hesabı değişmez. Yeni kural kaydedilsin mi?`)) return;
    await request(route, {
      method: "PUT", body: JSON.stringify({ ...input, preview: preview.preview }),
    });
    await loadAttendance();
    toast(`Fazla mesai kuralı ${preview.effective_date} tarihinden itibaren geçerli.`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function effectiveAttendanceCode(person, day) {
  const override = state.attendance?.overrides?.[day]?.[String(person.employee_id)];
  return override ?? person.baseCodes?.[day] ?? "";
}

function attendanceScheduleMismatches(rows) {
  const mismatches = [];
  for (const person of rows) {
    for (const [date, values] of Object.entries(state.attendance?.overrides || {})) {
      const personId = String(person.employee_id);
      if (!Object.prototype.hasOwnProperty.call(values, personId)) continue;
      const manual = values[personId];
      const automatic = person.automatic_codes?.[date] || "";
      if (manual !== automatic) {
        mismatches.push({
          employee_id: Number(person.employee_id),
          name: person.name,
          date,
          automatic,
          manual,
        });
      }
    }
  }
  return mismatches;
}

function getVisibleAttendanceRows(rows, warnings, mismatches) {
  const query = state.attendanceQuery.trim().toLocaleLowerCase("tr-TR");
  const conflictIds = new Set(warnings.map((warning) => Number(warning.employee_id)));
  const mismatchIds = new Set(mismatches.map((item) => Number(item.employee_id)));
  return rows.filter((person) => {
    if (query && !person.name.toLocaleLowerCase("tr-TR").includes(query)) return false;
    const days = Object.keys(person.baseCodes || {});
    const allDays = new Set([...days, ...Object.keys(state.attendance?.overrides || {})]);
    const hasEvent = [...allDays].some((day) => isExceptionCode(effectiveAttendanceCode(person, day)));
    const isManual = Object.values(state.attendance?.overrides || {})
      .some((values) => Object.prototype.hasOwnProperty.call(values, String(person.employee_id)));
    const hasDeduction = Object.prototype.hasOwnProperty.call(
      state.attendance?.deductions || {}, String(person.employee_id),
    );
    if (state.attendanceFilter === "events" && !hasEvent) return false;
    if (state.attendanceFilter === "manual" && !isManual && !hasDeduction) return false;
    if (state.attendanceFilter === "conflicts" && !conflictIds.has(Number(person.employee_id))) return false;
    if (state.attendanceFilter === "mismatches" && !mismatchIds.has(Number(person.employee_id))) return false;
    return true;
  });
}

function updateAttendanceSummary(rows, warnings, mismatches = attendanceScheduleMismatches(rows)) {
  let holidayHours = 0;
  let overtimeHours = 0;
  let eventCount = 0;
  let workedDays = 0;
  for (const person of rows) {
    holidayHours += Number(person.holiday_hours || 0);
    overtimeHours += Number(person.overtime_hours || 0);
    const days = new Set([
      ...Object.keys(person.baseCodes || {}),
      ...Object.keys(state.attendance?.overrides || {}),
    ]);
    for (const day of days) {
      const code = effectiveAttendanceCode(person, day);
      if (code === "X") workedDays += 1;
      if (isExceptionCode(code)) eventCount += 1;
    }
  }
  const manualCount = Object.values(state.attendance?.overrides || {})
    .reduce((total, values) => total + Object.keys(values).length, 0);
  const conflictCount = new Set(warnings.map((warning) =>
    `${warning.employee_id}:${warning.date}`)).size;
  const deductionTotal = Object.values(state.attendance?.deductions || {})
    .reduce((total, amount) => total + Number(amount || 0), 0);
  const formattedDeduction = new Intl.NumberFormat("tr-TR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(deductionTotal);
  renderSummary("#attendance-summary", [
    ["Çalışılan gün", String(workedDays)],
    ["Resmî tatil mesaisi", `${holidayHours.toFixed(2)} saat`],
    ["Fazla mesai", `${overtimeHours.toFixed(2)} saat`],
    ["İzin / özel puantaj kodu", String(eventCount)],
    ["Kesinti tutarı", `₺${formattedDeduction}`],
    ["Çakışma / elle düzeltme", `${conflictCount} / ${manualCount}`],
    ["Plan farkı", String(mismatches.length)],
  ]);
  const reportRows = rows.map((person) => {
    const counts = { X: 0, H: 0, S: 0, R: 0, Ü: 0, G: 0, "G.Ç": 0, other: 0 };
    const days = new Set([
      ...Object.keys(person.baseCodes || {}),
      ...Object.keys(state.attendance?.overrides || {}),
    ]);
    for (const day of days) {
      const code = effectiveAttendanceCode(person, day);
      if (Object.prototype.hasOwnProperty.call(counts, code)) counts[code] += 1;
      else if (code) counts.other += 1;
    }
    return {
      person,
      counts,
      holiday: Number(person.holiday_hours || 0),
      overtime: Number(person.overtime_hours || 0),
    };
  });
  el("#attendance-person-report").innerHTML = `<details class="attendance-person-details">
    <summary>Personel bazında dönem özeti (${rows.length})</summary>
    <p>Fazla mesai, her gün 8 saati aşan çalışma sürelerinin toplamıdır; ücret veya prim hesaplanmaz.</p>
    <div class="attendance-person-table-wrap"><table class="attendance-person-table">
      <thead><tr><th>Personel</th><th>X</th><th>H</th><th>S</th><th>R</th><th>Ü</th><th>G</th><th>G.Ç</th><th>Diğer</th><th>Resmî tatil saat</th><th>Fazla mesai saat</th></tr></thead>
      <tbody>${reportRows.map(({ person, counts, holiday, overtime }) => `<tr>
        <th scope="row">${escapeHtml(person.name)}</th>
        <td>${counts.X}</td><td>${counts.H}</td><td>${counts.S}</td><td>${counts.R}</td>
        <td>${counts["Ü"]}</td><td>${counts.G}</td><td>${counts["G.Ç"]}</td><td>${counts.other}</td>
        <td>${holiday.toFixed(2)}</td><td>${overtime.toFixed(2)}</td>
      </tr>`).join("")}</tbody>
    </table></div>
  </details>`;
  renderBalanceSummary();
}

function leaveStatus(person) {
  if (!person.has_opening) return null;
  if (person.annual_leave_remaining_days < 0) return "negative";
  if (person.annual_leave_remaining_days <= 2) return "low";
  return null;
}

function renderLeaveAlerts() {
  const node = el("#leave-alerts");
  if (!node) return;
  const people = (state.balanceSummary?.people || []).filter((person) => person.active && leaveStatus(person));
  node.hidden = !people.length;
  if (!people.length) { node.innerHTML = ""; return; }
  const number = (value) => new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 2 }).format(value);
  node.innerHTML = `<strong>Yıllık izin uyarısı</strong>${people.map((person) =>
    `<span class="leave-badge leave-${leaveStatus(person)}">${escapeHtml(person.name)}: ${number(person.annual_leave_remaining_days)} gün</span>`).join("")}`;
}

function renderBalanceSummary() {
  renderLeaveAlerts();
  const container = el("#attendance-balance-report");
  if (!container) return;
  const summary = state.balanceSummary;
  if (!summary) {
    container.innerHTML = `<p class="attendance-balance-empty">Yıl içi takip özeti yükleniyor…</p>`;
    return;
  }
  const number = (value) => new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 2 }).format(value);
  const rows = summary.people.map((person) => `<tr>
    <th scope="row">${escapeHtml(person.name)}${person.active ? "" : " · pasif"}</th>
    <td><input data-balance-field="annual_leave_opening_days" type="number" min="-9999" max="9999" step="0.25"
      value="${escapeHtml(person.annual_leave_opening_days)}" aria-label="${escapeHtml(person.name)} açılış izin günü"></td>
    <td>${number(person.annual_leave_used_days)}</td>
    <td>${number(person.annual_leave_remaining_days)}${leaveStatus(person) ? ` <span class="leave-badge leave-${leaveStatus(person)}">${leaveStatus(person) === "negative" ? "Eksi" : "Az"}</span>` : ""}</td>
    <td><input data-balance-field="overtime_opening_hours" type="number" min="-9999" max="9999" step="0.25"
      value="${escapeHtml(person.overtime_opening_hours)}" aria-label="${escapeHtml(person.name)} açılış fazla mesai saati"></td>
    <td>${number(person.overtime_reported_hours)}</td>
    <td>${number(person.overtime_tracked_hours)}</td>
    <td>${number(person.sick_days)}</td>
    <td>${number(person.unpaid_leave_days)}</td>
    <td>${number(person.absence_days)}</td>
    <td><button class="button button-light" type="button" data-balance-ledger="${person.employee_id}">Hareket dökümü</button>
      <button class="button button-outline" type="button" data-balance-save="${person.employee_id}">Kaydet</button></td>
  </tr>`).join("");
  container.innerHTML = `<details class="attendance-person-details balance-details" open>
    <summary>${summary.year} · Ocak–${MONTH_NAMES[summary.through_month - 1]} · Personel bakiyeleri (${summary.people.length})</summary>
    <div class="attendance-person-table-wrap"><table class="attendance-person-table balance-table">
      <thead><tr><th>Personel</th><th>Açılış izin günü</th><th>Kullanılan S</th><th>Kalan takip günü</th>
        <th>Açılış mesai saati</th><th>Hesaplanan mesai</th><th>Takip toplamı</th>
        <th>R günü</th><th>Ü günü</th><th>G günü</th><th>İşlem</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="11">Personel kaydı bulunamadı.</td></tr>`}</tbody>
    </table></div>
    <section id="balance-ledger-result" aria-live="polite"></section>
  </details>`;
}

async function showLeaveBalanceLedger(button) {
  const personId = Number(button.dataset.balanceLedger);
  const result = el("#balance-ledger-result");
  result.textContent = "İzin hareket dökümü yükleniyor…";
  try {
   const data = await request(`/api/balances?month=${encodeURIComponent(state.attendanceMonth)}&employee_id=${personId}`);
   const ledger = data.people.find((person) => person.employee_id === personId)?.leave_balance_ledger;
   if (!ledger) throw new Error("Seçilen personel için hareket dökümü bulunamadı.");
   const format = (value) => value === null || value === undefined
     ? "—" : new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 2 }).format(value);
   result.innerHTML = `<h3>${escapeHtml(data.people.find((person) => person.employee_id === personId).name)} · ${ledger.year} izin hareketleri</h3>
     <p>${escapeHtml(ledger.notice)}</p>
     ${ledger.opening_configured ? "" : '<p role="status">Bu yıl için açılış bakiyesi yapılandırılmamış; kalan bakiye hesaplanmadı.</p>'}
     <div class="attendance-person-table-wrap"><table class="attendance-person-table">
       <thead><tr><th>Tarih</th><th>Hareket</th><th>Kaynak / açıklama</th><th>Değişim (gün)</th><th>İşlem sonrası</th><th>Kaydeden</th></tr></thead>
       <tbody>${ledger.movements.map((item) => `<tr>
         <td>${escapeHtml(item.date)}</td><td>${escapeHtml(item.type === "opening" ? "Açılış" : item.type === "adjustment" ? "Düzeltme" : item.type === "cancellation" ? "İptal (bilgi)" : "Kullanım")}</td>
         <td>${escapeHtml(`${item.source} · ${item.detail}`)}</td><td>${format(item.delta)}</td>
         <td>${format(item.running_balance)}</td><td>${escapeHtml(item.actor || "—")}</td></tr>`).join("") || '<tr><td colspan="6">Bu dönemde hareket yok.</td></tr>'}</tbody>
     </table></div><p><strong>Hesaplanan kapanış:</strong> ${format(ledger.closing_days)} gün</p>`;
  } catch (error) {
   result.textContent = error.message;
   toast(error.message, true);
  }
}

async function saveBalanceOpening(button) {
  const row = button.closest("tr");
  const employeeId = Number(button.dataset.balanceSave);
  const leaveInput = row.querySelector('[data-balance-field="annual_leave_opening_days"]');
  const overtimeInput = row.querySelector('[data-balance-field="overtime_opening_hours"]');
  const controls = [...row.querySelectorAll("input, button")];
  controls.forEach((control) => { control.disabled = true; });
  el("#balance-status").textContent = "Açılış bakiyesi kaydediliyor.";
  try {
    await request("/api/balances", {
      method: "PUT",
      body: JSON.stringify({
        employee_id: employeeId,
        year: state.balanceSummary.year,
        annual_leave_opening_days: Number(leaveInput.value),
        overtime_opening_hours: Number(overtimeInput.value),
      }),
    });
    state.balanceSummary = await request(`/api/balances?month=${encodeURIComponent(state.attendanceMonth)}`);
    renderBalanceSummary();
    el("#balance-status").textContent = "Açılış bakiyesi kaydedildi.";
  } catch (error) {
    controls.forEach((control) => { control.disabled = false; });
    el("#balance-status").textContent = error.message;
    toast(error.message, true);
  }
}

function attendancePeriodTotals(data) {
  const totals = {
    workedDays: 0, weeklyOffDays: 0, annualLeaveDays: 0, sickDays: 0,
    unpaidLeaveDays: 0, absenceDays: 0, overnightExitDays: 0, otherCodes: 0,
    holidayHours: 0,
  };
  const overrides = data.overrides || {};
  for (const person of data.attendance.rows || []) {
    totals.holidayHours += Number(person.holiday_hours || 0);
    const personId = String(person.employee_id);
    const days = new Set([
      ...Object.keys(person.automatic_codes || {}),
      ...Object.keys(overrides),
    ]);
    for (const day of days) {
      const code = overrides[day]?.[personId] ?? person.automatic_codes?.[day] ?? "";
      if (code === "X") totals.workedDays += 1;
      else if (code === "H") totals.weeklyOffDays += 1;
      else if (code === "S") totals.annualLeaveDays += 1;
      else if (code === "R") totals.sickDays += 1;
      else if (code === "Ü") totals.unpaidLeaveDays += 1;
      else if (code === "G") totals.absenceDays += 1;
      else if (code === "G.Ç") totals.overnightExitDays += 1;
      else if (code) totals.otherCodes += 1;
    }
  }
  return totals;
}

function monthLabel(month) {
  const [year, monthNumber] = month.split("-").map(Number);
  return new Intl.DateTimeFormat("tr-TR", { month: "long", year: "numeric" })
    .format(new Date(year, monthNumber - 1, 15));
}

async function renderAttendancePeriodComparison() {
  const comparisonMonth = el("#attendance-compare-month").value;
  const result = el("#attendance-comparison-result");
  if (!comparisonMonth) throw new Error("Karşılaştırılacak ayı seçin.");
  if (comparisonMonth === state.attendanceMonth) {
    throw new Error("Karşılaştırma için farklı bir ay seçin.");
  }
  const requestId = ++state.attendanceComparisonRequestId;
  const currentMonthValue = state.attendanceMonth;
  const response = await request(`/api/attendance?month=${encodeURIComponent(comparisonMonth)}`);
  if (requestId !== state.attendanceComparisonRequestId || currentMonthValue !== state.attendanceMonth) return;
  const currentTotals = attendancePeriodTotals({
    attendance: state.attendance,
    overrides: state.attendance.overrides,
  });
  const comparisonTotals = attendancePeriodTotals(response);
  const metrics = [
    ["Çalışılan gün (X)", "workedDays"],
    ["Hafta tatili (H)", "weeklyOffDays"],
    ["Yıllık izin (S)", "annualLeaveDays"],
    ["Rapor (R)", "sickDays"],
    ["Ücretsiz izin (Ü)", "unpaidLeaveDays"],
    ["Devamsızlık (G)", "absenceDays"],
    ["Gece çıkışı (G.Ç)", "overnightExitDays"],
    ["Diğer puantaj kodları", "otherCodes"],
    ["Resmî tatil saat", "holidayHours"],
  ];
  const format = (key, value) => key.endsWith("Hours")
    ? Number(value).toFixed(2)
    : String(value);
  result.innerHTML = `<div class="attendance-person-table-wrap"><table class="attendance-person-table">
    <thead><tr><th>Gösterge</th><th>${escapeHtml(monthLabel(state.attendanceMonth))}</th>
      <th>${escapeHtml(monthLabel(comparisonMonth))}</th><th>Fark</th></tr></thead>
    <tbody>${metrics.map(([label, key]) => {
      const current = currentTotals[key];
      const comparison = comparisonTotals[key];
      const difference = current - comparison;
      const formattedDifference = key.endsWith("Hours")
        ? `${difference > 0 ? "+" : ""}${difference.toFixed(2)}`
        : `${difference > 0 ? "+" : ""}${difference}`;
      return `<tr><th scope="row">${label}</th><td>${format(key, current)}</td>
        <td>${format(key, comparison)}</td><td>${formattedDifference}</td></tr>`;
    }).join("")}</tbody>
  </table></div>`;
}

function applyAttendanceOvertime(person) {
  const manual = state.attendance.overtime?.[String(person.employee_id)];
  person.overtime_manual = manual !== undefined;
  const value = manual !== undefined ? Number(manual) : Number(person.overtime_computed_hours || 0);
  person.overtime_hours = Math.round(value * 100) / 100;
}

function handleAttendanceEdit(event) {
  const control = event.target;
  if (!state.attendance) return;
  if (control.dataset.action === "attendance-overtime") {
    const personId = String(control.dataset.person);
    const person = state.attendance.rows.find((item) => String(item.employee_id) === personId);
    const raw = control.value.trim();
    const hours = Number(raw.replace(",", "."));
    if (raw && (!/^\d+(?:[.,]\d{0,2})?$/.test(raw) || !Number.isFinite(hours) || hours > 744)) {
      toast("Fazla mesai saatini 0 ile 744 arasında girin; otomatik hesaba dönmek için kutuyu boşaltın.", true);
      renderAttendance();
      return;
    }
    if (raw) state.attendance.overtime[personId] = Math.round(hours * 100) / 100;
    else delete state.attendance.overtime[personId];
    if (person) applyAttendanceOvertime(person);
    setAttendanceDirty();
    renderAttendance();
    return;
  }
  if (control.dataset.action === "attendance-deduction") {
    const personId = String(control.dataset.person);
    const rawAmount = control.value.trim();
    const normalizedAmount = rawAmount.replace(",", ".");
    const amount = Number(normalizedAmount);
    if (rawAmount && (!/^\d+(?:[.,]\d{0,2})?$/.test(rawAmount)
      || !Number.isFinite(amount) || amount > 999999999.99)) {
      const current = state.attendance.deductions[personId];
      control.value = current ? Number(current).toFixed(2).replace(".", ",") : "";
      toast("Kesinti tutarını 0 ile 999999999,99 arasında girin.", true);
      return;
    }
    if (rawAmount && amount > 0) {
      state.attendance.deductions[personId] = Math.round((amount + Number.EPSILON) * 100) / 100;
      control.value = state.attendance.deductions[personId].toFixed(2).replace(".", ",");
    } else {
      delete state.attendance.deductions[personId];
      control.value = "";
    }
    setAttendanceDirty();
    updateAttendanceSummary(state.attendance.rows, state.attendance.warnings);
    return;
  }
  if (control.dataset.action !== "attendance-code") return;
  const date = control.dataset.day;
  const personId = String(control.dataset.person);
  const code = control.value.trim().toLocaleUpperCase("tr-TR");
  const overrides = state.attendance.overrides;
  if (!overrides[date]) overrides[date] = {};
  const editedPerson = state.attendance.rows.find((item) => String(item.employee_id) === personId);
  // Typing the schedule's own code is not a manual correction; keep the cell linked to the schedule.
  if (code && code === (editedPerson?.automatic_codes?.[date] || "")) {
    delete overrides[date][personId];
    editedPerson.codes[date] = code;
    editedPerson.sources[date] = "schedule";
    control.classList.remove("manual");
  } else if (code) {
    overrides[date][personId] = code;
    control.classList.add("manual");
    const person = state.attendance.rows.find((item) => String(item.employee_id) === personId);
    if (person) {
      person.codes[date] = code;
      person.sources[date] = "manual";
    }
  } else {
    delete overrides[date][personId];
    control.classList.remove("manual");
    const person = state.attendance.rows.find((item) => String(item.employee_id) === personId);
    if (person) {
      const automatic = person.automatic_codes?.[date];
      if (automatic) {
        person.codes[date] = automatic;
        person.sources[date] = "schedule";
      } else {
        delete person.codes[date];
        delete person.sources[date];
      }
    }
  }
  if (!Object.keys(overrides[date]).length) delete overrides[date];
  setAttendanceDirty();
  renderAttendance();
}

async function saveAttendance(options = {}) {
  if (!state.attendanceDirty) return;
  if (window.easyAssistant && !options.assistantConfirmed &&
      !(await window.easyAssistant.confirmChanges("attendance"))) return;
  const button = el("#save-attendance");
  button.disabled = true;
  const saveStarted = performance.now();
  try {
    const editingMonth = state.attendanceMonth;
    const table = state.attendance;
    const draft = JSON.stringify(editSnapshot("attendance"));
    const result = await request("/api/attendance", {
      method: "PUT",
      body: JSON.stringify({
        month: editingMonth,
        revision: state.attendance.revision,
        overrides: state.attendance.overrides,
        deductions: state.attendance.deductions,
        overtime: state.attendance.overtime || {},
        ...(window.easyUsability ? { merge_independent: true } : {}),
      }),
    });
    if (state.attendanceMonth !== editingMonth || state.attendance !== table ||
        JSON.stringify(editSnapshot("attendance")) !== draft ||
        await loadAttendance({ table, draft, saved: result }) === false) {
      button.disabled = !state.attendanceDirty;
      toast("Gönderilen puantaj kaydedildi. Bu sırada yaptığınız düzenlemeler taslakta kaldı.", true);
      return;
    }
    toast(result.merged ? "Puantaj kaydedildi. Diğer oturumun çakışmayan kodları korundu." : "Puantaj düzeltmeleri kaydedildi.");
  } catch (error) {
    button.disabled = false;
    if (error.attendanceConflicts?.length) showAttendanceSaveConflicts(error.attendanceConflicts);
    toast(error.message, true);
  } finally {
    window.easyUsability?.recordSave("attendance", saveStarted);
  }
}

function askUnsavedChanges(kind, intent) {
  const label = kind === "attendance" ? "puantaj düzeltmeleri" : "vardiya değişiklikleri";
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "unsaved-changes-dialog";
    dialog.setAttribute("aria-labelledby", "unsaved-changes-title");
    dialog.innerHTML = `
      <h2 id="unsaved-changes-title">Kaydedilmemiş ${label} var</h2>
      <p>${escapeHtml(intent)} önce ne yapılsın?</p>
      <div class="dialog-actions">
        <button type="button" class="button button-light" data-choice="cancel">İptal</button>
        <button type="button" class="button button-light danger-button" data-choice="discard">Kaydetmeden devam et</button>
        <button type="button" class="button button-primary" data-choice="save">Kaydet ve devam et</button>
      </div>`;
    let choice = "cancel";
    dialog.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-choice]");
      if (!button) return;
      choice = button.dataset.choice;
      dialog.close();
    });
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(choice);
    }, { once: true });
    document.body.append(dialog);
    dialog.showModal();
    dialog.querySelector("[data-choice='save']").focus();
  });
}

function discardDirty(kind) {
  if (kind === "schedule") {
    state.scheduleDirty = false;
    el("#save-schedule").disabled = true;
  } else {
    state.attendanceDirty = false;
    el("#save-attendance").disabled = true;
  }
}

async function resolveUnsaved(kind, intent) {
  const dirty = kind === "schedule" ? state.scheduleDirty : state.attendanceDirty;
  if (!dirty) return true;
  const choice = await askUnsavedChanges(kind, intent);
  if (choice === "cancel") return false;
  if (choice === "discard") {
    discardDirty(kind);
    return true;
  }
  await (kind === "schedule" ? saveSchedule({ assistantConfirmed: true }) : saveAttendance({ assistantConfirmed: true }));
  return !(kind === "schedule" ? state.scheduleDirty : state.attendanceDirty);
}

async function canLeaveCurrentView() {
  if (state.view !== "schedule" && state.view !== "attendance") return true;
  return resolveUnsaved(state.view, "Bu sayfadan ayrılmadan");
}

function captureViewContext(viewName) {
  const view = el(`#${viewName}-view`);
  if (!view) return null;
  const active = document.activeElement;
  let focus = null;
  if (active && view.contains(active) && !active.closest(".main-nav")) {
    if (active.id) {
      focus = { id: active.id };
    } else {
      const attributes = ["data-action", "data-row", "data-person", "data-day", "data-section", "data-slot", "data-field"]
        .filter((name) => active.hasAttribute(name))
        .map((name) => [name, active.getAttribute(name)]);
      if (attributes.length) focus = { tagName: active.tagName, attributes };
    }
  }
  return {
    scheduleMonth: state.scheduleMonth,
    weekIndex: state.weekIndex,
    windowX: window.scrollX,
    windowY: window.scrollY,
    frames: [...view.querySelectorAll(".table-frame")].map((frame) => ({
      left: frame.scrollLeft,
      top: frame.scrollTop,
    })),
    details: [...view.querySelectorAll("details")].map((item) => item.open),
    focus,
  };
}

function restoreViewContext(viewName, context) {
  if (!context) return;
  const view = el(`#${viewName}-view`);
  if (!view) return;
  [...view.querySelectorAll("details")].forEach((item, index) => {
    if (context.details[index] !== undefined) item.open = context.details[index];
  });
  window.requestAnimationFrame(() => {
    if (state.view !== viewName) return;
    window.scrollTo(context.windowX, context.windowY);
    [...view.querySelectorAll(".table-frame")].forEach((frame, index) => {
      const position = context.frames[index];
      if (!position) return;
      frame.scrollLeft = position.left;
      frame.scrollTop = position.top;
    });
    const focus = context.focus;
    const target = focus?.id
      ? document.getElementById(focus.id)
      : focus
        ? [...view.querySelectorAll("input, select, button, textarea, [tabindex]")].find((item) =>
          item.tagName === focus.tagName
          && focus.attributes.every(([name, value]) => item.getAttribute(name) === value))
        : null;
    if (target && view.contains(target) && !target.disabled) {
      target.focus({ preventScroll: true });
    }
  });
}

async function switchView(view) {
  if (view === state.view) return true;
  const previousContext = captureViewContext(state.view);
  if (!(await canLeaveCurrentView())) return false;
  const previousView = state.view;
  state.viewContexts[previousView] = previousContext;
  const targetContext = state.viewContexts[view];
  if (state.templateEdit && state.templateEdit !== view) state.templateEdit = null;
  state.view = view;
  document.querySelectorAll(".nav-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
  document.querySelectorAll(".view").forEach((section) => section.classList.toggle("active", section.id === `${view}-view`));
  el(`#${view}-view .control-strip`).prepend(el(".main-nav"), el("#save-state"));
  try {
    if (view === "schedule") await loadSchedule(targetContext?.scheduleMonth === state.scheduleMonth ? targetContext.weekIndex : 0);
    else if (view === "attendance") await loadAttendance();
    else if (view === "archive") await Promise.all([loadArchive(), loadBackups(), loadHistory()]);
    else if (view === "templates") await loadTableTemplates();
    if (state.view !== view) return false;
    restoreViewContext(view, targetContext);
    return true;
  } catch (error) {
    if (state.view !== view) return false;
    state.view = previousView;
    document.querySelectorAll(".nav-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === previousView));
    document.querySelectorAll(".view").forEach((section) => section.classList.toggle("active", section.id === `${previousView}-view`));
    el(`#${previousView}-view .control-strip`).prepend(el(".main-nav"), el("#save-state"));
    restoreViewContext(previousView, previousContext);
    const label = view === "attendance" ? "Puantaj" : view === "schedule" ? "Vardiya" : "Sayfa";
    toast(`${label} yüklenemedi. ${friendlyErrorText(error.message)}`, true, {
      label: "Yeniden dene",
      run: async () => {
        if (await switchView(view)) el("#toast").classList.remove("visible");
      },
    });
    return false;
  }
}

async function loadTableTemplates() {
  state.tableTemplates = await request("/api/table-templates");
  renderTableTemplates();
}

function ownPageKeys(kind) {
  return Object.keys(state.workbookSettings.sheets)
    .filter((key) => key.startsWith(`${kind}:`) && !key.startsWith(`${kind}:template:`));
}

function renderTableTemplates() {
  if (!state.tableTemplates) return;
  for (const card of document.querySelectorAll("[data-template-kind]")) {
    const kind = card.dataset.templateKind;
    const status = state.tableTemplates[kind];
    const badge = card.querySelector("[data-template-badge]");
    badge.textContent = status.custom ? "Özel Excel şablonu" : "Özgün şablon";
    badge.classList.toggle("custom", status.custom);
    const updated = status.updated_at
      ? ` · Yüklenme: ${new Date(status.updated_at).toLocaleString("tr-TR", { dateStyle: "medium", timeStyle: "short" })}`
      : "";
    card.querySelector("[data-template-meta]").textContent = `Sayfalar: ${status.sheets.join(", ")}${updated}`;
    const layers = Object.entries(state.workbookSettings.sheets)
      .filter(([key]) => key.startsWith(`${kind}:template:`));
    const cellCount = layers.reduce((total, [, sheet]) => total + Object.keys(sheet.cells || {}).length, 0);
    const ownPages = ownPageKeys(kind).length;
    card.querySelector("[data-template-layer]").textContent = `${layers.length
      ? `Şablon biçimi: ${layers.length} sayfa, ${cellCount} biçimli hücre.`
      : "Şablon biçimi yok; tablolar Excel şablonunun kendi görünümüyle açılır."} `
      + `${ownPages ? `${ownPages} ${kind === "schedule" ? "ay/hafta sayfası" : "ay"} kendi biçimini kullanıyor ve şablon değişikliklerinden etkilenmez.` : "Kendi biçimi olan sayfa yok."}`;
    card.querySelector("[data-template-layer-reset]").disabled = !layers.length;
    card.querySelector("[data-template-pages-reset]").disabled = !ownPages;
    card.querySelector("[data-template-file-reset]").disabled = !status.custom;
  }
}

function invalidateTemplateCache() {
  state.templates = { schedule: {}, attendance: {} };
}

async function openTemplateEditorDialog(kind) {
  if (!["schedule", "attendance"].includes(kind)) throw new Error("Düzenlenecek tablo türü geçersiz.");
  const previousView = state.view;
  if (!await switchView(kind)) return;
  const templateIndex = kind === "schedule" ? (state.weekIndex >= 5 ? 1 : state.weekIndex) : 0;
  if (!(kind === "schedule" ? state.schedule : state.attendance) || !state.templates[kind]?.[templateIndex]) {
    await switchView(previousView);
    throw new Error("Tablo şablonu yüklenemedi; önce bağlantıyı kontrol edip yeniden deneyin.");
  }
  state.templateEdit = kind;
  state.formatMode = true;
  const view = el(`#${kind}-view`);
  const placeholder = document.createComment("template-editor-view");
  const dialog = document.createElement("dialog");
  dialog.className = "template-editor-dialog";
  const heading = document.createElement("div");
  heading.className = "template-editor-heading";
  const title = document.createElement("strong");
  title.id = "template-editor-title";
  title.textContent = `${kind === "schedule" ? "Vardiya" : "Puantaj"} şablonunu düzenle`;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "button button-light";
  close.textContent = "Şablonlara dön";
  close.addEventListener("click", () => dialog.close());
  heading.append(title, close);
  dialog.setAttribute("aria-labelledby", title.id);
  try {
    view.before(placeholder);
    dialog.append(heading, view);
    document.body.append(dialog);
    const nav = el(".main-nav");
    const topbar = el(".topbar");
    if (nav && topbar && nav.parentElement !== topbar) {
      const anchor = el("#open-guide");
      topbar.insertBefore(nav, anchor?.parentElement === topbar ? anchor : null);
    }
    const panel = view.querySelector(".format-panel");
    if (panel) panel.open = true;
    view.querySelectorAll("[data-format-mode]").forEach((input) => { input.checked = true; });
    if (kind === "schedule") renderSchedule();
    else renderAttendance();
    dialog.addEventListener("close", () => {
      placeholder.before(view);
      placeholder.remove();
      dialog.remove();
      state.formatMode = false;
      state.templateEdit = null;
      view.querySelectorAll("[data-format-mode]").forEach((input) => { input.checked = false; });
      switchView(previousView).catch((error) => toast(error.message, true));
    }, { once: true });
    dialog.showModal();
    view.querySelector(".template-edit-banner")?.scrollIntoView({ block: "nearest" });
  } catch (error) {
    if (view.parentElement === dialog && placeholder.parentNode) placeholder.before(view);
    placeholder.remove();
    dialog.remove();
    state.formatMode = false;
    state.templateEdit = null;
    view.querySelectorAll("[data-format-mode]").forEach((input) => { input.checked = false; });
    if (state.view !== previousView) await switchView(previousView);
    throw error;
  }
}

async function replaceWorkbookSheets(mutator, message) {
  const settings = structuredClone(state.workbookSettings);
  mutator(settings.sheets);
  state.workbookSettings = await request("/api/workbook-settings", { method: "PUT", body: JSON.stringify(settings) });
  state.workbookHistory = {};
  renderTableTemplates();
  toast(message);
}

function installTableTemplatePage() {
  el("#templates-view").addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    const card = event.target.closest("[data-template-kind]");
    if (!button || !card) return;
    const kind = card.dataset.templateKind;
    const label = kind === "schedule" ? "Vardiya" : "Puantaj";
    try {
      if (button.matches("[data-template-edit]")) {
        await openTemplateEditorDialog(kind);
      } else if (button.matches("[data-template-layer-reset]")) {
        if (!window.confirm(`${label} şablonundaki tüm tablo biçimleri temizlensin mi? Kendi biçimi olmayan sayfalar Excel şablonunun görünümüne döner.`)) return;
        await replaceWorkbookSheets((sheets) => {
          for (const key of Object.keys(sheets)) if (key.startsWith(`${kind}:template:`)) delete sheets[key];
        }, `${label} şablon biçimi temizlendi.`);
      } else if (button.matches("[data-template-pages-reset]")) {
        const count = ownPageKeys(kind).length;
        if (!window.confirm(`${count} sayfanın kendi biçimi, eklenen satır/sütunları ve alt not metinleri silinip şablona döndürülsün mü? Vardiya ve puantaj verileri silinmez.`)) return;
        await replaceWorkbookSheets((sheets) => {
          for (const key of ownPageKeys(kind)) delete sheets[key];
        }, `${count} sayfa şablona döndürüldü.`);
      } else if (button.matches("[data-template-download]")) {
        const environment = await request("/api/environment");
        if (environment.desktop) {
          const result = await request("/api/table-templates/save", { method: "POST", body: JSON.stringify({ kind }) });
          if (!result.cancelled) toast(`Şablon kaydedildi: ${result.path}`);
        } else {
          const blob = await request(`/api/table-templates/download?kind=${kind}`);
          await saveFormBlob(blob, "xlsx", `${kind === "schedule" ? "VARDIYA" : "PUANTAJ"}_SABLONU.xlsx`);
        }
      } else if (button.matches("[data-template-file-reset]")) {
        if (!window.confirm(`${label} için yüklenen Excel şablonu kaldırılıp özgün şablona dönülsün mü?`)) return;
        state.tableTemplates[kind] = await request("/api/table-templates/reset", { method: "POST", body: JSON.stringify({ kind }) });
        invalidateTemplateCache();
        renderTableTemplates();
        toast(`${label} özgün Excel şablonuna döndü.`);
      }
    } catch (error) {
      if (error.name !== "AbortError") toast(error.message, true);
    }
  });
  el("#templates-view").addEventListener("change", async (event) => {
    const input = event.target.closest("[data-template-upload]");
    if (!input) return;
    const kind = input.closest("[data-template-kind]").dataset.templateKind;
    const file = input.files[0];
    input.value = "";
    if (!file) return;
    if (file.size > 5000000 || !file.name.toLowerCase().endsWith(".xlsx")) {
      toast("En fazla 5 MB boyutunda bir .xlsx dosyası seçin.", true);
      return;
    }
    try {
      const data = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("Dosya okunamadı."));
        reader.readAsDataURL(file);
      });
      state.tableTemplates[kind] = await request("/api/table-templates/upload", {
        method: "POST", body: JSON.stringify({ kind, content: String(data).split(",")[1] || "" }),
      });
      invalidateTemplateCache();
      renderTableTemplates();
      toast(`${kind === "schedule" ? "Vardiya" : "Puantaj"} Excel şablonu yüklendi; tablolar ve Excel çıktıları bu şablonu kullanacak.`);
    } catch (error) {
      toast(error.message, true);
    }
  });
}

async function loadArchive() {
  const result = await request("/api/archive");
  state.archiveMonths = result.months;
  if (hasArchiveFilters()) await applyArchiveFilters();
  else renderArchive();
}

function hasArchiveFilters() {
  return ["#archive-filter-month", "#archive-filter-person", "#archive-filter-section", "#archive-filter-type"]
    .some((selector) => el(selector).value.trim());
}

async function applyArchiveFilters() {
  const button = el("#apply-archive-filter");
  button.disabled = true;
  try {
    const params = new URLSearchParams({
      kind: state.archiveKind,
      month: el("#archive-filter-month").value,
      employee: el("#archive-filter-person").value,
      section: el("#archive-filter-section").value,
      type: el("#archive-filter-type").value,
    });
    const result = await request(`/api/archive/search?${params}`);
    state.archiveMatchMonths = new Set(result.months);
    el("#archive-filter-status").textContent = `${result.months.length} ay eşleşti`;
    renderArchive();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function loadBackups() {
  const result = await request("/api/backups");
  state.backups = result.backups;
  const input = el("#backup-keep-input");
  if (input && document.activeElement !== input) input.value = String(result.settings?.keep_automatic ?? 0);
  renderBackups();
}

async function saveBackupRetention(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  const keep = Number(el("#backup-keep-input").value || 0);
  if (keep > 0 && !window.confirm(`Son ${keep} otomatik yedek dışındaki eski otomatik yedekler silinecek. Devam edilsin mi?`)) return;
  button.disabled = true;
  try {
    const result = await request("/api/backup-settings", {
      method: "PUT", body: JSON.stringify({ keep_automatic: keep }),
    });
    el("#backup-keep-input").blur();
    await loadBackups();
    toast(result.keep_automatic
      ? `Son ${result.keep_automatic} otomatik yedek saklanacak${result.removed.length ? `; ${result.removed.length} eski yedek silindi` : ""}.`
      : "Otomatik yedekler sınırsız saklanacak.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function loadHistory() {
  const [result, user] = await Promise.all([request("/api/history?limit=100"), request("/api/user-settings")]);
  state.history = result.history;
  const input = el("#user-name-input");
  if (document.activeElement !== input) input.value = user.user_name === "Yerel kullanıcı" ? "" : user.user_name;
  renderHistory();
}

async function saveUserName(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  try {
    const result = await request("/api/user-settings", {
      method: "PUT", body: JSON.stringify({ user_name: el("#user-name-input").value }),
    });
    el("#user-name-input").blur();
    await loadHistory();
    toast(`Değişiklikler artık “${result.user_name}” adıyla kaydedilecek.`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function renderHistory() {
  const list = el("#history-list");
  if (!state.history.length) {
    list.innerHTML = '<p class="archive-empty">Henüz değişiklik geçmişi yok.</p>';
    return;
  }
  list.innerHTML = `<table class="archive-table history-table"><thead><tr><th>Tarih / saat</th><th>Kim</th><th>Değişiklik</th></tr></thead><tbody>${state.history.map((item) => {
    const when = new Date(item.occurred_at).toLocaleString("tr-TR");
    return `<tr><td>${escapeHtml(when)}</td><td>${escapeHtml(item.actor)}</td><td>${escapeHtml(item.summary)}</td></tr>`;
  }).join("")}</tbody></table>`;
}

function renderBackups() {
  const list = el("#backup-list");
  if (!state.backups.length) {
    list.innerHTML = '<p class="archive-empty">Henüz yedek yok. İlk veri değişikliğinde otomatik yedek alınır veya şimdi yedekleyebilirsiniz.</p>';
    return;
  }
  const formatBytes = (size) => new Intl.NumberFormat("tr-TR", {
    maximumFractionDigits: 1,
  }).format(size / 1024) + " KB";
  list.innerHTML = `<table class="archive-table backup-table"><thead><tr><th>Tür</th><th>Oluşturulma</th><th>Boyut</th><th>İşlem</th></tr></thead><tbody>${state.backups.map((backup) => {
    const created = new Date(backup.created_at).toLocaleString("tr-TR");
    const id = escapeHtml(backup.id);
    const type = backup.id.startsWith("automatic-") ? "Otomatik"
      : backup.id.startsWith("before-restore-") ? "Geri yükleme öncesi" : "Elle oluşturuldu";
    return `<tr><td>${type}</td><td>${escapeHtml(created)}</td><td>${formatBytes(backup.size_bytes)}${backup.template_count === null || backup.template_count === undefined ? "" : `<br><small>${backup.template_count ? `${backup.template_count} özel tablo şablonu` : "Varsayılan tablo şablonları"}</small>`}</td><td><div class="archive-actions">
      <button class="button button-outline" type="button" data-backup-verify="${id}">Doğrula</button>
      <button class="button button-light" type="button" data-backup-restore="${id}">Geri yükle</button>
    </div></td></tr>`;
  }).join("")}</tbody></table>`;
}

async function handleBackupClick(event) {
  const verify = event.target.closest("[data-backup-verify]");
  const restore = event.target.closest("[data-backup-restore]");
  if (verify) {
    verify.disabled = true;
    try {
      const { backup } = await request("/api/backups/verify", {
        method: "POST",
        body: JSON.stringify({ id: verify.dataset.backupVerify }),
      });
      const cell = verify.closest("td");
      const summary = cell.querySelector(".backup-contents") || document.createElement("p");
      summary.className = "backup-contents";
      summary.setAttribute("role", "status");
      const { attachment_count: attachments, ui_settings: hasSettings } = backup.contents;
      const templates = backup.template_count;
      summary.textContent = [
        "SQLite bütünlük denetimi geçti.",
        `Belge ekleri: ${attachments === null ? "ek tablosu yok" : `${attachments} kayıt`}.`,
        `Özel Excel şablonları: ${templates == null ? "şablon klasörü yok" : `${templates} dosya`}.`,
        `Kayıtlı görünüm ayarları: ${hasSettings ? "dahil" : "bu yedekte kayıt yok"}.`,
        "Harici çıktı dosyaları, tarayıcı tercihleri ve EXE dahil değil. Ekler ve şablonlar tek tek açılarak sınanmadı.",
      ].join(" ");
      cell.append(summary);
    } catch (error) {
      toast(error.message, true);
    } finally {
      verify.disabled = false;
    }
  } else if (restore) {
    const id = restore.dataset.backupRestore;
    if (!window.confirm("Seçilen yedek uygulama verilerinin ve yedekte kayıtlı tablo şablonlarının yerini alacak. Mevcut veriler geri yükleme öncesi ayrı bir yedek olarak saklanacak. Devam edilsin mi?")) return;
    restore.disabled = true;
    try {
      const result = await request("/api/backups/restore", {
        method: "POST",
        body: JSON.stringify({ id }),
      });
      toast(`Yedek geri yüklendi. Önceki veriler "${result.safety_backup.id}" adıyla saklandı.`);
      window.location.reload();
    } catch (error) {
      toast(error.message, true);
      restore.disabled = false;
    }
  }
}

function renderArchive() {
  const kind = state.archiveKind;
  const list = el("#archive-list");
  const months = state.archiveMonths.filter((item) =>
    (kind === "schedule" ? item.schedule_revision : item.attendance_derived || item.attendance_revision)
    && (!state.archiveMatchMonths || state.archiveMatchMonths.has(item.month)));
  if (!months.length) {
    list.innerHTML = `<p class="archive-empty">Henüz ${kind === "schedule" ? "vardiya" : "puantaj"} arşivinde kayıtlı bir ay yok.</p>`;
    return;
  }
  list.innerHTML = `<table class="archive-table"><thead><tr><th>Ay</th><th>${kind === "schedule" ? "Vardiya" : "Puantaj"}</th></tr></thead><tbody>${months.map((item) => {
    const [year, month] = item.month.split("-").map(Number);
    const revision = item[`${kind}_revision`];
    const actions = `<div class="archive-actions">
      <button class="button button-outline" type="button" data-archive-export="${kind}" data-month="${escapeHtml(item.month)}">Excel'e aktar</button>
      ${revision ? `<button class="button button-light" type="button" data-archive-revisions="${kind}" data-month="${escapeHtml(item.month)}">Sürümler</button>` : ""}
      <button class="button button-outline" type="button" data-archive-open="${kind}" data-month="${escapeHtml(item.month)}">${item.locked ? "Aç / kilitli" : "Aç / düzenle"}</button>
      <button class="button button-light" type="button" data-archive-lock="${escapeHtml(item.month)}" data-locked="${item.locked ? "true" : "false"}">${item.locked ? "Kilidi aç" : "Dönemi kilitle"}</button>
      ${revision ? `<button class="button button-light" type="button" data-archive-delete="${kind}" data-month="${escapeHtml(item.month)}">Sil</button>` : ""}
      </div>${!revision ? '<small>Vardiyadan hesaplanır; elle girilmiş puantaj kaydı yok.</small>' : ""}`;
    return `<tr><th scope="row">${MONTH_NAMES[month - 1]} ${year}</th><td>${actions}</td></tr>`;
  }).join("")}</tbody></table>`;
}

async function handleArchiveClick(event) {
  const open = event.target.closest("[data-archive-open]");
  const exportButton = event.target.closest("[data-archive-export]");
  const revisionsButton = event.target.closest("[data-archive-revisions]");
  const lock = event.target.closest("[data-archive-lock]");
  const remove = event.target.closest("[data-archive-delete]");
  if (revisionsButton) {
    await openArchiveRevisions(revisionsButton.dataset.archiveRevisions, revisionsButton.dataset.month);
  } else if (exportButton) {
    await downloadExcel(exportButton.dataset.archiveExport, {
      month: exportButton.dataset.month,
      employee: el("#archive-filter-person").value.trim(),
    });
  } else if (lock) {
    const month = lock.dataset.archiveLock;
    const currentlyLocked = lock.dataset.locked === "true";
    const question = currentlyLocked ? "kilidi açılsın mı?" : "kilitlensin mi?";
    const warning = currentlyLocked
      ? "Dönem yeniden düzenlenebilir olacak."
      : "Dönem puantajı, vardiya kayıtları ve bu tarihlerle kesişen izin/devamsızlık kayıtları değiştirilemeyecek.";
    if (!window.confirm(`${month} dönemi ${question}\n\n${warning}`)) return;
    let reason = "";
    if (currentlyLocked) {
      reason = (window.prompt(`${month} dönemini yeniden açma gerekçesi (zorunlu, değişiklik geçmişine yazılır):`, "") || "").trim();
      if (reason.length < 5) { toast("Kilidi açmak için en az 5 karakterlik gerekçe yazın.", true); return; }
    }
    lock.disabled = true;
    try {
      await request("/api/archive/period-lock", {
        method: "POST",
        body: JSON.stringify(currentlyLocked ? { month, locked: false, reason } : { month, locked: true }),
      });
      await loadArchive();
      toast(`${month} dönemi ${currentlyLocked ? "yeniden açıldı" : "kilitlendi"}.`);
    } catch (error) {
      toast(error.message, true);
      lock.disabled = false;
    }
  } else if (open) {
    const kind = open.dataset.archiveOpen;
    const month = open.dataset.month;
    if (kind === "schedule") {
      state.scheduleMonth = month;
      el("#schedule-month").value = month;
    } else {
      state.attendanceMonth = month;
      el("#attendance-month").value = month;
      const [year, number] = month.split("-").map(Number);
      el("#attendance-period-label").textContent = `${MONTH_NAMES[number - 1]} ${year}`;
    }
    await switchView(kind);
  } else if (remove) {
    const kind = remove.dataset.archiveDelete;
    const month = remove.dataset.month;
    const record = state.archiveMonths.find((item) => item.month === month);
    const revision = record?.[`${kind}_revision`];
    if (!revision) return;
    const warning = kind === "schedule"
      ? "Bu ayın tüm vardiya satırları silinecek; puantaj hesapları ve komşu ayın sınır günleri değişebilir. Elle girilen puantaj düzeltmeleri korunur."
      : "Bu ayın elle girilen puantaj kodları ve kesintileri silinecek. Vardiyalar korunur ve puantaj onlardan yeniden hesaplanır.";
    if (!window.confirm(`${month}: ${warning}\n\nBu işlem geri alınamaz. Silinsin mi?`)) return;
    remove.disabled = true;
    try {
      await request("/api/archive/delete", { method: "POST", body: JSON.stringify({ kind, month, revision }) });
      await loadArchive();
      toast("Kayıt silindi.");
    } catch (error) {
      toast(error.message, true);
      await loadArchive();
    }
  }
}

function archivePayloadValues(kind, payload) {
  const values = new Map();
  if (!payload) return values;
  const nameFor = (id) => state.people.find((person) => person.id === Number(id))?.name || `Personel #${id}`;
  if (kind === "schedule") {
    payload.forEach((entry) => Object.entries(entry.days || {}).forEach(([day, shift]) => {
      const label = shift.kind === "work" ? `${shift.start}–${shift.end}` : shift.kind;
      values.set(`${nameFor(entry.employee_id)} · ${entry.section} · ${day}`, label);
    }));
    return values;
  }
  const overrides = payload.overrides || payload;
  Object.entries(overrides).forEach(([day, dayValues]) => {
    if (!dayValues || typeof dayValues !== "object") return;
    Object.entries(dayValues).forEach(([id, code]) => {
      values.set(`${nameFor(id)} · ${day}`, String(code));
    });
  });
  Object.entries(payload.deductions || {}).forEach(([id, amount]) => {
    values.set(`${nameFor(id)} · Kesinti`, `${amount} TL`);
  });
  return values;
}

async function updateArchiveRevisionDiff() {
  const selection = state.archiveRevision;
  const revision = el("#archive-revision-select").value;
  const output = el("#archive-revision-diff");
  const picker = el("#archive-cell-restore");
  picker.hidden = true;
  if (selection) selection.cellRestore = null;
  if (!selection || !revision) {
    output.textContent = "";
    return;
  }
  try {
    const params = new URLSearchParams({
      kind: selection.kind, month: selection.month, revision,
    });
    const comparison = await request(`/api/archive/compare?${params}`);
    if (state.archiveRevision !== selection || el("#archive-revision-select").value !== revision) return;
    if (window.easyUsability && accessCan(`${selection.kind}.write`)) {
      const cells = [];
      if (selection.kind === "schedule") {
        const currentRows = new Map((comparison.current || []).map((row) => [row.row_id, row]));
        for (const previousRow of comparison.previous) {
          const row = currentRows.get(previousRow.row_id);
          if (!row || row.employee_id !== previousRow.employee_id || row.section !== previousRow.section || row.slot !== previousRow.slot) continue;
          const dates = Object.keys({ ...previousRow.days, ...row.days }).sort();
          const name = state.people.find((person) => person.id === row.employee_id)?.name || row.employee_id;
          for (const date of dates) {
            const previous = previousRow.days[date], current = row.days[date];
            if (JSON.stringify(previous) !== JSON.stringify(current)) {
              cells.push({ rowId: row.row_id, date, label: `${name} · ${row.section} · ${date}: ${scheduleConflictValue(current)} → ${scheduleConflictValue(previous)}` });
            }
          }
        }
      } else {
        const oldCodes = comparison.previous?.overrides ?? comparison.previous ?? {};
        const liveCodes = comparison.current?.overrides ?? comparison.current ?? {};
        const removed = new Set(comparison.current?.roster?.removed ?? []);
        for (const date of monthDates(selection.month).map(dateKey)) {
          const oldDay = oldCodes[date] ?? {}, liveDay = liveCodes[date] ?? {};
          for (const employee of Object.keys({ ...oldDay, ...liveDay })) {
            const person = state.people.find((item) => item.id === Number(employee));
            if (!person || removed.has(person.id) || oldDay[employee] === liveDay[employee]) continue;
            cells.push({ employeeId: person.id, date,
              label: `${person.name} · ${date}: ${liveDay[employee] ?? "Otomatik"} → ${oldDay[employee] ?? "Otomatik"}` });
          }
        }
      }
      const select = el("#archive-cell-restore-select");
      const options = document.createDocumentFragment();
      cells.forEach((item, index) => options.append(new Option(item.label, String(index))));
      select.replaceChildren(options);
      selection.cellRestore = { cells, revision, currentRevision: comparison.current_revision };
      picker.hidden = !cells.length;
    }
    const previous = archivePayloadValues(selection.kind, comparison.previous);
    const current = archivePayloadValues(selection.kind, comparison.current);
    const keys = [...new Set([...previous.keys(), ...current.keys()])].sort((a, b) => a.localeCompare(b, "tr"));
    const changes = keys.filter((key) => previous.get(key) !== current.get(key));
    if (!changes.length) {
      output.textContent = "Sürümler arasında kayıt farkı yok.";
      return;
    }
    const maxRows = 100;
    output.textContent = changes.slice(0, maxRows).map((key) =>
      `${key}\nÖnce: ${previous.get(key) ?? "—"}\nŞimdi: ${current.get(key) ?? "—"}`,
    ).join("\n\n") + (changes.length > maxRows ? `\n\n… ${changes.length - maxRows} fark daha` : "");
  } catch (error) {
    output.textContent = error.message;
  }
}

async function restoreArchiveCell() {
  const selection = state.archiveRevision;
  const preview = selection?.cellRestore;
  const select = el("#archive-cell-restore-select");
  const cell = select.value === "" ? null : preview?.cells[Number(select.value)];
  if (!cell || preview.revision !== el("#archive-revision-select").value) return;
  const label = selection.kind === "attendance" ? "puantaj kodu" : "vardiya günü";
  if (!window.confirm(`${cell.label}\n\nYalnız bu ${label} geri alınacak. Devam edilsin mi?`)) return;
  const button = el("#restore-archive-cell");
  button.disabled = true;
  try {
    await request(`/api/${selection.kind}/restore-cell`, { method: "POST", body: JSON.stringify({
      month: selection.month, revision: preview.revision, expected_revision: preview.currentRevision,
      ...(selection.kind === "attendance" ? { employee_id: cell.employeeId } : { row_id: cell.rowId }), date: cell.date,
    }) });
    el("#archive-revision-dialog").close();
    await loadArchive();
    toast(`Seçili ${label} geri alındı. Diğer günler değişmedi.`);
  } catch (error) {
    toast(error.message, true);
  } finally { button.disabled = false; }
}

async function openArchiveRevisions(kind, month) {
  const params = new URLSearchParams({ kind, month });
  try {
    const result = await request(`/api/archive/revisions?${params}`);
    state.archiveRevision = { kind, month, currentRevision: result.current_revision };
    const select = el("#archive-revision-select");
    select.innerHTML = result.revisions.map((item) => {
      const timestamp = new Date(item.recorded_at).toLocaleString("tr-TR");
      return `<option value="${escapeHtml(item.revision)}">${escapeHtml(timestamp)} · ${escapeHtml(item.revision.slice(0, 8))}</option>`;
    }).join("");
    const status = el("#archive-revision-status");
    const restore = el("#restore-archive-revision");
    restore.disabled = result.revisions.length === 0;
    if (!result.revisions.length) {
      status.textContent = "Henüz önceki sürüm yok. Bir sonraki kayıt değişikliğinden sonra burada karşılaştırabilirsiniz.";
      el("#archive-revision-diff").textContent = "";
    } else {
      status.textContent = "Önceki sürüm ile güncel kayıt arasındaki farklar gösterilir.";
      await updateArchiveRevisionDiff();
    }
    el("#archive-revision-dialog").showModal();
  } catch (error) {
    toast(error.message, true);
  }
}

async function restoreArchiveRevision() {
  const selection = state.archiveRevision;
  const revision = el("#archive-revision-select").value;
  if (!selection || !revision) return;
  if (!window.confirm(
    `${selection.month} döneminin tamamı seçilen önceki sürümle değiştirilecek. `
    + "Şu anki sürüm gelecekte karşılaştırmak için saklanacak. Devam edilsin mi?",
  )) return;
  const button = el("#restore-archive-revision");
  button.disabled = true;
  try {
    await request("/api/archive/restore", {
      method: "POST",
      body: JSON.stringify({
        kind: selection.kind, month: selection.month,
        revision, expected_revision: selection.currentRevision,
      }),
    });
    el("#archive-revision-dialog").close();
    await loadArchive();
    toast(`${selection.month} dönemi önceki sürüme döndürüldü.`);
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
}

async function downloadExcel(kind, options = {}) {
  const month = options.month || (kind === "schedule" ? state.scheduleMonth : state.attendanceMonth);
  const dirty = options.month ? false : (kind === "schedule" ? state.scheduleDirty : state.attendanceDirty);
  if (dirty && !window.confirm("Kaydedilmemiş değişiklikler Excel'e aktarılmaz. Kayıtlı verilerle devam edilsin mi?")) return false;
  const week = options.week ?? (kind === "schedule" ? state.weekIndex : 0);
  const employee = options.employee || "";
  const params = new URLSearchParams({ kind, month, week: String(week) });
  if (employee) params.set("employee", employee);
  const output = el(`#${kind}-export-location`);
  try {
    const environment = await request("/api/environment");
    if (environment.desktop) {
      const result = await request("/api/save-excel", {
        method: "POST", body: JSON.stringify({ kind, month, week, employee }),
      });
      if (!result.cancelled) {
        output.textContent = `Kaydedildi: ${result.path}`;
        output.title = result.path;
        toast("Excel seçtiğiniz konuma kaydedildi.");
      }
      return !result.cancelled;
    }
    if (!window.showSaveFilePicker) {
      throw new Error("Bu tarayıcı konum seçimini desteklemiyor. Masaüstü uygulamasını kullanın.");
    }
    let filename = `${kind === "schedule" ? "VARDIYA_LISTESI" : "PUANTAJ"}_${month}${employee ? "_PERSONEL" : ""}.xlsx`;
    try { filename = (await request(`/api/export-name?${params}`)).filename || filename; } catch { /* varsayılan ad */ }
    const handle = await window.showSaveFilePicker({
      suggestedName: filename,
      types: [{ description: "Excel dosyası", accept: { "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"] } }],
    });
    const response = await fetch(`/api/export?${params}`);
    const contentType = response.headers.get("content-type") || "";
    if (!response.ok) {
      const problem = contentType.includes("application/json") ? await response.json() : {};
      throw new Error(problem.error || "Excel çıktısı oluşturulamadı.");
    }
    const writable = await handle.createWritable();
    await writable.write(await response.blob());
    await writable.close();
    output.textContent = `Kaydedildi: ${handle.name}`;
    toast("Excel seçtiğiniz konuma kaydedildi.");
    return true;
  } catch (error) {
    if (error.name === "AbortError") return false;
    output.textContent = `Excel kaydedilemedi: ${error.message}`;
    toast(error.message, true);
    return false;
  }
}

function outputAddress(value, table, simple = false) {
  const match = /^([A-Za-z]+)([1-9]\d*)$/.exec(value.trim());
  if (!match) throw new Error("Hücre adresini A1 biçiminde girin.");
  let col = 0;
  for (const letter of match[1].toUpperCase()) col = col * 26 + letter.charCodeAt(0) - 64;
  const row = Number(match[2]);
  const maxCol = table.tHead.rows[0].cells.length - (simple ? 0 : 1);
  const maxRow = table.tBodies[0].rows.length + (simple ? 1 : 0);
  if (col < 1 || col > maxCol || row > maxRow) {
    throw new Error("Hücre adresi tablonun dışında.");
  }
  return { row, col };
}

function outputCellText(cell) {
  const clone = cell.cloneNode(true);
  const originalControls = cell.querySelectorAll("input, select");
  clone.querySelectorAll("input, select").forEach((control, index) => {
    const source = originalControls[index];
    const value = source.tagName === "SELECT"
      ? (source.value ? source.selectedOptions[0]?.textContent : "")
      : source.value;
    control.replaceWith(document.createTextNode(value || ""));
  });
  clone.querySelectorAll("button").forEach((button) => button.remove());
  if (clone.querySelector(".shift-slot-label")?.textContent === "Önce personel seçin") return "";
  return clone.textContent.trim();
}

function scheduleOutputPeopleCounts() {
  const week = weekStarts(state.scheduleMonth)[state.weekIndex] || weekStarts(state.scheduleMonth)[0];
  const weekDays = Array.from({ length: 7 }, (_, index) =>
    dateKey(localDate(week.getFullYear(), week.getMonth(), week.getDate() + index)));
  const allPeople = new Set(state.schedule.entries.map((entry) => entry.employee_id));
  const visiblePeople = new Set(state.schedule.entries
    .filter((entry) => scheduleEntryMatchesFilters(entry, weekDays))
    .map((entry) => entry.employee_id));
  return { visible: visiblePeople.size, all: allPeople.size };
}

function withUnfilteredScheduleOutput(render) {
  const filters = state.scheduleFilters;
  state.scheduleFilters = { ...filters, query: "", section: "", kind: "" };
  try {
    renderSchedule();
    return render();
  } finally {
    state.scheduleFilters = filters;
    renderSchedule();
  }
}

function outputTableImage(kind, first, last) {
  const table = el(`#${kind}-table`);
  const start = outputAddress(first, table);
  const end = outputAddress(last, table);
  if (start.row > end.row || start.col > end.col) throw new Error("İlk hücre son hücreden önce olmalıdır.");
  const headers = table.tHead.rows[0].cells;
  const rows = [...table.tBodies[0].rows].slice(start.row - 1, end.row).filter((row) => row.getClientRects().length);
  if (!rows.length) throw new Error("Seçilen aralıkta görünür satır yok.");
  const left = headers[start.col].getBoundingClientRect().left;
  const right = headers[end.col].getBoundingClientRect().right;
  const top = rows[0].getBoundingClientRect().top;
  const bottom = rows[rows.length - 1].getBoundingClientRect().bottom;
  const width = right - left;
  const height = bottom - top;
  if (width <= 0 || height <= 0) throw new Error("Seçilen aralıkta görünür hücre yok.");
  const zoom = Number(table.style.zoom) || 1;
  const scale = Math.min(2 / zoom, 16000 / width, 16000 / height, Math.sqrt(30_000_000 / (width * height)));
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(width * scale);
  canvas.height = Math.ceil(height * scale);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Tablo görüntüsü oluşturulamadı.");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.scale(scale, scale);
  for (const cell of table.querySelectorAll("td[data-cell-row]")) {
    const row = Number(cell.dataset.cellRow);
    const col = Number(cell.dataset.cellCol);
    if (row > end.row || row + cell.rowSpan - 1 < start.row
      || col > end.col || col + cell.colSpan - 1 < start.col || !cell.getClientRects().length) continue;
    const rect = cell.getBoundingClientRect();
    const x = rect.left - left, y = rect.top - top, w = rect.width, h = rect.height;
    const style = getComputedStyle(cell);
    ctx.fillStyle = style.backgroundColor;
    ctx.fillRect(x, y, w, h);
    for (const [side, x1, y1, x2, y2] of [
      ["Top", x, y, x + w, y], ["Right", x + w, y, x + w, y + h],
      ["Bottom", x, y + h, x + w, y + h], ["Left", x, y, x, y + h],
    ]) {
      const lineWidth = parseFloat(style[`border${side}Width`]) * zoom;
      if (lineWidth > 0 && style[`border${side}Style`] !== "none") {
        ctx.strokeStyle = style[`border${side}Color`];
        ctx.lineWidth = lineWidth;
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      }
    }
    const text = outputCellText(cell);
    if (!text) continue;
    ctx.save();
    ctx.beginPath(); ctx.rect(x + 1, y + 1, Math.max(0, w - 2), Math.max(0, h - 2)); ctx.clip();
    const fontSize = (parseFloat(style.fontSize) || 12) * zoom;
    ctx.font = `${style.fontStyle} ${style.fontWeight} ${fontSize}px ${style.fontFamily}`;
    ctx.fillStyle = style.color;
    ctx.textBaseline = "middle";
    if (cell.querySelector(".rotated-cell-value")) {
      ctx.translate(x + w / 2, y + h / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.textAlign = "center";
      ctx.fillText(text, 0, 0);
    } else {
      const align = style.textAlign === "left" || style.textAlign === "right" ? style.textAlign : "center";
      ctx.textAlign = align;
      const textX = align === "left" ? x + 3 : align === "right" ? x + w - 3 : x + w / 2;
      const lines = [];
      for (const part of text.split(/\r?\n/)) {
        if (style.whiteSpace === "nowrap") { lines.push(part); continue; }
        let line = "";
        for (const word of part.split(/\s+/)) {
          const next = line ? `${line} ${word}` : word;
          if (line && ctx.measureText(next).width > w - 4) { lines.push(line); line = word; }
          else line = next;
        }
        lines.push(line);
      }
      const lineHeight = fontSize * 1.15;
      const startY = style.verticalAlign === "top" ? y + lineHeight / 2
        : style.verticalAlign === "bottom" ? y + h - lineHeight * (lines.length - 0.5)
        : y + h / 2 - lineHeight * (lines.length - 1) / 2;
      lines.forEach((line, index) => ctx.fillText(line, textX, startY + index * lineHeight));
    }
    ctx.restore();
  }
  const data = canvas.toDataURL("image/jpeg", 0.95);
  if (!data.startsWith("data:image/jpeg;base64,")) throw new Error("JPEG oluşturulamadı; daha küçük bir alan seçin.");
  return {
    data,
    width: canvas.width,
    height: canvas.height,
    rowHeights: rows.map((row) => row.getBoundingClientRect().height * scale),
  };
}

function outputSimpleTableImage(table, first, last, kind) {
  const start = outputAddress(first, table, true);
  const end = outputAddress(last, table, true);
  if (start.row > end.row || start.col > end.col) throw new Error("İlk hücre son hücreden önce olmalıdır.");
  const rows = [table.tHead.rows[0], ...table.tBodies[0].rows]
    .slice(start.row - 1, end.row).filter((row) => row.getClientRects().length);
  if (!rows.length) throw new Error("Seçilen aralıkta görünür satır yok.");
  const widths = [...table.tHead.rows[0].cells].slice(start.col - 1, end.col)
    .map((cell) => Math.max(100, cell.getBoundingClientRect().width));
  const width = widths.reduce((a, b) => a + b, 0);
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Tablo görüntüsü oluşturulamadı.");
  ctx.font = "14px sans-serif";
  const cells = rows.map((row) => widths.map((cellWidth, index) => {
    const cell = row.cells[start.col - 1 + index];
    let text = cell ? outputCellText(cell) : "";
    if (kind === "archive" && start.col + index === 2 && row.parentElement === table.tBodies[0] && !text) text = "Kayıtlı";
    const lines = [];
    for (const paragraph of text.split(/\r?\n/)) {
      let line = "";
      for (const word of paragraph.split(/\s+/)) {
        const next = line ? `${line} ${word}` : word;
        if (line && ctx.measureText(next).width > cellWidth - 16) { lines.push(line); line = word; }
        else line = next;
        if (ctx.measureText(line).width > cellWidth - 16) {
          let fragment = "";
          for (const character of line) {
            if (fragment && ctx.measureText(fragment + character).width > cellWidth - 16) {
              lines.push(fragment); fragment = character;
            } else fragment += character;
          }
          line = fragment;
        }
      }
      lines.push(line);
    }
    return lines;
  }));
  const heights = cells.map((row) => Math.max(34, ...row.map((lines) => lines.length * 19 + 12)));
  const height = heights.reduce((a, b) => a + b, 0);
  const scale = Math.min(2, 16000 / width, 16000 / height, Math.sqrt(30_000_000 / (width * height)));
  canvas.width = Math.ceil(width * scale);
  canvas.height = Math.ceil(height * scale);
  ctx.scale(scale, scale);
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, width, height);
  ctx.font = "14px sans-serif"; ctx.textBaseline = "middle";
  let y = 0;
  rows.forEach((row, rowIndex) => {
    let x = 0;
    widths.forEach((cellWidth, colIndex) => {
      const cell = row.cells[start.col - 1 + colIndex];
      if (cell?.tagName === "TH") { ctx.fillStyle = "#e1eaee"; ctx.fillRect(x, y, cellWidth, heights[rowIndex]); }
      ctx.strokeStyle = "#b8c8c0"; ctx.lineWidth = 1;
      ctx.strokeRect(x, y, cellWidth, heights[rowIndex]);
      ctx.save(); ctx.beginPath(); ctx.rect(x + 1, y + 1, cellWidth - 2, heights[rowIndex] - 2); ctx.clip();
      ctx.fillStyle = "#172b24";
      cells[rowIndex][colIndex].forEach((line, lineIndex) => ctx.fillText(line, x + 8, y + 16 + lineIndex * 19));
      ctx.restore();
      x += cellWidth;
    });
    y += heights[rowIndex];
  });
  const data = canvas.toDataURL("image/jpeg", 0.95);
  if (!data.startsWith("data:image/jpeg;base64,")) throw new Error("JPEG oluşturulamadı; daha küçük bir alan seçin.");
  return {
    data,
    width: canvas.width,
    height: canvas.height,
    rowHeights: heights.map((rowHeight) => rowHeight * scale),
  };
}

async function paginateOutputImage(output) {
  const image = new Image();
  image.src = output.data;
  await image.decode();
  const maxPageHeight = Math.floor(image.naturalWidth * 194 / 281);
  const groups = [];
  let group = [];
  let groupHeight = 0;
  output.rowHeights.forEach((rowHeight, index) => {
    if (group.length && groupHeight + rowHeight > maxPageHeight) {
      groups.push(group);
      group = [];
      groupHeight = 0;
    }
    group.push(index);
    groupHeight += rowHeight;
  });
  if (group.length) groups.push(group);
  const boundaries = [0];
  output.rowHeights.forEach((rowHeight) => boundaries.push(Math.round(boundaries.at(-1) + rowHeight)));
  const overflowRows = [];
  const pages = groups.map((indices, pageIndex) => {
    const first = indices[0];
    const last = indices[indices.length - 1];
    const top = boundaries[first];
    const height = Math.min(image.naturalHeight - top, boundaries[last + 1] - top);
    if (indices.some((index) => output.rowHeights[index] > maxPageHeight)) overflowRows.push(pageIndex + 1);
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = Math.max(1, height);
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Sayfa önizlemesi oluşturulamadı.");
    context.fillStyle = "#fff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, top, image.naturalWidth, height, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.95);
  });
  return { pages, overflowRows };
}

function registerOutputEvents() {
  let kind = "schedule";
  document.querySelectorAll("[data-output-kind]").forEach((button) => button.addEventListener("click", () => {
    kind = button.dataset.outputKind;
    const simple = kind === "archive" || kind === "import";
    const table = simple ? el(kind === "archive" ? "#archive-list table" : "#import-preview table") : el(`#${kind}-table`);
    if (!table) { toast("Önce çıktısı alınacak bir tablo oluşturun.", true); return; }
    const selected = !simple && state.selectedCell?.key === sheetKey(kind) ? state.selectedCell : null;
    el("#output-title").textContent = ({ schedule: "Vardiya çıktısı", attendance: "Puantaj çıktısı", archive: "Arşiv çıktısı", import: "İçe aktarma önizlemesi" })[kind];
    el("#output-start").value = selected
      ? `${excelColumnName(Math.min(selected.col, selected.endCol))}${Math.min(selected.row, selected.endRow)}` : "A1";
    el("#output-end").value = selected
      ? `${excelColumnName(Math.max(selected.col, selected.endCol))}${Math.max(selected.row, selected.endRow)}`
      : `${excelColumnName(table.tHead.rows[0].cells.length - (simple ? 0 : 1))}${table.tBodies[0].rows.length + (simple ? 1 : 0)}`;
    const printArea = !simple && !selected && sheetSettings(kind).page_setup?.print_area;
    if (printArea) {
      const [start, end] = printArea.split(":");
      el("#output-start").value = start;
      el("#output-end").value = end || start;
    }
    el("#output-message").textContent = "";
    el("#output-preview").replaceChildren();
    el("#output-preview").hidden = true;
    const scopeField = el("#output-scope-field");
    const scope = el("#output-scope");
    scopeField.hidden = kind !== "schedule";
    if (kind === "schedule") {
      const counts = scheduleOutputPeopleCounts();
      scope.innerHTML = `<option value="visible">Görünen ${counts.visible} personel (filtreler uygulanır)</option>
        <option value="all">Tüm ${counts.all} personel (filtrelerden bağımsız)</option>`;
      scope.value = "visible";
    }
    el("#output-dialog").showModal();
  }));
  const selectedOutput = () => {
    const simple = kind === "archive" || kind === "import";
    const table = simple && el(kind === "archive" ? "#archive-list table" : "#import-preview table");
    if (simple && !table) throw new Error("Çıktısı alınacak tablo artık mevcut değil.");
    if (simple) return outputSimpleTableImage(table, el("#output-start").value, el("#output-end").value, kind);
    const render = () => outputTableImage(kind, el("#output-start").value, el("#output-end").value);
    return kind === "schedule" && el("#output-scope").value === "all"
      ? withUnfilteredScheduleOutput(render)
      : render();
  };
  el("#output-preview-button").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    const message = el("#output-message");
    try {
      message.textContent = "Sayfa kırılımları hesaplanıyor…";
      const { pages, overflowRows } = await paginateOutputImage(selectedOutput());
      const preview = el("#output-preview");
      preview.replaceChildren(...pages.map((src, index) => {
        const figure = document.createElement("figure");
        figure.className = "output-page-preview";
        const image = document.createElement("img");
        image.src = src;
        image.alt = `Yazdırma sayfası ${index + 1}`;
        const caption = document.createElement("figcaption");
        caption.textContent = `Sayfa ${index + 1} / ${pages.length}${overflowRows.includes(index + 1) ? " · Satır sayfaya sığmıyor" : ""}`;
        figure.append(image, caption);
        return figure;
      }));
      preview.hidden = false;
      message.textContent = overflowRows.length
        ? `${pages.length} sayfa. ${overflowRows.length} sayfada en az bir satır yazdırılabilir alandan uzun; yazdırırken küçültülür.`
        : `${pages.length} sayfa. Kırılımlar satır sınırlarında gösteriliyor.`;
    } catch (error) {
      message.textContent = error.message;
    } finally { button.disabled = false; }
  });
  for (const action of ["jpeg", "print"]) {
    el(`#output-${action}`).addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      const message = el("#output-message");
      try {
        message.textContent = "Tablo görüntüsü hazırlanıyor…";
        const output = window.easyAssistant
          ? await window.easyAssistant.trackViewOutput(selectedOutput(), kind)
          : selectedOutput();
        if (action === "print") {
          const { pages, overflowRows } = await paginateOutputImage(output);
          const printPages = el("#print-pages");
          printPages.replaceChildren(...pages.map((src) => {
            const image = document.createElement("img");
            image.className = "print-output-page";
            image.alt = "Yazdırılacak tablo";
            image.src = src;
            return image;
          }));
          await Promise.all([...printPages.querySelectorAll("img")].map((image) => image.decode()));
          message.textContent = "Yazdırma penceresinde yazıcıyı ve sayfa ayarlarını seçin.";
          if (overflowRows.length) {
            message.textContent += " Sığmayan satırlar yazdırma sayfasında küçültülecek.";
          }
          window.print();
        } else {
          const data = output.data;
          const environment = await request("/api/environment");
          let location;
          if (environment.desktop) {
            const result = await request("/api/save-jpeg", {
              method: "POST", body: JSON.stringify({ content: data.split(",")[1] }),
            });
            if (result.cancelled) { message.textContent = "Kaydetme iptal edildi."; return; }
            location = result.path;
          } else {
            if (!window.showSaveFilePicker) throw new Error("Bu tarayıcı konum seçimini desteklemiyor.");
            const handle = await window.showSaveFilePicker({
              suggestedName: "TABLO.jpg", types: [{ description: "JPEG", accept: { "image/jpeg": [".jpg"] } }],
            });
            const writable = await handle.createWritable();
            await writable.write(await (await fetch(data)).blob());
            await writable.close();
            location = handle.name;
          }
          message.textContent = `Kaydedildi: ${location}`;
        }
      } catch (error) {
        if (error.name !== "AbortError") message.textContent = error.message;
      } finally { button.disabled = false; }
    });
  }
}

function readableColor(color) {
  const rgb = color.slice(1).match(/.{2}/g).map((part) => parseInt(part, 16) / 255)
    .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722 > 0.179 ? "#000000" : "#ffffff";
}

function noteStart(kind) { return kind === "schedule" ? 34 : 25; }

function sheetIndex(kind) { return kind === "schedule" ? state.weekIndex : 0; }

function templateSheetKey(kind) { return `${kind}:template:${sheetIndex(kind)}`; }

function pageSheetKey(kind) {
  return `${kind}:${kind === "schedule" ? state.scheduleMonth : state.attendanceMonth}:${sheetIndex(kind)}`;
}

function sheetKey(kind) {
  return state.templateEdit === kind ? templateSheetKey(kind) : pageSheetKey(kind);
}

function sheetSettings(kind) {
  return state.workbookSettings.sheets[sheetKey(kind)]
    || state.workbookSettings.sheets[templateSheetKey(kind)]
    || { cells: {}, hide_notes: false, zoom: kind === "schedule" ? 55 : 100 };
}

function shiftDimensionSettings(sheet, field, position, delta) {
  const dimensions = sheet[field];
  if (!dimensions) return;
  const moved = {};
  for (const [key, value] of Object.entries(dimensions)) {
    let index = Number(key);
    if (delta < 0 && index === position) continue;
    if (delta > 0 ? index >= position : index > position) index += delta;
    moved[String(index)] = value;
  }
  sheet[field] = moved;
}

function moveCellSettingsForAxis(sheet, axis, position) {
  const moved = {};
  for (const [address, value] of Object.entries(sheet.cells || {})) {
    const coord = address.split(":").map(Number);
    if (coord[axis] === position) continue;
    if (coord[axis] > position) coord[axis] -= 1;
    moved[coord.join(":")] = value;
  }
  sheet.cells = moved;
}

function removeInsertionOperation(positions, operationIndex, baseLength) {
  const originalLayout = Array.from({ length: baseLength }, (_, index) => `base-${index}`);
  positions.forEach((position, index) => {
    originalLayout.splice(position - 1, 0, `insert-${index}`);
  });
  const desiredLayout = originalLayout.filter((item) => item !== `insert-${operationIndex}`);
  const layout = Array.from({ length: baseLength }, (_, index) => `base-${index}`);
  const nextPositions = [];
  positions.forEach((_, index) => {
    if (index === operationIndex) return;
    const token = `insert-${index}`;
    const desiredIndex = desiredLayout.indexOf(token);
    let position = layout.length + 1;
    for (let cursor = desiredIndex - 1; cursor >= 0; cursor -= 1) {
      const previous = layout.indexOf(desiredLayout[cursor]);
      if (previous >= 0) {
        position = previous + 2;
        break;
      }
    }
    if (position === layout.length + 1) {
      for (let cursor = desiredIndex + 1; cursor < desiredLayout.length; cursor += 1) {
        const next = layout.indexOf(desiredLayout[cursor]);
        if (next >= 0) {
          position = next + 1;
          break;
        }
      }
    }
    nextPositions.push(position);
    layout.splice(position - 1, 0, token);
  });
  return nextPositions;
}

function formatHistoryFor(key) {
  if (!state.workbookHistory[key]) state.workbookHistory[key] = { undo: [], redo: [] };
  return state.workbookHistory[key];
}

function describeWorkbookHistoryOperation(before, after) {
  const previous = before || {};
  const current = after || {};
  for (const [field, label] of [
    ["delete_rows", "satır silme"],
    ["delete_cols", "sütun silme"],
    ["insert_rows", "satır ekleme"],
    ["insert_cols", "sütun ekleme"],
  ]) {
    if (JSON.stringify(previous[field] || []) !== JSON.stringify(current[field] || [])) return label;
  }
  return "biçim değişikliği";
}

async function applyWorkbookHistory(kind, direction) {
  const key = sheetKey(kind);
  const history = formatHistoryFor(key);
  const source = history[direction];
  if (!source.length || state.formattingBusy) return;
  const destination = history[direction === "undo" ? "redo" : "undo"];
  const current = state.workbookSettings.sheets[key] ?? null;
  const target = source[source.length - 1];
  const settings = structuredClone(state.workbookSettings);
  if (target === null) delete settings.sheets[key];
  else settings.sheets[key] = structuredClone(target);
  if (await persistWorkbookSettings(settings, { recordHistory: false })) {
    source.pop();
    destination.push(current === null ? null : structuredClone(current));
    if (destination.length > 50) destination.shift();
    updateFormatToolbar(kind);
  }
}

async function persistWorkbookSettings(settings, options = {}) {
  if (state.formattingBusy) {
    toast("Önceki biçim değişikliği kaydediliyor; tekrar deneyin.", true);
    return false;
  }
  const kind = state.view === "schedule" || state.view === "attendance" ? state.view : null;
  const key = kind ? sheetKey(kind) : null;
  const previous = key ? state.workbookSettings.sheets[key] ?? null : null;
  if (key?.includes(":template:")) {
    if (typeof outputSheetDiff !== "function") {
      toast("Şablon değişiklik özeti yüklenemedi; değişiklik uygulanmadı.", true);
      return false;
    }
    const changes = outputSheetDiff(previous || {}, settings.sheets[key] || {});
    if (changes.length && !window.confirm(
      `Şablon değişiklikleri uygulanmadan önce inceleyin:\n\n${changes.slice(0, 20).join("\n")}`
      + (changes.length > 20 ? `\n… ve ${changes.length - 20} değişiklik daha` : "")
      + "\n\nBu düzeni şablona kaydetmek istiyor musunuz?",
    )) return false;
  }
  if (key && settings.sheets[key]) fitGhostExtents(kind, settings.sheets[key], previous);
  state.formattingBusy = true;
  document.querySelectorAll(".workbook-tools button, [data-preset-color]").forEach((control) => { control.disabled = true; });
  try {
    state.workbookSettings = await request("/api/workbook-settings", {
      method: "PUT", body: JSON.stringify(settings),
    });
    const current = key ? state.workbookSettings.sheets[key] ?? null : null;
    if (options.recordHistory !== false && key
      && JSON.stringify(previous) !== JSON.stringify(current)) {
      const history = formatHistoryFor(key);
      history.undo.push(previous === null ? null : structuredClone(previous));
      if (history.undo.length > 50) history.undo.shift();
      history.redo = [];
    }
    toast("Tablo ayarları kaydedildi.");
    return true;
  } catch (error) {
    toast(error.message, true);
    return false;
  } finally {
    state.formattingBusy = false;
    document.querySelectorAll("[data-preset-color]").forEach((control) => { control.disabled = false; });
    renderSchedule();
    renderAttendance();
  }
}

function updateFormatToolbar(kind) {
  const toolbar = el(`#${kind}-format-tools`);
  if (!toolbar) return;
  const settings = sheetSettings(kind);
  const selected = state.selectedCell?.key === sheetKey(kind) ? state.selectedCell : null;
  const template = state.templates[kind][kind === "schedule" ? (state.weekIndex >= 5 ? 1 : state.weekIndex) : 0];
  const selectedAnchor = selected && el(`#${kind}-table`).querySelector(
    `td[data-cell-row="${selected.row}"][data-cell-col="${selected.col}"]`,
  );
  const editableText = selectedAnchor && Number(selectedAnchor.dataset.sourceRow) >= 0 && (
    Number(selectedAnchor.dataset.sourceRow) === 0 || Number(selectedAnchor.dataset.sourceCol) === 0
    || Number(selectedAnchor.dataset.sourceRow) >= noteStart(kind)
    || Number(selectedAnchor.dataset.sourceCol) > (template?.columns.length || 0));
  toolbar.querySelector("[data-format-selection]").textContent = selected
    ? `${excelColumnName(selected.col)}${selected.row}${selected.endRow !== selected.row || selected.endCol !== selected.col ? `:${excelColumnName(selected.endCol)}${selected.endRow}` : ""}` : "Hücre seçin";
  toolbar.querySelector("[data-format-zoom]").value = settings.zoom;
  toolbar.querySelector("[data-format-notes]").checked = !settings.hide_notes;
  toolbar.querySelector("[data-format-mode]").checked = state.formatMode;
  toolbar.querySelectorAll("button[data-format-add]").forEach((control) => {
    control.disabled = state.formattingBusy;
  });
  toolbar.querySelectorAll("button[data-format-remove]").forEach((control) => {
    control.disabled = state.formattingBusy || !(settings[control.dataset.formatRemove] || 0)
      || (settings.insert_rows || []).length > 0 || (settings.insert_cols || []).length > 0;
  });
  toolbar.querySelectorAll("button[data-format-insert]").forEach((control) => {
    control.disabled = state.formattingBusy || !selected
      || (control.dataset.formatInsert === "insert_rows" && selectedAnchor
        && Number(selectedAnchor.dataset.sourceRow) < 0);
  });
  toolbar.querySelectorAll("button[data-format-undo-insert]").forEach((control) => {
    control.disabled = state.formattingBusy || !(settings[control.dataset.formatUndoInsert] || []).length;
  });
  const table = el(`#${kind}-table`);
  const rowCount = table.querySelectorAll("tbody > tr").length;
  const columnCount = table.querySelectorAll("thead [data-format-column]").length;
  const minRow = selected ? Math.min(selected.row, selected.endRow) : 0;
  const maxRow = selected ? Math.max(selected.row, selected.endRow) : 0;
  const minCol = selected ? Math.min(selected.col, selected.endCol) : 0;
  const maxCol = selected ? Math.max(selected.col, selected.endCol) : 0;
  toolbar.querySelectorAll("[data-format-delete]").forEach((control) => {
    const wholeAxis = control.dataset.formatDelete === "row"
      ? minCol === 1 && maxCol === columnCount && maxRow - minRow + 1 === 1
      : minRow === 1 && maxRow === rowCount && maxCol - minCol + 1 === 1;
    control.disabled = state.formattingBusy || !selected || !wholeAxis;
  });
  const history = formatHistoryFor(sheetKey(kind));
  toolbar.querySelectorAll("[data-format-history]").forEach((control) => {
    const direction = control.dataset.formatHistory;
    const entries = history[direction];
    control.disabled = state.formattingBusy || !entries.length;
    if (entries.length) {
      const current = settings;
      const target = entries[entries.length - 1];
      const description = direction === "undo"
        ? describeWorkbookHistoryOperation(target, current)
        : describeWorkbookHistoryOperation(current, target);
      control.title = `${direction === "undo" ? "Geri al" : "Yinele"}: ${description}`;
      control.setAttribute("aria-label", control.title);
    } else {
      control.title = direction === "undo" ? "Geri alınacak biçim değişikliği yok." : "Yinelenecek biçim değişikliği yok.";
      control.setAttribute("aria-label", direction === "undo" ? "Biçim değişikliğini geri al" : "Biçim değişikliğini yinele");
    }
  });
  toolbar.querySelectorAll("button[data-format-action]").forEach((control) => {
    control.disabled = state.formattingBusy || !selected
      || (control.dataset.formatAction === "text" && (!editableText || !selectedAnchor))
      || (control.dataset.formatAction === "clear" && !editableText)
      || (control.dataset.formatAction === "clear-shifts" && kind !== "schedule");
  });
  const note = toolbar.querySelector("[data-format-text]");
  note.disabled = !editableText || !selectedAnchor;
  if (selected) {
    const value = settings.cells[`${selected.row}:${selected.col}`];
    note.value = value && Object.prototype.hasOwnProperty.call(value, "text")
      ? value.text : selectedAnchor?.textContent || "";
    table.querySelectorAll(".format-selected").forEach((cell) => cell.classList.remove("format-selected"));
    const minRow = Math.min(selected.row, selected.endRow);
    const maxRow = Math.max(selected.row, selected.endRow);
    const minCol = Math.min(selected.col, selected.endCol);
    const maxCol = Math.max(selected.col, selected.endCol);
    table.querySelectorAll("td[data-cell-row]").forEach((cell) => {
      const row = Number(cell.dataset.cellRow);
      const col = Number(cell.dataset.cellCol);
      if (row <= maxRow && row + cell.rowSpan - 1 >= minRow
        && col <= maxCol && col + cell.colSpan - 1 >= minCol) cell.classList.add("format-selected");
    });
  } else note.value = "";
  el(`#${kind}-view`).classList.toggle("formatting-mode", state.formatMode);
  const editingTemplate = state.templateEdit === kind;
  const ownPage = Boolean(state.workbookSettings.sheets[pageSheetKey(kind)]);
  el(`#${kind}-view .template-edit-banner`).hidden = !editingTemplate;
  el(`#${kind}-view`).classList.toggle("template-editing", editingTemplate);
  const promote = toolbar.querySelector('[data-template-page="promote"]');
  const revert = toolbar.querySelector('[data-template-page="revert"]');
  promote.hidden = editingTemplate;
  revert.hidden = editingTemplate;
  promote.disabled = state.formattingBusy || !ownPage;
  revert.disabled = state.formattingBusy || !ownPage;
  const scaleLabel = el(`#${kind}-view .workbook-status`).children[1];
  scaleLabel.textContent = `Görünüm ölçeği: %${settings.zoom}`;
  el(`#${kind}-view`).querySelectorAll(".table-footer").forEach((footer) => { footer.hidden = settings.hide_notes; });
  if (typeof updateExtraFormatTools === "function") updateExtraFormatTools(kind, toolbar);
}

function installFormatToolbars() {
  for (const kind of ["schedule", "attendance"]) {
    const toolbar = document.createElement("div");
    toolbar.id = `${kind}-format-tools`;
    toolbar.className = "workbook-tools";
    toolbar.innerHTML = `<label><input type="checkbox" data-format-mode> Biçimlendirme modu</label>
      <output data-format-selection>Hücre seçin</output>
      <label>Dolgu <input type="color" data-format-color value="#fff2cc" aria-label="Hücre dolgu rengi"></label>
      <button class="button button-light" type="button" data-format-action="color" disabled>Rengi uygula</button>
      <label>Yazı <select data-format-font-name aria-label="Yazı tipi">${FORMAT_FONT_NAMES.map((name) => `<option>${name}</option>`).join("")}</select></label>
      <label>Boyut <input type="number" min="6" max="72" value="11" data-format-font-size aria-label="Yazı boyutu"></label>
      <label>Yazı rengi <input type="color" data-format-font-color value="#000000" aria-label="Yazı rengi"></label>
      <label><input type="checkbox" data-format-bold> Kalın</label>
      <label><input type="checkbox" data-format-italic> İtalik</label>
      <label><input type="checkbox" data-format-underline> Altı çizili</label>
      <label><input type="checkbox" data-format-strike> Üstü çizili</label>
      <label>Metin yönü <select data-format-rotation aria-label="Metin yönü"><option value="">Değiştirme</option><option value="0">Yatay</option><option value="45">45° yukarı</option><option value="90">Dikey yukarı</option><option value="135">45° aşağı</option><option value="180">Dikey aşağı</option><option value="255">Harfler alt alta</option></select></label>
      <button class="button button-light" type="button" data-format-action="font" disabled>Yazıyı uygula</button>
      <label>Yatay <select data-format-horizontal aria-label="Yatay hizalama"><option value="left">Sol</option><option value="center" selected>Orta</option><option value="right">Sağ</option></select></label>
      <label>Dikey <select data-format-vertical aria-label="Dikey hizalama"><option value="top">Üst</option><option value="center" selected>Orta</option><option value="bottom">Alt</option></select></label>
      <label>Metin <select data-format-fit aria-label="Metin sığdırma"><option value="normal">Normal</option><option value="shrink">Hücreye sığdır</option><option value="wrap">Metni kaydır</option></select></label>
      <button class="button button-light" type="button" data-format-action="alignment" disabled>Hizalamayı uygula</button>
      <label>Sütun genişliği <input type="number" min="1" max="255" step="0.5" value="12" data-format-width aria-label="Sütun genişliği"></label>
      <button class="button button-light" type="button" data-format-action="width" disabled>Genişliği uygula</button>
      <label>Satır yüksekliği <input type="number" min="1" max="409" step="0.5" value="22" data-format-height aria-label="Satır yüksekliği"></label>
      <button class="button button-light" type="button" data-format-action="height" disabled>Yüksekliği uygula</button>
      ${kind === "schedule" ? '<button class="button button-light" type="button" data-format-action="clear-shifts" disabled>Seçili vardiyaları temizle</button>' : ""}
      <button class="button button-light" type="button" data-format-add="extra_rows">Alta boş satır ekle</button>
      <button class="button button-light" type="button" data-format-add="extra_cols">Sağa boş sütun ekle</button>
      <button class="button button-light" type="button" data-format-insert="insert_rows" disabled>Seçimin önüne satır ekle</button>
      <button class="button button-light" type="button" data-format-insert="insert_cols" disabled>Seçimin önüne sütun ekle</button>
      <button class="button button-light" type="button" data-format-undo-insert="insert_rows" disabled>Eklenen son satırı geri al</button>
      <button class="button button-light" type="button" data-format-undo-insert="insert_cols" disabled>Eklenen son sütunu geri al</button>
      <button class="button button-light" type="button" data-format-delete="row" disabled>Seçili satırı sil</button>
      <button class="button button-light" type="button" data-format-delete="column" disabled>Seçili sütunu sil</button>
      <button class="button button-light" type="button" data-format-history="undo" disabled>Geri al</button>
      <button class="button button-light" type="button" data-format-history="redo" disabled>Yinele</button>
      <button class="button button-light" type="button" data-format-remove="extra_rows">Son ek satırı kaldır</button>
      <button class="button button-light" type="button" data-format-remove="extra_cols">Son ek sütunu kaldır</button>
      <label>Ölçek <input type="number" min="25" max="150" step="5" data-format-zoom aria-label="Tablo görünüm ölçeği"> %</label>
      <label><input type="checkbox" data-format-notes checked> Alt notları göster</label>
      <label class="note-text-field">Hücre metni <input type="text" maxlength="2000" data-format-text placeholder="Not veya ek hücre seçin" disabled></label>
      <button class="button button-light" type="button" data-format-action="text" disabled>Notu güncelle</button>
      <button class="button button-light" type="button" data-format-action="clear" disabled>Seçili notları temizle</button>
      <button class="button button-light" type="button" data-template-page="promote">Bu sayfayı şablon yap</button>
      <button class="button button-light" type="button" data-template-page="revert">Bu sayfayı şablona döndür</button>
      <span class="format-help">Genişlik seçili sütunların tamamını, yükseklik seçili satırların tamamını değiştirir. Birleşik gün hücresinde giriş ve çıkış sütunları birlikte seçilir. Genişlik Excel sütun birimi, yükseklik punto cinsindendir. Eklenen hücreler personel kapasitesini artırmaz.</span>`;
    const panel = document.createElement("details");
    panel.className = "format-panel";
    const summary = document.createElement("summary");
    summary.textContent = "Biçimlendirme";
    summary.title = "Tablo biçimlendirme araçları";
    panel.append(summary, toolbar);
    const frame = el(`#${kind}-view .table-frame`);
    const banner = document.createElement("div");
    banner.className = "template-edit-banner";
    banner.hidden = true;
    banner.setAttribute("role", "status");
    banner.innerHTML = `<strong>Şablon düzenleniyor</strong>
      <span>Yapılan biçim değişiklikleri kendi biçimi olmayan tüm ${kind === "schedule" ? "ay ve haftalara" : "aylara"} uygulanır.${kind === "schedule" ? " Her hafta sekmesinin şablonu ayrıdır." : ""}</span>
      <span class="template-guide-legend" aria-label="Şablon alan sınırları">
        <span><i class="template-guide-key-header"></i>Başlık</span>
        <span><i class="template-guide-key-person"></i>Personel alanı</span>
        <span><i class="template-guide-key-signature"></i>İmza satırı</span>
      </span>
      <button class="button button-light" type="button" data-template-edit-done>Şablon düzenlemeyi bitir</button>`;
    banner.querySelector("[data-template-edit-done]").addEventListener("click", () => {
      state.templateEdit = null;
      state.selectedCell = null;
      if (kind === "schedule") renderSchedule(); else renderAttendance();
      toast("Şablon düzenleme bitti; artık bu sayfanın kendi biçimini düzenliyorsunuz.");
    });
    frame.before(banner);
    frame.before(panel);
    toolbar.addEventListener("change", async (event) => {
      if (event.target.matches("[data-format-mode]")) {
        state.formatMode = event.target.checked;
        document.querySelectorAll(".format-panel summary").forEach((item) => {
          item.textContent = state.formatMode
            ? "Biçimlendirme · Açık"
            : "Biçimlendirme";
        });
        if (state.formatMode && kind === "attendance") {
          state.attendanceQuery = "";
          state.attendanceFilter = "all";
          el("#attendance-search").value = "";
          el("#attendance-filter").value = "all";
        }
        state.selectedCell = null;
        renderSchedule();
        renderAttendance();
        return;
      }
      if (!event.target.matches("[data-format-notes], [data-format-zoom]")) return;
      const settings = structuredClone(state.workbookSettings);
      const key = sheetKey(kind);
      settings.sheets[key] = structuredClone(sheetSettings(kind));
      settings.sheets[key].zoom = Number(toolbar.querySelector("[data-format-zoom]").value);
      settings.sheets[key].hide_notes = !toolbar.querySelector("[data-format-notes]").checked;
      await persistWorkbookSettings(settings);
    });
    toolbar.addEventListener("click", async (event) => {
      const templatePageButton = event.target.closest("[data-template-page]");
      if (templatePageButton) {
        const promote = templatePageButton.dataset.templatePage === "promote";
        const pageKey = pageSheetKey(kind);
        const message = promote
          ? "Bu sayfanın biçimi şablon olarak kaydedilsin mi? Kendi biçimi olmayan tüm sayfalar bu görünümü kullanacak."
          : "Bu sayfanın kendi biçimi, eklenen satır/sütunları ve alt not metinleri silinip şablon görünümüne dönülsün mü?";
        if (!window.confirm(message)) return;
        const settings = structuredClone(state.workbookSettings);
        if (promote) settings.sheets[templateSheetKey(kind)] = structuredClone(settings.sheets[pageKey]);
        else delete settings.sheets[pageKey];
        if (await persistWorkbookSettings(settings)) {
          state.selectedCell = null;
          toast(promote ? "Sayfa biçimi şablon yapıldı." : "Sayfa şablon görünümüne döndü.");
        }
        return;
      }
      const historyButton = event.target.closest("[data-format-history]");
      if (historyButton) {
        await applyWorkbookHistory(kind, historyButton.dataset.formatHistory);
        return;
      }
      const undoButton = event.target.closest("[data-format-undo-insert]");
      if (undoButton) {
        const field = undoButton.dataset.formatUndoInsert;
        const axis = field === "insert_rows" ? 0 : 1;
        const key = sheetKey(kind);
        const settings = structuredClone(state.workbookSettings);
        settings.sheets[key] = structuredClone(sheetSettings(kind));
        const sheet = settings.sheets[key];
        const positions = [...(sheet[field] || [])];
        if (!positions.length) return;
        const operationIndex = positions.length - 1;
        const table = el(`#${kind}-table`);
        const insertedNode = axis === 0
          ? table.querySelector(`tbody tr[data-insert-operation-index="${operationIndex}"]`)
          : table.querySelector(`thead [data-insert-operation-index="${operationIndex}"]`);
        const position = axis === 0
          ? [...table.tBodies[0].rows].indexOf(insertedNode) + 1
          : Number(insertedNode?.dataset.formatColumn);
        if (!position || position < 1) {
          toast("Eklenen satır veya sütun tabloda bulunamadı.", true);
          return;
        }
        if (!window.confirm(`Eklenen son ${axis === 0 ? "satır" : "sütun"} ve içeriği kaldırılacak. Devam edilsin mi?`)) return;
        moveCellSettingsForAxis(sheet, axis, position);
        shiftDimensionSettings(sheet, axis === 0 ? "row_heights" : "column_widths", position, -1);
        const template = state.templates[kind][kind === "schedule" ? (state.weekIndex >= 5 ? 1 : state.weekIndex) : 0];
        const baseLength = axis === 0
          ? template.rows.length + (sheet.extra_rows || 0)
          : template.columns.length + (sheet.extra_cols || 0);
        sheet[field] = removeInsertionOperation(sheet[field], operationIndex, baseLength);
        if (await persistWorkbookSettings(settings)) {
          state.selectedCell = null;
          updateFormatToolbar(kind);
        }
        return;
      }
      const insertButton = event.target.closest("[data-format-insert]");
      if (insertButton) {
        const selected = state.selectedCell;
        if (!selected || selected.key !== sheetKey(kind)) return;
        const field = insertButton.dataset.formatInsert;
        const axis = field === "insert_rows" ? 0 : 1;
        const table = el(`#${kind}-table`);
        const visiblePosition = axis === 0
          ? Math.min(selected.row, selected.endRow)
          : Math.min(selected.col, selected.endCol);
        const selectedNode = axis === 0
          ? table.querySelector(`tbody tr:nth-child(${visiblePosition})`)
          : table.querySelector(`thead [data-format-column="${visiblePosition}"]`);
        const position = Number(selectedNode?.dataset[axis === 0 ? "layoutRow" : "layoutColumn"]);
        if (!position) {
          toast("Seçilen satır veya sütun konumu alınamadı.", true);
          return;
        }
        const settings = structuredClone(state.workbookSettings);
        settings.sheets[selected.key] = structuredClone(sheetSettings(kind));
        const sheet = settings.sheets[selected.key];
        const positions = sheet[field] || [];
        if (positions.length >= 20) {
          toast("Bir sayfaya en fazla 20 satır veya sütun eklenebilir.", true);
          return;
        }
        const moved = {};
        for (const [address, value] of Object.entries(sheet.cells)) {
          const coord = address.split(":").map(Number);
          if (coord[axis] >= visiblePosition) coord[axis] += 1;
          moved[coord.join(":")] = value;
        }
        sheet.cells = moved;
        shiftDimensionSettings(sheet, axis === 0 ? "row_heights" : "column_widths", visiblePosition, 1);
        sheet[field] = [...positions, position];
        if (await persistWorkbookSettings(settings)) {
          state.selectedCell = null;
          updateFormatToolbar(kind);
        }
        return;
      }
      const deleteButton = event.target.closest("[data-format-delete]");
      if (deleteButton) {
        const selected = state.selectedCell;
        if (!selected || selected.key !== sheetKey(kind)) return;
        const axis = deleteButton.dataset.formatDelete === "row" ? 0 : 1;
        const visiblePosition = axis === 0
          ? Math.min(selected.row, selected.endRow)
          : Math.min(selected.col, selected.endCol);
        const table = el(`#${kind}-table`);
        const selectedNode = axis === 0
          ? table.querySelector(`tbody tr:nth-child(${visiblePosition})`)
          : table.querySelector(`thead [data-format-column="${visiblePosition}"]`);
        const sourceCell = axis === 0
          ? selectedNode?.querySelector("td[data-source-row]")
          : table.querySelector(`td[data-cell-col="${visiblePosition}"][data-source-col]`);
        const sourcePosition = Number(axis === 0
          ? sourceCell?.dataset.sourceRow
          : sourceCell?.dataset.sourceCol);
        if (sourcePosition < 0) {
          toast("Personel vardiya satırları silinemez.", true);
          return;
        }
        if (!sourcePosition) {
          const field = axis === 0 ? "insert_rows" : "insert_cols";
          const operationIndex = Number(selectedNode?.dataset.insertOperationIndex);
          if (!Number.isInteger(operationIndex)) {
            toast("Seçilen ek satır veya sütun bulunamadı.", true);
            return;
          }
          const settings = structuredClone(state.workbookSettings);
          settings.sheets[selected.key] = structuredClone(sheetSettings(kind));
          const sheet = settings.sheets[selected.key];
          const positions = sheet[field] || [];
          const template = state.templates[kind][kind === "schedule" ? (state.weekIndex >= 5 ? 1 : state.weekIndex) : 0];
          const baseLength = axis === 0
            ? template.rows.length + (sheet.extra_rows || 0)
            : template.columns.length + (sheet.extra_cols || 0);
          if (operationIndex >= positions.length) {
            toast("Seçilen ek satır veya sütun artık bulunmuyor.", true);
            return;
          }
          if (!window.confirm(`Seçili ek ${axis === 0 ? "satır" : "sütun"} ve içeriği silinecek. Devam edilsin mi?`)) return;
          moveCellSettingsForAxis(sheet, axis, visiblePosition);
          shiftDimensionSettings(sheet, axis === 0 ? "row_heights" : "column_widths", visiblePosition, -1);
          sheet[field] = removeInsertionOperation(positions, operationIndex, baseLength);
          if (await persistWorkbookSettings(settings)) {
            state.selectedCell = null;
            updateFormatToolbar(kind);
          }
          return;
        }
        if (axis === 0 && kind === "schedule"
          && (sourcePosition <= 33 || sourcePosition === 35)) {
          toast("Vardiya/personel veya istasyon müdürü satırı silinemez.", true);
          return;
        }
        if (axis === 0 && kind === "attendance" && sourcePosition <= 24) {
          toast("Puantaj başlığı ve personel satırları silinemez.", true);
          return;
        }
        const settings = structuredClone(state.workbookSettings);
        settings.sheets[selected.key] = structuredClone(sheetSettings(kind));
        const sheet = settings.sheets[selected.key];
        const field = axis === 0 ? "delete_rows" : "delete_cols";
        const deleted = sheet[field] || [];
        if (deleted.includes(sourcePosition)) {
          toast("Bu satır veya sütun zaten silinmiş.", true);
          return;
        }
        if (!window.confirm(`Seçili ${axis === 0 ? "satır" : "sütun"} ve biçimlendirmesi silinecek. Dolu şablon, vardiya veya puantaj alanları korunur. Devam edilsin mi?`)) return;
        moveCellSettingsForAxis(sheet, axis, visiblePosition);
        shiftDimensionSettings(sheet, axis === 0 ? "row_heights" : "column_widths", visiblePosition, -1);
        sheet[field] = [...deleted, sourcePosition];
        if (await persistWorkbookSettings(settings)) {
          state.selectedCell = null;
          updateFormatToolbar(kind);
        }
        return;
      }
      const removeButton = event.target.closest("[data-format-remove]");
      if (removeButton) {
        const field = removeButton.dataset.formatRemove;
        const settings = structuredClone(state.workbookSettings);
        const key = sheetKey(kind);
        settings.sheets[key] = structuredClone(sheetSettings(kind));
        const sheet = settings.sheets[key];
        const count = sheet[field] || 0;
        if (!count) return;
        const template = state.templates[kind][kind === "schedule" ? (state.weekIndex >= 5 ? 1 : state.weekIndex) : 0];
        const index = (field === "extra_rows" ? template.rows.length : template.columns.length) + count;
        if (!window.confirm(`Son ek ${field === "extra_rows" ? "satır" : "sütun"} ve içeriği kaldırılacak. Devam edilsin mi?`)) return;
        const moved = {};
        for (const [address, value] of Object.entries(sheet.cells)) {
          let [row, col] = address.split(":").map(Number);
          if ((field === "extra_rows" ? row : col) === index) continue;
          if (field === "extra_rows" && kind === "schedule" && row > index) row -= 1;
          moved[`${row}:${col}`] = value;
        }
        sheet.cells = moved;
        shiftDimensionSettings(sheet, field === "extra_rows" ? "row_heights" : "column_widths", index, -1);
        sheet[field] = count - 1;
        if (await persistWorkbookSettings(settings)) {
          state.selectedCell = null;
          updateFormatToolbar(kind);
        }
        return;
      }
      const addButton = event.target.closest("[data-format-add]");
      if (addButton) {
        const settings = structuredClone(state.workbookSettings);
        const key = sheetKey(kind);
        settings.sheets[key] = structuredClone(sheetSettings(kind));
        const field = addButton.dataset.formatAdd;
        const max = field === "extra_rows" ? 50 : 20;
        const current = settings.sheets[key][field] || 0;
        if (current >= max) {
          toast(`En fazla ${max} ek ${field === "extra_rows" ? "satır" : "sütun"} eklenebilir.`, true);
          return;
        }
        if (field === "extra_rows" && kind === "schedule") {
          const template = state.templates.schedule[state.weekIndex >= 5 ? 1 : state.weekIndex];
          const firstExtra = template.rows.length + current + (settings.sheets[key].insert_rows || []).length + 1;
          const moved = {};
          for (const [address, value] of Object.entries(settings.sheets[key].cells)) {
            let [row, col] = address.split(":").map(Number);
            if (row >= firstExtra) row += 1;
            moved[`${row}:${col}`] = value;
          }
          settings.sheets[key].cells = moved;
          shiftDimensionSettings(settings.sheets[key], "row_heights", firstExtra, 1);
        }
        settings.sheets[key][field] = current + 1;
        await persistWorkbookSettings(settings);
        return;
      }
      const button = event.target.closest("[data-format-action]");
      const selected = state.selectedCell;
      if (!button || !selected || selected.key !== sheetKey(kind)) return;
      const action = button.dataset.formatAction;
      if (action === "clear-shifts") {
        const targets = [...el(`#${kind}-table`).querySelectorAll("td.format-selected[data-shift-day]")].map((cell) => {
          const row = state.schedule.entries.find((item) => item.row_id === cell.dataset.shiftRow);
          const day = cell.dataset.shiftDay;
          const owner = row && scheduleRowForDay(row, day, false);
          return owner?.days[day] ? { owner, day } : null;
        }).filter(Boolean);
        if (!targets.length) {
          toast("Seçili hücrelerde temizlenecek vardiya yok.", true);
          return;
        }
        if (!window.confirm(`${targets.length} günlük vardiya veya durum temizlenecek. Devam edilsin mi?`)) return;
        for (const { owner, day } of targets) {
          delete owner.days[day];
          if (day.slice(0, 7) !== state.scheduleMonth) state.neighborDirty.add(day.slice(0, 7));
        }
        setScheduleDirty();
        renderSchedule();
        toast("Seçili vardiyalar temizlendi. Değişiklikleri kaydetmeyi unutmayın.");
        return;
      }
      const settings = structuredClone(state.workbookSettings);
      settings.sheets[selected.key] = structuredClone(sheetSettings(kind));
      const cells = settings.sheets[selected.key].cells;
      if (action === "width" || action === "height") {
        const isWidth = action === "width";
        const size = Number(toolbar.querySelector(isWidth ? "[data-format-width]" : "[data-format-height]").value);
        const max = isWidth ? 255 : 409;
        if (!Number.isFinite(size) || size < 1 || size > max) {
          toast(`Ölçü 1 ile ${max} arasında olmalıdır.`, true);
          return;
        }
        const field = isWidth ? "column_widths" : "row_heights";
        const dimensions = settings.sheets[selected.key][field] || {};
        let start = isWidth ? Math.min(selected.col, selected.endCol) : Math.min(selected.row, selected.endRow);
        let end = isWidth ? Math.max(selected.col, selected.endCol) : Math.max(selected.row, selected.endRow);
        if (isWidth && start === end) {
          const anchor = el(`#${kind}-table`).querySelector(`td[data-cell-row="${selected.row}"][data-cell-col="${selected.col}"]`);
          if (anchor?.dataset.shiftDay) end = start + anchor.colSpan - 1;
        }
        for (let index = start; index <= end; index += 1) dimensions[String(index)] = size;
        settings.sheets[selected.key][field] = dimensions;
      } else if (action === "color" || action === "font" || action === "alignment") {
        const size = Number(toolbar.querySelector("[data-format-font-size]").value);
        if (action === "font" && (!Number.isInteger(size) || size < 6 || size > 72)) {
          toast("Yazı boyutu 6 ile 72 arasında olmalıdır.", true);
          return;
        }
        const selectedCells = el(`#${kind}-table`).querySelectorAll("td.format-selected");
        for (const cell of selectedCells) {
          const row = Number(cell.dataset.cellRow);
          const col = Number(cell.dataset.cellCol);
          const addresses = [`${row}:${col}`];
          if (kind === "schedule" && cell.dataset.shiftDay && cell.colSpan === 2) addresses.push(`${row}:${col + 1}`);
          for (const address of addresses) {
            cells[address] = { ...cells[address] };
            if (action === "color") cells[address].color = toolbar.querySelector("[data-format-color]").value;
            else if (action === "font") Object.assign(cells[address], {
              font_name: toolbar.querySelector("[data-format-font-name]").value,
              font_size: size,
              font_color: toolbar.querySelector("[data-format-font-color]").value,
              font_bold: toolbar.querySelector("[data-format-bold]").checked,
              font_italic: toolbar.querySelector("[data-format-italic]").checked,
              font_underline: toolbar.querySelector("[data-format-underline]").checked,
              font_strike: toolbar.querySelector("[data-format-strike]").checked,
            });
            if (action === "font" && toolbar.querySelector("[data-format-rotation]").value !== "") {
              cells[address].text_rotation = Number(toolbar.querySelector("[data-format-rotation]").value);
            }
            if (action !== "color" && action !== "font") {
              const fit = toolbar.querySelector("[data-format-fit]").value;
              Object.assign(cells[address], {
                horizontal: toolbar.querySelector("[data-format-horizontal]").value,
                vertical: toolbar.querySelector("[data-format-vertical]").value,
                shrink_to_fit: fit === "shrink",
                wrap_text: fit === "wrap",
              });
            }
          }
        }
      } else if (action === "clear") {
        const notes = [...el(`#${kind}-table`).querySelectorAll("td.format-selected")]
          .filter((cell) => Number(cell.dataset.sourceRow) >= 0 && (Number(cell.dataset.sourceRow) === 0 || Number(cell.dataset.sourceCol) === 0
            || Number(cell.dataset.sourceRow) >= noteStart(kind)
            || Number(cell.dataset.sourceCol) > state.templates[kind][kind === "schedule" ? (state.weekIndex >= 5 ? 1 : state.weekIndex) : 0].columns.length));
        if (!window.confirm(`${notes.length} not hücresi temizlenecek. Devam edilsin mi?`)) return;
        for (const cell of notes) {
          const address = `${cell.dataset.cellRow}:${cell.dataset.cellCol}`;
          cells[address] = { ...cells[address], text: "" };
        }
      } else {
        const address = `${selected.row}:${selected.col}`;
        cells[address] = { ...cells[address], text: toolbar.querySelector("[data-format-text]").value };
      }
      await persistWorkbookSettings(settings);
    });
    const table = el(`#${kind}-table`);
    let selecting = false;
    table.addEventListener("pointerdown", (event) => {
      if (!state.formatMode || event.button !== 0) return;
      if (kind === "attendance" && (state.attendanceQuery || state.attendanceFilter !== "all")) {
        toast("Biçimlendirmek için Tüm personel görünümüne geçin.", true);
        return;
      }
      const cell = event.target.closest("td[data-cell-row]");
      event.preventDefault();
      const rowHeader = event.target.closest("th[data-format-row]");
      const colHeader = event.target.closest("th[data-format-column]");
      if (rowHeader || colHeader) {
        const row = rowHeader ? Number(rowHeader.dataset.formatRow) : 1;
        const col = colHeader ? Number(colHeader.dataset.formatColumn) : 1;
        const rowCount = table.tBodies[0].rows.length;
        const colCount = table.tHead.rows[0].cells.length - 1;
        state.selectedCell = {
          key: sheetKey(kind), row, col,
          endRow: colHeader ? rowCount : row,
          endCol: rowHeader ? colCount : col,
        };
        selecting = false;
        updateFormatToolbar(kind);
        return;
      }
      if (!cell) return;
      const row = Number(cell.dataset.cellRow);
      const col = Number(cell.dataset.cellCol);
      state.selectedCell = { key: sheetKey(kind), row, col, endRow: row, endCol: col };
      selecting = true;
      updateFormatToolbar(kind);
    });
    table.addEventListener("pointerover", (event) => {
      if (!selecting || !state.formatMode) return;
      const cell = event.target.closest("td[data-cell-row]");
      if (!cell) return;
      state.selectedCell.endRow = Number(cell.dataset.cellRow) + cell.rowSpan - 1;
      state.selectedCell.endCol = Number(cell.dataset.cellCol) + cell.colSpan - 1;
      updateFormatToolbar(kind);
    });
    document.addEventListener("pointerup", () => { selecting = false; });
    table.addEventListener("keydown", (event) => {
      if (state.formatMode && event.target.matches("td[data-cell-row]")
        && ["Enter", " "].includes(event.key)) {
        event.preventDefault();
        const row = Number(event.target.dataset.cellRow);
        const col = Number(event.target.dataset.cellCol);
        state.selectedCell = { key: sheetKey(kind), row, col, endRow: row, endCol: col };
        updateFormatToolbar(kind);
      }
    });
  }
}

function prepareImport(rows) {
  const normalizeName = (name) => name.trim().replace(/\s+/g, " ").toLocaleLowerCase("tr-TR");
  const staged = structuredClone(state.schedule.entries);
  const used = Object.fromEntries(SECTIONS.map((section) => [section, new Set()]));
  for (const item of scheduleRowsByTemplate().values()) used[item.section].add(item.slot);
  let conflicts = 0;
  for (const row of rows) {
    const matches = state.people.filter((person) => person.active && normalizeName(person.name) === normalizeName(row.person));
    if (matches.length !== 1) throw new Error(`Personel eşleşmedi veya birden fazla eşleşme var: ${row.person}. Önce personeli kaydedin.`);
    const employeeId = matches[0].id;
    let target = staged.find((item) => item.employee_id === employeeId && item.section === row.section);
    if (!target) {
      const slot = Array.from({ length: SECTION_LIMITS[row.section] }, (_, index) => index + 1)
        .find((number) => !used[row.section].has(number));
      if (!slot) throw new Error(`${row.section} bölümünde boş personel satırı yok.`);
      used[row.section].add(slot);
      target = { row_id: newRowId(), employee_id: employeeId, section: row.section, slot, days: {} };
      staged.push(target);
    }
    if (target.days[row.date] && JSON.stringify(target.days[row.date]) !== JSON.stringify(row.entry)) conflicts += 1;
    target.days[row.date] = row.entry;
  }
  if (new Set(staged.map((row) => row.employee_id)).size > SECTION_SETTINGS.max_personnel) {
    throw new Error(`En fazla ${SECTION_SETTINGS.max_personnel} farklı personel desteklenir.`);
  }
  return { entries: staged, conflicts };
}

function registerImportEvents() {
  const dialog = el("#import-dialog");
  const input = el("#import-file");
  const message = el("#import-message");
  const preview = el("#import-preview");
  const apply = el("#apply-import");
  const mapping = el("#import-mapping");
  const source = el("#import-source");
  const tableSelect = el("#import-table");
  const scopeControls = el("#import-scope-controls");
  const scopeSelect = el("#import-scope");
  const codeOptions = el("#import-code-options");
  const scopePreview = el("#preview-import-scope");
  const sourceContext = el("#import-source-context");
  const sourceContextRows = el("#import-source-context-rows");
  const ocrEvidenceSection = el("#import-ocr-evidence");
  const ocrEvidenceRows = el("#import-ocr-evidence-rows");
  const openMapping = el("#open-import-mapping");
  let documentData = null;
  let tables = [];
  let ocrPages = [];
  let ocrEvidence = [];
  let tableIndex = 0;
  let originalText = "";
  let importSourceRows = [];
  let importReview = [];
  let importSourceMode = "auto";
  let fileVersion = 0;
  let previewVersion = 0;
  let importProfiles = [];
  const importProfileSelect = el("#import-profile-select");
  const importProfileName = el("#import-profile-name");
  const profileHeaderWarning = el("#import-profile-header-warning");
  const profileHeaderWarningText = el("#import-profile-header-warning-text");
  const profileHeaderConfirm = el("#import-profile-header-confirm");
  let profileHeaderCheckKey = "";
  let profileHeaderCheck = null;
  let profileHeaderConfirmed = false;
  const importExistingRows = () => {
    const names = new Map(state.people.filter((person) => person.active)
      .map((person) => [person.id, person.name]));
    return state.schedule.entries.flatMap((item) => {
      const person = names.get(item.employee_id);
      if (!person) return [];
      return Object.entries(item.days || {}).map(([date, entry]) => ({
        person, section: item.section, date, entry,
      }));
    });
  };
  const importMapping = () => {
    const number = (id) => {
      const value = el(`#map-${id}`).value;
      return value ? Number(value) : null;
    };
    return {
      mode: el("#map-mode").value,
      start_row: number("start-row"),
      person_col: number("person-col"),
      section_col: number("section-col"),
      default_section: el("#map-default-section").value,
      date_col: number("date-col"),
      status_col: number("status-col"),
      start_col: number("start-col"),
      end_col: number("end-col"),
      header_row: number("header-row"),
      first_day_col: number("first-day-col"),
    };
  };
  const renderImportProfiles = () => {
    importProfileSelect.innerHTML = `<option value="">Profil seçin</option>${importProfiles
      .map((profile) => `<option value="${escapeHtml(profile.id)}">${escapeHtml(profile.name)}</option>`)
      .join("")}`;
  };
  const loadImportProfiles = async () => {
    const result = await request("/api/import-profiles");
    importProfiles = result.profiles;
    renderImportProfiles();
  };
  const profileHeaderContext = (profile) =>
    JSON.stringify([profile.id, source.value, importMapping()]);
  const renderProfileHeaderWarning = (check) => {
    profileHeaderCheck = check;
    profileHeaderConfirmed = !check.needs_confirmation;
    profileHeaderConfirm.checked = false;
    profileHeaderWarning.hidden = !check.needs_confirmation;
    if (!check.needs_confirmation) return;
    if (!check.expected) {
      profileHeaderWarningText.textContent = "Bu eski profilde başlık örneği kayıtlı değil. Yeni belgenin sütun başlıklarını inceleyip aşağıdaki onayı verin.";
      return;
    }
    const expected = new Map(check.expected.columns.map((item) =>
      [`${item.field}:${item.column}`, item.header]));
    const actual = new Map(check.actual.columns.map((item) =>
      [`${item.field}:${item.column}`, item.header]));
    const keys = new Set([...expected.keys(), ...actual.keys()]);
    const changes = [...keys].filter((key) => expected.get(key) !== actual.get(key)).map((key) => {
      const [field, column] = key.split(":");
      const before = expected.get(key) || "başlık yok";
      const after = actual.get(key) || "başlık yok";
      return `<li>${escapeHtml(field)} · sütun ${escapeHtml(column)}: “${escapeHtml(before)}” → “${escapeHtml(after)}”</li>`;
    });
    profileHeaderWarningText.innerHTML = changes.length
      ? `Yeni belgenin başlıkları kaydedilmiş profilden farklı:<ul>${changes.join("")}</ul> Devam etmeden önce eşleştirmeyi kontrol edin.`
      : "Bu profilin kayıtlı bir başlık örneği yok veya eşleştirme yapısı değişti. Devam etmeden önce sütunları kontrol edin.";
  };
  const ensureProfileHeadersConfirmed = async () => {
    const profile = importProfiles.find((item) => item.id === importProfileSelect.value);
    if (!profile || !source.value) return true;
    const context = profileHeaderContext(profile);
    if (context !== profileHeaderCheckKey) {
      const check = await request("/api/import-profile-headers", {
        method: "POST",
        body: JSON.stringify({
          text: source.value, mapping: importMapping(),
          expected: profile.header_signature || null,
        }),
      });
      if (context !== profileHeaderContext(profile)) return false;
      profileHeaderCheckKey = context;
      renderProfileHeaderWarning(check);
    }
    return !profileHeaderCheck?.needs_confirmation || profileHeaderConfirmed;
  };
  const invalidate = () => {
    previewVersion += 1;
    state.importPreview = null;
    apply.disabled = true;
    el('[data-output-kind="import"]').disabled = true;
    preview.innerHTML = "";
  };
  const clearImportSource = () => {
    importSourceRows = [];
    scopeSelect.value = "all";
    scopeControls.hidden = true;
    codeOptions.replaceChildren();
    codeOptions.hidden = true;
    scopePreview.disabled = true;
  };
  const clearSourceContext = () => {
    importReview = [];
    sourceContext.hidden = true;
    sourceContextRows.replaceChildren();
  };
  const renderOcrEvidence = () => {
    const rows = ocrEvidence.filter((item) => item.page === tableIndex + 1);
    const pageNeedsReview = ocrPages.includes(tableIndex + 1);
    ocrEvidenceSection.hidden = !pageNeedsReview;
    if (!pageNeedsReview) {
      ocrEvidenceRows.replaceChildren();
      return;
    }
    ocrEvidenceRows.innerHTML = rows.length
      ? rows.map((item) => `<figure class="import-ocr-evidence-row">
          <figcaption>Sayfa ${item.page} · satır ${item.row}: ${escapeHtml(item.text)}</figcaption>
          <img src="${item.image}" alt="Sayfa ${item.page}, satır ${item.row} için OCR ile tanınan metnin özgün görüntü bölgesi" loading="lazy">
        </figure>`).join("")
      : "<p>Bu sayfada görüntü konumuyla eşleştirilebilen OCR metni bulunamadı.</p>";
  };
  const renderSourceContext = (items = importReview) => {
    importReview = items;
    sourceContext.hidden = !items.length;
    sourceContextRows.innerHTML = items.map((item) => {
      const location = item.location || "Konum bulunamadı";
      const lineNumber = Number(location.match(/satır\s+(\d+)/i)?.[1]);
      const pageMatches = item.page == null || item.page === tableIndex + 1;
      const editedLine = pageMatches && lineNumber
        ? source.value.split(/\r?\n/)[lineNumber - 1] || ""
        : "";
      const currentText = pageMatches
        ? editedLine || "Düzenlenebilir metin satırı bulunamadı."
        : "Bu satır başka bir sayfa / tablodadır; düzenlemek için ilgili sayfayı seçin.";
      const sourceLocation = [
        item.filename || documentData?.filename || "Düzenlenen metin",
        item.page == null ? "" : `Sayfa / tablo ${item.page}`,
        location,
      ].filter(Boolean).map(escapeHtml).join(" · ");
      return `<article class="import-source-card"><strong>${sourceLocation}</strong><div class="import-source-comparison"><div><span>Özgün içerik</span><pre>${escapeHtml(item.source || "Kaynak içerik alınamadı.")}</pre></div><div><span>Düzenlenebilir satır</span><pre>${escapeHtml(currentText)}</pre></div></div><small>${escapeHtml(item.reason)}</small></article>`;
    }).join("");
  };
  const updateScopePreviewButton = () => {
    scopePreview.disabled = !importSourceRows.length
      || (scopeSelect.value === "codes" && !codeOptions.querySelector('input[type="checkbox"]:checked'));
  };
  const renderImportCodes = () => {
    codeOptions.replaceChildren();
    codeOptions.hidden = scopeSelect.value !== "codes";
    if (!codeOptions.hidden) {
      const kinds = [...new Set(importSourceRows.map((row) => row.entry.kind).filter((kind) => kind !== "work"))];
      for (const kind of kinds) {
        const option = SHIFT_OPTIONS.find(([value]) => value === kind);
        const code = option?.[1].match(/·\s*(.+)$/)?.[1].trim();
        if (!code) continue;
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = code;
        label.append(checkbox, document.createTextNode(`${code} — ${option[1].split("·")[0].trim()}`));
        codeOptions.append(label);
      }
    }
    updateScopePreviewButton();
  };
  const reset = () => {
    fileVersion += 1;
    invalidate();
    clearImportSource();
    clearSourceContext();
    input.value = "";
    message.textContent = "";
    mapping.hidden = true;
    openMapping.disabled = true;
    documentData = null;
    tables = [];
    ocrPages = [];
    ocrEvidence = [];
    renderOcrEvidence();
    tableSelect.innerHTML = "";
    source.value = "";
    originalText = "";
    importProfileSelect.value = "";
    importProfileName.value = "";
    el("#delete-import-profile").disabled = true;
    importSourceMode = "auto";
  };
  const setTable = (index) => {
    tableIndex = index;
    originalText = (tables[index] || []).map((row) => row.join(" | ")).join("\n");
    source.value = originalText;
    renderOcrEvidence();
  };
  const showPreview = (rows, month, comparison, review, sourceRows = null) => {
    if (sourceRows) {
      importSourceRows = sourceRows;
      scopeControls.hidden = false;
      renderImportCodes();
    }
    const plan = prepareImport(rows);
    if (review.length) mapping.hidden = false;
    renderSourceContext(review);
    state.importPreview = { rows, month, comparison, review, selectedValidGroup: false };
    const counts = comparison.counts;
    const noChanges = !rows.length || (counts.new === 0 && counts.changed === 0);
    message.textContent = `${rows.length} uygun kayıt: ${counts.new} yeni, ${counts.same} aynı, ${counts.changed} değişecek; ${review.length} kayıt inceleme bekliyor.`;
    const validTable = rows.length
      ? `<table><thead><tr><th>Personel</th><th>Bölüm</th><th>Tarih</th><th>Vardiya / durum</th><th>İçerik karşılaştırması</th></tr></thead><tbody>${rows.map((row, index) => {
      const detail = row.entry.kind === "work" ? `${row.entry.start} – ${row.entry.end}`
        : SHIFT_OPTIONS.find(([value]) => value === row.entry.kind)?.[1] || row.entry.kind;
      const status = { same: "Aynı · değişmeden kalır", new: "Yeni · eklenecek", changed: "Değişmiş · güncellenecek" }[comparison.statuses[index]];
      return `<tr><td>${escapeHtml(row.person)}</td><td>${escapeHtml(row.section)}</td><td>${escapeHtml(row.date)}</td><td>${escapeHtml(detail)}</td><td>${status}</td></tr>`;
      }).join("")}</tbody></table>`
      : `<p class="import-empty">Seçilen kapsamda uygun kayıt yok.</p>`;
    const reviewTable = review.length
      ? `<section class="import-review"><h3>İnceleme bekleyen kayıtlar (${review.length})</h3><table><thead><tr><th>Kaynak satırı</th><th>İçerik</th><th>Sorun</th></tr></thead><tbody>${review.map((item) =>
        `<tr><td>${escapeHtml(item.location || "Konum bulunamadı")}</td><td>${escapeHtml(item.source || "Kaynak içerik alınamadı")}</td><td>${escapeHtml(item.reason)}</td></tr>`
      ).join("")}</tbody></table></section>`
      : "";
    const applyGroup = `<fieldset class="import-apply-group">
      <legend>Uygulanacak grup</legend>
      <label><input id="import-apply-valid-group" type="checkbox"${noChanges ? " disabled" : ""}>
        Yalnız uygun kayıtları uygula (${counts.new} yeni, ${counts.changed} değişecek, ${counts.same} aynı)</label>
      <p>İnceleme grubu: ${review.length} kayıt; bu işlemde aktarılmayacak.</p>
    </fieldset>`;
    preview.innerHTML = validTable + applyGroup + reviewTable;
    const applyValidGroup = preview.querySelector("#import-apply-valid-group");
    apply.disabled = noChanges || !applyValidGroup || !applyValidGroup.checked;
    applyValidGroup?.addEventListener("change", () => {
      state.importPreview.selectedValidGroup = applyValidGroup.checked;
      apply.disabled = noChanges || !applyValidGroup.checked;
    });
    if (noChanges && rows.length) message.textContent += " Çizelgede değişiklik gerekmediği için aktarım kapalı.";
    el('[data-output-kind="import"]').disabled = false;
  };
  const extract = async (version) => {
    const result = await request("/api/import-extract", {
      method: "POST",
      body: JSON.stringify({ ...documentData, force_ocr: el("#import-force-ocr").checked }),
    });
    if (version !== fileVersion) return null;
    tables = result.tables;
    ocrPages = result.ocr_pages;
    ocrEvidence = result.ocr_evidence || [];
    tableSelect.innerHTML = tables.map((table, index) =>
      `<option value="${index}">${index + 1}. sayfa / tablo (${table.length} satır)</option>`).join("");
    setTable(0);
    openMapping.disabled = false;
    return result;
  };
  const ocrReviewMessage = (result, includeMapping) =>
    `Taranmış sayfalar: ${result.ocr_pages.join(", ")}. ${
      result.ocr_confidence_available
        ? "Tanıma sonucunu asıl belgeyle karşılaştırıp düzeltin."
        : "OCR motoru alan bazlı güven puanı vermiyor; tüm tanınan hücreleri belgeyle tek tek karşılaştırıp düzeltin."
    }${includeMapping ? " Sütunları da eşleştirin." : ""}`;
  source.addEventListener("input", () => {
    invalidate();
    renderSourceContext();
  });
  const discardEdits = () => source.value === originalText
    || window.confirm("Metindeki düzeltmeler kaybolacak. Devam edilsin mi?");
  el("#open-import").addEventListener("click", async () => {
    reset();
    dialog.showModal();
    try {
      await loadImportProfiles();
    } catch (error) {
      message.textContent = `Eşleştirme profilleri yüklenemedi: ${error.message}`;
    }
  });
  el("#close-import").addEventListener("click", () => { if (discardEdits()) dialog.close(); });
  dialog.addEventListener("cancel", (event) => { if (!discardEdits()) event.preventDefault(); });
  scopeSelect.addEventListener("change", () => {
    invalidate();
    renderImportCodes();
  });
  codeOptions.addEventListener("change", () => {
    invalidate();
    updateScopePreviewButton();
  });
  scopePreview.addEventListener("click", async () => {
    const month = state.scheduleMonth;
    const fields = {
      mode: scopeSelect.value,
      codes: scopeSelect.value === "codes"
        ? Array.from(codeOptions.querySelectorAll('input[type="checkbox"]:checked'), (item) => item.value)
        : [],
    };
    if (fields.mode === "codes" && !fields.codes.length) return;
    invalidate();
    const versionOfPreview = previewVersion;
    const versionOfFile = fileVersion;
    message.textContent = "Seçilen kapsam için önizleme yeniden oluşturuluyor…";
    try {
      const result = importSourceMode === "mapped"
        ? await request("/api/import-mapped-preview", {
          method: "POST",
          body: JSON.stringify({
            month, text: source.value, mapping: importMapping(), fields, existing: importExistingRows(), partial: true,
            filename: documentData?.filename || "Düzenlenen metin", page: tableIndex + 1,
          }),
        })
        : await request("/api/import-preview", {
          method: "POST",
          body: JSON.stringify({ ...documentData, month, fields, existing: importExistingRows(), partial: true }),
        });
      if (versionOfPreview !== previewVersion || versionOfFile !== fileVersion) return;
      if (month !== state.scheduleMonth) throw new Error("Ay değişti; önizlemeyi yeniden oluşturun.");
      showPreview(result.rows, month, result.comparison, result.review);
    } catch (error) {
      if (versionOfPreview === previewVersion && versionOfFile === fileVersion) {
        message.textContent = error.message;
      }
    }
  });
  input.addEventListener("change", async () => {
    if (!discardEdits()) {
      input.value = "";
      return;
    }
    const version = ++fileVersion;
    invalidate();
    clearImportSource();
    clearSourceContext();
    ocrPages = [];
    ocrEvidence = [];
    renderOcrEvidence();
    importSourceMode = "auto";
    mapping.hidden = true;
    openMapping.disabled = true;
    documentData = null;
    const file = input.files[0];
    if (!file) return;
    if (file.size > 5_000_000) {
      message.textContent = "Dosya 5 MB sınırını aşmamalıdır.";
      return;
    }
    const month = state.scheduleMonth;
    message.textContent = "Dosya okunuyor…";
    try {
      const encoded = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = () => reject(new Error("Dosya okunamadı."));
        reader.readAsDataURL(file);
      });
      if (version !== fileVersion) return;
      documentData = { filename: file.name, content: encoded };
      el("#import-force-ocr").checked = false;
      el("#import-force-ocr").disabled = !file.name.toLocaleLowerCase("tr-TR").endsWith(".pdf");
      const extracted = await extract(version);
      if (!extracted || version !== fileVersion) return;
      if (extracted.ocr_pages.length) {
        mapping.hidden = false;
        message.textContent = ocrReviewMessage(extracted, true);
      } else {
        try {
          const versionOfPreview = previewVersion;
          const result = await request("/api/import-preview", {
            method: "POST", body: JSON.stringify({ ...documentData, month, existing: importExistingRows(), partial: true }),
          });
          if (version === fileVersion && versionOfPreview === previewVersion && month === state.scheduleMonth) {
            showPreview(result.rows, month, result.comparison, result.review, result.rows);
          }
        } catch (error) {
          if (version !== fileVersion) return;
          mapping.hidden = false;
          message.textContent = `${error.message} Elle eşleştirmeyi kullanın.`;
        }
      }
    } catch (error) {
      if (version !== fileVersion) return;
      message.textContent = error.message || "Dosya okunamadı.";
    }
  });
  openMapping.addEventListener("click", () => { mapping.hidden = false; });
  tableSelect.addEventListener("change", () => {
    if (source.value !== originalText && !window.confirm("Bu tablodaki metin düzeltmeleri kaybolacak. Başka tabloya geçilsin mi?")) {
      tableSelect.value = String(tableIndex);
      return;
    }
    clearImportSource();
    invalidate();
    setTable(Number(tableSelect.value));
    clearSourceContext();
  });
  el("#import-reread").addEventListener("click", async () => {
    if (!documentData) return;
    if (source.value !== originalText && !window.confirm("Metindeki düzeltmeler kaybolacak. Dosya yeniden okunsun mu?")) return;
    clearImportSource();
    clearSourceContext();
    invalidate();
    message.textContent = "Dosya yeniden okunuyor…";
    try {
      const result = await extract(fileVersion);
      if (result) message.textContent = result.ocr_pages.length
        ? ocrReviewMessage(result, false)
        : "Metni kontrol edip sütunları eşleştirin.";
    } catch (error) { message.textContent = error.message; }
  });
  const invalidateMapping = () => {
    importSourceMode = "mapped";
    clearImportSource();
    invalidate();
    renderSourceContext();
  };
  mapping.addEventListener("input", invalidateMapping);
  mapping.addEventListener("change", invalidateMapping);
  el("#map-mode").addEventListener("change", (event) => {
    const matrix = event.target.value === "matrix";
    document.querySelectorAll(".map-rows-field").forEach((item) => { item.hidden = matrix; });
    document.querySelectorAll(".map-matrix-field").forEach((item) => { item.hidden = !matrix; });
  });
  importProfileSelect.addEventListener("change", () => {
    const profile = importProfiles.find((item) => item.id === importProfileSelect.value);
    el("#delete-import-profile").disabled = !profile;
    profileHeaderCheckKey = "";
    profileHeaderCheck = null;
    profileHeaderConfirmed = false;
    profileHeaderWarning.hidden = true;
    profileHeaderConfirm.checked = false;
    if (!profile) return;
    importProfileName.value = profile.name;
    const mappingProfile = profile.mapping;
    el("#map-mode").value = mappingProfile.mode;
    el("#map-mode").dispatchEvent(new Event("change", { bubbles: true }));
    el("#map-default-section").value = mappingProfile.default_section;
    for (const field of [
      "start_row", "person_col", "section_col", "date_col", "status_col",
      "start_col", "end_col", "header_row", "first_day_col",
    ]) {
      const inputField = el(`#map-${field.replaceAll("_", "-")}`);
      inputField.value = mappingProfile[field] ?? "";
    }
    invalidate();
    message.textContent = `“${profile.name}” profili uygulandı; belgeye göre alanları doğrulayın.`;
  });
  el("#save-import-profile").addEventListener("click", async () => {
    const name = importProfileName.value.trim();
    if (!name) {
      message.textContent = "Profili kaydetmek için bir ad yazın.";
      importProfileName.focus();
      return;
    }
    const selected = importProfiles.find((item) => item.id === importProfileSelect.value);
    if (selected && source.value && !(await ensureProfileHeadersConfirmed())) {
      message.textContent = "Profil güncellenmedi. Önce yeni belge başlıklarını inceleyip onaylayın.";
      return;
    }
    let headerSignature = selected?.header_signature || null;
    if (source.value) {
      try {
        const check = await request("/api/import-profile-headers", {
          method: "POST",
          body: JSON.stringify({ text: source.value, mapping: importMapping(), expected: null }),
        });
        headerSignature = check.actual.columns.length ? check.actual : null;
      } catch (error) {
        message.textContent = error.message;
        return;
      }
    }
    const profile = {
      id: selected?.id || "", name, mapping: importMapping(),
      header_signature: headerSignature,
    };
    const profiles = selected
      ? importProfiles.map((item) => item.id === selected.id ? profile : item)
      : [...importProfiles, profile];
    try {
      const result = await request("/api/import-profiles", {
        method: "PUT",
        body: JSON.stringify({ profiles }),
      });
      importProfiles = result.profiles;
      renderImportProfiles();
      const saved = importProfiles.find((item) => item.name === name);
      importProfileSelect.value = saved?.id || "";
      el("#delete-import-profile").disabled = !saved;
      profileHeaderCheckKey = "";
      profileHeaderCheck = null;
      profileHeaderWarning.hidden = true;
      message.textContent = `“${name}” eşleştirme profili kaydedildi.`;
    } catch (error) {
      message.textContent = error.message;
    }
  });
  profileHeaderConfirm.addEventListener("change", () => {
    profileHeaderConfirmed = profileHeaderConfirm.checked;
    if (profileHeaderConfirmed) {
      message.textContent = "Başlık farklarını incelediniz. Önizlemeyi yeniden başlatarak devam edebilirsiniz.";
    }
  });
  el("#delete-import-profile").addEventListener("click", async () => {
    const selected = importProfiles.find((item) => item.id === importProfileSelect.value);
    if (!selected || !window.confirm(`“${selected.name}” profili silinsin mi?`)) return;
    try {
      const result = await request("/api/import-profiles", {
        method: "PUT",
        body: JSON.stringify({ profiles: importProfiles.filter((item) => item.id !== selected.id) }),
      });
      importProfiles = result.profiles;
      renderImportProfiles();
      importProfileName.value = "";
      el("#delete-import-profile").disabled = true;
      message.textContent = "Eşleştirme profili silindi.";
    } catch (error) {
      message.textContent = error.message;
    }
  });
  el("#preview-import-mapping").addEventListener("click", async () => {
    try {
      if (!(await ensureProfileHeadersConfirmed())) {
        message.textContent = "Profil başlıklarını kontrol edin ve yeni belge için açık onay verin.";
        return;
      }
    } catch (error) {
      message.textContent = error.message;
      return;
    }
    importSourceMode = "mapped";
    clearImportSource();
    invalidate();
    const month = state.scheduleMonth;
    const versionOfPreview = previewVersion;
    const versionOfFile = fileVersion;
    try {
      const result = await request("/api/import-mapped-preview", {
        method: "POST",
        body: JSON.stringify({
          month, text: source.value, mapping: importMapping(), existing: importExistingRows(), partial: true,
          filename: documentData?.filename || "Düzenlenen metin", page: tableIndex + 1,
        }),
      });
      if (versionOfPreview !== previewVersion || versionOfFile !== fileVersion) return;
      if (month !== state.scheduleMonth) throw new Error("Ay değişti; önizlemeyi yeniden oluşturun.");
      showPreview(result.rows, month, result.comparison, result.review, result.rows);
    } catch (error) { message.textContent = error.message; }
  });
  apply.addEventListener("click", () => {
    if (!state.importPreview || state.importPreview.month !== state.scheduleMonth) {
      message.textContent = "Ay değişti; dosyayı yeniden seçin.";
      apply.disabled = true;
      return;
    }
    if (!state.importPreview.selectedValidGroup) {
      message.textContent = "Önce uygulanacak uygun kayıt grubunu seçin.";
      return;
    }
    try {
      const plan = prepareImport(state.importPreview.rows);
      const counts = state.importPreview.comparison.counts;
      const reviewCount = state.importPreview.review.length;
      const warning = `Yalnız uygun kayıt grubu uygulanacak: ${counts.new} yeni, ${counts.changed} değişecek, ${counts.same} aynı kayıt. İnceleme grubu (${reviewCount} kayıt) aktarılmayacak. Devam edilsin mi?`;
      if (!window.confirm(warning)) return;
      state.importUndoSnapshot = {
        month: state.scheduleMonth,
        entries: structuredClone(state.schedule.entries),
      };
      state.schedule.entries = plan.entries;
      el("#undo-import").hidden = false;
      setScheduleDirty(true, true);
      renderSchedule();
      dialog.close();
      toast(`Uygun grup: ${counts.new + counts.changed} uygulandı (${counts.new} yeni, ${counts.changed} güncellendi, ${counts.same} aynı); inceleme grubu: ${reviewCount} aktarılmadı. Kaydet düğmesine basın.`);
    } catch (error) {
      message.textContent = error.message;
    }
  });
  el("#undo-import").addEventListener("click", undoLastImport);
}

function undoLastImport() {
  const snapshot = state.importUndoSnapshot;
  if (!snapshot || snapshot.month !== state.scheduleMonth) {
    clearImportUndo();
    toast("İçe aktarma geri alma kaydı artık kullanılamıyor.", true);
    return;
  }
  state.schedule.entries = structuredClone(snapshot.entries);
  clearImportUndo();
  setScheduleDirty();
  renderSchedule();
  toast("Son içe aktarma geri alındı. Kaydetmeyi unutmayın.");
}

async function loadForms() {
  const result = await request("/api/forms");
  state.formTemplates = result.forms;
}

function resetFormSettings() {
  el("#form-settings-form").reset();
  el("#form-settings-id").value = "";
}

function renderFormSettings() {
  el("#form-settings-list").innerHTML = state.formTemplates.length
    ? state.formTemplates.map((form) => `<div class="form-settings-row">
        <span>${escapeHtml(form.name)} · ${escapeHtml(form.category)} · ${escapeHtml(form.extension.toUpperCase())}
          · ${({ draft: "Taslak", active: "Kullanıma açık", retired: "Kullanımdan kaldırıldı" })[form.status] || "Durum belirsiz"}</span>
        ${form.status === "draft" ? `<button class="button button-light" type="button" data-form-status="active" data-form-id="${escapeHtml(form.id)}">Kullanıma aç</button>` : ""}
        ${form.status === "active" ? `<button class="button button-light" type="button" data-form-status="retired" data-form-id="${escapeHtml(form.id)}">Kullanımdan kaldır</button>` : ""}
        <button class="button button-light" type="button" data-form-download="${escapeHtml(form.id)}">Şablonu indir</button>
        <button class="button button-light" type="button" data-form-replace="${escapeHtml(form.id)}">Güncelle</button>
        <button class="button button-light" type="button" data-form-delete="${escapeHtml(form.id)}">Sil</button>
      </div>`).join("")
    : "<p>Henüz form şablonu yok.</p>";
}

async function openFormSettings() {
  await loadForms();
  resetFormSettings();
  renderFormSettings();
  el("#form-settings-dialog").showModal();
}

function selectFormOptions() {
  const category = el("#form-category").value;
  const select = el("#form-template");
  const previous = select.value;
  const matching = state.formTemplates.filter((item) => item.status === "active"
    && (item.category === category || item.category === "GENEL"));
  select.innerHTML = matching.map((item) =>
    `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} (.${escapeHtml(item.extension)})</option>`).join("");
  if (matching.some((item) => item.id === previous)) select.value = previous;
  else {
    const specific = matching.find((item) => item.category === category);
    if (specific) select.value = specific.id;
  }
}

function formPayload() {
  return {
    id: el("#form-template").value,
    person_id: Number(el("#form-person").value),
    category: el("#form-category").value,
    start: el("#form-start").value,
    end: el("#form-end").value,
    section: el("#form-section").value.trim(),
    description: el("#form-description").value.trim(),
  };
}

async function saveFormDraft() {
  const button = el("#form-save-draft");
  button.disabled = true;
  try {
    await request("/api/form-draft", {
      method: "PUT",
      body: JSON.stringify(formPayload()),
    });
    el("#form-preview-status").textContent = "Son form taslağı bu bilgisayara kaydedildi.";
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function loadFormDraft() {
  const button = el("#form-load-draft");
  button.disabled = true;
  try {
    const { draft } = await request("/api/form-draft");
    if (!draft) {
      el("#form-preview-status").textContent = "Kaydedilmiş form taslağı yok.";
      return;
    }
    el("#form-category").value = draft.category;
    selectFormOptions();
    if (draft.id && !state.formTemplates.some((item) => item.id === draft.id)) {
      throw new Error("Taslakta seçilen form şablonu artık kullanılmıyor; önce geçerli bir şablon seçin.");
    }
    el("#form-template").value = draft.id;
    el("#form-person").value = draft.person_id ? String(draft.person_id) : "";
    el("#form-start").value = draft.start;
    el("#form-end").value = draft.end;
    el("#form-section").value = draft.section;
    el("#form-description").value = draft.description;
    await previewForm();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function previewForm() {
  const sequence = ++state.formPreviewSequence;
  const status = el("#form-preview-status");
  el("#form-preview").textContent = "";
  el("#form-preview-frame").srcdoc = "";
  el("#form-save").disabled = true;
  el("#form-print").disabled = true;
  if (!el("#form-template").value || !el("#form-person").value) {
    status.textContent = "Personel ve bu durum için bir form şablonu seçin.";
    return;
  }
  status.textContent = "Önizleme hazırlanıyor…";
  try {
    const result = await request("/api/forms/preview", {
      method: "POST", body: JSON.stringify(formPayload()),
    });
    if (sequence !== state.formPreviewSequence) return;
    el("#form-preview").textContent = result.text;
    el("#form-preview-frame").srcdoc = result.html;
    status.textContent = "Belgeyi ve boş alanları kontrol edin.";
    el("#form-save").disabled = false;
    el("#form-print").disabled = false;
  } catch (error) {
    if (sequence !== state.formPreviewSequence) return;
    status.textContent = error.message;
  }
}

async function openForms(cell = null) {
  if (state.formOpening || el("#forms-dialog").open) return;
  state.formOpening = true;
  try {
    await loadForms();
    state.formCell = cell;
    el("#form-person").innerHTML = state.people.map((person) =>
      `<option value="${person.id}">${escapeHtml(person.name)}${person.active ? "" : " (pasif)"}</option>`).join("");
    if (cell) el("#form-person").value = cell.dataset.person;
    el("#form-category").value = cell ? cell.value.trim().toLocaleUpperCase("tr-TR") : "G";
    const today = dateKey(new Date());
    const day = cell ? cell.dataset.day : (today.startsWith(state.attendanceMonth) ? today : `${state.attendanceMonth}-01`);
    el("#form-start").value = day;
    el("#form-end").value = day;
    el("#form-section").value = "";
    el("#form-description").value = "";
    el("#form-save-location").textContent = "";
    el("#form-edit-code").hidden = !cell;
    selectFormOptions();
    el("#forms-dialog").showModal();
    await previewForm();
  } finally { state.formOpening = false; }
}

async function saveFormBlob(blob, extension, suggestedName) {
  if (!window.showSaveFilePicker) throw new Error("Bu tarayıcı konum seçimini desteklemiyor. Masaüstü uygulamasını kullanın.");
  const mime = {
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    pdf: "application/pdf",
    csv: "text/csv",
    zip: "application/zip",
  }[extension] || "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  const handle = await window.showSaveFilePicker({
    suggestedName, types: [{ description: `${extension.toUpperCase()} dosyası`, accept: { [mime]: [`.${extension}`] } }],
  });
  const writable = await handle.createWritable();
  await writable.write(blob);
  await writable.close();
  return handle.name;
}

async function saveReportFile(path, extension, suggestedName) {
  const environment = await request("/api/environment");
  if (environment.desktop) {
    const separator = path.includes("?") ? "&" : "?";
    const result = await request(`${path}${separator}save=1`, { method: "POST", body: JSON.stringify({}) });
    if (!result.cancelled) toast(`Kaydedildi: ${result.path}`);
    return !result.cancelled;
  }
  const blob = await request(path);
  try {
    const name = await saveFormBlob(blob, extension, suggestedName);
    toast(`Kaydedildi: ${name}`);
    return true;
  } catch (error) {
    if (error.name === "AbortError") return false;
    throw error;
  }
}

function openReportsDialog() {
  if (!state.attendance) throw new Error("Puantaj yüklenmeden rapor alınamaz.");
  const month = state.attendanceMonth;
  const [year, monthNumber] = month.split("-").map(Number);
  el("#reports-subtitle").textContent = `${MONTH_NAMES[monthNumber - 1]} ${year} · ${state.attendance.rows.length} personel`;
  el("#reports-dirty-note").hidden = !state.attendanceDirty;
  const people = [...state.attendance.rows].sort((a, b) => a.name.localeCompare(b.name, "tr"));
  el("#report-person").innerHTML = `<option value="">Tüm personel (${people.length} sayfa)</option>`
    + people.map((row) => `<option value="${row.employee_id}">${escapeHtml(row.name)}</option>`).join("");
  el("#report-year").value = String(year);
  el("#reports-dialog").showModal();
}

function registerReportEvents() {
  const run = async (button, action) => {
    button.disabled = true;
    try { await action(); } catch (error) { toast(error.message, true); } finally { button.disabled = false; }
  };
  el("#open-reports").addEventListener("click", () => {
    try { openReportsDialog(); } catch (error) { toast(error.message, true); }
  });
  document.querySelectorAll("[data-report-payroll]").forEach((button) => {
    button.addEventListener("click", () => run(button, () => {
      const format = button.dataset.reportPayroll;
      const month = state.attendanceMonth;
      return saveReportFile(`/api/reports/payroll?month=${month}&format=${format}`, format, `BORDRO_${month}.${format}`);
    }));
  });
  el("#report-person-pdf").addEventListener("click", (event) => run(event.currentTarget, () => {
    const month = state.attendanceMonth;
    const id = el("#report-person").value;
    const path = `/api/reports/person-pdf?month=${month}${id ? `&employee_id=${id}` : ""}`;
    return saveReportFile(path, "pdf", `PERSONEL_OZETI_${month}.pdf`);
  }));
  el("#report-yearly").addEventListener("click", (event) => run(event.currentTarget, () => {
    const year = Number(el("#report-year").value);
    if (!Number.isInteger(year) || year < 2000 || year > 2100) throw new Error("Geçerli bir yıl girin.");
    return saveReportFile(`/api/reports/yearly?year=${year}`, "xlsx", `YILLIK_PUANTAJ_${year}.xlsx`);
  }));
  el("#open-month-close").addEventListener("click", () => openMonthClose().catch((error) => toast(error.message, true)));
  el("#month-close-dialog").addEventListener("click", (event) => {
    const button = event.target.closest("[data-close-action]");
    if (button) run(button, () => runMonthCloseStep(button.dataset.closeAction));
  });
}

function setMonthCloseStep(step, status, message) {
  const item = el(`#month-close-dialog [data-close-step="${step}"]`);
  item.dataset.status = status;
  item.querySelector("[data-step-status]").textContent = message;
}

async function openMonthClose() {
  if (!state.attendance) throw new Error("Puantaj yüklenmeden ay sonu kapanışı yapılamaz.");
  const month = state.attendanceMonth;
  const [year, number] = month.split("-").map(Number);
  el("#month-close-subtitle").textContent = `${MONTH_NAMES[number - 1]} ${year}`;
  el("#month-close-dirty").hidden = !state.attendanceDirty;
  setMonthCloseStep("check", "", "Boş gün, dinlenme ve izin uyarılarını gözden geçirin.");
  setMonthCloseStep("backup", "", "Kilitlemeden önce tüm verilerin doğrulanmış yedeği alınır.");
  setMonthCloseStep("export", "", "Puantaj Excel'i ve bordro aktarım dosyasını kaydedin.");
  const exportStep = el('#month-close-dialog [data-close-step="export"]');
  delete exportStep.dataset.excel;
  delete exportStep.dataset.payroll;
  const archive = await request("/api/archive");
  const item = archive.months.find((entry) => entry.month === month);
  if (item?.locked) setMonthCloseStep("lock", "done", "Bu dönem zaten kilitli.");
  else setMonthCloseStep("lock", "", "Kilitli ayın vardiya ve puantajı yanlışlıkla değiştirilemez; Arşiv'den açılabilir.");
  el("#month-close-dialog").showModal();
}

async function runMonthCloseStep(action) {
  const month = state.attendanceMonth;
  if (state.attendanceDirty && action !== "check") {
    throw new Error("Önce puantajdaki kaydedilmemiş değişiklikleri kaydedin.");
  }
  if (action === "check") {
    const report = await request(`/api/reports/check?month=${encodeURIComponent(month)}`);
    const errors = report.counts?.error || 0;
    const warnings = report.counts?.warning || 0;
    setMonthCloseStep("check", errors || warnings ? "warning" : "done",
      errors || warnings
        ? `${errors} hata, ${warnings} uyarı bulundu. Ayrıntı için "Dönem kontrolü"nü açın; gerekirse düzeltin.`
        : "Hata veya uyarı yok.");
  } else if (action === "backup") {
    const result = await request("/api/backups", { method: "POST", body: JSON.stringify({}) });
    setMonthCloseStep("backup", "done", `Yedek alındı ve doğrulandı: ${result.backup.id}`);
    if (typeof loadBackups === "function") loadBackups().catch(() => {});
  } else if (action === "lock") {
    const step = el('#month-close-dialog [data-close-step="lock"]');
    if (step.dataset.status === "done") return;
    if (el('#month-close-dialog [data-close-step="backup"]').dataset.status !== "done"
      && !window.confirm("Yedek almadan kilitlensin mi?")) return;
    if (!window.confirm(`${month} dönemi kilitlensin mi?\n\nVardiya, puantaj ve kesişen izin kayıtları değiştirilemeyecek.`)) return;
    await request("/api/archive/period-lock", { method: "POST", body: JSON.stringify({ month, locked: true }) });
    setMonthCloseStep("lock", "done", "Dönem kilitlendi. Arşiv bölümünden kilidi açabilirsiniz.");
    loadArchive().catch(() => {});
  } else if (action === "export") {
    const step = el('#month-close-dialog [data-close-step="export"]');
    if (!(await downloadExcel("attendance"))) {
      if (step.dataset.status !== "done") setMonthCloseStep("export", step.dataset.payroll ? "warning" : "", "Puantaj Excel kaydı iptal edildi veya tamamlanmadı; tekrar deneyin.");
      return;
    }
    step.dataset.excel = "1";
    setMonthCloseStep("export", step.dataset.payroll ? "done" : "warning", step.dataset.payroll
      ? "Puantaj Excel'i ve bordro dosyası kaydedildi." : "Puantaj Excel çıktısı alındı. Bordro dosyasını da kaydedin.");
  } else if (action === "payroll") {
    const step = el('#month-close-dialog [data-close-step="export"]');
    if (!(await saveReportFile(`/api/reports/payroll?month=${month}&format=xlsx`, "xlsx", `BORDRO_${month}.xlsx`))) {
      if (step.dataset.status !== "done") setMonthCloseStep("export", step.dataset.excel ? "warning" : "", "Bordro kaydı iptal edildi; tekrar deneyin.");
      return;
    }
    step.dataset.payroll = "1";
    setMonthCloseStep("export", step.dataset.excel ? "done" : "warning", step.dataset.excel
      ? "Puantaj Excel'i ve bordro dosyası kaydedildi." : "Bordro dosyası kaydedildi. Puantaj Excel'ini de kaydedin.");
  }
}

function registerFormEvents() {
  registerReportEvents();
  const safeOpen = (action) => action().catch((error) => toast(error.message, true));
  el("#open-form-settings").addEventListener("click", () => safeOpen(openFormSettings));
  el("#open-forms").addEventListener("click", () => safeOpen(() => openForms()));
  el("#attendance-table").addEventListener("click", (event) => {
    if (window.easyAssistant) return;
    const cell = event.target.closest("input[data-action='attendance-code']");
    if (!cell || state.formatMode || !["G", "S", "Ü"].includes(cell.value.trim().toLocaleUpperCase("tr-TR"))) return;
    safeOpen(() => openForms(cell));
  });
  el("#attendance-table").addEventListener("keydown", (event) => {
    const cell = event.target.closest("input[data-action='attendance-code']");
    const formCodes = window.easyAssistant ? ["G", "S", "Ü", "R", "H", "E", "K"] : ["G", "S", "Ü"];
    if (event.key !== "Enter" || !cell || state.formatMode || !formCodes.includes(cell.value.trim().toLocaleUpperCase("tr-TR"))) return;
    event.preventDefault();
    if (window.easyAssistant && window.documentWorkflows) {
      safeOpen(() => Promise.resolve(window.documentWorkflows.openSelection(window.easyAssistant.selectedCells(), { editable: true })));
      return;
    }
    safeOpen(() => openForms(cell));
  });
  el("#form-edit-code").addEventListener("click", () => {
    const cell = state.formCell;
    el("#forms-dialog").close();
    if (cell?.isConnected) { cell.focus(); cell.select(); }
  });
  el("#form-category").addEventListener("change", () => { selectFormOptions(); previewForm(); });
  ["#form-person", "#form-start", "#form-end", "#form-template", "#form-section", "#form-description"].forEach((selector) => {
    el(selector).addEventListener("change", previewForm);
  });
  el("#form-save-draft").addEventListener("click", saveFormDraft);
  el("#form-load-draft").addEventListener("click", loadFormDraft);
  el("#form-print").addEventListener("click", () => {
    try {
      const frame = el("#form-preview-frame");
      if (!frame.contentWindow) throw new Error("Önizleme penceresi hazır değil.");
      frame.contentWindow.focus();
      frame.contentWindow.print();
    } catch (error) {
      toast(`Önizleme yazdırılamadı: ${error.message}`, true);
    }
  });
  el("#form-save").addEventListener("click", async () => {
    const button = el("#form-save");
    const payload = formPayload();
    const form = state.formTemplates.find((item) => item.id === payload.id);
    button.disabled = true;
    try {
      const environment = await request("/api/environment");
      let location;
      if (environment.desktop) {
        const result = await request("/api/forms/save", { method: "POST", body: JSON.stringify(payload) });
        if (result.cancelled) return;
        location = result.path;
      } else {
        const content = await request("/api/forms/generate", { method: "POST", body: JSON.stringify(payload) });
        location = await saveFormBlob(content, form.extension, `FORM.${form.extension}`);
        await request("/api/forms/history", {
          method: "POST",
          body: JSON.stringify({ ...payload, file_name: location }),
        });
      }
      el("#form-save-location").textContent = `Kaydedildi: ${location}`;
    } catch (error) {
      if (error.name !== "AbortError") toast(error.message, true);
    } finally { button.disabled = false; }
  });
  el("#form-settings-reset").addEventListener("click", resetFormSettings);
  el("#form-settings-list").addEventListener("click", async (event) => {
    const edit = event.target.closest("[data-form-replace]");
    const remove = event.target.closest("[data-form-delete]");
    const download = event.target.closest("[data-form-download]");
    const statusChange = event.target.closest("[data-form-status]");
    const id = edit?.dataset.formReplace || remove?.dataset.formDelete || download?.dataset.formDownload
      || statusChange?.dataset.formId;
    const form = state.formTemplates.find((item) => item.id === id);
    if (!form) return;
    try {
      if (statusChange) {
        const status = statusChange.dataset.formStatus;
        if (!window.confirm(`${form.name} şablonu ${status === "active" ? "kullanıma açılsın" : "kullanımdan kaldırılsın"} mı?`)) return;
        await request("/api/forms/status", { method: "POST", body: JSON.stringify({ id, status }) });
        await loadForms(); renderFormSettings();
      } else if (edit) {
        el("#form-settings-id").value = id;
        el("#form-settings-name").value = form.name;
        el("#form-settings-category").value = form.category;
        el("#form-settings-file").value = "";
        el("#form-settings-file").focus();
      } else if (remove) {
        if (!window.confirm(`${form.name} şablonu silinsin mi?`)) return;
        await request("/api/forms/delete", { method: "POST", body: JSON.stringify({ id }) });
        await loadForms(); renderFormSettings(); resetFormSettings();
      } else if (download) {
        const environment = await request("/api/environment");
        if (environment.desktop) {
          const result = await request("/api/forms/template-save", { method: "POST", body: JSON.stringify({ id }) });
          if (!result.cancelled) toast(`Şablon kaydedildi: ${result.path}`);
        } else {
          const blob = await request(`/api/forms/template?id=${encodeURIComponent(id)}`);
          await saveFormBlob(blob, form.extension, `SABLON.${form.extension}`);
        }
      }
    } catch (error) {
      if (error.name !== "AbortError") toast(error.message, true);
    }
  });
  el("#form-settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = el("#form-settings-file").files[0];
    if (!file || file.size > 5000000) { toast("En fazla 5 MB boyutunda bir şablon seçin.", true); return; }
    const button = event.currentTarget.querySelector("button[type='submit']");
    button.disabled = true;
    try {
      const data = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("Dosya okunamadı."));
        reader.readAsDataURL(file);
      });
      await request("/api/forms", { method: "POST", body: JSON.stringify({
        id: el("#form-settings-id").value, name: el("#form-settings-name").value,
        category: el("#form-settings-category").value, filename: file.name,
        content: data.split(",")[1],
      }) });
      await loadForms(); renderFormSettings(); resetFormSettings();
      toast("Form şablonu kaydedildi.");
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  });
}

function registerEvents() {
  registerOutputEvents();
  registerAttendanceEventEvents();
  registerAttendanceRosterEvents();
  registerFormEvents();
  el("#archive-list").addEventListener("click", handleArchiveClick);
  el("#archive-revision-select").addEventListener("change", updateArchiveRevisionDiff);
  el("#restore-archive-revision").addEventListener("click", restoreArchiveRevision);
  el("#restore-archive-cell").addEventListener("click", restoreArchiveCell);
  el("#close-archive-revisions").addEventListener("click", () => el("#archive-revision-dialog").close());
  el("#backup-list").addEventListener("click", handleBackupClick);
  el("#backup-retention-form").addEventListener("submit", saveBackupRetention);
  el("#create-backup").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await request("/api/backups", { method: "POST", body: JSON.stringify({}) });
      await loadBackups();
      toast("Yedek oluşturuldu ve doğrulandı.");
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
  document.querySelectorAll("[data-archive-kind]").forEach((button) => button.addEventListener("click", () => {
    state.archiveKind = button.dataset.archiveKind;
    document.querySelectorAll("[data-archive-kind]").forEach((tab) => {
      const active = tab === button;
      tab.setAttribute("aria-pressed", String(active));
      tab.classList.toggle("button-primary", active);
      tab.classList.toggle("button-outline", !active);
    });
    if (hasArchiveFilters()) applyArchiveFilters();
    else {
      state.archiveMatchMonths = null;
      renderArchive();
    }
  }));
  el("#apply-archive-filter").addEventListener("click", applyArchiveFilters);
  el("#clear-archive-filter").addEventListener("click", () => {
    el("#archive-filter-month").value = "";
    el("#archive-filter-person").value = "";
    el("#archive-filter-section").value = "";
    el("#archive-filter-type").value = "";
    el("#archive-filter-status").textContent = "";
    state.archiveMatchMonths = null;
    renderArchive();
  });
  el("#schedule-month").value = state.scheduleMonth;
  el("#attendance-month").value = state.attendanceMonth;
  el("#attendance-compare-month").value = previousMonth(state.attendanceMonth);
  const updateAttendancePeriodLabel = () => {
    const [year, month] = state.attendanceMonth.split("-").map(Number);
    el("#attendance-period-label").textContent = `${MONTH_NAMES[month - 1]} ${year}`;
  };
  updateAttendancePeriodLabel();
  document.querySelectorAll(".nav-tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
  el("#schedule-month").addEventListener("change", async (event) => {
    if (!(await resolveUnsaved("schedule", "Ay değiştirilmeden"))) {
      event.target.value = state.scheduleMonth;
      return;
    }
    state.scheduleMonth = event.target.value;
    state.weekIndex = 0;
    try { await loadSchedule(); } catch (error) { toast(error.message, true); }
  });
  el("#schedule-week").addEventListener("change", (event) => selectScheduleWeek(Number(event.target.value)));
  el("#schedule-comparison-panel").addEventListener("toggle", (event) => {
    if (event.currentTarget.open) populateScheduleComparisonOptions();
  });
  el("#schedule-compare-run").addEventListener("click", renderScheduleComparison);
  el("#schedule-search").addEventListener("input", (event) => {
    state.scheduleFilters.query = event.target.value.trim().toLocaleLowerCase("tr-TR");
    renderSchedule();
  });
  el("#schedule-section-filter").addEventListener("change", (event) => {
    state.scheduleFilters.section = event.target.value;
    renderSchedule();
  });
  el("#schedule-kind-filter").addEventListener("change", (event) => {
    state.scheduleFilters.kind = event.target.value;
    renderSchedule();
  });
  el("#schedule-sort").addEventListener("change", (event) => {
    state.scheduleFilters.sort = event.target.value;
    renderSchedule();
  });
  el("#schedule-min-rest-hours").addEventListener("change", saveScheduleValidationSettings);
  el("#clear-schedule-filters").addEventListener("click", () => {
    state.scheduleFilters = { query: "", section: "", kind: "", sort: "slot" };
    el("#schedule-search").value = "";
    el("#schedule-section-filter").value = "";
    el("#schedule-kind-filter").value = "";
    el("#schedule-sort").value = "slot";
    renderSchedule();
  });
  el("#previous-week").addEventListener("click", () => selectScheduleWeek(state.weekIndex - 1));
  el("#next-week").addEventListener("click", () => selectScheduleWeek(state.weekIndex + 1));
  el("#copy-previous-week").addEventListener("click", copyPreviousWeek);
  el("#copy-previous-month").addEventListener("click", copyPreviousMonth);
  el("#suggest-week").addEventListener("click", () => suggestWeek().catch((error) => toast(error.message, true)));
  el("#schedule-template-form").addEventListener("submit", saveScheduleTemplate);
  el("#schedule-template-select").addEventListener("change", (event) => {
    renderScheduleTemplateOptions(event.target.value);
  });
  el("#apply-schedule-template").addEventListener("click", applyScheduleTemplate);
  el("#delete-schedule-template").addEventListener("click", deleteScheduleTemplate);
  el("#open-bulk-schedule-dialog").addEventListener("click", openBulkScheduleDialog);
  el("#bulk-schedule-form").addEventListener("submit", applyBulkScheduleAssignment);
  el("#bulk-schedule-form").addEventListener("change", renderBulkSchedulePreview);
  el("#bulk-schedule-select-all").addEventListener("click", () => {
    const boxes = [...document.querySelectorAll('input[name="bulk-schedule-person"]')];
    const shouldSelect = boxes.some((box) => !box.checked);
    boxes.forEach((box) => { box.checked = shouldSelect; });
    updateBulkScheduleSelectAll();
    renderBulkSchedulePreview();
  });
  el("#bulk-schedule-people").addEventListener("change", () => {
    updateBulkScheduleSelectAll();
    renderBulkSchedulePreview();
  });
  el("#open-extra-personnel").addEventListener("click", () => {
    if (!state.schedule) return;
    const section = el("#extra-personnel-section").value;
    const row = openExtraScheduleSlot(section);
    if (row === null) {
      toast(`${section} bölümündeki ${SECTION_LIMITS[section]} satırın tamamı açık. Daha fazlası için Araçlar › Bölümler ve satırlar.`, true);
      return;
    }
    renderSchedule();
    const cell = el(`#schedule-table td[data-source-row="${row}"]`);
    if (cell) cell.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
    toast(`${section} için ek satır açıldı. Boş bırakılırsa Excel'e eklenmez.`);
  });
  el("#attendance-month").addEventListener("change", async (event) => {
    if (!(await resolveUnsaved("attendance", "Ay değiştirilmeden"))) {
      event.target.value = state.attendanceMonth;
      return;
    }
    state.attendanceMonth = event.target.value;
    updateAttendancePeriodLabel();
    try { await loadAttendance(); } catch (error) { toast(error.message, true); }
  });
  el("#schedule-sheet-tabs").addEventListener("click", async (event) => {
    const tab = event.target.closest("[data-week]");
    if (!tab) return;
    await selectScheduleWeek(Number(tab.dataset.week));
  });
  el("#shift-palette").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-preset-id], button[data-action='clear-brush'], button[data-free-edit]");
    if (!button) return;
    if (button.dataset.freeEdit) state.brush = null;
    else if (button.dataset.action === "clear-brush") state.brush = { id: "clear" };
    else state.brush = quickEntryByKey(button.dataset.presetId);
    el("#shift-palette").querySelectorAll(".shift-chip").forEach((chip) =>
      chip.classList.toggle("active", chip === button));
    toast(state.brush
      ? "Seçeneği hücreye sürükleyin veya seçiliyse hücreye tıklayarak uygulayın."
      : "Serbest düzenleme etkin.");
  });
  el("#shift-palette").addEventListener("dragstart", (event) => {
    const button = event.target.closest("button[data-preset-id]");
    if (!button) return;
    event.dataTransfer.setData("text/plain", button.dataset.presetId);
    event.dataTransfer.effectAllowed = "copy";
    button.classList.add("dragging");
  });
  el("#shift-palette").addEventListener("dragend", (event) => {
    event.target.closest("button[data-preset-id]")?.classList.remove("dragging");
  });
  el("#schedule-table").addEventListener("change", handleScheduleChange);
  el("#schedule-table").addEventListener("click", handleScheduleClick);
  el("#schedule-table").addEventListener("dblclick", handleScheduleDoubleClick);
  el("#schedule-table").addEventListener("dragover", (event) => {
    const cell = event.target.closest("td[data-shift-day]");
    if (!cell) return;
    event.preventDefault();
    cell.classList.add("drag-target");
  });
  el("#schedule-table").addEventListener("dragleave", (event) => {
    const cell = event.target.closest("td[data-shift-day]");
    if (cell && !cell.contains(event.relatedTarget)) cell.classList.remove("drag-target");
  });
  el("#schedule-table").addEventListener("drop", handleScheduleDrop);
  el("#open-bulk-person-dialog").addEventListener("click", () => {
    el("#bulk-person-dialog").showModal();
    el("#bulk-person-names").focus();
  });
  el("#bulk-person-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const names = el("#bulk-person-names").value.split(/\r?\n/)
      .map((name) => name.trim()).filter(Boolean);
    if (!names.length) {
      toast("Eklemek için en az bir personel adı yazın.", true);
      return;
    }
    const submit = event.currentTarget.querySelector("button[type='submit']");
    submit.disabled = true;
    try {
      const result = await request("/api/people/bulk", {
        method: "POST",
        body: JSON.stringify({ names }),
      });
      state.people.push(...result.people);
      state.people.sort((a, b) => Number(b.active) - Number(a.active) || a.name.localeCompare(b.name, "tr"));
      renderPersonSelectors();
      renderSchedule();
      el("#bulk-person-names").value = "";
      el("#bulk-person-dialog").close();
      toast(`${result.people.length} personel listeye eklendi. Çizelgede boş satırdan seçebilirsiniz.`);
    } catch (error) {
      toast(error.message, true);
    } finally {
      submit.disabled = false;
    }
  });
  el("#open-quick-entry-settings").addEventListener("click", () => {
    resetQuickEntryForm();
    state.quickEntryColorDraft = { ...state.workbookSettings.quick_colors };
    renderQuickEntrySettings();
    el("#quick-entry-settings-dialog").showModal();
  });
  el("#quick-entry-settings-dialog").addEventListener("close", discardQuickEntryColorDraft);
  el("#quick-entry-form").addEventListener("submit", saveQuickEntry);
  el("#reset-quick-entry-form").addEventListener("click", resetQuickEntryForm);
  el("#custom-quick-entry-list").addEventListener("click", (event) => {
    const edit = event.target.closest("[data-edit-quick-entry]");
    const remove = event.target.closest("[data-delete-quick-entry]");
    const toggle = event.target.closest("[data-toggle-quick-entry]");
    const reset = event.target.closest("[data-reset-quick-entry]");
    if (edit) editQuickEntry(edit.dataset.editQuickEntry);
    else if (remove) deleteQuickEntry(remove.dataset.deleteQuickEntry);
    else if (toggle) togglePresetHidden(toggle.dataset.toggleQuickEntry);
    else if (reset) resetPresetEntry(reset.dataset.resetQuickEntry);
  });
  ["#quick-entry-start", "#quick-entry-end", "#quick-entry-shift-type"].forEach((selector) =>
    el(selector).addEventListener("input", updateQuickEntryTypePreview));
  ["#quick-entry-start", "#quick-entry-end"].forEach((selector) =>
    el(selector).addEventListener("change", (event) => {
      const parsed = parseTimeText(event.target.value);
      if (parsed) event.target.value = displayTime(parsed, selector.endsWith("end"));
      updateQuickEntryTypePreview();
    }));
  el("#quick-entry-color-list").addEventListener("change", async (event) => {
    const input = event.target.closest("[data-preset-color]");
    if (!input) return;
    previewQuickEntryColor(input.dataset.presetColor, input.value);
  });
  el("#quick-entry-color-list").addEventListener("click", async (event) => {
    if (event.target.closest("[data-save-quick-entry-colors]")) {
      try { await saveQuickEntryColorDraft(); }
      catch (error) { toast(error.message, true); }
      return;
    }
    if (event.target.closest("[data-cancel-quick-entry-colors]")) {
      discardQuickEntryColorDraft();
      return;
    }
    const button = event.target.closest("[data-preset-color-pick]");
    if (!button) return;
    previewQuickEntryColor(button.dataset.presetColorPick, button.dataset.color);
  });
  el("#cell-quick-entry-options").addEventListener("click", (event) => {
    const button = event.target.closest("[data-cell-preset]");
    if (!button || !state.quickDialogTarget) return;
    const entry = button.dataset.cellPreset === "clear"
      ? { id: "clear" }
      : quickEntryByKey(button.dataset.cellPreset);
    if (entry && entry.id !== "clear") {
      const row = resolveScheduleTarget(state.quickDialogTarget);
      if (row) applyQuickEntryToCell(row, state.quickDialogTarget.day, entry);
    } else if (entry?.id === "clear" && state.quickDialogTarget.rowId) {
      const row = state.schedule?.entries.find((item) => item.row_id === state.quickDialogTarget.rowId);
      if (row) applyQuickEntryToCell(row, state.quickDialogTarget.day, entry);
    }
    state.quickDialogTarget = null;
    el("#cell-quick-entry-dialog").close();
  });
  el("#copy-schedule-day").addEventListener("click", copyScheduleDay);
  el("#paste-schedule-day").addEventListener("click", pasteScheduleDay);
  el("#copy-schedule-week").addEventListener("click", copyScheduleWeek);
  el("#paste-schedule-week").addEventListener("click", pasteScheduleWeek);
  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = el(`#${button.dataset.closeDialog}`);
      if (dialog?.open) dialog.close();
    });
  });
  el("#save-schedule").addEventListener("click", saveSchedule);
  el("#undo-schedule").addEventListener("click", () => moveEditHistory("schedule", "undo"));
  el("#redo-schedule").addEventListener("click", () => moveEditHistory("schedule", "redo"));
  el("#schedule-table").addEventListener("keydown", handleScheduleKeyboard);
  document.addEventListener("keydown", handleGlobalShortcuts);
  el("#user-name-form").addEventListener("submit", saveUserName);
  el("#open-schedule-check").addEventListener("click", () => openPeriodCheck("schedule"));
  el("#open-attendance-check").addEventListener("click", () => openPeriodCheck("attendance"));
  el("#period-check-content").addEventListener("click", handlePeriodCheckClick);
  el("#schedule-period-check-flow").addEventListener("click", handlePeriodCheckFlowClick);
  el("#attendance-period-check-flow").addEventListener("click", handlePeriodCheckFlowClick);
  el("#open-guide").addEventListener("click", () => el("#guide-dialog").showModal());
  function openPersonEntry() {
    const guide = el(".easy-guide");
    window.requestAnimationFrame(() => {
      if (guide) guide.open = true;
      else el("#schedule-view .person-panel").open = true;
      el("#new-person-name").focus();
    });
  }
  el("#attendance-empty").addEventListener("click", async (event) => {
    const action = event.target.closest("[data-empty-action]")?.dataset.emptyAction;
    if (action === "clear-filters") {
      state.attendanceQuery = "";
      state.attendanceFilter = "all";
      el("#attendance-search").value = "";
      el("#attendance-filter").value = "all";
      renderAttendance();
    } else if (action === "add-person") {
      await switchView("schedule");
      if (state.view !== "schedule") return;
      openPersonEntry();
    }
  });
  el("#schedule-empty").addEventListener("click", (event) => {
    const action = event.target.closest("[data-empty-action]")?.dataset.emptyAction;
    if (action === "clear-schedule-filters") {
      el("#clear-schedule-filters").click();
    } else if (action === "add-person") {
      openPersonEntry();
    }
  });
  el("#attendance-table").addEventListener("change", handleAttendanceEdit);
el("#attendance-table").addEventListener("dblclick", (event) => {
  const input = event.target.closest?.('[data-action="attendance-overtime"]');
  if (input) showOvertimeExplanation(input.dataset.person);
});
  el("#attendance-table").addEventListener("keydown", (event) => {
    moveWorkbookFocus(event, 'input[data-action="attendance-code"]');
  });
  el("#attendance-warnings").addEventListener("click", handleAttendanceWarningClick);
  el("#attendance-search").addEventListener("input", (event) => {
    state.attendanceQuery = event.target.value;
    if (state.attendance) renderAttendance();
  });
  el("#attendance-filter").addEventListener("change", (event) => {
    state.attendanceFilter = event.target.value;
    if (state.attendance) renderAttendance();
  });
  el("#compare-attendance-months").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const result = el("#attendance-comparison-result");
    button.disabled = true;
    result.textContent = "Karşılaştırma yükleniyor...";
    try { await renderAttendancePeriodComparison(); }
    catch (error) {
      result.textContent = error.message || "Dönem karşılaştırması yüklenemedi.";
      toast(error.message, true);
    } finally { button.disabled = false; }
  });
  el("#save-attendance").addEventListener("click", saveAttendance);
  el("#undo-attendance").addEventListener("click", () => moveEditHistory("attendance", "undo"));
  el("#redo-attendance").addEventListener("click", () => moveEditHistory("attendance", "redo"));
  el("#save-overtime-settings").addEventListener("click", saveOvertimeSettings);
  el("#attendance-balance-report").addEventListener("click", async (event) => {
    const ledgerButton = event.target.closest("[data-balance-ledger]");
    if (ledgerButton) {
      await showLeaveBalanceLedger(ledgerButton);
      return;
    }
    const button = event.target.closest("[data-balance-save]");
    if (button) await saveBalanceOpening(button);
  });
  el("#export-schedule").addEventListener("click", () => downloadExcel("schedule"));
  el("#export-attendance").addEventListener("click", () => downloadExcel("attendance"));
  el("#add-person-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const nameInput = el("#new-person-name");
    try {
      const result = await request("/api/people", { method: "POST", body: JSON.stringify({ name: nameInput.value }) });
      state.people.push(result.person);
      state.people.sort((a, b) => Number(b.active) - Number(a.active) || a.name.localeCompare(b.name, "tr"));
      renderPersonSelectors();
      renderSchedule();
      nameInput.value = "";
      nameInput.focus();
      toast("Personel listeye eklendi. Çizelgede boş satırdan seçebilirsiniz.");
    } catch (error) {
      toast(error.message, true);
    }
  });
  el("#deactivate-person").addEventListener("click", async () => {
    const personId = Number(el("#existing-person").value);
    if (!personId) {
      toast("Önce pasife alınacak personeli seçin.", true);
      return;
    }
    const person = state.people.find((item) => item.id === personId);
    if (!person || !window.confirm(`${person.name} personeli pasife alınsın mı? Geçmiş vardiya kayıtları korunur.`)) return;
    try {
      await request(`/api/people/${personId}/active`, {
        method: "POST",
        body: JSON.stringify({ active: false }),
      });
      person.active = false;
      renderPersonSelectors();
      toast("Personel pasife alındı. Geçmiş kayıtları korundu.");
      if (state.view === "schedule") renderSchedule();
    } catch (error) {
      toast(error.message, true);
    }
  });
  el("#existing-person").addEventListener("change", updatePersonActions);
  el("#delete-person").addEventListener("click", deleteSelectedPerson);
  el("#activate-person").addEventListener("click", async () => {
    const person = state.people.find((item) => item.id === Number(el("#existing-person").value));
    if (!person) return;
    try {
      await request(`/api/people/${person.id}/active`, {
        method: "POST",
        body: JSON.stringify({ active: true }),
      });
      person.active = true;
      renderPersonSelectors();
      toast(`${person.name} yeniden aktif edildi.`);
      if (state.view === "schedule") renderSchedule();
    } catch (error) {
      toast(error.message, true);
    }
  });
}

function setupCompactRibbon() {
  for (const kind of ["schedule", "attendance"]) {
    const view = el(`#${kind}-view`);
    const ribbon = view.querySelector(".control-strip");
    ribbon.setAttribute("aria-label", "Uygulama araç şeridi, diğer araçlar için yatay kaydırın");
    ribbon.setAttribute("tabindex", "0");
    for (const panel of view.querySelectorAll(".person-panel, .quick-panel, .filter-panel, .format-panel")) ribbon.append(panel);
    const dock = document.createElement("div");
    dock.className = "ribbon-panel-dock";
    ribbon.after(dock);
    ribbon.querySelectorAll("details").forEach((panel, index) => {
      const content = [...panel.children].find((child) => child.tagName !== "SUMMARY");
      content.id ||= `${kind}-ribbon-panel-${index}`;
      panel.querySelector("summary").setAttribute("aria-controls", content.id);
      panel.addEventListener("toggle", () => {
        if (panel.open) {
          ribbon.querySelectorAll("details").forEach((other) => {
            if (other !== panel && !other.contains(panel)) other.open = false;
          });
          dock.append(content);
        } else {
          panel.append(content);
        }
      });
    });
  }
  el("#schedule-view .control-strip").prepend(el(".main-nav"), el("#save-state"));
  document.body.classList.add("compact-ribbon-ready");
}

async function start() {
  const initializationErrors = [];
  for (const [label, initialize] of [
    ["İçe aktarma", registerImportEvents],
    ["Biçimlendirme araçları", installFormatToolbars],
    ["Şablon araçları", installTableTemplatePage],
    ["Uygulama araçları", registerEvents],
    ["Araç şeridi", setupCompactRibbon],
  ]) {
    try {
      initialize();
    } catch (error) {
      console.error(`${label} başlatılamadı`, error);
      const detail = error instanceof Error ? error.message : String(error);
      initializationErrors.push(`${label}: ${detail || "beklenmeyen hata"}`);
      if (label === "İçe aktarma") {
        const importButton = el("#open-import");
        if (importButton) {
          importButton.disabled = true;
          importButton.title = `İçe aktarma kullanılamıyor: ${detail || "beklenmeyen hata"}`;
        }
      }
    }
  }
  if (initializationErrors.length) {
    toast(`Bazı araçlar başlatılamadı. ${initializationErrors.join(" · ")}`, true);
  }
  try {
    // Birbirinden bağımsız açılış istekleri paralel yüklenir (hızlı açılış).
    const [workbookSettings] = await Promise.all([
      request("/api/workbook-settings"),
      request("/api/ui-settings").then((result) => {
        state.uiSettings = result.settings;
        document.dispatchEvent(new CustomEvent("ui-settings-changed"));
      }).catch((error) => console.warn("Görünüm ayarları yüklenemedi", error)),
      loadScheduleSections(),
    ]);
    state.workbookSettings = workbookSettings;
    const [, , , validationSettings] = await Promise.all([
      loadPeople(),
      loadQuickEntries(),
      loadScheduleTemplates(),
      request("/api/schedule-validation-settings"),
    ]);
    state.scheduleValidationSettings = validationSettings;
    el("#schedule-min-rest-hours").value = String(validationSettings.minimum_rest_hours);
    renderScheduleValidationStatus();
    await loadSchedule();
  } catch (error) {
    toast(error.message, true);
  } finally {
    const loader = el("#app-loader");
    if (loader) {
      loader.classList.add("done");
      setTimeout(() => loader.remove(), 400);
    }
  }
}

start();
