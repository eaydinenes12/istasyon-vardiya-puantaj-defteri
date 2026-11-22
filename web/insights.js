/* Aşama 6: rapor filtre hafızası, ay grafikleri, kişisel vardiya kartı, görev panosu ve devir notları. */

/* ---------- Rapor filtre hafızası ---------- */

const FILTER_MEMORY_KEY = "vp.reportFilters";
const FILTER_MEMORY_IDS = ["report-person", "report-program-person", "history-action", "balancing-weeks",
  "pdks-tolerance", "chart-month-kind", "personal-card-person"];
const filterMemoryDispatched = new Map();
let filterMemoryRestoring = false;

function filterMemoryAll() {
  try { return JSON.parse(localStorage.getItem(FILTER_MEMORY_KEY) || "{}") || {}; } catch { return {}; }
}

function filterMemoryGet(id, fallback = "") {
  const value = filterMemoryAll()[id];
  return value === undefined ? fallback : value;
}

function filterMemorySet(id, value) {
  const all = filterMemoryAll();
  all[id] = value;
  try { localStorage.setItem(FILTER_MEMORY_KEY, JSON.stringify(all)); } catch { /* depolama kapalı */ }
}

function filterMemoryRestore(root = document) {
  const saved = filterMemoryAll();
  for (const id of FILTER_MEMORY_IDS) {
    const element = root.querySelector ? root.querySelector(`#${id}`) || (root.id === id ? root : null) : null;
    if (!element || element.dataset.memoryRestored || saved[id] === undefined) continue;
    const value = String(saved[id]);
    if (element.tagName === "SELECT" && ![...element.options].some((option) => option.value === value)) continue;
    element.dataset.memoryRestored = "1";
    if (element.value === value) continue;
    element.value = value;
    const last = filterMemoryDispatched.get(id) || 0;
    if (Date.now() - last > 1500) {
      filterMemoryDispatched.set(id, Date.now());
      filterMemoryRestoring = true;
      try { element.dispatchEvent(new Event("change", { bubbles: true })); } finally { filterMemoryRestoring = false; }
    }
  }
}

document.addEventListener("change", (event) => {
  const target = event.target;
  if (target && FILTER_MEMORY_IDS.includes(target.id) && !filterMemoryRestoring) {
    filterMemorySet(target.id, target.value);
  }
}, true);

if (typeof MutationObserver !== "undefined") {
  let pending = false;
  new MutationObserver(() => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; filterMemoryRestore(document); });
  }).observe(document.documentElement, { childList: true, subtree: true });
}

/* ---------- Ay grafikleri ---------- */

const INSIGHT_CODE_COLORS = {
  X: "#237b55", "G.Ç": "#4f8fbf", H: "#9aa7a0", S: "#d69a2d", R: "#c4574e", "Ü": "#8d6bb3", Y: "#5fb39a",
};

function insightHours(value) {
  return (Math.round((Number(value) || 0) * 10) / 10).toLocaleString("tr-TR");
}

function insightBarChart(items, { unit = "saat", max = 0, stacked = null } = {}) {
  if (!items.length) return '<p class="archive-empty">Bu ay için veri yok.</p>';
  const top = max || Math.max(1, ...items.map((item) => item.value));
  const rowHeight = 26;
  const labelWidth = 170;
  const width = 640;
  const barWidth = width - labelWidth - 70;
  const height = items.length * rowHeight + 10;
  const bars = items.map((item, index) => {
    const y = index * rowHeight + 4;
    const total = Math.max(0, item.value) / top * barWidth;
    const extra = stacked && item[stacked] ? Math.min(total, item[stacked] / top * barWidth) : 0;
    return `<g class="insight-bar"><title>${escapeHtml(item.label)}: ${insightHours(item.value)} ${unit}${extra ? ` (FM ${insightHours(item[stacked])})` : ""}</title>
      <text x="${labelWidth - 8}" y="${y + 15}" text-anchor="end">${escapeHtml(item.label.length > 24 ? `${item.label.slice(0, 23)}…` : item.label)}</text>
      <rect x="${labelWidth}" y="${y}" width="${Math.max(1, total - extra).toFixed(1)}" height="${rowHeight - 8}" rx="3" class="insight-bar-main"></rect>
      ${extra ? `<rect x="${(labelWidth + total - extra).toFixed(1)}" y="${y}" width="${extra.toFixed(1)}" height="${rowHeight - 8}" rx="3" class="insight-bar-extra"></rect>` : ""}
      <text x="${(labelWidth + total + 6).toFixed(1)}" y="${y + 15}" class="insight-bar-value">${insightHours(item.value)}</text></g>`;
  }).join("");
  return `<svg class="insight-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Çubuk grafik">${bars}</svg>`;
}

