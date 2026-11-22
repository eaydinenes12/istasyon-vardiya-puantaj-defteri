"use strict";

/* Kontrol ve mutabakat araçları (öneri listesi A–G): PDKS karşılaştırması,
   yayınlanan plan farkı ve puantaj onay akışı. extras.js'den sonra yüklenir. */

function controlsFileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = () => reject(new Error("Dosya okunamadı."));
    reader.readAsDataURL(file);
  });
}

function controlsDate(value) {
  if (!value) return "";
  const [day, time] = value.split("T");
  const [y, m, d] = day.split("-");
  return `${d}.${m}.${y}${time ? ` ${time.slice(0, 5)}` : ""}`;
}

function controlsTable(headers, rows, empty) {
  if (!rows.length) return `<p class="archive-empty">${escapeHtml(empty)}</p>`;
  return `<div class="controls-table-wrap"><table class="archive-table extras-table"><thead><tr>${headers.map((item) => `<th>${escapeHtml(item)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

const CONTROLS_SEVERITY = { error: "Hata", warning: "Uyarı", info: "Bilgi" };
const PDKS_LABELS = {
  no_show: "Gelmedi", unplanned: "Plansız çalışma", late: "Geç geldi", early: "Erken çıktı", missing_punch: "Eksik okutma",
};

/* ---------- A1 / A4: PDKS ↔ plan mutabakatı, geç gelme / erken çıkma ---------- */

function pdksSourceHealthMarkup(report) {
  const heading = '<h3 class="extras-subtitle">Kart kayıtlarının sağlık kontrolü</h3>';
  if (!report) return `${heading}<p>Bu eski aktarımda kaynak satır bulguları yok. Denetlemek için özgün dosyayı yeniden okutun.</p>`;
  const labels = { duplicate: "Olası mükerrer", missing: "Eksik saat", midnight: "Gece geçişi / 00:00" };
  const count = Object.values(report.counts).reduce((sum, value) => sum + value, 0);
  return `${heading}<p>Bulgular kaynak satırlarından gelir. Gece geçişi hata anlamına gelmez; eksik saat tahmin edilmez.</p>
    <div class="controls-chips">${Object.entries(labels).map(([key, label]) =>
      `<span class="controls-chip">${label}: <b>${report.counts[key]}</b></span>`).join("")}</div>
    ${report.missing_clusters.length ? `<p>Birden çok kişide eksik saat: ${report.missing_clusters.map((item) =>
      `${escapeHtml(item.date)} (${item.people} kişi)`).join(", ")}</p>` : ""}
    ${count > report.findings.length ? `<p>${count} bulgunun ilk ${report.findings.length} tanesi gösteriliyor.</p>` : ""}
    ${controlsTable(["Kaynak satırı", "Tarih", "Dosyadaki personel", "Bulgu"],
      report.findings.map((item) => [item.row, escapeHtml(item.date), escapeHtml(item.name), labels[item.kind]]),
      "Örüntü bulunmadı.")}`;
}

function pdksTimelineMarkup(records) {
  if (!records?.length) return "";
  const rows = records.map((record) => {
    const readings = record.punches?.length
      ? record.punches.map((item) =>
        `${escapeHtml(item.time)} · ${item.row} satır · ${
          { in: "Giriş", out: "Çıkış", punch: "Okutma" }[item.direction] || "Okutma"
        }`).join("<br>")
      : `Giriş ${escapeHtml(record.in || "yok")} · Çıkış ${escapeHtml(record.out || "yok")} · eski kayıtta ara okutmalar yok`;
    const pairings = record.pairings?.length
      ? record.pairings.map((item, index) =>
        `${index + 1}. ${escapeHtml(item.in || "Giriş yok")} → ${escapeHtml(item.out || "Çıkış yok")} (kaynak satırı ${item.rows.join(", ")})`).join("<br>")
      : `Giriş ${escapeHtml(record.in || "yok")} → Çıkış ${escapeHtml(record.out || "yok")}`;
    return `<tr><td>${escapeHtml(record.date)}</td><td>${escapeHtml(record.name)}</td><td>${readings}</td><td>${pairings}</td></tr>`;
  }).join("");
  return `<details class="pdks-timeline">
    <summary>Günün tüm okutmaları ve önerilen eşleşmeler (${records.length} kişi/gün)</summary>
    <p>Okutmalar kaynak satırı sırasındadır. Eşleşmeler yalnızca öneridir; eksik giriş veya çıkış “yok” gösterilir ve uydurulmaz.</p>
    <div class="pdks-timeline-scroll"><table><thead><tr><th>Tarih</th><th>Personel</th><th>Tüm okutmalar</th><th>Önerilen giriş–çıkış</th></tr></thead><tbody>${rows}</tbody></table></div>
  </details>`;
}

async function openPdks() {
  const month = extrasActiveMonth();
  const dialog = extrasDialog("pdks-dialog", `PDKS ↔ plan mutabakatı · ${extrasMonthLabel(month)}`,
    "Kart okuyucu dökümünü (Excel/CSV) planla karşılaştırır: gelmeyen, plansız çalışan, geç gelen ve erken çıkan.");
  dialog.querySelector(".extras-body").innerHTML = `<div class="forms-fields">
    <label class="forms-wide">PDKS dosyası (.xlsx / .csv) <input id="pdks-file" type="file" accept=".xlsx,.xlsm,.csv,.txt"></label>
    <label>Tolerans (dk) <input id="pdks-tolerance" type="number" min="0" max="120" value="5"></label>
  </div>
  <p class="controls-hint">Gerekli sütunlar: <b>Personel</b> (Adı Soyadı), <b>Tarih</b>, <b>Giriş</b> ve <b>Çıkış</b> — ya da tek bir <b>Saat</b> sütunu (ilk ve son okutma alınır). Gece vardiyasında çıkış saati ertesi sabah sayılır.</p>
  <div class="dialog-footer"><button class="button button-primary" type="button" id="pdks-import">Dosyayı oku ve karşılaştır</button>
  <button class="button button-light" type="button" id="pdks-refresh">Toleransla yeniden hesapla</button>
  <button class="button button-light" type="button" id="pdks-clear">Bu ayın PDKS verisini sil</button></div>
  <div id="pdks-result"></div>`;
  const tolerance = () => Math.max(0, Math.min(120, Number(dialog.querySelector("#pdks-tolerance").value) || 0));
  const draw = (data) => {
    const target = dialog.querySelector("#pdks-result");
    if (!data.imported) { target.innerHTML = '<p class="archive-empty">Bu ay için PDKS verisi yok. Dosya seçip okutun.</p>'; return; }
    const counts = Object.entries(PDKS_LABELS).map(([key, label]) => `<span class="controls-chip controls-${key}">${label}: <b>${data.counts[key]}</b></span>`).join("");
    const unknown = data.unknown_names.length
      ? `<p class="controls-warn">Personel listesinde bulunamayan isimler: ${data.unknown_names.map((item) => `${escapeHtml(item.name)} (${item.count})`).join(", ")}</p>` : "";
    const clockReview = data.clock_drift_review?.recommended
      ? `<p class="controls-warn">Birbirine yakın ${data.clock_drift_review.matching_records} kayıtta plan saatine göre benzer
        ${data.clock_drift_review.range_minutes[0]}–${data.clock_drift_review.range_minutes[1]} dakika sapma var.
        Cihaz saatini incelemeyi düşünün; bu tek başına saat hatasını doğrulamaz ve hiçbir saat otomatik düzeltilmedi.</p>`
      : "";
    target.innerHTML = `<p class="controls-meta">${escapeHtml(data.imported.file)} · ${controlsDate(data.imported.imported_at)} · ${data.records} kayıt · tolerans ${data.tolerance_minutes} dk</p>
      <div class="controls-chips">${counts}</div>${unknown}${clockReview}${pdksSourceHealthMarkup(data.source_health)}
      ${pdksTimelineMarkup(data.punch_records)}
      <h3 class="extras-subtitle">Kişi bazında dakika özeti</h3>
      ${controlsTable(["Personel", "Geç (dk)", "Geç (adet)", "Erken (dk)", "Erken (adet)", "Gelmedi", "Plansız", "Eksik okutma"],
        data.people.map((p) => [escapeHtml(p.name), p.late_minutes, p.late_count, p.early_minutes, p.early_count, p.no_show, p.unplanned, p.missing_punch]),
        "Karşılaştırılacak kişi yok.")}
      <h3 class="extras-subtitle">Farklar</h3>
      ${controlsTable(["Tarih", "Personel", "Tür", "Açıklama"],
        data.items.map((item) => [controlsDate(item.date), escapeHtml(item.name), `<span class="controls-chip controls-${item.type}">${PDKS_LABELS[item.type]}</span>`, escapeHtml(item.message)]),
        "Plan ile PDKS birebir uyuşuyor.")}`;
  };
  dialog.querySelector("#pdks-import").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    const file = dialog.querySelector("#pdks-file").files[0];
    if (!file) throw new Error("Önce PDKS dosyasını seçin.");
    if (file.size > 8_000_000) throw new Error("Dosya 8 MB'tan büyük olamaz.");
    draw(await request("/api/pdks/import", {
      method: "POST",
      body: JSON.stringify({ month, filename: file.name, content: await controlsFileBase64(file), tolerance: tolerance() }),
    }));
    toast("PDKS verisi okundu ve planla karşılaştırıldı.");
  }));
  dialog.querySelector("#pdks-refresh").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    draw(await request(`/api/pdks?month=${month}&tolerance=${tolerance()}`));
  }));
  dialog.querySelector("#pdks-clear").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    if (!window.confirm(`${extrasMonthLabel(month)} PDKS verisi silinsin mi?`)) return;
    draw(await request("/api/pdks/clear", { method: "POST", body: JSON.stringify({ month }) }));
  }));
  draw(await request(`/api/pdks?month=${month}&tolerance=5`));
  showExtras(dialog);
}

