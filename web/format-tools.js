"use strict";

/* Excel benzeri biçim araçları: hücre birleştirme/ayırma, kenarlık, biçim boyacısı,
   sayfa yapısı (yazdırma alanı), şablon sürümleri ve fareyle satır/sütun boyutlandırma. */

const fx = { painter: null };
const FX_STYLE_GROUPS = {
  fill: ["color"],
  font: ["font_color", "font_size", "font_bold", "font_italic", "font_underline", "font_strike", "font_name"],
  alignment: ["horizontal", "vertical", "shrink_to_fit", "wrap_text", "text_rotation"],
  border: ["border"],
};

function fxBounds(selected) {
  return {
    minRow: Math.min(selected.row, selected.endRow), maxRow: Math.max(selected.row, selected.endRow),
    minCol: Math.min(selected.col, selected.endCol), maxCol: Math.max(selected.col, selected.endCol),
  };
}

function fxDraft(kind) {
  const settings = structuredClone(state.workbookSettings);
  const key = sheetKey(kind);
  settings.sheets[key] = structuredClone(sheetSettings(kind));
  const sheet = settings.sheets[key];
  sheet.cells ||= {};
  return { settings, sheet };
}

function fxSelected(kind) {
  return state.selectedCell?.key === sheetKey(kind) ? state.selectedCell : null;
}

function fxSelectedCells(kind) {
  return [...el(`#${kind}-table`).querySelectorAll("td.format-selected[data-cell-row]")];
}

function fxCellBox(cell) {
  const row = Number(cell.dataset.cellRow);
  const col = Number(cell.dataset.cellCol);
  return { row, col, endRow: row + cell.rowSpan - 1, endCol: col + cell.colSpan - 1 };
}

function fxRemoveMerge(sheet, cell) {
  if (cell.dataset.customMerge) {
    const box = fxCellBox(cell);
    sheet.merges_add = (sheet.merges_add || []).filter((merge) => merge.row !== box.row || merge.column !== box.col);
    return true;
  }
  const anchor = cell.dataset.mergeAnchor;
  if (!anchor) return false;
  sheet.merges_remove = [...new Set([...(sheet.merges_remove || []), anchor])];
  return true;
}

async function fxMerge(kind) {
  const selected = fxSelected(kind);
  if (!selected) return;
  const bounds = fxBounds(selected);
  const rowspan = bounds.maxRow - bounds.minRow + 1;
  const colspan = bounds.maxCol - bounds.minCol + 1;
  if (rowspan * colspan < 2) {
    toast("Birleştirmek için en az iki hücre seçin.", true);
    return;
  }
  const { settings, sheet } = fxDraft(kind);
  const cells = fxSelectedCells(kind);
  for (const cell of cells) {
    const box = fxCellBox(cell);
    if (box.row < bounds.minRow || box.col < bounds.minCol || box.endRow > bounds.maxRow || box.endCol > bounds.maxCol) {
      toast("Seçim başka bir birleşik hücreyi yarım kesiyor. Seçimi birleşik hücreyi kapsayacak şekilde genişletin.", true);
      return;
    }
    if (cell.dataset.mergeAnchor !== undefined) fxRemoveMerge(sheet, cell);
  }
  const texts = cells.filter((cell) => cell.textContent.trim()).length;
  if (texts > 1 && !window.confirm("Birleştirilen alanda yalnızca sol üst hücrenin değeri görünür. Devam edilsin mi?")) return;
  sheet.merges_add = [...(sheet.merges_add || []), {
    row: bounds.minRow, column: bounds.minCol, rowspan, colspan,
  }];
  if (await persistWorkbookSettings(settings)) {
    state.selectedCell = { ...selected, row: bounds.minRow, col: bounds.minCol, endRow: bounds.maxRow, endCol: bounds.maxCol };
    updateFormatToolbar(kind);
  }
}