function insightCodeChart(totals) {
  const entries = Object.entries(totals).filter(([, count]) => count > 0).sort((a, b) => b[1] - a[1]);
  const sum = entries.reduce((total, [, count]) => total + count, 0);
  if (!sum) return '<p class="archive-empty">Bu ay için kod yok.</p>';
  let x = 0;
  const segments = entries.map(([code, count]) => {
    const width = count / sum * 600;
    const color = INSIGHT_CODE_COLORS[code] || "#7d8f86";
    const part = `<rect x="${x.toFixed(1)}" y="0" width="${width.toFixed(1)}" height="28" fill="${color}"><title>${escapeHtml(code)}: ${count} gün</title></rect>`;
    x += width;
    return part;
  }).join("");
  const legend = entries.map(([code, count]) => `<li><span class="insight-swatch" style="background:${INSIGHT_CODE_COLORS[code] || "#7d8f86"}"></span>${escapeHtml(code)} <strong>${count}</strong> <small>(%${Math.round(count / sum * 100)})</small></li>`).join("");
  return `<svg class="insight-chart insight-stack" viewBox="0 0 600 28" role="img" aria-label="Kod dağılımı">${segments}</svg><ul class="insight-legend">${legend}</ul>`;
}

function insightDailyChart(dates, rows) {
  if (!dates.length) return "";
  const counts = dates.map((day) => rows.reduce((total, row) => total + (["X", "G.Ç", "Y"].includes((row.codes || {})[day]) ? 1 : 0), 0));
  const top = Math.max(1, ...counts);
  const width = 640;
  const height = 150;
  const step = (width - 40) / dates.length;
  const bars = counts.map((count, index) => {
    const barHeight = count / top * (height - 40);
    const date = new Date(`${dates[index]}T12:00:00`);
    const weekend = date.getDay() === 0 || date.getDay() === 6;
    return `<g><title>${dates[index]}: ${count} kişi çalışıyor</title>
      <rect x="${(30 + index * step + 1).toFixed(1)}" y="${(height - 22 - barHeight).toFixed(1)}" width="${Math.max(2, step - 2).toFixed(1)}" height="${barHeight.toFixed(1)}" rx="2" class="${weekend ? "insight-day-weekend" : "insight-day"}"></rect>
      <text x="${(30 + index * step + step / 2).toFixed(1)}" y="${height - 8}" text-anchor="middle" class="insight-axis">${date.getDate()}</text></g>`;
  }).join("");
  return `<svg class="insight-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Günlük çalışan sayısı">
    <text x="4" y="14" class="insight-axis">${top}</text><line x1="28" x2="${width}" y1="${height - 22}" y2="${height - 22}" class="insight-axis-line"></line>${bars}</svg>`;
}

