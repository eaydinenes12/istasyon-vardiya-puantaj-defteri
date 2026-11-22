"use strict";

/* Release operations tools. Load after extras.js and before easy-mode.js. */
(() => {
  const tools = [
    ["quality", "Plan kalite"],
    ["rules", "Kurallar"],
    ["emergency", "Acil devamsızlık"],
    ["publish", "Yayın farkı"],
    ["closing", "Ay kapanışı"],
    ["fatigue", "Yorgunluk örüntüsü"],
    ["trends", "Dönem eğilimleri"],
    ["branch", "Şube karşılaştırma"],
    ["cost", "Maliyet simülasyonu"],
    ["lifecycle", "Personel geçişi"],
    ["assignments", "Geçici görevlendirme"],
    ["leave", "İzin çakışmaları"],
    ["corrections", "Puantaj düzeltme kutusu"],
    ["activities", "Mola ve görev devri"],
    ["mutualSwaps", "Karşılıklı vardiya takası"],
  ];
  let dialog;
  let requestSequence = 0;
  let reportMeta = null;
  let activeTool = "quality";
  let month = "";
  let latestCost = null;
  const planScenarios = new Map();
  let activityDate = "";

  const esc = (value) => escapeHtml(value == null ? "" : String(value));
  const fmt = (value) => Number(value || 0).toLocaleString("tr-TR", { maximumFractionDigits: 2 });
  const table = (headers, rows, empty) => rows.length
    ? `<div class="controls-table-wrap"><table class="archive-table extras-table"><thead><tr>${headers.map((item) => `<th>${esc(item)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
    : `<p class="archive-empty">${esc(empty)}</p>`;
  const request = async (path, options) => {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || data.message || "İstek tamamlanamadı.");
    if (data.report_meta) reportMeta = data.report_meta;
    return data;
  };
  const field = (label, html, wide = false) => `<label${wide ? ' class="forms-wide"' : ""}>${label}${html}</label>`;
  const errorBlock = (error, retry) => `<div class="controls-warn-text" role="alert">${esc(error.message || error)}
    <button class="button button-light" type="button" data-ops-retry>Yeniden dene</button></div>`;

  function openMenuTool(prefix) {
    const item = EXTRAS_MENU.find(([label, action]) => label.startsWith(prefix) && typeof action === "function");
    if (item) item[1]();
    else toast(`“${prefix}” aracı bu görünümde bulunamadı.`, true);
  }

  function hasDirtyDraft() {
    return Boolean(dialog?.querySelector('form[data-dirty="true"]'));
  }

  function clearDirtyDraft() {
    dialog?.querySelectorAll('form[data-dirty="true"]').forEach((form) => { form.dataset.dirty = "false"; });
  }

  function shell() {
    if (!dialog) {
      dialog = extrasDialog("operations-center", "Operasyon merkezi",
        "Planı inceleyin, kararları açıkça onaylayın ve dönem kaynaklarını izleyin.");
      dialog.classList.add("operations-dialog");
      dialog.addEventListener("change", (event) => {
        const form = event.target.closest("form");
        if (form) form.dataset.dirty = "true";
        if (event.target.matches("[data-ops-month]")) {
          if (hasDirtyDraft() && !window.confirm("Kaydedilmemiş operasyon girdisi var. Ayı değiştirmekten vazgeçilsin mi?")) {
            event.target.value = month;
            return;
          }
          clearDirtyDraft();
          month = event.target.value;
          renderTool(activeTool);
        }
      });
      dialog.addEventListener("input", (event) => {
        const form = event.target.closest("form");
        if (form) form.dataset.dirty = "true";
      }, true);
      dialog.addEventListener("click", (event) => {
        const leavesTool = event.target.closest("[data-ops-tool], [data-extras-close]");
        if (!leavesTool || !hasDirtyDraft()) return;
        if (!window.confirm("Kaydedilmemiş operasyon girdisi var. Kapatmadan veya başka araca geçmeden vazgeçilsin mi?")) {
          event.preventDefault();
          event.stopPropagation();
          return;
        }
        clearDirtyDraft();
        if (event.target.closest("[data-extras-close]")) {
          event.preventDefault();
          event.stopPropagation();
          dialog.close();
        }
      }, true);
      dialog.addEventListener("cancel", (event) => {
        if (hasDirtyDraft() && !window.confirm("Kaydedilmemiş operasyon girdisi var. Kapatmadan vazgeçilsin mi?")) {
          event.preventDefault();
        } else {
          clearDirtyDraft();
        }
      });
      dialog.addEventListener("click", (event) => {
        const nav = event.target.closest("[data-ops-tool]");
        if (nav) {
          activeTool = nav.dataset.opsTool;
          renderTool(activeTool);
          return;
        }
        if (event.target.closest("[data-ops-retry]")) renderTool(activeTool);
      });
    }
    if (!month) month = state.attendanceMonth || state.scheduleMonth || new Date().toISOString().slice(0, 7);
    const body = dialog.querySelector(".extras-body");
    body.innerHTML = `<div class="forms-fields">
      ${field("Ay", `<input data-ops-month type="month" value="${esc(month)}">`)}
      <nav class="dialog-footer operations-nav" aria-label="Operasyon araçları">
        ${tools.map(([key, label]) => `<button class="button ${key === activeTool ? "button-primary" : "button-light"}" type="button" data-ops-tool="${key}" aria-pressed="${key === activeTool}">${label}</button>`).join("")}
      </nav>
      <div class="forms-wide" data-ops-content aria-live="polite"></div></div>`;
    return body.querySelector("[data-ops-content]");
  }

  async function loadData(content, sequence, path) {
    content.innerHTML = '<div class="forms-fields" aria-label="Yükleniyor"><div class="forms-wide"><p class="controls-hint">Veriler alınıyor…</p></div></div>';
    try {
      const result = await request(path);
      if (sequence !== requestSequence || !dialog.open) return null;
      return result;
    } catch (error) {
      if (sequence === requestSequence && dialog.open) content.innerHTML = errorBlock(error);
      return null;
    }
  }

  function findingsTable(items) {
    return table(["Önem", "Tarih", "Personel / bölüm", "Bulgu"],
      items.map((item) => [
        `<span class="controls-${item.severity === "error" ? "warn-text" : "hint"}">${esc({ error: "Hata", warning: "Uyarı", info: "Bilgi" }[item.severity] || item.severity)}</span>`,
        esc(Array.isArray(item.date) ? item.date.map(opsDate).join(" → ") : opsDate(item.date)),
        esc(item.name || item.section || "—"), esc(item.message),
      ]), "Bu kapsamda bulgu yok.");
  }

  function opsDate(value) {
    if (!value) return "—";
    const [day, time] = String(value).split("T");
    const [year, monthNum, dateNum] = day.split("-");
    return `${dateNum}.${monthNum}.${year}${time ? ` ${time.slice(0, 5)}` : ""}`;
  }

  async function renderQuality(content, sequence) {
    const data = await loadData(content, sequence, `/api/operations/quality?month=${encodeURIComponent(month)}`);
    if (!data || sequence !== requestSequence) return;
    content.innerHTML = `<p class="controls-status">${data.counts.error} hata · ${data.counts.warning} uyarı · ${data.counts.info} bilgi</p>
      <p class="controls-hint">${esc(data.next_action)} · Bulgular inceleme içindir; kayıtlı planı otomatik değiştirmez.</p>
      <h3>Yarım işler ve veri kalitesi</h3>
      ${table(["İş", "Adet", ""], (data.pending_work || []).map((item) => [
        esc(item.label), esc(item.count),
        `<button class="button button-light" type="button" data-pending-tool="${esc(item.tool)}">İlgili aracı aç</button>`,
      ]), "Bekleyen iş yok.")}
      <h3>A/B vardiya karşılaştırması</h3>
      <p class="controls-hint">A varsayılan olarak kayıtlı planı kullanır. Vardiyada taslağı düzenleyip A veya B olarak alın; bu işlem planı kaydetmez. Taslak kopyaları yalnız bu oturumda tutulur.</p>
      <div class="dialog-actions">
        <button class="button button-outline" type="button" data-plan-snapshot="a">A olarak al</button>
        <button class="button button-outline" type="button" data-plan-snapshot="b">B olarak al</button>
        <button class="button button-light" type="button" data-plan-reset>A için kayıtlı planı kullan</button>
        <button class="button button-primary" type="button" data-plan-compare>Karşılaştır</button>
      </div>
      <p data-plan-status aria-live="polite"></p>
      <div data-plan-results aria-live="polite"></div>
      <h3>Saatlik kadro kontrolü</h3>
      <form id="hourly-staffing-form" class="forms-fields">
        <label>Gün <input name="day" type="date" min="${esc(month)}-01" value="${esc(month)}-01" required></label>
        <button class="button button-outline" type="submit">Saatleri göster</button>
      </form>
      <div id="hourly-staffing-result" aria-live="polite"></div>
      <h3>Plan bulguları</h3>
      ${findingsTable(data.findings)}`;
    const period = month;
    const snapshots = planScenarios.get(period) || { a: null, b: null };
    const status = content.querySelector("[data-plan-status]");
    const outputPlans = content.querySelector("[data-plan-results]");
    const compare = content.querySelector("[data-plan-compare]");
    const refreshPlans = () => {
      status.textContent = `A: ${snapshots.a === null ? "Kayıtlı plan" : "Alınan taslak"} · B: ${snapshots.b === null ? "Henüz alınmadı" : "Alınan taslak"}`;
      compare.disabled = snapshots.b === null;
      outputPlans.replaceChildren();
    };
    content.querySelectorAll("[data-plan-snapshot]").forEach((button) => button.addEventListener("click", () => {
      if (state.view !== "schedule" || state.scheduleMonth !== period || state.schedule?.month !== period) {
        toast("Taslağı almak için önce Vardiya sayfasında karşılaştırılan ayı açın.", true);
        return;
      }
      snapshots[button.dataset.planSnapshot] = structuredClone(state.schedule.entries);
      planScenarios.set(period, snapshots);
      refreshPlans();
    }));
    content.querySelector("[data-plan-reset]").addEventListener("click", () => {
      snapshots.a = null;
      refreshPlans();
    });
    let comparing = false;
    compare.addEventListener("click", async () => {
      if (comparing || snapshots.b === null) return;
      const { a, b } = snapshots;
      comparing = true;
      compare.disabled = true;
      outputPlans.textContent = "Planlar karşılaştırılıyor…";
      try {
        const result = await request("/api/schedule/compare-plans", {
          method: "POST", body: JSON.stringify({ month: period, a, b }),
        });
        if (sequence !== requestSequence || !dialog.open || snapshots.a !== a || snapshots.b !== b) return;
        const weekend = new Map();
        for (const key of ["a", "b"]) {
          for (const person of result[key].weekend) {
            if (!weekend.has(person.employee_id)) weekend.set(person.employee_id, { name: person.name, a: 0, b: 0 });
            weekend.get(person.employee_id)[key] = person.shifts;
          }
        }
        outputPlans.innerHTML = table(["Ölçüt", "A", "B"], [
          ["Kadro açığı bulgusu", fmt(result.a.staffing_findings), fmt(result.b.staffing_findings)],
          ["Hesaplanan fazla mesai (saat)", fmt(result.a.overtime_hours), fmt(result.b.overtime_hours)],
          ["Yayımlanmış vardiyası değişecek kişi", fmt(result.a.published_affected_people), fmt(result.b.published_affected_people)],
          ["Yayımlanmış vardiyada hücre farkı", fmt(result.a.published_change_count), fmt(result.b.published_change_count)],
          ["En yakın değişiklik tarihi", result.a.earliest_change_date || "Yayın yok / fark yok",
            result.b.earliest_change_date || "Yayın yok / fark yok"],
          ["En yakın değişikliğe kalan / geçen gün", result.a.days_until_earliest_change === null ? "—" : fmt(result.a.days_until_earliest_change),
            result.b.days_until_earliest_change === null ? "—" : fmt(result.b.days_until_earliest_change)],
        ], "") + '<h4>Personel bazında hafta sonu başlayan vardiyalar</h4>'
          + table(["Personel", "A", "B"], [...weekend.values()].filter((row) => row.a || row.b)
            .map((row) => [esc(row.name), fmt(row.a), fmt(row.b)]), "Hafta sonu başlayan vardiya yok.")
          + '<h4>Yayımlanmış plan farkları</h4>'
          + table(["Senaryo", "Tarih", "Personel", "Önce", "Sonra"], ["a", "b"].flatMap((key) =>
            result[key].published_changes.map((change) => [
              key.toUpperCase(), esc(change.date), esc(change.name),
              esc(change.before || "Boş"), esc(change.after || "Boş"),
            ])), result.a.published_plan_available || result.b.published_plan_available
              ? "Yayımlanmış vardiya değişikliği yok."
              : "Bu ay yayımlanmış plan bulunmadığından ilanlı vardiya etkisi hesaplanamadı.");
      } catch (error) {
        if (sequence === requestSequence && snapshots.a === a && snapshots.b === b) outputPlans.textContent = error.message;
      } finally {
        comparing = false;
        compare.disabled = snapshots.b === null;
      }
    });
    refreshPlans();
    const form = content.querySelector("#hourly-staffing-form");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const output = content.querySelector("#hourly-staffing-result");
      const button = form.querySelector("button");
      button.disabled = true;
      output.textContent = "Saatlik kadro hesaplanıyor…";
      try {
       const query = new URLSearchParams({ month, date: form.elements.day.value });
       const report = await request(`/api/operations/hourly-staffing?${query}`);
       if (sequence !== requestSequence || !dialog.open) return;
       output.innerHTML = table(["Bölüm", "Saat", "Gerekli", "En düşük", "Eksik aralık"],
         report.rows.map((row) => [esc(row.section), `${esc(row.start)}–${esc(row.end)}`,
           esc(row.required), esc(row.lowest),
           row.gaps.map((gap) => `${esc(gap.start)}–${esc(gap.end)}: ${esc(gap.shortfall)} eksik`).join("<br>") || "Yok"]),
         "Saat aralıklı asgari kadro kuralı tanımlı değil.");
      } catch (error) {
       output.textContent = error.message;
      } finally {
       button.disabled = false;
      }
    });
    content.querySelectorAll("[data-pending-tool]").forEach((button) => {
      button.addEventListener("click", () => { dialog.close(); openMenuTool(button.dataset.pendingTool); });
    });
  }

  async function renderRules(content, sequence) {
    const data = await loadData(content, sequence, "/api/operations/rules");
    if (!data || sequence !== requestSequence) return;
    const [availability, roster] = await Promise.all([
      request("/api/operations/availability"),
      request("/api/people"),
    ]);
    if (sequence !== requestSequence || !dialog.open) return;
    const sections = (state.scheduleSections || []).map((item) => item.name || item).filter(Boolean);
    content.innerHTML = `<h3 class="extras-subtitle">Yapılandırılmış kural ekle</h3>
      <p class="controls-hint">Kural seçilen tarih, gün ve saat aralığında değerlendirilir. Hata seviyesi kuralları çizelge kaydı sırasında doğrulanır.</p>
      <form class="forms-fields" data-ops-rule-form>
        ${field("Kural türü", `<select name="type"><option value="staffing">Asgari kadro</option><option value="leave_limit">Eşzamanlı izin üst sınırı</option></select>`)}
        ${field("Özel gün adı (isteğe bağlı)", '<input name="special_day" maxlength="80" placeholder="Örn. Sayım">')}
        <p class="forms-wide controls-hint">Özel gün adı girilirse başlangıç tarihi esas alınır; kadro eksiği yalnızca Plan kalitesinde uyarı olarak gösterilir.</p>
        ${field("Bölüm", `<input name="section" list="ops-sections" required maxlength="60">${sections.length ? `<datalist id="ops-sections">${sections.map((s) => `<option value="${esc(s)}">`).join("")}</datalist>` : ""}`)}
        ${field("Başlangıç", `<input name="start_date" type="date" value="${month}-01" required>`)}
        ${field("Bitiş", `<input name="end_date" type="date" value="${month}-${String(new Date(Number(month.slice(0, 4)), Number(month.slice(5), 0)).getDate()).padStart(2, "0")}" required>`)}
        ${field("Başlangıç saati", '<input name="start" type="time" value="00:00" required>')}
        ${field("Bitiş saati", '<input name="end" type="time" value="23:59" required>')}
        ${field("En az / en çok", '<input name="number" type="number" min="0" max="99" value="1" required>')}
        ${field("Yetkinlik (isteğe bağlı)", '<input name="skill" maxlength="60">')}
        ${field("Önem", '<select name="severity"><option value="warning">Uyarı</option><option value="error">Hata</option><option value="info">Bilgi</option></select>')}
        <div class="forms-wide">${["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"].map((label, index) =>
          `<label class="forms-check"><input type="checkbox" name="rule_weekdays" value="${index}" checked>${label}</label>`).join("")}</div>
        ${field("İstisna gerekçesi (varsa)", '<input name="exception_reason" maxlength="300">', true)}
        <div class="forms-wide"><button class="button button-primary" type="submit">Kuralı kaydet</button></div>
      </form>
      <h3 class="extras-subtitle">Etkin kurallar</h3>
      ${table(["Tür", "Bölüm", "Tarih aralığı", "Saat", "Eşik", "Önem", "İstisna", ""],
        data.rules.map((rule) => [rule.special_day ? `Özel gün: ${esc(rule.special_day)}` : rule.type === "staffing" ? "Asgari kadro" : "Eşzamanlı izin", esc(rule.section),
          `${opsDate(rule.start_date)} – ${opsDate(rule.end_date)}`, `${esc(rule.start)}–${esc(rule.end)}`,
          rule.type === "staffing" ? `${rule.minimum}${rule.skill ? ` · ${esc(rule.skill)}` : ""}` : rule.maximum,
          esc(rule.severity), esc(rule.exception_reason || "—"),
          `<button class="button button-light" type="button" data-rule-remove="${esc(rule.id)}">Sil</button>`]), "Henüz kural yok.")}
      <h3 class="extras-subtitle">Personel uygunluğu</h3>
      <p class="controls-hint">Geçerlilik tarihleri zorunludur. Tercih kaydı bilgi verir; uygunluk engeli hata olarak görünür ve çizelge kaydı sırasında uygulanır.</p>
      <form class="forms-fields" data-ops-availability>
        ${field("Personel", `<select name="employee_id" required><option value="">Seçin</option>${roster.people.map((person) => `<option value="${person.id}">${esc(person.name)}</option>`).join("")}</select>`)}
        ${field("Geçerlilik başlangıcı", `<input name="start_date" type="date" value="${month}-01" required>`)}
        ${field("Geçerlilik bitişi", `<input name="end_date" type="date" value="${month}-${String(new Date(Number(month.slice(0, 4)), Number(month.slice(5), 0)).getDate()).padStart(2, "0")}" required>`)}
        ${field("Kayıt türü", '<select name="preference"><option value="true">Tercih (bilgi)</option><option value="false">Uygunluk engeli</option></select>')}
        <div class="forms-wide forms-check">${["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"].map((label, index) =>
          `<label><input type="checkbox" name="unavailable_weekdays" value="${index}">${label}</label>`).join("")}</div>
        <label class="forms-check"><input type="checkbox" name="no_night">Gece vardiyası uygun değil</label>
        <label class="forms-check"><input type="checkbox" name="no_weekend">Hafta sonu uygun değil</label>
        <div><button class="button button-primary" type="submit">Uygunluğu kaydet</button></div>
      </form>
      ${table(["Personel", "Geçerlilik", "Hafta günleri", "Kısıt", "Tür", ""],
        availability.items.map((item) => {
          const person = roster.people.find((candidate) => candidate.id === item.employee_id);
          return [esc(person?.name || item.employee_id), `${opsDate(item.start_date)} – ${opsDate(item.end_date)}`,
            esc(item.unavailable_weekdays.map((day) => ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"][day]).join(", ") || "—"),
            [item.no_night && "Gece", item.no_weekend && "Hafta sonu"].filter(Boolean).join(", ") || "—",
            item.preference ? "Tercih" : "Uygunluk engeli",
            `<button class="button button-light" type="button" data-availability-delete="${item.employee_id}">Sil</button>`];
        }), "Uygunluk kaydı yok.")}`;
    content.querySelector("[data-ops-rule-form]").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = Object.fromEntries(new FormData(event.currentTarget).entries());
      const number = Number(form.number);
      const weekdays = [...event.currentTarget.querySelectorAll('[name="rule_weekdays"]:checked')]
        .map((item) => Number(item.value));
      const rule = {
        id: `rule-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        type: form.type, section: form.section, start_date: form.start_date, end_date: form.end_date,
        start: form.start, end: form.end, weekdays, severity: form.severity,
        exception_reason: form.exception_reason,
        ...(form.type === "staffing" ? { minimum: number, skill: form.skill } : { maximum: number }),
      };
      if (form.special_day.trim()) {
        if (form.type !== "staffing") {
          toast("Özel gün için asgari kadro kuralı seçin.", true);
          return;
        }
        rule.special_day = form.special_day.trim();
        rule.end_date = rule.start_date;
        rule.weekdays = [0, 1, 2, 3, 4, 5, 6];
      }
      operationsRun(event.submitter, async () => {
        await request("/api/operations/rules", { method: "PUT", body: JSON.stringify({ rules: [...data.rules, rule] }) });
        renderTool("rules");
      });
    });
    content.querySelectorAll("[data-rule-remove]").forEach((button) => button.addEventListener("click", (event) => operationsRun(event.currentTarget, async () => {
      if (!window.confirm("Seçilen yapılandırılmış kural silinsin mi?")) return;
      await request("/api/operations/rules", {
        method: "PUT",
        body: JSON.stringify({ rules: data.rules.filter((rule) => rule.id !== button.dataset.ruleRemove) }),
      });
      renderTool("rules");
    })));
    content.querySelector("[data-ops-availability]").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const values = Object.fromEntries(new FormData(form).entries());
      const weekdays = [...form.querySelectorAll('[name="unavailable_weekdays"]:checked')].map((item) => Number(item.value));
      operationsRun(event.submitter, async () => {
        await request("/api/operations/availability", {
          method: "PUT",
          body: JSON.stringify({
            employee_id: Number(values.employee_id), start_date: values.start_date, end_date: values.end_date,
            unavailable_weekdays: weekdays, no_night: form.elements.no_night.checked,
            no_weekend: form.elements.no_weekend.checked, preference: values.preference === "true",
          }),
        });
        renderTool("rules");
      });
    });
    content.querySelectorAll("[data-availability-delete]").forEach((button) => button.addEventListener("click", (event) => operationsRun(event.currentTarget, async () => {
      if (!window.confirm("Seçilen personelin uygunluk kaydı silinsin mi?")) return;
      await request("/api/operations/availability", {
        method: "PUT", body: JSON.stringify({ employee_id: Number(button.dataset.availabilityDelete), delete: true }),
      });
      renderTool("rules");
    })));
  }

  async function renderEmergency(content, sequence) {
    const people = await loadData(content, sequence, "/api/people");
    if (!people || sequence !== requestSequence) return;
    const options = (people.people || []).filter((item) => item.active).map((person) =>
      `<option value="${person.id}">${esc(person.name)}</option>`).join("");
    content.innerHTML = `<p class="controls-hint">Vardiya hücresi silinmez: seçilen durum kaydedilir, özgün vardiya denetim kaydında korunur. Uygulamadan önce uygun yedeği seçmeniz gerekir.</p>
      <form class="forms-fields" data-ops-emergency>
        ${field("Personel", `<select name="employee_id" required><option value="">Seçin</option>${options}</select>`)}
        ${field("Tarih", `<input name="date" type="date" value="${month}-01" required>`)}
        ${field("Durum", '<select name="status"><option value="absent">Devamsız</option><option value="sick_leave">Raporlu</option><option value="annual_leave">Yıllık izin</option><option value="unpaid_leave">Ücretsiz izin</option></select>')}
        ${field("Gerekçe", '<input name="reason" minlength="3" maxlength="240" required>', true)}
        <div class="forms-wide"><button class="button button-primary" type="submit">Önizle ve yedek ara</button></div>
      </form><div data-ops-emergency-result></div>`;
    const form = content.querySelector("[data-ops-emergency]");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      operationsRun(event.submitter, async () => {
        const values = Object.fromEntries(new FormData(form).entries());
        const preview = await request("/api/operations/emergency/preview", {
          method: "POST", body: JSON.stringify({ ...values, employee_id: Number(values.employee_id) }),
        });
        if (sequence !== requestSequence || !dialog.open) return;
        const result = content.querySelector("[data-ops-emergency-result]");
        const eligible = preview.alternatives.filter((item) => item.eligible);
        result.innerHTML = `<p class="controls-hint">${esc(preview.warning)} ${preview.coverage_gaps.length
          ? `Boşluk: ${preview.coverage_gaps.map((gap) => `${esc(gap.start)}–${esc(gap.end)}`).join(", ")}`
          : "Bölümde tanımlı saat aralığında ek boşluk görünmüyor."}</p>
          ${table(["Yedek", "Uygunluk", "Gerekçe"], preview.alternatives.map((item) => [
            esc(item.name), item.eligible ? "Uygun" : "Uygun değil", esc(item.reasons.join(" ") || "Engel yok"),
          ]), "Uygun yedek bulunamadı.")}
          ${eligible.length ? `<div class="forms-fields">${field("Seçilen yedek", `<select data-emergency-replacement>${eligible.map((item) => `<option value="${item.employee_id}">${esc(item.name)}</option>`).join("")}</select>`)}
            <div class="forms-wide"><button class="button button-primary" type="button" data-emergency-apply>Değişikliği uygula</button></div></div>` : ""}`;
        result.querySelector("[data-emergency-apply]")?.addEventListener("click", (buttonEvent) => operationsRun(buttonEvent.currentTarget, async () => {
          if (!window.confirm("Seçilen durumu kaydedip vardiyayı seçilen yedeğe aktarmak istiyor musunuz?")) return;
          const replacement_id = Number(result.querySelector("[data-emergency-replacement]").value);
          await request("/api/operations/emergency/apply", {
            method: "POST", body: JSON.stringify({
              ...values, employee_id: Number(values.employee_id), replacement_id,
              revision: preview.revision, confirm: true,
            }),
          });
          toast("Acil vardiya değişikliği kaydedildi; özgün vardiya denetim kaydında korundu.");
          renderTool("quality");
        }));
      });
    });
  }

  async function renderPublish(content, sequence) {
    const data = await loadData(content, sequence, `/api/operations/publish-diff?month=${encodeURIComponent(month)}`);
    if (!data || sequence !== requestSequence) return;
    content.innerHTML = `<p class="controls-hint">${data.published_at ? `Son yayın: ${opsDate(data.published_at)}` : "Bu ay için önceki yayın yok."}
      · Yayın onayı kaynak revision ile yeniden doğrulanır. Farklar gün ve bölüm bazında gruplanır; yalnız kayıtlı acil değişiklik açıklamaları gösterilir, gerekçe yoksa neden tahmin edilmez.</p>
      ${table(["Tarih", "Bölüm", "Değişiklikler", "Kayıtlı gerekçe"],
        data.change_groups.map((group) => [
          opsDate(group.date), esc(group.section),
          esc(group.changes.map((item) => `${item.name}: ${item.before || "—"} → ${item.after || "—"}`).join("; ")),
          esc(group.recorded_reasons.map((item) => item.reason).join("; ") || "Kayıtlı gerekçe yok."),
        ]), "Yayın ile taslak aynı.")}
      <button class="button button-primary" type="button" data-publish>Bu revizyonu yayınla</button>`;
    content.querySelector("[data-publish]").addEventListener("click", (event) => operationsRun(event.currentTarget, async () => {
      if (!window.confirm("Gösterilen kaynak revizyonunu yayınlamak istiyor musunuz?")) return;
      await request("/api/operations/publish", {
        method: "POST", body: JSON.stringify({ month, revision: data.revision }),
      });
      toast("Vardiya revizyonu yayınlandı.");
      renderTool("publish");
    }));
  }

  async function renderClosing(content, sequence) {
    const period = month;
    const [data, delivery] = await Promise.all([
      loadData(content, sequence, `/api/operations/closing?month=${encodeURIComponent(period)}`),
      accessCanAdmin() ? request(`/api/operations/payroll-delivery?month=${encodeURIComponent(period)}`) : null,
    ]);
    if (!data || sequence !== requestSequence) return;
    const next = data.steps.find((item) => item.step === data.next);
    content.innerHTML = `<p class="controls-hint">${data.complete ? "Dönem kapanış adımları tamamlandı." : `Sıradaki: ${esc(next.label)}`}</p>
      ${table(["Kontrol", "Sorumlu", "Zaman", "Durum", "Bağlantı"],
        data.steps.map((item) => [esc(item.label), esc(item.name || "—"), opsDate(item.checked_at),
          item.done ? "Tamamlandı" : item.stale ? "Kaynak değişti" : "Bekliyor",
          `<a href="${esc(item.next_action)}" data-ops-action-link="${item.step}">Aç</a>`]), "")}
      ${next ? `<form class="forms-fields" data-ops-close>
        ${field("Kontrolü yapan", '<input name="name" minlength="2" maxlength="60" required>')}
        <div class="forms-wide"><button class="button button-primary" type="submit">“${esc(next.label)}” adımını tamamla</button></div></form>` : ""}
      ${delivery ? `<h3>Bordro teslimi ve mutabakat</h3>
        <p class="controls-hint">Standart bordro aktarımının saha dışında yapılan teslimini kaydedin. Bu işlem dosyayı göndermez. Her sürüm yalnız bir kez kaydedilebilir.</p>
        <p>${delivery.person_count} personel · ${delivery.duplicate ? "Bu sürüm zaten teslim edilmiş." : "Bu sürüm teslim edilmemiş."}</p>
        ${table(["Sürüm", "Teslim zamanı", "Teslim açıklaması"], delivery.versions.map((row) =>
         [esc(row.id), opsDate(row.delivered_at), esc(row.reference)]), "Teslim kaydı yok.")}
        ${delivery.versions.length ? `<h4>Son teslimden sonra değişen alanlar</h4>
         ${table(["Personel", "Alan", "Teslim edilen", "Güncel"], delivery.changes.flatMap((person) =>
           person.fields.map((field) => [esc(person.name), esc(field.name), esc(field.before ?? "—"), esc(field.after ?? "—")])),
         "Bordroya yansıyan fark yok.")}` : ""}
        ${delivery.changes.length && delivery.locked ? `<h4>Kilitli dönem düzeltme fişi</h4>
          <p class="controls-hint">İlk teslim (#${delivery.versions[0]?.id}) korunur. Fark ayrı fiş olarak kaydedilir; kaydetmek onay değildir.</p>
          <form data-payroll-adjustment class="forms-fields">
            ${field("Düzeltme gerekçesi", '<textarea name="reason" minlength="3" maxlength="500" required></textarea>', true)}
            <div class="forms-wide"><button class="button button-outline" type="submit">Düzeltme fişini oluştur</button></div>
          </form>` : delivery.changes.length ? `<p class="controls-hint">Düzeltme fişi için dönem kilidi gerekir. Dönem açıkken rapor farkı yalnız önizlenir.</p>` : ""}
        ${delivery.adjustments.length ? `<h4>Eski teslimden sonraki düzeltme fişleri</h4>
          ${table(["İlk teslim", "Durum", "Gerekçe", "İsteyen / zaman", "Onaylayan / zaman", "Kişi farkı", "İşlem"],
            delivery.adjustments.map((item) => [
              `#${item.delivery_id}`, item.status === "pending" ? "Onay bekliyor" : item.status === "approved" ? "Onaylandı" : "Reddedildi",
              esc(item.reason), `${esc(item.requested_by)} · ${opsDate(item.requested_at)}`,
              `${esc(item.decided_by || "—")} · ${opsDate(item.decided_at)}`, item.changes.length,
              item.status === "pending" ? `<button type="button" class="button button-outline" data-payroll-adjustment-review="${esc(item.id)}" data-approve="true">Onayla</button>
                <button type="button" class="button button-light" data-payroll-adjustment-review="${esc(item.id)}" data-approve="false">Reddet</button>` : "—",
            ]), "Henüz düzeltme fişi yok.")}` : ""}
        ${delivery.recorded && delivery.person_count && !delivery.duplicate ? `<form data-payroll-delivery class="forms-fields">
         ${field("Teslim açıklaması", '<input name="reference" maxlength="120" required>')}
         <label class="forms-check forms-wide"><input name="confirmed" type="checkbox" required> Bu sürümü bordro sorumlusuna teslim ettiğimi doğruluyorum.</label>
         <div class="forms-wide"><button class="button button-outline" type="submit">Teslimi kaydet</button></div>
        </form>` : ""}
        <h4>Harici muhasebe dosyasıyla kişi-gün mutabakatı</h4>
        <p class="controls-hint">Yalnız yerel önizleme yapılır; dosya saklanmaz veya gönderilmez. CSV başlıkları: employee_id (veya tekil payroll_code), date, code, hours. Ücret karşılaştırması yapılmaz.</p>
        <form data-payroll-reconcile class="forms-fields">
          ${field("CSV dosyası", '<input name="file" type="file" accept=".csv,text/csv" required>', true)}
          <div class="forms-wide"><button class="button button-outline" type="submit">Mutabakatı önizle</button></div>
        </form><div data-payroll-reconcile-result aria-live="polite"></div>` : ""}`;
    content.querySelector("[data-payroll-delivery]")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const { reference, confirmed } = event.currentTarget.elements;
      operationsRun(event.submitter, async () => {
        await request("/api/operations/payroll-delivery", { method: "POST", body: JSON.stringify({
         month: period, fingerprint: delivery.fingerprint, reference: reference.value, confirmed: confirmed.checked,
        }) });
        if (sequence === requestSequence && dialog.open) await renderTool("closing");
      });
    });
    content.querySelector("[data-payroll-adjustment]")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const reason = new FormData(event.currentTarget).get("reason");
      operationsRun(event.submitter, async () => {
        await request("/api/operations/payroll-adjustments/request", {
          method: "POST",
          body: JSON.stringify({
            month: period, fingerprint: delivery.fingerprint,
            delivery_id: delivery.versions[0].id, reason,
          }),
        });
        if (sequence === requestSequence && dialog.open) await renderTool("closing");
      });
    });
    content.querySelectorAll("[data-payroll-adjustment-review]").forEach((button) => {
      button.addEventListener("click", () => operationsRun(button, async () => {
        await request("/api/operations/payroll-adjustments/review", {
          method: "POST",
          body: JSON.stringify({
            month: period, id: button.dataset.payrollAdjustmentReview,
            approve: button.dataset.approve === "true",
          }),
        });
        if (sequence === requestSequence && dialog.open) await renderTool("closing");
      }));
    });
    content.querySelector("[data-payroll-reconcile]")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const file = event.currentTarget.elements.file.files[0];
      if (!file) return;
      operationsRun(event.submitter, async () => {
        const result = await request("/api/operations/payroll-reconcile/preview", {
          method: "POST",
          body: JSON.stringify({ month: period, filename: file.name, content: await file.text() }),
        });
        if (sequence !== requestSequence || !dialog.open) return;
        const labels = { code: "Puantaj kodu", hours: "Çalışma saati" };
        content.querySelector("[data-payroll-reconcile-result]").innerHTML = `<p class="controls-hint">
          ${esc(result.file)} · ${esc(result.generated_at)} · plan ${esc(result.schedule_revision || "kayıt yok")} · puantaj ${esc(result.attendance_revision || "kayıt yok")}
          <br>Uygulama ${result.expected_rows} kişi-gün / ${esc(result.expected_hours)} saat; dosya ${result.external_rows} satır / ${esc(result.external_hours)} saat.
          Toplam saat ${result.hours_total_equal ? "eşit" : "farklı"}, kod adetleri ${result.code_totals_equal ? "eşit" : "farklı"}; ${result.difference_count} kişi-gün farkı.</p>
          ${table(["Personel", "Gün", "Farklar"], result.differences.map((item) => [
            esc(item.name), opsDate(item.date),
            esc(item.fields.map((field) => `${labels[field.field]}: ${field.expected || "—"} → ${field.external || "—"}`).join("; ")),
          ]), "Kişi-gün kod ve saatleri eşleşiyor.")}
          <p class="controls-hint">${esc(result.scope)}</p>`;
      });
    });
    content.querySelectorAll("[data-ops-action-link]").forEach((link) => link.addEventListener("click", (event) => {
      event.preventDefault();
      const step = data.steps.find((item) => item.step === link.dataset.opsActionLink);
      if (step.step === "quality") renderTool("quality");
      else if (step.step === "attendance") renderTool("trends");
      else if (step.step === "reports") openMenuTool("Yönetici özeti");
      else if (step.step === "approval") openMenuTool("Puantaj onayı");
      else openMenuTool("Yedekler");
    }));
    content.querySelector("[data-ops-close]")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const name = new FormData(event.currentTarget).get("name");
      const fingerprint = next.fingerprint;
      operationsRun(event.submitter, async () => {
        await request("/api/operations/closing", {
          method: "POST", body: JSON.stringify({ month, step: next.step, name, fingerprint }),
        });
        renderTool("closing");
      });
    });
  }

  async function renderCorrections(content, sequence) {
    const period = month;
    const route = "/api/attendance/corrections";
    const canWrite = accessCan("attendance.write");
    const [data, attendance] = await Promise.all([
      request(`${route}?month=${period}`), request(`/api/attendance?month=${period}`),
    ]);
    if (sequence !== requestSequence || !dialog.open) return;
    const people = attendance.attendance?.rows ?? attendance.rows ?? [];
    const names = new Map(state.people.map((person) => [person.id, person.name]));
    const statuses = { pending: "Bekliyor", approved: "Onaylandı", rejected: "Reddedildi" };
    const evidence = (employee, day) => request(`${route}?${new URLSearchParams({ month: period, employee_id: employee, date: day })}`);
    const show = (item) => `<p class="controls-hint">Plan revision: ${esc(item.schedule_revision || "kayıt yok")} · Puantaj revision: ${esc(item.attendance_revision || "kayıt yok")}</p>
        ${table(["Vardiya planı", "PDKS", "Puantaj kodu"], [[
          item.plan.map((row) => `${esc(row.section)}: ${esc(row.value)}`).join("<br>") || "Kayıt yok",
          `${item.ambiguous ? "İsim eşleşmesi belirsiz" : item.punches.map((row) => `${esc(row.in || "—")} – ${esc(row.out || "—")}`).join("<br>") || "Bu gün için eşleşen kayıt yok"}<br>`
            + (item.pdks_source.file
              ? `Dosya ${esc(item.pdks_source.file)} · ${esc(item.pdks_source.imported_at || "zaman kaydı yok")} · ${esc(item.pdks_source.actor || "aktarımı yapan bilinmiyor")}`
              : "Bu dönem için kayıtlı PDKS aktarımı yok"),
          `${esc(item.code || "—")} · ${item.manual == null ? "Otomatik" : "Manuel"}`,
        ]], "")}
        <h5>Kayıtlı Excel aktarımları (dönem düzeyi; hücre eşleşmesi ayrıca kaydedilmemiş olabilir)</h5>
        ${table(["Dosya", "Zaman", "İşlemi yapan", "Kod sayısı"], item.imports.map((row) => [
          esc(row.file || "Dosya adı kayıtlı değil"), esc(row.occurred_at), esc(row.actor || "Bilinmiyor"),
          row.codes == null ? "Bilinmiyor" : row.codes,
        ]), "Bu dönem için kayıtlı Excel aktarımı yok.")}
        <h5>Bu hücre için düzeltme geçmişi</h5>
        ${table(["Durum", "Kod", "Gerekçe", "Talep eden", "Zaman", "Karar veren"],
          item.corrections.map((row) => [esc(statuses[row.status] || row.status), esc(row.code), esc(row.reason),
            esc(row.requested_by || "Bilinmiyor"), esc(row.created_at),
            `${esc(row.decided_by || "—")} · ${esc(row.decided_at || "—")}`]),
          "Kayıtlı düzeltme yok.")}
        <p class="controls-hint">Önceki puantaj revision kayıtları: ${item.attendance_revisions.length
          ? item.attendance_revisions.map((row) => `${esc(row.revision)} (${esc(row.recorded_at)})`).join(" · ")
          : "yok"}. Geçmiş Excel aktarımına ait hücre bağlantısı kaydedilmemişse bu ilişki varsayılmaz.</p>`;
    const lastDay = new Date(Number(period.slice(0, 4)), Number(period.slice(5, 7)), 0).getDate();
    content.innerHTML = `<p class="controls-hint">Planı, PDKS kaydını ve puantajı karşılaştırın. Onay yalnız seçilen günün manuel kodunu değiştirir.</p>
      ${people.length ? `<form class="forms-fields" data-correction>
          ${field("Personel", `<select name="employee" required>${people.map((person) => `<option value="${person.employee_id}">${esc(person.name)}</option>`).join("")}</select>`)}
          ${field("Tarih", `<input name="day" type="date" min="${period}-01" max="${period}-${String(lastDay).padStart(2, "0")}" value="${period}-01" required>`)}
          <button type="button" class="button button-outline" data-correction-check ${canWrite ? "" : "disabled"}>Kaynakları karşılaştır</button>
          <div class="forms-wide" data-correction-evidence aria-live="polite"></div>
          ${field("Önerilen kod", '<input name="code" maxlength="8" required>')}
          ${field("Gerekçe", '<input name="reason" minlength="3" maxlength="500" required>')}
          <button class="button button-primary" type="submit" disabled>Talebi oluştur</button>
        </form>${canWrite ? "" : `<p class="controls-hint" role="note">Kaynakları karşılaştırmak ve düzeltme talebi oluşturmak için Puantaj düzenleme yetkisi gerekir.</p>`}`
        : `<p class="archive-empty">Bu ay puantaj tablosunda düzeltme yapılabilecek personel yok.</p>`}
      ${table(["Personel", "Tarih", "Öneri ve gerekçe", "Durum", ""], data.requests.map((item, index) => [
        esc(names.get(item.employee_id) || item.employee_id), esc(item.day), `${esc(item.code)} · ${esc(item.reason)}`,
        esc(statuses[item.status]), item.status === "pending" && accessCanAdmin()
          ? `<button type="button" class="button button-outline" data-review-correction="${index}">İncele</button>` : "",
      ]), "Henüz düzeltme talebi yok.")}
      <div data-correction-review aria-live="polite"></div>`;
    const form = content.querySelector("[data-correction]");
    if (form) {
      const output = form.querySelector("[data-correction-evidence]");
      const submit = form.querySelector("[type=submit]");
      const check = form.querySelector("[data-correction-check]");
      let preview;
      let evidenceSequence = 0;
      const reset = () => {
        evidenceSequence += 1;
        preview = null;
        submit.disabled = true;
        output.replaceChildren();
        output.removeAttribute("role");
      };
      for (const control of [form.elements.employee, form.elements.day]) control.addEventListener("change", reset);
      check.addEventListener("click", async () => {
        reset();
        const { employee, day } = form.elements;
        if (!employee.reportValidity() || !day.reportValidity()) return;
        const who = employee.value, when = day.value, currentSequence = evidenceSequence;
        check.disabled = true;
        check.setAttribute("aria-busy", "true");
        output.textContent = "Kaynaklar karşılaştırılıyor…";
        try {
          const result = await evidence(who, when);
          if (currentSequence !== evidenceSequence || sequence !== requestSequence
              || employee.value !== who || day.value !== when) return;
          preview = result;
          output.innerHTML = show(result);
          submit.disabled = !canWrite;
        } catch (error) {
          if (currentSequence === evidenceSequence) {
            output.textContent = error.message;
            output.setAttribute("role", "alert");
          }
        } finally {
          check.disabled = !canWrite;
          check.removeAttribute("aria-busy");
        }
      });
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!preview) return;
        const { employee, day, code, reason } = form.elements;
        const currentPreview = preview;
        operationsRun(submit, async () => {
          try {
            await request(route, { method: "POST", body: JSON.stringify({
              month: period, employee_id: Number(employee.value), date: day.value, code: code.value,
              reason: reason.value, fingerprint: currentPreview.fingerprint,
            }) });
          } catch (error) {
            if (error.message.includes("Kaynak değişti")) {
              reset();
              output.innerHTML = `<p class="controls-warn-text" role="alert">Kaynak değişti. Güncel bilgileri yeniden karşılaştırın.</p>`;
            }
            throw error;
          }
          if (sequence === requestSequence) await renderTool("corrections");
        });
      });
    }
    content.querySelectorAll("[data-review-correction]").forEach((button) => button.addEventListener("click", async () => {
      const item = data.requests[Number(button.dataset.reviewCorrection)];
      const review = content.querySelector("[data-correction-review]");
      review.textContent = "Kaynaklar kontrol ediliyor…";
      let fresh;
      let detail;
      try {
        const current = await evidence(item.employee_id, item.day);
        fresh = current.fingerprint === item.evidence;
        detail = show(current);
      } catch (error) { detail = `<p>${esc(error.message)}</p>`; }
      if (sequence !== requestSequence || !dialog.open) return;
      review.innerHTML = `<h4>${esc(names.get(item.employee_id) || item.employee_id)} · ${esc(item.day)}</h4>${detail}
        ${fresh ? "" : "<p>Talebin kaynağı artık aynı değil; yalnız reddedebilirsiniz.</p>"}
        <button type="button" class="button button-primary" data-decision="approve" ${fresh ? "" : "disabled"}>Onayla</button>
        <button type="button" class="button button-outline" data-decision="reject">Reddet</button>`;
      review.querySelectorAll("[data-decision]").forEach((choice) => choice.addEventListener("click", () => {
        const approve = choice.dataset.decision === "approve";
        if (!window.confirm(`${item.day} · ${item.code}\n${approve ? "Bu günün kodu değiştirilsin mi?" : "Talep reddedilsin mi?"}`)) return;
        operationsRun(choice, async () => {
          await request(route, { method: "PUT", body: JSON.stringify({ month: period, id: item.id, approve }) });
          if (state.view === "attendance" && state.attendanceMonth === period) await loadAttendance();
          if (sequence === requestSequence) await renderTool("corrections");
        });
      }));
    }));
  }

  async function renderFatigue(content, sequence) {
    const data = await loadData(content, sequence, `/api/operations/fatigue?month=${encodeURIComponent(month)}`);
    if (!data || sequence !== requestSequence) return;
    content.innerHTML = `<p class="controls-hint">Bu göstergeler çizelge örüntüleridir; tıbbi değerlendirme değildir. Tarihler vardiyanın başladığı güne göre listelenir.</p>
      ${findingsTable(data.findings)}`;
  }

  async function renderTrends(content, sequence) {
    const data = await loadData(content, sequence, `/api/operations/trends?month=${encodeURIComponent(month)}&months=6`);
    if (!data || sequence !== requestSequence) return;
    content.innerHTML = `<p class="controls-hint">En fazla 12 ay; her ay mevcut puantaj hesaplamasından üretilir. Bir satırı seçerek kişi-gün dökümünü açın.</p>
      ${table(["Ay", "Çalışma (sa)", "Mesai (sa)", "UBGT (sa)", "Rapor", "Devamsız", "Kişi"],
        data.months.map((item) => [`<button class="button button-light" type="button" data-trend-month="${item.month}">${esc(item.month)}</button>`,
          fmt(item.metrics.worked_hours), fmt(item.metrics.overtime_hours), fmt(item.metrics.ubgt_hours),
          item.metrics.sick_days, item.metrics.absence_days, item.metrics.people]), "")}
      <h3 class="extras-subtitle">Tekrarlayan kadro açıkları</h3>
      <p class="controls-hint">${esc(data.staffing_patterns.source_basis)} · Aynı hafta günü, bölüm ve zaman aralığındaki açıklar gruplanır. İki veya daha fazla farklı haftada görülenler tekrarlayan örüntüdür; bu sonuç kadro ihtiyacı önerisi değil, mevcut hedeflere göre gözlenen eksikliktir.</p>
      ${data.staffing_patterns.has_thresholds
        ? table(["Örüntü", "Gün", "Bölüm", "Zaman", "Açık sayısı", "Hafta", "En düşük", "Tarihler", "Kaynak"],
          data.staffing_patterns.patterns.map((item) => [
            item.pattern === "repeated" ? "Tekrarlayan" : "Tek hafta",
            esc(item.weekday), esc(item.section), item.kind === "hourly" ? `${esc(item.start)}–${esc(item.end)}` : "Gün boyu",
            `${item.occurrence_count} (${item.required} gerekli)`, item.week_count, item.lowest_observed,
            esc(item.dates.join(", ")),
            esc(item.source_revisions.map((source) => `${source.month}: ${source.revision || "boş"}`).join(" · ")),
          ]), "Seçilen dönemde kayıtlı hedeflere göre açık yok.")
        : `<p class="controls-hint">Asgari kadro hedefi kayıtlı değil; tekrar analizi yapılamadı.</p>`}
      <div data-trend-detail></div>`;
    content.querySelectorAll("[data-trend-month]").forEach((button) => button.addEventListener("click", () => {
      const row = data.months.find((item) => item.month === button.dataset.trendMonth);
      content.querySelector("[data-trend-detail]").innerHTML = `<h3 class="extras-subtitle">${esc(row.month)} · kaynak ${esc(row.source.revisions.schedule || "—")}</h3>
        ${table(["Personel", "Çalışma (sa)", "Mesai (sa)", "UBGT (sa)", "Kod dağılımı"],
          row.drilldown.map((person) => [esc(person.name), fmt(person.worked_hours), fmt(person.overtime_hours), fmt(person.ubgt_hours),
            esc(Object.entries(person.codes || {}).filter(([, code]) => code).map(([day, code]) => `${opsDate(day)} ${code}`).join(", "))]),
          "Puantajda kişi yok.")}`;
    }));
  }

  async function renderBranch(content, sequence) {
    const branches = await loadData(content, sequence, "/api/workspace/branches");
    if (!branches || sequence !== requestSequence) return;
    content.innerHTML = `<p class="controls-hint">Yalnızca yapılandırılmış şube verisi salt okunur biçimde karşılaştırılır. Karşılaştırma sırasında başka veri dosyası açılmaz veya şeması değiştirilmez.</p>
      <div class="forms-fields">${field("Şube", `<select data-branch-id>${branches.branches.filter((item) => item.exists).map((item) => `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join("")}</select>`)}
      <div><button class="button button-primary" type="button" data-branch-compare>Karşılaştır</button></div></div><div data-branch-result></div>`;
    content.querySelector("[data-branch-compare]").addEventListener("click", (event) => operationsRun(event.currentTarget, async () => {
      const branch_id = content.querySelector("[data-branch-id]").value;
      const data = await request(`/api/operations/branch-compare?branch_id=${encodeURIComponent(branch_id)}&month=${encodeURIComponent(month)}`);
      if (sequence !== requestSequence || !dialog.open) return;
      content.querySelector("[data-branch-result]").innerHTML = `<h3 class="extras-subtitle">${esc(data.branch_name)} · ${esc(data.month)}</h3>
        ${table(["Vardiya", "Çalışma saati", "Personel", "Revision"], [[data.work_shifts, fmt(data.work_hours), data.people, esc(data.revision || "—")]], "")}
        <p class="controls-hint">Salt okunur sonuç · revizyon ${esc(data.revision || "boş dönem")}.</p>`;
    }));
  }

  async function renderCost(content, sequence) {
    const data = await loadData(content, sequence, `/api/operations/cost?month=${encodeURIComponent(month)}`);
    if (!data || sequence !== requestSequence) return;
    const period = month;
    const [people, budget] = await Promise.all([
      loadData(content, sequence, "/api/people"),
      loadData(content, sequence, `/api/operations/overtime-budget?month=${encodeURIComponent(period)}`),
    ]);
    if (!people || !budget || sequence !== requestSequence) return;
    const staff = (people.people || []).filter((item) => item.active);
    content.innerHTML = `<h3>Bölüm fazla mesai bütçesi</h3>
      <p class="controls-hint">Saatler önce personelin ana bölümüne, ana bölüm yoksa ayda çalıştığı tek bölüme yazılır. Diğerleri bölüm belirtilmeden gösterilir. Puantaj sütunu kayıtlı aya yansıyan değerdir; kart okutma teyidi değildir. Hedef, ücret veya mesai hesabını değiştirmez.</p>
      ${table(["Bölüm", "Hedef (sa.)", "Plan tahmini (sa.)", "Puantaja yansıyan (sa.)"], budget.rows.map((row) => [
        esc(row.section || "Bölümü belirsiz"), row.target === null ? "—" : fmt(row.target),
        fmt(row.forecast), row.actual === null ? "Kayıt yok" : fmt(row.actual),
      ]), "Tanımlı bölüm yok.")}
      <form class="forms-fields" data-overtime-budget>
        ${field("Bölüm", `<select name="section">${budget.rows.filter((row) => row.section).map((row) =>
          `<option value="${esc(row.section)}">${esc(row.section)}</option>`).join("")}</select>`)}
        ${field("Hedef saat (kaldırmak için boş bırakın)", '<input name="target" type="number" min="0" max="10000" step="0.01">')}
        <div class="forms-wide"><button class="button button-outline" type="submit">Hedefi kaydet</button></div>
      </form>
      <h3>Maliyet senaryosu</h3>
      <p class="controls-hint">Varsayılan ücret yok. Her oranı siz girmelisiniz. Sonuç tahmindir; bordro, vergi veya hukuk hesabı değildir.</p>
      <form class="forms-fields" data-ops-cost>
        ${field("Aylık karşılık saat", '<input name="hours_per_month" type="number" min="150" max="240" step="1" placeholder="Siz girin" required>')}
        ${field("Fazla mesai çarpanı", '<input name="overtime_multiplier" type="number" min="0.01" max="5" step="0.05" placeholder="Siz girin" required>')}
        ${field("Tatil çarpanı", '<input name="holiday_multiplier" type="number" min="0.01" max="5" step="0.05" placeholder="Siz girin" required>')}
        ${field("Senaryo adı (isteğe bağlı)", '<input name="scenario_name" maxlength="60">', true)}
        <div class="forms-wide">${table(["Personel", "Ücret türü", "Tutar (₺)"], staff.map((person) => [
          esc(person.name), `<select data-cost-basis="${person.id}"><option value="monthly">Aylık</option><option value="hourly">Saatlik</option></select>`,
          `<input data-cost-rate="${person.id}" type="number" min="0" step="0.01" inputmode="decimal" placeholder="Siz girin">`,
        ]), "Etkin personel yok.")}</div>
        <div class="forms-wide"><button class="button button-primary" type="submit">Tahmini karşılaştır</button></div>
      </form><div data-cost-result></div><h3 class="extras-subtitle">Kaydedilmiş senaryolar</h3>
      ${table(["Ad", "Ay", "Kaydedilme", ""],
        data.scenarios.map((item) => [esc(item.name), esc(item.month), opsDate(item.created_at),
          `<button class="button button-light" type="button" data-cost-compare="${esc(item.id)}" ${item.month !== month ? "disabled" : ""}>${item.month === month ? "Mevcutla karşılaştır" : "Farklı ay"}</button>`]), "Henüz senaryo yok.")}`;
    const budgetForm = content.querySelector("[data-overtime-budget]");
    const updateTarget = () => {
      budgetForm.elements.target.value = budget.rows.find((row) => row.section === budgetForm.elements.section.value)?.target ?? "";
    };
    budgetForm.elements.section.addEventListener("change", updateTarget);
    updateTarget();
    budgetForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const { section, target } = budgetForm.elements;
      operationsRun(event.submitter, async () => {
        await request("/api/operations/overtime-budget", { method: "PUT", body: JSON.stringify({
          month: period, section: section.value, target: target.value === "" ? null : Number(target.value),
        }) });
        if (sequence === requestSequence && dialog.open) await renderTool("cost");
      });
    });
    content.querySelector("[data-ops-cost]").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      operationsRun(event.submitter, async () => {
        const values = Object.fromEntries(new FormData(form).entries());
        const rates = {};
        for (const person of staff) {
          const amount = form.querySelector(`[data-cost-rate="${person.id}"]`).value;
          if (amount !== "") rates[person.id] = {
            basis: form.querySelector(`[data-cost-basis="${person.id}"]`).value, amount: Number(amount),
          };
        }
        const parameters = {
          rates, hours_per_month: Number(values.hours_per_month),
          overtime_multiplier: Number(values.overtime_multiplier),
          holiday_multiplier: Number(values.holiday_multiplier),
        };
        const response = await request("/api/operations/cost", {
          method: "POST", body: JSON.stringify(values.scenario_name.trim()
            ? { month, scenario: { ...parameters, name: values.scenario_name.trim() } }
            : { month, ...parameters }),
        });
        if (response.scenario) toast(`“${response.scenario}” senaryosu kaydedildi.`);
        const result = response.result || response;
        latestCost = { month, result };
        if (response.scenario) {
          await renderTool("cost");
          return;
        }
        if (sequence !== requestSequence || !dialog.open) return;
        content.querySelector("[data-cost-result]").innerHTML = `<p class="controls-hint">${esc(result.notice)}</p>
          <strong>Toplam: ${fmt(result.totals.total)} ₺</strong>
          ${result.missing_rates.length ? `<p class="controls-warn-text">Oran girilmedi: ${result.missing_rates.map(esc).join(", ")}</p>` : ""}
          ${table(["Personel", "Baz", "Mesai", "Tatil", "Tahmin"],
            result.people.map((item) => [esc(item.name), `${fmt(item.base)} ₺`, `${fmt(item.overtime)} ₺`,
              `${fmt(item.holiday)} ₺`, `<b>${fmt(item.total)} ₺</b>`]), "Girilen oranla hesaplanabilen kişi yok.")}`;
      });
    });
    content.querySelectorAll("[data-cost-compare]").forEach((button) => button.addEventListener("click", (event) => operationsRun(event.currentTarget, async () => {
      if (!latestCost || latestCost.month !== month) throw new Error("Önce bu ay için oranları girip mevcut senaryoyu hesaplayın.");
      const saved = await request(`/api/operations/cost?month=${encodeURIComponent(month)}&scenario_id=${encodeURIComponent(button.dataset.costCompare)}`);
      const previous = saved.result;
      const delta = Number(latestCost.result.totals.total) - Number(previous.totals.total);
      content.querySelector("[data-cost-result]").innerHTML = `<p class="controls-hint">${esc(previous.notice)}</p>
        <strong>Mevcut: ${fmt(latestCost.result.totals.total)} ₺ · ${esc(saved.scenario)}: ${fmt(previous.totals.total)} ₺ · fark: ${delta > 0 ? "+" : ""}${fmt(delta)} ₺</strong>
        <p class="controls-hint">İki tutar da kullanıcı girdisiyle üretilen tahmindir.</p>`;
    })));
  }

  async function renderLifecycle(content, sequence) {
    const data = await loadData(content, sequence, `/api/operations/lifecycle?month=${encodeURIComponent(month)}`);
    if (!data || sequence !== requestSequence) return;
    const sectionOptions = (data.sections || []).map((section) => `<option value="${esc(section)}">${esc(section)}</option>`).join("");
    const weekdays = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"];
    const patternOptions = '<option value="X">Çalışma (X)</option><option value="H">Sabit izin (H)</option><option value="">Tanımsız</option>';
    content.innerHTML = `<p class="controls-hint">Önizleme ve revision kontrolüyle işlem belirtilen tarihte yürürlüğe girer. Geçmiş vardiyalar yeniden yazılmaz; işten çıkış tarihinde paylaşım bağlantıları ve portal tokenları iptal edilir.</p>
      <form class="forms-fields" data-ops-lifecycle>
        ${field("Personel", `<select name="employee_id"><option value="">Seçin</option>${data.people.map((person) => `<option value="${person.id}">${esc(person.name)}</option>`).join("")}</select>`)}
        ${field("İşlem", '<select name="action"><option value="hire">İşe giriş</option><option value="exit">İşten ayrılış</option><option value="transfer">Bölüm değişikliği</option><option value="fixed_pattern">Sabit izin düzeni değişikliği</option></select>')}
        ${field("Geçerlilik tarihi", `<input name="effective_date" type="date" value="${new Date().toISOString().slice(0, 10)}" required>`)}
        ${field("Yeni bölüm", `<select name="section" data-lifecycle-section disabled>${sectionOptions}</select>`)}
        <div class="forms-wide" data-lifecycle-fixed-pattern hidden><strong>Yeni haftalık sabit düzen</strong>
          <div class="forms-fields">${weekdays.map((day, index) => field(day,
            `<select data-lifecycle-fixed-day="${index}">${patternOptions}</select>`)).join("")}</div>
        </div>
        <div><button class="button button-primary" type="submit">Önizle</button></div>
      </form>
      <h3 class="extras-subtitle">Yürürlük bekleyen işlemler</h3>
      ${table(["Personel", "İşlem", "Tarih", "Önceki bölüm", "Yeni bölüm", "Yeni sabit düzen"],
        data.pending_transitions.map((item) => [esc(item.name),
          item.action === "hire" ? "İşe giriş" : item.action === "exit" ? "İşten ayrılış"
            : item.action === "fixed_pattern" ? "Sabit izin düzeni değişikliği" : "Bölüm değişikliği",
          opsDate(item.effective_date), esc(item.from_section || "—"), esc(item.section || "—"),
          item.pattern?.length ? esc(item.pattern.map((code) => code || "—").join(" · ")) : "—"]),
        "Bekleyen personel işlemi yok.")}
      <div data-lifecycle-result></div>`;
    const form = content.querySelector("[data-ops-lifecycle]");
    const fixedOption = form.elements.action.querySelector('option[value="fixed_pattern"]');
    const fixedPatternField = form.querySelector("[data-lifecycle-fixed-pattern]");
    const updateLifecycleFields = () => {
      const isTransfer = form.elements.action.value === "transfer";
      const isPattern = form.elements.action.value === "fixed_pattern";
      form.elements.section.disabled = !isTransfer;
      form.elements.section.required = isTransfer;
      fixedPatternField.hidden = !isPattern;
      fixedOption.disabled = data.fixed_patterns?.[form.elements.employee_id.value]?.staff_type !== "nonshift";
      if (isPattern) {
        const base = data.fixed_patterns?.[form.elements.employee_id.value]?.pattern || ["X", "X", "X", "X", "X", "X", "H"];
        form.querySelectorAll("[data-lifecycle-fixed-day]").forEach((select) => {
          select.value = base[Number(select.dataset.lifecycleFixedDay)] || "";
        });
      }
    };
    form.elements.action.addEventListener("change", updateLifecycleFields);
    form.elements.employee_id.addEventListener("change", updateLifecycleFields);
    form.addEventListener("input", () => {
      content.querySelector("[data-lifecycle-result]").replaceChildren();
    });
    form.addEventListener("change", () => {
      content.querySelector("[data-lifecycle-result]").replaceChildren();
    });
    updateLifecycleFields();
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      operationsRun(event.submitter, async () => {
        const values = Object.fromEntries(new FormData(form).entries());
        const requestPayload = { ...values, employee_id: Number(values.employee_id) };
        if (values.action !== "transfer") delete requestPayload.section;
        if (values.action === "fixed_pattern") {
          requestPayload.pattern = [...form.querySelectorAll("[data-lifecycle-fixed-day]")]
            .map((select) => select.value);
        } else {
          delete requestPayload.pattern;
        }
        const preview = await request("/api/operations/lifecycle/preview", {
          method: "POST", body: JSON.stringify(requestPayload),
        });
        if (sequence !== requestSequence || !dialog.open) return;
        const target = content.querySelector("[data-lifecycle-result]");
        const affected = preview.action === "fixed_pattern"
          ? table(["Tarih", "Mevcut puantaj", "Önerilen puantaj"], preview.future_work.map((item) => [
            opsDate(item.date), esc(item.old_code || "—"), esc(item.new_code || "—"),
          ]), "Kaydedilmiş ileri dönem planlarında değişiklik yok.")
          : table(["Tarih", "Atanmış bölüm", "Etkin ana bölüm", "Önerilen ana bölüm", "Vardiya"],
            preview.future_work.map((item) => [
              opsDate(item.date), esc(item.section), esc(item.home_section || "—"),
              esc(item.proposed_home_section || "—"), `${esc(item.start)}–${esc(item.end)}`,
            ]), "Etkilenecek gelecek vardiya yok.");
        target.innerHTML = `<p class="controls-hint"><strong>${esc(preview.name)} · ${opsDate(preview.effective_date)}</strong><br>
          ${esc(preview.warning)} ${preview.action === "transfer"
          ? `${esc(preview.changes.from_section || "Bölüm belirtilmemiş")} → ${esc(preview.changes.section)}`
          : preview.action === "fixed_pattern"
            ? `Mevcut düzen: ${esc(preview.changes.old_pattern.map((code) => code || "—").join(" · "))} → yeni düzen: ${esc(preview.changes.pattern.map((code) => code || "—").join(" · "))}`
          : ""}</p>
          ${affected}
          ${preview.offboarding_checklist ? `<h3 class="extras-subtitle">Ayrılış kontrolü</h3>
            <p class="controls-hint">Açık kayıtlar önizlemede listelenir; teslim/iade, belge tamamlama ve erişim kapatma yürürlük tarihinde ayrıca kontrol edilmelidir.</p>
            ${table(["Açık zimmet", "Tür", "Teslim tarihi"], preview.offboarding_checklist.open_assets.map((item) => [
              esc(item.item), esc(item.category), opsDate(item.issued_at.slice(0, 10))]), "Açık zimmet yok.")}
            ${table(["Bekleyen belge", "Durum", "Oluşturulma"], preview.offboarding_checklist.pending_documents.map((item) => [
              esc(item.filename), item.status === "draft" ? "Taslak" : "Yazdırıldı", opsDate(item.created_at.slice(0, 10))]), "Bekleyen belge yok.")}
            ${table(["Erişim türü", "Ayrıntı"], [
              ...preview.offboarding_checklist.active_portal_tokens.map((item) => ["Personel portalı", `Bitiş: ${opsDate(new Date(item.expires_at * 1000).toISOString().slice(0, 10))}`]),
              ...preview.offboarding_checklist.active_workflow_shares.map((item) => ["Paylaşım bağlantısı", `${esc(item.month)} · Bitiş: ${opsDate(item.expires_at.slice(0, 10))}`]),
            ].map((row) => row.map(esc)), "Etkin erişim kaydı yok.")}` : ""}
          <button class="button button-primary" type="button" data-lifecycle-apply>Önizlenen işlemi uygula</button>`;
        target.querySelector("[data-lifecycle-apply]").addEventListener("click", (applyEvent) => operationsRun(applyEvent.currentTarget, async () => {
          if (!window.confirm("Önizlenen personel kartı değişikliğini uygulamak istiyor musunuz?")) return;
          await request("/api/operations/lifecycle/apply", {
            method: "POST", body: JSON.stringify({ ...requestPayload,
              revision: preview.revision, confirm: true }),
          });
          toast("Personel yaşam döngüsü güncellendi.");
          renderTool("lifecycle");
        }));
      });
    });
  }

  async function renderTemporaryAssignments(content, sequence) {
    const data = await loadData(content, sequence,
      `/api/operations/temporary-assignments?month=${encodeURIComponent(month)}`);
    if (!data || sequence !== requestSequence) return;
    const people = data.people.filter((person) => person.active)
      .map((person) => `<option value="${person.id}">${esc(person.name)}</option>`).join("");
    const sections = data.sections.map((section) => `<option value="${esc(section)}">`).join("");
    content.innerHTML = `<p class="controls-hint">Geçici bölüm veya şube görevlendirmesini tarih ve saatle kaydedin. Kaydetmeden önce mevcut ve komşu gün vardiyalarıyla aynı saat çakışması denetlenir.</p>
      <form class="forms-fields" data-temporary-assignment-form>
        ${field("Personel", `<select name="employee_id" required>${people}</select>`)}
        ${field("Hedef türü", `<select name="location_type"><option value="section">Bölüm</option><option value="branch">Şube</option></select>`)}
        ${field("Hedef bölüm veya şube", `<input name="location" list="temporary-assignment-sections" maxlength="80" required><datalist id="temporary-assignment-sections">${sections}</datalist>`)}
        ${field("Başlangıç", `<input name="start_date" type="date" value="${esc(`${month}-01`)}" required>`)}
        ${field("Bitiş", `<input name="end_date" type="date" value="${esc(`${month}-01`)}" required>`)}
        ${field("Günlük başlangıç", `<input name="start_time" type="time" value="09:00" required>`)}
        ${field("Günlük bitiş", `<input name="end_time" type="time" value="17:00" required>`)}
        ${field("Not (isteğe bağlı)", `<input name="note" maxlength="300">`, true)}
        <button class="button button-outline" type="submit" data-assignment-preview>Çakışmaları önizle</button>
      </form>
      <div data-assignment-preview-result aria-live="polite"></div>
      <h3>Kaydedilmiş geçici görevlendirmeler</h3>
      ${table(["Personel", "Hedef", "Tarih aralığı", "Saat", "Not", "Kaydeden"], data.items.map((item) => [
        esc(item.employee_name), `${item.location_type === "branch" ? "Şube" : "Bölüm"} · ${esc(item.location)}`,
        `${esc(opsDate(item.start_date))}–${esc(opsDate(item.end_date))}`,
        `${esc(item.start_time)}–${esc(item.end_time)}`, esc(item.note || "—"), esc(item.actor || "—"),
      ]), "Bu ay için geçici görevlendirme yok.")}`;
    const form = content.querySelector("[data-temporary-assignment-form]");
    const output = content.querySelector("[data-assignment-preview-result]");
    let preview = null;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const button = form.querySelector("[data-assignment-preview]");
      operationsRun(button, async () => {
        const assignment = Object.fromEntries(new FormData(form));
        assignment.employee_id = Number(assignment.employee_id);
        preview = await request("/api/operations/temporary-assignments/preview", {
          method: "POST", body: JSON.stringify({ action: "preview", assignment }),
        });
        const conflicts = preview.conflicts || [];
        output.innerHTML = `<h3>Görevlendirme etkisi · ${preview.days} gün</h3>
          <p>${conflicts.length ? "Aynı saatte başka konumda vardiya bulundu; kaydedilemez." : "Çakışma bulunmadı. Kaydetmek için onaylayın."}</p>
          ${table(["Tarih", "Mevcut bölüm/şube", "Yeni konum", "Çakışan saat"], conflicts.map((item) => [
            esc(opsDate(item.date)), esc(item.scheduled_section || "Diğer görevlendirme"),
            esc(preview.location), `${esc(item.start)}–${esc(item.end)}`,
          ]), "Mevcut vardiya veya görevlendirmeyle saat çakışması yok.")}
          ${conflicts.length ? "" : '<button class="button button-primary" type="button" data-assignment-save>Önizlenen görevlendirmeyi kaydet</button>'}`;
        form.dataset.dirty = "true";
        const save = output.querySelector("[data-assignment-save]");
        if (save) save.addEventListener("click", () => operationsRun(save, async () => {
          await request("/api/operations/temporary-assignments/preview", {
            method: "POST", body: JSON.stringify({
              action: "save", assignment, preview: preview.preview,
            }),
          });
          form.dataset.dirty = "false";
          toast("Geçici görevlendirme kaydedildi.");
          await renderTool("assignments");
        }));
      });
    });
  }

  async function renderLeave(content, sequence) {
    const data = await loadData(content, sequence, `/api/operations/leave-conflicts?month=${encodeURIComponent(month)}`);
    if (!data || sequence !== requestSequence) return;
    content.innerHTML = `<p class="controls-hint">Bağlı izin ve mevcut talep doğrulamaları, planlama modülünün aynı kural sonuçlarını kullanır.</p>
      ${findingsTable(data.findings)}`;
  }

  async function renderActivities(content, sequence) {
    const period = month;
    if (!activityDate.startsWith(`${period}-`)) activityDate = `${period}-01`;
    const date = activityDate;
    const path = "/api/operations/shift-activities";
    const data = await loadData(content, sequence, `${path}?${new URLSearchParams({ month: period, date })}`);
    if (!data) return;
    const admin = data.can_edit && !data.locked;
    const names = new Map(data.people.map(({ id, name }) => [id, name]));
    const peopleOptions = `<option value="">Personel seçin</option>${data.people.map(({ id, name }) =>
      `<option value="${id}">${esc(name)}</option>`).join("")}`;
    content.innerHTML = `<p class="controls-hint">Yalnız kayıtlı vardiya saatleri kullanılır. Bu plan, puantajı ve ücret hesabını değiştirmez.</p>
      ${field("Plan günü", `<input data-activity-day type="date" value="${esc(date)}" min="${esc(period)}-01" max="${esc(period)}-31">`)}
      ${admin ? `<form data-activity-form class="forms-fields">
      ${field("Bölüm", `<select name="section" required>${data.sections.map((item) => `<option>${esc(item)}</option>`).join("")}</select>`)}
      ${field("Tür", '<select name="kind"><option value="break">Mola</option><option value="handover">Görev devri</option></select>')}
      ${field("Personel / devreden", `<select name="employee_id" required>${peopleOptions}</select>`)}
      ${field("Devralan", `<select name="recipient_id" disabled>${peopleOptions}</select>`)}
      ${field("Başlangıç", '<input name="start" type="time" required>')}
      ${field("Bitiş (SS:DD)", '<input name="end" placeholder="17:00" pattern="[0-9]{2}:[0-9]{2}" maxlength="5" required>')}
      <div><button class="button button-primary" type="submit">Plana ekle</button></div></form>` : data.locked
      ? '<p class="controls-status">Dönem kilitli. Plan salt okunur.</p>' : ''}
      <h3>Günün mola ve devir planı</h3>
      ${table(["Saat", "Bölüm", "Tür", "Personel", ...(admin ? ["İşlem"] : [])],
        data.items.map((item) => [
          `${esc(item.start)}–${esc(item.end)}`, esc(item.section), item.kind === "break" ? "Mola" : "Görev devri",
          esc([item.employee_id, item.recipient_id].filter(Boolean).map((id) => names.get(id) || `#${id}`).join(" → ")),
          ...(admin ? [`<button type="button" class="button button-light" data-activity-remove="${esc(item.id)}">Sil</button>`] : []),
        ]), "Bu gün için mola veya görev devri planlanmadı.")}
      <h3>Çakışma ve vardiya kontrolü</h3>
      ${table(["Saat", "Bulgu"], data.findings.map((item) => [
        esc(item.ids.map((id) => data.items.find((row) => row.id === id)).map((row) => `${row.start}–${row.end}`).join(" / ")),
        esc(item.message),
      ]), "Çakışma veya vardiya dışı kayıt yok.")}`;
    const dayInput = content.querySelector("[data-activity-day]");
    dayInput.addEventListener("change", () => {
      if (!dayInput.value.startsWith(`${period}-`)) {
        dayInput.value = date;
        toast("Seçilen ay içinde bir gün seçin.", true);
        return;
      }
      if (hasDirtyDraft() && !window.confirm("Kaydedilmemiş mola veya devir girdisinden vazgeçilsin mi?")) {
        dayInput.value = date;
        return;
      }
      activityDate = dayInput.value;
      renderTool("activities");
    });
    const save = async (payload) => {
      await request(path, { method: "POST", body: JSON.stringify({ month: period, date, ...payload }) });
      if (sequence !== requestSequence || !dialog.open) return;
      toast("Mola ve görev devri planı güncellendi.");
      renderTool("activities");
    };
    const form = content.querySelector("[data-activity-form]");
    form?.elements.kind.addEventListener("change", () => {
      const handover = form.elements.kind.value === "handover";
      form.elements.recipient_id.disabled = !handover;
      form.elements.recipient_id.required = handover;
    });
    form?.addEventListener("submit", (event) => {
      event.preventDefault();
      const fields = Object.fromEntries(new FormData(form));
      operationsRun(event.submitter, () => save({ ...fields, employee_id: Number(fields.employee_id),
        recipient_id: fields.kind === "handover" ? Number(fields.recipient_id) : null }));
    });
    content.querySelectorAll("[data-activity-remove]").forEach((button) => button.addEventListener("click", () => {
      if (window.confirm("Bu mola veya devir kaydı silinsin mi?")) {
        operationsRun(button, () => save({ remove_id: button.dataset.activityRemove }));
      }
    }));
  }

  async function renderMutualSwaps(content, sequence) {
    const period = month;
    const path = "/api/operations/mutual-swaps";
    const data = await loadData(content, sequence, `${path}?month=${encodeURIComponent(period)}`);
    if (!data) return;
    const names = new Map(data.people.map(({ id, name }) => [id, name]));
    const options = `<option value="">Personel seçin</option>${data.people.filter((person) => person.active)
      .map(({ id, name }) => `<option value="${id}">${esc(name)}</option>`).join("")}`;
    const statuses = { pending: "Bekliyor", approved: "Kayda uygulandı", declined: "Personel reddetti", cancelled: "İptal edildi" };
    const lastDay = new Date(Number(period.slice(0, 4)), Number(period.slice(5, 7)), 0).getDate();
    content.innerHTML = `<p class="controls-hint">İki personel kendi portal bağlantısından kabul etmelidir. Yönetici onayı kayıtlı planı değiştirir; personele yeni saatleri göstermek için planı tekrar yayımlayın.</p>
      <form class="forms-fields" data-mutual-create>
        ${field("Gün", `<input name="date" type="date" min="${esc(period)}-01" max="${esc(period)}-${String(lastDay).padStart(2, "0")}" value="${esc(period)}-01" required>`)}
        ${field("Birinci personel", `<select name="first_id" required>${options}</select>`)}
        ${field("İkinci personel", `<select name="second_id" required>${options}</select>`)}
        <div>
          <button class="button button-light" type="button" data-swap-preview>Önizlemeyi hesapla</button>
          <button class="button button-primary" type="submit" data-swap-create disabled>İki personel onayına gönder</button>
        </div>
      </form>
      <div data-swap-preview-output aria-live="polite"><p class="controls-hint">Önce vardiya ve puantaj etkilerini önizleyin.</p></div>
      <h3>Bu ayın takasları</h3>
      ${table(["Gün", "Personel ve mevcut saat", "Durum", "İşlem"], data.items.map((item) => [
        esc(opsDate(item.date)),
        item.employees.map((id, index) => {
          const shift = item.pair[index].shift;
          return `${esc(names.get(id) || `#${id}`)}: ${esc(shift.start)}–${esc(shift.end)}`;
        }).join("<br>"),
        esc(item.status === "pending" ? `${item.accepted.length}/2 personel kabul etti` : statuses[item.status]),
        item.status === "pending" ? `<button type="button" class="button button-primary" data-swap-action="approve" data-swap-id="${esc(item.id)}" ${item.accepted.length !== 2 ? "disabled" : ""}>Takası uygula</button>
        <button type="button" class="button button-light" data-swap-action="cancel" data-swap-id="${esc(item.id)}">İptal et</button>` : "—",
      ]), "Bu ay için karşılıklı vardiya takası yok.")}`;
    const form = content.querySelector("[data-mutual-create]");
    const previewButton = form.querySelector("[data-swap-preview]");
    const createButton = form.querySelector("[data-swap-create]");
    const previewOutput = content.querySelector("[data-swap-preview-output]");
    let preview = null;
    const selection = () => Object.fromEntries(new FormData(form));
    const clearPreview = () => {
      preview = null;
      createButton.disabled = true;
      previewOutput.innerHTML = `<p class="controls-hint">Seçim değişti. Devam etmeden önce yeni bir önizleme hesaplayın.</p>`;
    };
    const renderPreview = (result) => {
      const findingRows = result.findings.map((item) => [
        esc(item.severity === "error" ? "Engel" : item.severity === "warning" ? "Uyarı" : "Bilgi"),
        esc(item.date || result.date),
        esc(item.employee_name || names.get(item.employee_id) || item.section || "—"),
        esc(item.message || item.kind || "—"),
      ]);
      previewOutput.innerHTML = `<section class="mutual-swap-preview">
        <h3>Vardiya takası önizlemesi</h3>
        <p class="controls-hint">${esc(result.notice)}</p>
        ${result.blockers.length
          ? `<p class="controls-warn-text" role="alert">${result.blockers.map((item) => esc(item.message)).join("<br>")}</p>`
          : `<p role="status">Bu önizlemede engel bulunmadı. Takas henüz kaydedilmedi.</p>`}
        ${table(["Personel", "Mevcut saat", "Takas sonrası"], result.swaps.map((item) => [
          esc(item.name), `${esc(item.from.start)}–${esc(item.from.end)}`, `${esc(item.to.start)}–${esc(item.to.end)}`,
        ]), "Vardiya değişikliği bulunamadı.")}
        ${table(["Personel", "Gün kodu önce → sonra", "Aylık çalışma saati önce → sonra", "Aylık fazla mesai önce → sonra"],
          result.attendance_impacts.map((item) => [
            esc(item.name), `${esc(item.code_before || "—")} → ${esc(item.code_after || "—")}`,
            `${fmt(item.worked_hours_before)} → ${fmt(item.worked_hours_after)}`,
            `${fmt(item.overtime_hours_before)} → ${fmt(item.overtime_hours_after)}`,
          ]), "Puantaj etkisi hesaplanamadı.")}
        ${table(["Seviye", "Gün", "Personel / bölüm", "Yeni bulgu"], findingRows, "Yeni kalite veya dinlenme uyarısı yok.")}
      </section>`;
      createButton.disabled = !result.can_create;
    };
    const send = async (payload) => {
      await request(path, { method: "POST", body: JSON.stringify({ month: period, ...payload }) });
      if (payload.action === "approve" && state.scheduleMonth === period && typeof loadSchedule === "function") await loadSchedule();
      if (sequence !== requestSequence || !dialog.open) return;
      toast(payload.action === "approve" ? "Takas kayda uygulandı. Yeni saatler için planı yayımlayın."
        : payload.action === "create" ? "Takas iki personelin onayına gönderildi." : "Takas kaydı güncellendi.");
      renderTool("mutualSwaps");
    };
    form.addEventListener("input", clearPreview);
    form.addEventListener("change", clearPreview);
    previewButton.addEventListener("click", () => {
      if (!form.reportValidity()) return;
      const { date, first_id, second_id } = selection();
      if (first_id === second_id) {
        toast("İki farklı personel seçin.", true);
        return;
      }
      operationsRun(previewButton, async () => {
        const result = await request(path, {
          method: "POST",
          body: JSON.stringify({ month: period, action: "preview", date, first_id: Number(first_id), second_id: Number(second_id) }),
        });
        if (sequence !== requestSequence || !dialog.open) return;
        preview = result;
        renderPreview(result);
      });
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const { date, first_id, second_id } = selection();
      const submitButton = event.submitter || createButton;
      if (!preview || preview.date !== date || preview.swaps[0]?.employee_id !== Number(first_id)
          || preview.swaps[1]?.employee_id !== Number(second_id)) {
        clearPreview();
        toast("Seçim için güncel bir önizleme hesaplayın.", true);
        return;
      }
      if (!window.confirm("Önizlemedeki etkileri kontrol ettiniz mi? Takas iki personelin kabulüne gönderilsin mi?")) return;
      operationsRun(submitButton, async () => {
        try {
          await send({
            action: "create", date, first_id: Number(first_id), second_id: Number(second_id),
            fingerprint: preview.fingerprint,
          });
        } catch (error) {
          if (error.message.includes("yeniden önizleyin")) {
            clearPreview();
            previewOutput.innerHTML = `<p class="controls-warn-text" role="alert">Önizleme artık güncel değil. Yeni etkileri yeniden hesaplayın.</p>`;
          }
          throw error;
        }
      });
    });
    content.querySelectorAll("[data-swap-action]").forEach((button) => button.addEventListener("click", () => {
      const action = button.dataset.swapAction;
      if (window.confirm(action === "approve" ? "İki kayıtlı vardiya karşılıklı değiştirilsin mi?" : "Bu takas iptal edilsin mi?")) {
        operationsRun(button, () => send({ action, id: button.dataset.swapId }));
      }
    }));
  }

  async function renderTool(tool) {
    activeTool = tool;
    const focused = dialog?.contains(document.activeElement) ? document.activeElement : null;
    const focusMonth = focused?.matches("[data-ops-month]");
    const focusTool = focused?.closest("[data-ops-tool]")?.dataset.opsTool;
    reportMeta = null;
    const content = shell();
    if (focused && !focused.isConnected) {
      const target = focusMonth ? dialog.querySelector("[data-ops-month]")
        : dialog.querySelector(`[data-ops-tool="${focusTool || activeTool}"]`);
      target?.focus({ preventScroll: true });
    }
    const sequence = ++requestSequence;
    const renderers = {
      quality: renderQuality, rules: renderRules, emergency: renderEmergency, publish: renderPublish,
      closing: renderClosing, fatigue: renderFatigue, trends: renderTrends, branch: renderBranch,
      cost: renderCost, lifecycle: renderLifecycle, assignments: renderTemporaryAssignments,
      leave: renderLeave, corrections: renderCorrections,
      activities: renderActivities, mutualSwaps: renderMutualSwaps,
    };
    try {
      await renderers[tool](content, sequence);
      if (sequence === requestSequence && dialog.open && reportMeta && !content.querySelector('[role="alert"]')) {
        const meta = reportMeta;
        const missing = meta.missing_sources.length
          ? `Eksik kaynaklar: ${meta.missing_sources.join("; ")}`
          : "Eksik kayıtlı kaynak yok.";
        const label = document.createElement("p");
        label.className = "controls-hint";
        label.dataset.reportMetaLabel = "";
        label.textContent = `Son hesap: ${opsDate(meta.calculated_at)} · dönem ${meta.month} · plan sürümü ${
          meta.plan_revision || "Bilinmiyor"} · puantaj sürümü ${meta.attendance_revision || "Bilinmiyor"} · ${
          meta.pdks_file ? `PDKS ${meta.pdks_file}` : missing}`;
        if (meta.pdks_file && meta.missing_sources.length) label.textContent += ` · ${missing}`;
        content.prepend(label);
      }
    } catch (error) {
      if (sequence === requestSequence && dialog.open) content.innerHTML = errorBlock(error);
    }
  }

  function operationsRun(button, action) {
    const complete = async () => {
      if (typeof state !== "undefined" && (
        state.scheduleDirty || state.attendanceDirty || state.neighborDirty?.size
      )) {
        throw new Error("Önce vardiya, puantaj ve komşu ay taslaklarını kaydedin.");
      }
      await action();
      const form = button.closest("form");
      if (form) form.dataset.dirty = "false";
    };
    if (typeof extrasRun === "function") return extrasRun(button, complete);
    button.disabled = true;
    Promise.resolve().then(complete).catch((error) => toast(error.message, true))
      .finally(() => { button.disabled = false; });
  }

  function openOperations(tool = activeTool) {
    if (tools.some(([key]) => key === tool)) activeTool = tool;
    shell();
    showExtras(dialog);
    renderTool(activeTool);
  }

  if (typeof EXTRAS_MENU !== "undefined" && !EXTRAS_MENU.some(([label]) => label === "Operasyon merkezi")) {
    EXTRAS_MENU.push(["Operasyon merkezi", openOperations]);
  }
  window.operationsTools = { open: openOperations };
})();
