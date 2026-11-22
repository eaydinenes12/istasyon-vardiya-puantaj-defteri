"use strict";

(() => {
  const kindLabels = {
    weekly_off: "Hafta tatili (yıllık izinden düşmez)",
    annual_leave: "Yıllık izin",
    sick_leave: "Raporlu",
    unpaid_leave: "Ücretsiz izin",
    marriage_leave: "Evlilik izni",
    quarantine: "Karantina",
    half_day: "Yarım gün",
    absent: "İşe gelmedi",
  };
  const statusLabels = { pending: "Bekliyor", approved: "Onaylı", cancelled: "İptal" };
  let context = {};

  function dateValue(value) {
    return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : "";
  }

  function linkedMonth() {
    return typeof extrasActiveMonth === "function" ? extrasActiveMonth() : new Date().toISOString().slice(0, 7);
  }

  function ensureScheduleClean() {
    if (typeof state !== "undefined" && (state.scheduleDirty || state.attendanceDirty || state.neighborDirty?.size)) {
      throw new Error("Önce kaydedilmemiş vardiya ve puantaj değişikliklerini kaydedin veya geri alın; bağlantılı izin taslakları ezemez.");
    }
  }

  async function refreshLinkedSchedule() {
    ensureScheduleClean();
    if (typeof state !== "undefined" && state.schedule && typeof loadSchedule === "function") {
      await loadSchedule();
    }
    ensureScheduleClean();
    if (typeof state !== "undefined" && state.attendance && typeof loadAttendance === "function") {
      await loadAttendance();
    }
  }

  function openLinkedLeave(options = {}) {
    context = options || {};
    const dialog = extrasDialog("linked-leave-dialog", "Bağlantılı izinler",
      "Onaylanan kayıtlar uygun boş günlere işlenir; dolu hücreler ve kilitli dönemler korunur.");
    const body = dialog.querySelector(".extras-body");
    const contextualIds = Array.isArray(context.employee_ids)
      ? context.employee_ids.map(Number).filter(Number.isInteger)
      : Number.isInteger(Number(context.employee_id)) && Number(context.employee_id) > 0
        ? [Number(context.employee_id)] : null;
    const selected = Array.isArray(context.cells)
      ? context.cells
      : (contextualIds ? [] : (typeof window.easyAssistant?.selectedCells === "function" ? window.easyAssistant.selectedCells() : []));
    const selectedIds = [...new Set((contextualIds || selected.map((cell) => Number(cell.employee_id)))
      .filter(Number.isInteger))];
    const selectedDates = selected.map((cell) => dateValue(cell.date)).filter(Boolean).sort();
    const onePerson = selectedIds.length === 1 ? selectedIds[0] : "";
    const uniqueDates = [...new Set(selectedDates)];
    const contiguous = uniqueDates.length > 1 && uniqueDates.every((day, index) =>
      index === 0 || Date.parse(`${day}T12:00:00`) - Date.parse(`${uniqueDates[index - 1]}T12:00:00`) === 86400000);
    const safeSelection = selected.length === 1 || (onePerson && contiguous);
    const start = safeSelection ? (uniqueDates[0] || "") : "";
    const end = safeSelection ? (uniqueDates[uniqueDates.length - 1] || start) : "";
    const patternValue = onePerson && contiguous ? "range" : "single";
    const selectedPeople = new Set(selectedIds);
    const employeeOptions = state.people.filter((person) => person.active)
      .sort((left, right) => left.name.localeCompare(right.name, "tr"))
      .map((person) => `<option value="${person.id}"${selectedPeople.has(person.id) ? " selected" : ""}>${escapeHtml(person.name)}</option>`)
      .join("");
    let editingRequest = null;
    let listedRequests = [];
    body.innerHTML = `
      <form class="dialog-grid" data-linked-form>
        <label class="linked-leave-people">Personel (birden çok kişi için Ctrl/Shift ile seçin)
          <select name="employee_ids" multiple size="5" required aria-label="İzin atanacak aktif personeller">${employeeOptions}</select></label>
        <div class="linked-leave-selection-actions">
          <button class="button button-light" type="button" data-linked-select-all>Tüm aktifleri seç</button>
          <button class="button button-light" type="button" data-linked-clear-selection>Seçimi temizle</button>
          <span data-linked-selection-count aria-live="polite"></span>
        </div>
        <label>İzin türü<select name="kind">${Object.entries(kindLabels).map(([key, label]) => `<option value="${key}">${escapeHtml(label)}</option>`).join("")}</select></label>
        <label>Yinelenme<select name="pattern"><option value="single"${patternValue === "single" ? " selected" : ""}>Tek gün</option><option value="range"${patternValue === "range" ? " selected" : ""}>Tarih aralığı</option><option value="weekdays">Haftanın seçili günleri</option></select></label>
        <label>Başlangıç<input name="start" type="date" required value="${escapeHtml(start)}"></label>
        <label>Bitiş<input name="end" type="date" required value="${escapeHtml(end)}"></label>
        <fieldset data-linked-weekdays hidden><legend>Tekrar günleri</legend>
          ${["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"].map((label, index) =>
    `<label class="extras-check"><input type="checkbox" name="weekday" value="${index}">${label}</label>`).join("")}
        </fieldset>
        <label class="dialog-help">Not (isteğe bağlı)<input name="note" maxlength="300" autocomplete="off"></label>
        <div class="dialog-actions"><button class="button button-primary" type="submit" data-linked-submit-approve>Oluştur ve hemen onayla</button>
          <button class="button button-light" type="submit" data-linked-submit>Bekleyen izin oluştur</button>
          <button class="button button-light" type="button" data-linked-cancel-edit hidden>Düzenlemeyi bırak</button></div>
        <p class="dialog-help">“Oluştur ve hemen onayla” izni kaydeder, onaylar ve kayıtlı aylardaki uygun boş günlere tek adımda işler; dolu hücreler ve kilitli dönemler değişmez.</p>
      </form>
      <section aria-labelledby="linked-leave-list-title">
        <h3 id="linked-leave-list-title">İzin kayıtları</h3>
        <p>Onayla, kayıtlı tüm aylarda uygun boş günleri otomatik doldurur. Düzenlemek veya iptal etmek için önce her uygulanmış dönemi “İlişkili hücreleri geri al” ile kaldırın.</p>
        <div data-linked-outcome role="status" aria-live="polite"></div>
        <div data-linked-list role="region" aria-live="polite">Kayıtlar yükleniyor…</div>
        <button class="button button-outline" type="button" data-linked-forecast>Yıl sonuna kadar izin bakiyesi</button>
        <div data-linked-forecast-result aria-live="polite"></div>
        <h3>Aylık izin yoğunluğu ve çakışmalar</h3>
        <button class="button button-light" type="button" data-linked-calendar>Ayı bölüm bazında incele</button>
        <div data-linked-calendar-result role="status" aria-live="polite"></div>
      </section>
      <section aria-labelledby="linked-fairness-title">
        <h3 id="linked-fairness-title">Adil dağılım önerileri</h3>
        <button class="button button-light" type="button" data-linked-fairness>Önerileri incele</button>
        <div data-linked-fairness-results role="status"></div>
      </section>
      <section aria-labelledby="linked-candidate-title">
        <h3 id="linked-candidate-title">Vardiya için uygun personel</h3>
        <form class="dialog-grid" data-linked-candidate-form>
          <label>Vardiya tarihi<input name="date" type="date" required value="${escapeHtml(dateValue(context.date) || start)}"></label>
          <label>Bölüm<select name="section">${schedulingSectionOptions(context.section)}</select></label>
          <label>Başlangıç<input name="start" type="time" required value="${escapeHtml(context.start || "09:00")}"></label>
          <label>Bitiş<input name="end" type="time" required value="${escapeHtml(context.end || "17:00")}"></label>
          <label>Mevcut personel<select name="employee_id" required><option value="">Seçin</option>${extrasPeopleOptions(context.employee_id || onePerson, false)}</select></label>
          <div class="dialog-actions"><button class="button button-light" type="submit">Uygunluğu denetle</button></div>
        </form>
        <div data-linked-candidates role="status"></div>
      </section>
      <section aria-labelledby="linked-hourly-title">
        <h3 id="linked-hourly-title">Saat aralıklı izin önizlemesi</h3>
        <form class="dialog-grid" data-hourly-leave-form>
          <label>Personel<select name="employee_id" required>${extrasPeopleOptions(context.employee_id || onePerson, false)}</select></label>
          <label>Tarih<input name="date" type="date" required value="${escapeHtml(dateValue(context.date) || start)}"></label>
          <label>İzin başlangıcı<input name="start" type="time" required value="10:00"></label>
          <label>İzin bitişi<input name="end" type="time" required value="12:00"></label>
          <button class="button button-outline" type="submit">Saat aralığını önizle</button>
        </form>
        <div data-hourly-leave-result role="status" aria-live="polite"></div>
        <form class="dialog-grid" data-hourly-rule-form>
          <label>Kurumca açıkça belirlenen puantaj karşılığı
            <input name="description" maxlength="300" placeholder="Kural yoksa boş bırakın"></label>
          <label>Yürürlük tarihi<input name="effective_date" type="date"></label>
          <button class="button button-light" type="submit">Kurum kuralını kaydet</button>
        </form>
        <p class="dialog-help">Boş açıklama, kuralın yapılandırılmadığı anlamına gelir. Bu alan hukuki kural önermiyor ve puantajı otomatik değiştirmiyor.</p>
      </section>`;

    const pattern = body.querySelector('[name="pattern"]');
    const employeeSelect = body.querySelector('[name="employee_ids"]');
    const selectionCount = body.querySelector("[data-linked-selection-count]");
    const startInput = body.querySelector('[name="start"]');
    const endInput = body.querySelector('[name="end"]');
    const weekdays = body.querySelector("[data-linked-weekdays]");
    const updateSelectionCount = () => {
      const count = [...employeeSelect.selectedOptions].length;
      selectionCount.textContent = count ? `${count} personel seçili` : "Personel seçin.";
    };
    const updatePattern = () => {
      weekdays.hidden = pattern.value !== "weekdays";
      endInput.disabled = pattern.value === "single";
      if (pattern.value === "single") endInput.value = startInput.value;
    };
    employeeSelect.addEventListener("change", updateSelectionCount);
    body.querySelector("[data-linked-select-all]").addEventListener("click", () => {
      for (const option of employeeSelect.options) option.selected = true;
      updateSelectionCount();
    });
    body.querySelector("[data-linked-clear-selection]").addEventListener("click", () => {
      for (const option of employeeSelect.options) option.selected = false;
      updateSelectionCount();
    });
    pattern.addEventListener("change", updatePattern);
    startInput.addEventListener("change", () => { if (pattern.value === "single") endInput.value = startInput.value; });
    updatePattern();
    updateSelectionCount();
    const requestForm = body.querySelector("[data-linked-form]");
    body.querySelector("[data-linked-forecast]").addEventListener("click", (event) => {
      extrasRun(event.currentTarget, async () => {
        const month = linkedMonth();
        const data = await request(`/api/linked-leave/forecast?month=${encodeURIComponent(month)}`);
        const output = body.querySelector("[data-linked-forecast-result]");
        if (!output) return;
        const value = (number) => escapeHtml(number ?? "Açılış yok");
        output.innerHTML = `<p>${escapeHtml(data.from)} sonrasından ${escapeHtml(data.through)} tarihine kadar.
          Onaylı günler tahminden düşülür; bekleyenler ayrı gösterilir. Resmî bakiye değişmez.</p>` +
          controlsTable(["Personel", "Ay sonu bakiyesi", "Onaylı gün", "Bekleyen gün", "Tahmini bakiye"],
            data.people.map((item) => [escapeHtml(item.name), value(item.remaining),
              value(item.approved), value(item.pending), value(item.projected)]),
            "Bu aralıkta yıllık izin talebi yok.");
      });
    });
    body.querySelector("[data-linked-calendar]").addEventListener("click", (event) => {
      extrasRun(event.currentTarget, async () => {
        const month = linkedMonth();
        const data = await request(`/api/leave-requests?month=${encodeURIComponent(month)}`);
        const output = body.querySelector("[data-linked-calendar-result]");
        const rows = data.days.flatMap((day) => day.sections.map((section) => [
          escapeHtml(day.date), escapeHtml(section.section), String(section.count),
          escapeHtml(section.people.join(", ")),
        ]));
        const overlaps = (data.overlaps || []).map((item) =>
          `<li>${escapeHtml(item.date)} · ${escapeHtml(item.name)}: ${
            item.requests.map((request) => escapeHtml(kindLabels[request.kind] || request.label)).join(" / ")
          }. ${escapeHtml(item.message)}</li>`).join("");
        output.innerHTML = `<p>${escapeHtml(month)} · Aynı gün ve bölümde izinli kişiler aşağıdadır. Bölümü belirsiz kayıtlar ayrı gösterilir.</p>`
          + controlsTable(["Tarih", "Bölüm", "Kişi", "İzinli çalışanlar"], rows, "Bu ay için izin kaydı yok.")
          + `<h4>Önceliği yapılandırılmamış çakışan izin türleri</h4>${
            overlaps ? `<ul>${overlaps}</ul>` : "<p>Çakışan izin türü yok.</p>"
          }`;
      });
    });
    function finishEditing() {
      editingRequest = null;
      requestForm.reset();
      employeeSelect.multiple = true;
      requestForm.querySelector("[data-linked-submit]").textContent = "Bekleyen izin oluştur";
      requestForm.querySelector("[data-linked-submit-approve]").hidden = false;
      body.querySelector("[data-linked-cancel-edit]").hidden = true;
      updateSelectionCount();
      updatePattern();
    }
    body.querySelector("[data-linked-cancel-edit]").addEventListener("click", finishEditing);

    function showOutcome(result, label) {
      const conflicts = result.conflicts || [];
      body.querySelector("[data-linked-outcome]").innerHTML =
        `<p>${escapeHtml(label)} ${Number(result.applied || 0)} hücre işlendi; ${conflicts.length} çakışma korundu.</p>`
        + (conflicts.length ? `<ul>${conflicts.map((item) => {
          const person = state.people.find((person) => person.id === item.employee_id)?.name || `Personel ${item.employee_id}`;
          return `<li>${escapeHtml(item.date)} · ${escapeHtml(person)}: ${escapeHtml(item.reason)}${item.existing_kind ? ` (${escapeHtml(kindLabels[item.existing_kind] || item.existing_kind)})` : ""}</li>`;
        }).join("")}</ul>` : "");
    }

    body.querySelector("[data-linked-form]").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      const employeeIds = [...employeeSelect.selectedOptions].map((option) => Number(option.value));
      if (!employeeIds.length) {
        toast("En az bir personel seçin.", true);
        return;
      }
      if (editingRequest && employeeIds.length !== 1) {
        toast("Düzenlenen izin kaydı tek bir personele ait olmalıdır.", true);
        return;
      }
      const weekdayValues = [...form.querySelectorAll('[name="weekday"]:checked')].map((item) => Number(item.value));
      const approveNow = !editingRequest && Boolean(event.submitter?.matches("[data-linked-submit-approve]"));
      if (approveNow) {
        try {
          ensureScheduleClean();
        } catch (error) {
          toast(error.message, true);
          return;
        }
        if (!window.confirm(`${employeeIds.length} personel için izin oluşturulup hemen onaylanacak ve kayıtlı aylardaki uygun boş günlere işlenecek. Dolu hücreler ve kilitli dönemler değişmeyecek. Devam edilsin mi?`)) return;
      }
      extrasRun(event.submitter || form.querySelector("[data-linked-submit]"), async () => {
        const values = {
            kind: data.get("kind"),
            pattern: data.get("pattern"),
            start: data.get("start"),
            end: data.get("pattern") === "single" ? data.get("start") : data.get("end"),
            weekdays: weekdayValues,
            note: data.get("note") || "",
        };
        if (editingRequest) values.employee_id = employeeIds[0];
        const saved = await request(editingRequest
          ? `/api/linked-leave/requests/${encodeURIComponent(editingRequest.id)}`
          : employeeIds.length > 1 ? "/api/linked-leave/requests/bulk" : "/api/linked-leave/requests", {
          method: editingRequest ? "PUT" : "POST",
          body: JSON.stringify(editingRequest
            ? { revision: editingRequest.revision, request: values }
            : employeeIds.length > 1 ? { ...values, employee_ids: employeeIds } : { ...values, employee_id: employeeIds[0] }),
        });
        finishEditing();
        if (approveNow) {
          const created = saved.request ? [saved.request] : (saved.requests || []);
          const total = { applied: 0, conflicts: [] };
          const failures = [];
          for (const item of created) {
            try {
              const result = await request(`/api/linked-leave/requests/${encodeURIComponent(item.id)}/approve`, {
                method: "POST", body: JSON.stringify({ revision: item.revision, status: "approved" }),
              });
              total.applied += Number(result.applied || 0);
              total.conflicts.push(...(result.conflicts || []));
            } catch (error) {
              failures.push(error.message);
            }
          }
          showOutcome(total, `${created.length - failures.length} izin onaylandı.`);
          await refreshLinkedSchedule();
          await refreshRequests();
          if (failures.length) toast(`${failures.length} izin onaylanamadı ve beklemede kaldı: ${failures[0]}`, true);
          else toast(`${created.length} izin onaylandı; ${total.applied} hücre işlendi.`);
          return;
        }
        await refreshRequests();
        toast(`${employeeIds.length} personel için bekleyen izin talebi kaydedildi.`);
      });
    });

    async function refreshRequests() {
      body.querySelector("[data-linked-forecast-result]").textContent = "";
      const month = linkedMonth();
      const result = await request(`/api/linked-leave/requests?month=${encodeURIComponent(month)}`);
      listedRequests = result.requests;
      const list = body.querySelector("[data-linked-list]");
      if (!result.requests.length) {
        list.innerHTML = '<p>Bu ayı kapsayan izin kaydı yok.</p>';
        return;
      }
      list.innerHTML = `<ul class="linked-leave-list">${result.requests.map((item) => {
        const recurrence = item.pattern === "weekdays"
          ? ` · her ${item.weekdays.map((day) => ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"][day]).join(", ")}`
          : "";
        const actions = [
          item.status !== "cancelled" && !(item.applied_months || []).length
            ? `<button class="button button-light" type="button" data-linked-action="edit" data-id="${escapeHtml(item.id)}">Düzenle</button>`
            : "",
          item.status === "pending"
            ? `<button class="button button-light" type="button" data-linked-action="impact" data-id="${escapeHtml(item.id)}">Etkiyi önizle</button>
               <button class="button button-light" type="button" data-linked-action="approve" data-id="${escapeHtml(item.id)}" data-revision="${item.revision}">Onayla</button>`
            : "",
          item.status !== "cancelled"
            ? `<button class="button button-light" type="button" data-linked-action="cancel" data-id="${escapeHtml(item.id)}" data-revision="${item.revision}">İptal et</button>`
            : "",
          item.status === "approved"
            ? `<button class="button button-light" type="button" data-linked-action="apply" data-id="${escapeHtml(item.id)}">Bu aya uygula</button>`
            : "",
          item.status === "approved"
            ? `<button class="button button-light" type="button" data-linked-action="undo-request" data-id="${escapeHtml(item.id)}">Onayı ve tüm bağlı günleri geri al</button>`
            : "",
          item.status === "approved"
            ? `<button class="button button-light" type="button" data-linked-action="move" data-id="${escapeHtml(item.id)}">İzin tarihlerini birlikte taşı</button>`
            : "",
          ...(item.applied_months || []).map((month) =>
            `<button class="button button-light" type="button" data-linked-action="undo" data-id="${escapeHtml(item.id)}" data-month="${escapeHtml(month)}">${escapeHtml(month)} · İlişkili hücreleri geri al</button>`),
        ].filter(Boolean).join("");
        return `<li><div><strong>${escapeHtml(state.people.find((person) => person.id === item.employee_id)?.name || `Personel ${item.employee_id}`)}</strong>
          <span>${escapeHtml(kindLabels[item.kind] || item.kind)} · ${escapeHtml(item.start)}${item.end !== item.start ? ` – ${escapeHtml(item.end)}` : ""}${recurrence}</span>
          <span>Durum: ${escapeHtml(statusLabels[item.status] || item.status)} · Sürüm ${item.revision}</span>
          ${item.note ? `<span>${escapeHtml(item.note)}</span>` : ""}</div><div class="dialog-actions">${actions}</div></li>`;
      }).join("")}</ul>`;
    }

    body.querySelector("[data-linked-list]").addEventListener("click", async (event) => {
      const button = event.target.closest("[data-linked-action]");
      if (!button) return;
      const action = button.dataset.linkedAction;
      const id = button.dataset.id;
      extrasRun(button, async () => {
        if (action === "edit") {
          const item = listedRequests.find((request) => request.id === id);
          if (!item) throw new Error("İzin listesi değişti; pencereyi yeniden açın.");
          editingRequest = item;
          employeeSelect.multiple = false;
          employeeSelect.value = String(item.employee_id);
          updateSelectionCount();
          for (const key of ["pattern", "start", "end", "kind", "note"]) {
            requestForm.elements.namedItem(key).value = String(item[key] ?? "");
          }
          requestForm.querySelectorAll('[name="weekday"]').forEach((input) => {
            input.checked = item.weekdays.includes(Number(input.value));
          });
          updatePattern();
          requestForm.querySelector("[data-linked-submit]").textContent = "Düzenlemeyi kaydet · yeniden onay gerekir";
          requestForm.querySelector("[data-linked-submit-approve]").hidden = true;
          body.querySelector("[data-linked-cancel-edit]").hidden = false;
          employeeSelect.focus();
          requestForm.scrollIntoView({ block: "nearest" });
          return;
        } else if (action === "move") {
          ensureScheduleClean();
          const item = listedRequests.find((entry) => entry.id === id);
          if (!item || item.status !== "approved") throw new Error("Taşınacak onaylı izin bulunamadı.");
          const outcome = body.querySelector("[data-linked-outcome]");
          outcome.innerHTML = `<h3>Onaylı izni birlikte taşı</h3>
            <p>Eski izin hücreleri ve yeni tarihler tek işlemde önizlenir. Herhangi bir dönem kilitli, eski hücre değiştirilmiş veya yeni tarih çakışmalıysa hiçbir kayıt taşınmaz.</p>
            <form class="forms-fields" data-linked-move-form>
              <label>Yeni başlangıç<input name="start" type="date" value="${escapeHtml(item.start)}" required></label>
              <label>Yeni bitiş<input name="end" type="date" value="${escapeHtml(item.end)}" required></label>
              <button class="button button-outline" type="submit">Eski ve yeni tarihleri önizle</button>
            </form>
            <div data-linked-move-result aria-live="polite"></div>`;
          const form = outcome.querySelector("[data-linked-move-form]");
          const result = outcome.querySelector("[data-linked-move-result]");
          let latestMove = null;
          form.addEventListener("input", () => {
            latestMove = null;
            result.querySelector("[data-linked-move-apply]")?.remove();
          });
          form.addEventListener("change", () => {
            latestMove = null;
            result.querySelector("[data-linked-move-apply]")?.remove();
          });
          form.addEventListener("submit", async (submitEvent) => {
            submitEvent.preventDefault();
            const values = new FormData(form);
            latestMove = {
              request_id: id, request_revision: item.revision,
              start: String(values.get("start")), end: String(values.get("end")),
            };
            const preview = await request("/api/linked-leave/move-preview", {
              method: "POST", body: JSON.stringify(latestMove),
            });
            result.innerHTML = `<h4>${escapeHtml(item.start)}–${escapeHtml(item.end)} → ${escapeHtml(preview.start)}–${escapeHtml(preview.end)}</h4>
              <p>Eski ilişkili gün: ${preview.old_removals} · Yeni ilişkili gün: ${preview.new_applications}</p>
              ${preview.months.length ? `<div class="controls-table-wrap"><table class="archive-table">
                <thead><tr><th>Dönem</th><th>Kaldırılacak eski gün</th><th>Yeni eklenecek gün</th></tr></thead>
                <tbody>${preview.months.map((month) => `<tr><td>${escapeHtml(month.month)} · sürüm ${escapeHtml(month.revision || "yok")}</td>
                  <td>${month.old_removals.length ? month.old_removals.map(escapeHtml).join(", ") : "—"}</td>
                  <td>${month.new_applications.length ? month.new_applications.map((entry) => escapeHtml(entry.date)).join(", ") : "—"}</td></tr>`).join("")}</tbody>
              </table></div>` : "<p>Henüz etkilenmiş kayıtlı vardiya dönemi yok.</p>"}
              ${preview.conflicts.length ? `<h4>Yeni tarihte çakışma</h4><ul>${preview.conflicts.map((entry) =>
                `<li>${escapeHtml(entry.month)} · ${escapeHtml(entry.date)}: ${escapeHtml(entry.reason)}</li>`).join("")}</ul>` : ""}
              ${preview.blocked.length ? `<h4>Taşımayı engelleyen kayıt</h4><ul>${preview.blocked.map((entry) =>
                `<li>${escapeHtml(entry.month)}${entry.date ? ` · ${escapeHtml(entry.date)}` : ""}: ${escapeHtml(entry.reason)}</li>`).join("")}</ul>` : ""}
              ${preview.unscheduled.length ? `<p>Henüz kaydedilmiş vardiyası olmayan yeni tarihler, daha sonra ilgili vardiya dönemi kaydedildiğinde uygulanacak: ${preview.unscheduled.map(escapeHtml).join(", ")}.</p>` : ""}
              <p>${preview.can_apply ? "Eski ve yeni izin değişiklikleri birlikte uygulanabilir." : "Çakışma veya koruma nedeniyle taşıma yapılamaz; hiçbir kayıt değişmedi."}</p>
              ${preview.can_apply ? '<button class="button button-primary" type="button" data-linked-move-apply>Önizlenen tarihleri birlikte uygula</button>' : ""}`;
            const apply = result.querySelector("[data-linked-move-apply]");
            if (apply) apply.addEventListener("click", async () => {
              ensureScheduleClean();
              if (!latestMove || !window.confirm(
                `Eski ${preview.old_start}–${preview.old_end} izinini kaldırıp yeni ${preview.start}–${preview.end} tarihlerini aynı işlemde uygulamak istiyor musunuz?`)) return;
              await request("/api/linked-leave/move", {
                method: "POST",
                body: JSON.stringify({ ...latestMove, preview_hash: preview.preview_hash, confirmed: true }),
              });
              await refreshLinkedSchedule();
              toast("İzin tarihleri tek işlemde taşındı; onay korundu.");
            });
          });
          outcome.scrollIntoView({ block: "nearest" });
        } else if (action === "impact") {
          const month = linkedMonth();
          const impact = await request(`/api/linked-leave/impact?${new URLSearchParams({ month, request_id: id })}`);
          const outcome = body.querySelector("[data-linked-outcome]");
          const shifts = impact.shifts.map((shift) => {
            const names = shift.candidates.map((person) => escapeHtml(person.name)).join(", ");
            const more = shift.candidate_count - shift.candidates.length;
            return `<li>${escapeHtml(shift.date)} · ${escapeHtml(shift.section)} · ${escapeHtml(shift.start)}–${escapeHtml(shift.end)}: ${names || "Uygun yedek bulunamadı"}${more ? ` ve ${more} kişi daha` : ""}</li>`;
          }).join("");
          const gaps = impact.gaps.map((gap) =>
            `<li>${escapeHtml(gap.date)} · ${escapeHtml(gap.section)} · ${escapeHtml(gap.start)}–${escapeHtml(gap.end)}: eksik ${gap.before} → ${gap.after}</li>`).join("");
          outcome.innerHTML = `<h3>İzin etki önizlemesi · ${escapeHtml(month)}</h3>
            <p>Kayıtlı vardiyalar üzerinden hesaplanır. Talep bu ayda ${impact.requested_days} günü kapsıyor. Bu işlem izin, vardiya veya yedek ataması kaydetmez.</p>
            ${shifts ? `<h4>Etkilenen vardiyalar ve uygun yedekler</h4><ul>${shifts}</ul>` : "<p>Bu ay için etkilenen çalışma vardiyası yok.</p>"}
            ${gaps ? `<h4>Yeni veya büyüyen kadro açıkları</h4><ul>${gaps}</ul>` : impact.has_windows
              ? "<p>Tanımlı saat aralıklarında yeni veya büyüyen kadro açığı yok.</p>"
              : shifts ? "<p>Etkilenen bölümlerde saatlik asgari kadro kuralı tanımlı değil.</p>" : ""}
            <h4>Alternatif tarihleri karşılaştır</h4>
            <p>En fazla üç tarih aralığı seçin. Karşılaştırma kaydetmez; izin örüntüsü ve seçili günler korunur.</p>
            <form data-linked-alternative-form class="forms-fields">
              ${[1, 2, 3].map((number) => `<fieldset><legend>Alternatif ${number}</legend>
                <label>Başlangıç<input name="alternative_start" type="date" ${number === 1 ? "required" : ""}></label>
                <label>Bitiş<input name="alternative_end" type="date" ${number === 1 ? "required" : ""}></label>
              </fieldset>`).join("")}
              <button class="button button-outline" type="submit">Tarih etkilerini yan yana karşılaştır</button>
            </form><div data-linked-alternatives-result aria-live="polite"></div>`;
          outcome.querySelector("[data-linked-alternative-form]").addEventListener("submit", async (submitEvent) => {
            submitEvent.preventDefault();
            const form = submitEvent.currentTarget;
            const data = new FormData(form);
            const starts = data.getAll("alternative_start").map((value) => String(value).trim());
            const ends = data.getAll("alternative_end").map((value) => String(value).trim());
            const alternatives = starts.map((start, index) => ({ start, end: ends[index] }))
              .filter((entry) => entry.start || entry.end);
            if (!alternatives.length || alternatives.some((entry) => !entry.start || !entry.end)) {
              toast("Her alternatif için başlangıç ve bitiş tarihini birlikte seçin.", true);
              return;
            }
            const params = new URLSearchParams({ month, request_id: id });
            alternatives.forEach((entry) => {
              params.append("alternative_start", entry.start);
              params.append("alternative_end", entry.end);
            });
            const target = outcome.querySelector("[data-linked-alternatives-result]");
            target.textContent = "Tarih etkileri karşılaştırılıyor…";
            try {
              const comparison = await request(`/api/linked-leave/impact?${params}`);
              const columns = [
                ["Seçili tarihler", comparison],
                ...(comparison.alternatives || []).map((item) => [item.label, item]),
              ];
              target.innerHTML = `<div class="controls-table-wrap"><table class="archive-table">
                <thead><tr><th>Ölçüt</th>${columns.map(([label, item]) =>
                  `<th>${escapeHtml(label)}${item.start ? ` · ${escapeHtml(item.start)} – ${escapeHtml(item.end)}` : ""}</th>`).join("")}</tr></thead>
                <tbody>${[
                  ["İzin gün sayısı", (item) => item.requested_days],
                  ["Etkilenen vardiya", (item) => item.shifts.length],
                  ["Uygun yedek toplamı", (item) => item.shifts.reduce((sum, shift) => sum + shift.candidate_count, 0)],
                  ["Yeni/büyüyen kadro açığı aralığı", (item) => item.gaps.length],
                ].map(([label, value]) => `<tr><th>${escapeHtml(label)}</th>${columns.map(([, item]) =>
                  `<td>${escapeHtml(value(item))}</td>`).join("")}</tr>`).join("")}</tbody>
              </table></div>${columns.slice(1).map(([label, item]) => `<section>
                <h5>${escapeHtml(label)} · vardiya/yedek ayrıntısı</h5>
                ${item.shifts.length ? `<ul>${item.shifts.map((shift) =>
                  `<li>${escapeHtml(shift.date)} · ${escapeHtml(shift.section)} · ${escapeHtml(shift.start)}–${escapeHtml(shift.end)}: ${shift.candidate_count} uygun yedek</li>`).join("")}</ul>` : "<p>Etkilenen vardiya yok.</p>"}
                ${item.gaps.length ? `<ul>${item.gaps.map((gap) =>
                  `<li>${escapeHtml(gap.date)} · ${escapeHtml(gap.section)} · ${escapeHtml(gap.start)}–${escapeHtml(gap.end)}: eksik ${gap.before} → ${gap.after}</li>`).join("")}</ul>` : "<p>Yeni veya büyüyen kadro açığı yok.</p>"}
              </section>`).join("")}<p>Bu karşılaştırma hiçbir talebi veya çizelgeyi değiştirmedi.</p>`;
            } catch (error) {
              target.textContent = error.message;
            }
          });
          outcome.scrollIntoView({ block: "nearest" });
        } else if (action === "approve" || action === "cancel") {
          ensureScheduleClean();
          if (action === "approve" && !window.confirm("İzin onaylanacak ve kayıtlı tüm aylardaki uygun boş günlere otomatik işlenecek. Dolu hücreler ve kilitli dönemler değişmeyecek. Onaylıyor musunuz?")) return;
          const result = await request(`/api/linked-leave/requests/${encodeURIComponent(id)}/approve`, {
            method: "POST",
            body: JSON.stringify({ revision: Number(button.dataset.revision), status: action === "approve" ? "approved" : "cancelled" }),
          });
          showOutcome(result, action === "approve" ? "İzin onaylandı." : "İzin iptal edildi.");
          if (action === "approve") await refreshLinkedSchedule();
        } else if (action === "apply") {
          const month = linkedMonth();
          ensureScheduleClean();
          const preview = await request("/api/linked-leave/preview", {
            method: "POST", body: JSON.stringify({ month, request_ids: [id] }),
          });
          const summary = `${preview.placements.length} hücre uygulanacak; ${preview.conflicts.length} dolu veya çakışan hücre korunacak.`;
          if (!window.confirm(`${summary}\nÖnizlemeyi onaylayıp uygun boş günlere uygula?`)) return;
          const result = await request("/api/linked-leave/apply", {
            method: "POST",
            body: JSON.stringify({
              month, request_ids: [id], schedule_revision: preview.schedule_revision,
              preview_hash: preview.preview_hash, confirm_conflicts: preview.conflicts.length > 0,
            }),
          });
          showOutcome(result, "İzin uygulandı.");
          await refreshLinkedSchedule();
          toast("İzin boş ve uygun günlere uygulandı; çakışmalar değiştirilmedi.");
        } else if (action === "undo-request") {
          ensureScheduleClean();
          const item = listedRequests.find((entry) => entry.id === id);
          if (!item) throw new Error("İzin listesi değişti; pencereyi yeniden açın.");
          const revisions = Object.fromEntries(await Promise.all((item.applied_months || []).map(async (month) => {
            const schedule = await request(`/api/schedule?month=${encodeURIComponent(month)}`);
            return [month, schedule.revision];
          })));
          if (!window.confirm(`${item.start} – ${item.end} tarihli izin onayı ve ${Object.keys(revisions).length} dönemdeki tüm bağlı günler birlikte geri alınsın mı?\nTalep beklemeye döner. Değişmiş hücre veya kilit varsa hiçbir kayıt değişmez.`)) return;
          await request("/api/linked-leave/undo-request", {
            method: "POST", body: JSON.stringify({
              request_id: id, request_revision: item.revision, schedule_revisions: revisions, confirmed: true,
            }),
          });
          await refreshLinkedSchedule();
          toast("İzin onayı ve tüm bağlı günler birlikte geri alındı. Talep bekliyor.");
        } else if (action === "undo") {
          const month = button.dataset.month || linkedMonth();
          ensureScheduleClean();
          const schedule = await request(`/api/schedule?month=${encodeURIComponent(month)}`);
          if (!window.confirm("Bu izin kaydının değiştirilmemiş hücrelerini geri almak istiyor musunuz?")) return;
          await request("/api/linked-leave/undo", {
            method: "POST",
            body: JSON.stringify({ month, request_ids: [id], schedule_revision: schedule.revision }),
          });
          await refreshLinkedSchedule();
          toast("İzinle ilişkili değişiklikler geri alındı.");
        }
        await refreshRequests();
      });
    });

    body.querySelector("[data-linked-fairness]").addEventListener("click", (event) => {
      extrasRun(event.currentTarget, async () => {
        const result = await request("/api/linked-leave/fairness-proposals", {
          method: "POST", body: JSON.stringify({ month: linkedMonth() }),
        });
        const target = body.querySelector("[data-linked-fairness-results]");
        target.innerHTML = result.proposals.length
          ? `<ul>${result.proposals.map((item) =>
            `<li>${escapeHtml(item.date)} · ${escapeHtml(item.section)}: ${escapeHtml(item.from.name)} → ${escapeHtml(item.to.name)} (${escapeHtml(item.shift.start)}–${escapeHtml(item.shift.end)}). ${escapeHtml(item.reason)}</li>`).join("")}</ul>
            <p>Öneriler yalnızca inceleme içindir; çizelge otomatik değiştirilmedi.</p>`
          : "<p>Yeni kontrol uyarısı oluşturmadan yapılabilecek güvenli bir dağılım önerisi bulunamadı.</p>";
      });
    });

    body.querySelector("[data-linked-candidate-form]").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      extrasRun(form.querySelector('[type="submit"]'), async () => {
        const result = await request("/api/linked-leave/candidates", {
          method: "POST",
          body: JSON.stringify({
            employee_id: Number(data.get("employee_id")),
            exclude_employee_id: Number(data.get("employee_id")),
            date: data.get("date"), section: data.get("section"),
            start: data.get("start"), end: data.get("end"),
          }),
        });
        const target = body.querySelector("[data-linked-candidates]");
        target.innerHTML = result.candidates.length
          ? `<ul>${result.candidates.map((item) =>
            `<li><strong>${escapeHtml(item.name)}</strong> · ${item.eligible ? "Uygun" : `Uygun değil: ${escapeHtml(item.reasons.join(" "))}`}
              ${item.hour_projection ? `<div class="candidate-hours"><b>Kaydedilmiş hafta saatleri:</b> ${item.hour_projection.weeks.map((week) =>
                `${escapeHtml(week.start)}–${escapeHtml(week.end)}: ${week.scheduled_hours_before} → ${week.projected_hours} saat` +
                (week.configured_limit_hours ? ` · yapılandırılmış sınır ${week.configured_limit_hours} saat (${week.limit_assessment === "exceeds_configured_limit" ? "aşılır" : "içinde"})` : " · sınır yapılandırılmamış")
              ).join("; ")}<small>${item.hour_projection.unknowns.map(escapeHtml).join(" ")}</small></div>` : ""}</li>`).join("")}</ul>
            <p><b>Kontrol edilen kurallar:</b> ${escapeHtml(result.checked_rules.join(", "))}</p>
            ${result.required_skills.length ? `<p>Gerekli yetkinlikler: ${escapeHtml(result.required_skills.join(", "))}</p>` : ""}`
          : "<p>Etkin personel bulunamadı.</p>";
      });
    });

    const hourlyForm = body.querySelector("[data-hourly-leave-form]");
    const hourlyResult = body.querySelector("[data-hourly-leave-result]");
    hourlyForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(hourlyForm);
      extrasRun(hourlyForm.querySelector('[type="submit"]'), async () => {
        const preview = await request("/api/linked-leave/hourly-preview", {
          method: "POST",
          body: JSON.stringify({
            employee_id: Number(data.get("employee_id")), date: data.get("date"),
            start: data.get("start"), end: data.get("end"),
          }),
        });
        hourlyResult.innerHTML = `<h4>${escapeHtml(preview.name)} · ${escapeHtml(preview.date)}</h4>
          <p>Vardiya: ${escapeHtml(preview.shift.start.slice(11, 16))}–${escapeHtml(preview.shift.end.slice(11, 16))}. 
            Saatlik izin: ${escapeHtml(preview.leave.start.slice(11, 16))}–${escapeHtml(preview.leave.end.slice(11, 16))}
            (${preview.leave.minutes} dakika).</p>
          <p>Kalan çalışma aralıkları: ${preview.remaining_intervals.map((item) =>
            `${escapeHtml(item.start.slice(11, 16))}–${escapeHtml(item.end.slice(11, 16))}`).join(", ") || "Yok"}.</p>
          <p>Puantaj kuralı: ${escapeHtml(preview.attendance_rule.configured
            ? `${preview.attendance_rule.effective_date} · ${preview.attendance_rule.description}`
            : preview.attendance_rule.notice)}</p>
          <p>${escapeHtml(preview.attendance_rule.notice)} ${preview.manual_attendance_code
            ? `Mevcut manuel puantaj kodu: ${escapeHtml(preview.manual_attendance_code)}.` : ""}
            ${preview.saved ? "" : "Önizleme kaydedilmedi; vardiya ve puantaj değişmedi."}</p>`;
      }).catch((error) => { hourlyResult.textContent = error.message; });
    });
    const hourlyRuleForm = body.querySelector("[data-hourly-rule-form]");
    request("/api/linked-leave/hourly-rule").then((rule) => {
      if (!dialog.open) return;
      hourlyRuleForm.elements.description.value = rule.description || "";
      hourlyRuleForm.elements.effective_date.value = rule.effective_date || "";
    }).catch((error) => { if (dialog.open) toast(error.message, true); });
    hourlyRuleForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(hourlyRuleForm);
      extrasRun(hourlyRuleForm.querySelector('[type="submit"]'), async () => {
        await request("/api/linked-leave/hourly-rule", {
          method: "PUT",
          body: JSON.stringify({
            description: data.get("description"), effective_date: data.get("effective_date"),
          }),
        });
        toast("Kurumun saatlik izin puantaj açıklaması kaydedildi.");
      }).catch((error) => toast(error.message, true));
    });

    if (typeof context.onBack === "function") {
      const back = document.createElement("button");
      back.className = "button button-light";
      back.type = "button";
      back.dataset.linkedBack = "";
      back.textContent = context.backLabel || "← Personel listesine dön";
      back.addEventListener("click", () => { dialog.close(); context.onBack(); });
      dialog.querySelector(".extras-actions").prepend(back);
    }
    showExtras(dialog);
    refreshRequests().catch((error) => toast(error.message, true));
    return dialog;
  }

  function schedulingSectionOptions(selected) {
    const sections = typeof SECTIONS !== "undefined" ? SECTIONS : ["MARKET", "TEMİZLİK", "ÖSSE"];
    return sections.map((section) => `<option value="${escapeHtml(section)}"${section === selected ? " selected" : ""}>${escapeHtml(section)}</option>`).join("");
  }

  function openSelection(cells) {
    const list = Array.isArray(cells) ? cells : [];
    return openLinkedLeave({ cells: list });
  }

  function openCandidates(cell) {
    return openLinkedLeave(cell || {});
  }

  window.linkedLeave = { open: openLinkedLeave, openSelection, openCandidates };
})();