/* ---------- A2: Yayınlanan plan ↔ son plan farkı ---------- */

async function openPlanDiff() {
  const month = state.scheduleMonth;
  const dialog = extrasDialog("plan-diff-dialog", `Yayınlanan plan farkı · ${extrasMonthLabel(month)}`,
    "Personele duyurduğunuz planı “yayınla” ile saklayın; sonradan yapılan her değişiklik burada listelenir.");
  dialog.querySelector(".extras-body").innerHTML = `<div id="plan-diff-meta" class="controls-meta"></div>
    <div class="dialog-footer"><button class="button button-primary" type="button" id="plan-publish">Kayıtlı planı şimdi yayınla</button></div>
    <div id="plan-diff-list"></div>`;
  const labels = { added: "Eklendi", removed: "Silindi", changed: "Değişti" };
  let previewRevision = "";
  const draw = async () => {
    const data = await request(`/api/schedule/plan-diff?month=${month}`);
    previewRevision = data.revision;
    dialog.querySelector("#plan-diff-meta").textContent = data.published
      ? `Son yayın: ${controlsDate(data.published.at)}${data.published.actor ? ` · ${data.published.actor}` : ""} · ${data.changes.length} değişiklik`
      : "Bu ay için henüz plan yayınlanmadı.";
    dialog.querySelector("#plan-diff-list").innerHTML = controlsTable(
      ["Tarih", "Personel", "Bölüm", "Değişiklik", "Yayınlanan", "Şimdiki"],
      data.changes.map((item) => [controlsDate(item.date), escapeHtml(item.name),
        escapeHtml(item.section || ""),
        `<span class="controls-chip controls-${item.type}">${labels[item.type]}</span>`,
        escapeHtml(item.before || "—"), escapeHtml(item.after || "—")]),
      "Yayın ile kayıtlı plan aynı.");
  };
  dialog.querySelector("#plan-publish").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    if (state.scheduleDirty || state.attendanceDirty || state.neighborDirty?.size) throw new Error("Önce açık tablo değişikliklerini kaydedin.");
    if (!window.confirm(`${extrasMonthLabel(month)} planı yayınlansın mı? Önceki yayın bu kayıtla değiştirilir.`)) return;
    await request("/api/schedule/publish", { method: "POST", body: JSON.stringify({ month, revision: previewRevision }) });
    await draw();
    toast("Plan yayınlandı; sonraki değişiklikler fark listesinde görünecek.");
  }));
  await draw();
  showExtras(dialog);
}

/* ---------- A6: Puantaj onay akışı ---------- */

async function openApprovals() {
  const month = state.attendanceMonth || state.scheduleMonth;
  const dialog = extrasDialog("approval-dialog", `Puantaj onayı · ${extrasMonthLabel(month)}`,
    "Hazırlandı → Kontrol edildi → Onaylandı. Onaydan sonra vardiya veya puantaj değişirse onay “sonradan değişti” olarak işaretlenir.");
  const draw = (data) => {
    const body = dialog.querySelector(".extras-body");
    body.innerHTML = `<p class="controls-status ${data.stale ? "controls-stale" : data.current === "approved" ? "controls-ok" : ""}">Durum: <b>${escapeHtml(data.status)}</b></p>
      <ol class="controls-steps">${data.steps.map((step) => `<li class="${step.name ? "done" : ""}${step.step === data.next ? " next" : ""}">
        <span class="controls-step-label">${escapeHtml(step.label)}</span>
        <span class="controls-step-meta">${step.name ? `${escapeHtml(step.name)} · ${controlsDate(step.at)}` : "bekliyor"}</span></li>`).join("")}</ol>
      <div class="forms-fields"><label class="forms-wide">Ad soyad <input id="approval-name" maxlength="60" value="${escapeHtml(localStorage.getItem("vp-approver") || "")}"></label></div>
      <div class="dialog-footer">${data.next ? `<button class="button button-primary" type="button" data-approval-step="${data.next}">“${escapeHtml(data.steps.find((step) => step.step === data.next).label)}” olarak işaretle</button>` : ""}
      ${data.stale ? '<button class="button button-primary" type="button" data-approval-step="prepared">Yeniden hazırla</button>' : ""}
      ${data.current ? '<button class="button button-light" type="button" data-approval-step="reset">Onayları sıfırla</button>' : ""}</div>`;
    body.querySelectorAll("[data-approval-step]").forEach((button) => button.addEventListener("click", () => extrasRun(button, async () => {
      const name = body.querySelector("#approval-name").value.trim();
      const step = button.dataset.approvalStep;
      if (step === "reset" && !window.confirm("Bu ayın onay adımları sıfırlansın mı?")) return;
      if (step !== "reset") localStorage.setItem("vp-approver", name);
      draw(await request("/api/approvals", { method: "POST", body: JSON.stringify({ month, step, name }) }));
    })));
  };
  draw(await request(`/api/approvals?month=${month}`));
  showExtras(dialog);
}

/* ---------- B: İş Kanunu uygunluk ayarları ---------- */