async function openMonthCharts(month = state.attendanceMonth || state.scheduleMonth) {
  const data = await request(`/api/attendance?month=${encodeURIComponent(month)}`);
  const attendance = data.attendance || data;
  const rows = attendance.rows || [];
  const dates = attendance.dates || [];
  const dialog = extrasDialog("month-charts-dialog", `Grafikler · ${extrasMonthLabel(month)}`,
    "Puantajdan hesaplanır; çubukların üzerine gelince ayrıntı görünür.");
  const body = dialog.querySelector(".extras-body");
  const codeTotals = {};
  rows.forEach((row) => Object.values(row.codes || {}).forEach((code) => { if (code) codeTotals[code] = (codeTotals[code] || 0) + 1; }));
  const hourItems = rows.map((row) => ({
    label: row.name, value: Math.max(0, (row.worked_hours || 0) - (row.break_hours || 0)), overtime: row.overtime_hours || 0,
  })).sort((a, b) => b.value - a.value);
  const overtimeItems = rows.filter((row) => row.overtime_hours > 0)
    .map((row) => ({ label: row.name, value: row.overtime_hours })).sort((a, b) => b.value - a.value);
  const totalHours = hourItems.reduce((total, item) => total + item.value, 0);
  const totalOvertime = overtimeItems.reduce((total, item) => total + item.value, 0);
  body.innerHTML = `<div class="insight-kpis">
      <div><span>Personel</span><strong>${rows.length}</strong></div>
      <div><span>Net çalışma</span><strong>${insightHours(totalHours)} sa</strong></div>
      <div><span>Fazla mesai</span><strong>${insightHours(totalOvertime)} sa</strong></div>
      <div><span>Kişi başı ortalama</span><strong>${insightHours(rows.length ? totalHours / rows.length : 0)} sa</strong></div>
    </div>
    <label class="extras-search insight-select"><span>Grafik</span><select id="chart-month-kind">
      <option value="hours">Kişi başı net çalışma (FM vurgulu)</option>
      <option value="overtime">Fazla mesai sıralaması</option>
      <option value="codes">Puantaj kod dağılımı</option>
      <option value="daily">Günlük çalışan sayısı</option>
    </select></label>
    <div class="insight-panel" id="chart-month-panel"></div>`;
  const panel = body.querySelector("#chart-month-panel");
  const select = body.querySelector("#chart-month-kind");
  const paint = () => {
    const kind = select.value;
    panel.innerHTML = kind === "overtime" ? insightBarChart(overtimeItems)
      : kind === "codes" ? insightCodeChart(codeTotals)
      : kind === "daily" ? insightDailyChart(dates, rows)
      : `${insightBarChart(hourItems, { stacked: "overtime" })}<p class="insight-note"><span class="insight-swatch insight-swatch-main"></span>Net çalışma <span class="insight-swatch insight-swatch-extra"></span>Bunun fazla mesai kısmı</p>`;
  };
  select.addEventListener("change", paint);
  paint();
  showExtras(dialog);
}

/* ---------- Kişisel vardiya kartı (PNG) ---------- */

const PERSONAL_KIND_STYLE = {
  work: ["#e6f4ed", "#1d5e3b"], weekly_off: ["#eef1ef", "#55665d"], overnight_exit: ["#e7f0f8", "#2f5f86"],
  annual_leave: ["#fff3dc", "#8a5300"], sick_leave: ["#fbe9e7", "#a4352e"], unpaid_leave: ["#f1ebf8", "#5e4486"],
};

function personalDayText(item) {
  if (!item) return ["", ""];
  if (item.kind === "work") return [`${item.start}–${item.end}`, ""];
  return ["", ""];
}