async function fxUnmerge(kind) {
  const { settings, sheet } = fxDraft(kind);
  const changed = fxSelectedCells(kind).filter((cell) => cell.dataset.mergeAnchor !== undefined)
    .map((cell) => fxRemoveMerge(sheet, cell)).some(Boolean);
  if (!changed) {
    toast("Seçimde ayrılabilecek birleşik hücre yok.", true);
    return;
  }
  await persistWorkbookSettings(settings);
}

function fxBorderSides(preset, box, bounds) {
  const edges = {
    top: box.row === bounds.minRow, bottom: box.endRow === bounds.maxRow,
    left: box.col === bounds.minCol, right: box.endCol === bounds.maxCol,
  };
  if (preset === "all" || preset === "none") return ["top", "bottom", "left", "right"];
  if (preset === "outline") return Object.keys(edges).filter((side) => edges[side]);
  if (preset === "inside") return Object.keys(edges).filter((side) => !edges[side]);
  return edges[preset] ? [preset] : [];
}

async function fxBorder(kind, toolbar) {
  const selected = fxSelected(kind);
  if (!selected) return;
  const bounds = fxBounds(selected);
  const preset = toolbar.querySelector("[data-fx-border-preset]").value;
  const style = preset === "none" ? "none" : toolbar.querySelector("[data-fx-border-style]").value;
  const color = toolbar.querySelector("[data-fx-border-color]").value;
  const { settings, sheet } = fxDraft(kind);
  for (const cell of fxSelectedCells(kind)) {
    const box = fxCellBox(cell);
    // Gün hücreleri ekranda birleşik görünse de Excel'de iki ayrı sütundur.
    const parts = cell.dataset.mergeAnchor === undefined && cell.colSpan > 1
      ? Array.from({ length: cell.colSpan }, (_, index) => ({ ...box, col: box.col + index, endCol: box.col + index }))
      : [box];
    for (const part of parts) {
      const address = `${part.row}:${part.col}`;
      const current = { ...sheet.cells[address] };
      if (preset === "reset") {
        delete current.border;
      } else {
        const sides = fxBorderSides(preset, part, bounds);
        if (!sides.length) continue;
        current.border = { ...current.border };
        for (const side of sides) current.border[side] = { style, color };
      }
      if (Object.keys(current).length) sheet.cells[address] = current;
      else delete sheet.cells[address];
    }
  }
  await persistWorkbookSettings(settings);
}

function fxStartPainter(kind, toolbar) {
  const selected = fxSelected(kind);
  if (!selected) return;
  const keys = [...toolbar.querySelectorAll("[data-fx-painter-style]:checked")]
    .flatMap((control) => FX_STYLE_GROUPS[control.dataset.fxPainterStyle] || []);
  if (!keys.length) {
    toast("Kopyalanacak en az bir biçim türü seçin.", true);
    return;
  }
  const source = sheetSettings(kind).cells[`${selected.row}:${selected.col}`] || {};
  const style = {};
  for (const key of keys) if (source[key] !== undefined) style[key] = structuredClone(source[key]);
  fx.painter = { kind, style, keys };
  document.body.classList.add("format-painter-active");
  toast("Biçim kopyalandı. Uygulamak istediğiniz hücreleri seçin (iptal için Esc).");
}

function fxStopPainter() {
  fx.painter = null;
  document.body.classList.remove("format-painter-active");
}

async function fxApplyPainter(kind) {
  if (!fx.painter || fx.painter.kind !== kind || !fxSelected(kind)) return;
  const { style } = fx.painter;
  const { keys } = fx.painter;
  fxStopPainter();
  const { settings, sheet } = fxDraft(kind);
  for (const cell of fxSelectedCells(kind)) {
    const addresses = [`${cell.dataset.cellRow}:${cell.dataset.cellCol}`];
    if (cell.dataset.shiftDay && cell.colSpan === 2) addresses.push(`${cell.dataset.cellRow}:${Number(cell.dataset.cellCol) + 1}`);
    for (const address of addresses) {
      const next = { ...sheet.cells[address] };
      for (const key of keys) delete next[key];
      Object.assign(next, structuredClone(style));
      if (Object.keys(next).length) sheet.cells[address] = next;
      else delete sheet.cells[address];
    }
  }
  await persistWorkbookSettings(settings);
}