async function openCompliance() {
  const dialog = extrasDialog("compliance-dialog", "Yasal uyum ayarları",
    "Kontrol raporunda kullanılan İş Kanunu kuralları. Özel durumlar (hamile, emziren, 18 yaş altı, engelli) personel kartından işaretlenir.");
  const data = await request("/api/compliance-settings");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = `<form class="forms-fields" id="compliance-form">
      <label>Vardiya arası en az dinlenme (saat) <input name="legal_rest_hours" type="number" min="0" max="24" step="0.5" value="${data.legal_rest_hours}"></label>
      <label>Üst üste en fazla gece haftası <input name="max_night_weeks" type="number" min="0" max="8" value="${data.max_night_weeks}"></label>
      <label>Denkleştirme dönemi <select name="balancing_weeks">${[[0, "Kapalı"], [2, "2 hafta"], [4, "4 hafta"], [8, "8 hafta (≈2 ay)"], [12, "12 hafta"], [16, "16 hafta (≈4 ay)"]]
        .map(([value, label]) => `<option value="${value}" ${value === data.balancing_weeks ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      <div class="forms-wide dialog-footer"><button class="button button-primary" type="submit">Kaydet</button></div>
    </form>
    <ul class="controls-hint">
      <li>Hafta tatili: her 7 günde en az 24 saat kesintisiz dinlenme (md. 46).</li>
      <li>Gece çalışması 7,5 saati aşarsa uyarı verilir. Yukarıdaki alanlara 0 yazılırsa o kural kapanır.</li>
      <li>Resmî tatil (UBGT) saatleri bütün resmî/dinî tatillerde, arifelerde 13:00'ten sonra ayrıca hesaplanır.</li>
      <li>18 yaş altı doğum tarihinden otomatik tanınır.</li>
    </ul>`;
  body.querySelector("#compliance-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    extrasRun(event.currentTarget.querySelector("button"), async () => {
      await request("/api/compliance-settings", {
        method: "PUT",
        body: JSON.stringify(Object.fromEntries([...form.entries()].map(([key, value]) => [key, Number(value)]))),
      });
      toast("Yasal uyum ayarları kaydedildi.");
    });
  });
  showExtras(dialog);
}

/* ---------- C: Bordro ve SGK ---------- */

function controlsNumber(value) {
  return Number(value || 0).toLocaleString("tr-TR", { maximumFractionDigits: 2 });
}

function controlsPeopleOptions(selected = "") {
  return state.people.filter((person) => person.active || String(person.id) === String(selected))
    .sort((a, b) => a.name.localeCompare(b.name, "tr"))
    .map((person) => `<option value="${person.id}" ${String(person.id) === String(selected) ? "selected" : ""}>${escapeHtml(person.name)}</option>`).join("");
}

async function openPayroll() {
  const month = state.attendanceMonth || state.scheduleMonth;
  const dialog = extrasDialog("payroll-dialog", `SGK eksik gün ve mesai · ${extrasMonthLabel(month)}`,
    "Kayıtlı puantajdan prim günü, eksik gün nedeni ve fazla mesai türlerini hesaplar; bordro programına aktarım dosyası üretir.");
  const data = await request(`/api/payroll/sgk?month=${month}`);
  const overtime = Object.fromEntries(data.overtime.map((row) => [row.employee_id, row]));
  const reasons = (row) => Object.entries(row.missing_by_reason).map(([code, days]) => `${code}: ${days}`).join(", ");
  dialog.querySelector(".extras-body").innerHTML = `
    <div class="dialog-footer controls-export">
      <span>Bordro programına aktar:</span>
      ${["logo", "mikro", "netsis"].map((format) => `<button class="button button-light" type="button" data-payroll-format="${format}">${format === "netsis" ? "Netsis (CSV)" : `${format[0].toUpperCase()}${format.slice(1)} (Excel)`}</button>`).join("")}
    </div>
    <p class="controls-hint">Fazla mesai puantajla aynıdır: her gün ${controlsNumber(data.daily_limit_hours)} saati aşan süre (mola ve resmî tatil saatleri düşülür); puantajda elle düzeltilen FM esas alınır. Sicil no, işe giriş ve çıkış tarihleri personel kartından alınır.</p>
    ${controlsTable(["Personel", "Sicil", "Çalıştığı dönem", "SGK gün", "Eksik gün", "Neden kodu", "Çalışma (sa)", "Fazla mesai (sa)", "UBGT (sa)", "Gece (sa)", "Boş gün"],
      data.sgk.map((row) => {
        const extra = overtime[row.employee_id] || {};
        return [escapeHtml(row.name), escapeHtml(row.payroll_code || "—"),
          row.employed_from ? (row.full_month ? "Tüm ay" : `${controlsDate(row.employed_from)} – ${controlsDate(row.employed_to)}`) : "Çalışmadı",
          `<b>${row.sgk_days}</b>`, row.missing_days,
          row.missing_code ? `<span title="${escapeHtml(reasons(row))}">${row.missing_code} · ${escapeHtml(row.missing_label)}</span>` : "—",
          controlsNumber(extra.worked_hours), `${controlsNumber(extra.overtime_hours)}${extra.overtime_manual ? ' <small title="Puantajda elle düzeltildi">✎</small>' : ""}`, controlsNumber(extra.ubgt_hours), controlsNumber(extra.night_hours),
          row.empty_days.length ? `<span class="controls-warn-text" title="${row.empty_days.map(controlsDate).join(", ")}">${row.empty_days.length}</span>` : "0"];
      }), "Bu ay puantajda personel yok.")}
    <p class="controls-hint">Eksik gün kodları: 01 İstirahat (R), 15 Devamsızlık (G), 21 Ücretsiz izin (Ü), 12 Birden fazla neden. “Boş gün” sütunu, kodu girilmemiş günleri gösterir — bildirgeden önce tamamlayın.</p>`;
  dialog.querySelectorAll("[data-payroll-format]").forEach((button) => button.addEventListener("click", () => extrasRun(button, async () => {
    const format = button.dataset.payrollFormat;
    const extension = format === "netsis" ? "csv" : "xlsx";
    await saveReportFile(`/api/reports/payroll-program?month=${month}&format=${format}`, extension, `${format.toUpperCase()}_PUANTAJ_${month}.${extension}`);
  })));
  showExtras(dialog);
}

async function openSickReports() {
  const month = state.attendanceMonth || state.scheduleMonth;
  const dialog = extrasDialog("sick-report-dialog", `Rapor (R) belgeleri · ${extrasMonthLabel(month)}`,
    "E-rapor numarası ve tarih aralığını kaydedin; puantajdaki R günleriyle karşılaştırılır, SGK'nın ödediği günler ayrılır.");
  const body = dialog.querySelector(".extras-body");
  const draw = (data) => {
    body.querySelector("#sick-list").innerHTML = `
      <h3 class="extras-subtitle">Bu aya düşen raporlar</h3>
      ${controlsTable(["Personel", "Tür", "Rapor no", "Tarih aralığı", "Bu ay", "İşveren", "SGK öder", ""],
        data.reports.map((item) => [escapeHtml(item.name), escapeHtml(item.type_label), escapeHtml(item.report_no || "—"),
          `${controlsDate(item.start)} – ${controlsDate(item.end)}`, item.days_in_month, item.employer_days, `<b>${item.sgk_paid_days}</b>`,
          `<button class="button button-light" type="button" data-sick-delete="${escapeHtml(item.id)}">Sil</button>`]),
        "Bu ay için rapor kaydı yok.")}
      <h3 class="extras-subtitle">Puantaj ile uyuşmayan günler</h3>
      ${controlsTable(["Tarih", "Personel", "Açıklama"],
        data.mismatches.map((item) => [controlsDate(item.date), escapeHtml(item.name), escapeHtml(item.message)]),
        "Rapor kayıtları ile R günleri uyuşuyor.")}`;
    body.querySelectorAll("[data-sick-delete]").forEach((button) => button.addEventListener("click", () => extrasRun(button, async () => {
      if (!window.confirm("Rapor kaydı silinsin mi?")) return;
      await request("/api/sick-reports", { method: "POST", body: JSON.stringify({ action: "delete", id: button.dataset.sickDelete }) });
      draw(await request(`/api/sick-reports?month=${month}`));
    })));
  };
  const first = `${month}-01`;
  body.innerHTML = `<form class="forms-fields" id="sick-form">
      <label>Personel <select name="employee_id" required><option value="">Seçin</option>${controlsPeopleOptions()}</select></label>
      <label>Rapor türü <select name="type">
        <option value="illness">Hastalık</option><option value="work_accident">İş kazası</option>
        <option value="occupational">Meslek hastalığı</option><option value="maternity">Analık</option></select></label>
      <label>Başlangıç <input name="start" type="date" required value="${first}"></label>
      <label>Bitiş <input name="end" type="date" required value="${first}"></label>
      <label>E-rapor no <input name="report_no" maxlength="40"></label>
      <label>Veren kurum / hekim <input name="issuer" maxlength="80"></label>
      <label class="forms-wide">Not <input name="notes" maxlength="200"></label>
      <div class="forms-wide dialog-footer"><button class="button button-primary" type="submit">Raporu kaydet</button></div>
    </form>
    <p class="controls-hint">Hastalık raporunda ilk 2 gün işverene aittir, SGK 3. günden itibaren öder. İş kazası, meslek hastalığı ve analıkta SGK ilk günden öder. Puantajdaki R kodları bu kayıtlardan otomatik değişmez.</p>
    <div id="sick-list"></div>`;
  body.querySelector("#sick-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    extrasRun(event.currentTarget.querySelector("button"), async () => {
      const report = Object.fromEntries(form.entries());
      report.employee_id = Number(report.employee_id);
      await request("/api/sick-reports", { method: "POST", body: JSON.stringify({ action: "add", report }) });
      draw(await request(`/api/sick-reports?month=${month}`));
      toast("Rapor kaydedildi.");
    });
  });
  draw(await request(`/api/sick-reports?month=${month}`));
  showExtras(dialog);
}

async function openLeaveCarry() {
  const month = state.attendanceMonth || state.scheduleMonth;
  const year = Number(month.slice(0, 4));
  const dialog = extrasDialog("leave-carry-dialog", "Yıllık izin devri ve ücreti",
    "Yıl sonunda kalan izinleri yeni yılın açılış bakiyesine taşır; işten çıkışta kullanılmayan izin ücretini hesaplar.");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = `<h3 class="extras-subtitle">Yıl sonu devri</h3>
    <div class="forms-fields">
      <label>Devredilecek yıl <input id="carry-year" type="number" min="2000" max="2098" value="${year - 1}"></label>
      <label class="forms-check"><input id="carry-entitlement" type="checkbox" checked> Yeni yılın kıdem hakkını da ekle</label>
    </div>
    <div class="dialog-footer"><button class="button button-primary" type="button" id="carry-run">Kalan izinleri devret</button></div>
    <div id="carry-result"></div>
    <p class="controls-hint">Yalnızca açılış bakiyesi girilmiş personel devredilir; yeni yılda zaten açılışı olan kişilere dokunulmaz.</p>
    <h3 class="extras-subtitle">İşten çıkışta izin ücreti</h3>
    <div class="forms-fields">
      <label>Personel <select id="term-person"><option value="">Seçin</option>${controlsPeopleOptions()}</select></label>
      <label>Çıkış tarihi <input id="term-date" type="date" value="${new Date().toISOString().slice(0, 10)}"></label>
    </div>
    <div class="dialog-footer"><button class="button button-light" type="button" id="term-run">Hesapla</button></div>
    <div id="term-result"></div>`;
  body.querySelector("#carry-run").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    const target = Number(body.querySelector("#carry-year").value);
    if (!window.confirm(`${target} yılından kalan izinler ${target + 1} açılış bakiyesine yazılsın mı?`)) return;
    const data = await request("/api/leave/carry-over", {
      method: "POST", body: JSON.stringify({ year: target, include_entitlement: body.querySelector("#carry-entitlement").checked }),
    });
    body.querySelector("#carry-result").innerHTML = controlsTable(["Personel", "Devreden", "Yeni hak", `${data.year} açılış`],
      data.done.map((item) => [escapeHtml(item.name), controlsNumber(item.carried), controlsNumber(item.earned), `<b>${controlsNumber(item.opening)}</b>`]),
      "Devredilecek kişi yok.") + (data.skipped.length ? `<p class="controls-hint">Atlananlar: ${data.skipped.map(escapeHtml).join(", ")}</p>` : "");
    toast(`${data.done.length} kişinin izni ${data.year} yılına devredildi.`);
  }));
  body.querySelector("#term-run").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    const person = body.querySelector("#term-person").value;
    if (!person) throw new Error("Personel seçin.");
    const data = await request(`/api/leave/termination?employee_id=${person}&exit_date=${body.querySelector("#term-date").value}`);
    body.querySelector("#term-result").innerHTML = `<p class="controls-status">${escapeHtml(data.name)}: kalan <b>${controlsNumber(data.remaining_days)} gün</b> ×
      günlük ${controlsNumber(data.daily_wage)} ₺ = <b>${controlsNumber(data.amount)} ₺</b> (brüt)</p>
      ${data.monthly_wage ? "" : '<p class="controls-warn">Personel kartında aylık ücret girilmemiş.</p>'}
      ${data.has_opening ? "" : '<p class="controls-warn">Bu yıl için izin açılış bakiyesi girilmemiş; kalan gün eksik olabilir.</p>'}`;
  }));
  showExtras(dialog);
}

/* ---------- D: Planlama kalitesi ---------- */


function controlsMonthShift(month, delta) {
  const [year, value] = month.split("-").map(Number);
  const date = new Date(year, value - 1 + delta, 1);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

async function openFairness() {
  const month = state.scheduleMonth;
  const dialog = extrasDialog("fairness-dialog", "Adil dağılım raporu",
    "Gece, hafta sonu ve bayram vardiyalarının kişiler arasında ne kadar dengeli paylaşıldığını gösterir.");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = `<div class="forms-fields">
      <label>Başlangıç ayı <input id="fair-from" type="month" value="${controlsMonthShift(month, -2)}"></label>
      <label>Bitiş ayı <input id="fair-to" type="month" value="${month}"></label>
    </div>
    <div class="dialog-footer"><button class="button button-primary" type="button" id="fair-run">Hesapla</button></div>
    <div id="fair-result"></div>`;
  const run = async () => {
    const data = await request(`/api/planning/fairness?from=${body.querySelector("#fair-from").value}&to=${body.querySelector("#fair-to").value}`);
    const avg = data.averages;
    body.querySelector("#fair-result").innerHTML = `<p class="controls-meta">${data.months.map(extrasMonthLabel).join(", ")} · ekip ortalaması: ${controlsNumber(avg.shifts)} vardiya, ${controlsNumber(avg.hours)} saat, ${controlsNumber(avg.night)} gece, ${controlsNumber(avg.weekend)} hafta sonu, ${controlsNumber(avg.holiday)} bayram</p>
      ${controlsTable(["Personel", "Vardiya", "Saat", "Gece", "Hafta sonu", "Bayram", "Kayıtlı uygunluk bağlamı"],
        data.people.map((row) => {
          const periods = row.availability.periods.map((period) =>
            `${extrasMonthLabel(period.month)}: ${period.unrestricted_start_days} engel görünmeyen gün · gece ${period.night_start_days} · hafta sonu ${period.weekend_start_days} · tatil ${period.holiday_start_days}`,
          ).join("<br>");
          const restrictions = [
            row.availability.unavailable_weekdays.length
              ? `Uygun değil olarak kayıtlı: ${row.availability.unavailable_weekdays.join(", ")}`
              : "",
            row.availability.unavailable_ranges.map((item) =>
              `${item.start}–${item.end} uygun değil`,
            ).join(", "),
            row.availability.no_night_preference ? "Gece tercihi: yok" : "",
            row.availability.no_weekend_preference ? "Hafta sonu tercihi: yok" : "",
          ].filter(Boolean).join(" · ") || "Bu kapsamda kayıtlı uygunluk kısıtı yok.";
          return [escapeHtml(row.name), row.shifts, controlsNumber(row.hours), row.night, row.weekend, row.holiday,
            `<span>${periods}</span><small>${escapeHtml(restrictions)}</small>`];
        }), "Seçilen aylarda vardiya yok.")}
      <p class="controls-hint">Gece: 20:00–06:00 arasında en az 3 saat; hafta sonu: Cumartesi/Pazar başlayan vardiya. “Engel görünmeyen gün” personel kartındaki kayıtlı tercih ve görev tarihlerine göre hesaplanır; gerçek müsaitlik teyidi değildir. Dağılım tek puanla sıralanmaz.</p>`;
  };
  body.querySelector("#fair-run").addEventListener("click", (event) => extrasRun(event.currentTarget, run));
  await run();
  showExtras(dialog);
}

async function openAttendanceImport() {
  const month = state.attendanceMonth || state.scheduleMonth;
  const dialog = extrasDialog("attendance-import-dialog", `Geçmiş puantaj Excel'i içe aktar · ${extrasMonthLabel(month)}`,
    "Elle doldurduğunuz eski puantaj dosyasındaki kodları (X, H, S, R, Ü, G.Ç …) bu ayın puantajına aktarır.");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = `<div class="forms-fields">
      <label class="forms-wide">Puantaj dosyası (.xlsx) <input id="att-import-file" type="file" accept=".xlsx,.xlsm"></label>
    </div>
    <p class="controls-hint">Dosyada gün numaralarının (1, 2, 3 …) yazılı olduğu bir başlık satırı ve personel adlarının olduğu bir sütun aranır. Önce önizleme yapılır; onaylamadan puantaj değişmez. Yalnız seçilen kodlar aktarılır; boş hücreler mevcut kodları korur.</p>
    <div class="dialog-footer"><button class="button button-primary" type="button" id="att-import-preview">Önizle</button>
      <button class="button button-outline" type="button" id="att-import-apply" disabled>Puantaja aktar</button></div>
    <div id="att-import-code-filter"></div>
    <button class="button button-light" type="button" id="att-import-filter-preview" disabled>Seçilen kodlarla yeniden önizle</button>
    <p id="att-import-filter-status" class="controls-hint" role="status"></p>
    <div id="att-import-result"></div>`;
  let payload = null;
  let preview = null;
  let includedCodes = null;
  const draw = (data) => {
    const codes = data.available_codes || [];
    const checkedCodes = includedCodes === null ? codes : includedCodes.filter((code) => codes.includes(code));
    body.querySelector("#att-import-code-filter").innerHTML = codes.length
      ? `<fieldset><legend>Aktarılacak kodlar</legend><p class="controls-hint">İlk önizlemede tüm kodlar seçilidir. İstediğiniz kodları bırakıp yeniden önizleyin.</p>
          <div class="forms-fields">${codes.map((code) => `<label><input type="checkbox" data-att-import-code value="${escapeHtml(code)}"
            ${checkedCodes.includes(code) ? "checked" : ""}> ${escapeHtml(code)}</label>`).join("")}</div></fieldset>`
      : "<p>Dosyada tanınan kod bulunamadı.</p>";
    body.querySelector("#att-import-result").innerHTML = `<p class="controls-meta">Sayfa “${escapeHtml(data.sheet)}” · başlık satırı ${data.header_row} · ${data.code_count} kod · ${data.matched.length} kişi eşleşti${data.applied ? " · <b>aktarıldı</b>" : ""}</p>
      ${controlsTable(["Personel", "Kod sayısı"], data.matched.map((item) => [escapeHtml(item.name), item.codes]), "Eşleşen personel yok.")}
      ${data.unknown.length ? `<p class="controls-warn">Personel listesinde olmayan isimler (aktarılmaz): ${data.unknown.map((item) => `${escapeHtml(item.name)} (${item.codes})`).join(", ")}</p>` : ""}
      ${data.invalid.length ? `<p class="controls-warn">Tanınmayan hücreler: ${data.invalid.map((item) => `${escapeHtml(item.name)} ${item.day}. gün “${escapeHtml(item.value)}”`).join(", ")}</p>` : ""}`;
    body.querySelector("#att-import-filter-preview").disabled = !codes.length;
    body.querySelector("#att-import-filter-status").textContent = data.filtered_out_count
      ? `${data.filtered_out_count} farklı kod seçiminiz dışında bırakıldı.`
      : "";
  };
  body.querySelector("#att-import-file").addEventListener("change", () => {
    payload = null;
    preview = null;
    includedCodes = null;
    body.querySelector("#att-import-apply").disabled = true;
    body.querySelector("#att-import-filter-preview").disabled = true;
    body.querySelector("#att-import-code-filter").innerHTML = "";
    body.querySelector("#att-import-result").innerHTML = "";
  });
  body.querySelector("#att-import-preview").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    const file = body.querySelector("#att-import-file").files[0];
    if (!file) throw new Error("Önce puantaj dosyasını seçin.");
    if (file.size > 8_000_000) throw new Error("Dosya 8 MB'tan büyük olamaz.");
    payload = { month, filename: file.name, content: await controlsFileBase64(file) };
    preview = await request("/api/import/attendance-excel", { method: "POST", body: JSON.stringify({ ...payload, apply: false }) });
    includedCodes = null;
    draw(preview);
    body.querySelector("#att-import-apply").disabled = !preview.code_count;
  }));
  body.querySelector("#att-import-code-filter").addEventListener("change", (event) => {
    if (!event.target.matches("[data-att-import-code]")) return;
    preview = null;
    body.querySelector("#att-import-apply").disabled = true;
    body.querySelector("#att-import-filter-preview").disabled =
      !body.querySelector("[data-att-import-code]:checked");
    body.querySelector("#att-import-filter-status").textContent = "Kod seçimi değişti; aktarım için yeniden önizleyin.";
  });
  body.querySelector("#att-import-filter-preview").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    if (!payload) throw new Error("Önce dosyayı seçip önizleyin.");
    includedCodes = [...body.querySelectorAll("[data-att-import-code]:checked")].map((input) => input.value);
    if (!includedCodes.length) throw new Error("En az bir puantaj kodu seçin.");
    preview = await request("/api/import/attendance-excel", {
      method: "POST",
      body: JSON.stringify({ ...payload, apply: false, include_codes: includedCodes }),
    });
    draw(preview);
    body.querySelector("#att-import-apply").disabled = !preview.code_count;
  }));
  body.querySelector("#att-import-apply").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    if (!payload || !preview) throw new Error("Önce önizleme yapın.");
    if (state.attendanceDirty) throw new Error("Puantajda kaydedilmemiş değişiklik var; önce kaydedin.");
    if (!window.confirm(`${preview.code_count} kod ${extrasMonthLabel(month)} puantajına aktarılsın mı?`)) return;
    const result = await request("/api/import/attendance-excel", {
      method: "POST", body: JSON.stringify({
        ...payload, apply: true, revision: preview.revision,
        ...(includedCodes === null ? {} : { include_codes: includedCodes }),
      }),
    });
    draw(result);
    event.currentTarget.disabled = true;
    if (state.attendanceMonth === month && typeof loadAttendance === "function") await loadAttendance();
    toast(`${result.code_count} puantaj kodu aktarıldı.`);
  }));
  showExtras(dialog);
}