function personalCardCanvas(name, month, days, title, revision) {
  const [year, monthNumber] = month.split("-").map(Number);
  const first = new Date(year, monthNumber - 1, 1);
  const count = new Date(year, monthNumber, 0).getDate();
  const offset = (first.getDay() + 6) % 7;
  const weeks = Math.ceil((offset + count) / 7);
  const cell = 150;
  const header = 180;
  const canvas = document.createElement("canvas");
  canvas.width = cell * 7 + 40;
  canvas.height = header + 36 + weeks * (cell * 0.72) + 60;
  const ctx = canvas.getContext("2d");
  const rowHeight = cell * 0.72;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#1d5e3b";
  ctx.fillRect(0, 0, canvas.width, 110);
  ctx.fillStyle = "#ffffff";
  ctx.font = "bold 38px Segoe UI, Arial, sans-serif";
  ctx.fillText(name, 24, 56);
  ctx.font = "22px Segoe UI, Arial, sans-serif";
  ctx.fillText(`Görev: ${title || "Belirtilmemiş"}`, 24, 92);
  ctx.fillText(`${extrasMonthLabel(month)} vardiya programı`, 24, 124);
  const work = Object.values(days).filter((item) => item.kind === "work").length;
  ctx.fillStyle = "#20362e";
  ctx.font = "20px Segoe UI, Arial, sans-serif";
  ctx.fillText(`${work} çalışma günü`, 24, 158);
  const names = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];
  ctx.font = "bold 18px Segoe UI, Arial, sans-serif";
  names.forEach((label, index) => { ctx.fillStyle = index >= 5 ? "#a4352e" : "#55665d"; ctx.fillText(label, 20 + index * cell + 8, header + 24); });
  for (let day = 1; day <= count; day += 1) {
    const position = offset + day - 1;
    const x = 20 + (position % 7) * cell;
    const y = header + 36 + Math.floor(position / 7) * rowHeight;
    const iso = `${month}-${String(day).padStart(2, "0")}`;
    const item = days[iso];
    const [fill, ink] = item ? (item.fill_color && item.kind === "work" ? [item.fill_color, "#102a3d"] : PERSONAL_KIND_STYLE[item.kind] || ["#f2f5f2", "#20362e"]) : ["#fafbfa", "#9aa7a0"];
    ctx.fillStyle = fill;
    ctx.fillRect(x + 3, y + 3, cell - 6, rowHeight - 6);
    ctx.strokeStyle = "#dce5df";
    ctx.strokeRect(x + 3, y + 3, cell - 6, rowHeight - 6);
    ctx.fillStyle = "#55665d";
    ctx.font = "bold 18px Segoe UI, Arial, sans-serif";
    ctx.fillText(String(day), x + 12, y + 26);
    const [main, sub] = personalDayText(item);
    ctx.fillStyle = ink;
    ctx.font = "bold 20px Segoe UI, Arial, sans-serif";
    ctx.fillText(main.length > 13 ? `${main.slice(0, 12)}…` : main, x + 12, y + 60);
    if (sub) {
      ctx.font = "16px Segoe UI, Arial, sans-serif";
      ctx.fillText(sub.length > 16 ? `${sub.slice(0, 15)}…` : sub, x + 12, y + 84);
    }
  }
  ctx.fillStyle = "#64766e";
  ctx.font = "15px Segoe UI, Arial, sans-serif";
  ctx.fillText(`Vardiya Puantaj · Plan sürümü ${revision.slice(0, 8)} · ${new Date().toLocaleDateString("tr-TR")}`, 24, canvas.height - 22);
  return canvas;
}

function personalDays(entries, employeeId) {
  const days = {};
  entries.filter((entry) => String(entry.employee_id) === String(employeeId))
    .forEach((entry) => Object.entries(entry.days || {}).forEach(([day, item]) => {
      if (item.kind === "work" && (!days[day] || days[day].kind !== "work")) {
        days[day] = { kind: "work", start: item.start || "", end: item.end || "" };
      }
    }));
  return days;
}

function personalCardClipboardText(name, title, month, days, revision) {
  const workLines = Object.entries(days)
    .filter(([, item]) => item.kind === "work")
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([day, item]) => {
      const label = new Intl.DateTimeFormat("tr-TR", { day: "2-digit", month: "long" })
        .format(new Date(`${day}T12:00:00`));
      return `${label}: ${item.start}–${item.end}`;
    });
  return [
    `Personel: ${name}`,
    `Görev: ${title || "Belirtilmemiş"}`,
    `Dönem: ${extrasMonthLabel(month)}`,
    `Plan sürümü: ${revision.slice(0, 8)}`,
    "",
    ...workLines,
  ].join("\n");
}

function personalSafeName(text) {
  return text.normalize("NFKD").replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, "_") || "personel";
}

