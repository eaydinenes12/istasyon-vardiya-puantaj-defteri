"use strict";

(() => {
  const API = "/api/document-workflows";
  const supportedCodes = new Set(["G", "S", "Ü"]);
  const codeLabels = { G: "İşe gelmedi", S: "Yıllık izin", "Ü": "Ücretsiz izin", R: "Rapor", "": "Kod yok" };
  const statusLabels = { draft: "Taslak", printed: "Yazdırıldı", signed: "İmzalandı" };
  let busy = false;
  let dialog;
  let body;

  function escape(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[character]));
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const type = response.headers.get("content-type") || "";
    const result = type.includes("application/json") ? await response.json() : await response.blob();
    if (!response.ok) {
      let message = result && result.error;
      if (!message && result instanceof Blob) message = `İstek başarısız (${response.status}).`;
      throw new Error(message || `İstek başarısız (${response.status}).`);
    }
    return result;
  }

  function json(method, value) {
    return { method, body: JSON.stringify(value) };
  }

  function showError(error) {
    if (window.easyAssistant && typeof window.easyAssistant.reportError === "function") {
      window.easyAssistant.reportError(error.message || "İşlem tamamlanamadı.");
      return;
    }
    if (typeof toast === "function") toast(error.message || "İşlem tamamlanamadı.", true);
    else window.alert(error.message || "İşlem tamamlanamadı.");
  }

  function ensureDialog() {
    if (dialog) return;
    dialog = document.createElement("dialog");
    dialog.id = "easy-document-workflows";
    dialog.className = "quick-entry-dialog forms-dialog";
    dialog.setAttribute("aria-labelledby", "easy-document-workflows-title");
    dialog.setAttribute("aria-describedby", "easy-document-workflows-hint");
    dialog.innerHTML = `<div class="dialog-heading">
        <div><h2 id="easy-document-workflows-title">Belgeler ve kayıtlar</h2>
        <p id="easy-document-workflows-hint"></p></div>
        <button class="icon-button dialog-close" type="button" aria-label="Kapat">×</button>
      </div><div class="easy-document-workflows-content"></div>`;
    document.body.append(dialog);
    const style = document.createElement("style");
    style.textContent = `
      #easy-document-workflows .easy-document-workflows-content { display: grid; gap: 12px; }
      #easy-document-workflows .easy-document-list { display: grid; gap: 0; }
      #easy-document-workflows .easy-document-row { padding: 14px 0; border-top: 1px solid #dce5de; }
      #easy-document-workflows .easy-document-row h3,
      #easy-document-workflows .easy-document-workflows-content h3 { margin: 8px 0; color: #214b31; font-size: 14px; }
      #easy-document-workflows .easy-document-row p { margin: 5px 0; line-height: 1.5; overflow-wrap: anywhere; }
      #easy-document-workflows .easy-document-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
      #easy-document-workflows .easy-document-preview-actions { display: flex; flex-wrap: wrap; gap: 8px; }
      #easy-document-workflows .easy-document-status { font-weight: 700; }
      #easy-document-workflows .easy-document-selection { max-height: 180px; overflow: auto; padding-left: 22px; }
      #easy-document-workflows .dialog-field { margin: 4px 0; }
      #easy-document-workflows select, #easy-document-workflows textarea { width: 100%; }
      #easy-document-workflows .easy-document-editor,
      #easy-document-workflows .easy-document-presets { display: grid; gap: 6px; margin: 0; padding: 8px 12px 12px; border: 1px solid #dce5de; }
      #easy-document-workflows .easy-document-editor legend,
      #easy-document-workflows .easy-document-presets legend { padding: 0 4px; color: #214b31; font-weight: 700; }
      #easy-document-workflows .easy-document-presets select,
      #easy-document-workflows .easy-document-presets input { flex: 1 1 200px; width: auto; min-width: 0; }
      #easy-document-workflows .easy-document-presets small { color: #5d6b63; }
      #easy-document-workflows pre { max-width: 100%; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
      #easy-document-workflows li { margin: 7px 0; overflow-wrap: anywhere; }
      @media (max-width: 600px) {
        #easy-document-workflows .easy-document-actions > button { flex: 1 1 100%; }
      }`;
    document.head.append(style);
    body = dialog.querySelector(".easy-document-workflows-content");
    dialog.querySelector(".dialog-close").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  }

  function openPanel(title, hint, markup) {
    ensureDialog();
    dialog.querySelector("#easy-document-workflows-title").textContent = title;
    dialog.querySelector("#easy-document-workflows-hint").textContent = hint;
    body.innerHTML = markup;
    if (!dialog.open) dialog.showModal();
  }

  async function people() {
    if (typeof state !== "undefined" && Array.isArray(state.people) && state.people.length) return state.people;
    const result = await api("/api/people");
    const items = Array.isArray(result) ? result : result.people || [];
    if (typeof state !== "undefined") state.people = items;
    return items;
  }

  async function download(path, filename) {
    const response = await fetch(path);
    if (!response.ok) {
      let message = `Dosya indirilemedi (${response.status}).`;
      try { message = (await response.json()).error || message; } catch (_) { /* Keep the status message. */ }
      throw new Error(message);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    // Blob URLs don't inherit the versioned filename from the calendar response.
    const calendarName = /^attachment; filename="(vardiya-[A-Za-z0-9_-]+\.ics)"$/i
      .exec(response.headers.get("Content-Disposition") || "")?.[1];
    link.download = filename || calendarName || "belge";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return link.download;
  }

  function statusControls(item, attachment = false) {
    const status = item.status;
    const signaturesComplete = !item.signers?.length || item.signers.every((signer) => signer.status === "signed");
    const next = status === "draft" ? "printed"
      : status === "printed" && (attachment || signaturesComplete) ? "signed" : "";
    const endpoint = attachment
      ? `${API}/attachments/${encodeURIComponent(item.id)}/status`
      : `${API}/documents/${encodeURIComponent(item.id)}/status`;
    return `<span class="easy-document-status">${escape(statusLabels[status] || status)}</span>
      ${next ? `<button type="button" class="button button-outline" data-status-url="${escape(endpoint)}"
        data-next-status="${next}">${next === "printed" ? "Yazdırıldı olarak işaretle" : "İmzalandı olarak işaretle"}</button>` : ""}`;
  }

  function confirmStaffVisibility(scope, action) {
    return scope !== "staff" || window.confirm(
      `Bu belge personelin görebileceği kapsamda. ${action} öncesinde iç yönetici notu belgeye eklenmedi; kapsamı kontrol edip devam edin.`,
    );
  }

  function renderDocuments(items) {
    if (!items.length) return `<p>Henüz belge kaydı yok.</p>`;
    return `<div class="easy-document-list">${items.map((item) => `
      <article class="easy-document-row" data-document-id="${escape(item.id)}">
        <h3>${escape(item.person_name)} · ${escape(item.dates.join(", "))}</h3>
        <p>${escape(item.filename)} · ${escape(item.status_label)}
          ${item.current ? " · Güncel" : " · Kaynak plan/puantaj sonradan değişti"}
        </p>
        <p>Görünürlük: ${escape(item.visibility_label)}</p>
        ${item.internal_note ? `<p><strong>Yalnız yönetici iç notu:</strong> ${escape(item.internal_note)}</p>` : ""}
        <p>Sürüm ${escape(item.app_version)} · Kod ${escape(item.code_revision)}</p>
        ${item.signers?.length ? `<h4>İmzacı takibi</h4><ul>${item.signers.map((signer) => `<li>
          ${escape(signer.name)} · ${signer.status === "signed" ? "Tamamlandı" : "Bekliyor"}
          ${signer.status === "pending" && item.status === "printed" ? `<button type="button" class="button button-outline"
            data-document-signer="${escape(item.id)}" data-signer-id="${escape(signer.id)}">İmzayı tamamlandı olarak işaretle</button>` : ""}
        </li>`).join("")}</ul>` : "<p>İmzacı listesi tanımlanmamış.</p>"}
        ${item.status === "printed" && item.signers?.some((signer) => signer.status !== "signed")
          ? "<p role=\"status\">Belgenin genel durumu, tüm imzalar tamamlanana kadar imzalandı olmaz.</p>" : ""}
        <div class="easy-document-actions">
          <button type="button" class="button button-outline" data-preview-document="${escape(item.id)}"
            data-visibility-scope="${escape(item.visibility_scope)}">Önizle / yazdır</button>
          <button type="button" class="button button-outline" data-save-document="${escape(item.id)}"
            data-save-name="${escape(item.filename)}" data-visibility-scope="${escape(item.visibility_scope)}">Dosyayı kaydet…</button>
          ${statusControls(item)}
        </div>
        <label class="dialog-field">PDF, PNG veya JPEG ekle (en fazla 8 MB)
          <input type="file" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
            data-attachment-for="${escape(item.id)}">
        </label>
        ${item.attachments.length ? `<ul>${item.attachments.map((file) => `<li>
          <button type="button" class="button button-light" data-download-attachment="${escape(file.id)}"
            data-download-name="${escape(file.filename)}">${escape(file.filename)}</button>
          ${file.same_name_changed ? `<p>Bu adla farklı içerikli ekler de kaydedilmiş.</p>` : ""}
          <p>SHA-256: <code>${escape(file.sha256)}</code></p>
          ${statusControls(file, true)}
        </li>`).join("")}</ul><p>SHA-256 yalnız dosya içeriğinin bütünlük karşılaştırması içindir; hukuki gerçekliğini kanıtlamaz.</p>` : ""}
      </article>`).join("")}</div>`;
  }

  function formatDateTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString("tr-TR");
  }

  function renderRetention(result) {
    const policy = result.policy;
    const reviewDocuments = result.review_documents || [];
    const policyStatus = policy
      ? `Belge oluşturulma tarihinden ${escape(policy.retention_days)} gün sonra inceleme listesine alınır.`
      : "Saklama süresi belirlenmedi; uygulama süre varsaymaz ve otomatik inceleme başlatmaz.";
    const reviewList = reviewDocuments.length
      ? `<div class="easy-document-list">${reviewDocuments.map((item) => `
          <article class="easy-document-row">
            <h4>${escape(item.person_name)} · ${escape(item.filename)}</h4>
            <p>Oluşturulma: ${escape(formatDateTime(item.created_at))} · İnceleme tarihi: ${escape(formatDateTime(item.review_due_at))}</p>
            <p>${escape(statusLabels[item.status] || item.status)} · Kayıt inceleme için listelenmiştir; silinmemiştir.</p>
            <p>Görünürlük: ${escape(item.visibility_label)}
              ${item.internal_note ? `· Yalnız yönetici iç notu: ${escape(item.internal_note)}` : ""}</p>
            <div class="easy-document-actions">
              <button type="button" class="button button-outline" data-preview-document="${escape(item.id)}"
                data-visibility-scope="${escape(item.visibility_scope)}">Önizle / incele</button>
              <button type="button" class="button button-outline" data-save-document="${escape(item.id)}"
                data-save-name="${escape(item.filename)}" data-visibility-scope="${escape(item.visibility_scope)}">Dosyayı kaydet…</button>
            </div>
          </article>`).join("")}</div>`
      : "<p>İnceleme bekleyen belge yok.</p>";
    return `<section aria-labelledby="easy-retention-title">
      <h3 id="easy-retention-title">Belge saklama ve inceleme</h3>
      <p>Kurumunuzun belirlediği süreyi girin. Süre belge oluşturulma tarihinden hesaplanır.
        Yasal süre tahmin edilmez; süresi gelen belgeler listelenir, hiçbir belge otomatik silinmez.</p>
      <label class="dialog-field" for="easy-retention-days">Kurumsal saklama süresi (gün)
        <input id="easy-retention-days" type="number" min="1" max="365000" step="1"
          value="${policy ? escape(policy.retention_days) : ""}" placeholder="Kurumun belirlediği gün sayısı">
      </label>
      <div class="easy-document-actions">
        <button type="button" class="button button-primary" data-save-retention>Süreyi kaydet</button>
        <button type="button" class="button button-outline" data-clear-retention${policy ? "" : " disabled"}>Süreyi kaldır</button>
      </div>
      <p role="status">${policyStatus}</p>
      <h4>İnceleme listesi (${reviewDocuments.length})</h4>
      ${reviewList}
    </section>`;
  }

  async function openCenter() {
    openPanel("Belge ve tutanak dosyası", "Belge oluşturma, indirme, yazdırma ve imza ayrı adımlardır.", "<p>Yükleniyor…</p>");
    try {
      const [{ documents }, retention] = await Promise.all([
        api(`${API}/documents?limit=200`),
        api(`${API}/retention`),
      ]);
      body.innerHTML = `${renderRetention(retention)}<h3>Belge kayıtları</h3>${renderDocuments(documents)}`;
    } catch (error) { showError(error); }
  }

  async function openDocumentPreview(documentId, visibilityScope = "internal") {
    openPanel("Belge önizlemesi", "Görsel düzen yaklaşık olarak gösterilir. Yazdırma bittikten sonra durumu ayrıca işaretleyebilirsiniz.", `
      <div class="easy-document-preview-actions">
        <button type="button" class="button button-light" data-back-document-center>Kayıtlara dön</button>
        <button type="button" class="button button-primary" data-print-document
          data-visibility-scope="${escape(visibilityScope)}" disabled>Yazdır</button>
      </div>
      <p role="status" id="easy-document-preview-status">Önizleme hazırlanıyor…</p>
      <iframe class="easy-document-preview-frame" title="Tutanak önizlemesi"
        sandbox="allow-same-origin allow-modals"></iframe>
      <details><summary>Metin dökümünü göster</summary><pre id="easy-document-preview-text"></pre></details>`);
    try {
      const result = await api(`${API}/documents/${encodeURIComponent(documentId)}/preview`);
      const frame = body.querySelector(".easy-document-preview-frame");
      frame.srcdoc = result.html;
      body.querySelector("#easy-document-preview-text").textContent = result.text;
      body.querySelector("#easy-document-preview-status").textContent = "Belge içeriğini kontrol edin; yazdırma ayrı bir adımdır.";
      body.querySelector("[data-print-document]").disabled = false;
    } catch (error) {
      body.querySelector("#easy-document-preview-status").textContent = error.message || "Önizleme hazırlanamadı.";
    }
  }

  async function saveDocument(documentId, suggestedName, visibilityScope = "internal") {
    if (!confirmStaffVisibility(visibilityScope, "dosyayı kaydetme")) return;
    const environment = await api("/api/environment");
    if (environment.desktop) {
      const result = await api(`${API}/documents/${encodeURIComponent(documentId)}/save`, json("POST", {}));
      if (!result.cancelled && typeof toast === "function") toast(`Belge kaydedildi: ${result.path}`);
      return;
    }
    const response = await fetch(`${API}/documents/${encodeURIComponent(documentId)}/file`);
    if (!response.ok) {
      let message = `Dosya indirilemedi (${response.status}).`;
      try { message = (await response.json()).error || message; } catch (_) { /* Keep the status message. */ }
      throw new Error(message);
    }
    const blob = await response.blob();
    const filename = suggestedName || "TUTANAK.docx";
    const extension = filename.split(".").pop().toLowerCase();
    if (typeof saveFormBlob !== "function") throw new Error("Dosya kaydetme penceresi kullanılamıyor.");
    try {
      const savedName = await saveFormBlob(blob, extension, filename);
      if (typeof toast === "function") toast(`Belge kaydedildi: ${savedName}`);
    } catch (error) {
      if (error.name !== "AbortError") throw error;
    }
  }

  function exactCells(cells) {
    if (!Array.isArray(cells) || !cells.length || cells.length > 250) {
      throw new Error("Seçim 1-250 hücre arasında olmalıdır.");
    }
    const seen = new Set();
    return cells.map((cell) => {
      if (!cell || !Number.isSafeInteger(Number(cell.employee_id)) || Number(cell.employee_id) < 1
          || !/^\d{4}-\d{2}-\d{2}$/.test(String(cell.date))) {
        throw new Error("Seçili hücrede personel veya tarih bilgisi eksik.");
      }
      const employeeId = Number(cell.employee_id);
      const date = String(cell.date);
      const code = String(cell.code || "").trim().toLocaleUpperCase("tr-TR");
      const key = `${employeeId}|${date}`;
      if (seen.has(key)) return null;
      seen.add(key);
      return { employee_id: employeeId, date, code };
    }).filter(Boolean);
  }

  const documentCodeChoices = [
    ["G", "??e gelmeme tutana??"], ["S", "Y?ll?k izin formu"], ["?", "?cretsiz izin formu"],
    ["R", "Rapor kayd?"], ["", "Genel durum / olay tutana??"],
  ];

  function documentPresets() {
    const presets = typeof state !== "undefined" && state.uiSettings && state.uiSettings.document_presets;
    return Array.isArray(presets) ? presets : [];
  }

  async function storeDocumentPresets(presets) {
    if (typeof saveUiSettings === "function") return saveUiSettings({ document_presets: presets });
    const result = await api("/api/ui-settings", json("PUT", { document_presets: presets }));
    if (typeof state !== "undefined") state.uiSettings = result.settings;
    return result.settings;
  }

  async function openSelection(cells, options = {}) {
    let selection;
    try { selection = exactCells(cells); } catch (error) { showError(error); return; }
    let peopleList;
    let forms;
    try {
      [peopleList, { forms }] = await Promise.all([people(), api(`${API}/templates`)]);
    } catch (error) { showError(error); return; }
    const nameById = new Map(peopleList.map((person) => [Number(person.id), person.name]));
    const editable = Boolean(options.editable) && selection.length === 1;
    const draft = {
      title: "", section: "", description: "", preset: "", signers: "", eventTime: "",
      visibilityScope: "internal", internalNote: "",
    };
    const field = (selector) => body.querySelector(selector);
    const remember = () => {
      if (!field("#easy-document-description")) return;
      draft.title = field("#easy-document-title").value;
      draft.section = field("#easy-document-section").value;
      draft.description = field("#easy-document-description").value;
      draft.preset = field("#easy-document-preset").value;
      draft.signers = field("#easy-document-signers").value;
      draft.eventTime = field("#easy-document-event-time").value;
      draft.visibilityScope = field("#easy-document-visibility").value;
      draft.internalNote = field("#easy-document-internal-note").value;
      if (editable) {
        const date = field("#easy-document-date").value;
        if (/^\d{4}-\d{2}-\d{2}$/.test(date)) selection[0].date = date;
        selection[0].code = field("#easy-document-code").value;
      }
    };
    const presetCode = () => {
      const codes = [...new Set(selection.map((cell) => cell.code))];
      return codes.length === 1 && documentCodeChoices.some(([code]) => code === codes[0]) ? codes[0] : "";
    };
    const render = () => {
      const codes = [...new Set(selection.map((cell) => cell.code))];
      const templateFields = codes.map((code) => {
        const available = supportedCodes.has(code)
          ? forms.filter((item) => item.status === "active"
            && (item.category === code || item.category === "GENEL"))
          : [];
        const choices = [`<option value="neutral"${!available.length ? " selected" : ""}>Durum kay?t formu (n?tr)</option>`,
          ...available.map((item, index) =>
            `<option value="${escape(item.id)}"${index === 0 ? " selected" : ""}>${escape(item.name)} (.${escape(item.extension)})</option>`)]
          .join("");
        return `<label class="dialog-field">${escape(codeLabels[code] || `Kod ${code || "yok"}`)} formu
          <select data-template-code="${escape(code)}">${choices}</select>
        </label>`;
      }).join("");
      const rows = selection.map((cell) => `<li>${escape(nameById.get(cell.employee_id) || `Personel ${cell.employee_id}`)} ?
          ${escape(cell.date)} ? ${escape(codeLabels[cell.code] || cell.code || "kod yok")}
          ? ayr? belge olarak dahil edilir</li>`).join("");
      const current = selection[0];
      const codeChoices = documentCodeChoices.some(([code]) => code === current.code)
        ? documentCodeChoices : [[current.code, `Kod ${current.code}`], ...documentCodeChoices];
      const editor = editable ? `<fieldset class="easy-document-editor"><legend>Bu tutana?? d?zenle</legend>
          <label class="dialog-field">Tarih <input type="date" id="easy-document-date" value="${escape(current.date)}"></label>
          <label class="dialog-field">Tutanak t?r? <select id="easy-document-code">${codeChoices.map(([code, label]) =>
            `<option value="${escape(code)}"${code === current.code ? " selected" : ""}>${escape(label)}</option>`).join("")}</select></label>
        </fieldset>` : "";
      const presets = documentPresets();
      const presetFields = `<fieldset class="easy-document-presets"><legend>Tutanak ?ablonlar?</legend>
          <div class="easy-document-actions">
            <select id="easy-document-preset" aria-label="Kay?tl? tutanak ?ablonu">
              <option value="">${presets.length ? "Kay?tl? ?ablon se?in?" : "Hen?z kay?tl? ?ablon yok"}</option>
              ${presets.map((preset) => `<option value="${escape(preset.name)}"${preset.name === draft.preset ? " selected" : ""}>${escape(preset.name)}</option>`).join("")}
            </select>
            <button type="button" class="button button-light" id="easy-document-preset-apply"${presets.length ? "" : " disabled"}>Uygula</button>
            <button type="button" class="button button-light" id="easy-document-preset-delete"${presets.length ? "" : " disabled"}>Sil</button>
          </div>
          <div class="easy-document-actions">
            <input id="easy-document-preset-name" maxlength="60" placeholder="Yeni ?ablon ad?" aria-label="Yeni tutanak ?ablonu ad?">
            <button type="button" class="button button-outline" id="easy-document-preset-save">?ablon olarak kaydet</button>
          </div>
          <small>Ba?l?k, t?r, b?l?m ve a??klama birlikte saklan?r; sonraki tutanaklarda tek t?kla uygulan?r.</small>
        </fieldset>`;
      openPanel(
        editable ? "Tutanak / belge haz?rla" : "Se?ili g?nler i?in form",
        editable
          ? "Tarihi, t?r? ve metni yaln?zca bu tutanak i?in de?i?tirebilirsiniz. S?k kulland???n?z i?eri?i ?ablon olarak saklay?n."
          : "Her se?ili personel/tarih i?in ayr? bir g?nl?k belge haz?rlan?r. Aradaki g?nler eklenmez; ki?iler birle?tirilmez.",
        `<p><strong>${selection.length} kay?t</strong> ? tamam? ayr? belgeye al?n?r</p>
          <ul class="easy-document-selection">${rows}</ul>
          ${editor}
          ${presetFields}
          ${templateFields}
          <p>Rapor (R) ve ?ablonsuz t?rlerde n?tr form kullan?l?r: personel, tarih, durum ve a??klama.</p>
          <label class="dialog-field">Tutanak ba?l??? <input id="easy-document-title" maxlength="120"
            placeholder="?rn. ??e gelmeme tutana??" value="${escape(draft.title)}"></label>
          <label class="dialog-field">B?l?m <input id="easy-document-section" maxlength="80" value="${escape(draft.section)}"></label>
          <label class="dialog-field">A??klama <textarea id="easy-document-description" maxlength="500" rows="3">${escape(draft.description)}</textarea></label>
          <p>Olu?turulan kay?tlar <strong>Taslak</strong> olarak saklan?r. Ard?ndan ?nizleyip yazd?rabilir veya istedi?iniz klas?re kaydedebilirsiniz.</p>
          <label class="dialog-field">Olay saati (isteğe bağlı)
            <input id="easy-document-event-time" type="time" value="${escape(draft.eventTime)}">
          </label>
          <div id="easy-document-field-preview" role="status" aria-live="polite"></div>
          <label class="dialog-field">Belge görünürlük kapsamı
            <select id="easy-document-visibility">
              <option value="internal"${draft.visibilityScope === "internal" ? " selected" : ""}>Yalnız yönetici / iç kullanım</option>
              <option value="staff"${draft.visibilityScope === "staff" ? " selected" : ""}>Personele gösterilebilir</option>
            </select>
          </label>
          <label class="dialog-field">Yalnız yönetici iç notu
            <textarea id="easy-document-internal-note" maxlength="500" rows="2">${escape(draft.internalNote)}</textarea>
          </label>
          <label class="dialog-field">İmzacı adları (her satıra bir kişi)
            <textarea id="easy-document-signers" maxlength="800" rows="2" placeholder="İmzacı adını yazın">${escape(draft.signers)}</textarea>
          </label>
          <div class="dialog-actions">
            ${typeof options.onBack === "function" ? `<button type="button" class="button button-light" id="easy-document-back">${escape(options.backLabel || "? Geri d?n")}</button>` : ""}
            <button type="button" class="button button-primary" id="easy-document-generate">${selection.length === 1 ? "Tasla?? olu?tur" : "Ayr? taslaklar? olu?tur"}</button>
          </div>
          <div id="easy-document-generation-result" role="status" aria-live="polite"></div>`,
      );
      if (editable) {
        field("#easy-document-date").addEventListener("change", () => { remember(); render(); });
        field("#easy-document-code").addEventListener("change", () => { remember(); render(); });
      }
      const renderFieldPreview = () => {
        const time = field("#easy-document-event-time").value;
        field("#easy-document-field-preview").innerHTML = time
          ? `<p>Belge önizlemesi · Olay saati: <strong>${escape(time)}</strong></p>`
          : '<p>Belge önizlemesi · Olay saati: <button type="button" class="button button-light" data-document-field="event_time">[Olay saati]</button></p>';
      };
      renderFieldPreview();
      field("#easy-document-event-time").addEventListener("input", () => { remember(); renderFieldPreview(); });
      field("#easy-document-field-preview").addEventListener("click", (event) => {
        if (event.target.closest('[data-document-field="event_time"]')) {
          const sourceField = field("#easy-document-event-time");
          sourceField.scrollIntoView({ block: "center", inline: "nearest" });
          sourceField.focus({ preventScroll: true });
        }
      });
      field("#easy-document-back")?.addEventListener("click", () => {
        dialog.close();
        options.onBack();
      });
      field("#easy-document-preset-apply").addEventListener("click", () => {
        remember();
        const preset = documentPresets().find((item) => item.name === draft.preset);
        if (!preset) { showError(new Error("Uygulanacak ?ablonu se?in.")); return; }
        Object.assign(draft, {
          title: preset.title || "", section: preset.section || "", description: preset.description || "",
          eventTime: preset.event_time || "",
        });
        if (editable && documentCodeChoices.some(([code]) => code === preset.code)) selection[0].code = preset.code;
        render();
        if (typeof toast === "function") toast(`?${preset.name}? ?ablonu uyguland?.`);
      });
      field("#easy-document-preset-delete").addEventListener("click", async (event) => {
        remember();
        if (!draft.preset) { showError(new Error("Silinecek ?ablonu se?in.")); return; }
        if (!window.confirm(`?${draft.preset}? tutanak ?ablonu silinsin mi?`)) return;
        event.currentTarget.disabled = true;
        try {
          await storeDocumentPresets(documentPresets().filter((item) => item.name !== draft.preset));
          draft.preset = "";
          render();
          if (typeof toast === "function") toast("Tutanak ?ablonu silindi.");
        } catch (error) { showError(error); event.currentTarget.disabled = false; }
      });
      field("#easy-document-preset-save").addEventListener("click", async (event) => {
        const button = event.currentTarget;
        const name = field("#easy-document-preset-name").value.trim().replace(/\s+/g, " ");
        remember();
        if (!name) { showError(new Error("?ablon i?in bir ad yaz?n.")); field("#easy-document-preset-name").focus(); return; }
        const existing = documentPresets();
        const replaced = existing.some((item) => item.name.toLowerCase() === name.toLowerCase());
        if (replaced && !window.confirm(`?${name}? adl? ?ablon g?ncellensin mi?`)) return;
        const preset = {
          name, title: draft.title.trim(), code: presetCode(), section: draft.section.trim(),
          description: draft.description.trim(), event_time: draft.eventTime,
        };
        button.disabled = true;
        try {
          await storeDocumentPresets([...existing.filter((item) => item.name.toLowerCase() !== name.toLowerCase()), preset]);
          draft.preset = name;
          render();
          if (typeof toast === "function") toast(replaced ? "Tutanak ?ablonu g?ncellendi." : "Tutanak ?ablonu kaydedildi.");
        } catch (error) { showError(error); button.disabled = false; }
      });
      let generatedDocuments = [];
      let failedCells = [];
      const renderBatchResult = () => {
        const resultNode = field("#easy-document-generation-result");
        const documentRows = generatedDocuments.map((item) => `<li>${escape(item.person_name)} · ${escape(item.dates[0])} ·
          <button type="button" class="button button-outline" data-preview-document="${escape(item.id)}"
            data-visibility-scope="${escape(item.visibility_scope)}">Önizle / yazdır</button>
          <button type="button" class="button button-outline" data-save-document="${escape(item.id)}"
            data-save-name="${escape(item.filename)}" data-visibility-scope="${escape(item.visibility_scope)}">Dosyayı kaydet</button></li>`).join("");
        const failures = failedCells.map((item) => `<li>${escape(item.person_name)} · ${escape(item.date)} ·
          ${escape(codeLabels[item.code] || item.code || "kod yok")}: ${escape(item.reason)}</li>`).join("");
        resultNode.innerHTML = `<p>${generatedDocuments.length} başarılı, ${failedCells.length} başarısız belge.
            Oluşturulan taslaklar yazdırılmış veya imzalanmış sayılmaz.</p>
          <h3>Başarılı belgeler</h3>${documentRows ? `<p>Görünürlük kapsamı: ${escape(generatedDocuments[0].visibility_label)}.</p><ul>${documentRows}</ul>` : "<p>Henüz başarılı belge yok.</p>"}
          <h3>Başarısız belgeler ve nedenleri</h3>${failures ? `<ul>${failures}</ul>` : "<p>Başarısız belge yok.</p>"}
          ${failedCells.length ? `<button type="button" class="button button-light" id="easy-documents-retry">
            Yalnız başarısızları yeniden dene (${failedCells.length})</button>` : ""}
          <button type="button" class="button button-light" id="easy-documents-refresh">Belge kayıtlarını aç</button>`;
        resultNode.querySelector("#easy-documents-refresh").addEventListener("click", openCenter);
        resultNode.querySelector("#easy-documents-retry")?.addEventListener("click", async (event) => {
          const button = event.currentTarget;
          if (!confirmStaffVisibility(draft.visibilityScope, "başarısız belgeleri yeniden oluşturma")) return;
          button.disabled = true;
          button.textContent = "Başarısız belgeler yeniden deneniyor…";
          try {
            const result = await sendBatch(failedCells.map(({ employee_id, date, code }) => ({ employee_id, date, code })));
            generatedDocuments = generatedDocuments.concat(result.documents);
            failedCells = result.failures;
            renderBatchResult();
          } catch (error) {
            showError(error);
            button.disabled = false;
            button.textContent = `Yalnız başarısızları yeniden dene (${failedCells.length})`;
          }
        });
      };
      const sendBatch = (cells) => {
        const templates = {};
        body.querySelectorAll("[data-template-code]").forEach((select) => {
          templates[select.dataset.templateCode] = select.value;
        });
        return api(`${API}/documents/batch`, json("POST", {
          cells,
          templates,
          section: draft.section.trim(),
          description: draft.description.trim(),
          title: draft.title.trim(),
          signers: draft.signers.split(/\r?\n/).map((name) => name.trim()).filter(Boolean),
          event_time: draft.eventTime,
          visibility_scope: draft.visibilityScope,
          internal_note: draft.internalNote.trim(),
        }));
      };
      field("#easy-document-generate").addEventListener("click", async (event) => {
        const button = event.currentTarget;
        remember();
        if (!confirmStaffVisibility(draft.visibilityScope, "belge oluşturma")) return;
        if (editable && !/^\d{4}-\d{2}-\d{2}$/.test(field("#easy-document-date").value)) {
          showError(new Error("Tutanak i?in ge?erli bir tarih se?in."));
          return;
        }
        button.disabled = true;
        try {
          const result = await sendBatch(selection);
          generatedDocuments = result.documents;
          failedCells = result.failures;
          renderBatchResult();
        } catch (error) {
          showError(error);
          button.disabled = false;
        }
      });
    };
    render();
  }

  async function openRelated() {
    const initialMonth = typeof state !== "undefined" && state.attendanceMonth || new Date().toISOString().slice(0, 7);
    openPanel("İlişkili kayıtlar", "Plan veya puantaj sürümü değiştiğinde eski onayları ve belgeleri bulun.", `
      <label class="dialog-field">Ay <input id="easy-related-month" type="month" value="${escape(initialMonth)}"></label>
      <button type="button" class="button button-primary" id="easy-related-load">Güncelliği denetle</button>
      <div id="easy-related-results" role="status"></div>`);
    body.querySelector("#easy-related-load").addEventListener("click", async () => {
      try {
        const month = body.querySelector("#easy-related-month").value;
        const result = await api(`${API}/related?month=${encodeURIComponent(month)}`);
        const approvals = result.stale_approvals.map((item) =>
          `<li>${escape(item.step)} · ${escape(item.name)} · Onay kaynağı değişti</li>`).join("");
        const documents = result.stale_documents.map((item) =>
          `<li>${escape(item.person_name)} · ${escape(item.dates.join(", "))} · ${escape(item.filename)}</li>`).join("");
        body.querySelector("#easy-related-results").innerHTML =
          `<h3>${escape(month)} · mevcut kaynak sürümleri</h3>
          <pre>${escape(JSON.stringify(result.revisions, null, 2))}</pre>
          <h3>Eski onaylar</h3><ul>${approvals || "<li>Eski onay yok.</li>"}</ul>
          <h3>Eski belgeler</h3><ul>${documents || "<li>Eski belge yok.</li>"}</ul>`;
      } catch (error) { showError(error); }
    });
  }

  async function openCalculation(employeeId = null) {
    const [peopleList] = await Promise.all([people()]);
    const month = typeof state !== "undefined" && state.attendanceMonth || new Date().toISOString().slice(0, 7);
    const options = peopleList.map((person) =>
      `<option value="${person.id}">${escape(person.name)}${person.active ? "" : " (pasif)"}</option>`).join("");
    openPanel("Hesap açıklaması", "Var olan puantaj/bakiye hesabını açar; yeni hesap kuralı uygulanmaz.", `
      <label class="dialog-field">Ay <input id="easy-calculation-month" type="month" value="${escape(month)}"></label>
      <label class="dialog-field">Personel <select id="easy-calculation-person">${options}</select></label>
      <button type="button" class="button button-primary" id="easy-calculation-load">Açıklamayı göster</button>
      <div id="easy-calculation-result" role="status"></div>`);
    if (employeeId != null) {
      const select = body.querySelector("#easy-calculation-person");
      if ([...select.options].some((option) => option.value === String(employeeId))) {
        select.value = String(employeeId);
      }
    }
    body.querySelector("#easy-calculation-load").addEventListener("click", async () => {
      try {
        const selectedMonth = body.querySelector("#easy-calculation-month").value;
        const employee = body.querySelector("#easy-calculation-person").value;
        const result = await api(`${API}/calculation?month=${encodeURIComponent(selectedMonth)}&employee_id=${encodeURIComponent(employee)}`);
        const labels = {
          name: "Personel", active: "Aktif personel", has_opening: "Kayıtlı açılış bakiyesi",
          annual_leave_opening_days: "Yıllık izin açılışı (gün)",
          annual_leave_used_days: "Kullanılan yıllık izin (gün)",
          annual_leave_remaining_days: "Kalan yıllık izin (gün)",
          overtime_opening_hours: "Devreden fazla mesai (saat)",
          overtime_reported_hours: "Dönem fazla mesaisi (saat)",
          overtime_tracked_hours: "Toplam takip edilen fazla mesai (saat)",
          sick_days: "Raporlu gün", unpaid_leave_days: "Ücretsiz izin günü", absence_days: "İşe gelinmeyen gün",
        };
        const entries = Object.entries(labels).filter(([key]) => key in result.result).map(([key, label]) => {
          const value = result.result[key];
          return `<tr><th scope="row">${escape(label)}</th><td>${escape(typeof value === "boolean" ? value ? "Evet" : "Hayır" : value)}</td></tr>`;
        }).join("");
        const manual = result.manual_overrides.map((item) =>
          `<li>${escape(item.date)} · ${escape(item.code)} · Elle girildi</li>`).join("");
        const daily = result.daily_rows.map((item) => `<tr>
          <th scope="row">${escape(item.date)}</th><td>${escape(item.code || "—")}</td>
          <td>${escape(({ manual: "Elle girildi", schedule: "Vardiya planı" })[item.source] || "Kaynak yok")}</td><td>${escape(item.worked_hours)}</td>
          <td>${escape(item.worked_minutes ?? "—")}</td>
          <td>${escape(item.break_hours)} sa (${escape(item.break_minutes ?? "—")} dk)</td>
          <td>${escape(item.holiday_hours)} sa (${escape(item.holiday_minutes ?? "—")} dk)</td>
          <td>${escape(item.daily_limit_hours)} sa (${escape(item.daily_limit_minutes ?? "—")} dk)</td>
          <td>${escape(item.overtime_hours)} sa (${escape(item.overtime_minutes ?? "—")} dk)</td></tr>`).join("");
        const leave = result.annual_leave_equation;
        const totals = result.monthly_totals;
        const target = body.querySelector("#easy-calculation-result");
        if (!target || body.querySelector("#easy-calculation-month").value !== selectedMonth
            || body.querySelector("#easy-calculation-person").value !== employee) return;
        const settings = result.overtime_settings;
        const breaks = (settings.break_rules || []).map(([hours, minutes]) =>
          `Çalışma eşiği ${hours} saat / ${minutes} dakika mola`).join("; ") || "Mola kuralı yok";
        target.innerHTML =
          `<p>${escape(result.notice)}</p><table><tbody>${entries}</tbody></table>
          <h3>Yıllık izin hesabı</h3>
          <p>${escape(leave.opening_days)} açılış − ${escape(leave.used_days)} kullanılan =
            <strong>${escape(leave.remaining_days)} gün</strong> kalan</p>
          <h3>${escape(selectedMonth)} çalışma kaynağı ve günlük döküm</h3>
          <div class="table-scroll"><table><thead><tr><th>Tarih</th><th>Kod</th><th>Kaynak</th>
            <th>Çalışma (sa)</th><th>Çalışma (dk)</th><th>Mola</th><th>Tatil</th><th>Günlük sınır</th><th>FM</th></tr></thead><tbody>${daily}</tbody></table></div>
          <h3>Var olan aylık toplamlar</h3>
          <p>Hesaplanan FM: ${escape(totals.overtime_computed_hours)} sa · Puantaja yazılan:
            ${escape(totals.overtime_hours)} sa${totals.overtime_manual ? " (elle girilen FM kullanılıyor)" : ""}</p>
          <p>Uygulanan kural: ${escape(result.overtime_rule_effective_date || "Yürürlük tarihi kayıtlı değil")} ·
            ${escape(result.overtime_rule_date_note)} Günlük sınır ${escape(settings.daily_limit_hours)} saat;
            ${escape(breaks)}. Limit kaynağı: ${escape(result.overtime_rule_source.daily_limit)};
            mola kaynağı: ${escape(result.overtime_rule_source.break)}.</p>
          <p>${escape(result.rounding.display)} Hesap dakika hassasiyetindedir; saat yuvarlaması yalnız gösterim içindir.</p>
          <h3>Bu ay elle girilen puantaj düzeltmeleri</h3><ul>${manual || "<li>Elle girilen düzeltme yok.</li>"}</ul>`;
      } catch (error) { showError(error); }
    });
    if (employeeId != null) body.querySelector("#easy-calculation-load").click();
  }

  async function openShares() {
    const [peopleList, { shares }] = await Promise.all([people(), api(`${API}/shares`)]);
    const month = typeof state !== "undefined" && state.scheduleMonth || new Date().toISOString().slice(0, 7);
    const options = peopleList.map((person) => `<option value="${person.id}">${escape(person.name)}</option>`).join("");
    openPanel("Yayımlanmış vardiya paylaşımı", "Telefon bağlantısı yalnızca yerel ağ görünümü açıkken üretilebilir. Kişiye özel bağlantı sadece yayımlanmış vardiya planını gösterir; puantaj ve diğer kayıtlar paylaşılmaz.", `
      <form id="easy-share-form">
        <label class="dialog-field">Personel <select name="employee_id" required>${options}</select></label>
        <label class="dialog-field">Yayımlanmış ay <input name="month" type="month" value="${escape(month)}" required></label>
        <label class="dialog-field">Bağlantı geçerliliği
          <select name="days"><option value="1">1 gün</option><option value="7" selected>7 gün</option>
            <option value="14">14 gün</option><option value="30">30 gün</option></select>
        </label>
        <button class="button button-primary" type="submit">Güvenli bağlantı oluştur</button>
        <button class="button button-outline" id="easy-share-calendar" type="button" aria-describedby="easy-calendar-help">Takvim dosyasını indir (.ics)</button>
        <p id="easy-calendar-help">Bu dosya yalnızca seçili kişinin yayımlanmış vardiyalarını ve plan sürümünü içerir. Takviminiz otomatik güncellenmez. Plan değişirse yeni dosyayı indirip takviminize yeniden aktarın.</p>
      </form><div id="easy-share-result" role="status"></div>
      <h3>Mevcut bağlantılar</h3>
      <ul>${shares.length ? shares.map((share) => `<li>${escape(share.person_name)} · ${escape(share.month)} ·
        ${share.active ? `sona erer ${escape(share.expires_at)}` : "süresi dolmuş / iptal edilmiş"}
        ${share.active ? `<button type="button" class="button button-outline" data-revoke-share="${escape(share.id)}">İptal et</button>` : ""}
      </li>`).join("") : "<li>Paylaşım yok.</li>"}</ul>`);
    body.querySelector("#easy-share-calendar").addEventListener("click", async (event) => {
      const form = event.currentTarget.form;
      const { employee_id, month } = form.elements;
      if (!employee_id.reportValidity() || !month.reportValidity()) return;
      const payload = { employee_id: Number(employee_id.value), month: month.value };
      const status = body.querySelector("#easy-share-result");
      try {
        const environment = await api("/api/environment");
        if (environment.desktop) {
          const result = await api(`${API}/calendar/save`, json("POST", payload));
          if (result.cancelled) {
            status.textContent = "Kaydetme iptal edildi; dosya oluşturulmadı.";
          } else if (result.verified) {
            status.innerHTML = `<p>${escape(result.person_name)} · ${escape(result.month)} takvimi kaydedildi ve doğrulandı.</p>
              <p>Plan sürümü: ${escape(result.plan_version)} · Dosya: ${escape(result.path)}</p>
              <p>SHA-256: ${escape(result.sha256)}</p>`;
          } else {
            throw new Error("Takvim dosyasının erişimi doğrulanamadı.");
          }
        } else {
          const filename = await download(`${API}/calendar?${new URLSearchParams(payload)}`);
          status.textContent = `İndirme başlatıldı: ${filename}. Dosyanın kaydedildiği tarayıcıdan doğrulanamaz.`;
        }
      }
      catch (error) { showError(error); }
    });
    body.querySelector("#easy-share-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const payload = {
        employee_id: Number(form.elements.employee_id.value),
        month: form.elements.month.value,
        days: Number(form.elements.days.value),
      };
      try {
        const phone = await api("/api/workspace/phone");
        const origins = phone.running ? [...new Set((phone.urls || []).flatMap((raw) => {
          try {
            const url = new URL(raw);
            if (!["http:", "https:"].includes(url.protocol)
                || !url.hostname || /^(localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[?::1\]?)$/i.test(url.hostname)) return [];
            return [url.origin];
          } catch (_) { return []; }
        }))] : [];
        if (!origins.length) {
          throw new Error("Telefon bağlantısını oluşturmak için önce yerel ağ telefon görünümünü açın.");
        }
        const { share } = await api(`${API}/shares`, json("POST", payload));
        const urls = origins.map((origin) => `${origin}/document-share/${encodeURIComponent(share.token)}`);
        const url = urls[0];
        body.querySelector("#easy-share-result").innerHTML = `<p>Bağlantı yalnızca bir kez gösterilir. Son kullanma: ${escape(share.expires_at)}</p>
          <ul>${urls.map((item) => `<li><a href="${escape(item)}" target="_blank" rel="noopener noreferrer">${escape(item)}</a></li>`).join("")}</ul>
          <button type="button" class="button button-outline" id="easy-share-copy">Bağlantıyı kopyala</button>`;
        body.querySelector("#easy-share-copy").addEventListener("click", async () => {
          try { await navigator.clipboard.writeText(url); toast("Bağlantı kopyalandı."); }
          catch (_) { showError(new Error("Panoya kopyalanamadı; bağlantıyı seçip kopyalayın.")); }
        });
      } catch (error) { showError(error); }
    });
  }

  async function openBackups() {
    openPanel("Yedeği incele", "Yedek salt okunur açılır; geri yükleme, şema geçişi ve yönetici hesabı/parolası gösterimi yapılmaz.", "<p>Yedekler yükleniyor…</p>");
    try {
      const { backups } = await api("/api/backups");
      body.innerHTML = backups.length ? `<ul>${backups.map((item) => `<li>
        ${escape(item.id)} · ${escape(item.created_at)} · ${escape(item.size_bytes)} bayt
        <button type="button" class="button button-outline" data-inspect-backup="${escape(item.id)}">Özetini incele</button>
        </li>`).join("")}</ul><div id="easy-backup-result" role="status"></div>` : "<p>İncelenecek yedek yok.</p>";
    } catch (error) { showError(error); }
  }

  async function openExports() {
    openPanel("Çıktı sürüm izleri", "Belgedeki kodla kaynağı, sürümü ve güncelliği arayın.", "<p>İzler yükleniyor…</p>");
    try {
      const { exports } = await api(`${API}/exports`);
      body.innerHTML = `<form id="easy-export-lookup-form" class="easy-document-actions">
          <label class="dialog-field">Belgedeki çıktı kodu <input id="easy-export-code" maxlength="19"
            placeholder="EX-1A2B3C4D5E6F7788" autocomplete="off"></label>
          <button type="submit" class="button button-primary">Kodla ara</button>
        </form><div id="easy-export-lookup-result" role="status"></div>
        <h3>Son kayıtlı çıktılar</h3>
        ${exports.length ? `<div class="easy-document-list">${exports.map((item) => `
        <article class="easy-document-row"><h3>${escape(item.code)} · ${escape(item.filename)} · ${escape(item.month)}</h3>
          <p>${escape(item.type)} · ${item.current ? "Kaynak güncel" : "Kaynak sonradan değişmiş"}</p>
          ${item.template_revision ? `<p>Şablon sürümü <code>${escape(item.template_revision)}</code></p>` : ""}
          <p>Sürüm ${escape(item.app_version)} · Kod ${escape(item.code_revision)}</p>
          <p>SHA-256 <code>${escape(item.sha256)}</code></p>
          <pre>${escape(JSON.stringify(item.source_revisions, null, 2))}</pre>
        </article>`).join("")}</div>` : "<p>Henüz izlenebilir bir çıktı yok.</p>"}`;
    } catch (error) { showError(error); }
  }

  function renderExport(item) {
    return `<article class="easy-document-row"><h3>${escape(item.code)} · ${escape(item.filename)} · ${escape(item.month)}</h3>
      <p>${escape(item.type)} · ${item.current ? "Kaynak güncel" : "Kaynak sonradan değişmiş"}</p>
      ${item.template_revision ? `<p>Şablon sürümü <code>${escape(item.template_revision)}</code></p>` : ""}
      <p>Sürüm ${escape(item.app_version)} · Kod ${escape(item.code_revision)}</p>
      <p>SHA-256 <code>${escape(item.sha256)}</code></p>
      <pre>${escape(JSON.stringify(item.source_revisions, null, 2))}</pre></article>`;
  }

  function bindDelegates() {
    body.addEventListener("click", async (event) => {
      const button = event.target.closest("button");
      if (!button || busy) return;
      try {
        if (button.dataset.downloadDocument) {
          if (!confirmStaffVisibility(button.dataset.visibilityScope, "dosyayı indirme")) return;
          await download(`${API}/documents/${encodeURIComponent(button.dataset.downloadDocument)}/file`, button.dataset.downloadName);
        } else if (button.dataset.previewDocument) {
          if (!confirmStaffVisibility(button.dataset.visibilityScope, "önizleme")) return;
          await openDocumentPreview(button.dataset.previewDocument, button.dataset.visibilityScope);
        } else if (button.hasAttribute("data-print-document")) {
          if (!confirmStaffVisibility(button.dataset.visibilityScope, "yazdırma")) return;
          const frame = body.querySelector(".easy-document-preview-frame");
          if (!frame?.contentWindow) throw new Error("Önizleme penceresi hazır değil.");
          frame.contentWindow.focus();
          frame.contentWindow.print();
        } else if (button.hasAttribute("data-back-document-center")) {
          await openCenter();
        } else if (button.dataset.saveDocument) {
          await saveDocument(button.dataset.saveDocument, button.dataset.saveName, button.dataset.visibilityScope);
        } else if (button.hasAttribute("data-save-retention")) {
          const input = body.querySelector("#easy-retention-days");
          if (!input.reportValidity()) return;
          busy = true;
          button.disabled = true;
          await api(`${API}/retention`, json("PUT", { retention_days: input.valueAsNumber }));
          await openCenter();
        } else if (button.hasAttribute("data-clear-retention")) {
          if (!window.confirm("Saklama süresi kaldırılır; inceleme listesi kapanır ancak belgeler silinmez. Devam edilsin mi?")) return;
          busy = true;
          button.disabled = true;
          await api(`${API}/retention`, json("PUT", { retention_days: null }));
          await openCenter();
        } else if (button.dataset.downloadAttachment) {
          await download(`${API}/attachments/${encodeURIComponent(button.dataset.downloadAttachment)}/file`, button.dataset.downloadName);
        } else if (button.dataset.statusUrl) {
          busy = true;
          await api(button.dataset.statusUrl, json("PUT", { status: button.dataset.nextStatus }));
          await openCenter();
        } else if (button.dataset.documentSigner) {
          busy = true;
          await api(`${API}/documents/${encodeURIComponent(button.dataset.documentSigner)}/signers`, json("PUT", {
            signer_id: button.dataset.signerId,
            status: "signed",
          }));
          await openCenter();
        } else if (button.dataset.revokeShare) {
          if (!window.confirm("Bu kişisel vardiya bağlantısını iptal edilsin mi?")) return;
          await api(`${API}/shares/${encodeURIComponent(button.dataset.revokeShare)}/revoke`, json("POST", {}));
          await openShares();
        } else if (button.dataset.inspectBackup) {
          const { inspection } = await api(`${API}/backup-inspection?id=${encodeURIComponent(button.dataset.inspectBackup)}`);
          const result = body.querySelector("#easy-backup-result");
          const choices = Object.entries({ schedule: "schedule_months", attendance: "attendance_months" })
            .flatMap(([kind, key]) => (inspection.months[key] || []).map((month) =>
              `<button type="button" class="button button-light" data-backup-month="${escape(month)}"
                data-backup-kind="${kind}" data-backup-id="${escape(inspection.id)}">${kind === "schedule" ? "Vardiya" : "Puantaj"} ${escape(month)}</button>`));
          const periodFields = [["schedule", "Vardiya"], ["attendance", "Puantaj"]].map(([kind, label]) => {
            const latest = inspection.months[`${kind}_months`]?.[0];
            return latest ? `<label>${label} ayı <input type="month" required data-rehearsal-period="${kind}"
              value="${escape(latest)}"></label>` : `<span>${label}: yedekte kayıtlı ay yok.</span>`;
          }).join(" ");
          result.innerHTML = `<p>${escape(inspection.valid ? "Bütünlük denetimi başarılı." : inspection.integrity)}
            · ${escape(inspection.size_bytes)} bayt · ${escape(JSON.stringify(inspection.counts))}</p>
            <p>Ay içeriğini salt okunur aç:</p><div class="easy-document-actions">${choices.join("") || "İncelenecek ay kaydı yok."}</div>
            ${inspection.valid ? `<fieldset><legend>Geri dönüş provası</legend>${periodFields}
              <p>Yalnız seçilen aylardaki örnek Excel dosyaları açılır. Diğer aylar, belge eklerinin içeriği
              ve basılı sayfa düzeni bu denemeyle doğrulanmaz.</p>
              <button type="button" class="button button-outline" data-rehearse-backup="${escape(inspection.id)}">Geçici alanda geri dönüş provası</button></fieldset>` : ""}
            <p id="easy-backup-rehearsal" role="status"></p><pre id="easy-backup-detail"></pre>`;
        } else if (button.dataset.rehearseBackup) {
          busy = true;
          button.disabled = true;
          const output = body.querySelector("#easy-backup-rehearsal");
          output.textContent = "Yedek geçici alanda geri yükleniyor…";
          try {
            const periods = {};
            for (const input of button.closest("fieldset").querySelectorAll("[data-rehearsal-period]")) {
              if (!input.reportValidity()) { output.textContent = "Prova başlatılmadı. Geçerli bir ay seçin."; return; }
              periods[input.dataset.rehearsalPeriod] = input.value;
            }
            const { rehearsal } = await api(`${API}/backup-rehearsal`, json("POST", { id: button.dataset.rehearseBackup, periods }));
            const checks = Object.entries({ schedule: "Vardiya", attendance: "Puantaj" }).map(([kind, label]) => {
              const check = rehearsal.checks[kind];
              return check ? `${label} ${check.month}: ${check.sheets} Excel sayfası açıldı` : `${label}: yedekte kayıtlı ay yok`;
            });
            output.textContent = `Canlı kayıt değiştirilmedi. ${checks.join(" · ")}.`;
          } catch (error) {
            output.textContent = "Prova tamamlanmadı.";
            throw error;
          } finally {
            button.disabled = false;
          }
        } else if (button.dataset.backupMonth) {
          const params = new URLSearchParams({
            id: button.dataset.backupId,
            month: button.dataset.backupMonth,
            kind: button.dataset.backupKind,
          });
          const { inspection } = await api(`${API}/backup-inspection?${params}`);
          body.querySelector("#easy-backup-detail").textContent =
            JSON.stringify(inspection.detail, null, 2);
        }
      } catch (error) { showError(error); }
      finally { busy = false; }
    });
    body.addEventListener("change", async (event) => {
      const input = event.target;
      const documentId = input.dataset.attachmentFor;
      if (!documentId || !input.files?.[0]) return;
      const file = input.files[0];
      if (file.size > 8_000_000) {
        showError(new Error("Ek dosyası 8 MB sınırından büyük."));
        input.value = "";
        return;
      }
      try {
        const encoded = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onerror = () => reject(new Error("Dosya okunamadı."));
          reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
          reader.readAsDataURL(file);
        });
        await api(`${API}/documents/${encodeURIComponent(documentId)}/attachments`,
          json("POST", { filename: file.name, content: encoded }));
        await openCenter();
      } catch (error) { showError(error); }
      finally { input.value = ""; }
    });
    body.addEventListener("submit", async (event) => {
      if (event.target.id !== "easy-export-lookup-form") return;
      event.preventDefault();
      const code = body.querySelector("#easy-export-code").value.trim().toUpperCase();
      try {
        const { export: item } = await api(`${API}/exports/lookup?code=${encodeURIComponent(code)}`);
        body.querySelector("#easy-export-lookup-result").innerHTML = renderExport(item);
      } catch (error) {
        body.querySelector("#easy-export-lookup-result").textContent = error.message || "Kod bulunamadı.";
      }
    });
  }

  ensureDialog();
  bindDelegates();
  document.getElementById("open-document-center")?.addEventListener("click", openCenter);
  window.documentWorkflows = { openSelection, openCenter, openRelated, openShares, openBackups, openCalculation, openExports };

  if (typeof EXTRAS_MENU !== "undefined") {
    const entries = [
      ["Belge ve tutanak dosyası", openCenter],
      ["İlişkili kayıtlar", openRelated],
      ["Yayımlanmış vardiya paylaşımı", () => openShares().catch(showError)],
      ["Yedeği incele", openBackups],
      ["Hesap açıklaması", () => openCalculation().catch(showError)],
      ["Çıktı sürüm izleri", openExports],
    ];
    for (const entry of entries) {
      if (!EXTRAS_MENU.some(([label]) => label === entry[0])) EXTRAS_MENU.push(entry);
    }
  }
})();