async function openPlanningRules() {
  const dialog = extrasDialog("planning-rules-dialog", "Yetkinlik ve izin kuralları",
    "Her vardiyada belirli bir yetkinliğe sahip en az bir kişi bulunmasını ve bir güne düşebilecek en fazla izin talebini tanımlayın.");
  const body = dialog.querySelector(".extras-body");
  const data = await request("/api/planning-settings");
  let rules = data.skill_requirements.map((rule) => ({ ...rule }));
  const sectionOptions = (selected) => `<option value="">Tüm bölümler</option>${data.known_sections.concat(
    selected && !data.known_sections.includes(selected) ? [selected] : []).map((section) =>
    `<option value="${escapeHtml(section)}" ${section === selected ? "selected" : ""}>${escapeHtml(section)}</option>`).join("")}`;
  body.innerHTML = `<datalist id="planning-skill-list">${data.known_skills.map((skill) => `<option value="${escapeHtml(skill)}">`).join("")}</datalist>
    <h3 class="extras-subtitle">Yetkinlik kuralları</h3>
    <p class="controls-hint">Örnek: “Kasa” yetkisi olan biri her gün ÖSSE bölümünde çalışmalı. Yetkinlikler personel kartından girilir${data.known_skills.length ? ` (kayıtlı: ${data.known_skills.map(escapeHtml).join(", ")})` : ""}.</p>
    <div id="planning-rules"></div>
    <div class="dialog-footer"><button class="button button-light" type="button" id="planning-add">Kural ekle</button></div>
    <h3 class="extras-subtitle">İzin talepleri</h3>
    <div class="forms-fields"><label>Aynı güne en fazla izin talebi <input id="planning-limit" type="number" min="0" max="50" value="${data.daily_limit}"></label></div>
    <p class="controls-hint">0 yazarsanız sınır kontrolü kapanır. Reddedilen talepler sayılmaz.</p>
    <div class="dialog-footer"><button class="button button-primary" type="button" id="planning-save">Kuralları kaydet</button></div>`;
  const list = body.querySelector("#planning-rules");
  const draw = () => {
    list.innerHTML = rules.length ? rules.map((rule, index) => `<div class="forms-fields planning-rule" data-index="${index}">
        <label>Yetkinlik <input data-field="skill" list="planning-skill-list" maxlength="40" value="${escapeHtml(rule.skill)}"></label>
        <label>Bölüm <select data-field="section">${sectionOptions(rule.section)}</select></label>
        <div class="dialog-footer"><button class="button button-light" type="button" data-remove="${index}">Kaldır</button></div>
      </div>`).join("") : '<p class="archive-empty">Henüz yetkinlik kuralı yok.</p>';
  };
  list.addEventListener("input", (event) => {
    const row = event.target.closest(".planning-rule");
    if (row && event.target.dataset.field) rules[Number(row.dataset.index)][event.target.dataset.field] = event.target.value;
  });
  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove]");
    if (button) { rules.splice(Number(button.dataset.remove), 1); draw(); }
  });
  body.querySelector("#planning-add").addEventListener("click", () => { rules.push({ skill: "", section: "" }); draw(); });
  body.querySelector("#planning-save").addEventListener("click", (event) => extrasRun(event.currentTarget, async () => {
    const saved = await request("/api/planning-settings", {
      method: "PUT",
      body: JSON.stringify({ daily_limit: Number(body.querySelector("#planning-limit").value || 0),
        skill_requirements: rules.filter((rule) => rule.skill.trim()) }),
    });
    rules = saved.skill_requirements.map((rule) => ({ ...rule }));
    draw();
    toast("Planlama kuralları kaydedildi; dönem kontrolünde uyarı olarak görünecek.");
  }));
  draw();
  showExtras(dialog);
}

