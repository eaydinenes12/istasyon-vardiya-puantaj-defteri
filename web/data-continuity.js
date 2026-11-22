"use strict";

(() => {
  const API = "/api/continuity";
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const labels = {
    employees: "Personel", schedule_months: "Vardiya ayları", attendance_months: "Puantaj ayları",
    attendance_events: "Rapor/izin kayıtları", employee_balance_openings: "Açılış bakiyeleri",
    app_settings: "Ayarlar", form_templates: "Belge şablonları", archive_revisions: "Arşiv sürümleri",
    linked_leave_requests: "Bağlı izinler", linked_leave_applications: "İzin uygulama izleri",
    workflow_documents: "Belgeler", workflow_attachments: "Belge ekleri",
    workflow_exports: "Çıktı sürüm izleri",
  };
  let dialog, body, busy = false;
  const month = () => typeof state !== "undefined" ? state.scheduleMonth : new Date().toISOString().slice(0, 7);
  const dirty = () => typeof state !== "undefined" && (state.scheduleDirty || state.attendanceDirty || state.neighborDirty?.size);
  async function api(path, payload) {
    const response = await fetch(API + path, payload === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `İşlem tamamlanamadı (${response.status}).`);
    return result;
  }
  async function databaseBackupRequest(path, file, passphrase, previewId = "", confirmRestore = false) {
    const encodedPassphrase = btoa(String.fromCharCode(...new TextEncoder().encode(passphrase)));
    const response = await fetch(API + path, {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-VP-Backup-Passphrase": encodedPassphrase,
        ...(previewId ? { "X-VP-Backup-Preview": previewId } : {}),
        ...(confirmRestore ? { "X-VP-Confirm-Restore": "yes" } : {}),
      },
      body: file,
    });
    if (!response.ok) {
      let result = {};
      try { result = await response.json(); } catch (_) {}
      throw new Error(result.error || `İşlem tamamlanamadı (${response.status}).`);
    }
    return response;
  }
  function show(title, html) {
    if (!dialog) {
      dialog = document.createElement("dialog");
      dialog.id = "data-continuity-dialog";
      dialog.className = "quick-entry-dialog forms-dialog";
      dialog.setAttribute("aria-labelledby", "data-continuity-title");
      dialog.innerHTML = `<div class="dialog-heading"><h2 id="data-continuity-title"></h2>
        <button type="button" class="icon-button" aria-label="Kapat">×</button></div>
        <p role="status" class="continuity-status"></p><div class="continuity-body"></div>`;
      dialog.querySelector("button").onclick = () => { if (!busy) dialog.close(); };
      dialog.addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
      document.body.append(dialog);
      body = dialog.querySelector(".continuity-body");
      const style = document.createElement("style");
      style.textContent = `#data-continuity-dialog{width:min(860px,calc(100vw - 24px));}
        #data-continuity-dialog .continuity-body{display:grid;gap:12px;min-width:0}
        #data-continuity-dialog label{display:grid;gap:6px}
        #data-continuity-dialog input,#data-continuity-dialog select,#data-continuity-dialog button{min-height:44px}
        #data-continuity-dialog input:not([type=checkbox]),#data-continuity-dialog select{width:100%;min-width:0}
        #data-continuity-dialog .continuity-scroll{overflow:auto;max-height:350px}
        #data-continuity-dialog table{width:100%;border-collapse:collapse;font-size:13px}
        #data-continuity-dialog td,#data-continuity-dialog th{text-align:left;padding:9px;border-bottom:1px solid #d5dee7}
        #data-continuity-dialog p{line-height:1.55;overflow-wrap:anywhere;margin:4px 0}
        #data-continuity-dialog .continuity-error{color:#982b28}
        #data-continuity-dialog .continuity-actions{display:flex;flex-wrap:wrap;gap:8px}
        #data-continuity-dialog :focus-visible{outline:3px solid #1864ab;outline-offset:2px}`;
      document.head.append(style);
    }
    dialog.querySelector("h2").textContent = title;
    body.innerHTML = html;
    status("");
    if (!dialog.open) dialog.showModal();
  }
  function status(text, error = false) {
    const node = dialog.querySelector(".continuity-status");
    node.textContent = text;
    node.classList.toggle("continuity-error", error);
    node.setAttribute("role", error ? "alert" : "status");
  }
  function action(selector, callback) {
    body.querySelector(selector).onclick = async () => {
      if (busy) return;
      busy = true;
      status("İşlem sürüyor…");
      dialog.setAttribute("aria-busy", "true");
      for (const button of body.querySelectorAll("button")) button.disabled = true;
      try { await callback(); }
      catch (error) { status(error.message, true); }
      finally {
        busy = false;
        dialog.removeAttribute("aria-busy");
        for (const button of body.querySelectorAll("button")) button.disabled = false;
      }
    };
  }
  function guard() {
    if (!dirty()) return true;
    throw new Error("Önce açık vardiya ve puantaj değişikliklerini kaydedin veya geri alın. Ekrandaki taslaklar değiştirilmedi.");
  }
  function done(message) {
    show("İşlem tamamlandı", `<p>${esc(message)}</p><p>Yeni verileri görmek için uygulamayı yenileyin.
      Kaynak dosya ve başka bilgisayardaki kayıtlar değiştirilmedi.</p><button class="button" id="continuity-reload">Uygulamayı yenile</button>`);
    action("#continuity-reload", () => { guard(); window.location.reload(); });
  }
  const button = (id, label) => `<button type="button" class="button button-outline" id="${id}">${label}</button>`;

  async function openMigration() {
    show("Güvenli veri aktarımı", `<p>Eski uygulamadaki kayıtları yalnız boş Kolay Kullanım kayıt alanına taşır.
      Mevcut verilerin üzerine yazmaz. Önizleme kaynağı salt okunur açar; aktarım öncesi güvenlik yedeği alınır.
      Kullanıcı hesapları ve paylaşım anahtarları taşınmaz.</p>
      <label>Kaynak SQLite dosyasının tam yolu<input id="continuity-source" type="text"
        placeholder="C:\\…\\vardiya_puantaj.sqlite3" autocomplete="off"></label>
      ${button("continuity-preview", "Kaynağı salt okunur önizle")}<div id="continuity-preview-result"></div>
      <details><summary>Önceki veri aktarımları</summary><div id="continuity-migration-history">Yükleniyor…</div></details>`);
    api("/status").then((result) => {
      const history = body.querySelector("#continuity-migration-history");
      if (!history) return;
      history.innerHTML = result.actions.length ? result.actions.map((item, index) =>
        `<p>${esc(item.created_at.slice(0, 16))} · ${item.undone ? "Geri alındı" : "Aktarıldı"}
        ${item.undone ? "" : button(`continuity-previous-undo-${index}`, "Bu aktarımı geri al")}</p>`).join("")
        : "<p>Henüz veri aktarımı yapılmadı.</p>";
      result.actions.forEach((item, index) => {
        if (item.undone) return;
        action(`#continuity-previous-undo-${index}`, async () => {
          guard();
          if (!window.confirm("Aktarım geri alınsın mı? Sonradan kayıt değiştiyse işlem güvenlik için engellenir.")) return;
          await api("/migration/undo", { action_id: item.id, confirmed: true });
          done("Veri aktarımı geri alındı.");
        });
      });
    }).catch((error) => status(error.message, true));
    action("#continuity-preview", async () => {
      guard();
      const result = await api("/migration/preview", { source: body.querySelector("#continuity-source").value.trim() });
      body.querySelector("#continuity-preview-result").innerHTML = `<p><strong>Kaynak:</strong> ${esc(result.source)}</p>
        <p><strong>Hedef:</strong> ${esc(result.target)}</p><p>${esc(result.notice)}</p>
        <div class="continuity-scroll"><table><thead><tr><th>Kayıt</th><th>Adet</th></tr></thead><tbody>
        ${Object.entries(result.counts).map(([key, count]) => `<tr><td>${esc(labels[key] || key)}</td><td>${count}</td></tr>`).join("")}
        </tbody></table></div><p>Aktarılmayanlar: ${result.excluded.map(esc).join(", ")}.</p>
        <p>Özel tablo şablonları: ${result.template_files.length}</p>
        <p>Kaynakta bırakılan teknik tablolar: ${Object.entries(result.omitted_tables)
          .filter(([, count]) => count).map(([name, count]) => `${esc(name)} (${count})`).join(", ") || "Yok"}</p>
        ${result.can_apply ? button("continuity-migrate", "Yedek al ve boş alana aktar") : "<p>Hedefte kayıt var. Seçili yedek kurtarma veya çalışma paketi kullanın.</p>"}`;
      status("Önizleme hazır. Henüz veri aktarılmadı.");
      if (result.can_apply) action("#continuity-migrate", async () => {
        guard();
        if (!window.confirm("Gösterilen kaynaktaki kayıtlar boş hedefe aktarılsın mı? Hedefin işlem öncesi yedeği alınacak.")) return;
        const applied = await api("/migration/apply", { preview_id: result.preview_id, confirmed: true });
        show("Aktarım tamamlandı", `<p>${esc(applied.notice)}</p><p>Güvenlik yedeği: ${esc(applied.backup_id)}</p>
          <p>Aktarım sonrası yeni veri girilmediyse güvenle geri alınabilir.</p>
          <div class="continuity-actions">${button("continuity-undo", "Bu aktarımı geri al")}
          ${button("continuity-reload", "Uygulamayı yenile")}</div>`);
        action("#continuity-undo", async () => {
          if (!window.confirm("Bu aktarım geri alınsın mı? Sonradan kayıt değiştiyse işlem engellenir.")) return;
          await api("/migration/undo", { action_id: applied.action_id, confirmed: true });
          done("Veri aktarımı geri alındı.");
        });
        action("#continuity-reload", () => { guard(); window.location.reload(); });
      });
    });
  }

  async function openRecovery() {
    show("Seçili dönem kurtarma", "<p>Yedek listesi yükleniyor…</p>");
    try {
      const response = await fetch("/api/backups");
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Yedek listesi alınamadı.");
      if (!result.backups.length) { body.innerHTML = "<p>Henüz yedek yok. Önce Yedekleme aracından yedek oluşturun.</p>"; return; }
      body.innerHTML = `<p>Yedekteki yalnız seçtiğiniz dönem kaydı kurtarılır. Diğer aylar, personel kimlikleri ve hesaplar değiştirilmez.
        Kilitli veya önizleme sonrası değişmiş kayıtların üzerine yazılmaz.</p>
        <label>Yedek<select id="continuity-backup">${result.backups.map((item) =>
          `<option value="${esc(item.id)}">${esc(item.id)}</option>`).join("")}</select></label>
        <label>Dönem<input type="month" id="continuity-month" value="${esc(month())}"></label>
        <label>Kurtarılacak kayıt<select id="continuity-kind"><option value="schedule">Vardiya</option>
          <option value="attendance">Puantaj</option><option value="both">Vardiya ve puantaj</option></select></label>
        ${button("continuity-recovery-preview", "Seçili kayıtları karşılaştır")}<div id="continuity-recovery-result"></div>`;
      action("#continuity-recovery-preview", async () => {
        guard();
        const selected = body.querySelector("#continuity-kind").value;
        const kinds = selected === "both" ? ["schedule", "attendance"] : [selected];
        const preview = await api("/backup/preview", {
          backup_id: body.querySelector("#continuity-backup").value,
          selections: kinds.map((kind) => ({ kind, month: body.querySelector("#continuity-month").value })),
        });
        body.querySelector("#continuity-recovery-result").innerHTML = preview.selections.map((item) =>
          `<p>${esc(item.month)} ${item.kind === "schedule" ? "vardiya" : "puantaj"}: ${item.changed ? `${item.differences.length} fark` : "aynı"}</p>
          <div class="continuity-scroll"><table><thead><tr><th>Hücre</th><th>Güncel</th><th>Yedek</th></tr></thead><tbody>
          ${item.differences.map((difference) => `<tr><td>${esc(difference.cell.join(" · "))}</td>
          <td>${esc(JSON.stringify(difference.before))}</td><td>${esc(JSON.stringify(difference.after))}</td></tr>`).join("")}
          </tbody></table></div>`).join("")
          + `<p>Güncel kayıt arşivlenir, işlem öncesi tam güvenlik yedeği alınır.</p>${button("continuity-recover", "Seçili dönemleri kurtar")}`;
        action("#continuity-recover", async () => {
          guard();
          if (!window.confirm("Seçili dönemler yedekteki sürümle değiştirilsin mi?")) return;
          const result = await api("/backup/apply", { preview_id: preview.preview_id, confirmed: true });
          done(`${result.restored} dönem kaydı kurtarıldı. Güvenlik yedeği: ${result.safety_backup}`);
        });
        status("Karşılaştırma hazır; henüz kurtarma yapılmadı.");
      });
      status("");
    } catch (error) { status(error.message, true); }
  }

  function download(value, filename) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url; link.download = filename; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function downloadBinary(value, filename) {
    const url = URL.createObjectURL(new Blob([value], { type: "application/octet-stream" }));
    const link = document.createElement("a");
    link.href = url; link.download = filename; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function mappingRows(items, people, getName) {
    return `<div class="continuity-scroll"><table><thead><tr><th>Kaynak kişi</th><th>Doğrulanmış hedef kişi</th></tr></thead><tbody>
      ${items.map((item, index) => `<tr><td>${esc(getName(item))}<br><small>${(item.candidates || [])
        .map((candidate) => esc(candidate.name) + " (öneri)").join(", ") || "Otomatik eşleştirme yok"}</small></td>
        <td><select data-mapping="${index}" aria-label="${esc(getName(item))} için hedef kişi"><option value="">Açıkça seçin</option>
        ${people.map((person) => `<option value="${person.id}">${esc(person.name)}</option>`).join("")}</select></td></tr>`).join("")}
      </tbody></table></div><p>Ad benzerliği otomatik birleştirme değildir. Eksik personeli önce Personel aracından ekleyin.</p>`;
  }
  function collectMapping(items, getKey) {
    const mapping = {};
    for (let index = 0; index < items.length; index += 1) {
      const value = body.querySelector(`[data-mapping="${index}"]`).value;
      if (!value) throw new Error("Her kaynak kişi için hedef personeli açıkça seçin.");
      mapping[getKey(items[index])] = Number(value);
    }
    return mapping;
  }
  async function openPackages() {
    show("Çevrimdışı çalışma paketi", `<p>İki bilgisayar arasında dosyayla çalışın. Paket personel adı, vardiya ve puantaj içerir;
      yalnız yetkili kişiye güvenli biçimde iletin. Aynı paket ikinci kez uygulanmaz. Çakışmalar tek tek seçilir.</p>
      <label>Dönem<input id="continuity-package-month" type="month" value="${esc(month())}"></label>
      <label>Dönüş paketi için alınan paket (isteğe bağlı)<select id="continuity-lineage"><option value="">Yeni paket</option></select></label>
      <label>İndirme biçimi<select id="continuity-package-encryption"><option value="plain">Şifresiz JSON</option>
        <option value="encrypted">Parola korumalı</option></select></label>
      <div id="continuity-package-encryption-fields" hidden>
        <label>Şifreleme parolası<input id="continuity-package-passphrase" type="password" minlength="12"
          maxlength="1024" autocomplete="new-password"></label>
        <label>Parolayı doğrula<input id="continuity-package-passphrase-confirm" type="password" minlength="12"
          maxlength="1024" autocomplete="new-password"></label>
        <p>Parola yalnız bu dosyayı açmak için kullanılır ve kaydedilmez. Unutulursa dosya kurtarılamaz.</p>
      </div>
      ${button("continuity-export", "Dönem paketini indir")}
      <label>Alınacak çalışma paketi<input id="continuity-package-file" type="file" accept=".json,.vpe,application/json"></label>
      <label>Şifreli dosya parolası<input id="continuity-package-import-passphrase" type="password"
        maxlength="1024" autocomplete="current-password"></label>
      <p>Şifreli paketin parolası unutulursa içeriği geri getirilemez. Şifresiz JSON dosyaları da desteklenir.</p>
      ${button("continuity-package-load", "Dosyayı oku ve personeli eşleştir")}<div id="continuity-package-result"></div>`);
    body.querySelector("#continuity-package-encryption").onchange = (event) => {
      body.querySelector("#continuity-package-encryption-fields").hidden = event.target.value !== "encrypted";
    };
    api("/status").then((result) => {
      const select = body.querySelector("#continuity-lineage");
      if (select) select.insertAdjacentHTML("beforeend", result.receipts.map((item) =>
        `<option value="${esc(item.id)}">${esc(item.id)} · ${esc(item.received_at.slice(0, 10))}</option>`).join(""));
    }).catch((error) => status(error.message, true));
    action("#continuity-export", async () => {
      guard();
      const selectedMonth = body.querySelector("#continuity-package-month").value;
      const lineage = body.querySelector("#continuity-lineage").value;
      const result = await api(`/package?month=${encodeURIComponent(selectedMonth)}${lineage ? `&lineage=${encodeURIComponent(lineage)}` : ""}`);
      const encrypted = body.querySelector("#continuity-package-encryption").value === "encrypted";
      if (encrypted) {
        const passphrase = body.querySelector("#continuity-package-passphrase").value;
        if (passphrase !== body.querySelector("#continuity-package-passphrase-confirm").value) {
          throw new Error("Şifreleme parolası doğrulaması eşleşmiyor.");
        }
        const protectedPackage = await api("/package/encrypt", { package: result.package, passphrase });
        download(protectedPackage.envelope, `vardiya-calisma-paketi-${selectedMonth}.vpe`);
        body.querySelector("#continuity-package-passphrase").value = "";
        body.querySelector("#continuity-package-passphrase-confirm").value = "";
        status("Şifreli paket indirildi; dosya açılmadan önce parola istenir. Uygulama verileri değiştirilmedi.");
      } else {
        download(result.package, `vardiya-calisma-paketi-${selectedMonth}.json`);
        status("Şifresiz JSON paket indirildi; yalnız parola korumalı seçeneği dosyayı şifreler. Veriler değiştirilmedi.");
      }
    });
    action("#continuity-package-load", async () => {
      guard();
      const file = body.querySelector("#continuity-package-file").files[0];
      if (!file || file.size > 12_000_000) throw new Error("En fazla 12 MB büyüklüğünde çalışma paketi seçin.");
      let packageData;
      try { packageData = JSON.parse(await file.text()); } catch (_) { throw new Error("Dosya geçerli bir JSON çalışma paketi değil."); }
      if (packageData?.format === "vardiya-encrypted-work-package") {
        const passphrase = body.querySelector("#continuity-package-import-passphrase").value;
        if (!passphrase) throw new Error("Şifreli dosyanın açma parolasını girin.");
        const opened = await api("/package/decrypt", { envelope: packageData, passphrase });
        packageData = opened.package;
        body.querySelector("#continuity-package-import-passphrase").value = "";
      }
      const result = await api("/package/matching", { package: packageData });
      body.querySelector("#continuity-package-result").innerHTML =
        `<p>Paket dönemi: ${esc(result.month)}</p>` + mappingRows(result.matching, result.people, (item) => item.source.name)
        + button("continuity-package-compare", "Hücre farklarını karşılaştır");
      action("#continuity-package-compare", async () => {
        const mapping = collectMapping(result.matching, (item) => String(item.source.id));
        const preview = await api("/package/preview", { package: packageData, mapping });
        body.querySelector("#continuity-package-result").innerHTML =
          `<p>${preview.changes.length} fark, ${preview.conflicts} çakışma. Seçim yapılmadan çakışma uygulanmaz.</p>
          <div class="continuity-scroll"><table><thead><tr><th>Hücre</th><th>Mevcut</th><th>Gelen</th><th>Karar</th></tr></thead><tbody>
          ${preview.changes.map((item, index) => `<tr><td>${esc(item.cell.join(" · "))}</td>
          <td>${esc(JSON.stringify(item.before))}</td><td>${esc(JSON.stringify(item.after))}</td>
          <td>${item.conflict ? `<select data-conflict="${index}" aria-label="Çakışma kararı"><option value="">Seçin</option>
          <option value="local">Mevcut kalsın</option><option value="incoming">Gelen kullanılsın</option></select>` : "Gelen uygulanacak"}</td></tr>`).join("")}
          </tbody></table></div>${button("continuity-package-apply", "Kararları onayla ve paketi al")}`;
        action("#continuity-package-apply", async () => {
          guard();
          const choices = {};
          preview.changes.forEach((item, index) => {
            if (!item.conflict) return;
            const value = body.querySelector(`[data-conflict="${index}"]`).value;
            if (!value) throw new Error("Her çakışma için açık karar seçin.");
            choices[item.key] = value;
          });
          if (!window.confirm("Gösterilen hücre farkları seçtiğiniz kararlara göre uygulansın mı? Önce güvenlik yedeği alınacak.")) return;
          const applied = await api("/package/apply", { preview_id: preview.preview_id, choices, confirmed: true });
          done(`Çalışma paketi alındı. Güvenlik yedeği: ${applied.backup_id}`);
        });
        status("Hücre karşılaştırması hazır.");
      });
      status("Personel eşleştirmesini doğrulayın.");
    });
  }

  async function openFullDatabaseBackup() {
    let previewId = "";
    let selectedEnvelope = null;
    show("Şifreli tam veritabanı yedeği", `<p>Bu işlem mevcut SQLite veritabanının tutarlı tam kopyasını üretir; dosya güçlü parola tabanlı anahtarla şifrelenir.
      Parola uygulamada saklanmaz. Parola unutulursa yedek geri getirilemez. Yalnız sentetik veya açıkça yetkili dosyaları seçin.</p>
      <label>Yeni yedek parolası<input id="continuity-db-export-passphrase" type="password" minlength="12" maxlength="1024" autocomplete="new-password"></label>
      <label>Parolayı doğrula<input id="continuity-db-export-confirm" type="password" minlength="12" maxlength="1024" autocomplete="new-password"></label>
      ${button("continuity-db-export", "Şifreli tam yedeği indir")}
      <hr><label>Geri yüklenecek şifreli tam yedek<input id="continuity-db-file" type="file" accept=".vpsql,application/octet-stream"></label>
      <label>Yedek parolası<input id="continuity-db-passphrase" type="password" maxlength="1024" autocomplete="current-password"></label>
      ${button("continuity-db-preview", "Dosyayı doğrula ve önizle")}<div id="continuity-db-preview-result"></div>`);
    action("#continuity-db-export", async () => {
      guard();
      const passphrase = body.querySelector("#continuity-db-export-passphrase").value;
      if (passphrase.length < 12 || passphrase !== body.querySelector("#continuity-db-export-confirm").value) {
        throw new Error("En az 12 karakterli bir parola girin ve doğrulayın.");
      }
      const response = await fetch(API + "/database-backup/export", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ passphrase }),
      });
      if (!response.ok) {
        let result = {};
        try { result = await response.json(); } catch (_) {}
        throw new Error(result.error || `İşlem tamamlanamadı (${response.status}).`);
      }
      downloadBinary(await response.arrayBuffer(), "vardiya-tam-veritabani.vpsql");
      body.querySelector("#continuity-db-export-passphrase").value = "";
      body.querySelector("#continuity-db-export-confirm").value = "";
      status("Şifreli tam veritabanı indirildi. Veritabanı değiştirilmedi; parolayı uygulama dışında güvenli yerde saklayın.");
    });
    action("#continuity-db-preview", async () => {
      guard();
      previewId = "";
      body.querySelector("#continuity-db-preview-result").replaceChildren();
      selectedEnvelope = body.querySelector("#continuity-db-file").files[0];
      if (!selectedEnvelope || selectedEnvelope.size > 500 * 1024 * 1024) {
        throw new Error("500 MB sınırını aşmayan şifreli tam veritabanı dosyası seçin.");
      }
      const passphrase = body.querySelector("#continuity-db-passphrase").value;
      if (!passphrase) throw new Error("Yedek parolasını girin.");
      const response = await databaseBackupRequest("/database-backup/preview", selectedEnvelope, passphrase);
      const result = await response.json();
      previewId = result.preview_id;
      body.querySelector("#continuity-db-preview-result").innerHTML =
        `<p>Doğrulandı: ${esc(result.metadata.size_bytes)} bayt SQLite veritabanı, ${esc(result.metadata.schema_version)}. şema sürümü,
        ${esc(result.encrypted_size_bytes)} bayt şifreli dosya. SQLite bütünlük ve dış anahtar kontrolleri geçti.</p>
        <p>Geri yükleme mevcut verilerin tamamını değiştirecek; önce güvenlik yedeği alınır ve tüm oturumlar kapatılır.</p>
        ${button("continuity-db-restore", "Doğrulanmış yedeği geri yükle")}`;
      action("#continuity-db-restore", async () => {
        guard();
        if (!previewId || !selectedEnvelope || !window.confirm("Doğrulanmış tam veritabanı yedeği mevcut kayıtların tamamının yerini alsın mı? Önce güvenlik yedeği alınacak.")) return;
        const restoredResponse = await databaseBackupRequest(
          "/database-backup/restore", selectedEnvelope,
          body.querySelector("#continuity-db-passphrase").value, previewId, true,
        );
        const restored = await restoredResponse.json();
        body.querySelector("#continuity-db-passphrase").value = "";
        done(`${restored.message} Güvenlik yedeği: ${restored.safety_backup}`);
      });
      body.querySelector("#continuity-db-passphrase").value = "";
      status("Dosya doğrulandı; geri yükleme için parola yeniden istenir.");
    });
  }

  async function openPdks() {
    show("PDKS dosyası aktar ve kişileri eşleştir", `<p>Cihaz dosyasındaki adları kayıtlı personele açıkça eşleştirin.
      Aktarım çalışma/ücret hesabını otomatik değiştirmez. Farkları inceleyip gerekli düzeltmeyi ilgili tabloda yapın.</p>
      <label>Dönem<input id="continuity-pdks-month" type="month" value="${esc(month())}"></label>
      <label>PDKS dosyası<input id="continuity-pdks-file" type="file" accept=".csv,.xlsx"></label>
      ${button("continuity-pdks-read", "Dosyayı oku")}<div id="continuity-pdks-result"></div>`);
    action("#continuity-pdks-read", async () => {
      guard();
      const file = body.querySelector("#continuity-pdks-file").files[0];
      if (!file || file.size > 5_000_000) throw new Error("En fazla 5 MB CSV veya Excel dosyası seçin.");
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
      const source = { month: body.querySelector("#continuity-pdks-month").value, filename: file.name,
        content: btoa(binary), mapping: {} };
      const result = await api("/pdks/preview", source);
      body.querySelector("#continuity-pdks-result").innerHTML =
        pdksSourceHealthMarkup(result.source_health) +
        mappingRows(result.matching, result.people, (item) => item.name) + button("continuity-pdks-compare", "Kayıtları karşılaştır");
      action("#continuity-pdks-compare", async () => {
        const mapping = collectMapping(result.matching, (item) => item.name);
        const preview = await api("/pdks/preview", { ...source, mapping });
        body.querySelector("#continuity-pdks-result").innerHTML = `<p>${esc(preview.notice)}</p>${pdksSourceHealthMarkup(preview.source_health)}
          <p>${preview.records.length} kişi/gün kaydı.</p><div class="continuity-scroll"><table>
          <thead><tr><th>Personel</th><th>Tarih</th><th>Giriş</th><th>Çıkış</th></tr></thead><tbody>
          ${preview.records.map((item) => `<tr><td>${esc(item.name)}</td><td>${esc(item.date)}</td><td>${esc(item.in)}</td><td>${esc(item.out)}</td></tr>`).join("")}
          </tbody></table></div>${button("continuity-pdks-save", "Eşleştirilmiş PDKS kaynağını sakla")}`;
        action("#continuity-pdks-save", async () => {
          guard();
          if (!window.confirm("Eşleştirilmiş PDKS kaynağı saklansın mı? Vardiya ve puantaj değiştirilmeyecek.")) return;
          await api("/pdks/apply", { preview_id: preview.preview_id, confirmed: true });
          done("PDKS kaynağı kaydedildi. Mutabakat farkları mevcut PDKS aracında incelenebilir.");
        });
        status("Mutabakat hazır; henüz kaynak kaydedilmedi.");
      });
      status("Her cihaz adını doğru kişiyle eşleştirin.");
    });
  }
  async function openDuplicates() {
    show("Personel kimlik incelemesi", "<p>Benzer adlar ve yinelenen sicil/bordro kodları inceleniyor…</p>");
    try {
      const result = await api("/duplicates");
      const groupsMarkup = result.groups.map((group, groupIndex) => {
        const pairs = [];
        for (let personIndex = 0; personIndex < group.people.length; personIndex += 1) {
          const person = group.people[personIndex];
          for (let candidateIndex = personIndex + 1; candidateIndex < group.people.length; candidateIndex += 1) {
            const candidate = group.people[candidateIndex];
            const id = `${groupIndex}-${personIndex}-${candidateIndex}`;
            pairs.push(`<div class="continuity-scroll"><p>${esc(person.name)} (#${person.id}, ${person.active ? "aktif" : "pasif"}) ·
              sicil ${esc(person.registry_no || "yok")} · bölüm ${esc(person.section || "belirtilmemiş")} · görevler ${esc(person.skills.join(", ") || "belirtilmemiş")}<br>
              ${esc(candidate.name)} (#${candidate.id}, ${candidate.active ? "aktif" : "pasif"}) ·
              sicil ${esc(candidate.registry_no || "yok")} · bölüm ${esc(candidate.section || "belirtilmemiş")} · görevler ${esc(candidate.skills.join(", ") || "belirtilmemiş")}</p>
              <label>Karar<select id="continuity-match-decision-${id}"><option value="">Seçin</option>
                <option value="same">Aynı kişi</option><option value="different">Farklı kişiler</option></select></label>
              <label>Gerekçe<input id="continuity-match-reason-${id}" maxlength="250" minlength="5"></label>
              ${button(`continuity-match-save-${id}`, "Kararı kaydet")}</div>`);
          }
        }
        return `<h3>${group.kind === "registry" ? "Aynı sicil/bordro kodu" : "Benzer ad"}: ${esc(group.value)}</h3>${pairs.join("")}`;
      }).join("");
      const historyMarkup = result.history.map((item) =>
        "<tr><td>" + esc(item.decided_at.slice(0, 16)) + "</td><td>" +
        esc(item.employee_name) + " (#" + item.employee_id + ") ↔ " + esc(item.candidate_name) +
        " (#" + item.candidate_id + ")</td><td>" +
        (item.decision === "same" ? "Aynı kişi" : "Farklı kişiler") + "</td><td>" +
        esc(item.reason) + "</td><td>" + esc(item.actor) + "</td></tr>"
      ).join("");
      body.innerHTML = `<p>Benzer adlar aynı kişi demek değildir. Hiçbir kayıt otomatik birleştirilmez veya silinmez.
        Sicil, bölüm ve görev bilgilerini karşılaştırıp her aday çifti için gerekçeli karar kaydedin.
        Bu kararlar eşleştirme tarihçesidir; personel kayıtlarını birleştirmez.</p>
        ${groupsMarkup || "<p>Yinelenen sicil/bordro kodu veya benzer adlı mükerrer aday bulunmadı.</p>"}
        <h3>Sicil/bordro kodu eksik</h3><p>${result.missing_registry.map((person) => `${esc(person.name)} (#${person.id})`).join(", ") || "Yok"}</p>
        <h3>Eşleştirme tarihçesi</h3>
        ${historyMarkup ? '<div class="continuity-scroll"><table><thead><tr><th>Tarih</th><th>Kayıtlar</th><th>Karar</th>' +
          historyMarkup + "</tbody></table></div>" : "<p>Henüz kimlik kararı kaydedilmedi.</p>"}`;
      result.groups.forEach((group, groupIndex) => group.people.forEach((person, personIndex) => {
        group.people.slice(personIndex + 1).forEach((candidate, candidateIndex) => {
         const id = `${groupIndex}-${personIndex}-${candidateIndex}`;
         action(`#continuity-match-save-${id}`, async () => {
           const decision = body.querySelector(`#continuity-match-decision-${id}`).value;
           const reason = body.querySelector(`#continuity-match-reason-${id}`).value;
           if (!decision) throw new Error("Önce aynı kişi veya farklı kişiler kararını seçin.");
           await api("/duplicates/decision", {
             employee_id: person.id, candidate_id: candidate.id, decision, reason,
           });
           await openDuplicates();
         });
        });
      }));
      status("");
    } catch (error) { status(error.message, true); }
  }
  window.dataContinuity = { openMigration, openRecovery, openPackages, openFullDatabaseBackup, openPdks, openDuplicates };
  if (typeof EXTRAS_MENU !== "undefined") EXTRAS_MENU.push(
    ["Güvenli veri aktarımı", openMigration], ["Seçili yedek kurtarma", openRecovery],
    ["Çevrimdışı çalışma paketi", openPackages], ["PDKS dosyası aktar ve kişileri eşleştir", openPdks],
    ["Şifreli tam veritabanı yedeği", openFullDatabaseBackup], ["Personel kimlik incelemesi", openDuplicates],
  );
})();