function fxSelectionRange(kind) {
  const selected = fxSelected(kind);
  if (!selected) return "";
  const bounds = fxBounds(selected);
  return `${excelColumnName(bounds.minCol)}${bounds.minRow}:${excelColumnName(bounds.maxCol)}${bounds.maxRow}`;
}

function fxOpenPageSetup(kind) {
  const setup = sheetSettings(kind).page_setup || {};
  const margins = setup.margins || {};
  const dialog = extrasDialog("page-setup-dialog", "Sayfa yapısı",
    "Excel çıktısında kullanılır. Boş bırakılan alanlar şablonun kendi ayarını korur.");
  dialog.querySelector(".extras-body").innerHTML = `<form class="extras-form page-setup-form" id="page-setup-form">
    <fieldset><legend>Yazdırma alanı</legend>
      <label>Alan <input name="print_area" placeholder="A1:AH40" value="${escapeHtml(setup.print_area || "")}" pattern="[A-Za-z]{1,3}[1-9][0-9]*:[A-Za-z]{1,3}[1-9][0-9]*"></label>
      <button class="button button-light" type="button" data-page-use-selection ${fxSelectionRange(kind) ? "" : "disabled"}>Seçimi yazdırma alanı yap</button>
      <label>Her sayfada yinelenecek satırlar <input name="print_title_rows" placeholder="1:3" value="${escapeHtml(setup.print_title_rows || "")}" pattern="[1-9][0-9]*:[1-9][0-9]*"></label>
    </fieldset>
    <fieldset><legend>Sayfa</legend>
      <label>Yön <select name="orientation"><option value="">Şablondaki gibi</option><option value="portrait">Dikey</option><option value="landscape">Yatay</option></select></label>
      <label>Kâğıt <select name="paper"><option value="">Şablondaki gibi</option><option>A4</option><option>A3</option><option>A5</option><option>Letter</option></select></label>
      <label>Genişlik (sayfa) <input type="number" name="fit_width" min="0" max="20" value="${setup.fit_width ?? ""}" placeholder="0 = serbest"></label>
      <label>Yükseklik (sayfa) <input type="number" name="fit_height" min="0" max="20" value="${setup.fit_height ?? ""}" placeholder="0 = serbest"></label>
      <label><input type="checkbox" name="center_horizontally" ${setup.center_horizontally ? "checked" : ""}> Yatayda ortala</label>
    </fieldset>
    <fieldset><legend>Kenar boşlukları (cm)</legend>
      ${[["top", "Üst"], ["bottom", "Alt"], ["left", "Sol"], ["right", "Sağ"]].map(([side, label]) =>
        `<label>${label} <input type="number" name="margin_${side}" min="0" max="10" step="0.1" value="${margins[side] ?? ""}"></label>`).join("")}
    </fieldset>
    <fieldset><legend>Üst / alt bilgi</legend>
      <label>Üst bilgi (ortada) <input name="header_text" maxlength="120" value="${escapeHtml(setup.header_text || "")}" placeholder="ör. ÖRNEK İSTASYON"></label>
      <label>Alt bilgi (solda) <input name="footer_text" maxlength="120" value="${escapeHtml(setup.footer_text || "")}"></label>
      <button class="button button-light" type="button" data-page-signature>İmza satırı ekle</button>
      <label><input type="checkbox" name="page_numbers" ${setup.page_numbers ? "checked" : ""}> Sayfa numarası (Sayfa 1 / 2)</label>
    </fieldset>
    ${kind === "attendance" ? `<fieldset><legend>Excel yardımcıları</legend>
      <label><input type="checkbox" name="code_dropdown" ${setup.code_dropdown ? "checked" : ""}> Gün hücrelerinde kod açılır listesi</label>
      <label><input type="checkbox" name="highlight_codes" ${setup.highlight_codes ? "checked" : ""}> R / S / Ü / İST. / E kodlarını renklendir (koşullu biçim)</label>
    </fieldset>` : ""}
  </form>`;
  const form = dialog.querySelector("form");
  form.orientation.value = setup.orientation || "";
  form.paper.value = setup.paper || "";
  form.querySelector("[data-page-use-selection]").addEventListener("click", () => {
    form.print_area.value = fxSelectionRange(kind);
  });
  form.querySelector("[data-page-signature]").addEventListener("click", () => {
    form.footer_text.value = "Hazırlayan: ____________    Kontrol eden: ____________    Onaylayan: ____________";
  });
  const actions = dialog.querySelector(".extras-actions");
  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "button button-light";
  clear.textContent = "Sayfa yapısını sıfırla";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "button";
  save.textContent = "Kaydet";
  actions.prepend(clear, save);
  const persist = async (pageSetup) => {
    const { settings, sheet } = fxDraft(kind);
    if (pageSetup) sheet.page_setup = pageSetup;
    else delete sheet.page_setup;
    if (await persistWorkbookSettings(settings)) dialog.close();
  };
  clear.addEventListener("click", () => persist(null));
  save.addEventListener("click", () => {
    if (!form.reportValidity()) return;
    const data = new FormData(form);
    const pageSetup = {};
    const area = String(data.get("print_area")).trim().toUpperCase();
    if (area) pageSetup.print_area = area;
    const titles = String(data.get("print_title_rows")).trim();
    if (titles) pageSetup.print_title_rows = titles;
    if (data.get("orientation")) pageSetup.orientation = data.get("orientation");
    if (data.get("paper")) pageSetup.paper = data.get("paper");
    for (const name of ["fit_width", "fit_height"]) {
      if (data.get(name) !== "") pageSetup[name] = Number(data.get(name));
    }
    const pageMargins = {};
    for (const side of ["top", "bottom", "left", "right"]) {
      if (data.get(`margin_${side}`) !== "") pageMargins[side] = Number(data.get(`margin_${side}`));
    }
    if (Object.keys(pageMargins).length) pageSetup.margins = pageMargins;
    if (data.get("center_horizontally")) pageSetup.center_horizontally = true;
    for (const name of ["header_text", "footer_text"]) {
      const text = String(data.get(name) || "").trim();
      if (text) pageSetup[name] = text;
    }
    for (const name of ["page_numbers", "code_dropdown", "highlight_codes"]) {
      if (data.get(name)) pageSetup[name] = true;
    }
    persist(Object.keys(pageSetup).length ? pageSetup : null);
  });
  showExtras(dialog);
}