async function openLeaveRequests() {
  let month = state.scheduleMonth;
  const dialog = extrasDialog("leave-requests-dialog", "İzin talep takvimi",
    "Personelin izin isteklerini kaydedin, onaylayın; aynı güne çok talep düşerse ve onaylı izin gününe vardiya yazılırsa uyarılır.");
  const body = dialog.querySelector(".extras-body");
  body.innerHTML = `<form class="forms-fields" id="leave-request-form">
      <label>Personel <select name="employee_id" required><option value="">Seçin</option>${controlsPeopleOptions()}</select></label>
      <label>İzin türü <select name="code"><option value="S">Yıllık izin</option><option value="Ü">Ücretsiz izin</option><option value="E">Evlilik izni</option><option value="H">Hafta tatili isteği</option></select></label>
      <label>Başlangıç <input name="start" type="date" required value="${month}-01"></label>
      <label>Bitiş <input name="end" type="date" required value="${month}-01"></label>
      <label>Durum <select name="status"><option value="pending">Bekliyor</option><option value="approved">Onaylandı</option></select></label>
      <label>Not <input name="note" maxlength="120"></label>
      <div class="forms-wide dialog-footer"><button class="button button-primary" type="submit">Talebi kaydet</button></div>
    </form>
    <div class="dialog-footer controls-export"><button class="button button-light" type="button" data-leave-month="-1">‹ Önceki ay</button>
      <b id="leave-month-label"></b><button class="button button-light" type="button" data-leave-month="1">Sonraki ay ›</button></div>
    <div id="leave-calendar"></div><div id="leave-list"></div>`;
  const draw = async () => {
    const data = await request(`/api/leave-requests?month=${month}`);
    body.querySelector("#leave-month-label").textContent = `${extrasMonthLabel(month)} · günlük sınır ${data.daily_limit || "yok"}`;
    const offset = (new Date(`${month}-01T12:00:00`).getDay() + 6) % 7;
    body.querySelector("#leave-calendar").innerHTML = `<div class="leave-calendar">${PLANNING_CARD_DAYS.map((day) => `<span class="leave-calendar-head">${day}</span>`).join("")}
      ${"<span></span>".repeat(offset)}${data.days.map((day) => `<span class="leave-calendar-day${day.over ? " is-over" : ""}${day.count ? " has-leave" : ""}" title="${escapeHtml(day.people.map((person) => `${person.name} (${person.status === "approved" ? "onaylı" : "bekliyor"})`).join("\n"))}">
        <b>${Number(day.date.slice(8))}</b>${day.count ? `<small>${day.count} kişi</small>` : ""}</span>`).join("")}</div>`;
    body.querySelector("#leave-list").innerHTML = controlsTable(["Personel", "Tür", "Tarih aralığı", "Durum", "Not", ""],
      data.requests.map((item) => [escapeHtml(item.name), escapeHtml(item.code_label), `${controlsDate(item.start)} – ${controlsDate(item.end)}`,
        `<select data-leave-status="${escapeHtml(item.id)}">${Object.entries(data.statuses).map(([key, label]) => `<option value="${key}" ${key === item.status ? "selected" : ""}>${label}</option>`).join("")}</select>`,
        escapeHtml(item.note || ""), `<button class="button button-light" type="button" data-leave-delete="${escapeHtml(item.id)}">Sil</button>`]),
      "Bu ay için izin talebi yok.");
  };
  body.addEventListener("change", (event) => {
    const select = event.target.closest("[data-leave-status]");
    if (!select) return;
    extrasRun(select, async () => {
      await request("/api/leave-requests", { method: "POST", body: JSON.stringify({ action: "status", id: select.dataset.leaveStatus, status: select.value }) });
      await draw();
    });
  });
  body.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-leave-delete]");
    const move = event.target.closest("[data-leave-month]");
    if (remove) extrasRun(remove, async () => {
      if (!window.confirm("İzin talebi silinsin mi?")) return;
      await request("/api/leave-requests", { method: "POST", body: JSON.stringify({ action: "delete", id: remove.dataset.leaveDelete }) });
      await draw();
    });
    if (move) extrasRun(move, async () => { month = controlsMonthShift(month, Number(move.dataset.leaveMonth)); await draw(); });
  });
  body.querySelector("#leave-request-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    extrasRun(event.currentTarget.querySelector("button"), async () => {
      const item = Object.fromEntries(form.entries());
      item.employee_id = Number(item.employee_id);
      await request("/api/leave-requests", { method: "POST", body: JSON.stringify({ action: "add", request: item }) });
      month = item.start.slice(0, 7);
      await draw();
      toast("İzin talebi kaydedildi.");
    });
  });
  await draw();
  showExtras(dialog);
}

