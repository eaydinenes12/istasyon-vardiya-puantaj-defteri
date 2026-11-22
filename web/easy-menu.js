"use strict";

// Top menu bar with one tab per tool heading (opens its in-app page) and the emblem shortcut ring.
(() => {
  const nav = document.querySelector(".main-nav");
  const toolsButton = document.querySelector("#tools-menu-button");
  const toolsMenu = document.querySelector("#tools-menu");
  if (!nav || !toolsButton || !toolsMenu || typeof EXTRAS_MENU === "undefined") return;
  const header = document.querySelector(".easy-header-content");
  const brand = document.querySelector(".brand");
  const VIEWS = ["view:schedule", "view:attendance", "view:archive", "view:templates"];
  const RING_SIZES = [160, 190, 220, 260, 300, 340];
  const RING_LABELS = { 160: "Çok küçük", 190: "Küçük", 220: "Kompakt", 260: "Orta", 300: "Geniş", 340: "Büyük" };
  const RING_DEFAULT_SIZE = 190;
  const RING_MAX = 12;
  const ID_LIMIT = 140;
  const RING_DEFAULTS = [
    "tool:Personel listesi", "tool:Otomatik vardiya oluştur", "tool:Belge ve tutanak dosyası",
    "tool:Yönetici özeti", "tool:Excel çıktı önizlemesi", "tool:Ay sonu paketi",
    "tool:Toplu giriş çalışma alanı", "tool:Personel ana sayfası",
  ];
  const DISABLED_RING_MESSAGE = "Kısayol halkası kapalı. Açmak için Tüm araçlar > Kısayollar > "
    + "Kısayol halkasını aç / kapat seçeneğini kullanın.";

  const normalize = (text) => String(text ?? "").trim().replace(/\s+/g, " ");
  const fold = (text) => normalize(text).toLocaleLowerCase("tr-TR");
  const hubsApi = () => window.easyHubs || null;
  const svg = (name, className = "easy-icon") => {
    try { return hubsApi()?.icon?.(name, className) || ""; } catch { return ""; }
  };
  const iconOf = (id) => {
    try { return hubsApi()?.iconFor?.(id) || "grid"; } catch { return "grid"; }
  };
  const plain = (label) => {
    try { return hubsApi()?.labelFor?.(label) || normalize(label); } catch { return normalize(label); }
  };
  const catalog = new Map();
  const created = new Map();
  const settingsButtons = [];
  let busy = false;
  let opening = false;
  let ready = Boolean(state.uiSettings?.easy_menu);
  let fitFrame = 0;
  let ringOpen = false;

  function button(text, action, className = "button button-outline") {
    const node = document.createElement("button");
    node.type = "button";
    node.className = className;
    node.textContent = text;
    if (action) node.addEventListener("click", action);
    return node;
  }

  function register(id, item) {
    catalog.set(id, { id, ...item });
    return catalog.get(id);
  }

  nav.querySelectorAll(".nav-tab[data-view]").forEach((node) => {
    const id = `view:${node.dataset.view}`;
    const label = normalize(node.textContent);
    node.dataset.menuId = id;
    register(id, { kind: "view", label, short: label, category: "Ana sayfalar", node, icon: iconOf(id) });
  });
  const hubInfo = new Map((() => {
    try { return hubsApi()?.list?.() || []; } catch { return []; }
  })().map((hub) => [hub.id, hub]));
  toolsMenu.querySelectorAll(".easy-tool-group").forEach((group) => {
    const heading = group.querySelector(".easy-tool-heading");
    const label = normalize(heading?.textContent);
    const id = `group:${label}`;
    const hub = hubInfo.get(id);
    if (!label || !hub || id.length > ID_LIMIT || group.querySelector("[data-easy-view]")) return;
    heading.dataset.menuId = id;
    register(id, {
      kind: "group", label, short: hub.short || label, category: "Başlık sayfaları", node: heading,
      icon: hub.icon || iconOf(id), view: hub.view, description: hub.description || "",
    });
  });

  function scanTools() {
    toolsMenu.querySelectorAll(".easy-tool-group").forEach((group) => {
      const category = normalize(group.querySelector(".easy-tool-heading")?.textContent) || "Diğer araçlar";
      group.querySelectorAll("[data-tool]").forEach((node) => {
        const label = normalize(node.textContent);
        const id = `tool:${label}`;
        if (!label || id.length > ID_LIMIT) return;
        node.dataset.menuId = id;
        const known = catalog.get(id);
        if (!known) register(id, { kind: "tool", label, short: plain(label), category, node, icon: iconOf(id) });
        else if (known.kind === "tool") known.node = node;
      });
    });
    toolsMenu.querySelectorAll("[data-easy-view]").forEach((node) => {
      node.dataset.menuId = `view:${node.dataset.easyView}`;
    });
  }
  scanTools();

  function validId(id) {
    if (typeof id !== "string" || !id || id.length > ID_LIMIT || /[\x00-\x1f\x7f]/.test(id)) return false;
    if (VIEWS.includes(id)) return true;
    const separator = id.indexOf(":");
    const prefix = id.slice(0, separator + 1);
    const name = id.slice(separator + 1);
    return (prefix === "tool:" || prefix === "group:") && Boolean(name) && name === normalize(name);
  }

  function unique(list) {
    return [...new Set(list)];
  }

  function preferences() {
    const raw = state.uiSettings?.easy_menu || {};
    const strings = (value) => (Array.isArray(value) ? value.filter((item) => typeof item === "string") : []);
    const order = strings(raw.order);
    return {
      order: order.length ? order : [...VIEWS],
      hidden: strings(raw.hidden),
      ring_enabled: raw.ring_enabled !== false,
      ring_size: RING_SIZES.includes(raw.ring_size) ? raw.ring_size : RING_DEFAULT_SIZE,
      ring_items: Array.isArray(raw.ring_items)
        ? raw.ring_items.filter((item) => item === null || typeof item === "string") : null,
    };
  }

  function sanitize(prefs) {
    const order = unique((prefs.order || []).filter(validId));
    for (const view of VIEWS) if (!order.includes(view)) order.push(view);
    while (order.length > 100) {
      let index = order.length - 1;
      while (index >= 0 && VIEWS.includes(order[index])) index -= 1;
      order.splice(index, 1);
    }
    return {
      order,
      hidden: unique((prefs.hidden || []).filter((id) => validId(id) && !id.startsWith("tool:"))),
      ring_enabled: prefs.ring_enabled !== false,
      ring_size: RING_SIZES.includes(prefs.ring_size) ? prefs.ring_size : RING_DEFAULT_SIZE,
      ring_items: Array.isArray(prefs.ring_items) ? (() => {
        const seen = new Set();
        return prefs.ring_items.slice(0, RING_MAX).map((id) => {
          if (id === null || !validId(id) || seen.has(id)) return null;
          seen.add(id);
          return id;
        });
      })() : null,
    };
  }

  function effectiveOrder(prefs = preferences()) {
    const order = unique(prefs.order.filter((id) => catalog.has(id)));
    const missing = [...catalog.values()]
      .filter((item) => item.kind !== "tool" && !order.includes(item.id)).map((item) => item.id);
    if (missing.length) {
      let at = 0;
      order.forEach((id, index) => { if (!id.startsWith("tool:")) at = index + 1; });
      order.splice(at, 0, ...missing);
    }
    return order;
  }

  function withUnknown(order, prefs = preferences()) {
    return [...order, ...prefs.order.filter((id) => !catalog.has(id) && !order.includes(id))];
  }

  function itemLabel(item) {
    if (!item) return "";
    if (item.kind === "group") return `${item.label} sayfası`;
    return item.kind === "tool" ? plain(item.label) : item.label;
  }

  function setContent(node, item) {
    const text = item.kind === "view" ? item.label : item.short;
    let label = node.querySelector(":scope > .easy-tab-label");
    if (!label) {
      node.textContent = "";
      node.insertAdjacentHTML("beforeend", svg(item.icon));
      label = document.createElement("span");
      label.className = "easy-tab-label";
      node.append(label);
    }
    if (label.textContent !== text) label.textContent = text;
    node.setAttribute("aria-label", itemLabel(item));
  }

  async function runItem(id) {
    try {
      const api = hubsApi();
      if (api?.run) return await api.run(id);
      const item = catalog.get(id);
      item?.node?.click();
      return Boolean(item);
    } catch (error) {
      toast(error?.message || String(error), true);
      return false;
    }
  }

  function createNode(item) {
    const node = document.createElement("button");
    node.type = "button";
    node.dataset.menuId = item.id;
    if (item.kind === "group") {
      node.className = "nav-tab easy-hub-tab";
      node.dataset.view = item.view;
      node.dataset.hub = item.id;
      node.addEventListener("click", () => {
        const api = hubsApi();
        if (api?.open) api.open(item.id);
        else toast("Başlık sayfası henüz hazır değil. Birkaç saniye sonra yeniden deneyin.", true);
      });
    } else {
      node.className = "nav-tab easy-menu-shortcut";
      node.addEventListener("click", () => runItem(item.id));
    }
    setContent(node, item);
    return node;
  }

  function titleFor(item) {
    const drag = ready && !busy;
    if (item.kind === "group") {
      return `${item.label} sayfasını aç${item.description ? ` — ${item.description}` : ""}`
        + (drag ? "\nSürükleyerek sıralayın · Gizlemek için Tüm araçlar üzerine bırakın" : "");
    }
    return `${itemLabel(item)}${drag ? " · Sürükleyerek sıralayın · Şeritten kaldırmak için Tüm araçlar üzerine bırakın" : ""}`;
  }

  function syncActive() {
    nav.querySelectorAll(".nav-tab[data-view]").forEach((tab) => {
      const current = tab.dataset.view === state.view;
      tab.classList.toggle("active", current);
      if (current) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    });
  }

  const moreButton = document.createElement("button");
  moreButton.type = "button";
  moreButton.className = "nav-tab easy-nav-more easy-nav-more-off";
  moreButton.setAttribute("aria-haspopup", "menu");
  moreButton.setAttribute("aria-expanded", "false");
  moreButton.title = "Menü şeridine sığmayan sekmeler";
  moreButton.innerHTML = '<span class="easy-tab-label">Daha fazla</span><span aria-hidden="true">▾</span>';
  const morePanel = document.createElement("div");
  morePanel.className = "easy-nav-more-menu";
  morePanel.setAttribute("role", "menu");
  morePanel.hidden = true;
  document.body.append(morePanel);

  function closeMore(focusButton = false) {
    if (morePanel.hidden) return;
    morePanel.hidden = true;
    moreButton.setAttribute("aria-expanded", "false");
    if (focusButton) moreButton.focus({ preventScroll: true });
  }

  function openMore() {
    const hiddenTabs = [...nav.querySelectorAll(".easy-nav-overflow")];
    morePanel.textContent = "";
    for (const tab of hiddenTabs) {
      const entry = document.createElement("button");
      entry.type = "button";
      entry.setAttribute("role", "menuitem");
      const icon = tab.querySelector(".easy-icon");
      if (icon) entry.append(icon.cloneNode(true));
      const label = document.createElement("span");
      label.textContent = tab.getAttribute("aria-label") || tab.textContent.trim();
      entry.append(label);
      entry.addEventListener("click", () => { closeMore(); tab.click(); });
      morePanel.append(entry);
    }
    if (!hiddenTabs.length) return;
    const rect = moreButton.getBoundingClientRect();
    morePanel.hidden = false;
    const width = morePanel.offsetWidth;
    morePanel.style.top = `${Math.round(rect.bottom + 4)}px`;
    morePanel.style.left = `${Math.max(8, Math.min(Math.round(rect.right - width), window.innerWidth - width - 8))}px`;
    moreButton.setAttribute("aria-expanded", "true");
    morePanel.querySelector("button")?.focus({ preventScroll: true });
  }

  moreButton.addEventListener("click", (event) => {
    event.stopPropagation();
    if (morePanel.hidden) openMore();
    else closeMore();
  });
  morePanel.addEventListener("keydown", (event) => {
    const items = [...morePanel.querySelectorAll("button")];
    const index = items.indexOf(document.activeElement);
    if (event.key === "Escape") { event.preventDefault(); closeMore(true); }
    else if (event.key === "ArrowDown") { event.preventDefault(); items[(index + 1) % items.length]?.focus(); }
    else if (event.key === "ArrowUp") { event.preventDefault(); items[(index - 1 + items.length) % items.length]?.focus(); }
  });
  document.addEventListener("click", (event) => {
    if (!morePanel.hidden && !morePanel.contains(event.target) && event.target !== moreButton) closeMore();
  });
  window.addEventListener("resize", () => closeMore());

  function fit() {
    fitFrame = 0;
    const topbar = nav.closest(".topbar");
    nav.classList.remove("easy-nav-compact-1", "easy-nav-compact-2");
    topbar?.classList.remove("easy-topbar-tight");
    nav.querySelectorAll(".easy-nav-overflow").forEach((node) => node.classList.remove("easy-nav-overflow"));
    moreButton.classList.add("easy-nav-more-off");
    if (nav.lastElementChild !== moreButton) nav.append(moreButton);
    const overflowing = () => nav.clientWidth > 0 && nav.scrollWidth > nav.clientWidth + 2;
    if (!overflowing()) return closeMore();
    nav.classList.add("easy-nav-compact-1");
    if (!overflowing()) return closeMore();
    nav.classList.add("easy-nav-compact-2");
    if (!overflowing()) return closeMore();
    // Give the menu bar the room used by the status text and assistant label.
    topbar?.classList.add("easy-topbar-tight");
    if (!overflowing()) return closeMore();
    // Last resort: move trailing tabs into a "Daha fazla" menu instead of a hidden scroll area.
    moreButton.classList.remove("easy-nav-more-off");
    const candidates = [...nav.querySelectorAll(":scope > [data-menu-id]")]
      .filter((node) => !node.hidden && !node.classList.contains("active"));
    while (overflowing() && candidates.length) candidates.pop().classList.add("easy-nav-overflow");
    if (!nav.querySelector(".easy-nav-overflow")) moreButton.classList.add("easy-nav-more-off");
    closeMore();
  }

  function scheduleFit() {
    if (!fitFrame) fitFrame = requestAnimationFrame(fit);
  }

  function applyPreferences() {
    scanTools();
    const prefs = preferences();
    const order = effectiveOrder(prefs);
    const hidden = new Set(prefs.hidden);
    const shown = new Set(order);
    const wanted = [toolsButton];
    for (const id of order) {
      const item = catalog.get(id);
      let node = item.kind === "view" ? item.node : created.get(id);
      if (!node) {
        node = createNode(item);
        created.set(id, node);
      }
      if (item.kind === "view") setContent(node, item);
      node.hidden = item.kind !== "tool" && hidden.has(id);
      if (item.kind === "tool") node.disabled = Boolean(item.node?.disabled);
      node.draggable = ready && !busy;
      node.title = titleFor(item);
      wanted.push(node);
    }
    for (const [id, node] of created) if (!shown.has(id)) node.hidden = true;
    const wantedSet = new Set(wanted);
    const sequence = [...nav.children].filter((node) => wantedSet.has(node));
    if (sequence.length !== wanted.length || sequence.some((node, index) => node !== wanted[index])) {
      const focused = nav.contains(document.activeElement) ? document.activeElement : null;
      nav.prepend(toolsButton);
      nav.append(...wanted.slice(1));
      if (focused?.isConnected && !focused.hidden && document.activeElement !== focused) {
        focused.focus({ preventScroll: true });
      }
    }
    toolsMenu.querySelectorAll("[data-menu-id]").forEach((node) => {
      node.draggable = ready && !busy;
      if (node.matches("[data-tool]")) node.title = "Menü şeridine eklemek için üst menüye sürükleyin.";
    });
    nav.setAttribute("aria-busy", String(busy));
    settingsButtons.forEach((node) => { node.disabled = busy; });
    syncActive();
    scheduleFit();
  }

  async function loadFresh() {
    const result = await request("/api/ui-settings");
    state.uiSettings = result.settings;
    ready = true;
    document.dispatchEvent(new CustomEvent("ui-settings-changed"));
  }

  async function ensureReady() {
    if (ready) return true;
    try {
      await loadFresh();
      return true;
    } catch (error) {
      toast(`Kısayol ayarları yüklenemedi: ${error.message}`, true);
      return false;
    }
  }

  async function commit(prefs) {
    if (busy) return false;
    busy = true;
    applyPreferences();
    try {
      await saveUiSettings({ easy_menu: sanitize(prefs) });
      return true;
    } catch (error) {
      toast(`Menü düzeni kaydedilemedi: ${error.message}`, true);
      return false;
    } finally {
      busy = false;
      applyPreferences();
    }
  }

  function closeToolsMenu() {
    toolsMenu.hidden = true;
    toolsButton.setAttribute("aria-expanded", "false");
  }

  function enableDragging(container, selector, horizontal, getOrder, move, remove = null) {
    let dragged = null;
    let fromCatalog = false;
    const sources = remove ? [container, toolsMenu] : [container];
    function clear() {
      dragged = null;
      fromCatalog = false;
      container.classList.remove("easy-menu-receiving", "easy-menu-drop-end");
      toolsButton.classList.remove("easy-menu-return-target");
      for (const source of sources) {
        source.querySelectorAll(".easy-menu-dragging, .easy-menu-drop-before, .easy-menu-drop-after")
          .forEach((node) => node.classList.remove("easy-menu-dragging", "easy-menu-drop-before", "easy-menu-drop-after"));
      }
    }
    for (const source of sources) {
      source.addEventListener("dragstart", (event) => {
        const node = event.target.closest?.(selector);
        if (!node || busy || (remove && !ready)) { event.preventDefault(); return; }
        dragged = node.dataset.menuId;
        fromCatalog = toolsMenu.contains(node);
        container.classList.toggle("easy-menu-receiving", fromCatalog);
        event.dataTransfer.effectAllowed = fromCatalog ? "copyMove" : "move";
        event.dataTransfer.setData("text/plain", dragged);
        node.classList.add("easy-menu-dragging");
      });
      source.addEventListener("dragend", clear);
    }
    if (remove) {
      for (const target of [toolsButton, toolsMenu]) {
        target.addEventListener("dragover", (event) => {
          if (!dragged || fromCatalog || busy) return;
          event.preventDefault();
          event.stopPropagation();
          event.dataTransfer.dropEffect = "move";
          toolsButton.classList.add("easy-menu-return-target");
        });
        target.addEventListener("dragleave", () => toolsButton.classList.remove("easy-menu-return-target"));
        target.addEventListener("drop", async (event) => {
          if (!dragged || fromCatalog || busy) return;
          event.preventDefault();
          event.stopPropagation();
          const order = getOrder().filter((id) => id !== dragged);
          const id = dragged;
          clear();
          await remove(order, id);
        });
      }
    }
    container.addEventListener("dragover", (event) => {
      const node = event.target.closest?.(selector);
      if (!dragged || (!node && !horizontal) || node?.dataset.menuId === dragged) return;
      if (horizontal && !fromCatalog && event.target.closest("#tools-menu-button")) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = fromCatalog ? "copy" : "move";
      container.querySelectorAll(".easy-menu-drop-before, .easy-menu-drop-after")
        .forEach((item) => item.classList.remove("easy-menu-drop-before", "easy-menu-drop-after"));
      container.classList.toggle("easy-menu-drop-end", !node);
      if (!node) return;
      const rect = node.getBoundingClientRect();
      const after = horizontal ? event.clientX > rect.left + rect.width / 2
        : event.clientY > rect.top + rect.height / 2;
      node.classList.add(after ? "easy-menu-drop-after" : "easy-menu-drop-before");
    });
    container.addEventListener("drop", async (event) => {
      const node = event.target.closest?.(selector);
      if (!dragged || (!node && !horizontal) || node?.dataset.menuId === dragged) { clear(); return; }
      if (horizontal && !fromCatalog && event.target.closest("#tools-menu-button")) { clear(); return; }
      event.preventDefault();
      const order = getOrder().filter((id) => id !== dragged);
      let index = fromCatalog && event.target.closest("#tools-menu-button") ? 0 : order.length;
      if (node) {
        const rect = node.getBoundingClientRect();
        const after = horizontal ? event.clientX > rect.left + rect.width / 2
          : event.clientY > rect.top + rect.height / 2;
        index = order.indexOf(node.dataset.menuId) + (after ? 1 : 0);
      }
      order.splice(index, 0, dragged);
      const adding = fromCatalog;
      const id = dragged;
      clear();
      await move(order, adding, id);
    });
  }

  // ----- Emblem shortcut ring -----
  function defaultRing() {
    scanTools();
    const result = [];
    const tools = [...catalog.values()].filter((item) => item.kind === "tool");
    for (const prefix of RING_DEFAULTS) {
      const found = catalog.get(prefix) || tools.find((item) => item.id.startsWith(prefix));
      if (found && !result.includes(found.id)) result.push(found.id);
    }
    for (const id of VIEWS) if (result.length < 8 && catalog.has(id) && !result.includes(id)) result.push(id);
    return result.slice(0, 8);
  }

  function ringItems() {
    scanTools();
    const prefs = preferences();
    const stored = prefs.ring_items === null ? defaultRing() : prefs.ring_items;
    const seen = new Set();
    return Array.from({ length: RING_MAX }, (_, index) => {
      const id = stored[index];
      if (!id || !catalog.has(id) || seen.has(id)) return null;
      seen.add(id);
      return id;
    });
  }

  function ringGeometry(count, size, limit = Infinity) {
    let radius = Math.max(size / 2 - 26, (Math.max(count, 1) * 52) / (2 * Math.PI), 52);
    let diameter = 2 * radius + 52;
    if (diameter > limit) {
      diameter = Math.max(limit, 160);
      radius = (diameter - 52) / 2;
    }
    return { radius, diameter };
  }

  function ringPoint(index, count, radius) {
    const angle = ((-90 + (360 * index) / Math.max(count, 1)) * Math.PI) / 180;
    return { x: (Math.cos(angle) * radius).toFixed(1), y: (Math.sin(angle) * radius).toFixed(1) };
  }

  const ring = document.createElement("div");
  ring.id = "easy-ring";
  ring.className = "easy-ring";
  ring.setAttribute("role", "menu");
  ring.setAttribute("aria-label", "Kısayol halkası");
  ring.hidden = true;
  ring.innerHTML = `<div class="easy-ring-wheel" role="none">
      <span class="easy-ring-disc" aria-hidden="true"></span>
      <p class="easy-ring-caption" aria-hidden="true">Kısayollar</p>
    </div>
    <div class="easy-ring-actions" role="group" aria-label="Halka işlemleri"></div>`;
  document.body.append(ring);
  const wheel = ring.querySelector(".easy-ring-wheel");
  const caption = ring.querySelector(".easy-ring-caption");
  const ringActions = ring.querySelector(".easy-ring-actions");
  let captionText = "Kısayollar";
  for (const [name, text, action] of [
    ["edit", "Düzenle", () => { closeRing(true); openRingEditor(); }],
    ["grid", "Tüm araçlar", () => { closeRing(false); setTimeout(() => toolsButton.click()); }],
    ["close", "Kapat", () => closeRing(true)],
  ]) {
    const node = button("", action, "easy-ring-action");
    node.setAttribute("role", "menuitem");
    node.tabIndex = -1;
    node.insertAdjacentHTML("beforeend", svg(name));
    const label = document.createElement("span");
    label.textContent = text;
    node.append(label);
    ringActions.append(node);
  }

  function renderRing() {
    const prefs = preferences();
    const ids = ringItems();
    wheel.querySelectorAll(".easy-ring-item").forEach((node) => node.remove());
    const limit = Math.min(window.innerWidth - 16, window.innerHeight - 72);
    const { radius, diameter } = ringGeometry(RING_MAX, prefs.ring_size, limit);
    ring.style.setProperty("--ring-size", `${Math.round(diameter)}px`);
    ids.forEach((id, index) => {
      if (!id) return;
      const item = catalog.get(id);
      const point = ringPoint(index, RING_MAX, radius);
      const node = document.createElement("button");
      node.type = "button";
      node.className = "easy-ring-item";
      node.setAttribute("role", "menuitem");
      node.tabIndex = -1;
      node.dataset.ringTarget = id;
      node.setAttribute("aria-label", itemLabel(item));
      node.title = itemLabel(item);
      node.disabled = item.kind === "tool" && Boolean(item.node?.disabled);
      node.style.setProperty("--x", `${point.x}px`);
      node.style.setProperty("--y", `${point.y}px`);
      node.style.setProperty("--i", String(index));
      node.innerHTML = svg(item.icon);
      wheel.append(node);
    });
    captionText = ids.some(Boolean) ? "Kısayollar" : "Halka boş — Düzenle ile ekleyin";
    caption.textContent = captionText;
    ring.classList.toggle("easy-ring-empty", !ids.length);
  }

  function positionRing() {
    const rect = brand?.getBoundingClientRect();
    const width = ring.offsetWidth;
    const height = ring.offsetHeight;
    let left = (window.innerWidth - width) / 2;
    let top = 64;
    if (rect?.width) {
      left = rect.left + rect.width / 2 - width / 2;
      top = rect.bottom + 6;
      if (top + height > window.innerHeight - 8 && rect.top - height - 6 >= 8) top = rect.top - height - 6;
    }
    left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
    top = Math.max(8, Math.min(top, window.innerHeight - height - 8));
    ring.style.left = `${Math.round(left)}px`;
    ring.style.top = `${Math.round(top)}px`;
  }

  function ringButtons() {
    return [...ring.querySelectorAll("button")].filter((node) => !node.disabled && !node.hidden);
  }

  function onOutsidePointer(event) {
    if (ring.contains(event.target) || brand?.contains(event.target)) return;
    closeRing(false);
  }

  function onOutsideFocus(event) {
    if (ring.contains(event.target) || event.target === brand) return;
    closeRing(false);
  }

  function closeRing(restoreFocus = false) {
    if (!ringOpen) return;
    ringOpen = false;
    ring.classList.remove("easy-ring-open");
    ring.hidden = true;
    brand?.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", onOutsidePointer, true);
    document.removeEventListener("focusin", onOutsideFocus, true);
    if (restoreFocus) brand?.focus({ preventScroll: true });
  }

  async function openRing() {
    if (!(await ensureReady())) return false;
    closeToolsMenu();
    document.querySelector(".easy-tool-flyout:not([hidden])")?.setAttribute("hidden", "");
    renderRing();
    ring.hidden = false;
    ring.classList.remove("easy-ring-open");
    positionRing();
    ringOpen = true;
    brand?.setAttribute("aria-expanded", "true");
    requestAnimationFrame(() => { if (ringOpen) ring.classList.add("easy-ring-open"); });
    ringButtons()[0]?.focus({ preventScroll: true });
    document.addEventListener("pointerdown", onOutsidePointer, true);
    document.addEventListener("focusin", onOutsideFocus, true);
    return true;
  }

  async function openFromBrand() {
    if (!(await ensureReady())) return;
    if (!preferences().ring_enabled) {
      toast(DISABLED_RING_MESSAGE);
      return;
    }
    await openRing();
  }

  ring.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeRing(true);
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      closeRing(true);
      return;
    }
    const steps = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    if (!(event.key in steps) && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    event.stopPropagation();
    const items = ringButtons();
    if (!items.length) return;
    const index = items.indexOf(document.activeElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
      : (Math.max(index, 0) + (index < 0 ? 0 : steps[event.key]) + items.length) % items.length;
    items[next].focus();
  });
  wheel.addEventListener("click", (event) => {
    const node = event.target.closest(".easy-ring-item");
    if (!node || node.disabled) return;
    const id = node.dataset.ringTarget;
    closeRing(true);
    setTimeout(() => runItem(id));
  });
  const showCaption = (event) => {
    const node = event.target.closest?.(".easy-ring-item");
    caption.textContent = node ? node.getAttribute("aria-label") : captionText;
    ring.classList.toggle("easy-ring-labelled", Boolean(node));
  };
  wheel.addEventListener("pointerover", showCaption);
  wheel.addEventListener("focusin", showCaption);
  wheel.addEventListener("pointerleave", () => {
    const focused = document.activeElement?.closest?.(".easy-ring-item");
    caption.textContent = focused ? focused.getAttribute("aria-label") : captionText;
    ring.classList.toggle("easy-ring-labelled", Boolean(focused));
  });
  ring.addEventListener("focusout", (event) => {
    if (!event.relatedTarget?.closest?.(".easy-ring-item")) {
      caption.textContent = captionText;
      ring.classList.remove("easy-ring-labelled");
    }
  });
  window.addEventListener("resize", () => {
    if (!ringOpen) return;
    renderRing();
    positionRing();
    ring.classList.add("easy-ring-open");
  });

  if (brand) {
    brand.removeAttribute("href");
    brand.setAttribute("role", "button");
    brand.tabIndex = 0;
    brand.setAttribute("aria-haspopup", "menu");
    brand.setAttribute("aria-controls", ring.id);
    brand.setAttribute("aria-expanded", "false");
    brand.setAttribute("aria-label", "Vardiya Planı ve Puantaj — kısayol halkası");
    brand.title = "Kısayol halkasını aç (sık kullanılan araçlar)";
    brand.addEventListener("click", (event) => {
      event.preventDefault();
      if (ringOpen) closeRing(true);
      else openFromBrand();
    });
    brand.addEventListener("keydown", (event) => {
      if (!["Enter", " ", "ArrowDown"].includes(event.key)) return;
      event.preventDefault();
      if (ringOpen) ringButtons()[0]?.focus();
      else openFromBrand();
    });
  }

  async function toggleRingItem(id) {
    const value = normalize(id);
    if (!(await ensureReady())) return false;
    scanTools();
    if (!validId(value) || !catalog.has(value)) {
      toast("Bu öğe kısayol halkasına eklenemedi.", true);
      return false;
    }
    const items = ringItems();
    const presentAt = items.indexOf(value);
    const present = presentAt >= 0;
    const used = items.filter(Boolean).length;
    if (!present && used >= RING_MAX) {
      toast(`Kısayol halkasında en fazla ${RING_MAX} öğe olabilir. Önce halkadan bir öğe çıkarın.`, true);
      return false;
    }
    const next = [...items];
    if (present) next[presentAt] = null;
    else next[next.indexOf(null)] = value;
    const saved = await commit({ ...preferences(), ring_items: next });
    if (saved) {
      toast(present ? "Kısayol halkasından çıkarıldı."
        : "Kısayol halkasına eklendi. Uygulama amblemini tıklayarak açabilirsiniz.");
    }
    return saved;
  }

  async function toggleRing() {
    if (!(await ensureReady())) return false;
    const prefs = preferences();
    const enabled = !prefs.ring_enabled;
    const saved = await commit({ ...prefs, ring_enabled: enabled });
    if (saved) {
      if (!enabled) closeRing(false);
      toast(enabled ? "Kısayol halkası açıldı. Uygulama amblemini tıklayarak kullanabilirsiniz."
        : "Kısayol halkası kapatıldı. Amblem tıklaması artık halkayı açmaz.");
    }
    return saved;
  }

  async function resizeRing(delta) {
    if (!(await ensureReady())) return false;
    const prefs = preferences();
    const next = RING_SIZES[RING_SIZES.indexOf(prefs.ring_size) + Math.sign(Number(delta) || 0)];
    if (!next) {
      toast(delta > 0 ? "Kısayol halkası zaten en büyük boyutta." : "Kısayol halkası zaten en küçük boyutta.");
      return false;
    }
    const saved = await commit({ ...prefs, ring_size: next });
    if (saved) {
      toast(`Kısayol halkası boyutu: ${RING_LABELS[next]} (${next} px).`);
      await openRing();
    }
    return saved;
  }

  function choiceGroups() {
    scanTools();
    const groups = new Map();
    for (const item of catalog.values()) {
      if (!groups.has(item.category)) groups.set(item.category, []);
      groups.get(item.category).push(item);
    }
    return groups;
  }

  async function openRingEditor() {
    if (busy || opening || document.querySelector("#easy-ring-dialog[open]")) return;
    opening = true;
    const trigger = document.activeElement;
    try {
      await loadFresh();
    } catch (error) {
      toast(`Kısayol ayarları yüklenemedi: ${error.message}`, true);
      return;
    } finally {
      opening = false;
    }
    closeRing(false);
    closeToolsMenu();
    const options = document.querySelector(".easy-header-options");
    if (options) options.open = false;
    const prefs = preferences();
    const draft = {
      enabled: prefs.ring_enabled,
      size: prefs.ring_size,
      items: ringItems(),
      defaults: prefs.ring_items === null,
    };
    const dialog = extrasDialog("easy-ring-dialog", "Kısayol halkasını düzenle",
      "Uygulama amblemine tıklayınca açılan halkada en fazla 12 sık kullanılan araç veya sayfa bulunur.");
    dialog.classList.add("easy-menu-dialog", "easy-ring-dialog");
    const body = dialog.querySelector(".extras-body");
    body.innerHTML = `<div class="easy-ring-editor">
      <section class="easy-ring-settings" aria-labelledby="easy-ring-settings-title">
        <h3 id="easy-ring-settings-title">Görünüm</h3>
        <label class="easy-ring-check"><input id="easy-ring-enabled" type="checkbox"> Amblem tıklanınca halkayı aç</label>
        <label class="easy-ring-field" for="easy-ring-size">Halka boyutu</label>
        <select id="easy-ring-size">${RING_SIZES.map((size) =>
          `<option value="${size}">${RING_LABELS[size]} · ${size} px</option>`).join("")}</select>
        <div class="easy-ring-preview" aria-hidden="true"><span class="easy-ring-preview-disc"></span></div>
        <p class="easy-ring-note">Kısayollar saat 12 yönünden başlayıp saat yönünde dizilir. Halkayı ok tuşlarıyla
          gezebilir, Esc ile kapatabilirsiniz.</p>
      </section>
      <section class="easy-ring-current" aria-labelledby="easy-ring-current-title">
        <h3 id="easy-ring-current-title">Halkadaki kısayollar <span class="easy-ring-count"></span></h3>
        <ol class="easy-ring-order" aria-labelledby="easy-ring-current-title"></ol>
        <p class="easy-ring-empty-note" hidden>Halka boş. Yandaki listeden en az bir kısayol seçin.</p>
      </section>
      <section class="easy-ring-add" aria-labelledby="easy-ring-add-title">
        <h3 id="easy-ring-add-title">Kısayol ekle</h3>
        <label class="easy-ring-field" for="easy-ring-search">Araç veya sayfa ara</label>
        <input id="easy-ring-search" type="search" placeholder="Personel, rapor, tutanak…" autocomplete="off">
        <div class="easy-ring-choices"></div>
        <p class="easy-ring-no-results" hidden>Eşleşen araç veya sayfa bulunamadı.</p>
      </section>
    </div>
    <p class="easy-menu-status easy-ring-status" role="status" aria-live="polite"></p>`;
    const enabled = body.querySelector("#easy-ring-enabled");
    const size = body.querySelector("#easy-ring-size");
    const preview = body.querySelector(".easy-ring-preview");
    const list = body.querySelector(".easy-ring-order");
    const count = body.querySelector(".easy-ring-count");
    const emptyNote = body.querySelector(".easy-ring-empty-note");
    const choices = body.querySelector(".easy-ring-choices");
    const status = dialog.querySelector(".easy-ring-status");
    const choiceInputs = new Map();
    enabled.checked = draft.enabled;
    size.value = String(draft.size);
    const changed = () => { draft.defaults = false; };

    function renderPreview() {
      preview.querySelectorAll(".easy-ring-preview-item").forEach((node) => node.remove());
      const scale = 0.5;
      const { radius, diameter } = ringGeometry(RING_MAX, draft.size);
      preview.style.setProperty("--preview-size", `${Math.round(diameter * scale)}px`);
      draft.items.forEach((id, index) => {
        if (!id) return;
        const point = ringPoint(index, RING_MAX, radius * scale);
        const dot = document.createElement("span");
        dot.className = "easy-ring-preview-item";
        dot.style.setProperty("--x", `${point.x}px`);
        dot.style.setProperty("--y", `${point.y}px`);
        dot.innerHTML = svg(catalog.get(id)?.icon || "grid");
        preview.append(dot);
      });
      preview.classList.toggle("easy-ring-preview-off", !draft.enabled);
    }

    function render(focusId = "") {
      const scrollTop = list.scrollTop;
      list.replaceChildren();
      draft.items.forEach((id, index) => {
        if (!id) return;
        const item = catalog.get(id);
        const row = document.createElement("li");
        row.dataset.menuId = id;
        row.draggable = true;
        row.tabIndex = -1;
        row.insertAdjacentHTML("beforeend", `<span class="easy-ring-order-icon">${svg(item?.icon || "grid")}</span>`);
        const name = document.createElement("span");
        name.className = "easy-ring-order-label";
        name.textContent = itemLabel(item);
        row.append(name);
        for (const [text, offset] of [["↑", -1], ["↓", 1]]) {
          const control = button(text, () => {
            const items = [...draft.items];
            [items[index], items[index + offset]] = [items[index + offset], items[index]];
            draft.items = items;
            changed();
            render(id);
          }, "icon-button");
          control.setAttribute("aria-label", `${itemLabel(item)} · ${offset < 0 ? "Yukarı taşı" : "Aşağı taşı"}`);
          control.disabled = index + offset < 0 || index + offset >= draft.items.length;
          row.append(control);
        }
        const remove = button("Kaldır", () => {
          draft.items[index] = null;
          changed();
          render(draft.items[Math.min(index, draft.items.length - 1)] || "");
          if (!draft.items.some(Boolean)) body.querySelector("#easy-ring-search").focus();
        }, "button button-light");
        remove.setAttribute("aria-label", `${itemLabel(item)} · Halkadan kaldır`);
        row.append(remove);
        list.append(row);
      });
      const used = draft.items.filter(Boolean).length;
      const full = used >= RING_MAX;
      count.textContent = `(${used}/${RING_MAX})`;
      emptyNote.hidden = used > 0;
      for (const [id, input] of choiceInputs) {
        input.checked = draft.items.includes(id);
        input.disabled = full && !input.checked;
      }
      status.textContent = full ? `Halka dolu (${RING_MAX} kısayol). Yeni eklemek için önce birini kaldırın.`
        : draft.defaults ? "Varsayılan kısayollar kullanılıyor." : "";
      list.scrollTop = scrollTop;
      if (focusId) [...list.children].find((row) => row.dataset.menuId === focusId)?.focus();
      renderPreview();
    }

    const sections = [];
    for (const [category, items] of choiceGroups()) {
      const section = document.createElement("fieldset");
      const legend = document.createElement("legend");
      legend.textContent = category;
      section.append(legend);
      for (const item of items) {
        const label = document.createElement("label");
        label.dataset.menuId = item.id;
        const input = document.createElement("input");
        input.type = "checkbox";
        input.addEventListener("change", () => {
          if (input.checked && draft.items.filter(Boolean).length >= RING_MAX) {
            input.checked = false;
            status.textContent = `Halkada en fazla ${RING_MAX} kısayol olabilir.`;
            return;
          }
          const next = [...draft.items];
          const presentAt = next.indexOf(item.id);
          if (input.checked) next[next.indexOf(null)] = item.id;
          else if (presentAt >= 0) next[presentAt] = null;
          draft.items = next;
          changed();
          render();
        });
        label.append(input);
        label.insertAdjacentHTML("beforeend", svg(item.icon));
        const text = document.createElement("span");
        text.textContent = itemLabel(item);
        label.append(text);
        label.dataset.search = fold(`${itemLabel(item)} ${category}`);
        section.append(label);
        choiceInputs.set(item.id, input);
      }
      sections.push(section);
      choices.append(section);
    }
    body.querySelector("#easy-ring-search").addEventListener("input", (event) => {
      const query = fold(event.target.value);
      let visible = 0;
      choices.querySelectorAll("label").forEach((label) => {
        label.hidden = !label.dataset.search.includes(query);
        if (!label.hidden) visible += 1;
      });
      sections.forEach((section) => {
        section.hidden = ![...section.querySelectorAll("label")].some((label) => !label.hidden);
      });
      body.querySelector(".easy-ring-no-results").hidden = visible > 0;
    });
    enabled.addEventListener("change", () => { draft.enabled = enabled.checked; renderPreview(); });
    size.addEventListener("change", () => { draft.size = Number(size.value); renderPreview(); });
    enableDragging(list, "li[data-menu-id]", false, () => draft.items, (items) => {
      draft.items = items;
      changed();
      render();
    });

    const actions = dialog.querySelector(".extras-actions");
    actions.querySelector("button").textContent = "Vazgeç";
    const fill = button("En çok kullandıklarımla doldur", () => {
      const usage = hubsApi()?.usage?.() || {};
      const top = Object.entries(usage)
        .filter(([id, value]) => catalog.has(id) && Number(value) > 0)
        .sort((a, b) => Number(b[1]) - Number(a[1]))
        .slice(0, 8)
        .map(([id]) => id);
      if (!top.length) {
        status.textContent = "Henüz yeterli kullanım kaydı yok. Araçları kullandıkça bu seçenek dolar.";
        return;
      }
      draft.items = top;
      changed();
      render();
      status.textContent = `En sık kullandığınız ${top.length} kısayol halkaya yerleştirildi. Kaydet ile uygulayın.`;
    }, "button button-light");
    const reset = button("Varsayılana dön", () => {
      draft.enabled = true;
      draft.size = RING_DEFAULT_SIZE;
      draft.items = defaultRing();
      draft.defaults = true;
      enabled.checked = true;
      size.value = String(RING_DEFAULT_SIZE);
      render();
    }, "button button-light");
    const save = button("Kaydet", async () => {
      const controls = [...dialog.querySelectorAll("button, input, select")].map((node) => [node, node.disabled]);
      controls.forEach(([node]) => { node.disabled = true; });
      try {
        const saved = await commit({
          ...preferences(),
          ring_enabled: draft.enabled,
          ring_size: draft.size,
          ring_items: draft.defaults ? null : draft.items.slice(0, RING_MAX),
        });
        if (saved) {
          dialog.close();
          toast(draft.enabled ? "Kısayol halkası kaydedildi. Uygulama amblemini tıklayarak açabilirsiniz."
            : "Kısayol halkası ayarları kaydedildi. Amblem halkası kapalı.");
        }
      } finally {
        controls.forEach(([node, disabled]) => { if (node.isConnected) node.disabled = disabled; });
      }
    }, "button button-primary");
    actions.prepend(fill, reset, save);
    dialog.oncancel = (event) => { if (busy) event.preventDefault(); };
    render();
    showExtras(dialog);
    const embedded = dialog.matches(".hub-embedded, .hub-floating");
    dialog.onclose = () => {
      if (embedded) return;
      const target = trigger?.isConnected && trigger.getClientRects().length && trigger !== document.body
        ? trigger : brand || toolsButton;
      target?.focus({ preventScroll: true });
    };
    body.scrollTop = 0;
  }

  // ----- Menu bar editor -----
  async function openEditor() {
    if (busy || opening || document.querySelector("#easy-menu-dialog[open]")) return;
    opening = true;
    const trigger = document.activeElement;
    try {
      await loadFresh();
    } catch (error) {
      toast(`Menü ayarları yüklenemedi: ${error.message}`, true);
      return;
    } finally {
      opening = false;
    }
    closeRing(false);
    closeToolsMenu();
    const options = document.querySelector(".easy-header-options");
    if (options) options.open = false;
    const prefs = preferences();
    let draft = { order: withUnknown(effectiveOrder(prefs), prefs), hidden: [...prefs.hidden] };
    const dialog = extrasDialog("easy-menu-dialog", "Menüyü özelleştir",
      "Üst menü şeridindeki başlık sayfalarını ve kısayolları sıralayın, gizleyin veya ekleyin. Değişiklikler Kaydet ile uygulanır.");
    dialog.classList.add("easy-menu-dialog");
    const body = dialog.querySelector(".extras-body");
    body.innerHTML = `<div class="easy-menu-editor">
      <section class="easy-menu-ring-link"><h3>Kısayol halkası</h3>
        <p>Uygulama amblemine tıklayınca açılan halkadaki kısayolları, halka boyutunu ve açık/kapalı durumunu ayrı ekrandan düzenleyin.</p>
      </section>
      <div class="easy-menu-editor-columns">
      <section><h3>Menü şeridinde görünenler</h3>
        <p>İşareti kaldırılan ana sayfa ve başlık sekmeleri gizlenir; Tüm araçlar menüsünden her zaman erişilebilir kalır.</p>
        <ol class="easy-menu-order" aria-label="Menü sıralaması"></ol>
      </section>
      <section><h3>Araç kısayolu ekle</h3>
        <label for="easy-menu-search">Araçlarda ara</label>
        <input id="easy-menu-search" type="search" placeholder="Personel, rapor, yedek…" autocomplete="off">
        <div class="easy-menu-choices"></div>
        <p id="easy-menu-no-results" hidden>Eşleşen araç bulunamadı.</p>
      </section></div></div>
      <p class="easy-menu-status" role="status" aria-live="polite"></p>`;
    const ringLink = button("Kısayol halkasını düzenle…", () => {
      dialog.close();
      setTimeout(openRingEditor);
    }, "button button-light");
    body.querySelector(".easy-menu-ring-link").append(ringLink);
    const list = body.querySelector(".easy-menu-order");
    const choices = body.querySelector(".easy-menu-choices");
    const choiceInputs = new Map();
    const labelFor = (id) => {
      const item = catalog.get(id);
      if (!item) return `${id.slice(id.indexOf(":") + 1)} (öğe bulunamadı)`;
      return item.kind === "group" ? `${item.label} (başlık sayfası)` : itemLabel(item);
    };
    function renderOrder(focusId = "") {
      const scrollTop = list.scrollTop;
      list.replaceChildren();
      for (const [index, id] of draft.order.entries()) {
        const item = catalog.get(id);
        const row = document.createElement("li");
        row.dataset.menuId = id;
        row.draggable = true;
        row.tabIndex = -1;
        const grip = document.createElement("span");
        grip.className = "easy-menu-grip";
        grip.setAttribute("aria-hidden", "true");
        grip.textContent = "↕";
        const label = document.createElement("label");
        label.className = "easy-menu-label";
        const toggleable = item && item.kind !== "tool";
        if (toggleable) {
          const input = document.createElement("input");
          input.type = "checkbox";
          input.checked = !draft.hidden.includes(id);
          input.addEventListener("change", () => {
            draft.hidden = input.checked ? draft.hidden.filter((value) => value !== id) : [...draft.hidden, id];
          });
          label.append(input);
        }
        label.insertAdjacentHTML("beforeend", svg(item?.icon || "grid"));
        label.append(document.createTextNode(labelFor(id)));
        row.append(grip, label);
        for (const [text, offset] of [["↑", -1], ["↓", 1]]) {
          const control = button(text, () => {
            const order = [...draft.order];
            [order[index], order[index + offset]] = [order[index + offset], order[index]];
            draft.order = order;
            renderOrder(id);
          }, "icon-button");
          control.setAttribute("aria-label", `${labelFor(id)} · ${offset < 0 ? "Yukarı taşı" : "Aşağı taşı"}`);
          control.disabled = index + offset < 0 || index + offset >= draft.order.length;
          row.append(control);
        }
        if (!toggleable && !VIEWS.includes(id)) {
          const remove = button("Kaldır", () => {
            draft.order = draft.order.filter((value) => value !== id);
            renderOrder(draft.order[Math.min(index, draft.order.length - 1)]);
          }, "button button-light");
          remove.setAttribute("aria-label", `${labelFor(id)} · Menüden kaldır`);
          row.append(remove);
        }
        list.append(row);
      }
      for (const [id, input] of choiceInputs) input.checked = draft.order.includes(id);
      const unknown = draft.order.filter((id) => !catalog.has(id)).length;
      body.querySelector(".easy-menu-status").textContent = unknown
        ? `${unknown} eski kısayolun aracı bulunamadı. Bu kısayolları listeden kaldırabilirsiniz.`
        : "Tüm araçlar menüsü eksiksiz kalır. Burada yalnız üst menü şeridini düzenlersiniz.";
      list.scrollTop = scrollTop;
      if (focusId) [...list.children].find((row) => row.dataset.menuId === focusId)?.focus();
    }
    const sections = new Map();
    for (const item of catalog.values()) {
      if (item.kind !== "tool") continue;
      if (!sections.has(item.category)) {
        const section = document.createElement("fieldset");
        const legend = document.createElement("legend");
        legend.textContent = item.category;
        section.append(legend);
        sections.set(item.category, section);
        choices.append(section);
      }
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.addEventListener("change", () => {
        draft.order = input.checked ? [...draft.order, item.id] : draft.order.filter((value) => value !== item.id);
        renderOrder();
      });
      label.append(input, document.createTextNode(itemLabel(item)));
      label.dataset.search = fold(`${item.label} ${item.category}`);
      sections.get(item.category).append(label);
      choiceInputs.set(item.id, input);
    }
    body.querySelector("#easy-menu-search").addEventListener("input", (event) => {
      const query = fold(event.target.value);
      let visible = 0;
      choices.querySelectorAll("label").forEach((label) => {
        label.hidden = !label.dataset.search.includes(query);
        if (!label.hidden) visible += 1;
      });
      for (const section of sections.values()) {
        section.hidden = ![...section.querySelectorAll("label")].some((label) => !label.hidden);
      }
      body.querySelector("#easy-menu-no-results").hidden = visible > 0;
    });
    enableDragging(list, "li[data-menu-id]", false, () => draft.order, (order) => {
      draft.order = order;
      renderOrder();
    });
    const actions = dialog.querySelector(".extras-actions");
    actions.querySelector("button").textContent = "Vazgeç";
    const reset = button("Varsayılana dön", () => {
      draft = {
        order: [...VIEWS, ...[...catalog.values()].filter((item) => item.kind === "group").map((item) => item.id)],
        hidden: [],
      };
      renderOrder();
    }, "button button-light");
    const save = button("Kaydet", async () => {
      const controls = [...dialog.querySelectorAll("button, input")].map((node) => [node, node.disabled]);
      controls.forEach(([node]) => { node.disabled = true; });
      try {
        if (await commit({ ...preferences(), order: draft.order, hidden: draft.hidden })) {
          dialog.close();
          toast("Menü düzeni kaydedildi.");
        }
      } finally {
        controls.forEach(([node, disabled]) => { if (node.isConnected) node.disabled = disabled; });
      }
    }, "button button-primary");
    actions.prepend(reset, save);
    dialog.oncancel = (event) => { if (busy) event.preventDefault(); };
    renderOrder();
    showExtras(dialog);
    const embedded = dialog.matches(".hub-embedded, .hub-floating");
    dialog.onclose = () => {
      if (embedded) return;
      const target = trigger?.isConnected && trigger.getClientRects().length && trigger !== document.body
        ? trigger : toolsButton;
      target.focus({ preventScroll: true });
    };
    dialog.scrollTop = 0;
    body.scrollTop = 0;
  }

  // ----- Wiring -----
  EXTRAS_MENU.push(["Menüyü özelleştir", () => openEditor()]);
  const menuSettingsButton = button("Menüyü özelleştir", null, "");
  menuSettingsButton.dataset.tool = String(EXTRAS_MENU.length - 1);
  menuSettingsButton.setAttribute("role", "menuitem");
  const groups = [...toolsMenu.querySelectorAll(".easy-tool-group")];
  const home = groups.find((group) => normalize(group.querySelector(".easy-tool-heading")?.textContent) === "Kısayollar")
    || groups.find((group) => normalize(group.querySelector(".easy-tool-heading")?.textContent) === "Tablo ve görünüm")
    || groups[groups.length - 1];
  (home?.querySelector(".easy-tool-items") || toolsMenu).append(menuSettingsButton);
  settingsButtons.push(menuSettingsButton);
  if (header) {
    const settingsButton = button("Menüyü özelleştir", openEditor, "button button-light");
    settingsButton.id = "easy-menu-settings";
    const ringButton = button("Kısayol halkası", openRingEditor, "button button-light");
    ringButton.id = "easy-ring-settings";
    header.append(settingsButton, ringButton);
    settingsButtons.push(settingsButton, ringButton);
  }
  scanTools();
  document.body.classList.add("easy-menu-enabled");

  enableDragging(nav, "[data-menu-id]", true, () => effectiveOrder(), async (order, adding, id) => {
    const prefs = preferences();
    const next = {
      ...prefs,
      order: withUnknown(order, prefs),
      hidden: adding ? prefs.hidden.filter((value) => value !== id) : prefs.hidden,
    };
    if (await commit(next)) {
      closeToolsMenu();
      toast(adding ? "Menü şeridine eklendi. Tüm araçlardaki yeri korunuyor." : "Menü sırası kaydedildi.");
    }
  }, async (order, id) => {
    const prefs = preferences();
    const next = id.startsWith("tool:")
      ? { ...prefs, order: withUnknown(order, prefs) }
      : { ...prefs, hidden: unique([...prefs.hidden, id]) };
    if (await commit(next)) {
      toast(id.startsWith("tool:") ? "Şeritten kaldırıldı. Tüm araçlar altında kullanılabilir."
        : "Sekme gizlendi. Tüm araçlar menüsünden veya Menüyü özelleştir ekranından yeniden açabilirsiniz.");
    }
  });

  new MutationObserver((records) => {
    if (records.some((record) => record.target !== nav && (record.type !== "attributes"
      || record.attributeName === "hidden"
      || String(record.oldValue || "").split(/\s+/).includes("active") !== record.target.classList.contains("active")))) {
      scheduleFit();
    }
  }).observe(nav, {
    subtree: true, childList: true, attributes: true, attributeOldValue: true, attributeFilter: ["class", "hidden"],
  });
  if (typeof ResizeObserver === "function") new ResizeObserver(scheduleFit).observe(nav);
  window.addEventListener("resize", scheduleFit);
  document.addEventListener("ui-settings-changed", () => {
    ready = true;
    applyPreferences();
    if (ringOpen) {
      renderRing();
      positionRing();
      ring.classList.add("easy-ring-open");
    }
  });
  applyPreferences();

  window.easyMenu = {
    setPinned: async (id, pinned) => {
      scanTools();
      if (!String(id).startsWith("tool:") || !catalog.has(id)) throw new Error("Sabitlenecek araç bulunamadı.");
      if (!(await ensureReady())) return false;
      const prefs = preferences();
      const order = pinned ? unique([...prefs.order, id]) : prefs.order.filter((item) => item !== id);
      return commit({ ...prefs, order });
    },
    openRing,
    closeRing: () => closeRing(false),
    openRingEditor,
    toggleRing,
    resizeRing,
    toggleRingItem,
    ringItems,
    openEditor,
  };
})();
