/* Çıktı araçları: yazdırma sihirbazı, Excel önizleme, dosya adı şablonu ve şablon sürüm farkları. */

const OUTPUT_PAGE_LABELS = {
  print_area: "Yazdırma alanı", orientation: "Sayfa yönü", paper: "Kâğıt", fit_width: "Sayfa genişliği",
  fit_height: "Sayfa yüksekliği", margins: "Kenar boşlukları", print_title_rows: "Yinelenen satırlar",
  center_horizontally: "Yatay ortalama", header_text: "Üst bilgi", footer_text: "Alt bilgi",
  page_numbers: "Sayfa numarası", code_dropdown: "Kod açılır listesi", highlight_codes: "Kod renklendirme",
};
const OUTPUT_SHEET_LABELS = {
  extra_rows: "Ek satır", extra_cols: "Ek sütun", zoom: "Yakınlaştırma", hide_notes: "Notları gizle",
  merges_add: "Eklenen birleştirmeler", merges_remove: "Kaldırılan birleştirmeler", insert_rows: "Araya eklenen satırlar",
  insert_cols: "Araya eklenen sütunlar", delete_rows: "Silinen satırlar", delete_cols: "Silinen sütunlar",
  column_widths: "Sütun genişlikleri", row_heights: "Satır yükseklikleri",
};
const OUTPUT_WIZARD_FIELDS = ["orientation", "fit_width", "fit_height", "header_text", "footer_text",
  "page_numbers", "code_dropdown", "highlight_codes"];
const OUTPUT_SIGNATURE = "Hazırlayan: ____________    Kontrol eden: ____________    Onaylayan: ____________";

function outputShow(value) {
  if (value === undefined || value === null || value === "") return "—";
  if (value === true) return "açık";
  if (value === false) return "kapalı";
  if (typeof value === "object") return Array.isArray(value) ? `${value.length} öğe` : JSON.stringify(value);
  return String(value);
}

function outputCellName(address) {
  const [row, col] = String(address).split(":").map(Number);
  return Number.isFinite(row) && Number.isFinite(col) ? `${excelColumnName(col)}${row}` : String(address);
}

/* İki şablon sayfası ayarı arasındaki farkları okunur satırlar olarak döndürür. */
function outputSheetDiff(before, after) {
  const lines = [];
  const beforeCells = before.cells || {};
  const afterCells = after.cells || {};
  const cellLines = [];
  for (const address of new Set([...Object.keys(beforeCells), ...Object.keys(afterCells)])) {
    const old = beforeCells[address];
    const now = afterCells[address];
    if (JSON.stringify(old) === JSON.stringify(now)) continue;
    const name = outputCellName(address);
    if (!old) cellLines.push(`${name}: biçim eklendi`);
    else if (!now) cellLines.push(`${name}: biçim kaldırıldı`);
    else {
      const keys = [...new Set([...Object.keys(old), ...Object.keys(now)])]
        .filter((key) => JSON.stringify(old[key]) !== JSON.stringify(now[key]));
      cellLines.push(`${name}: ${keys.map((key) => `${key} ${outputShow(old[key])} → ${outputShow(now[key])}`).join(", ")}`);
    }
  }
  cellLines.sort((a, b) => a.localeCompare(b, "tr", { numeric: true }));
  lines.push(...cellLines.slice(0, 60));
  if (cellLines.length > 60) lines.push(`… ve ${cellLines.length - 60} hücre değişikliği daha`);
  const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
  keys.delete("cells");
  keys.delete("page_setup");
  for (const key of keys) {
    if (JSON.stringify(before[key]) === JSON.stringify(after[key])) continue;
    lines.push(`${OUTPUT_SHEET_LABELS[key] || key}: ${outputShow(before[key])} → ${outputShow(after[key])}`);
  }
  const oldPage = before.page_setup || {};
  const newPage = after.page_setup || {};
  for (const key of new Set([...Object.keys(oldPage), ...Object.keys(newPage)])) {
    if (JSON.stringify(oldPage[key]) === JSON.stringify(newPage[key])) continue;
    lines.push(`${OUTPUT_PAGE_LABELS[key] || key}: ${outputShow(oldPage[key])} → ${outputShow(newPage[key])}`);
  }
  return lines;
}

function outputMonth(kind) {
  return kind === "schedule" ? state.scheduleMonth : state.attendanceMonth;
}

async function outputRenderPreview(kind, frame) {
  frame.innerHTML = '<p class="extras-empty">Önizleme hazırlanıyor…</p>';
  const params = new URLSearchParams({ kind, month: outputMonth(kind), week: String(kind === "schedule" ? state.weekIndex : 0) });
  const { html } = await request(`/api/export-preview?${params}`);
  frame.innerHTML = `<div class="xl-paper">${html}</div>`;
}