async function openBorrowed() {
  const month = state.scheduleMonth;
  const dialog = extrasDialog("borrowed-dialog", `Bölümler arası ödünç personel · ${extrasMonthLabel(month)}`,
    "Kendi bölümü dışında çalıştırılan personeli ve bu günlerin saatlerini listeler.");
  const data = await request(`/api/planning/borrowed?month=${month}`);
  dialog.querySelector(".extras-body").innerHTML = `${controlsTable(["Personel", "Kendi bölümü", "Başka bölümde", "Gün", "Saat"],
      data.people.map((row) => [escapeHtml(row.name),
        `${escapeHtml(row.home_section)}${row.home_declared ? "" : ' <small title="Personel kartında bölüm girilmediği için en çok çalıştığı bölüm alındı">(tahmini)</small>'}`,
        row.sections.filter((item) => !item.home).map((item) => `${escapeHtml(item.section)}: ${item.dates.map((day) => Number(day.slice(8))).join(", ")}`).join("<br>"),
        row.borrowed_days, controlsNumber(row.borrowed_hours)]),
      "Bu ay herkes kendi bölümünde çalışıyor.")}
    <p class="controls-hint">Kendi bölümü personel kartındaki “Asıl bölüm” alanından alınır; boşsa ay içinde en çok çalıştığı bölüm sayılır.</p>`;
  showExtras(dialog);
}

function analyticsDelta(value, unit = "", lowerIsBetter = true) {
  if (value === undefined || value === null) return '<span class="analytics-delta">—</span>';
  if (Math.abs(value) < 0.005) return '<span class="analytics-delta">değişmedi</span>';
  const worse = lowerIsBetter ? value > 0 : value < 0;
  return `<span class="analytics-delta ${worse ? "is-worse" : "is-better"}">${value > 0 ? "+" : "−"}${controlsNumber(Math.abs(value))}${unit}</span>`;
}

