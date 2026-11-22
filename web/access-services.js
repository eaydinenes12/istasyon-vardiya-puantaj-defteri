"use strict";

(() => {
  const e = escapeHtml;
  const call = (path, method, data) => request(path, { method, body: JSON.stringify(data) });
  const buttonStyle = "min-height:44px";
  function dialog(id, title, description) {
    const box = extrasDialog(id, title, description);
    box.querySelector(".extras-body").innerHTML = '<p role="status">Yükleniyor…</p>';
    showExtras(box);
    return box.querySelector(".extras-body");
  }
  function fail(body, error) {
    const status = body.querySelector("[data-error]") || body;
    status.textContent = error.message;
    status.setAttribute("role", "alert");
  }
  async function permissions() {
    const body = dialog("access-permissions", "Özel yetkiler", "Yetkileri yalnız yönetici değiştirir.");
    try {
      const data = await request("/api/access/permissions");
      const users = data.users.filter((item) => item.role === "operator");
      body.innerHTML = `<p data-error role="alert"></p>${users.length ? `
        <form><label>Kullanıcı <select name="username">${users.map(u => `<option value="${e(u.username)}">${e(u.username)}</option>`).join("")}</select></label>
        <fieldset><legend>Yetkiler</legend>${Object.entries(data.permissions).map(([id, label]) =>
    `<label class="extras-check" style="display:block;min-height:44px"><input type="checkbox" name="permission" value="${e(id)}"> ${e(label)}${data.sensitive.includes(id) ? " · hassas" : ""}</label>`).join("")}</fieldset>
        <section class="extras-note" data-permission-preview aria-live="polite"></section>
        <button class="button button-primary" style="${buttonStyle}">Yetkileri kaydet</button></form>` :
    "<p>Önce Kullanıcılar ve parolalar bölümünden özel yetkili kullanıcı oluşturun.</p>"}`;
      const form = body.querySelector("form");
      if (!form) return;
      const preview = form.querySelector("[data-permission-preview]");
      const renderPreview = () => {
        const selected = [...form.querySelectorAll('[name="permission"]:checked')].map((checkbox) => checkbox.value);
        const effects = { pages: [], fields: [], actions: [], outputs: [] };
        for (const permission of selected) for (const key of Object.keys(effects)) {
          for (const item of data.effects[permission][key]) if (!effects[key].includes(item)) effects[key].push(item);
        }
        const sections = [["Sayfalar", "pages"], ["Görebileceği alanlar", "fields"],
          ["Yapabileceği işlemler", "actions"], ["Alabileceği çıktılar", "outputs"]];
        preview.innerHTML = `<strong>Kaydetmeden önce örnek erişim</strong>${
          sections.map(([title, key]) => `<p><b>${title}:</b> ${effects[key].length
            ? effects[key].map(e).join(" · ") : "Bu seçimle yok"}</p>`).join("")}
          <p>Önizleme seçili yetkilerin etkisini gösterir; henüz uygulanmadı. Kaydetme sonrasında her istek sunucuda ayrıca denetlenir.</p>`;
      };
      const sync = () => {
        const user = users.find(u => u.username === form.username.value);
        form.querySelectorAll('[name="permission"]').forEach(c => { c.checked = user.permissions.includes(c.value); });
        renderPreview();
      };
      sync();
      form.username.addEventListener("change", sync);
      form.querySelectorAll('[name="permission"]').forEach((checkbox) =>
        checkbox.addEventListener("change", renderPreview));
      form.addEventListener("submit", async event => {
        event.preventDefault();
        const selected = [...form.querySelectorAll('[name="permission"]:checked')].map(c => c.value);
        const current = users.find(u => u.username === form.username.value);
        const sensitive = selected.filter(p => data.sensitive.includes(p) && !current.permissions.includes(p));
        if (sensitive.length && !confirm(`Hassas yetkiler verilsin mi?\n${sensitive.map(p => data.permissions[p]).join(", ")}`)) return;
        const submit = form.querySelector("button");
        submit.disabled = true;
        try {
          const result = await call("/api/access/permissions", "PUT",
            { username: form.username.value, permissions: selected, confirm_sensitive: !!sensitive.length });
          current.permissions = result.users.find(u => u.username === current.username).permissions;
          body.querySelector("[data-error]").textContent = "Yetkiler kaydedildi.";
        } catch (error) { fail(body, error); } finally { submit.disabled = false; }
      });
    } catch (error) { fail(body, error); }
  }

  async function links() {
    const body = dialog("access-links", "Personel bağlantıları", "Bağlantı yalnız seçili personele aittir. Güvenli bir kanaldan paylaşın.");
    try {
      const [data, deviceData] = await Promise.all([
        request("/api/access/portal-links"), request("/api/access/device-sessions"),
      ]);
      body.innerHTML = `<p data-error role="alert"></p><form class="forms-fields">
        <label>Personel<select name="employee_id" required><option value="">Seçin</option>${extrasPeopleOptions("", true)}</select></label>
        <label>Geçerlilik (gün)<input name="days" type="number" min="1" max="90" value="7" required></label>
        <button class="button button-primary" style="${buttonStyle}">Bağlantı oluştur</button></form>
        <p data-result role="status"></p><p>Bağlantı yalnız oluşturulduğunda gösterilir; telefon hizmeti ayrıca açılmalıdır.
          Son etkinlik bağlantı bazında tutulur; aynı bağlantıyı kullanan cihazlar ayrı ayrı tanımlanmaz.
          Bir bağlantıyı iptal etmek yalnız o bağlantıyı kapatır; diğer bağlantılar etkilenmez.</p>
        <h3>Bağlantılar</h3><div data-list></div>`;
      const list = body.querySelector("[data-list]");
      body.insertAdjacentHTML("beforeend", `<h3>Cihaz oturumları</h3>
        <p>Her mobil HTTPS girişine ayrı oturum kimliği verilir. Bir oturumu kapatmak diğer cihazları ve personel bağlantılarını etkilemez.</p>
        <div data-device-sessions></div>`);
      const deviceList = body.querySelector("[data-device-sessions]");
      const render = () => {
        list.innerHTML = data.links.length ? `<table class="extras-table"><thead><tr><th>Personel</th><th>Bitiş</th><th>Durum</th><th>Son etkinlik</th><th>Görülen plan sürümü</th><th></th></tr></thead><tbody>${data.links.map(link => {
          const versions = link.plan_versions || [];
          const activity = link.last_seen_at
            ? new Date(link.last_seen_at * 1000).toLocaleString("tr-TR") : "Henüz açılmadı";
          const versionText = versions.length ? versions.slice(0, 3).map((version) => {
            const viewed = version.viewed_revision
              ? `görülen ${e(version.viewed_revision.slice(0, 8))}` : "henüz açılmadı";
            return `${e(version.month)} · yayın ${e(version.published_revision.slice(0, 8))} · ${viewed}`;
          }).join("<br>") : "Yayımlanmış plan yok";
          return `<tr><td>${e(link.name)}</td><td>${new Date(link.expires_at * 1000).toLocaleDateString("tr-TR")}</td><td>${link.revoked_at ? "İptal" : link.expires_at * 1000 <= Date.now() ? "Süresi doldu" : "Etkin"}</td><td>${e(activity)}</td><td>${versionText}</td><td>${link.revoked_at ? "" : `<button class="button button-light" style="${buttonStyle}" data-revoke="${e(link.token_hash)}">İptal et</button>`}</td></tr>`;
        }).join("")}</tbody></table>` : "<p>Bağlantı yok.</p>";
      };
      const renderDevices = () => {
        deviceList.innerHTML = deviceData.sessions.length ? `<table class="extras-table"><thead><tr>
          <th>Personel</th><th>Cihaz oturumu</th><th>Oluşturuldu</th><th>Son etkinlik</th><th>Durum</th><th></th>
          </tr></thead><tbody>${deviceData.sessions.map(session => {
            const status = session.revoked_at ? "İptal" : session.active ? "Etkin" : "Süresi doldu";
            return `<tr><td>${e(session.name)}</td><td>${e(session.session_id)}</td>
              <td>${new Date(session.created_at * 1000).toLocaleString("tr-TR")}</td>
              <td>${new Date(session.last_seen_at * 1000).toLocaleString("tr-TR")}</td><td>${status}</td>
              <td>${session.active ? `<button class="button button-light" style="${buttonStyle}" data-device-revoke="${e(session.session_id)}">Oturumu kapat</button>` : ""}</td></tr>`;
          }).join("")}</tbody></table>` : "<p>Henüz mobil cihaz oturumu yok.</p>";
      };
      render();
      renderDevices();
      body.querySelector("form").addEventListener("submit", async event => {
        event.preventDefault();
        const form = event.currentTarget, submit = form.querySelector("button");
        submit.disabled = true;
        try {
          const result = await call("/api/access/portal-links", "POST", { employee_id: Number(form.employee_id.value), days: Number(form.days.value) });
          body.querySelector("[data-result]").textContent = result.origins.length
            ? `Personel bağlantısı: ${result.origins.map(origin => origin + result.path).join(" · ")}. Yalnız güvenilir ağda paylaşın.`
            : `Telefon hizmeti kapalı. Açtıktan sonra adresinin sonuna ekleyin: ${result.path}`;
          const fresh = await request("/api/access/portal-links");
          data.links = fresh.links; render();
          deviceData.sessions = (await request("/api/access/device-sessions")).sessions; renderDevices();
        } catch (error) { fail(body, error); } finally { submit.disabled = false; }
      });
      list.addEventListener("click", async event => {
        const button = event.target.closest("[data-revoke]");
        if (!button || !confirm("Bu bağlantı iptal edilsin mi?")) return;
        button.disabled = true;
        try {
          await call("/api/access/portal-links/revoke", "POST", { token_hash: button.dataset.revoke });
          data.links = (await request("/api/access/portal-links")).links; render();
          deviceData.sessions = (await request("/api/access/device-sessions")).sessions; renderDevices();
        } catch (error) { fail(body, error); button.disabled = false; }
      });
      deviceList.addEventListener("click", async event => {
        const button = event.target.closest("[data-device-revoke]");
        if (!button || !confirm("Bu cihaz oturumu hemen kapatılsın mı?")) return;
        button.disabled = true;
        try {
          await call("/api/access/device-sessions/revoke", "POST", { session_id: button.dataset.deviceRevoke });
          deviceData.sessions = (await request("/api/access/device-sessions")).sessions; renderDevices();
        } catch (error) { fail(body, error); button.disabled = false; }
      });
    } catch (error) { fail(body, error); }
  }

  async function reviews() {
    const body = dialog("access-requests", "Personel talepleri", "Kabul, çizelgeye işlemez. İzinler Bağlantılı izinler bölümünde ayrıca oluşturulup onaylanır.");
    try {
      const data = await request("/api/access/requests");
      const kind = { leave: "İzin", swap: "Takas", availability: "Uygunluk" };
      const status = { pending: "Bekliyor", accepted: "Kabul edildi · işlem bekliyor", rejected: "Reddedildi" };
      const render = () => {
        body.innerHTML = `<p data-error role="alert"></p>${["pending", "accepted", "rejected"].map(group => {
          const items = data.requests.filter(r => r.status === group);
          return `<h3>${status[group]}</h3>${items.length ? `<table class="extras-table"><thead><tr><th>Personel</th><th>Talep</th><th>Tarih</th><th>Açıklama</th><th>İşlem</th></tr></thead><tbody>${items.map(r =>
            `<tr><td>${e(r.name)}</td><td>${e(kind[r.kind])}</td><td>${e(r.start_date)} – ${e(r.end_date)}</td><td>${e(r.details)}</td><td>${group === "pending" ? `<button class="button button-light" style="${buttonStyle}" data-review="${r.id}" data-decision="accepted">Kabul et</button> <button class="button button-light" style="${buttonStyle}" data-review="${r.id}" data-decision="rejected">Reddet</button>` : group === "accepted" && r.kind === "leave" ? `<button class="button button-light" style="${buttonStyle}" data-leave="${r.id}">Bağlantılı izne git</button>` : "—"}</td></tr>`).join("")}</tbody></table>` : "<p>Kayıt yok.</p>"}`;
        }).join("")}`;
      };
      render();
      body.addEventListener("click", async event => {
        const link = event.target.closest("[data-leave]");
        if (link) {
          const item = data.requests.find(r => r.id === Number(link.dataset.leave));
          if (!window.linkedLeave) { fail(body, new Error("Bağlantılı izin ekranı yüklenmedi.")); return; }
          body.closest("dialog").close();
          const cells = [];
          for (let day = Date.parse(`${item.start_date}T12:00:00Z`), end = Date.parse(`${item.end_date}T12:00:00Z`); day <= end; day += 86400000) {
            cells.push({ employee_id: item.employee_id, date: new Date(day).toISOString().slice(0, 10) });
          }
          window.linkedLeave.open({ cells });
          return;
        }
        const button = event.target.closest("[data-review]");
        if (!button || !confirm(button.dataset.decision === "accepted" ? "Talep kabul edilsin mi? Henüz çizelgeye işlenmez." : "Talep reddedilsin mi?")) return;
        button.disabled = true;
        try {
          await call("/api/access/requests/review", "POST", { id: Number(button.dataset.review), decision: button.dataset.decision });
          data.requests = (await request("/api/access/requests")).requests; render();
        } catch (error) { fail(body, error); button.disabled = false; }
      });
    } catch (error) { fail(body, error); }
  }

  async function notifications() {
    const body = dialog("access-notifications", "Bildirimler", "Okundu işareti yasal tebligat değildir.");
    try {
      const data = await request("/api/access/notifications");
      const render = () => {
        body.innerHTML = `<p data-error role="alert"></p>${data.notifications.length ? `<table class="extras-table"><thead><tr><th>Bildirim</th><th>Durum</th><th></th></tr></thead><tbody>${data.notifications.map(n =>
          `<tr><td>${e(n.message)}<br><small>${new Date(n.created_at * 1000).toLocaleString("tr-TR")}</small></td><td>${n.read_at ? "Okundu" : "Teslim edildi · okunmadı"}</td><td>${n.read_at ? "" : `<button class="button button-light" style="${buttonStyle}" data-read="${n.id}">Okundu işaretle</button>`}</td></tr>`).join("")}</tbody></table>` : "<p>Bildirim yok.</p>"}`;
      };
      render();
      body.addEventListener("click", async event => {
        const button = event.target.closest("[data-read]");
        if (!button) return;
        button.disabled = true;
        try {
          await call("/api/access/notifications/read", "POST", { id: Number(button.dataset.read) });
          data.notifications = (await request("/api/access/notifications")).notifications; render();
        } catch (error) { fail(body, error); button.disabled = false; }
      });
    } catch (error) { fail(body, error); }
  }
  EXTRAS_MENU.push(["Özel yetkiler", permissions], ["Personel bağlantıları", links],
    ["Personel talepleri", reviews], ["Bildirimler", notifications]);
})();