async function fxOpenVersions(kind) {
  const key = templateSheetKey(kind);
  const dialog = extrasDialog("template-versions-dialog", "Şablon sürümleri",
    "Şablon her kaydedildiğinde önceki hali saklanır (en fazla 20). Eski bir sürüme dönebilirsiniz.");
  const body = dialog.querySelector(".extras-body");
  body.textContent = "Yükleniyor…";
  showExtras(dialog);
  try {
    const { versions } = await request("/api/template-versions");
    const list = versions[key] || [];
    if (!list.length) {
      body.innerHTML = `<p class="extras-empty">Bu ${kind === "schedule" ? "hafta sekmesinin" : "puantaj"} şablonu için kayıtlı eski sürüm yok.</p>`;
      return;
    }
    body.innerHTML = `<table class="archive-table extras-table"><thead><tr><th>Kaydedilme zamanı</th><th>İçerik</th><th></th></tr></thead><tbody>${list.map((version, index) => {
      const sheet = version.sheet || {};
      const summary = [
        `${Object.keys(sheet.cells || {}).length} biçimli hücre`,
        sheet.extra_rows ? `${sheet.extra_rows} ek satır` : "",
        sheet.extra_cols ? `${sheet.extra_cols} ek sütun` : "",
        sheet.merges_add?.length ? `${sheet.merges_add.length} birleştirme` : "",
        sheet.page_setup ? "sayfa yapısı" : "",
      ].filter(Boolean).join(", ");
      return `<tr><td>${escapeHtml(new Date(version.saved_at).toLocaleString("tr-TR"))}</td><td>${escapeHtml(summary)}</td>
        <td><button class="button button-light" type="button" data-version-diff="${index}">Farkları göster</button>
        <button class="button button-light" type="button" data-version-restore="${index}">Bu sürüme dön</button></td></tr>`;
    }).join("")}</tbody></table><div class="version-diff" hidden></div>`;
    body.querySelectorAll("[data-version-diff]").forEach((button) => button.addEventListener("click", () => {
      const panel = body.querySelector(".version-diff");
      const version = list[Number(button.dataset.versionDiff)];
      const changes = outputSheetDiff(version.sheet || {}, state.workbookSettings.sheets[key] || {});
      panel.hidden = false;
      panel.innerHTML = `<h3>${escapeHtml(new Date(version.saved_at).toLocaleString("tr-TR"))} sürümünden bugüne değişenler</h3>${changes.length
        ? `<ul>${changes.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>`
        : "<p class=\"extras-empty\">Bu sürüm mevcut şablonla aynı.</p>"}`;
      panel.scrollIntoView({ block: "nearest" });
    }));
    body.querySelectorAll("[data-version-restore]").forEach((button) => button.addEventListener("click", async () => {
      if (!window.confirm("Şablon seçilen sürüme döndürülsün mü? Mevcut hali de sürüm listesine eklenir.")) return;
      const settings = structuredClone(state.workbookSettings);
      settings.sheets[key] = structuredClone(list[Number(button.dataset.versionRestore)].sheet);
      if (await persistWorkbookSettings(settings)) {
        dialog.close();
        toast("Şablon seçilen sürüme döndürüldü.");
      }
    }));
  } catch (error) {
    body.textContent = error.message;
  }
}

/* Fareyle satır/sütun boyutlandırma: başlık kenarından sürükle, çift tıkla otomatik sığdır. */
const FX_EDGE = 6;

function fxHeaderEdge(event) {
  const header = event.target.closest?.("th[data-format-column], th[data-format-row]");
  if (!header || !state.formatMode) return null;
  const table = header.closest("table");
  const kind = table?.id === "schedule-table" ? "schedule" : table?.id === "attendance-table" ? "attendance" : null;
  if (!kind) return null;
  const rect = header.getBoundingClientRect();
  if (header.dataset.formatColumn && rect.right - event.clientX <= FX_EDGE) {
    return { kind, table, header, axis: "column", index: Number(header.dataset.formatColumn), size: rect.width };
  }
  if (header.dataset.formatRow && rect.bottom - event.clientY <= FX_EDGE) {
    return { kind, table, header, axis: "row", index: Number(header.dataset.formatRow), size: rect.height };
  }
  return null;
}

function fxResizeTargets(kind, axis, index) {
  const selected = fxSelected(kind);
  if (selected) {
    const bounds = fxBounds(selected);
    const [min, max] = axis === "column" ? [bounds.minCol, bounds.maxCol] : [bounds.minRow, bounds.maxRow];
    if (index >= min && index <= max) return Array.from({ length: max - min + 1 }, (_, offset) => min + offset);
  }
  return [index];
}

async function fxSaveSizes(kind, axis, indexes, size) {
  const { settings, sheet } = fxDraft(kind);
  const field = axis === "column" ? "column_widths" : "row_heights";
  sheet[field] = { ...sheet[field] };
  for (const index of indexes) {
    if (size === null) delete sheet[field][String(index)];
    else sheet[field][String(index)] = size;
  }
  await persistWorkbookSettings(settings);
}

function fxInstallResize() {
  document.addEventListener("pointermove", (event) => {
    if (fx.resizing) return;
    const edge = fxHeaderEdge(event);
    document.querySelectorAll(".fx-resize-hover").forEach((item) => item.classList.remove("fx-resize-hover", "fx-resize-row"));
    if (edge) edge.header.classList.add("fx-resize-hover", ...(edge.axis === "row" ? ["fx-resize-row"] : []));
  });
  document.addEventListener("pointerdown", (event) => {
    const edge = fxHeaderEdge(event);
    if (!edge || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const zoom = Number(edge.table.style.zoom) || 1;
    const start = edge.axis === "column" ? event.clientX : event.clientY;
    const startSize = edge.size / zoom;
    const column = edge.axis === "column" ? edge.table.querySelector(`colgroup col:nth-child(${edge.index + 1})`) : null;
    const row = edge.axis === "row" ? edge.header.closest("tr") : null;
    const guide = document.createElement("output");
    guide.className = "fx-resize-guide";
    document.body.append(guide);
    let current = startSize;
    fx.resizing = true;
    const move = (moveEvent) => {
      const delta = ((edge.axis === "column" ? moveEvent.clientX : moveEvent.clientY) - start) / zoom;
      current = Math.max(4, startSize + delta);
      if (column) column.style.width = `${current}px`;
      if (row) row.style.height = `${current}px`;
      guide.textContent = edge.axis === "column"
        ? `Genişlik: ${Math.max(1, (current - 5) / 7).toFixed(1)}`
        : `Yükseklik: ${(current * 72 / 96).toFixed(1)} pt`;
      guide.style.left = `${moveEvent.clientX + 12}px`;
      guide.style.top = `${moveEvent.clientY + 12}px`;
    };
    const finish = async () => {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", finish);
      guide.remove();
      fx.resizing = false;
      if (Math.abs(current - startSize) < 1) return;
      const size = edge.axis === "column"
        ? Math.min(255, Math.max(1, Math.round(((current - 5) / 7) * 2) / 2))
        : Math.min(409, Math.max(1, Math.round((current * 72 / 96) * 2) / 2));
      await fxSaveSizes(edge.kind, edge.axis, fxResizeTargets(edge.kind, edge.axis, edge.index), size);
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", finish);
  }, true);
  document.addEventListener("dblclick", async (event) => {
    const edge = fxHeaderEdge(event);
    if (!edge) return;
    event.preventDefault();
    const targets = fxResizeTargets(edge.kind, edge.axis, edge.index);
    if (edge.axis === "row") {
      await fxSaveSizes(edge.kind, "row", targets, null);
      return;
    }
    const context = document.createElement("canvas").getContext("2d");
    let widest = 0;
    for (const index of targets) {
      edge.table.querySelectorAll(`td[data-cell-col="${index}"]`).forEach((cell) => {
        if (cell.colSpan > 1 || !context) return;
        const style = getComputedStyle(cell);
        context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        const editor = cell.querySelector("input, select");
        const text = editor ? (editor.tagName === "SELECT" ? editor.selectedOptions[0]?.textContent : editor.value) : cell.textContent;
        widest = Math.max(widest, context.measureText(text || "").width + 10);
      });
    }
    if (!widest) return;
    await fxSaveSizes(edge.kind, "column", targets, Math.min(255, Math.max(1, Math.round(((widest - 5) / 7) * 2) / 2)));
  }, true);
}

function updateExtraFormatTools(kind, toolbar) {
  const group = toolbar.querySelector(".fx-tools");
  if (!group) return;
  const selected = fxSelected(kind);
  const cells = selected ? fxSelectedCells(kind) : [];
  const multi = selected && (selected.row !== selected.endRow || selected.col !== selected.endCol);
  const busy = state.formattingBusy;
  group.querySelector('[data-fx="merge"]').disabled = busy || !multi;
  group.querySelector('[data-fx="unmerge"]').disabled = busy || !cells.some((cell) => cell.dataset.mergeAnchor !== undefined);
  group.querySelector('[data-fx="border"]').disabled = busy || !selected;
  const painter = group.querySelector('[data-fx="painter"]');
  painter.disabled = busy || !selected;
  painter.classList.toggle("active", fx.painter?.kind === kind);
  group.querySelector('[data-fx="versions"]').hidden = state.templateEdit !== kind;
  const setup = sheetSettings(kind).page_setup;
  group.querySelector('[data-fx="page-setup"]').textContent = setup?.print_area
    ? `Sayfa yapısı… (${setup.print_area})` : "Sayfa yapısı…";
}

function installExtraFormatTools() {
  for (const kind of ["schedule", "attendance"]) {
    const toolbar = el(`#${kind}-format-tools`);
    if (!toolbar) continue;
    const group = document.createElement("div");
    group.className = "fx-tools";
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", "Birleştirme, kenarlık ve sayfa yapısı");
    group.innerHTML = `<button class="button button-light" type="button" data-fx="merge" disabled title="Seçili hücreleri birleştir">Birleştir</button>
      <button class="button button-light" type="button" data-fx="unmerge" disabled title="Seçimdeki birleşik hücreleri ayır">Birleşimi ayır</button>
      <label>Kenarlık <select data-fx-border-preset aria-label="Kenarlık yeri">
        <option value="all">Tüm kenarlar</option><option value="outline">Dış kenar</option><option value="inside">İç kenarlar</option>
        <option value="top">Üst</option><option value="bottom">Alt</option><option value="left">Sol</option><option value="right">Sağ</option>
        <option value="none">Kenarlıksız</option><option value="reset">Şablondaki gibi</option></select></label>
      <label>Çizgi <select data-fx-border-style aria-label="Kenarlık kalınlığı ve stili">
        <option value="hair">Çok ince</option><option value="thin" selected>İnce</option><option value="medium">Orta kalın</option>
        <option value="thick">Kalın</option><option value="double">Çift çizgi</option><option value="dashed">Kesikli</option><option value="dotted">Noktalı</option></select></label>
      <label>Renk <input type="color" data-fx-border-color value="#000000" aria-label="Kenarlık rengi"></label>
      <button class="button button-light" type="button" data-fx="border" disabled>Kenarlığı uygula</button>
      <button class="button button-light" type="button" data-fx="painter" disabled title="Seçili hücrenin biçimini kopyala, sonra hedef hücreleri seç">Biçim boyacısı</button>
      <fieldset class="fx-painter-options"><legend>Kopyalanacak biçimler</legend>
        <label><input type="checkbox" data-fx-painter-style="fill" checked>Dolgu</label>
        <label><input type="checkbox" data-fx-painter-style="font" checked>Yazı</label>
        <label><input type="checkbox" data-fx-painter-style="alignment" checked>Hizalama</label>
        <label><input type="checkbox" data-fx-painter-style="border" checked>Kenarlık</label>
      </fieldset>
      <button class="button button-light" type="button" data-fx="page-setup">Sayfa yapısı…</button>
      <button class="button button-light" type="button" data-fx="versions" hidden>Şablon sürümleri…</button>`;
    const help = toolbar.querySelector(".format-help");
    toolbar.insertBefore(group, help);
    if (help) help.textContent += " Sütun veya satır başlığının kenarını fareyle sürükleyerek boyutlandırabilir, çift tıklayarak sığdırabilirsiniz. Tablonun sağındaki ve altındaki soluk hücrelere de yazı ve biçim verebilirsiniz.";
    group.addEventListener("click", (event) => {
      const button = event.target.closest("[data-fx]");
      if (!button || button.disabled) return;
      const action = button.dataset.fx;
      if (action === "merge") fxMerge(kind);
      else if (action === "unmerge") fxUnmerge(kind);
      else if (action === "border") fxBorder(kind, toolbar);
      else if (action === "painter") {
        if (fx.painter) fxStopPainter(); else fxStartPainter(kind, toolbar);
        updateExtraFormatTools(kind, toolbar);
      } else if (action === "page-setup") fxOpenPageSetup(kind);
      else if (action === "versions") fxOpenVersions(kind);
    });
    el(`#${kind}-table`).addEventListener("pointerup", () => {
      if (fx.painter?.kind === kind) setTimeout(() => fxApplyPainter(kind), 0);
    });
    updateExtraFormatTools(kind, toolbar);
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && fx.painter) {
      fxStopPainter();
      document.querySelectorAll(".workbook-tools").forEach((toolbar) =>
        updateExtraFormatTools(toolbar.id.startsWith("schedule") ? "schedule" : "attendance", toolbar));
    }
  });
  fxInstallResize();
}

installExtraFormatTools();