async function openManagerSummary() {
  let month = state.attendanceMonth || state.scheduleMonth;
  const dialog = extrasDialog("manager-summary-dialog", "Yönetici özeti",
    "Ayın devamsızlık, fazla mesai, uyarı ve tahmini maliyet göstergeleri; önceki ay ve geçen yılın aynı ayıyla karşılaştırma.");
  const body = dialog.querySelector(".extras-body");
  const draw = async () => {
    const data = await request(`/api/analytics?month=${month}`);
    const m = data.metrics;
    const prev = data.compare_previous;
    const year = data.compare_last_year;
    const tile = (label, value, delta) => `<div class="analytics-tile"><span>${label}</span><b>${value}</b>${delta || ""}</div>`;
    const column = (metrics, key) => metrics && metrics.people ? controlsNumber(metrics[key]) : "—";
    const hours = [["Çalışma saati", m.worked_hours, "worked_hours"], ["Fazla mesai", m.overtime_hours, "overtime_hours"],
      ["UBGT mesaisi", m.ubgt_hours, "ubgt_hours"]];
    const days = [["Rapor", m.sick_days, "sick_days"], ["Devamsızlık", m.absence_days, "absence_days"],
      ["Ücretsiz izin", m.unpaid_days, "unpaid_days"], ["Yıllık izin", m.annual_leave_days, "annual_leave_days"]];
    const barRows = (items) => {
      const maximum = Math.max(1, ...items.map(([, value]) => Number(value) || 0));
      return items.map(([label, value, metric]) => {
        const numeric = Math.max(0, Number(value) || 0);
        const width = Math.min(100, numeric / maximum * 100);
        const note = data.chart_notes?.[metric];
        const editorId = `manager-chart-note-${metric}`;
        return `<div class="analytics-chart-row"><span>${label}</span><span class="analytics-chart-track" aria-hidden="true">
          <span style="width:${width}%"></span></span><b>${controlsNumber(numeric)}</b>
          <button class="button button-light analytics-detail-button" type="button" data-analytics-metric="${metric}"
            aria-label="${label} kayıtlarını göster">Kayıtlar</button>
          <button class="button button-light analytics-detail-button" type="button" data-chart-note-toggle="${metric}"
            aria-expanded="false" aria-controls="${editorId}">${note ? "Açıklamayı düzenle" : "Açıklama ekle"}</button></div>
          <div class="analytics-chart-note" data-chart-note="${metric}">
            ${note ? `<p class="analytics-chart-note-saved"><strong>Grafik açıklaması · ${escapeHtml(note.updated_at)}</strong><br>${escapeHtml(note.text)}</p>` : ""}
            <div class="analytics-chart-note-editor" id="${editorId}" hidden>
              <label><span>${label} grafiği için kısa açıklama</span>
                <textarea data-chart-note-input="${metric}" rows="2" maxlength="280" required>${note ? escapeHtml(note.text) : ""}</textarea>
              </label>
              <p>Bu açıklama yalnızca rapor sürümü <code>${escapeHtml(data.report_context.report_version)}</code> için saklanır.</p>
              <button class="button button-primary" type="button" data-chart-note-save="${metric}">Açıklamayı kaydet</button>
            </div>
          </div>`;
      }).join("");
    };
    const rows = [["Dönem uzunluğu (takvim günü)", "calendar_days"], ["Normal iş günü (takvim)", "normal_weekdays"],
      ["Kod girilen personel", "people"], ["Çalışılan kişi-gün (X)", "work_days"], ["Toplam çalışma (sa)", "worked_hours"],
      ["Fazla mesai (sa)", "overtime_hours"], ["UBGT (sa)", "ubgt_hours"],
      ["Rapor günü (R)", "sick_days"], ["Devamsız gün (G)", "absence_days"], ["Ücretsiz izin (Ü)", "unpaid_days"],
      ["Yıllık izin (S)", "annual_leave_days"], ["Devamsızlık oranı %", "absence_rate"]];
    const flagged = data.patterns.filter((item) => item.flagged);
    body.innerHTML = `
      <div class="analytics-toolbar">
        <button class="button button-light" type="button" data-month-move="-1">‹ Önceki ay</button>
        <strong>${extrasMonthLabel(month)}</strong>
        <button class="button button-light" type="button" data-month-move="1">Sonraki ay ›</button>
        <span class="analytics-spacer"></span>
        <button class="button button-primary" type="button" data-summary-pdf>Tek sayfa PDF al</button>
      </div>
      <section class="analytics-freshness" aria-label="Rapor güncelliği ve kaynak kapsamı">
        <p role="status">Hesaplandı: ${escapeHtml(new Date(data.report_context.calculated_at).toLocaleString("tr-TR"))}
          · Plan sürümü ${escapeHtml(data.report_context.source_revisions[month]?.schedule || "kayıt yok")}
          · Puantaj sürümü ${escapeHtml(data.report_context.source_revisions[month]?.attendance || "kayıt yok")}
          · Rapor sürümü <code>${escapeHtml(data.report_context.report_version)}</code></p>
        ${data.report_context.has_attendance_records ? "" : '<p class="controls-warn-text" role="status">Bu dönemde puantaj kodu yok; sıfır değerler doğrulanmış çalışma sonucu değildir.</p>'}
        <details><summary>Kaynaklar ve eksik kapsam</summary>
          <ul>${Object.entries(data.report_context.source_revisions).map(([period, revisions]) => `<li>${escapeHtml(extrasMonthLabel(period))} ·
            plan ${escapeHtml(revisions.schedule || "kayıt yok")} · puantaj ${escapeHtml(revisions.attendance || "kayıt yok")}</li>`).join("")}</ul>
          <p>${data.report_context.missing_sources.length
            ? `Eksik kaynak: ${data.report_context.missing_sources.map(escapeHtml).join(", ")}`
            : "Eksik kaynak yok."}</p>
        </details>
      </section>
      <div class="analytics-tiles">
        ${tile("Puantajdaki personel", m.people)}
        ${tile("Devamsızlık oranı", `%${controlsNumber(m.absence_rate)}`, `<small>önceki aya göre ${analyticsDelta(prev.absence_rate, " puan")}</small>`)}
        ${tile("Fazla mesai", `${controlsNumber(m.overtime_hours)} sa`, `<small>önceki aya göre ${analyticsDelta(prev.overtime_hours, " sa")}</small>`)}
        ${tile("Dönem kontrolü uyarısı", data.warnings.total, `<small>${data.warnings.counts.error || 0} hata · ${data.warnings.counts.warning || 0} uyarı</small>`)}
        ${tile("Tahmini brüt maliyet", data.cost.people.length ? `${controlsNumber(Math.round(data.cost.totals.total))} ₺` : "—", data.cost.missing_wage.length ? `<small>${data.cost.missing_wage.length} kişide ücret yok</small>` : "")}
        ${tile("Dikkat çeken devamsızlık", flagged.length, '<small>Pazartesi/Cuma/tatil bitişiği</small>')}
      </div>
      <h3 class="analytics-heading">Dönem grafikleri</h3>
      <div class="analytics-charts">
        <section aria-label="Saat dağılımı"><h4>Saat</h4>${barRows(hours)}</section>
        <section aria-label="Gün dağılımı"><h4>Gün</h4>${barRows(days)}</section>
      </div>
      <section class="analytics-detail-panel" data-analytics-detail aria-live="polite" hidden></section>
      <h3 class="analytics-heading">Karşılaştırma</h3>
      ${controlsTable(["Gösterge", extrasMonthLabel(month), extrasMonthLabel(data.previous_month), "Fark", extrasMonthLabel(data.last_year_month), "Geçen yıla göre"],
        rows.map(([label, key]) => [label, column(m, key), column(data.previous, key),
          key === "people" || key === "work_days" || key === "worked_hours" || key === "annual_leave_days" ? (prev[key] === undefined ? "—" : controlsNumber(prev[key])) : analyticsDelta(prev[key]),
          column(data.last_year, key),
          key === "people" || key === "work_days" || key === "worked_hours" || key === "annual_leave_days" ? (year[key] === undefined ? "—" : controlsNumber(year[key])) : analyticsDelta(year[key])]),
        "Veri yok.")}
      <p class="controls-hint">Normal iş günü, puantaj takvimindeki tatil ve hafta sonları dışındaki günlerdir; vardiya veya ücret hesabı değildir. Çalışılan kişi-gün ise kişilerdeki X kodlarının toplamıdır. Devamsızlık oranı = (R + G + Ü) ÷ (X + R + G + Ü). Kayıt yoksa “—” görünür.</p>
      <h3 class="analytics-heading">Öncelikli uyarılar</h3>
      ${controlsTable(["Personel", "Tarih", "Uyarı"], data.warnings.items.map((item) => [escapeHtml(item.name), item.date ? controlsDate(item.date) : "—",
        `<span class="${item.severity === "error" ? "controls-warn-text" : ""}">${escapeHtml(item.message)}</span>`]), "Hata veya uyarı yok.")}`;
    body.querySelectorAll("[data-month-move]").forEach((button) => button.addEventListener("click", () => extrasRun(button, async () => {
      month = controlsMonthShift(month, Number(button.dataset.monthMove));
      await draw();
    })));
    body.querySelectorAll("[data-analytics-metric]").forEach((button) => button.addEventListener("click", () =>
      extrasRun(button, async () => {
        const panel = body.querySelector("[data-analytics-detail]");
        panel.hidden = false;
        panel.textContent = "Kaynak kayıtlar yükleniyor…";
        const detail = await request(`/api/analytics/details?month=${encodeURIComponent(month)}&metric=${encodeURIComponent(button.dataset.analyticsMetric)}`);
        const labels = {
          schedule: "Vardiya", manual: "Elle puantaj düzeltmesi", "schedule-calculation": "Vardiya saat hesabı",
          "holiday-calculation": "Tatil saat hesabı", "overtime-rule": "Fazla mesai kuralı",
          "manual-period-total": "Elle girilen dönem toplamı", "weekly-rule-period-total": "Haftalık kural dönem toplamı",
          unknown: "Kaynak bilgisi yok",
        };
        panel.innerHTML = `<h4>${button.getAttribute("aria-label") || "Gösterge"} · ${extrasMonthLabel(month)}</h4>
          <p class="controls-hint">Plan sürümü: ${escapeHtml(detail.revisions.schedule)} · Puantaj sürümü: ${escapeHtml(detail.revisions.attendance)}</p>
          ${controlsTable(["Personel", "Tarih", "Değer", "Kod", "Kaynak"], detail.items.map((item) => [
            escapeHtml(item.name), item.date ? controlsDate(item.date) : "Dönem geneli", controlsNumber(item.value) + ` ${item.unit}`,
            escapeHtml(item.code || "—"), escapeHtml(labels[item.source] || "Hesaplama kaynağı"),
          ]), "Bu göstergenin kaynak kaydı yok.")}
          ${detail.items.some((item) => !item.date)
            ? '<p class="controls-hint">Dönem geneli olarak işaretlenen mesai kaydının günlük kırılımı mevcut değildir; tutar tek bir güne dağıtılmadı.</p>'
            : ""}`;
      })));
    body.querySelectorAll("[data-chart-note-toggle]").forEach((button) => button.addEventListener("click", () => {
      const editor = body.querySelector(`#${CSS.escape(button.getAttribute("aria-controls"))}`);
      if (!editor) return;
      editor.hidden = !editor.hidden;
      button.setAttribute("aria-expanded", String(!editor.hidden));
      if (!editor.hidden) editor.querySelector("textarea")?.focus();
    }));
    body.querySelectorAll("[data-chart-note-save]").forEach((button) => button.addEventListener("click", () =>
      extrasRun(button, async () => {
        const chartKey = button.dataset.chartNoteSave;
        const input = body.querySelector(`[data-chart-note-input="${CSS.escape(chartKey)}"]`);
        const text = input?.value.trim();
        if (!text) throw new Error("Grafik açıklamasını yazın.");
        const saved = await request("/api/analytics/chart-note", {
          method: "POST",
          body: JSON.stringify({
            month, report_version: data.report_context.report_version, chart_key: chartKey, text,
          }),
        });
        if (saved.report_version !== data.report_context.report_version) {
          throw new Error("Rapor sürümü değişti. Güncel raporu yeniden açıp açıklamayı kaydedin.");
        }
        await draw();
      })));
    const pdf = body.querySelector("[data-summary-pdf]");
    pdf.addEventListener("click", () => extrasRun(pdf, () => saveReportFile(`/api/reports/manager-summary?month=${month}`, "pdf", `Yonetici_Ozeti_${month}.pdf`)));
  };
  await draw();
  showExtras(dialog);
}