async function openPersonalCard(month = state.scheduleMonth || state.attendanceMonth, employeeId = "") {
  const data = await request(`/api/schedule?month=${encodeURIComponent(month)}`);
  const entries = data.entries || data.schedule?.entries || [];
  const revision = String(data.revision || "");
  if (!revision) throw new Error("Plan sürümü bulunamadı; kişisel kart oluşturulmadı.");
  const planned = [...new Set(entries.map((entry) => String(entry.employee_id)))];
  const people = state.people.filter((person) => planned.includes(String(person.id)))
    .sort((a, b) => a.name.localeCompare(b.name, "tr"));
  const dialog = extrasDialog("personal-card-dialog", `Kişisel vardiya kartı · ${extrasMonthLabel(month)}`,
    "Her çalışana kendi ayını gösteren görsel; WhatsApp veya e-postayla paylaşılabilir.");
  const body = dialog.querySelector(".extras-body");
  if (!people.length) {
    body.innerHTML = '<p class="archive-empty">Bu ay vardiya listesinde personel yok.</p>';
    showExtras(dialog);
    return;
  }
  const selected = employeeId || filterMemoryGet("personal-card-person", "");
  body.innerHTML = `<div class="personal-card-controls">
      <label class="extras-search"><span>Personel</span><select id="personal-card-person">${people.map((person) =>
        `<option value="${person.id}"${String(person.id) === String(selected) ? " selected" : ""}>${escapeHtml(person.name)}</option>`).join("")}</select></label>
      <button class="button button-primary" type="button" data-personal="download">PNG indir</button>
      <button class="button button-light" type="button" data-personal="copy">Panoya kopyala</button>
      <button class="button button-light" type="button" data-personal="copy-text">Güvenli metni kopyala</button>
      <button class="button button-light" type="button" data-personal="all">Herkesin kartını indir</button>
    </div>
    <p class="personal-card-privacy-note">Paylaşım yalnızca ad, görev, çalışma saatleri ve plan sürümünü içerir; izin türü/nedeni, notlar ve iletişim bilgileri eklenmez.</p>
    <div class="personal-card-preview" id="personal-card-preview"></div>`;
  const select = body.querySelector("#personal-card-person");
  const preview = body.querySelector("#personal-card-preview");
  let canvas = null;
  let title = "";
  let profileRequest = 0;
  const currentName = () => select.selectedOptions[0]?.textContent || "";
  const paint = () => {
    canvas = personalCardCanvas(currentName(), month, personalDays(entries, select.value), title, revision);
    preview.replaceChildren(canvas);
  };
  const loadRole = async () => {
    const employeeId = select.value;
    const requestId = ++profileRequest;
    title = "";
    paint();
    const profile = await request(
      `/api/employee-profile?employee_id=${encodeURIComponent(employeeId)}&fields=title`,
    );
    if (requestId !== profileRequest) return;
    title = typeof profile.title === "string" ? profile.title : "";
    paint();
  };
  const download = (target, name) => new Promise((resolve) => target.toBlob((blob) => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `Vardiya_${personalSafeName(name)}_${month}.png`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 4000);
    resolve();
  }, "image/png"));
  select.addEventListener("change", paint);
  body.querySelector(".personal-card-controls").addEventListener("click", (event) => {
    const action = event.target.closest("[data-personal]")?.dataset.personal;
    if (!action) return;
    extrasRun(event.target.closest("button"), async () => {
      if (action === "download") {
        await download(canvas, currentName());
      } else if (action === "copy") {
        if (!navigator.clipboard?.write || typeof ClipboardItem === "undefined") throw new Error("Bu pencere panoya görsel kopyalamayı desteklemiyor; “PNG indir” kullanın.");
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
        await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
        toast("Kart panoya kopyalandı; WhatsApp'ta Ctrl+V ile yapıştırabilirsiniz.");
        return;
      } else if (action === "copy-text") {
        if (!navigator.clipboard?.writeText) throw new Error("Bu pencerede metni panoya kopyalama desteklenmiyor.");
        await navigator.clipboard.writeText(personalCardClipboardText(
          currentName(), title, month, personalDays(entries, select.value), revision,
        ));
        toast("Güvenli vardiya özeti panoya kopyalandı.");
        return;
      } else {
        for (const person of people) {
          const profile = await request(
            `/api/employee-profile?employee_id=${encodeURIComponent(person.id)}&fields=title`,
          );
          await download(personalCardCanvas(
            person.name, month, personalDays(entries, person.id), profile.title || "", revision,
          ), person.name);
          await new Promise((resolve) => setTimeout(resolve, 250));
        }
      }
      toast("Kart indirildi.");
    });
  });
  select.addEventListener("change", () => loadRole().catch((error) => toast(error.message, true)));
  await loadRole();
  showExtras(dialog);
}

/* ---------- Görev panosu ve vardiya devir notları ---------- */

function dutyMinutes(text) {
  const [hours, minutes] = String(text || "0:0").split(":").map(Number);
  return hours * 60 + minutes;
}

function dutyStatus(person, isToday) {
  if (!isToday) return ["", ""];
  const now = new Date();
  const minute = now.getHours() * 60 + now.getMinutes();
  const start = person.from_previous ? -1 : dutyMinutes(person.start);
  let end = dutyMinutes(person.end);
  if (!person.from_previous && person.overnight) end += 24 * 60;
  if (minute < start) return ["upcoming", "Başlayacak"];
  if (minute >= end) return ["done", "Bitti"];
  return ["now", "Şu an görevde"];
}

