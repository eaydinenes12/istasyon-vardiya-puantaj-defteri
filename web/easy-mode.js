"use strict";

(() => {
  // Keep the original controls and event handlers; only their grouping changes.
  document.body.classList.add("easy-mode");
  const topbar = document.querySelector(".topbar");
  const nav = document.querySelector(".main-nav");
  const saveState = document.querySelector("#save-state");
  const brand = document.querySelector(".brand");
  brand.removeAttribute("href");
  brand.setAttribute("aria-label", "Vardiya Planı ve Puantaj — Kolay Kullanım");
  brand.title = "Vardiya Planı ve Puantaj — Kolay Kullanım";
  document.title = "Vardiya Planı ve Puantaj — Kolay Kullanım";
  const navLabels = {
    schedule: "Vardiya planla",
    attendance: "Puantajı kontrol et",
    archive: "Kayıtlı aylar",
    templates: "Tablo şablonları",
  };
  nav.querySelectorAll("[data-view]").forEach((button) => {
    button.textContent = navLabels[button.dataset.view] || button.textContent;
  });
  const toolsButton = document.querySelector("#tools-menu-button");
  if (toolsButton) toolsButton.textContent = "Tüm araçlar ▾";

  const notice = document.createElement("p");
  notice.className = "easy-isolation-note";
  notice.textContent = "Kayıt yeri: VardiyaPuantaj.exe'nin yanındaki data klasörü";
  notice.title = "Tüm vardiya, puantaj ve ayar kayıtları VardiyaPuantaj.exe ile aynı klasördeki data klasöründe saklanır.";
  document.querySelector("main").append(notice);

  function button(label, action, className = "button button-outline") {
    const node = document.createElement("button");
    node.type = "button";
    node.className = className;
    node.textContent = label;
    node.addEventListener("click", action);
    return node;
  }

  function focusSchedule() {
    const target = document.querySelector("#schedule-table td[data-shift-day]")
      || document.querySelector("#schedule-table select[data-action='person-slot']");
    if (target) {
      guide.open = false;
      target.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
      target.focus();
    } else {
      toast("Önce personel ekleyin ve çizelgenin ad soyad alanından personeli seçin.", true);
    }
  }

  const toolsMenu = document.querySelector("#tools-menu");
  if (toolsMenu) {
    const groupFlyout = document.createElement("div");
    groupFlyout.className = "tools-menu easy-tool-flyout";
    groupFlyout.setAttribute("role", "menu");
    groupFlyout.hidden = true;
    document.body.append(groupFlyout);
    let activeGroup = null;
    const closeGroupFlyout = (restoreFocus = false) => {
      groupFlyout.hidden = true;
      activeGroup?.querySelector(".easy-tool-heading").setAttribute("aria-expanded", "false");
      if (restoreFocus) activeGroup?.querySelector(".easy-tool-heading").focus();
      activeGroup = null;
    };
    const openGroupFlyout = (group, reveal = null) => {
      closeGroupFlyout();
      const heading = group.querySelector(".easy-tool-heading");
      groupFlyout.replaceChildren();
      const groupLabel = heading.textContent.trim();
      if (groupLabel !== "Ana sayfalar") {
        const hubItem = button(`${groupLabel} sayfasını aç`, () => {
          closeGroupFlyout();
          toolsMenu.hidden = true;
          toolsButton.setAttribute("aria-expanded", "false");
          if (window.easyHubs) window.easyHubs.openGroup(groupLabel);
          else toast("Başlık sayfası henüz hazır değil. Birkaç saniye sonra yeniden deneyin.", true);
        }, "easy-flyout-hub");
        hubItem.setAttribute("role", "menuitem");
        groupFlyout.append(hubItem);
      }
      let revealed = null;
      for (const original of group.querySelectorAll(".easy-tool-items button")) {
        const item = button(original.textContent, () => {
          closeGroupFlyout();
          toolsMenu.hidden = true;
          toolsButton.setAttribute("aria-expanded", "false");
          toolsButton.focus();
          original.click();
        }, "");
        item.setAttribute("role", "menuitem");
        item.disabled = original.disabled;
        item.hidden = original.hidden;
        groupFlyout.append(item);
        if (original === reveal) revealed = item;
      }
      activeGroup = group;
      heading.setAttribute("aria-expanded", "true");
      groupFlyout.setAttribute("aria-labelledby", heading.id);
      groupFlyout.hidden = false;
      const rect = heading.getBoundingClientRect();
      const width = groupFlyout.getBoundingClientRect().width;
      const height = Math.min(groupFlyout.scrollHeight, window.innerHeight - 16);
      const right = rect.right + 4;
      const left = right + width <= window.innerWidth - 8
        ? right : Math.max(8, rect.left - width - 4);
      const top = Math.max(8, Math.min(rect.top, window.innerHeight - height - 8));
      groupFlyout.style.left = `${left}px`;
      groupFlyout.style.top = `${top}px`;
      groupFlyout.style.maxHeight = `${Math.max(32, window.innerHeight - top - 8)}px`;
      (revealed || groupFlyout.querySelector("button:not(:disabled):not([hidden])"))?.focus();
    };
    toolsMenu.addEventListener("easy-reveal-tool", (event) => {
      const original = event.target;
      const group = original.closest(".easy-tool-group");
      if (group && !original.disabled && !original.hidden) {
        group.querySelector(".easy-tool-heading").scrollIntoView({ block: "nearest" });
        openGroupFlyout(group, original);
      }
    });
    groupFlyout.addEventListener("keydown", (event) => {
      const items = [...groupFlyout.querySelectorAll("button:not(:disabled):not([hidden])")];
      const index = items.indexOf(document.activeElement);
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const target = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
          : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items[target]?.focus();
      } else if (event.key === "Escape" || event.key === "ArrowLeft" || event.key === "Tab") {
        if (event.key !== "Tab") event.preventDefault();
        closeGroupFlyout(event.key !== "Tab");
      }
    });
    document.addEventListener("click", (event) => {
      if (!groupFlyout.contains(event.target) && !event.target.closest(".easy-tool-heading")) closeGroupFlyout();
    });
    toolsButton.addEventListener("click", () => closeGroupFlyout());
    window.addEventListener("resize", () => closeGroupFlyout());

    const ringAction = (method, ...args) => () => {
      const api = window.easyMenu;
      if (!api || typeof api[method] !== "function") {
        throw new Error("Kısayol ayarları henüz hazır değil. Birkaç saniye sonra yeniden deneyin.");
      }
      return api[method](...args);
    };
    EXTRAS_MENU.push(
      ["Kısayol halkasını düzenle", ringAction("openRingEditor")],
      ["Kısayol halkasını aç / kapat", ringAction("toggleRing")],
      ["Kısayol halkasını büyüt", ringAction("resizeRing", 1)],
      ["Kısayol halkasını küçült", ringAction("resizeRing", -1)],
    );
    const registered = new Set([...toolsMenu.querySelectorAll("[data-tool]")].map((node) => Number(node.dataset.tool)));
    EXTRAS_MENU.forEach(([label, action], index) => {
      if (label === "-" || typeof action !== "function" || registered.has(index)) return;
      const entry = document.createElement("button");
      entry.type = "button";
      entry.textContent = label;
      entry.dataset.tool = String(index);
      entry.setAttribute("role", "menuitem");
      toolsMenu.append(entry);
    });
    const categories = [
      ["Ana sayfalar", []],
      ["Personel", ["Personel", "İzin talep", "Bağlantılı izin", "Yıllık izin", "Rapor (R)", "Yetkinlik ve izin", "Bölümler arası ödünç", "Kişisel vardiya kartı"]],
      ["Vardiya planlama", ["Bugünün özeti", "Yeni ay", "Otomatik vardiya", "Vardiya döngüsü", "Vardiya değişimi", "Alternatif senaryolar", "Asgari kadro", "Tatil takvimi", "Yayınlanan plan farkı", "Görev panosu"]],
      ["Puantaj ve bordro", ["Ay sonu kontrol", "PDKS", "Puantaj", "SGK", "Bordro ekleri", "Geçmiş puantaj"]],
      ["Raporlar ve çıktılar", ["Adil dağılım", "Denkleştirme", "Devamsızlık", "Tahmini personel", "Grafikler", "Yönetici özeti", "Yazdırma", "Vardiya programı PDF", "Ay sonu paketi", "Excel çıktı", "E-posta"]],
      ["Tablo ve görünüm", ["Toplu giriş çalışma", "Örnek tablo", "Çalışma düzenleri", "Kaydetmeden önce", "Bölümler ve satırlar", "Vardiya türleri", "Şablon kitaplığı", "Biçimi şablona", "Görünüm:", "Sıkı / rahat"]],
      ["Yedekleme ve veri", ["Güvenli veri aktarımı", "Seçili yedek kurtarma", "Çevrimdışı çalışma paketi", "Yedeği incele", "Bulut yedeği", "Veri sağlığı", "Ayarları dışa", "Örnek veri"]],
      ["Yönetim ve kontroller", ["Operasyon merkezi", "Kullanıcılar", "Kayıt kilidi", "Şubeler", "Telefondan", "Yasal uyum", "Uyarı", "Eksik / hatalı"]],
      ["Yardım ve güncelleme", ["Kullanım ve hız", "Göreve göre yönlendirme", "Risksiz örnek", "Klavye kısayolları", "Başlangıç rehberi", "Uygulama bilgisi", "Uygulama güncelleme", "Yenilikler", "Sorun raporu"]],
      ["Kısayollar", ["Kısayol halkası"]],
      ["Diğer araçlar", []],
    ];
    const grouped = categories.map(() => []);
    nav.querySelectorAll("[data-view]").forEach((tab) => {
      const entry = button(tab.textContent, () => {
        toolsMenu.hidden = true;
        toolsButton.setAttribute("aria-expanded", "false");
        tab.click();
        (tab.hidden ? toolsButton : tab).focus();
      }, "");
      entry.dataset.easyView = tab.dataset.view;
      entry.setAttribute("role", "menuitem");
      grouped[0].push(entry);
    });
    for (const item of toolsMenu.querySelectorAll("[data-tool]")) {
      const index = categories.findIndex(([, prefixes]) =>
        prefixes.some((prefix) => item.textContent.startsWith(prefix)));
      grouped[index < 0 ? categories.length - 1 : index].push(item);
    }
    const groups = [];
    function setGroupOpen(group, open) {
      group.querySelector(".easy-tool-items").hidden = true;
      groups.forEach((other) => {
        other.querySelector(".easy-tool-heading").setAttribute("aria-expanded", "false");
      });
      if (open) openGroupFlyout(group);
      else closeGroupFlyout();
    }
    categories.forEach(([label], index) => {
      if (!grouped[index].length) return;
      const group = document.createElement("div");
      group.className = "easy-tool-group";
      group.setAttribute("role", "group");
      group.setAttribute("aria-labelledby", `easy-tool-heading-${index}`);
      const heading = button(label, () => {
        const open = heading.getAttribute("aria-expanded") !== "true";
        groups.forEach((other) => setGroupOpen(other, false));
        setGroupOpen(group, open);
      }, "easy-tool-heading");
      heading.id = `easy-tool-heading-${index}`;
      heading.setAttribute("role", "menuitem");
      heading.setAttribute("aria-expanded", "false");
      heading.setAttribute("aria-controls", `easy-tool-items-${index}`);
      const items = document.createElement("div");
      items.id = `easy-tool-items-${index}`;
      items.className = "easy-tool-items";
      items.hidden = true;
      items.append(...grouped[index]);
      group.append(heading, items);
      groups.push(group);
    });
    toolsMenu.replaceChildren(...groups);
    toolsMenu.addEventListener("keydown", (event) => {
      if (event.target.matches("input, textarea")) return;
      const active = document.activeElement;
      const group = active.closest(".easy-tool-group");
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        const items = [...toolsMenu.querySelectorAll("button, summary")]
          .filter((item) => !item.disabled && item.getClientRects().length);
        const index = items.indexOf(active);
        const target = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
          : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items[target]?.focus();
      } else if (group && event.key === "ArrowLeft") {
        event.preventDefault();
        event.stopImmediatePropagation();
        setGroupOpen(group, false);
        group.querySelector(".easy-tool-heading").focus();
      } else if (group && event.key === "ArrowRight") {
        event.preventDefault();
        event.stopImmediatePropagation();
        setGroupOpen(group, true);
        groupFlyout.querySelector("button:not(:disabled):not([hidden])")?.focus();
      }
    }, true);
  }

  for (const kind of ["schedule", "attendance"]) {
    const view = document.querySelector(`#${kind}-view`);
    const ribbon = view.querySelector(".control-strip");
    const title = view.querySelector("h1");
    title.classList.add("sr-only");
    title.textContent = kind === "schedule" ? "Bu haftanın vardiyasını planlayın" : "Puantajı kontrol edin";
    const heading = document.createElement("div");
    heading.className = "easy-heading";
    title.before(heading);
    heading.append(title);
    const hint = document.createElement("p");
    hint.textContent = kind === "schedule"
      ? "Personeli ekleyin, çizelgede adını seçin ve gün kutularına tıklayarak vardiyasını girin."
      : "Kaydedilmiş vardiyalardan hesaplanan kodları kontrol edin. Elle yaptığınız düzeltmeleri ayrıca kaydedin.";
    heading.append(hint);

    const more = document.createElement("details");
    more.className = "easy-more";
    const summary = document.createElement("summary");
    summary.textContent = "İşlemler";
    const content = document.createElement("div");
    content.className = "easy-more-content";
    more.append(summary, content);
    more.addEventListener("toggle", () => {
      if (!more.open) more.querySelectorAll("details[open]").forEach((panel) => { panel.open = false; });
    });
    const primaryIds = new Set([
      `save-${kind}`, `open-${kind}-check`, `export-${kind}`,
      `undo-${kind}`, `redo-${kind}`,
    ]);
    for (const control of [...ribbon.children]) {
      if (control === nav || control === saveState || control.classList.contains("control-spacer")) continue;
      if (primaryIds.has(control.id) || control.classList.contains("period-panel")) continue;
      content.append(control);
    }
    if (kind === "attendance") content.prepend(hint);
    ribbon.append(more);
    const save = document.querySelector(`#save-${kind}`);
    save.textContent = "Kaydet";
    save.setAttribute("aria-label", kind === "schedule" ? "Vardiyayı kaydet" : "Puantaj düzeltmelerini kaydet");
    const check = document.querySelector(`#open-${kind}-check`);
    check.textContent = "Kontrol";
    check.setAttribute("aria-label", kind === "schedule" ? "Vardiya eksiklerini kontrol et" : "Puantajı kontrol et");
    const excel = document.querySelector(`#export-${kind}`);
    excel.textContent = "Excel";
    excel.setAttribute("aria-label", kind === "schedule" ? "Vardiyayı Excel olarak kaydet" : "Puantajı Excel olarak kaydet");
    document.querySelector(`#undo-${kind}`).textContent = "↶";
    document.querySelector(`#redo-${kind}`).textContent = "↷";
    const quickSummary = ribbon.querySelector(".quick-panel > summary");
    if (quickSummary) quickSummary.textContent = "Vardiya seçenekleri";
  }

  const scheduleView = document.querySelector("#schedule-view");
  const steps = document.createElement("nav");
  steps.className = "easy-steps";
  steps.setAttribute("aria-label", "Vardiya hazırlama adımları");
  steps.append(
    button("1 · Personel ekle", () => {
      guide.open = true;
      document.querySelector("#new-person-name").focus();
    }),
    button("2 · Vardiya gir", focusSchedule),
    button("3 · Kontrol et", () => document.querySelector("#open-schedule-check").click()),
  );

  const workspace = document.createElement("div");
  workspace.className = "easy-workspace";
  const frame = scheduleView.querySelector(".table-frame");
  frame.before(workspace);
  const guide = document.createElement("details");
  guide.className = "easy-guide";
  guide.setAttribute("aria-label", "Vardiya hazırlama yardımı");
  guide.innerHTML = `<summary title="Personel ekle ve kullanım yardımı">Personel</summary>
    <div class="easy-guide-content"><div>
    <h2>Personel ekleyin</h2>
    <p>Adı yazıp listeye ekleyin. Ardından çizelgedeki boş ad soyad alanında bu kişiyi seçin.</p>
    <div class="easy-person-form"></div>
    <p class="easy-person-count" role="status"></p>
    </div><div class="easy-guide-instructions"><h2>Günü planlayın</h2>
    <p>Personelin gün kutusuna tıklayın. Açılan pencerede çalışma saatini veya izin türünü seçin.</p>
    <p><strong>H</strong> hafta tatili, <strong>S</strong> yıllık izin, <strong>R</strong> rapor demektir.</p>
    <p class="easy-save-help" role="status" aria-live="polite"></p>
    </div></div>`;
  guide.querySelector(".easy-guide-instructions").prepend(scheduleView.querySelector(".easy-heading > p"));
  guide.querySelector(".easy-guide-instructions").prepend(steps);
  guide.querySelector(".easy-person-form").append(document.querySelector("#add-person-form"));
  guide.querySelector(".easy-person-count").after(button("Çizelgede personel seç", () => {
    const target = document.querySelector("#schedule-table select[data-action='person-slot']")
      || document.querySelector("#schedule-table select[data-action='person']");
    if (target) {
      guide.open = false;
      target.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
      target.focus();
    } else {
      toast("Çizelge henüz yüklenmedi. Yükleme hatası varsa Tüm araçlar menüsünden durumu kontrol edin.", true);
    }
  }));
  const scheduleRibbon = scheduleView.querySelector(".control-strip");
  scheduleRibbon.prepend(guide);
  guide.addEventListener("toggle", () => {
    if (guide.open) {
      scheduleRibbon.querySelectorAll("details[open]").forEach((panel) => {
        if (panel !== guide && !panel.contains(guide)) panel.open = false;
      });
    }
  });
  document.addEventListener("click", (event) => {
    if (!guide.contains(event.target)) guide.open = false;
  });
  guide.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      guide.open = false;
      guide.querySelector("summary").focus();
    }
  });
  workspace.append(frame);

  const filters = scheduleView.querySelector(".schedule-filters");
  const filterPanel = document.createElement("details");
  filterPanel.className = "easy-filters";
  const filterSummary = document.createElement("summary");
  filterSummary.textContent = "Personel ara ve çizelgeyi filtrele";
  filterPanel.append(filterSummary, filters);
  scheduleView.querySelector(".easy-more-content").append(filterPanel);

  const expand = button("Tüm işlemleri göster", async () => {
    if (ribbonBusy) return;
    const panels = [...document.querySelectorAll(".easy-more")];
    const open = panels.some((panel) => !panel.open);
    if (open && Object.values(state.uiSettings.easy_ribbons || {}).some((visible) => !visible)) {
      ribbonBusy = true;
      applyRibbonPreferences();
      try {
        await saveUiSettings({ easy_ribbons: { schedule: true, attendance: true } });
      } catch (error) {
        toast(`Araç şeritleri gösterilemedi: ${error.message}`, true);
        return;
      } finally {
        ribbonBusy = false;
        applyRibbonPreferences();
      }
    }
    panels.forEach((panel) => { panel.open = open; });
    expand.textContent = open ? "İşlemleri sadeleştir" : "Tüm işlemleri göster";
  }, "button button-light easy-expand");
  const help = document.querySelector("#open-guide");
  help.textContent = "Yardım";
  help.classList.add("easy-help");
  help.removeAttribute("title");
  const headerOptions = document.createElement("details");
  headerOptions.className = "easy-header-options";
  headerOptions.innerHTML = '<summary>Diğer</summary><div class="easy-header-content"></div>';
  const headerContent = headerOptions.querySelector(".easy-header-content");
  headerContent.append(help, expand);
  topbar.append(headerOptions);
  document.addEventListener("click", (event) => {
    if (!headerOptions.contains(event.target)) headerOptions.open = false;
  });
  headerOptions.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      headerOptions.open = false;
      headerOptions.querySelector("summary").focus();
    }
  });

  const ribbonToggles = new Map();
  let ribbonBusy = false;
  function applyRibbonPreferences() {
    for (const [kind, toggle] of ribbonToggles) {
      const view = document.querySelector(`#${kind}-view`);
      const visible = state.uiSettings.easy_ribbons?.[kind] !== false;
      if (!visible && !view.classList.contains("easy-ribbon-collapsed")) {
        view.querySelectorAll("details[open]").forEach((panel) => { panel.open = false; });
      }
      view.classList.toggle("easy-ribbon-collapsed", !visible);
      toggle.textContent = visible ? "Şeridi gizle ▴" : "Araç şeridini göster ▾";
      toggle.setAttribute("aria-expanded", String(visible));
      toggle.setAttribute("aria-label", `${kind === "schedule" ? "Vardiya" : "Puantaj"} araç şeridini ${visible ? "gizle" : "göster"}`);
      toggle.disabled = ribbonBusy || !state.uiSettings.easy_ribbons;
    }
    expand.disabled = ribbonBusy || !state.uiSettings.easy_ribbons;
    expand.textContent = [...document.querySelectorAll(".easy-more")].every((panel) => panel.open)
      ? "İşlemleri sadeleştir" : "Tüm işlemleri göster";
  }
  for (const kind of ["schedule", "attendance"]) {
    const ribbon = document.querySelector(`#${kind}-view .control-strip`);
    ribbon.id ||= `easy-${kind}-ribbon`;
    const toggle = button("", async () => {
      if (ribbonBusy) return;
      const ribbons = { ...state.uiSettings.easy_ribbons };
      ribbons[kind] = !ribbons[kind];
      ribbonBusy = true;
      applyRibbonPreferences();
      try {
        await saveUiSettings({ easy_ribbons: ribbons });
      } catch (error) {
        toast(`Araç şeridi tercihi kaydedilemedi: ${error.message}`, true);
      } finally {
        ribbonBusy = false;
        applyRibbonPreferences();
      }
    }, "button button-light easy-ribbon-toggle");
    toggle.setAttribute("aria-controls", ribbon.id);
    ribbon.append(toggle);
    ribbonToggles.set(kind, toggle);
  }
  document.addEventListener("ui-settings-changed", applyRibbonPreferences);
  applyRibbonPreferences();

  function setText(node, text) {
    if (node.textContent !== text) node.textContent = text;
  }

  const attendanceDateFormatter = new Intl.DateTimeFormat("tr-TR", {
    day: "2-digit", month: "long", year: "numeric", weekday: "long",
  });

  function refresh() {
    // switchView moves these into the active ribbon; keep one real navigation at the top.
    if (nav.parentElement !== topbar) topbar.insertBefore(nav, headerOptions);
    if (saveState.parentElement !== topbar) topbar.insertBefore(saveState, headerOptions);
    const alertBell = document.querySelector("#alert-bell");
    if (alertBell && alertBell.parentElement !== topbar) topbar.insertBefore(alertBell, headerOptions);
    for (const selector of ["#month-status", "#user-badge"]) {
      const item = document.querySelector(selector);
      if (item && item.parentElement !== headerContent) headerContent.prepend(item);
    }
    for (const period of document.querySelectorAll(".period-panel > summary")) {
      const label = period.textContent.trim();
      if (period.title !== label) period.title = label;
    }
    for (const kind of ["schedule", "attendance"]) {
      const preview = document.querySelector(`#preview-${kind}`);
      const content = document.querySelector(`#${kind}-view .easy-more-content`);
      if (preview && preview.parentElement !== content) content.append(preview);
    }
    if (state.attendance) {
      const settings = sheetSettings("attendance");
      for (const cell of document.querySelectorAll('#attendance-table td[data-source-row="3"]')) {
        const day = state.attendance.dates[Number(cell.dataset.sourceCol) - 3];
        const label = cell.querySelector(".rotated-cell-value");
        const custom = settings.cells[`${cell.dataset.cellRow}:${cell.dataset.cellCol}`] || {};
        const standard = Boolean(day && label && settings.row_heights?.[cell.dataset.cellRow] === undefined && !Object.keys(custom).some((key) =>
          !["color", "font_color", "border"].includes(key)));
        if (cell.classList.contains("easy-attendance-date") !== standard) {
          cell.classList.toggle("easy-attendance-date", standard);
        }
        if (standard) {
          const parts = Object.fromEntries(attendanceDateFormatter.formatToParts(new Date(`${day}T12:00:00`))
            .map((part) => [part.type, part.value]));
          setText(label, `${parts.day}.${parts.month}.${parts.year} ${parts.weekday}`.toLocaleUpperCase("tr-TR"));
        }
      }
    }
    const count = state.people.filter((person) => person.active).length;
    setText(guide.querySelector(".easy-person-count"), `${count} aktif personel kayıtlı.`);
    const dirty = state.scheduleDirty;
    setText(guide.querySelector(".easy-save-help"), dirty
      ? "Kaydedilmemiş değişiklik var. Bitirince “Kaydet” düğmesine basın."
      : "Vardiya düzenlemeleri kaydedildikten sonra puantaja yansır.");
    nav.querySelectorAll("[data-view]").forEach((tab) => {
      if (tab.dataset.view === state.view) tab.setAttribute("aria-current", "page");
      else if (tab.hasAttribute("aria-current")) tab.removeAttribute("aria-current");
    });
  }

  let pending = false;
  const observer = new MutationObserver(() => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; refresh(); });
  });
  document.querySelectorAll(".control-strip, #schedule-table, #attendance-table, #save-state, #save-schedule").forEach((node) => {
    observer.observe(node, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "class"] });
  });
  observer.observe(topbar, { childList: true, subtree: true });
  refresh();
})();