function outputZoomControl(frame) {
  const label = document.createElement("label");
  label.className = "output-zoom";
  label.innerHTML = 'Ölçek <input type="range" min="30" max="150" step="10" value="70"> <output>%70</output>';
  const input = label.querySelector("input");
  const apply = () => {
    frame.style.setProperty("--xl-zoom", String(Number(input.value) / 100));
    label.querySelector("output").textContent = `%${input.value}`;
  };
  input.addEventListener("input", apply);
  apply();
  return label;
}

function openOutputPreview(kind = state.view === "attendance" ? "attendance" : "schedule") {
  const dirty = kind === "schedule" ? state.scheduleDirty : state.attendanceDirty;
  const dialog = extrasDialog("xl-output-preview", "Excel çıktı önizlemesi",
    `${kind === "schedule" ? "Vardiya listesi (seçili hafta)" : "Puantaj"} · ${extrasMonthLabel(outputMonth(kind))}. Önizleme kaydedilmiş verilerle, Excel şablonundan üretilir.${dirty ? " Kaydedilmemiş değişiklikler görünmez." : ""}`);
  dialog.classList.add("output-dialog");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = '<div class="output-preview-frame"></div>';
  const frame = body.querySelector(".output-preview-frame");
  const actions = dialog.querySelector(".extras-actions");
  const save = document.createElement("button");
  save.type = "button";
  save.className = "button";
  save.textContent = "Excel olarak kaydet";
  save.addEventListener("click", () => extrasRun(save, async () => { if (await downloadExcel(kind)) dialog.close(); }));
  actions.prepend(outputZoomControl(frame), save);
  showExtras(dialog);
  outputRenderPreview(kind, frame).catch((error) => { frame.innerHTML = `<p class="extras-empty">${escapeHtml(error.message)}</p>`; });
}

function outputPageDefaults(kind) {
  return { ...(sheetSettings(kind).page_setup || {}) };
}

async function outputSavePageSetup(kind, fields, scope) {
  const settings = structuredClone(state.workbookSettings);
  const pageKey = pageSheetKey(kind);
  const key = scope === "template" ? templateSheetKey(kind) : pageKey;
  if (!settings.sheets[key]) {
    settings.sheets[key] = scope === "template"
      ? { cells: {}, hide_notes: false, zoom: kind === "schedule" ? 55 : 100 }
      : structuredClone(sheetSettings(kind));
  }
  const sheet = settings.sheets[key];
  sheet.cells ||= {};
  const setup = { ...(sheet.page_setup || {}) };
  for (const name of OUTPUT_WIZARD_FIELDS) delete setup[name];
  Object.assign(setup, fields);
  if (Object.keys(setup).length) sheet.page_setup = setup;
  else delete sheet.page_setup;
  if (scope === "template" && settings.sheets[pageKey]?.page_setup) {
    // Ayın kendi sayfa yapısı şablonu ezmesin; hatırlanan ayarlar bu ayda da geçerli olsun.
    const own = { ...settings.sheets[pageKey].page_setup };
    for (const name of OUTPUT_WIZARD_FIELDS) delete own[name];
    if (Object.keys(own).length) settings.sheets[pageKey].page_setup = { ...own, ...fields };
    else delete settings.sheets[pageKey].page_setup;
  }
  return persistWorkbookSettings(settings);
}