let dutyRefreshTimer = null;

function dutyLocalDate(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

const DUTY_CATEGORY_LABELS = { official: "Resmî tatil", religious: "Dinî bayram", weekend: "Hafta sonu" };

async function openDutyBoard(day = dutyLocalDate()) {
  const dialog = extrasDialog("duty-board-dialog", "Görev panosu", "Günün vardiyaları bölüm bölüm; devir notlarıyla birlikte. Duvar ekranı için tam ekran açılabilir.");
  dialog.classList.add("duty-board-dialog");
  const body = dialog.querySelector(".extras-body");
  const shift = (offset) => {
    const date = new Date(`${day}T12:00:00`);
    date.setDate(date.getDate() + offset);
    return dutyLocalDate(date);
  };
  const render = async () => {
    const data = await request(`/api/duty-board?date=${day}`);
    const isToday = day === dutyLocalDate();
    const sectionNames = data.sections.map((section) => section.name);
    const filter = filterMemoryGet("duty-section-filter", "");
    const visible = data.sections.filter((section) => !filter || section.name === filter);
    const title = new Date(`${day}T12:00:00`).toLocaleDateString("tr-TR", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    body.innerHTML = `<div class="duty-toolbar">
        <button class="button button-light" type="button" data-duty="prev" aria-label="Önceki gün">‹</button>
        <input type="date" id="duty-date" value="${day}" aria-label="Gün">
        <button class="button button-light" type="button" data-duty="next" aria-label="Sonraki gün">›</button>
        <button class="button button-light" type="button" data-duty="today">Bugün</button>
        <label class="duty-filter">Bölüm <select id="duty-section-filter"><option value="">Tümü</option>${sectionNames.map((name) =>
          `<option${name === filter ? " selected" : ""}>${escapeHtml(name)}</option>`).join("")}</select></label>
        <button class="button button-light" type="button" data-duty="fullscreen">Tam ekran</button>
      </div>
      <h3 class="duty-title">${escapeHtml(title)}${DUTY_CATEGORY_LABELS[data.category] ? ` <small>${DUTY_CATEGORY_LABELS[data.category]}</small>` : ""}</h3>
      <div class="duty-grid">${visible.length ? visible.map((section) => `<section class="duty-section">
          <h4>${escapeHtml(section.name)} <small>${section.people.length} kişi</small></h4>
          <ul>${section.people.map((person) => {
            const [status, label] = dutyStatus(person, isToday);
            return `<li class="duty-person" data-status="${status}">
              <span class="duty-dot" style="${person.color ? `background:${escapeHtml(person.color)}` : ""}"></span>
              <span class="duty-name">${escapeHtml(person.name)}</span>
              <span class="duty-time">${person.from_previous ? "dünden → " : ""}${escapeHtml(person.start)}–${escapeHtml(person.end)}${person.label ? ` · ${escapeHtml(person.label)}` : ""}</span>
              ${label ? `<span class="duty-badge">${label}</span>` : ""}</li>`;
          }).join("")}</ul></section>`).join("") : '<p class="archive-empty">Bu gün için planlanmış vardiya yok.</p>'}</div>
      ${data.off.length ? `<p class="duty-off"><strong>Çalışmayanlar:</strong> ${data.off.map((item) => `${escapeHtml(item.name)} <small>(${escapeHtml(item.status || "")})</small>`).join(", ")}</p>` : ""}
      <section class="duty-notes">
        <h4>Vardiya devir notları</h4>
        <ul class="duty-note-list">${data.notes.length ? data.notes.map((note) => `<li class="duty-note${note.done ? " is-done" : ""}">
            <p>${escapeHtml(note.text)}</p>
            <small>${escapeHtml([note.section, note.shift, note.author,
              note.read_by ? `Okuyan: ${note.read_by}` : "",
              note.claimed_by ? `Üstlenen: ${note.claimed_by}` : ""].filter(Boolean).join(" · "))}</small>
            <strong>${({ open: "Açık", read: "Okundu", claimed: "Üstlenildi", completed: "Tamamlandı" })[note.status] || "Açık"}</strong>
            ${note.status === "open" ? `<button class="button button-outline" type="button" data-note-transition="read" data-note-id="${escapeHtml(note.id)}">Okundu olarak işaretle</button>` : ""}
            ${["open", "read"].includes(note.status) ? `<button class="button button-outline" type="button" data-note-transition="claimed" data-note-id="${escapeHtml(note.id)}">Üstlen</button>` : ""}
            ${note.status === "claimed" ? `<button class="button button-outline" type="button" data-note-transition="completed" data-note-id="${escapeHtml(note.id)}">Tamamla</button>` : ""}
            <button class="icon-button" type="button" data-note-delete="${escapeHtml(note.id)}" aria-label="Notu sil">×</button></li>`).join("")
          : '<li class="archive-empty">Bu gün için devir notu yok.</li>'}</ul>
        <form class="duty-note-form" id="duty-note-form">
          <select name="section" aria-label="Bölüm"><option value="">Bölüm (isteğe bağlı)</option>${sectionNames.map((name) =>
            `<option>${escapeHtml(name)}</option>`).join("")}</select>
          <input name="shift" maxlength="40" placeholder="Vardiya (ör. Akşam)" aria-label="Vardiya">
          <input name="text" maxlength="500" required placeholder="Sonraki vardiyaya not (ör. kasa 2 arızalı)" aria-label="Not">
          <button class="button button-primary" type="submit">Not ekle</button>
        </form>
      </section>`;
  };
  body.onclick = (event) => {
    const action = event.target.closest("[data-duty]")?.dataset.duty;
    const remove = event.target.closest("[data-note-delete]");
    const transition = event.target.closest("[data-note-transition]");
    if (action === "prev" || action === "next" || action === "today") {
      day = action === "today" ? dutyLocalDate() : shift(action === "prev" ? -1 : 1);
      render().catch((error) => toast(error.message, true));
    } else if (action === "fullscreen") {
      if (document.fullscreenElement) document.exitFullscreen?.();
      else dialog.requestFullscreen?.().catch(() => toast("Tam ekran bu pencerede desteklenmiyor.", true));
    } else if (remove) {
      if (!confirm("Devir notu silinsin mi?")) return;
      extrasRun(remove, async () => {
        await request("/api/handover", { method: "POST", body: JSON.stringify({ id: remove.dataset.noteDelete, delete: true }) });
        await render();
      });
    } else if (transition) {
      extrasRun(transition, async () => {
        await request("/api/handover", { method: "POST", body: JSON.stringify({
          id: transition.dataset.noteId, status: transition.dataset.noteTransition,
        }) });
        await render();
      });
    }
  };
  body.onchange = (event) => {
    const target = event.target;
    if (target.id === "duty-date" && target.value) {
      day = target.value;
      render().catch((error) => toast(error.message, true));
    } else if (target.id === "duty-section-filter") {
      filterMemorySet("duty-section-filter", target.value);
      render().catch((error) => toast(error.message, true));
    }
  };
  body.onsubmit = (event) => {
    if (event.target.id !== "duty-note-form") return;
    event.preventDefault();
    const form = new FormData(event.target);
    extrasRun(event.target.querySelector("button[type='submit']"), async () => {
      await request("/api/handover", { method: "POST", body: JSON.stringify({
        date: day, section: form.get("section") || "", shift: form.get("shift") || "", text: form.get("text") || "",
      }) });
      toast("Devir notu eklendi.");
      await render();
    });
  };
  await render();
  clearInterval(dutyRefreshTimer);
  dutyRefreshTimer = setInterval(() => {
    if (!dialog.open) { clearInterval(dutyRefreshTimer); return; }
    if (!dialog.contains(document.activeElement) || document.activeElement === dialog) render().catch(() => {});
  }, 60000);
  dialog.onclose = () => clearInterval(dutyRefreshTimer);
  showExtras(dialog);
}

if (typeof EXTRAS_MENU !== "undefined" && !EXTRAS_MENU.some((item) => item[0] === "Görev panosu ve devir notları")) {
  EXTRAS_MENU.push(
    ["Görev panosu ve devir notları", () => openDutyBoard().catch((error) => toast(error.message, true))],
    ["Grafikler (ay özeti)", () => openMonthCharts().catch((error) => toast(error.message, true))],
    ["Kişisel vardiya kartı (PNG)", () => openPersonalCard().catch((error) => toast(error.message, true))],
  );
}
