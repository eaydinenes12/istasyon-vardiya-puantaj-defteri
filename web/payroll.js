/* Bordro ekleri (kesinti açıklaması, avans, yemek/yol günü, tatil ücreti / telafi izni)
   ve denkleştirme raporu. */

const PAYROLL_CHOICES = { pay: "Ücret olarak öde", leave: "Telafi izni olarak biriktir" };

function payrollMoney(value) {
  return Number(value || 0).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function payrollHours(value) {
  return Number(value || 0).toLocaleString("tr-TR", { maximumFractionDigits: 2 });
}

function payrollNumber(text) {
  const value = Number(String(text || "").trim().replace(/\./g, "").replace(",", "."));
  return Number.isFinite(value) ? value : NaN;
}

async function openPayrollExtras(month = state.attendanceMonth || state.scheduleMonth) {
  let data = await request(`/api/payroll-extras?month=${encodeURIComponent(month)}`);
  const dialog = extrasDialog("payroll-extras-dialog", `Bordro ekleri · ${extrasMonthLabel(month)}`,
    "Avans, kesinti açıklaması, yemek / yol günü ve resmî tatil çalışmasının ücret ya da telafi izni olarak işlenmesi.");
  const body = dialog.querySelector(".extras-body");
  let people = structuredClone(data.people || {});
  let dirty = false;
  const record = (id) => {
    people[id] = people[id] || { deduction_note: "", advances: [], holiday_choice: "pay", meal: true, transport: true };
    return people[id];
  };
  const draw = () => {
    const rows = data.rows.map((row) => {
      const id = String(row.employee_id);
      const info = { deduction_note: "", advances: [], holiday_choice: "pay", meal: true, transport: true, ...(people[id] || {}) };
      const total = info.advances.reduce((sum, item) => sum + Number(item.amount || 0), 0);
      const advances = info.advances.map((item, index) => `<div class="payroll-advance">
          <input type="date" min="${month}-01" max="${month}-31" value="${escapeHtml(item.date || "")}" data-pe="${id}" data-adv="${index}" data-field="date" aria-label="Avans tarihi">
          <input inputmode="decimal" value="${escapeHtml(String(item.amount ?? "").replace(".", ","))}" data-pe="${id}" data-adv="${index}" data-field="amount" placeholder="Tutar" aria-label="Avans tutarı">
          <input maxlength="80" value="${escapeHtml(item.note || "")}" data-pe="${id}" data-adv="${index}" data-field="note" placeholder="Açıklama" aria-label="Avans açıklaması">
          <button class="icon-button" type="button" data-adv-remove="${id}:${index}" aria-label="Avansı sil">×</button></div>`).join("");
      return `<tr>
        <th scope="row">${escapeHtml(row.name)}</th>
        <td><label class="payroll-flag"><input type="checkbox" data-pe="${id}" data-field="meal"${info.meal ? " checked" : ""}> ${info.meal ? row.meal_days || 0 : 0} gün</label></td>
        <td><label class="payroll-flag"><input type="checkbox" data-pe="${id}" data-field="transport"${info.transport ? " checked" : ""}> ${info.transport ? row.transport_days || 0 : 0} gün</label></td>
        <td>${row.ubgt_hours ? `<select data-pe="${id}" data-field="holiday_choice" aria-label="Tatil çalışması">${Object.entries(PAYROLL_CHOICES)
          .map(([value, label]) => `<option value="${value}"${info.holiday_choice === value ? " selected" : ""}>${label}</option>`).join("")}</select>
          <small>${payrollHours(row.ubgt_hours)} saat</small>` : '<span class="extras-empty">—</span>'}</td>
        <td><div class="payroll-deduction"><span>${payrollMoney(row.deduction_amount)} ₺</span>
          <input maxlength="200" value="${escapeHtml(info.deduction_note)}" data-pe="${id}" data-field="deduction_note" placeholder="Kesinti açıklaması" aria-label="Kesinti açıklaması"></div></td>
        <td class="payroll-advances">${advances}<button class="button button-light" type="button" data-adv-add="${id}">+ Avans</button>
          ${total ? `<strong>${payrollMoney(total)} ₺</strong>` : ""}</td></tr>`;
    }).join("");
    const balance = (data.comp_balance || []).map((item) => `<tr><th scope="row">${escapeHtml(item.name)}</th>
      <td>${payrollHours(item.earned_hours)}</td><td>${payrollHours(item.used_hours)}</td>
      <td class="${item.balance_hours < 0 ? "payroll-negative" : ""}"><strong>${payrollHours(item.balance_hours)}</strong></td></tr>`).join("");
    body.innerHTML = `<div class="payroll-scroll"><table class="archive-table extras-table payroll-extras-table">
        <thead><tr><th>Personel</th><th>Yemek</th><th>Yol</th><th>Resmî tatil çalışması</th><th>Kesinti</th><th>Avanslar</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6">Bu ayın puantajında personel yok.</td></tr>'}</tbody></table></div>
      <div class="dialog-footer"><span class="payroll-dirty" ${dirty ? "" : "hidden"}>Kaydedilmemiş değişiklik var</span>
        <button class="button button-primary" type="button" id="payroll-extras-save">Bordro eklerini kaydet</button></div>
      <h3 class="extras-subtitle">Telafi izni bakiyesi (${month.slice(0, 4)} yılı, bu aya kadar)</h3>
      <table class="archive-table extras-table"><thead><tr><th>Personel</th><th>Kazanılan saat</th><th>Kullanılan saat</th><th>Kalan</th></tr></thead>
        <tbody>${balance || '<tr><td colspan="4">Telafi izni seçilmiş tatil çalışması yok.</td></tr>'}</tbody></table>
      <p class="dialog-help">Yemek ve yol günü puantajdaki çalışılan günlerden (X, G.Ç, Y) sayılır. Telafi izni kullanımını puantaja
        <b>${escapeHtml(data.comp_leave_code || "Tİ")}</b> koduyla girin; her gün kişinin günlük sınırı kadar bakiyeden düşer.
        Bu bilgiler Logo / Mikro / Netsis bordro dosyasına ek sütun olarak yazılır.</p>`;
  };
  const mark = () => {
    dirty = true;
    const flag = body.querySelector(".payroll-dirty");
    if (flag) flag.hidden = false;
  };
  body.addEventListener("input", (event) => {
    const target = event.target.closest("[data-pe]");
    if (!target || target.type === "checkbox" || target.tagName === "SELECT") return;
    const info = record(target.dataset.pe);
    if (target.dataset.adv !== undefined) info.advances[Number(target.dataset.adv)][target.dataset.field] = target.value;
    else info[target.dataset.field] = target.value;
    mark();
  });
  body.addEventListener("change", (event) => {
    const target = event.target.closest("[data-pe]");
    if (!target || (target.type !== "checkbox" && target.tagName !== "SELECT")) return;
    record(target.dataset.pe)[target.dataset.field] = target.type === "checkbox" ? target.checked : target.value;
    dirty = true;
    draw();
  });
  body.addEventListener("click", (event) => {
    const add = event.target.closest("[data-adv-add]");
    if (add) {
      record(add.dataset.advAdd).advances.push({ date: "", amount: "", note: "" });
      dirty = true;
      draw();
      body.querySelector(`[data-pe="${add.dataset.advAdd}"][data-field="amount"]:last-of-type`)?.focus();
      return;
    }
    const remove = event.target.closest("[data-adv-remove]");
    if (remove) {
      const [id, index] = remove.dataset.advRemove.split(":");
      record(id).advances.splice(Number(index), 1);
      dirty = true;
      draw();
      return;
    }
    if (event.target.id === "payroll-extras-save") {
      extrasRun(event.target, async () => {
        const payload = {};
        for (const [id, info] of Object.entries(people)) {
          const advances = [];
          for (const item of info.advances || []) {
            if (item.amount === "" && !item.note && !item.date) continue;
            const amount = typeof item.amount === "number" ? item.amount : payrollNumber(item.amount);
            if (!(amount > 0)) throw new Error("Avans tutarı 0'dan büyük bir sayı olmalıdır.");
            advances.push({ date: item.date || "", amount, note: item.note || "" });
          }
          payload[id] = { deduction_note: info.deduction_note || "", advances,
            holiday_choice: info.holiday_choice || "pay", meal: info.meal !== false, transport: info.transport !== false };
        }
        data = await request("/api/payroll-extras", { method: "PUT", body: JSON.stringify({ month, people: payload }) });
        people = structuredClone(data.people || {});
        dirty = false;
        draw();
        toast("Bordro ekleri kaydedildi.");
      });
    }
  });
  dialog.oncancel = (event) => {
    if (dirty && !confirm("Kaydedilmemiş bordro ekleri silinsin mi?")) event.preventDefault();
  };
  draw();
  showExtras(dialog);
}

async function openBalancingReport(month = state.attendanceMonth || state.scheduleMonth, weeks = 0) {
  const data = await request(`/api/payroll/balancing?month=${encodeURIComponent(month)}${weeks ? `&weeks=${weeks}` : ""}`);
  const dialog = extrasDialog("balancing-report-dialog", "Denkleştirme raporu",
    "Seçilen dönemde haftalık mola düşülmüş fiilî çalışma ve 45 saatlik ortalama kontrolü (İş Kanunu md. 63).");
  const body = dialog.querySelector(".extras-body");
  const short = (iso) => { const [, m, d] = iso.split("-"); return `${d}.${m}`; };
  const rows = data.rows.map((row) => `<tr class="${row.status === "over" ? "balancing-over" : ""}">
      <th scope="row">${escapeHtml(row.name)}</th>
      ${row.weeks.map((week) => `<td class="${week.hours > data.limit ? "balancing-week-over" : ""}">${payrollHours(week.hours)}</td>`).join("")}
      <td><strong>${payrollHours(row.total_hours)}</strong></td><td><strong>${payrollHours(row.average_hours)}</strong></td>
      <td>${row.excess_hours ? `<strong>${payrollHours(row.excess_hours)}</strong>` : "—"}</td></tr>`).join("");
  body.innerHTML = `<div class="balancing-controls"><label>Dönem uzunluğu <select id="balancing-weeks">${[2, 4, 8, 12, 16]
      .map((value) => `<option value="${value}"${value === data.weeks ? " selected" : ""}>${value} hafta</option>`).join("")}</select></label>
      <span>${short(data.start)}.${data.start.slice(0, 4)} – ${short(data.end)}.${data.end.slice(0, 4)}</span>
      <span class="balancing-summary">${data.rows.filter((row) => row.status === "over").length} kişi ortalamayı aşıyor</span></div>
    <div class="payroll-scroll"><table class="archive-table extras-table balancing-table">
      <thead><tr><th>Personel</th>${data.week_starts.map((week) => `<th title="${week} haftası">${short(week)}</th>`).join("")}
        <th>Toplam</th><th>Ortalama</th><th>Fazla çalışma</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="${data.week_starts.length + 4}">Bu dönemde çalışma kaydı yok.</td></tr>`}</tbody></table></div>
    <p class="dialog-help">Ortalama ${data.limit} saati aşan kişilerin fazla çalışması = toplam − ${data.limit} × hafta sayısı.
      Kırmızı hücre o haftanın 45 saati aştığını gösterir; denkleştirmede tek hafta aşımı tek başına fazla çalışma sayılmaz.</p>`;
  body.querySelector("#balancing-weeks").addEventListener("change", (event) => {
    openBalancingReport(month, Number(event.target.value)).catch((error) => toast(error.message, true));
  });
  showExtras(dialog);
}

if (typeof EXTRAS_MENU !== "undefined" && !EXTRAS_MENU.some((item) => item[0] === "Bordro ekleri (avans, kesinti notu, yemek/yol)")) {
  EXTRAS_MENU.push(["Bordro ekleri (avans, kesinti notu, yemek/yol)", () => openPayrollExtras().catch((error) => toast(error.message, true))],
    ["Denkleştirme raporu", () => openBalancingReport().catch((error) => toast(error.message, true))]);
}