async function openAbsencePatterns() {
  const month = state.attendanceMonth || state.scheduleMonth;
  const dialog = extrasDialog("absence-pattern-dialog", `Devamsızlık örüntüsü · son 3 ay (${extrasMonthLabel(month)} dahil)`,
    "Pazartesi, Cuma ya da resmî tatil / hafta tatili bitişiğine denk gelen rapor (R) ve devamsızlık (G) günlerini kişi bazında listeler.");
  const data = await request(`/api/analytics?month=${month}`);
  const reasons = (item) => item.hits.map((hit) => `${controlsDate(hit.date)} ${hit.code} (${hit.reason})`).join("<br>");
  dialog.querySelector(".extras-body").innerHTML = `
    ${controlsTable(["Personel", "R + G günü", "Örüntülü gün", "Oran", "Durum", "Günler"],
      data.patterns.map((item) => [escapeHtml(item.name), item.absence_days, item.pattern_days, `%${item.ratio}`,
        item.flagged ? '<span class="controls-warn-text">Dikkat</span>' : "Normal", `<small>${reasons(item)}</small>`]),
      "Son 3 ayda Pazartesi, Cuma veya tatil bitişiği rapor/devamsızlık yok.")}
    <p class="controls-hint">“Dikkat”: en az 3 örüntülü gün ve kişinin R+G günlerinin en az yarısı. Bu bir suçlama değil, görüşme için bir işarettir; tıbbi raporlar haklı nedendir.</p>`;
  showExtras(dialog);
}

async function openCostEstimate() {
  const month = state.attendanceMonth || state.scheduleMonth;
  const dialog = extrasDialog("cost-estimate-dialog", `Tahmini personel maliyeti · ${extrasMonthLabel(month)}`,
    "Personel kartındaki aylık brüt ücretten saat ücreti hesaplanır; SGK günü, fazla mesai ve UBGT ile aylık brüt maliyet tahmin edilir.");
  const body = dialog.querySelector(".extras-body");
  const draw = async (hours) => {
    const data = await request(`/api/analytics?month=${month}&hours=${hours}`);
    const cost = data.cost;
    const money = (value) => `${controlsNumber(Math.round(value))} ₺`;
    body.innerHTML = `
      <div class="analytics-toolbar">
        <label>Aylık çalışma saati <input id="cost-hours" type="number" min="150" max="240" step="0.5" value="${cost.hours_per_month}"></label>
        <button class="button button-light" type="button" data-cost-apply>Yeniden hesapla</button>
      </div>
      ${controlsTable(["Personel", "Aylık brüt", "Saat ücreti", "SGK gün", "Gün karşılığı", "Fazla mesai", "UBGT", "Toplam"],
        cost.people.map((row) => [escapeHtml(row.name), money(row.monthly_wage), controlsNumber(row.hourly_wage), row.sgk_days,
          money(row.base), money(row.overtime), money(row.ubgt), `<b>${money(row.total)}</b>`])
          .concat(cost.people.length ? [["<b>Toplam</b>", "", "", "", money(cost.totals.base), money(cost.totals.overtime),
            money(cost.totals.ubgt), `<b>${money(cost.totals.total)}</b>`]] : []),
        "Personel kartlarında aylık brüt ücret girilmemiş.")}
      ${cost.missing_wage.length ? `<p class="controls-hint controls-warn-text">Ücreti girilmeyen: ${cost.missing_wage.map(escapeHtml).join(", ")}</p>` : ""}
      <p class="controls-hint">Saat ücreti = aylık brüt ÷ ${controlsNumber(cost.hours_per_month)}. Fazla mesai (puantajdaki FM) 1,5 kat, UBGT saatleri 1 kat ek ödenir. Gece zammı ve işveren SGK/işsizlik payı dahil değildir; bu tutar bordro değil, brüt tahmindir.</p>`;
    const apply = body.querySelector("[data-cost-apply]");
    apply.addEventListener("click", () => extrasRun(apply, () => draw(body.querySelector("#cost-hours").value)));
  };
  await draw(225);
  showExtras(dialog);
}

REPORT_GROUPS.push(
  ["legal_rest", "Yasal sınır: vardiyalar arası 11 saat dinlenme", true],
  ["legal_weekly_rest", "Yasal sınır: kesintisiz 24 saat hafta tatili", true],
  ["night_rotation", "Gece vardiyası rotasyonu", true],
  ["balancing", "Denkleştirme dönemi ortalaması", true],
  ["special_night", "Özel durum: gece çalışması yasağı", true],
  ["special_overtime", "Özel durum: fazla mesai yasağı", true],
  ["special_daily", "Özel durum: günlük süre sınırı", true],
  ["preference", "Personel tercihine aykırı vardiya", true],
  ["skill_coverage", "Yetkinlik eksikliği", true],
  ["leave_request_crowd", "Aynı güne çok izin talebi", true],
  ["leave_request_conflict", "Onaylı izin gününe vardiya", true],
);

EXTRAS_MENU.splice(EXTRAS_MENU.findIndex(([label]) => label.startsWith("Yazdırma")) + 1, 0,
  ["-"],
  ["PDKS ↔ plan mutabakatı", openPdks],
  ["Yayınlanan plan farkı", openPlanDiff],
  ["Puantaj onay akışı", openApprovals],
  ["Yasal uyum ayarları", openCompliance],
  ["-"],
  ["SGK eksik gün ve mesai", openPayroll],
  ["Rapor (R) belgeleri", openSickReports],
  ["Yıllık izin devri ve ücreti", openLeaveCarry],
  ["-"],
  ["Adil dağılım raporu", openFairness],
  ["İzin talep takvimi", openLeaveRequests],
  ["Yetkinlik ve izin kuralları", openPlanningRules],
  ["Bölümler arası ödünç personel", openBorrowed],
  ["Geçmiş puantaj Excel'i içe aktar", openAttendanceImport],
  ["-"],
  ["Yönetici özeti (tek sayfa PDF)", openManagerSummary],
  ["Devamsızlık örüntüsü", openAbsencePatterns],
  ["Tahmini personel maliyeti", openCostEstimate],
);

startExtras();
