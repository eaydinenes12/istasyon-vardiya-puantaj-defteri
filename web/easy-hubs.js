"use strict";

(() => {
  // Every "Tüm araçlar" heading becomes its own in-app page with a collapsible ribbon.
  // Tools launched from a page open inside that page instead of as a blocking window.
  const toolsMenu = document.querySelector("#tools-menu");
  const main = document.querySelector("main");
  const topbar = document.querySelector(".topbar");
  if (!toolsMenu || !main || typeof EXTRAS_MENU === "undefined") return;

  const SVG = {
    calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    users: '<circle cx="9" cy="8.5" r="3.2"/><path d="M3 19.5c.6-3.3 3-5 6-5s5.4 1.7 6 5"/><path d="M15.5 5.6a3 3 0 0 1 0 5.8M17.5 14.8c1.8.6 3 2.2 3.4 4.7"/>',
    user: '<circle cx="12" cy="8" r="3.6"/><path d="M5 20c.8-3.8 3.6-5.8 7-5.8s6.2 2 7 5.8"/>',
    file: '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4M9 12h6M9 16h6"/>',
    chart: '<path d="M4 20h16"/><path d="M7 16v-4M12 16V7M17 16v-7"/>',
    printer: '<path d="M7 9V4h10v5"/><rect x="4" y="9" width="16" height="7" rx="1.5"/><path d="M7 14h10v6H7z"/>',
    download: '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5"/><path d="M5 19.5h14"/>',
    mail: '<rect x="3.5" y="5.5" width="17" height="13" rx="2"/><path d="m4 7 8 6 8-6"/>',
    shield: '<path d="M12 3.5 19 6v5.5c0 4.4-3 7.6-7 9-4-1.4-7-4.6-7-9V6z"/><path d="m9 12 2 2 4-4"/>',
    database: '<ellipse cx="12" cy="6" rx="7" ry="2.8"/><path d="M5 6v12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V6M5 12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8"/>',
    settings: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    table: '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><path d="M3.5 9.5h17M3.5 14.5h17M9.5 9.5v10"/>',
    sparkle: '<path d="M12 3.5 13.8 10l6.7 2-6.7 2L12 20.5 10.2 14l-6.7-2 6.7-2z"/>',
    help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.4a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.1-2.4 3.7"/><path d="M12 17h.01"/>',
    refresh: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4h-4"/>',
    star: '<path d="m12 3.8 2.5 5.2 5.7.7-4.2 3.9 1.1 5.6L12 16.4l-5.1 2.8L8 13.6 3.8 9.7l5.7-.7z"/>',
    archive: '<rect x="3.5" y="4.5" width="17" height="4.5" rx="1"/><path d="M5 9v10.5h14V9M10 13h4"/>',
    leave: '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4M9 15l2 2 4-4"/>',
    alert: '<path d="M12 4 21 19.5H3z"/><path d="M12 10v4M12 17h.01"/>',
    building: '<path d="M5 20.5V4.5h9v16M14 9.5h5v11M3 20.5h18M8 8h3M8 12h3M8 16h3"/>',
    phone: '<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 17.5h2"/>',
    money: '<rect x="3" y="6.5" width="18" height="11" rx="1.5"/><circle cx="12" cy="12" r="2.6"/><path d="M6.5 9.5v5M17.5 9.5v5"/>',
    layers: '<path d="m12 4 8.5 4.5L12 13 3.5 8.5z"/><path d="m3.5 12.5 8.5 4.5 8.5-4.5M3.5 16.5 12 21l8.5-4.5"/>',
    home: '<path d="M4 11 12 4l8 7"/><path d="M6 9.5v10h12v-10M10 19.5v-5h4v5"/>',
    grid: '<rect x="4" y="4" width="6.5" height="6.5" rx="1"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    swap: '<path d="M4 8h14M14.5 4.5 18 8l-3.5 3.5M20 16H6M9.5 12.5 6 16l3.5 3.5"/>',
    lock: '<rect x="5" y="10.5" width="14" height="10" rx="1.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
    check: '<path d="m4 7 2 2 3.5-3.5M4 15l2 2 3.5-3.5"/><path d="M13 7.5h7M13 15.5h7"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
    package: '<path d="M12 3.5 20 7.5v9L12 20.5 4 16.5v-9z"/><path d="M4 7.5 12 11.5l8-4M12 11.5v9"/>',
    ring: '<circle cx="12" cy="12" r="7.5"/><circle cx="12" cy="4.5" r="1.8"/><circle cx="18.5" cy="15.8" r="1.8"/><circle cx="5.5" cy="15.8" r="1.8"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    back: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
    chevron: '<path d="m6 15 6-6 6 6"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    planner: '<rect x="5" y="4.5" width="14" height="16" rx="2"/><path d="M9 3.5h6v3H9z"/><path d="m8.5 11 1.5 1.5 2.5-2.5M8.5 16l1.5 1.5 2.5-2.5M14.5 11.5h1.5M14.5 16.5h1.5"/>',
    palette: '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.2 0 1.8-.8 1.8-1.7 0-1.3-1-1.6-1-2.6 0-.9.7-1.5 1.6-1.5h2.1a4 4 0 0 0 4-4c0-4.1-3.8-7.2-8.5-7.2z"/><circle cx="7.8" cy="11" r="1.1"/><circle cx="10.5" cy="7.5" r="1.1"/><circle cx="15" cy="7.8" r="1.1"/>',
  };
  // Hub pages use different icons from the main views they sit next to (Vardiya planla / Puantaj / Tablo şablonları).
  const HUB_INFO = {
    "Personel": { short: "Personel", icon: "users", text: "Personel kartları, izinler, raporlar ve kişisel vardiya kartları." },
    "Vardiya planlama": { short: "Plan araçları", icon: "planner", text: "Yeni ay, otomatik vardiya, değişimler, tatil takvimi ve plan kontrolleri." },
    "Puantaj ve bordro": { short: "Bordro", icon: "money", text: "PDKS aktarımı, puantaj kontrolleri, SGK ve bordro ekleri." },
    "Raporlar ve çıktılar": { short: "Raporlar", icon: "chart", text: "Raporlar, grafikler, PDF ve Excel çıktıları, e-posta gönderimi." },
    "Tablo ve görünüm": { short: "Görünüm", icon: "palette", text: "Tablo düzeni, şablonlar, vardiya türleri ve görünüm ayarları." },
    "Yedekleme ve veri": { short: "Veri", icon: "database", text: "Yedekler, güvenli veri aktarımı, veri sağlığı ve çevrimdışı paketler." },
    "Yönetim ve kontroller": { short: "Yönetim", icon: "shield", text: "Kullanıcılar, kayıt kilidi, şubeler, uyarılar ve yasal uyum." },
    "Yardım ve güncelleme": { short: "Yardım", icon: "help", text: "Rehberler, klavye kısayolları, güncellemeler ve sorun bildirimi." },
    "Kısayollar": { short: "Kısayollar", icon: "star", text: "Amblem kısayol halkası ve üst menü şeridi tercihleri." },
    "Diğer araçlar": { short: "Diğer", icon: "grid", text: "Başka bir başlığa girmeyen yardımcı araçlar." },
  };
  const VIEW_ICONS = { schedule: "calendar", attendance: "clock", archive: "archive", templates: "table" };
  const ICON_RULES = [
    [/ay sonu kontrol/, "check"],
    [/klavye|rehber|yardım|başlangıç|göreve göre|kullanım ve hız|sorun raporu|yenilikler/, "help"],
    [/kısayol/, "ring"],
    [/menüyü/, "edit"],
    [/personel ana sayfa|kişisel/, "user"],
    [/personel|kadro|yetkinlik/, "users"],
    [/izin|rapor \(r\)/, "leave"],
    [/otomatik|döngü/, "sparkle"],
    [/e-posta|posta/, "mail"],
    [/yönetici özeti|grafik|adil|denkleştirme|devamsızlık|tahmini/, "chart"],
    [/tutanak|belge/, "file"],
    [/zıp|zip|paket/, "package"],
    [/pdf|yazdır/, "printer"],
    [/excel|dışa|içe aktar|aktarım/, "download"],
    [/yedek|güvenli|kurtarma/, "shield"],
    [/veri/, "database"],
    [/değişim|ödünç/, "swap"],
    [/değişiklik|önizleme|fark/, "eye"],
    [/senaryo|alternatif/, "layers"],
    [/tatil|yeni ay|vardiya|bugünün/, "calendar"],
    [/puantaj|pdks/, "clock"],
    [/sgk|bordro/, "money"],
    [/şube/, "building"],
    [/telefon/, "phone"],
    [/kilit|kullanıcı/, "lock"],
    [/uyarı|eksik|uyum/, "alert"],
    [/güncelleme/, "refresh"],
    [/görev/, "file"],
    [/tablo|şablon|görünüm|toplu giriş|sıkı|biçim|düzen/, "table"],
    [/türleri|bölümler|ayar|operasyon/, "settings"],
  ];
  const TABLE_VIEWS = new Set(["schedule", "attendance"]);
  const TABLE_TOOLS = /^(Biçimi şablona|Kaydetmeden önce|Toplu giriş çalışma|Sıkı \/ rahat)/;
  const NEEDS_TABLE = /önce.*(vardiya|puantaj).*(tablo|sayfa)/i;
  const EXCLUDED = "#live-assistant-dialog, #cell-quick-entry-dialog, .template-editor-dialog, .easy-cell-panel";
  const SHORTCUT = /\s*\(((?:Ctrl|Alt|Shift|F\d{1,2})[^)]*)\)\s*$/;
  const TEXT_INPUT = "textarea, input:not([type=search]):not([type=checkbox]):not([type=radio]):not([type=button])"
    + ":not([type=submit]):not([type=reset]):not([type=range]):not([type=color]):not([type=file]):not([type=hidden])";
  const USAGE_KEY = "vp-easy-usage";
  const RIBBON_KEY = "vp-hub-ribbon";

  const normalize = (text) => String(text ?? "").trim().replace(/\s+/g, " ");
  const plainLabel = (label) => normalize(label).replace(SHORTCUT, "");
  const shortcutOf = (label) => normalize(label).match(SHORTCUT)?.[1] || "";
  const fold = (text) => normalize(text).toLocaleLowerCase("tr-TR")
    .replace(/ç/g, "c").replace(/ğ/g, "g").replace(/ı/g, "i").replace(/ö/g, "o").replace(/ş/g, "s").replace(/ü/g, "u")
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

  function icon(name, className = "easy-icon") {
    return `<svg class="${className}" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor"`
      + ` stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">`
      + `${SVG[name] || SVG.grid}</svg>`;
  }

  function iconFor(value) {
    const text = normalize(value);
    if (text.startsWith("view:")) return VIEW_ICONS[text.slice(5)] || "grid";
    if (text.startsWith("group:")) return HUB_INFO[text.slice(6)]?.icon || "grid";
    const lower = (text.startsWith("tool:") ? text.slice(5) : text).toLocaleLowerCase("tr-TR");
    return ICON_RULES.find(([pattern]) => pattern.test(lower))?.[1] || "grid";
  }

  function readStore(key, fallback) {
    try {
      const value = localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch {
      return fallback;
    }
  }

  function writeStore(key, value) {
    try { localStorage.setItem(key, value); } catch { /* storage may be unavailable */ }
  }

  function usage() {
    try {
      const value = JSON.parse(readStore(USAGE_KEY, "{}"));
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch {
      return {};
    }
  }

  function trackUsage(id) {
    if (!id) return;
    const data = usage();
    data[id] = Math.min(1e6, (Number(data[id]) || 0) + 1);
    writeStore(USAGE_KEY, JSON.stringify(data));
  }

  const hubs = new Map();
  const hubByView = new Map();
  const homes = new WeakMap();
  const bound = new WeakSet();
  const dirty = new WeakSet();
  let pending = null;
  let lastTableView = TABLE_VIEWS.has(state.view) ? state.view : "schedule";
  const nativeShowModal = HTMLDialogElement.prototype.showModal;
  const nativeShow = HTMLDialogElement.prototype.show;

  function openIn(hub) {
    return [...hub.slot.children].find((node) => node instanceof HTMLDialogElement && node.open) || null;
  }

  function anyModalOpen() {
    try {
      return Boolean(document.querySelector("dialog:modal"));
    } catch {
      return [...document.querySelectorAll("dialog[open]")]
        .some((node) => !node.matches(".hub-embedded, .hub-floating, .easy-cell-panel"));
    }
  }

  function dialogTitle(dialog) {
    return normalize(dialog.querySelector(".dialog-heading h2, h2, h1, h3")?.textContent) || "Araç";
  }

  function markCurrent(hub, entry) {
    hub.ribbon.querySelectorAll(".easy-hub-ribbon-item").forEach((node) => {
      if (entry && node.dataset.entryId === entry.key) node.setAttribute("aria-current", "true");
      else node.removeAttribute("aria-current");
    });
  }

  function resetStage(hub) {
    hub.tiles.hidden = false;
    hub.stagebar.hidden = true;
    hub.current.textContent = "";
    hub.section.classList.remove("easy-hub-working");
    markCurrent(hub, null);
  }

  function visible(node) {
    return Boolean(node?.isConnected && node.getClientRects().length);
  }

  function focusHome(hub) {
    const target = visible(hub.launcher) ? hub.launcher : hub.title;
    target.focus({ preventScroll: false });
  }

  function restoreHome(dialog) {
    const home = homes.get(dialog);
    homes.delete(dialog);
    if (!dialog.isConnected) return;
    if (home?.parent?.isConnected) {
      home.parent.insertBefore(dialog, home.next?.parentNode === home.parent ? home.next : null);
    } else {
      document.body.append(dialog);
    }
  }

  function onDialogClose(event) {
    const dialog = event.currentTarget;
    if (dialog.open || !dialog.matches(".hub-embedded, .hub-floating")) return;
    const hub = [...hubs.values()].find((item) => item.slot === dialog.parentElement) || null;
    const hadFocus = !document.activeElement || document.activeElement === document.body
      || dialog.contains(document.activeElement);
    dialog.classList.remove("hub-embedded", "hub-floating");
    dirty.delete(dialog);
    restoreHome(dialog);
    if (!hub) return;
    hub.followUntil = Date.now() + 600;
    if (openIn(hub)) return;
    resetStage(hub);
    if (state.view === hub.view && hadFocus) focusHome(hub);
  }

  function onDialogKeydown(event) {
    const dialog = event.currentTarget;
    if (event.key !== "Escape" || event.defaultPrevented || !dialog.open
        || !dialog.matches(".hub-embedded, .hub-floating")) return;
    event.preventDefault();
    closeDialog(dialog);
  }

  function bindDialog(dialog) {
    if (bound.has(dialog)) return;
    bound.add(dialog);
    dialog.addEventListener("close", onDialogClose);
    dialog.addEventListener("keydown", onDialogKeydown);
    dialog.addEventListener("input", (event) => {
      if (dialog.matches(".hub-embedded, .hub-floating") && event.target.matches?.(TEXT_INPUT)) dirty.add(dialog);
    });
  }

  async function closeDialog(dialog, askDirty = true) {
    if (!dialog.open) return true;
    if (askDirty && dirty.has(dialog)
        && !window.confirm("Açık araçta kaydedilmemiş değişiklikler olabilir. Kapatılsın mı?")) return false;
    if (!dialog.dispatchEvent(new Event("cancel", { cancelable: true }))) {
      toast("Açık araç şu anda kapatılamıyor.", true);
      return false;
    }
    if (!dialog.open) return true;
    const closed = new Promise((resolve) => {
      const timer = setTimeout(resolve, 150);
      dialog.addEventListener("close", () => { clearTimeout(timer); resolve(); }, { once: true });
    });
    dialog.close();
    await closed;
    return true;
  }

  function floatDialog(hub, dialog) {
    dialog.classList.remove("hub-embedded");
    dialog.classList.add("hub-floating");
    document.body.append(dialog);
    resetStage(hub);
  }

  async function releaseStage(hub, leaving = false) {
    const dialog = openIn(hub);
    if (!dialog) return true;
    if (leaving && dialog.contains(document.activeElement)) {
      floatDialog(hub, dialog);
      return true;
    }
    return closeDialog(dialog);
  }

  function targetHub(dialog) {
    const hub = hubByView.get(state.view);
    if (!hub || !hub.rendered || dialog.matches(EXCLUDED)) return null;
    if (dialog.parentElement?.closest("dialog") || anyModalOpen()) return null;
    if (openIn(hub) && openIn(hub) !== dialog) return null;
    const now = Date.now();
    const fresh = pending?.hub === hub && now - pending.at < 10000;
    const chained = [...hub.slot.children].some((node) => node.matches?.("dialog.hub-embedded:not([open])"));
    return fresh || chained || now < hub.followUntil ? hub : null;
  }

  function embed(hub, dialog) {
    if (dialog.parentElement !== hub.slot) {
      homes.set(dialog, { parent: dialog.parentElement, next: dialog.nextSibling });
      hub.slot.append(dialog);
    }
    bindDialog(dialog);
    dirty.delete(dialog);
    dialog.classList.remove("hub-floating");
    dialog.classList.add("hub-embedded");
    const label = pending?.hub === hub ? pending.entry : null;
    pending = null;
    nativeShow.call(dialog);
    hub.tiles.hidden = true;
    hub.stagebar.hidden = false;
    hub.section.classList.add("easy-hub-working");
    hub.current.textContent = dialogTitle(dialog);
    if (label) markCurrent(hub, label);
    const focusable = dialog.querySelector("[autofocus]")
      || [...dialog.querySelectorAll("input:not([type=hidden]):not(:disabled), select:not(:disabled), textarea:not(:disabled),"
        + " button:not(:disabled):not([data-extras-close]), a[href], [tabindex]:not([tabindex='-1'])")]
        .find((node) => node.getClientRects().length);
    (focusable || dialog).focus({ preventScroll: true });
    hub.stagebar.scrollIntoView({ block: "nearest" });
  }

  HTMLDialogElement.prototype.showModal = function hubAwareShowModal(...args) {
    if (this.open && this.matches(".hub-embedded, .hub-floating")) {
      this.scrollIntoView({ block: "nearest" });
      return undefined;
    }
    const hub = targetHub(this);
    if (hub) {
      embed(hub, this);
      return undefined;
    }
    return nativeShowModal.apply(this, args);
  };

  function armPending(hub, entry) {
    const token = {};
    pending = { hub, entry, token, at: Date.now() };
    setTimeout(() => disarm(token), 10000);
    return token;
  }

  function disarm(token) {
    if (token && pending?.token === token) pending = null;
  }

  async function runTool(entry, hub = null) {
    const action = entry.index >= 0 ? EXTRAS_MENU[entry.index]?.[1] : null;
    if (TABLE_TOOLS.test(entry.plain) && !TABLE_VIEWS.has(state.view)) {
      if (!(await switchView(lastTableView))) return false;
      hub = null;
    }
    const token = hub ? armPending(hub, entry) : null;
    try {
      if (typeof action === "function") await action();
      else entry.node.click();
      return true;
    } catch (error) {
      const message = error?.message || String(error);
      if (typeof action === "function" && NEEDS_TABLE.test(message) && !TABLE_VIEWS.has(state.view)) {
        disarm(token);
        if (!(await switchView(lastTableView))) return false;
        try {
          await action();
          return true;
        } catch (retryError) {
          toast(retryError?.message || String(retryError), true);
          return false;
        }
      }
      toast(message, true);
      return false;
    } finally {
      if (token) {
        setTimeout(() => {
          disarm(token);
          if (hub && !openIn(hub)) markCurrent(hub, null);
        }, 1500);
      }
    }
  }

  function collectEntries(hub) {
    const entries = [];
    for (const node of hub.group.querySelectorAll(".easy-tool-items button")) {
      const label = normalize(node.textContent);
      if (node.hidden || !label) continue;
      const index = node.hasAttribute("data-tool") ? Number(node.dataset.tool) : -1;
      entries.push({
        node, label, index,
        key: `${index}:${label}`,
        id: node.dataset.menuId || (index >= 0 ? `tool:${label}` : ""),
        plain: plainLabel(label),
        shortcut: shortcutOf(label),
        advanced: Boolean(node.closest(".easy-advanced-tools")),
        disabled: node.disabled,
        icon: iconFor(label),
      });
    }
    return entries;
  }

  function ringItems() {
    try {
      return window.easyMenu?.ringItems?.() || [];
    } catch {
      return [];
    }
  }

  function applyStars(hub) {
    const items = new Set(ringItems());
    hub.section.querySelectorAll("[data-ring-id]").forEach((star) => {
      const on = items.has(star.dataset.ringId);
      const name = star.dataset.ringLabel;
      star.setAttribute("aria-pressed", String(on));
      star.setAttribute("aria-label", `${name}: ${on ? "kısayol halkasından çıkar" : "kısayol halkasına ekle"}`);
      star.title = on ? "Kısayol halkasından çıkar" : "Kısayol halkasına ekle";
      const text = star.querySelector("span");
      if (text) text.textContent = on ? "Halkada" : "Halkaya ekle";
    });
  }

  function starButton(id, label, withText = false) {
    const star = document.createElement("button");
    star.type = "button";
    star.className = withText ? "easy-hub-star easy-hub-star-wide" : "easy-hub-star";
    star.dataset.ringId = id;
    star.dataset.ringLabel = label;
    star.innerHTML = `${icon("star")}${withText ? "<span>Halkaya ekle</span>" : ""}`;
    star.addEventListener("click", async () => {
      if (!window.easyMenu?.toggleRingItem) {
        toast("Kısayol halkası henüz hazır değil. Birkaç saniye sonra yeniden deneyin.", true);
        return;
      }
      star.disabled = true;
      try {
        await window.easyMenu.toggleRingItem(id);
      } finally {
        star.disabled = false;
        applyStars(hubForNode(star) || { section: star.closest(".easy-hub-view") || document });
      }
    });
    return star;
  }

  function hubForNode(node) {
    const section = node.closest(".easy-hub-view");
    return section ? hubs.get(section.dataset.hub) : null;
  }

  // A tool action may await a dialog that stays open; never let that freeze the page's other tools.
  const LAUNCH_LOCK_MS = 6000;

  async function launch(hub, entry, trigger) {
    if (entry.disabled) return;
    if (hub.launching && Date.now() - hub.launching < LAUNCH_LOCK_MS) return;
    const startedAt = Date.now();
    hub.launching = startedAt;
    try {
      if (!(await releaseStage(hub))) return;
      hub.launcher = trigger;
      markCurrent(hub, entry);
      trackUsage(entry.id);
      await runTool(entry, hub);
    } finally {
      if (hub.launching === startedAt) hub.launching = 0;
    }
  }

  function renderHub(hub) {
    hub.entries = collectEntries(hub);
    hub.rendered = true;
    hub.count.textContent = `${hub.entries.length} araç`;
    hub.ribbon.replaceChildren();
    hub.tiles.replaceChildren();
    hub.entries.forEach((entry, index) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "easy-hub-ribbon-item";
      item.dataset.entryId = entry.key;
      item.title = entry.label;
      item.disabled = entry.disabled;
      item.tabIndex = index === 0 ? 0 : -1;
      item.innerHTML = `${icon(entry.icon)}<span>${escapeHtml(entry.plain)}</span>`;
      item.addEventListener("click", () => launch(hub, entry, item));
      hub.ribbon.append(item);
    });
    const sections = [["Temel araçlar", hub.entries.filter((entry) => !entry.advanced)],
      ["Gelişmiş araçlar", hub.entries.filter((entry) => entry.advanced)]].filter(([, list]) => list.length);
    if (sections.length === 1) sections[0][0] = "Araçlar";
    for (const [title, list] of sections) {
      const block = document.createElement("section");
      block.className = "easy-hub-tile-group";
      const heading = document.createElement("h2");
      heading.textContent = title;
      const grid = document.createElement("ul");
      grid.className = "easy-hub-tile-list";
      grid.setAttribute("role", "list");
      for (const entry of list) {
        const tile = document.createElement("li");
        tile.className = "easy-hub-tile";
        const open = document.createElement("button");
        open.type = "button";
        open.className = "easy-hub-tile-open";
        open.disabled = entry.disabled;
        open.title = entry.label;
        open.innerHTML = `<span class="easy-hub-tile-icon">${icon(entry.icon)}</span>`
          + `<span class="easy-hub-tile-label">${escapeHtml(entry.plain)}</span>`
          + (entry.shortcut ? `<kbd>${escapeHtml(entry.shortcut)}</kbd>` : "");
        open.addEventListener("click", () => launch(hub, entry, open));
        tile.append(open);
        if (entry.id.startsWith("tool:")) tile.append(starButton(entry.id, entry.plain));
        grid.append(tile);
      }
      block.append(heading, grid);
      hub.tiles.append(block);
    }
    if (!hub.entries.length) {
      const empty = document.createElement("p");
      empty.className = "easy-hub-empty";
      empty.textContent = "Bu başlıkta kullanılabilir araç yok.";
      hub.tiles.append(empty);
    }
    applyRibbon(hub);
    applyStars(hub);
    if (!openIn(hub)) resetStage(hub);
  }

  function ribbonOpen() {
    return readStore(RIBBON_KEY, "open") !== "closed";
  }

  function applyRibbon(hub) {
    const open = ribbonOpen();
    hub.section.classList.toggle("easy-hub-ribbon-collapsed", !open);
    hub.ribbon.hidden = !open;
    hub.toggle.setAttribute("aria-expanded", String(open));
    hub.toggle.querySelector("span").textContent = open ? "Şeridi gizle" : "Şeridi göster";
    hub.toggle.title = open ? "Araç şeridini gizle" : "Araç şeridini göster";
  }

  function createHub(group, label, slug) {
    const info = HUB_INFO[label] || { short: label, icon: iconFor(label), text: `${label} başlığındaki araçlar.` };
    const id = `group:${label}`;
    const view = `hub-${slug}`;
    const section = document.createElement("section");
    section.id = `${view}-view`;
    section.className = "view easy-hub-view";
    section.dataset.hub = id;
    section.setAttribute("aria-labelledby", `${view}-title`);
    section.innerHTML = `<div class="control-strip easy-hub-strip" hidden></div>
      <header class="easy-hub-header">
        <span class="easy-hub-mark">${icon(info.icon)}</span>
        <div class="easy-hub-titles">
          <h1 id="${view}-title" tabindex="-1">${escapeHtml(label)}</h1>
          <p>${escapeHtml(info.text)}</p>
        </div>
        <div class="easy-hub-header-actions">
          <span class="easy-hub-count"></span>
          <button type="button" class="easy-hub-ribbon-toggle" aria-controls="${view}-ribbon" aria-expanded="true">${icon("chevron")}<span>Şeridi gizle</span></button>
        </div>
      </header>
      <div class="easy-hub-ribbon" id="${view}-ribbon" role="toolbar" aria-label="${escapeHtml(label)} araç şeridi"></div>
      <div class="easy-hub-stage">
        <nav class="easy-hub-stagebar" aria-label="Konum" hidden>
          <button type="button" class="easy-hub-back">${icon("back")}<span>${escapeHtml(info.short)} sayfasına dön</span></button>
          <span class="easy-hub-crumb" aria-hidden="true">›</span>
          <span class="easy-hub-current" aria-live="polite"></span>
        </nav>
        <div class="easy-hub-tiles"></div>
        <div class="easy-hub-slot"></div>
      </div>`;
    main.insertBefore(section, [...main.children].find((node) => node.matches(".easy-isolation-note")) || null);
    const hub = {
      id, label, slug, view, group, section, info,
      short: info.short,
      icon: info.icon,
      title: section.querySelector("h1"),
      count: section.querySelector(".easy-hub-count"),
      toggle: section.querySelector(".easy-hub-ribbon-toggle"),
      ribbon: section.querySelector(".easy-hub-ribbon"),
      stagebar: section.querySelector(".easy-hub-stagebar"),
      current: section.querySelector(".easy-hub-current"),
      tiles: section.querySelector(".easy-hub-tiles"),
      slot: section.querySelector(".easy-hub-slot"),
      entries: [],
      rendered: false,
      launching: 0,
      launcher: null,
      followUntil: 0,
    };
    section.querySelector(".easy-hub-header-actions").prepend(starButton(id, label, true));
    hub.toggle.addEventListener("click", () => {
      writeStore(RIBBON_KEY, ribbonOpen() ? "closed" : "open");
      hubs.forEach(applyRibbon);
    });
    section.querySelector(".easy-hub-back").addEventListener("click", async () => {
      const dialog = openIn(hub);
      if (!dialog || await closeDialog(dialog)) {
        resetStage(hub);
        focusHome(hub);
      }
    });
    hub.ribbon.addEventListener("keydown", (event) => {
      const items = [...hub.ribbon.querySelectorAll(".easy-hub-ribbon-item:not(:disabled)")];
      const index = items.indexOf(document.activeElement);
      if (index < 0 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + items.length) % items.length;
      items.forEach((item) => { item.tabIndex = -1; });
      items[next].tabIndex = 0;
      items[next].focus();
    });
    hub.ribbon.addEventListener("focusin", (event) => {
      const item = event.target.closest(".easy-hub-ribbon-item");
      if (!item) return;
      hub.ribbon.querySelectorAll(".easy-hub-ribbon-item").forEach((node) => { node.tabIndex = node === item ? 0 : -1; });
    });
    new MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.removedNodes) {
          if (node instanceof HTMLDialogElement && !node.isConnected) node.classList.remove("hub-embedded");
        }
      }
      if (!openIn(hub) && ![...hub.slot.children].some((node) => node.matches?.("dialog.hub-embedded"))) resetStage(hub);
    }).observe(hub.slot, { childList: true });
    hubs.set(id, hub);
    hubByView.set(view, hub);
    return hub;
  }

  const slugs = new Set();
  for (const group of toolsMenu.querySelectorAll(".easy-tool-group")) {
    const label = normalize(group.querySelector(".easy-tool-heading")?.textContent);
    if (!label || label === "Ana sayfalar" || group.querySelector("[data-easy-view]")) continue;
    let slug = fold(label) || "baslik";
    while (slugs.has(slug)) slug += "-2";
    slugs.add(slug);
    createHub(group, label, slug);
  }

  function syncTopbar() {
    const nav = document.querySelector(".main-nav");
    const saveState = document.querySelector("#save-state");
    const anchor = topbar?.querySelector(".easy-header-options") || null;
    if (topbar && hubByView.has(state.view)) {
      if (nav && nav.parentElement !== topbar) topbar.insertBefore(nav, anchor);
      if (saveState && saveState.parentElement !== topbar) topbar.insertBefore(saveState, anchor);
    }
    nav?.querySelectorAll(".nav-tab[data-view]").forEach((tab) => {
      const current = tab.dataset.view === state.view;
      tab.classList.toggle("active", current);
      if (current) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    });
  }

  const baseSwitch = window.switchView;
  if (typeof baseSwitch === "function") {
    window.switchView = async function hubSwitchView(view, ...rest) {
      const from = hubByView.get(state.view);
      if (view !== state.view && from && !(await releaseStage(from, true))) return false;
      const target = hubByView.get(view);
      if (target && view !== state.view) renderHub(target);
      const result = await baseSwitch.call(this, view, ...rest);
      pending = null;
      if (TABLE_VIEWS.has(state.view)) lastTableView = state.view;
      syncTopbar();
      return result;
    };
  }

  async function open(id, { focus = false } = {}) {
    const hub = hubs.get(normalize(id).startsWith("group:") ? `group:${normalize(id).slice(6)}` : id);
    if (!hub) {
      toast("Bu başlık sayfası bulunamadı.", true);
      return false;
    }
    toolsMenu.hidden = true;
    document.querySelector("#tools-menu-button")?.setAttribute("aria-expanded", "false");
    const returnFocus = document.activeElement?.closest?.(".main-nav") ? document.activeElement : null;
    if (state.view === hub.view) {
      if (!openIn(hub)) renderHub(hub);
    } else if (!(await switchView(hub.view))) {
      return false;
    }
    trackUsage(hub.id);
    if (focus) hub.title.focus({ preventScroll: true });
    else if (returnFocus?.isConnected && (document.activeElement === document.body || !document.activeElement)) {
      returnFocus.focus({ preventScroll: true });
    }
    window.scrollTo({ top: 0 });
    return true;
  }

  function findMenuNode(id) {
    return [...document.querySelectorAll("#tools-menu [data-menu-id], .main-nav [data-menu-id]")]
      .find((node) => node.dataset.menuId === id) || null;
  }

  async function run(id) {
    const value = normalize(id);
    if (value.startsWith("group:")) return open(value, { focus: true });
    if (value.startsWith("view:")) {
      const tab = [...document.querySelectorAll(".main-nav .nav-tab[data-view]")]
        .find((node) => node.dataset.view === value.slice(5));
      trackUsage(value);
      if (tab && !tab.hidden) { tab.click(); return true; }
      return switchView(value.slice(5));
    }
    const node = [...toolsMenu.querySelectorAll("[data-menu-id]")].find((item) => item.dataset.menuId === value)
      || findMenuNode(value);
    if (!node) {
      toast("Bu kısayolun aracı bulunamadı. Kısayol halkasını düzenleyip kaldırabilirsiniz.", true);
      return false;
    }
    const label = normalize(node.textContent);
    trackUsage(value);
    return runTool({
      node, label,
      index: node.hasAttribute("data-tool") ? Number(node.dataset.tool) : -1,
      plain: plainLabel(label),
    });
  }

  document.addEventListener("ui-settings-changed", () => hubs.forEach((hub) => { if (hub.rendered) applyStars(hub); }));
  toolsMenu.addEventListener("click", (event) => {
    const node = event.target.closest?.("[data-tool][data-menu-id]");
    if (node && event.isTrusted) trackUsage(node.dataset.menuId);
  }, true);

  window.easyHubs = {
    list: () => [...hubs.values()].map(({ id, label, short, icon: name, view, info }) => ({
      id, label, short, icon: name, view, description: info.text,
    })),
    open,
    openGroup: (label) => open(`group:${normalize(label)}`, { focus: true }),
    run,
    slugFor: fold,
    icon,
    iconFor,
    labelFor: plainLabel,
    viewFor: (id) => hubs.get(id)?.view || null,
    usage,
    trackUsage,
  };
})();
