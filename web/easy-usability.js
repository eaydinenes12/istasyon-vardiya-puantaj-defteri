"use strict";

(() => {
  const clone = (value) => structuredClone(value);
  const canonical = (value) => Array.isArray(value) ? value.map(canonical)
    : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])])) : value;
  const same = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
  const fold = (text) => String(text).toLocaleLowerCase("tr-TR").normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "").replace(/ı/g, "i");
  const run = (action) => Promise.resolve().then(action).catch((error) => toast(error.message, true));
  const selected = new Map();
  const draftState = {};
  const workspace = {};
  const metrics = [];
  let scheduleAnchor = null;
  let scheduleSelection = false;
  let bulkClipboard = null;
  let returnPoint = null;
  let statusTimer;
  let freezeTimer;

  function button(label, action, className = "button button-outline") {
    const node = document.createElement("button");
    node.type = "button";
    node.className = className;
    node.textContent = label;
    node.addEventListener("click", () => {
      if (node.disabled) return;
      node.disabled = true;
      run(action).finally(() => { node.disabled = false; });
    });
    return node;
  }

  const monthFor = (kind) => kind === "schedule" ? state.scheduleMonth : state.attendanceMonth;
  const dataFor = (kind) => kind === "schedule" ? state.schedule : state.attendance;
  const nameFor = (id) => state.people.find((person) => person.id === Number(id))?.name || `Personel ${id}`;
  const canWrite = (kind) => accessCan(`${kind}.write`);

  function revisionsFor(kind) {
    if (kind === "schedule") return Object.fromEntries([
      [state.scheduleMonth, state.schedule?.revision || ""],
      ...Object.entries(state.neighborSchedules).map(([month, value]) => [month, value.revision || ""]),
    ].sort(([left], [right]) => left.localeCompare(right)));
    const sources = state.attendance?.source_revisions || {};
    const periods = [previousMonth(state.attendanceMonth), state.attendanceMonth,
      dateKey(new Date(Number(state.attendanceMonth.slice(0, 4)), Number(state.attendanceMonth.slice(5)), 1, 12)).slice(0, 7)];
    return { attendance: state.attendance?.revision || "",
      schedule: Object.fromEntries(periods.map((month) => [month, sources[month]?.schedule || ""])) };
  }

  function rememberCell(target) {
    const frame = document.querySelector(`#${target.kind}-table`).closest(".table-frame");
    returnPoint = {
      ...target, month: monthFor(target.kind), scrollLeft: frame.scrollLeft, scrollTop: frame.scrollTop,
    };
  }

  function restoreCell() {
    const point = returnPoint;
    if (!point || state.view !== point.kind || monthFor(point.kind) !== point.month) return;
    const table = document.querySelector(`#${point.kind}-table`);
    const target = point.kind === "schedule"
      ? [...table.querySelectorAll("[data-shift-day]")].find((cell) =>
        cell.dataset.shiftRow === point.rowId && cell.dataset.shiftDay === point.date)
      : [...table.querySelectorAll('[data-action="attendance-code"]')].find((cell) =>
        Number(cell.dataset.person) === point.employee_id && cell.dataset.day === point.date);
    const frame = table.closest(".table-frame");
    frame.scrollLeft = point.scrollLeft;
    frame.scrollTop = point.scrollTop;
    target?.focus({ preventScroll: true });
  }

  function showCellPanel(dialog, target) {
    rememberCell({ kind: "schedule", rowId: target.rowId, employee_id: target.employeeId, date: target.day });
    dialog.classList.add("easy-cell-panel");
    if (!dialog.dataset.usabilityPanel) {
      dialog.dataset.usabilityPanel = "true";
      dialog.addEventListener("close", restoreCell);
      dialog.addEventListener("keydown", (event) => {
        if (event.key === "Escape") { event.preventDefault(); dialog.close(); }
      });
    }
    if (!dialog.open) dialog.show();
    dialog.querySelector("button")?.focus({ preventScroll: true });
  }

  function toolSearch() {
    const menu = document.getElementById("tools-menu");
    const groups = [...menu.querySelectorAll(".easy-tool-group")];
    const entries = [...menu.querySelectorAll("[data-tool], [data-easy-view]")].map((node) => ({
      node, label: node.textContent, id: node.dataset.menuId,
      category: node.closest(".easy-tool-group").querySelector(".easy-tool-heading").textContent,
    }));
    entries.push(...["schedule", "attendance"].map((kind) => ({
      node: document.getElementById(`export-${kind}`), id: "", category: "Raporlar ve çıktılar",
      label: `${kind === "schedule" ? "Vardiya" : "Puantaj"} Excel al`,
    })));
    const search = document.createElement("div");
    search.className = "easy-tool-search";
    search.innerHTML = '<label>Araç veya işlem ara<input id="easy-tools-search" type="search" autocomplete="off" placeholder="Ör. izin gir, Excel al, personel bul"></label><p data-search-count role="status"></p><div data-search-results></div>';
    menu.prepend(search);
    const input = search.querySelector("input");
    const results = search.querySelector("[data-search-results]");
    const aliases = { izin: "izin gir talep tatil", personel: "personel bul sicil calisan",
      excel: "excel al indir aktar cikti", yedek: "yedek kurtar", puantaj: "puantaj kontrol" };
    const render = () => {
      const terms = fold(input.value).trim().split(/\s+/).filter(Boolean);
      groups.forEach((group) => { group.hidden = terms.length > 0; });
      results.replaceChildren();
      if (!terms.length) { search.querySelector("[data-search-count]").textContent = ""; return; }
      const matches = entries.filter((entry) => {
        let text = fold(`${entry.label} ${entry.category}`);
        for (const [key, value] of Object.entries(aliases)) if (text.includes(key)) text += ` ${value}`;
        return terms.every((term) => text.includes(term));
      });
      search.querySelector("[data-search-count]").textContent = matches.length
        ? `${matches.length} işlem bulundu.` : "Eşleşme yok. Daha kısa bir işlem adı deneyin.";
      matches.forEach((entry) => {
        const row = document.createElement("div");
        row.className = "easy-search-result";
        const open = button(`${entry.label} · ${entry.category}`, () => {
          menu.hidden = true;
          document.getElementById("tools-menu-button").setAttribute("aria-expanded", "false");
          document.getElementById("tools-menu-button").focus();
          entry.node.click();
        }, "");
        open.disabled = entry.node.disabled;
        row.append(open);
        if (entry.node.closest(".easy-tool-group") && !entry.node.disabled && !entry.node.hidden) {
          const reveal = button("Yerini göster", () => {
            input.value = "";
            render();
            entry.node.dispatchEvent(new Event("easy-reveal-tool", { bubbles: true }));
          }, "easy-search-pin");
          reveal.setAttribute("aria-label", `${entry.label}: menüdeki yerini göster`);
          reveal.addEventListener("click", (event) => event.stopPropagation());
          row.append(reveal);
        }
        if (entry.id?.startsWith("tool:")) {
          const pinned = state.uiSettings.easy_menu?.order.includes(entry.id);
          const pin = button(pinned ? "Sabiti kaldır" : "Sabitle", async () => {
            if (await window.easyMenu.setPinned(entry.id, !pinned)) render();
          }, "easy-search-pin");
          pin.setAttribute("aria-label", `${entry.label}: ${pinned ? "sabiti kaldır" : "kişisel şeride sabitle"}`);
          row.append(pin);
        }
        results.append(row);
      });
    };
    input.addEventListener("input", render);
    search.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" && event.target === input) {
        event.preventDefault();
        results.querySelector("button:not(:disabled)")?.focus();
      }
      if (event.key === "Escape" && input.value) {
        event.preventDefault(); event.stopPropagation(); input.value = ""; render(); input.focus();
      }
    });
    document.addEventListener("ui-settings-changed", render);
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k"
          && !document.querySelector("dialog[open]:not(.easy-cell-panel):not(.hub-embedded)")) {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (menu.hidden) document.getElementById("tools-menu-button").click();
        input.focus();
      }
    }, true);
    const common = /Yeni ay|Bugünün|Bağlantılı izin|Personel listesi|Personel talepleri|PDKS|Puantaj onay|Ay sonu|Kaydetmeden|Çalışma düzenleri|Göreve göre|Yayınlanan|Eksik \/|Operasyon merkezi|Başlangıç rehberi|Toplu giriş|Kısayol halkası|Menüyü özelleştir/;
    groups.forEach((group) => {
      if (group.querySelector("[data-easy-view]")) return;
      const items = group.querySelector(".easy-tool-items");
      const advanced = [...items.children].filter((node) => node.matches("button") && !common.test(node.textContent));
      if (advanced.length < 3) return;
      const details = document.createElement("details");
      details.className = "easy-advanced-tools";
      const summary = document.createElement("summary");
      summary.textContent = `Gelişmiş (${advanced.length})`;
      details.append(summary, ...advanced);
      items.append(details);
    });
  }

  function repaintScheduleSelection() {
    if (selected.size && [...selected.values()].some((cell) => cell.month !== state.scheduleMonth)) selected.clear();
    document.querySelectorAll("#schedule-table [data-shift-day]").forEach((cell) => {
      const key = `${cell.dataset.shiftRow}:${cell.dataset.shiftDay}`;
      cell.classList.toggle("easy-bulk-selected", selected.has(key));
    });
    const bar = document.getElementById("easy-schedule-actions");
    if (bar) {
      bar.hidden = false;
      bar.querySelector("span").textContent = `${selected.size} vardiya gün hücresi seçili`;
      bar.querySelector("[data-schedule-select]").setAttribute("aria-pressed", String(scheduleSelection));
      bar.querySelectorAll("button:not([data-schedule-select])").forEach((node) => { node.disabled = !selected.size; });
    }
  }

  function scheduleCell(cell) {
    const target = scheduleTargetForCell(cell);
    return target?.rowId ? { kind: "schedule", rowId: target.rowId, employee_id: target.employeeId,
      section: target.section, date: target.day, month: state.scheduleMonth } : null;
  }

  function currentCells(kind) {
    if (kind === "attendance") return easyAssistant.selectedCells();
    const visible = new Set([...document.querySelectorAll("#schedule-table [data-shift-row]")]
      .map((cell) => `${cell.dataset.shiftRow}:${cell.dataset.shiftDay}`));
    return [...selected.values()].filter((cell) => cell.month === state.scheduleMonth
      && visible.has(`${cell.rowId}:${cell.date}`) && state.schedule.entries.some((row) =>
        row.row_id === cell.rowId && row.employee_id === cell.employee_id && row.section === cell.section));
  }

  function selectedScheduleRectangle(target, add) {
    if (!add) selected.clear();
    const rows = [...new Set([...document.querySelectorAll("#schedule-table [data-shift-row]")]
      .map((cell) => cell.dataset.shiftRow))];
    const days = [...new Set([...document.querySelectorAll("#schedule-table [data-shift-day]")]
      .map((cell) => cell.dataset.shiftDay))].sort();
    const first = scheduleAnchor || target;
    const a = rows.indexOf(first.rowId), b = rows.indexOf(target.rowId);
    const c = days.indexOf(first.date), d = days.indexOf(target.date);
    if ([a, b, c, d].some((value) => value < 0)) return;
    const rowById = new Map(state.schedule.entries.map((row) => [row.row_id, row]));
    for (let r = Math.min(a, b); r <= Math.max(a, b); r++) {
      for (let column = Math.min(c, d); column <= Math.max(c, d); column++) {
        const row = rowById.get(rows[r]);
        const cell = { kind: "schedule", rowId: row.row_id, employee_id: row.employee_id,
          section: row.section, date: days[column], month: state.scheduleMonth };
        selected.set(`${cell.rowId}:${cell.date}`, cell);
      }
    }
  }

  function candidateSnapshot(kind, cells, values) {
    if (!cells.length || cells.length !== values.length) throw new Error("Hücre seçimi ve giriş sayısı uyuşmuyor.");
    const snapshot = editSnapshot(kind);
    cells.forEach((cell, index) => {
      if (kind === "attendance") {
        const code = String(values[index] ?? "").trim().toLocaleUpperCase("tr-TR");
        if (!ATTENDANCE_CODES.includes(code) && !/^[A-ZÇĞİÖŞÜ0-9. -]{1,8}$/.test(code)) throw new Error(`Geçersiz puantaj kodu: ${code}. Kod açıklamalarını kullanın.`);
        (snapshot.overrides[cell.date] ||= {})[String(cell.employee_id)] = code;
      } else {
        const rows = cell.date.slice(0, 7) === state.scheduleMonth ? snapshot.entries
          : snapshot.neighborEntries[cell.date.slice(0, 7)];
        if (!rows) throw new Error("Komşu ay yüklenmedi. Ayı yeniden açın.");
        const source = state.schedule.entries.find((row) => row.row_id === cell.rowId);
        let row = rows.find((item) => item.employee_id === cell.employee_id && item.section === cell.section);
        if (!row && values[index]) {
          const used = new Set(rows.filter((item) => item.section === cell.section).map((item) => item.slot));
          const slot = Array.from({ length: SECTION_LIMITS[cell.section] }, (_, position) => position + 1)
            .find((value) => !used.has(value));
          if (!slot) throw new Error(`${cell.date.slice(0, 7)} ${cell.section} bölümünde boş satır yok.`);
          row = { ...clone(source), row_id: newRowId(), slot, days: {} };
          rows.push(row);
        }
        if (row) {
          if (values[index]) row.days[cell.date] = clone(values[index]);
          else delete row.days[cell.date];
        }
      }
    });
    return snapshot;
  }

  function cellValue(kind, cell, snapshot) {
    if (kind === "attendance") {
      const manual = snapshot.overrides[cell.date]?.[String(cell.employee_id)];
      return manual !== undefined ? manual : state.attendance.rows.find((row) =>
        row.employee_id === cell.employee_id)?.automatic_codes[cell.date] || "";
    }
    const rows = cell.date.slice(0, 7) === state.scheduleMonth ? snapshot.entries
      : snapshot.neighborEntries[cell.date.slice(0, 7)] || [];
    return rows.find((row) => row.employee_id === cell.employee_id && row.section === cell.section)?.days[cell.date] || null;
  }

  const valueLabel = (value) => !value ? "Boş" : typeof value === "string" ? value
    : value.kind === "work" ? `${value.start}–${value.end}` : SHIFT_OPTIONS.find(([key]) => key === value.kind)?.[1] || value.kind;

  function changedCells(kind, cells, before, proposed) {
    const candidates = new Map(cells.map((cell) => [`${cell.employee_id}:${cell.section || ""}:${cell.date}`, cell]));
    if (kind === "schedule") {
      for (const snapshot of [before, proposed]) {
        for (const row of [snapshot.entries, ...Object.values(snapshot.neighborEntries)].flat()) {
          for (const date of Object.keys(row.days)) candidates.set(`${row.employee_id}:${row.section}:${date}`,
            { rowId: row.row_id, employee_id: row.employee_id, section: row.section, date });
        }
      }
    }
    return [...candidates.values()].filter((cell) => !same(cellValue(kind, cell, before), cellValue(kind, cell, proposed)));
  }

  function setBulkScope(dialog, kind, cells, changed = false) {
    const scope = dialog.querySelector("[data-extras-scope]");
    if (!scope) return;
    scope.setAttribute("aria-label", "İşlem kapsamı");
    if (!cells.length) {
      scope.textContent = "Değişiklik yok";
      return;
    }
    const months = [...new Set(cells.map((cell) => cell.date.slice(0, 7)))].sort()
      .map(extrasMonthLabel);
    const sections = kind === "schedule" ? [...new Set(cells.map((cell) => cell.section).filter(Boolean))] : [];
    const people = new Set(cells.map((cell) => cell.employee_id).filter(Boolean));
    scope.textContent = [...months, ...sections, `${people.size} ${changed ? "etkilenen" : "seçili"} personel`].join(" · ");
  }

  async function previewBulk(kind, cells, values, opener) {
    if (!canWrite(kind)) throw new Error("Bu tabloyu düzenleme yetkiniz yok.");
    if (state.formatMode) throw new Error("Önce biçimlendirme modundan çıkın.");
    const month = monthFor(kind);
    const before = editSnapshot(kind);
    const revisions = revisionsFor(kind);
    const proposed = candidateSnapshot(kind, cells, values);
    let findings = [];
    let skipped = [];
    if (kind === "schedule") {
      const months = Object.fromEntries(Object.entries({ [month]: proposed.entries, ...proposed.neighborEntries })
        .map(([period, entries]) => [period, { entries, revision: revisions[period] }]));
      const result = await request("/api/schedule/bulk-check", {
        method: "POST", body: JSON.stringify({ month, months }),
      });
      proposed.entries = result.months[month].entries;
      proposed.neighborEntries = Object.fromEntries(Object.entries(result.months)
        .filter(([period]) => period !== month).map(([period, data]) => [period, data.entries]));
      findings = result.findings;
      skipped = result.skipped || [];
    } else {
      const result = await request("/api/attendance/bulk-check", {
        method: "POST", body: JSON.stringify({ month, snapshot: proposed, revisions }),
      });
      Object.assign(proposed, result.snapshot);
    }
    if (monthFor(kind) !== month || !same(before, editSnapshot(kind))) throw new Error("Önizleme hazırlanırken tablo değişti; yeniden deneyin.");
    const dialog = extrasDialog("easy-bulk-preview", "Toplu giriş önizlemesi",
      "Yalnız aşağıdaki değişiklikler çalışma taslağına uygulanır. Kalıcı kayıt için ayrıca Kaydet gerekir; tek Geri al ile geri dönebilirsiniz.");
    const changes = changedCells(kind, cells, before, proposed);
    setBulkScope(dialog, kind, changes, true);
    dialog.querySelector(".extras-body").innerHTML = `<p>${changes.length} hücre değişecek. ${kind === "schedule"
      ? "Bağlı izinler dahil tüm değişen günler gösterilir; plan kuralları kontrol edildi."
      : "Manuel puantaj kodları değişir; çalışma ve ücret hesaplama kuralları değişmez."} Kaydet sırasında tekrar doğrulanır.</p>
      <div class="controls-table-wrap"><table class="extras-table"><thead><tr><th>Personel</th><th>Tarih</th><th>Önce</th><th>Sonra</th></tr></thead><tbody>${changes.map((cell) =>
        `<tr><td>${escapeHtml(nameFor(cell.employee_id))}</td><td>${escapeHtml(cell.date)}</td><td>${escapeHtml(valueLabel(cellValue(kind, cell, before)))}</td><td>${escapeHtml(valueLabel(cellValue(kind, cell, proposed)))}</td></tr>`).join("")}</tbody></table></div>
      ${skipped.length ? `<p>${skipped.length} korunan hücre atlandı:</p><ul>${skipped.map((item) =>
        `<li>${escapeHtml(nameFor(item.employee_id))} · ${escapeHtml(item.section)} · ${escapeHtml(item.day)}</li>`).join("")}</ul>` : ""}
      ${findings.length ? `<p>${findings.length} plan bulgusu var. Uyarı, izin veya hukuki onay değildir.</p><ul>${findings.slice(0, 20).map((item) => `<li>${escapeHtml(item.message)}</li>`).join("")}</ul>` : ""}`;
    const apply = button("Onayla ve taslağa uygula", async () => {
      if (!canWrite(kind) || monthFor(kind) !== month || !same(before, editSnapshot(kind))
          || !same(revisions, revisionsFor(kind))) throw new Error("Tablo veya yetki değişti; önizlemeyi yeniden hazırlayın.");
      const current = await request(`/api/${kind}/workspace?month=${month}`);
      if (current.locked || !same(current.revisions, revisions)) throw new Error("Kayıtlı kaynak veya dönem kilidi değişti; önizlemeyi yeniden hazırlayın.");
      if (kind === "schedule") {
        const verified = await request("/api/schedule/bulk-check", {
          method: "POST", body: JSON.stringify({ month, months: Object.fromEntries(
            Object.entries({ [month]: proposed.entries, ...proposed.neighborEntries })
              .map(([period, entries]) => [period, { entries, revision: revisions[period] }])) }),
        });
        const expected = Object.fromEntries(Object.entries({ [month]: proposed.entries, ...proposed.neighborEntries })
          .map(([period, entries]) => [period, { entries, revision: revisions[period] }]));
        if (!same(verified.months, expected)) throw new Error("Bağlı izin veya plan değişti; bütün değişiklikleri yeniden önizleyin.");
      } else {
        await request("/api/attendance/bulk-check", {
          method: "POST", body: JSON.stringify({ month, snapshot: proposed, revisions }),
        });
      }
      if (state.view !== kind || monthFor(kind) !== month || !same(before, editSnapshot(kind))) throw new Error("Açık tablo değişti; işlem uygulanmadı.");
      applyEditSnapshot(kind, proposed);
      recordEditChange(kind);
      dialog.close();
      toast("Toplu giriş taslağa uygulandı. Kalıcı kayıt için Kaydet; vazgeçmek için Geri al.");
    }, "button button-primary");
    apply.disabled = !changes.length;
    dialog.querySelector(".extras-actions").prepend(apply);
    showExtras(dialog, opener);
  }

  function openBulk(kind = state.view, opener) {
    if (!["schedule", "attendance"].includes(kind)) throw new Error("Önce vardiya veya puantaj tablosunu açın.");
    const cells = currentCells(kind);
    if (!cells.length) throw new Error("Önce gün hücrelerini seçin. Ctrl ayrı gün, Shift dikdörtgen seçim yapar.");
    const dialog = extrasDialog("easy-bulk-editor", "Toplu giriş çalışma alanı",
      `${cells.length} gün hücresi · Seçili günleri doldurun, haftayı kopyalayın veya Excel'den sekmeyle ayrılmış kodları yapıştırın.`);
    setBulkScope(dialog, kind, cells);
    const body = dialog.querySelector(".extras-body");
    body.innerHTML = `<label>Giriş kaynağı<select id="easy-bulk-source">
      <option value="same">Seçili günlere aynı değer</option>
      <option value="clipboard">Kopyalanan hücreleri sırayla yapıştır</option>
      ${kind === "attendance" ? '<option value="text">Excel’den kod tablosu yapıştır</option>' : ""}
      </select></label><div data-bulk-value></div><p data-bulk-order></p>`;
    const value = body.querySelector("[data-bulk-value]");
    if (kind === "attendance") {
      value.innerHTML = `<label>Puantaj kodu<select id="easy-bulk-code">${ATTENDANCE_CODES.map((code) =>
        `<option value="${escapeHtml(code)}">${escapeHtml(code || "Boş")}</option>`).join("")}</select></label>
        <label>Excel’den yapıştırılacak kodlar<textarea id="easy-bulk-text" rows="4" placeholder="Her satır bir kişi, sütunlar günler"></textarea></label>`;
    } else {
      value.innerHTML = `<label>Vardiya veya durum<select id="easy-bulk-entry">${allQuickEntries().map((entry, index) =>
        `<option value="${index}">${escapeHtml(entry.label)}</option>`).join("")}<option value="clear">Hücreyi temizle</option></select></label>`;
    }
    const ordered = [...cells].sort((a, b) => a.employee_id - b.employee_id || a.date.localeCompare(b.date));
    body.querySelector("[data-bulk-order]").textContent =
      "Sıra: personelin tablodaki sırası, ardından tarih. Excel tablosu seçimin tam dikdörtgeniyle aynı boyutta olmalıdır.";
    const order = kind === "attendance" ? state.attendance.rows.map((row) => row.employee_id)
      : state.schedule.entries.map((row) => row.row_id);
    ordered.sort((a, b) => order.indexOf(kind === "attendance" ? a.employee_id : a.rowId)
      - order.indexOf(kind === "attendance" ? b.employee_id : b.rowId) || a.date.localeCompare(b.date));
    body.append(button("Seçimi kopyala", () => {
      bulkClipboard = { kind, values: ordered.map((cell) => clone(cellValue(kind, cell, editSnapshot(kind)))) };
      toast(`${ordered.length} hücre uygulama panosuna kopyalandı.`);
    }));
    if (kind === "schedule") body.append(button("Seçili kişinin haftasını kopyala", () => {
      const ids = new Set(ordered.map((cell) => cell.rowId));
      if (ids.size !== 1) throw new Error("Hafta kopyalamak için tek kişinin gününü veya günlerini seçin.");
      const cell = ordered[0];
      const start = weekStarts(state.scheduleMonth)[state.weekIndex];
      const snapshot = editSnapshot(kind);
      bulkClipboard = { kind, values: Array.from({ length: 7 }, (_, index) =>
        cellValue(kind, { ...cell, date: dateKey(localDate(start.getFullYear(), start.getMonth(), start.getDate() + index)) }, snapshot)) };
      toast("Kişinin 7 günlük haftası kopyalandı. Hedefte 7 gün seçip önizleyin.");
    }));
    const previewButton = button("Önizlemeyi hazırla", async () => {
      let values;
      const source = body.querySelector("#easy-bulk-source").value;
      if (source === "clipboard") {
        if (bulkClipboard?.kind !== kind || bulkClipboard.values.length !== ordered.length) {
          throw new Error("Kopyalanan tablo türü veya hücre sayısı seçimle uyuşmuyor.");
        }
        values = clone(bulkClipboard.values);
      } else if (source === "text") {
        const rows = body.querySelector("#easy-bulk-text").value.replace(/\r/g, "").replace(/\n$/, "").split("\n").map((row) => row.split("\t"));
        const selectedPeople = [...new Set(ordered.map((cell) => cell.employee_id))];
        const dates = [...new Set(ordered.map((cell) => cell.date))].sort();
        if (ordered.length !== selectedPeople.length * dates.length || rows.length !== selectedPeople.length
            || rows.some((row) => row.length !== dates.length)) throw new Error("Excel satır/sütun sayısı seçili dikdörtgenle aynı olmalıdır; ayrık seçim kullanmayın.");
        values = rows.flat();
      } else {
        const chosen = kind === "attendance" ? body.querySelector("#easy-bulk-code").value
          : body.querySelector("#easy-bulk-entry").value;
        const entry = kind === "schedule" && chosen !== "clear" ? allQuickEntries()[Number(chosen)] : null;
        values = ordered.map(() => kind === "attendance" ? chosen : entry ? {
          ...scheduleItemForQuickEntry(entry), fill_color: entry.color || "#ffffff",
        } : null);
      }
      await previewBulk(kind, ordered, values, previewButton);
      dialog.close();
    }, "button button-primary");
    body.append(previewButton);
    showExtras(dialog, opener);
  }

  function openAttendancePanel() {
    const cells = currentCells("attendance");
    if (!cells.length) throw new Error("Önce puantajda gün seçin.");
    rememberCell({ kind: "attendance", ...cells[0] });
    const dialog = extrasDialog("easy-attendance-actions", "Seçili gün işlemleri",
      `${new Set(cells.map((cell) => cell.employee_id)).size} personel · ${cells.length} gün hücresi`);
    dialog.classList.add("easy-cell-panel");
    dialog.addEventListener("close", restoreCell, { once: true });
    const body = dialog.querySelector(".extras-body");
    body.append(button("Kod gir / kopyala / yapıştır", () => { dialog.close(); openBulk("attendance"); }),
      button("İzin kaydı oluştur", () => { dialog.close(); window.linkedLeave.openSelection(cells); }),
      button("Tutanak / belge hazırla", () => { dialog.close(); return window.documentWorkflows.openSelection(cells, { editable: true }); }),
      button("Hesap açıklaması", () => { dialog.close(); return window.documentWorkflows.openCalculation(cells[0].employee_id); }));
    dialog.show();
  }

  function freezeHeaders(kind) {
    const table = document.getElementById(`${kind}-table`);
    if (!table || state.formatMode) return;
    table.querySelectorAll(".easy-frozen-name, .easy-frozen-date").forEach((cell) => {
      cell.classList.remove("easy-frozen-name", "easy-frozen-date");
      cell.style.removeProperty("--freeze-left"); cell.style.removeProperty("--freeze-top");
    });
    const scale = Number(table.style.zoom) || 1;
    const corners = table.querySelector("thead");
    const baseTop = corners ? corners.getBoundingClientRect().height / scale : 0;
    const nameCells = kind === "attendance" ? [...table.querySelectorAll(".attendance-person-name")]
      : [...table.querySelectorAll(".employee-name-editor")].map((node) => node.closest("td"));
    for (const cell of nameCells) cell.classList.add("easy-frozen-name");
    // Keep attendance dates in the scroll flow so they never cover editable rows.
    const dateCells = kind === "attendance" ? []
      : table.querySelectorAll(".easy-schedule-date");
    for (const cell of dateCells) {
      cell.classList.add("easy-frozen-date");
      cell.style.setProperty("--freeze-top", `${baseTop}px`);
    }
  }

  function paintStatus() {
    for (const kind of ["schedule", "attendance"]) {
      const node = document.querySelector(`#easy-${kind}-record-state`);
      if (!node) continue;
      const dirty = state[`${kind}Dirty`] || (kind === "schedule" && state.neighborDirty.size);
      const status = workspace[kind];
      const current = status?.month === monthFor(kind) ? status : null;
      const draft = draftState[kind];
      node.textContent = `${dirty ? "Kaydedilmedi" : current ? current.saved ? "Kaydedildi" : "Henüz kayıt yok" : "Kayıt durumu alınıyor"}
        ${current?.locked ? " · Dönem kilitli" : ""}
        ${kind === "schedule" && current ? ` · ${{ none: "Yayımlanmadı", current: dirty ? "Yayın kayıtlı sürüme ait" : "Yayımlanan sürüm güncel", changed: "Yayın güncellenmeli" }[current.published]}` : ""}
        ${draft?.message ? ` · ${draft.message}` : ""}`;
      node.dataset.dirty = String(Boolean(dirty));
    }
  }

  async function refreshStatus(kind = state.view) {
    if (!["schedule", "attendance"].includes(kind) || !accessCan(`${kind}.read`) || !dataFor(kind)) return;
    const month = monthFor(kind);
    try {
      const result = await request(`/api/${kind}/workspace?month=${month}`);
      if (month === monthFor(kind)) { workspace[kind] = result; paintStatus(); }
    } catch (error) {
      workspace[kind] = null;
      const node = document.querySelector(`#easy-${kind}-record-state`);
      if (node) node.textContent = `Kayıt durumu alınamadı: ${error.message}`;
    }
  }

  function scheduleStatus() {
    paintStatus();
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => refreshStatus(), 300);
  }

  function installRecovery() {
    const previousWrite = writeDraft, previousClear = clearDraft, previousOffer = offerDraft;
    const owner = () => accessState.user ? `user:${accessState.user.username.toLocaleLowerCase("tr-TR")}` : "anonymous";
    const context = (kind) => {
      const month = monthFor(kind);
      if (draftState[kind]?.month !== month || draftState[kind]?.owner !== owner()) draftState[kind] = {
        month, owner: owner(), record: null, blocked: false, ready: false, message: "", queue: Promise.resolve(),
      };
      return draftState[kind];
    };
    const notify = (entry, message) => { entry.message = message; paintStatus(); };
    writeDraft = function persistentDraft(kind) {
      if (!document.body.classList.contains("easy-mode")) return previousWrite(kind);
      clearTimeout(features.draftTimers[kind]);
      const entry = context(kind);
      features.draftTimers[kind] = setTimeout(() => {
        if (!entry.ready || entry.blocked || !canWrite(kind) || monthFor(kind) !== entry.month) return;
        const payload = { month: entry.month, snapshot: editSnapshot(kind), revisions: revisionsFor(kind) };
        entry.queue = entry.queue.then(async () => {
          if (entry.blocked || entry.owner !== owner()
              || (monthFor(kind) === entry.month && (!state[`${kind}Dirty`] || !same(payload.revisions, revisionsFor(kind))))) return;
          try {
            const result = await request(`/api/${kind}/draft`, {
              method: "PUT", body: JSON.stringify({ ...payload, draft_revision: entry.record?.draft_revision || "" }),
            });
            entry.record = result.draft;
            notify(entry, "Kurtarma taslağı saklandı; kalıcı plan değildir");
          } catch (error) {
            entry.blocked = true;
            notify(entry, "Kurtarma taslağı saklanamadı");
            toast(`Taslak saklanamadı: ${error.message} Tabloyu kapatmadan Kaydet veya değişiklikleri dışarı not edin.`, true);
          }
        });
      }, 700);
    };
    clearDraft = function clearPersistentDraft(kind) {
      if (!document.body.classList.contains("easy-mode")) return previousClear(kind);
      clearTimeout(features.draftTimers[kind]);
      const entry = context(kind);
      if (features.loading[kind] || !entry.ready || entry.blocked) return;
      entry.queue = entry.queue.then(async () => {
        if (!entry.record || entry.owner !== owner()) return;
        try {
          await request(`/api/${kind}/draft/discard`, { method: "POST",
            body: JSON.stringify({ month: entry.month, draft_revision: entry.record.draft_revision }) });
          entry.record = null;
          notify(entry, "");
        } catch (error) { notify(entry, "Eski kurtarma taslağı kaldırılamadı"); toast(error.message, true); }
      });
    };
    offerDraft = async function offerPersistentDraft(kind) {
      if (!document.body.classList.contains("easy-mode")) return previousOffer(kind);
      if (!canWrite(kind)) return;
      const entry = context(kind);
      try {
        const { draft } = await request(`/api/${kind}/draft?month=${entry.month}`);
        if (monthFor(kind) !== entry.month || entry.owner !== owner()) return;
        entry.ready = true;
        entry.record = draft;
        if (!draft) return;
        if (same(editSnapshot(kind), draft.snapshot)) { clearDraft(kind); return; }
        entry.blocked = true;
        const dialog = extrasDialog(`draft-${kind}-dialog`, "Kaydedilmemiş taslak bulundu",
          `${entry.month} · ${new Date(draft.at).toLocaleString("tr-TR")} · Bu kayıt kalıcı vardiya veya puantaj değildir.`);
        const stale = draft.stale || !same(draft.revisions, revisionsFor(kind));
        dialog.querySelector(".extras-body").innerHTML = `<p>${stale
          ? "Kaynak veya komşu aylar değişmiş. Veri kaybını önlemek için doğrudan geri yükleme kapalıdır. Taslağı indirip güncel kayıtla karşılaştırın."
          : "Taslağı geri yükledikten sonra değişiklikleri inceleyip ayrıca Kaydetmeniz gerekir."}</p>`;
        const actions = dialog.querySelector(".extras-actions");
        actions.prepend(button("Taslağı indir", () => {
          const blob = new Blob([JSON.stringify({ kind, month: entry.month, ...draft }, null, 2)], { type: "application/json" });
          const link = document.createElement("a");
          link.href = URL.createObjectURL(blob); link.download = `TASLAK_${kind}_${entry.month}.json`;
          link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        }), button("Taslağı sil", async () => {
          if (!window.confirm("Kurtarma taslağı silinsin mi? Kayıtlı çizelge değişmez.")) return;
          await request(`/api/${kind}/draft/discard`, { method: "POST",
            body: JSON.stringify({ month: entry.month, draft_revision: draft.draft_revision }) });
          entry.record = null; entry.blocked = false; notify(entry, ""); dialog.close();
        }));
        const restore = button("Taslağı geri yükle", async () => {
          const fresh = await request(`/api/${kind}/draft?month=${entry.month}`);
          if (!canWrite(kind) || entry.owner !== owner() || fresh.draft?.stale || monthFor(kind) !== entry.month
              || fresh.draft?.draft_revision !== draft.draft_revision || !same(draft.revisions, revisionsFor(kind))) {
            throw new Error("Kaynak, taslak veya yetki değişti; geri yükleme uygulanmadı.");
          }
          applyEditSnapshot(kind, draft.snapshot);
          recordEditChange(kind);
          entry.blocked = false;
          dialog.close(); notify(entry, "Kurtarılan taslak henüz kaydedilmedi");
        }, "button button-primary");
        restore.disabled = stale;
        actions.prepend(restore);
        notify(entry, stale ? "Eski kaynaklı kurtarma taslağı var" : "Kurtarma taslağı bekliyor");
        showExtras(dialog);
      } catch (error) {
        entry.blocked = true;
        notify(entry, "Taslak kurtarma kullanılamıyor");
        toast(`Taslak kurtarma kontrol edilemedi: ${error.message}`, true);
      }
    };
  }

  function openMetrics() {
    const dialog = extrasDialog("easy-usage-metrics", "Kullanım ve hız ölçümü",
      "Yalnız bu uygulama oturumundaki süre ve tıklama sayılarıdır; isim, hücre içeriği veya ücret kaydedilmez ve dışarı gönderilmez.");
    const body = dialog.querySelector(".extras-body");
    body.innerHTML = `<p>Bu oturumdaki tıklamalar: <strong>${sessionClicks}</strong></p><div class="controls-table-wrap"><table class="extras-table"><thead><tr><th>İşlem</th><th>Süre (ms)</th></tr></thead><tbody>${metrics.slice(-30).map((item) =>
      `<tr><td>${escapeHtml(item.name)}</td><td>${item.ms.toFixed(1)}</td></tr>`).join("")}</tbody></table></div>`;
    showExtras(dialog);
  }
  function deduplicateAlerts(report) {
    const seen = new Set();
    const items = report.items.filter((item) => {
      const key = JSON.stringify(canonical({
        type: item.type, severity: item.severity, employee_id: item.employee_id,
        date: item.date, section: item.section, message: item.message,
      }));
      if (seen.has(key)) return false;
      seen.add(key); return true;
    });
    const counts = Object.fromEntries(["error", "warning", "info"].map((severity) =>
      [severity, items.filter((item) => item.severity === severity).length]));
    return { ...report, items, counts };
  }

  async function goToFinding(item, month) {
    const scheduleTypes = new Set(["overlap", "rest", "legal_rest", "legal_weekly_rest",
      "night_rotation", "min_staffing", "leave_request_conflict", "skill", "after_exit"]);
    const kind = scheduleTypes.has(item.type) && accessCan("schedule.read") ? "schedule" : "attendance";
    if (state[`${state.view}Dirty`] && (state.view !== kind || monthFor(kind) !== month)) {
      throw new Error("Önce açık tablodaki değişiklikleri kaydedin veya geri alın; uyarı konumuna geçilmedi.");
    }
    if (state.view !== kind) await switchView(kind);
    if (state.view !== kind) return;
    if (monthFor(kind) !== month) {
      state[`${kind}Month`] = month;
      document.getElementById(`${kind}-month`).value = month;
      await (kind === "schedule" ? loadSchedule() : loadAttendance());
    }
    const target = document.createElement("button");
    target.dataset.checkTarget = kind;
    target.dataset.person = item.employee_id || "";
    target.dataset.day = Array.isArray(item.date) ? item.date[0] : item.date || `${month}-01`;
    if (kind === "schedule") {
      const row = state.schedule.entries.find((row) =>
        (!item.employee_id || row.employee_id === item.employee_id) && (!item.section || row.section === item.section));
      if (!row) throw new Error("Bulguya ait personel satırı yok. Kadro veya kayıtlı personel listesini inceleyin.");
      target.dataset.row = row.row_id; target.dataset.section = row.section;
    }
    await handlePeriodCheckClick({ target });
  }
  let sessionClicks = 0;
  function recordSave(kind, started) {
    metrics.push({ name: `${kind}: kayıt ${state[`${kind}Dirty`] ? "tamamlanamadı" : "tamamlandı"}`,
      ms: performance.now() - started });
    if (metrics.length > 100) metrics.shift();
    scheduleStatus();
  }
  function measure(name, action) {
    const started = performance.now();
    const result = action();
    const record = () => {
      metrics.push({ name, ms: performance.now() - started });
      if (metrics.length > 100) metrics.shift();
    };
    if (result?.then) return result.then((value) => { record(); return value; });
    record(); return result;
  }

  function setup() {
    toolSearch();
    installRecovery();
    document.addEventListener("click", () => { sessionClicks++; }, { capture: true });
    const bar = document.createElement("div");
    bar.id = "easy-schedule-actions"; bar.className = "easy-cell-actions";
    bar.innerHTML = "<span></span>";
    const toggle = button("Çoklu seçim", () => {
      scheduleSelection = !scheduleSelection; repaintScheduleSelection();
    });
    toggle.dataset.scheduleSelect = "";
    const bulkButton = button("Toplu giriş", () => openBulk("schedule", bulkButton));
    bar.append(toggle, bulkButton, button("Seçimi temizle", () => {
      selected.clear(); scheduleAnchor = null; scheduleSelection = false; repaintScheduleSelection();
    }));
    document.getElementById("schedule-table").closest(".table-frame").before(bar);
    document.getElementById("easy-cell-actions").append(button("Gün işlemleri", openAttendancePanel));
    for (const kind of ["schedule", "attendance"]) {
      const record = document.createElement("p");
      record.id = `easy-${kind}-record-state`; record.className = "easy-record-state";
      record.setAttribute("role", "status");
      document.getElementById(`${kind}-table`).closest(".table-frame").before(record);
      const originalRender = window[kind === "schedule" ? "renderSchedule" : "renderAttendance"];
      window[kind === "schedule" ? "renderSchedule" : "renderAttendance"] = function measuredRender(...args) {
        const result = measure(`${kind}: tablo çizimi`, () => originalRender.apply(this, args));
        clearTimeout(freezeTimer);
        freezeTimer = setTimeout(() => {
          freezeHeaders(kind);
          if (kind === "schedule") repaintScheduleSelection();
        }, 0);
        return result;
      };
      const originalLoad = window[kind === "schedule" ? "loadSchedule" : "loadAttendance"];
      window[kind === "schedule" ? "loadSchedule" : "loadAttendance"] = async function measuredLoad(...args) {
        const result = await measure(`${kind}: ayı aç`, () => originalLoad.apply(this, args));
        scheduleStatus(); return result;
      };
    }
    document.addEventListener("pointerdown", (event) => {
      const cell = event.target.closest("#schedule-table [data-shift-day]");
      if (!cell || state.formatMode) return;
      const target = scheduleCell(cell);
      if (!target) return;
      if (scheduleSelection || event.ctrlKey || event.metaKey || event.shiftKey) {
        event.preventDefault(); event.stopImmediatePropagation();
        if (event.shiftKey && scheduleAnchor) selectedScheduleRectangle(target, event.ctrlKey || event.metaKey);
        else {
          const key = `${target.rowId}:${target.date}`;
          if (!(event.ctrlKey || event.metaKey || scheduleSelection)) selected.clear();
          if (selected.has(key)) selected.delete(key); else selected.set(key, target);
          scheduleAnchor = target;
        }
      } else {
        selected.clear(); selected.set(`${target.rowId}:${target.date}`, target); scheduleAnchor = target;
      }
      repaintScheduleSelection();
    }, true);
    document.addEventListener("click", (event) => {
      if (event.target.closest("#schedule-table [data-shift-day]") && !state.formatMode
          && (scheduleSelection || event.ctrlKey || event.metaKey || event.shiftKey)) {
        event.preventDefault(); event.stopImmediatePropagation();
      }
    }, true);
    document.addEventListener("focusin", (event) => {
      if (event.target.closest("#schedule-table, #attendance-table")) scheduleStatus();
    });
    const dirtyObserver = new MutationObserver(scheduleStatus);
    dirtyObserver.observe(document.getElementById("save-state-label"), { childList: true });
    document.addEventListener("ui-settings-changed", scheduleStatus);
    document.addEventListener("close", scheduleStatus, true);
    window.addEventListener("resize", () => { freezeHeaders("schedule"); freezeHeaders("attendance"); });
    scheduleStatus();
  }

  EXTRAS_MENU.push(["Toplu giriş çalışma alanı", () => openBulk()],
    ["Kullanım ve hız ölçümü", openMetrics]);
  window.previewWorkbookBulk = previewBulk;
  if (!document.querySelector('link[href="/easy-mode.css"]')) return;
  window.easyUsability = { showCellPanel, previewBulk, openBulk, currentCells, refreshStatus,
    deduplicateAlerts, goToFinding, recordSave,
    revisionsFor, freezeHeaders, fold, metrics };
  if (document.readyState !== "complete") document.addEventListener("DOMContentLoaded", setup, { once: true });
  else setup();
})();