async function openPrintWizard() {
  const dialog = extrasDialog("print-wizard", "Yazdırma ve çıktı sihirbazı",
    "Ne yazdıracağınızı seçin, sayfa ayarlarını yapın, önizleyin ve kaydedin. Ayarlar hatırlanır.");
  dialog.classList.add("output-dialog");
  const body = dialog.querySelector(".extras-body");
  let prefs = { filename_template: "", last_folder: "" };
  try { prefs = (await request("/api/ui-settings")).settings.export || prefs; } catch { /* varsayılanlar */ }
  const initialKind = state.view === "attendance" ? "attendance" : "schedule";
  body.innerHTML = `<form class="extras-form print-wizard-form" id="print-wizard-form">
    <fieldset class="pw-step"><legend><b>1</b> Ne yazdırılacak?</legend>
      <label><input type="radio" name="kind" value="schedule"> Vardiya listesi (seçili hafta)</label>
      <label><input type="radio" name="kind" value="attendance"> Aylık puantaj</label>
      <label><input type="radio" name="kind" value="package"> Ay sonu paketi (tüm Excel + PDF, ZIP)</label>
    </fieldset>
    <fieldset class="pw-step" data-pw-page><legend><b>2</b> Sayfa</legend>
      <label>Yön <select name="orientation"><option value="">Şablondaki gibi</option><option value="landscape">Yatay</option><option value="portrait">Dikey</option></select></label>
      <label>Genişlik <select name="fit_width">
        <option value="">Şablondaki gibi</option><option value="1">1 sayfa</option>
        <option value="2">2 sayfa</option><option value="3">3 sayfa</option><option value="4">4 sayfa</option>
      </select></label>
      <p class="pw-hint" data-pw-readability role="status" hidden></p>
      <label>Üst bilgi <input name="header_text" maxlength="120" placeholder="ör. ÖRNEK İSTASYON"></label>
      <label>Alt bilgi <input name="footer_text" maxlength="120"></label>
      <button class="button button-light" type="button" data-pw-signature>İmza satırı ekle</button>
      <label><input type="checkbox" name="page_numbers"> Sayfa numarası</label>
      <label data-pw-attendance><input type="checkbox" name="code_dropdown"> Gün hücrelerinde kod açılır listesi</label>
      <label data-pw-attendance><input type="checkbox" name="highlight_codes"> R / S / Ü / İST. / E kodlarını renklendir</label>
      <label>Ayarları kaydet <select name="scope"><option value="template">Tüm aylar için hatırla</option><option value="page">Yalnız bu ay</option></select></label>
    </fieldset>
    <fieldset class="pw-step"><legend><b>3</b> Dosya adı</legend>
      <label>Şablon <input name="filename_template" maxlength="120" placeholder="{TUR}_{AY}{PERSONEL}"></label>
      <p class="pw-hint">Kullanılabilir: {TUR} {AY} {YIL} {AYNO} {AYADI} {HAFTA} {PERSONEL}</p>
      <p class="pw-example">Örnek: <b data-pw-example>—</b></p>
      ${prefs.last_folder ? `<p class="pw-hint">Son kayıt klasörü: ${escapeHtml(prefs.last_folder)}</p>` : ""}
    </fieldset>
  </form>
  <div class="output-preview-frame" data-pw-preview hidden></div>`;
  const form = body.querySelector("form");
  const frame = body.querySelector("[data-pw-preview]");
  form.filename_template.value = prefs.filename_template || "";
  form.querySelector(`[name="kind"][value="${initialKind}"]`).checked = true;
  const kind = () => form.kind.value;
  const fill = () => {
    const current = kind();
    const pageKind = current === "package" ? "attendance" : current;
    const setup = outputPageDefaults(pageKind);
    form.orientation.value = setup.orientation || "";
    form.elements.namedItem("fit_width").value = Number.isInteger(setup.fit_width) && !setup.fit_height
      ? String(setup.fit_width) : "";
    form.header_text.value = setup.header_text || "";
    form.footer_text.value = setup.footer_text || "";
    form.page_numbers.checked = Boolean(setup.page_numbers);
    form.code_dropdown.checked = Boolean(setup.code_dropdown);
    form.highlight_codes.checked = Boolean(setup.highlight_codes);
    form.querySelector("[data-pw-page]").disabled = current === "package";
    form.querySelectorAll("[data-pw-attendance]").forEach((node) => { node.hidden = current !== "attendance"; });
    frame.hidden = true;
    example();
    updateReadability();
  };
  const updateReadability = () => {
    const notice = form.querySelector("[data-pw-readability]");
    const fitWidth = Number(form.elements.namedItem("fit_width").value);
    if (!fitWidth || kind() === "package") { notice.hidden = true; return; }
    const pageKind = kind();
    const index = pageKind === "schedule" ? state.weekIndex : 0;
    const template = state.templates[pageKind]?.[index];
    if (!template?.columns?.length) { notice.hidden = true; return; }
    const month = outputMonth(pageKind);
    const sheet = state.workbookSettings.sheets[`${pageKind}:${month}:${index}`]
      || state.workbookSettings.sheets[`${pageKind}:template:${index}`] || {};
    const totalWidth = template.columns.reduce((total, column, columnIndex) => {
      if (column.hidden) return total;
      const width = Number(sheet.column_widths?.[String(columnIndex + 1)] ?? column.width);
      return total + (Number.isFinite(width) && width > 0 ? width : 8.43);
    }, 0);
    const orientation = form.orientation.value
      || outputPageDefaults(pageKind).orientation || "portrait";
    const printableWidth = (orientation === "landscape" ? 10.1 : 6.7) * 96;
    const singlePageScale = Math.min(1, printableWidth / (totalWidth * 7));
    const selectedScale = singlePageScale * fitWidth;
    if (selectedScale >= 0.72) { notice.hidden = true; return; }
    const suggestedWidth = Math.min(4, Math.max(fitWidth + 1, Math.ceil(0.72 / singlePageScale)));
    notice.textContent = `Tahmini yazı ölçeği %${Math.round(selectedScale * 100)}. Daha okunaklı çıktı için genişliği en az ${suggestedWidth} sayfa seçin; son tercih sizde.`;
    notice.hidden = false;
  };
  let exampleTimer = 0;
  const example = () => {
    clearTimeout(exampleTimer);
    exampleTimer = setTimeout(async () => {
      const target = form.querySelector("[data-pw-example]");
      if (kind() === "package") { target.textContent = `AY_SONU_PAKETI_${state.attendanceMonth}.zip`; return; }
      const params = new URLSearchParams({ kind: kind(), month: outputMonth(kind()), week: String(state.weekIndex || 0), template: form.filename_template.value });
      try { target.textContent = (await request(`/api/export-name?${params}`)).filename; } catch (error) { target.textContent = error.message; }
    }, 200);
  };
  form.addEventListener("change", (event) => {
    if (event.target.name === "kind") fill();
    else if (event.target.name === "fit_width" || event.target.name === "orientation") updateReadability();
  });
  form.filename_template.addEventListener("input", example);
  form.querySelector("[data-pw-signature]").addEventListener("click", () => { form.footer_text.value = OUTPUT_SIGNATURE; });
  const fields = () => {
    const result = {};
    if (form.orientation.value) result.orientation = form.orientation.value;
    const selectedFitWidth = Number(form.elements.namedItem("fit_width").value);
    if (selectedFitWidth) Object.assign(result, { fit_width: selectedFitWidth, fit_height: 0 });
    for (const name of ["header_text", "footer_text"]) if (form[name].value.trim()) result[name] = form[name].value.trim();
    for (const name of ["page_numbers", "code_dropdown", "highlight_codes"]) if (form[name].checked) result[name] = true;
    if (kind() !== "attendance") { delete result.code_dropdown; delete result.highlight_codes; }
    return result;
  };
  const persist = async () => {
    if (!form.reportValidity()) return false;
    const template = form.filename_template.value.trim();
    if (template !== (prefs.filename_template || "")) {
      prefs = (await request("/api/ui-settings", {
        method: "PUT", body: JSON.stringify({ export: { filename_template: template, last_folder: prefs.last_folder || "" } }),
      })).settings.export;
    }
    if (kind() !== "package") {
      const before = JSON.stringify(outputPageDefaults(kind()));
      const wanted = { ...outputPageDefaults(kind()) };
      for (const name of OUTPUT_WIZARD_FIELDS) delete wanted[name];
      Object.assign(wanted, fields());
      if (JSON.stringify(wanted) !== before || form.scope.value === "template") {
        if (!(await outputSavePageSetup(kind(), fields(), form.scope.value))) return false;
      }
    }
    return true;
  };
  const actions = dialog.querySelector(".extras-actions");
  const preview = document.createElement("button");
  preview.type = "button";
  preview.className = "button button-light";
  preview.textContent = "Önizle";
  const run = document.createElement("button");
  run.type = "button";
  run.className = "button";
  run.textContent = "Kaydet ve dışa aktar";
  preview.addEventListener("click", () => extrasRun(preview, async () => {
    if (kind() === "package") { toast("Paket için önizleme yok; Excel dosyalarını ayrı ayrı önizleyebilirsiniz.", true); return; }
    if (!(await persist())) return;
    frame.hidden = false;
    await outputRenderPreview(kind(), frame);
    frame.scrollIntoView({ block: "nearest" });
  }));
  run.addEventListener("click", () => extrasRun(run, async () => {
    if (!(await persist())) return;
    const ok = kind() === "package"
      ? await saveReportFile(`/api/reports/month-package?month=${state.attendanceMonth}`, "zip", `AY_SONU_PAKETI_${state.attendanceMonth}.zip`)
      : await downloadExcel(kind());
    if (ok) dialog.close();
  }));
  actions.prepend(outputZoomControl(frame), preview, run);
  fill();
  showExtras(dialog);
}

function installOutputTools() {
  for (const kind of ["schedule", "attendance"]) {
    const exportButton = document.getElementById(`export-${kind}`);
    if (!exportButton || document.getElementById(`preview-${kind}`)) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.id = `preview-${kind}`;
    button.className = "button button-outline";
    button.textContent = "Önizle";
    button.title = "Excel çıktısını kaydetmeden önce görün";
    button.addEventListener("click", () => openOutputPreview(kind));
    exportButton.after(button);
  }
}

if (typeof EXTRAS_MENU !== "undefined" && !EXTRAS_MENU.some((item) => item[0] === "Yazdırma sihirbazı")) {
  EXTRAS_MENU.push(["Yazdırma sihirbazı", () => openPrintWizard().catch((error) => toast(error.message, true))],
    ["Excel çıktı önizlemesi", () => openOutputPreview()]);
}
document.addEventListener("DOMContentLoaded", installOutputTools);
