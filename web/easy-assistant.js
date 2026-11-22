"use strict";

(() => {
  const clone = (value) => structuredClone(value);
  const equal = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  const personName = (id) => state.people.find((person) => person.id === Number(id))?.name || `Personel ${id}`;
  const fullDate = (day) => new Date(`${day}T12:00:00`).toLocaleDateString("tr-TR", {
    day: "numeric", month: "long", year: "numeric", weekday: "long",
  });
  const run = (action) => Promise.resolve().then(action).catch((error) => toast(error.message, true));
  const tool = (prefix) => {
    const item = EXTRAS_MENU.find(([label, action]) => label.startsWith(prefix) && typeof action === "function");
    if (!item) throw new Error(`“${prefix}” aracı bulunamadı. Tüm araçlar menüsünü kontrol edin.`);
    return item[1]();
  };
  const selected = new Map();
  let anchor = null;
  let selectionMode = false;
  let dragging = false;
  let selectionMonth = "";
  let focusTarget = null;
  let clipboard = null;
  let changesOpen = false;
  let workspaceBusy = false;

  function button(text, action, className = "button button-outline") {
    const node = document.createElement("button");
    node.type = "button";
    node.className = className;
    node.textContent = text;
    node.addEventListener("click", () => run(action));
    return node;
  }

  function attendanceTarget(node) {
    const input = node.closest?.('[data-action="attendance-code"]');
    return input ? {
      employee_id: Number(input.dataset.person), date: input.dataset.day, code: input.value,
    } : null;
  }

  function cellTarget(node) {
    const attendance = attendanceTarget(node);
    if (attendance) return { ...attendance, kind: "attendance" };
    const cell = node.closest?.("td[data-shift-day]");
    const target = cell && scheduleTargetForCell(cell);
    return target?.employeeId ? {
      employee_id: target.employeeId, date: target.day, kind: "schedule", cell, target,
    } : null;
  }

  function selectRectangle(target, add = false) {
    if (!anchor || !state.attendance) anchor = target;
    if (!add) selected.clear();
    const people = state.attendance.rows.map((row) => row.employee_id);
    const dates = state.attendance.dates;
    const a = people.indexOf(anchor.employee_id), b = people.indexOf(target.employee_id);
    const c = dates.indexOf(anchor.date), d = dates.indexOf(target.date);
    if ([a, b, c, d].some((index) => index < 0)) return;
    for (let row = Math.min(a, b); row <= Math.max(a, b); row += 1) {
      const person = state.attendance.rows[row];
      for (let day = Math.min(c, d); day <= Math.max(c, d); day += 1) {
        const date = dates[day];
        selected.set(`${person.employee_id}:${date}`, {
          employee_id: person.employee_id, date, code: person.codes[date] || "",
        });
      }
    }
    repaintSelection();
  }

  function selectedCells() {
    if (selectionMonth !== state.attendanceMonth) {
      selected.clear();
      anchor = null;
      selectionMonth = state.attendanceMonth;
      if (focusTarget?.kind === "attendance") focusTarget = null;
    }
    return [...selected.values()].map((item) => ({
      employee_id: item.employee_id, date: item.date,
      code: state.attendance?.rows.find((row) => row.employee_id === item.employee_id)?.codes[item.date] || "",
    }));
  }

  function repaintSelection() {
    const cells = selectedCells();
    const indicator = document.getElementById("easy-cell-focus");
    if (indicator) indicator.hidden = !focusTarget || focusTarget.kind !== state.view
      || (state.view === "attendance" && cells.length > 0);
    document.querySelectorAll("#attendance-table td.easy-selected, td.easy-focus-cell").forEach((node) =>
      node.classList.remove("easy-selected", "easy-focus-cell"));
    document.querySelectorAll(".easy-focus-row").forEach((node) => node.classList.remove("easy-focus-row"));
    document.querySelectorAll(".easy-focus-column").forEach((node) => node.classList.remove("easy-focus-column"));
    document.querySelectorAll('#attendance-table [data-action="attendance-code"]').forEach((input) => {
      const active = selected.has(`${input.dataset.person}:${input.dataset.day}`);
      input.closest("td").classList.toggle("easy-selected", active);
      input.setAttribute("aria-selected", String(active));
    });
    const bar = document.getElementById("easy-cell-actions");
    if (bar) {
      bar.hidden = state.view !== "attendance" || !cells.length;
      bar.querySelector("[data-selection-count]").textContent = cells.length === 1
        ? `${personName(cells[0].employee_id)} · ${fullDate(cells[0].date)}`
        : `${new Set(cells.map((cell) => cell.employee_id)).size} personel · ${cells.length} gün hücresi seçili`;
      bar.querySelector("[data-selection-mode]").setAttribute("aria-pressed", String(selectionMode));
    }
    if (focusTarget) {
      const cell = focusTarget.kind === "schedule" ? focusTarget.cell
        : [...document.querySelectorAll('#attendance-table [data-action="attendance-code"]')].find((input) =>
          Number(input.dataset.person) === focusTarget.employee_id && input.dataset.day === focusTarget.date)?.closest("td");
      if (cell?.isConnected) {
        cell.classList.add("easy-focus-cell");
        cell.closest("tr").classList.add("easy-focus-row");
        const table = cell.closest("table");
        table.querySelectorAll("[data-shift-day], [data-day]").forEach((node) => {
          if ((node.dataset.day || node.dataset.shiftDay) === focusTarget.date) {
            node.closest("td")?.classList.add("easy-focus-column");
          }
        });
      }
    }
  }

  function trackCell(event) {
    const target = cellTarget(event.target);
    if (!target || state.formatMode) return;
    focusTarget = target;
    const indicator = document.getElementById("easy-cell-focus");
    if (indicator) {
      indicator.hidden = false;
      indicator.textContent = `${personName(target.employee_id)} · ${fullDate(target.date)}`;
    }
    if (target.kind !== "attendance") {
      repaintSelection();
      return;
    }
    selectedCells();
    const multi = event.ctrlKey || event.metaKey;
    if (event.type === "pointerdown" && (selectionMode || multi || event.shiftKey)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      dragging = true;
      if (event.shiftKey && anchor) selectRectangle(target, multi);
      else {
        if (!multi) selected.clear();
        const key = `${target.employee_id}:${target.date}`;
        if (multi && selected.has(key)) selected.delete(key);
        else selected.set(key, target);
        anchor = target;
      }
    } else if (event.type === "pointerdown" || event.type === "focusin") {
      const keepSelection = selected.has(`${target.employee_id}:${target.date}`)
        && (event.type === "focusin" || event.button === 2);
      if (!dragging && !selectionMode && !multi && !event.shiftKey && !keepSelection) {
        selected.clear();
        selected.set(`${target.employee_id}:${target.date}`, target);
        anchor = target;
      }
    }
    repaintSelection();
  }

  function changes(kind) {
    const before = state.editHistory[kind].baseline;
    const after = editSnapshot(kind);
    if (!before) return [];
    const result = [];
    const add = (label, left, right, restore) => {
      if (!equal(left, right)) result.push({ label, before: left, after: right, restore });
    };
    if (kind === "schedule") {
      const periods = new Set([state.scheduleMonth, ...Object.keys(before.neighborEntries), ...Object.keys(after.neighborEntries)]);
      for (const month of periods) {
        const original = month === state.scheduleMonth ? before.entries : before.neighborEntries[month] || [];
        const current = month === state.scheduleMonth ? after.entries : after.neighborEntries[month] || [];
        const snapshotRows = (snapshot) => month === state.scheduleMonth
          ? snapshot.entries : (snapshot.neighborEntries[month] ||= []);
        const ids = new Set([...original, ...current].map((row) => row.row_id));
        for (const id of ids) {
          const left = original.find((row) => row.row_id === id);
          const right = current.find((row) => row.row_id === id);
          const label = `${personName((right || left).employee_id)} · ${month}`;
          if (!left || !right) {
            add(`${label} · personel satırı`, left, right, (snapshot) => {
              const rows = snapshotRows(snapshot);
              const index = rows.findIndex((row) => row.row_id === id);
              if (index >= 0) rows.splice(index, 1);
              if (left) rows.push(clone(left));
            });
            continue;
          }
          for (const field of ["employee_id", "section", "slot"]) {
            add(`${label} · ${field === "section" ? "bölüm" : field === "slot" ? "sıra" : "personel"}`,
              left[field], right[field], (snapshot) => {
                snapshotRows(snapshot).find((row) => row.row_id === id)[field] = left[field];
              });
          }
          for (const day of new Set([...Object.keys(left.days), ...Object.keys(right.days)])) {
            add(`${label} · ${fullDate(day)}`, left.days[day], right.days[day], (snapshot) => {
              const days = snapshotRows(snapshot).find((row) => row.row_id === id).days;
              if (left.days[day] === undefined) delete days[day];
              else days[day] = clone(left.days[day]);
            });
          }
        }
      }
    } else {
      for (const day of new Set([...Object.keys(before.overrides), ...Object.keys(after.overrides)])) {
        for (const id of new Set([...Object.keys(before.overrides[day] || {}), ...Object.keys(after.overrides[day] || {})])) {
          add(`${personName(id)} · ${fullDate(day)}`, before.overrides[day]?.[id], after.overrides[day]?.[id],
            (snapshot) => {
              const original = before.overrides[day]?.[id];
              if (original === undefined) {
                delete snapshot.overrides[day]?.[id];
                if (!Object.keys(snapshot.overrides[day] || {}).length) delete snapshot.overrides[day];
              } else (snapshot.overrides[day] ||= {})[id] = original;
            });
        }
      }
      for (const field of ["deductions", "overtime"]) {
        for (const id of new Set([...Object.keys(before[field] || {}), ...Object.keys(after[field] || {})])) {
          add(`${personName(id)} · ${field === "overtime" ? "fazla mesai" : "kesinti"}`,
            before[field]?.[id], after[field]?.[id], (snapshot) => {
              if (before[field]?.[id] === undefined) delete snapshot[field][id];
              else snapshot[field][id] = before[field][id];
            });
        }
      }
    }
    return result;
  }

  function valueLabel(value) {
    if (value === undefined || value === null || value === "") return "Boş / otomatik";
    if (typeof value !== "object") return String(value);
    if (value.kind) return extrasItemLabel(value);
    if (value.days) return `${personName(value.employee_id)} · ${value.section} · ${Object.keys(value.days).length} gün`;
    return JSON.stringify(value);
  }

  function openChanges(kind = state.view, confirmation = false) {
    if (!["schedule", "attendance"].includes(kind)) {
      toast("Değişiklikleri görmek için Vardiya veya Puantaj sayfasını açın.", true);
      return Promise.resolve(false);
    }
    if (changesOpen) return Promise.resolve(false);
    changesOpen = true;
    return new Promise((resolve) => {
      const dialog = extrasDialog("easy-changes", "Kaydetmeden önce değişiklikler",
        "Yalnız bu çalışma ekranındaki kaydedilmemiş değişiklikler. Geri alma kayıtlı dönemlere dokunmaz.");
      const body = dialog.querySelector(".extras-body");
      const accept = button("Değişiklikleri kaydet", () => { accepted = true; dialog.close(); }, "button button-primary");
      let accepted = false;
      const render = () => {
        const items = changes(kind);
        body.replaceChildren();
        const count = document.createElement("p");
        count.textContent = `${items.length} değişiklik. Önceki değer → yeni değer`;
        body.append(count);
        const list = document.createElement("ul");
        list.className = "easy-change-list";
        items.forEach((item) => {
          const li = document.createElement("li");
          const text = document.createElement("span");
          const label = document.createElement("strong");
          label.textContent = item.label;
          const detail = document.createElement("span");
          detail.textContent = `${valueLabel(item.before)} → ${valueLabel(item.after)}`;
          text.append(label, detail);
          li.append(text, button("Bu değişikliği geri al", () => {
            const snapshot = editSnapshot(kind);
            item.restore(snapshot);
            applyEditSnapshot(kind, snapshot);
            recordEditChange(kind);
            render();
          }));
          list.append(li);
        });
        body.append(list);
        accept.disabled = !items.length;
      };
      if (confirmation) dialog.querySelector(".extras-actions").append(accept);
      dialog.addEventListener("close", () => {
        changesOpen = false;
        resolve(accepted);
      }, { once: true });
      render();
      showExtras(dialog);
    });
  }

  const lessons = [
    { title: "Personeli tanımlayın", target: ".easy-guide > summary", view: "schedule",
      steps: ["Personel bölümünden tek tek ad ekleyin veya toplu ad listesini yapıştırın.",
        "Çizelgenin ad soyad alanında ilgili personeli seçin. Ad eklemek tek başına vardiya atamaz."],
      check: "Adın doğru bölümde ve doğru satırda göründüğünü kontrol edin.",
      warning: "Kayıtları olan personeli silmek yerine gerektiğinde pasife alın." },
    { title: "Dönem ve bölümü seçin", target: "#schedule-month", view: "schedule",
      steps: ["Ay ve hafta seçicisinden çalışacağınız dönemi açın.", "Haftanın başındaki ve sonundaki tarihleri okuyun; komşu aya taşan günler olabilir."],
      check: "Ekrandaki tarih ile hazırlayacağınız belgenin dönemi aynı olmalı.",
      warning: "Kilitli ayda değişiklik yapmadan önce yetkili kontrolü gerekir." },
    { title: "Vardiyayı hazırlayın", target: "#schedule-table", view: "schedule",
      steps: ["Personelin gün kutusuna tıklayın; giriş/çıkış saati veya izin türü seçin.",
        "Tekrarlanan işler için önceki hafta, döngü veya toplu atamayı kullanın. Uygulamadan önce önizlemeyi okuyun."],
      check: "Geceyi aşan vardiyalarda çıkış gününü ve dinlenme uyarılarını kontrol edin.",
      warning: "Boş hücre her zaman izin değildir. İzin gününü uygun kodla açıkça belirtin." },
    { title: "İzinleri ve çakışmaları yönetin", tool: "Bağlantılı izin",
      steps: ["Personeli, izin türünü ve başlangıç/bitişi seçin. Sabit izin için geçerli hafta günlerini belirleyin.",
        "Bekleyen talepleri kesinleşmiş izinlerden ayırın. Aktarım önizlemesindeki dolu günleri inceleyin."],
      check: "İzinli günlere çalışma yazılmadığını ve kilitli ayların değişmediğini doğrulayın.",
      warning: "Çakışan rapor ve izin kayıtlarında hangi durumun uygulanacağına kendiniz karar verin." },
    { title: "Kontrol edin ve kaydedin", target: "#save-schedule", view: "schedule",
      steps: ["Kontrol aracındaki uyarıları açıp ilgili kişi ve güne gidin.",
        "Kaydet'e basın. Eski/yeni değerler ekranından değişiklikleri okuyun; istenmeyeni tek başına geri alın."],
      check: "Üstte “Bu bilgisayarda kayıtlı” bilgisini görün.",
      warning: "Kaydedilmemiş değişiklikler kayıtlı verilere dayanan çıktılara yansımaz." },
    { title: "Puantajı inceleyin", target: "#attendance-table", view: "attendance",
      steps: ["Kaydedilmiş vardiyalar puantaj kodlarını oluşturur. İş günü, hafta sonu ve tatil renkleri yalnız okumayı kolaylaştırır.",
        "Elle düzeltilen kodları ve toplamları kontrol edin. Yaptığınız düzeltmeleri ayrıca kaydedin."],
      check: "Toplamın açıklamasında kaynak günleri, saatleri ve elle girilmiş değerleri inceleyin.",
      warning: "Renk veya belge hazırlama işlemi ücret ve izin hakkı kurallarını kendiliğinden değiştirmez." },
    { title: "Tutanak ve belge hazırlayın", target: "#attendance-table", view: "attendance",
      steps: ["Puantajda tek gün seçin; çoklu seçim için Seçim düğmesini, Ctrl veya Shift tuşunu kullanın.",
        "Tutanak hazırla'yı açın. Personel ve seçilen günleri, belge türünü ve açıklamayı kontrol edip önizleyin."],
      check: "Aralıklı seçimlerde seçilmemiş günler belgeye eklenmemeli; her personel ayrı olmalı.",
      warning: "Oluşturulmuş dosya imzalanmış veya tebliğ edilmiş sayılmaz. İmzalı nüshayı ayrıca takip edin." },
    { title: "Çıktı, yedek ve ay kapanışı", target: "#open-month-close", view: "attendance",
      steps: ["Son kontrolleri ve kayıt işlemini bitirin. Çıktıda dönem ve sürüm bilgisini kontrol edin.",
        "Ay sonu kapanışındaki yedek, doğrulama ve kilitleme adımlarını tamamlayın. Yedeği geri yüklemeden önce içeriğini inceleyebilirsiniz."],
      check: "Kaydettiğiniz dosyanın konumunu ve belgenin güncelliğini doğrulayın.",
      warning: "EXE dosyasını tek başına taşımak kayıtları taşımaz; VardiyaPuantaj.exe'nin yanındaki data klasörünü birlikte koruyun." },
  ];

  async function openGuide() {
    const settings = state.uiSettings.easy_guide || { step: 0, completed: [], dismissed: false };
    let progress = clone(settings);
    const dialog = extrasDialog("easy-detailed-guide", "Adım adım kullanım rehberi",
      "Bölümleri istediğiniz sırada açabilirsiniz. İlerleme bu uygulamanın ayarlarında saklanır.");
    const body = dialog.querySelector(".extras-body");
    const render = () => {
      const lesson = lessons[progress.step];
      body.innerHTML = `<nav class="easy-guide-index" aria-label="Rehber bölümleri">${lessons.map((item, index) =>
        `<button type="button" class="button button-outline" data-lesson="${index}" aria-current="${index === progress.step ? "step" : "false"}">${progress.completed.includes(index) ? "✓ " : ""}${index + 1}. ${escapeHtml(item.title)}</button>`).join("")}</nav>
        <h3>${progress.step + 1}. ${escapeHtml(lesson.title)}</h3>
        <ol>${lesson.steps.map((text) => `<li>${escapeHtml(text)}</li>`).join("")}</ol>
        <p><strong>Sonucu kontrol edin:</strong> ${escapeHtml(lesson.check)}</p>
        <p><strong>Dikkat:</strong> ${escapeHtml(lesson.warning)}</p>`;
      const actions = document.createElement("div");
      actions.className = "easy-inline-actions";
      actions.append(button("İlgili alanı göster", async () => {
        if (lesson.tool) {
          dialog.close();
          await tool(lesson.tool);
          return;
        }
        if (lesson.view !== state.view) await switchView(lesson.view);
        if (state.view !== lesson.view) return;
        if (state.uiSettings.easy_ribbons?.[lesson.view] === false) {
          await saveUiSettings({ easy_ribbons: { ...state.uiSettings.easy_ribbons, [lesson.view]: true } });
        }
        dialog.close();
        let target = document.querySelector(lesson.target);
        for (let parent = target?.parentElement; parent; parent = parent.parentElement) {
          if (parent.matches("details")) parent.open = true;
        }
        if (!target || !target.getClientRects().length) {
          target = document.querySelector(`#${lesson.view}-view .easy-ribbon-toggle`)
            || document.querySelector(`#${lesson.view}-view .control-strip`);
          toast("İşlem alanı gizliyse araç şeridini veya Personel bölümünü açın.");
        }
        target?.scrollIntoView({ block: "center", inline: "nearest" });
        target?.classList.add("easy-guide-target");
        target?.focus({ preventScroll: true });
        setTimeout(() => target?.classList.remove("easy-guide-target"), 5000);
      }));
      actions.append(button(progress.completed.includes(progress.step) ? "Tamamlandı · yeniden işaretle" : "Bu bölümü tamamladım", async () => {
        const next = clone(progress);
        if (!next.completed.includes(next.step)) next.completed.push(next.step);
        next.step = Math.min(7, next.step + 1);
        await saveUiSettings({ easy_guide: next });
        progress = next;
        render();
      }));
      actions.append(button("Gerçek kayıtlardan ayrı örnekle öğren", () => openTraining()));
      body.append(actions);
      body.querySelectorAll("[data-lesson]").forEach((node) => node.addEventListener("click", () => run(async () => {
        const next = { ...clone(progress), step: Number(node.dataset.lesson) };
        await saveUiSettings({ easy_guide: next });
        progress = next;
        render();
      })));
    };
    render();
    showExtras(dialog);
  }

  function openTraining() {
    const dialog = extrasDialog("easy-training", "Risksiz örnek çalışma",
      "Bu örnek yalnız bu pencerededir. Personel, vardiya, puantaj veya veritabanına kaydedilmez.");
    const body = dialog.querySelector(".extras-body");
    const weekdays = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma"];
    const initialSample = [
      { name: "Örnek Personel 1", schedule: ["X", "X", "H", "X", "X"], attendance: ["X", "X", "R", "X", "X"] },
      { name: "Örnek Personel 2", schedule: ["X", "H", "X", "X", "X"], attendance: ["X", "H", "X", "X", "X"] },
    ];
    let sample = structuredClone(initialSample);
    const differenceBaseline = structuredClone(initialSample);
    const complete = { leave: false, difference: false, output: false };
    body.innerHTML = `
      <p>Bu üç kısa görev yalnızca aşağıdaki örnek kayıtları bellekte değiştirir. Gerçek veriler ve veritabanı kullanılmaz.</p>
      <section>
        <h3>Yıllık izin girin</h3>
        <p>Örnek Personel 1 için Cuma gününe yıllık izin (S) girin.</p>
        <label>Gün
          <select id="easy-training-leave-day">
            ${weekdays.map((day, index) => `<option value="${index}">${day}</option>`).join("")}
          </select>
        </label>
        <label>Puantaj kodu
          <select id="easy-training-leave-code">
            <option value="S">S · Yıllık izin</option>
            <option value="R">R · Rapor</option>
            <option value="H">H · Hafta tatili</option>
          </select>
        </label>
        <button class="button button-outline" type="button" data-training-check="leave">Örneğe uygula ve sonucu kontrol et</button>
        <p role="status" data-training-result="leave">Henüz kontrol edilmedi.</p>
      </section>
      <section>
        <h3>Farkı bulun</h3>
      <p>Başlangıç planı ile puantajdaki tek fark olan kişi ve günü seçin.</p>
        <label>Kişi ve gün
          <select id="easy-training-difference">
            ${sample.flatMap((person, personIndex) => weekdays.map((day, dayIndex) =>
              `<option value="${personIndex}:${dayIndex}">${person.name} · ${day}</option>`)).join("")}
          </select>
        </label>
        <button class="button button-outline" type="button" data-training-check="difference">Farkı kontrol et</button>
        <p role="status" data-training-result="difference">Henüz kontrol edilmedi.</p>
      </section>
      <section>
        <h3>Örnek çıktıyı alın</h3>
        <p>Uyguladığınız örnek puantaj kodlarını tablo olarak oluşturun.</p>
        <button class="button button-outline" type="button" data-training-check="output">Örnek çıktıyı oluştur</button>
        <div data-training-output aria-live="polite"></div>
        <p role="status" data-training-result="output">Henüz çıktı oluşturulmadı.</p>
      </section>
      <p role="status" data-training-score>Doğru görev: 0/3</p>
      <button class="button button-light" type="button" data-training-reset>Örneği baştan başlat</button>`;

    const renderScore = () => {
      const score = Object.values(complete).filter(Boolean).length;
      body.querySelector("[data-training-score]").textContent = `Doğru görev: ${score}/3`;
    };
    body.querySelector('[data-training-check="leave"]').addEventListener("click", () => {
      const dayIndex = Number(body.querySelector("#easy-training-leave-day").value);
      const code = body.querySelector("#easy-training-leave-code").value;
      sample[0].attendance[dayIndex] = code;
      complete.leave = dayIndex === 4 && code === "S";
      body.querySelector('[data-training-result="leave"]').textContent = complete.leave
        ? `Doğru sonuç: ${sample[0].name} · ${weekdays[dayIndex]} · ${sample[0].attendance[dayIndex]} (yıllık izin).`
        : "Görev tamamlanmadı: örnekte Cuma günü için S kodu kullanılmalı.";
      renderScore();
    });
    body.querySelector('[data-training-check="difference"]').addEventListener("click", () => {
      const mismatches = [];
      differenceBaseline.forEach((person, personIndex) => person.schedule.forEach((code, dayIndex) => {
        if (code !== person.attendance[dayIndex]) mismatches.push(`${personIndex}:${dayIndex}`);
      }));
      const selected = body.querySelector("#easy-training-difference").value;
      complete.difference = mismatches.length === 1 && mismatches[0] === selected;
      const [personIndex, dayIndex] = selected.split(":").map(Number);
      const person = differenceBaseline[personIndex];
      body.querySelector('[data-training-result="difference"]').textContent = complete.difference
        ? `Doğru fark: ${person.name} · ${weekdays[dayIndex]} · plan ${person.schedule[dayIndex]}, puantaj ${person.attendance[dayIndex]}.`
        : `Doğru değil: örnek kayıtlarda ${mismatches.length} fark var; kişi, gün ve kodları birlikte kontrol edin.`;
      renderScore();
    });
    body.querySelector('[data-training-check="output"]').addEventListener("click", () => {
      const output = body.querySelector("[data-training-output]");
      output.replaceChildren();
      const table = document.createElement("table");
      table.className = "archive-table extras-table";
      const head = document.createElement("tr");
      ["Personel", ...weekdays].forEach((label) => {
        const cell = document.createElement("th");
        cell.textContent = label;
        head.append(cell);
      });
      const header = document.createElement("thead");
      header.append(head);
      const rows = document.createElement("tbody");
      sample.forEach((person, personIndex) => {
        const row = document.createElement("tr");
        const name = document.createElement("th");
        name.scope = "row";
        name.textContent = person.name;
        row.append(name);
        person.attendance.forEach((code, dayIndex) => {
          const cell = document.createElement("td");
          cell.dataset.trainingCell = `${personIndex}-${dayIndex}`;
          cell.textContent = code;
          row.append(cell);
        });
        rows.append(row);
      });
      table.append(header, rows);
      output.append(table);
      const matchesAppliedLeave = sample[0].attendance[4] === "S";
      complete.output = matchesAppliedLeave
        && sample.length === 2
        && sample.every((person) => person.attendance.length === weekdays.length);
      body.querySelector('[data-training-result="output"]').textContent = complete.output
        ? "Çıktı doğru: iki örnek personel ve beş gün gösteriliyor; Cuma hücresinde S var."
        : "Çıktı oluşturuldu ancak örnek izin sonucu beklenen Cuma · S kaydıyla eşleşmiyor.";
      renderScore();
    });
    body.querySelector("[data-training-reset]").addEventListener("click", () => {
      sample = structuredClone(initialSample);
      Object.keys(complete).forEach((key) => { complete[key] = false; });
      body.querySelector("#easy-training-leave-day").value = "0";
      body.querySelector("#easy-training-leave-code").value = "S";
      body.querySelector("#easy-training-difference").value = "0:0";
      body.querySelector("[data-training-output]").replaceChildren();
      ["leave", "difference", "output"].forEach((key) => {
        body.querySelector(`[data-training-result="${key}"]`).textContent = "Henüz kontrol edilmedi.";
      });
      renderScore();
    });
    showExtras(dialog);
  }

  async function openWorkspaces() {
    const dialog = extrasDialog("easy-workspaces", "Çalışma düzenleri",
      "Menü, araç şeritleri ve ekran yakınlaştırmasını adlandırıp saklayın. Çizelge verileri değişmez.");
    const body = dialog.querySelector(".extras-body");
    const render = () => {
      body.innerHTML = '<label>Yeni düzenin adı<input id="easy-workspace-name" maxlength="60" placeholder="Ör. Puantaj kontrolü"></label>';
      body.append(button("Geçerli düzeni kaydet", async () => {
        if (workspaceBusy) return;
        const name = body.querySelector("input").value.trim();
        if (!name) throw new Error("Çalışma düzenine bir ad verin.");
        const current = state.uiSettings.easy_workspaces || [];
        const existing = current.findIndex((item) => item.name.toLocaleLowerCase("tr-TR") === name.toLocaleLowerCase("tr-TR"));
        if (existing >= 0 && !window.confirm("Aynı adlı çalışma düzeni güncellensin mi?")) return;
        const item = {
          name, menu: clone(state.uiSettings.easy_menu),
          ribbons: clone(state.uiSettings.easy_ribbons),
          zoom: Object.fromEntries(["schedule", "attendance"].map((kind) =>
            [kind, Math.round((Number(document.querySelector(`#${kind}-table`)?.style.zoom) || 1) * 100)])),
        };
        const items = clone(current);
        if (existing >= 0) items[existing] = item;
        else items.push(item);
        await saveUiSettings({ easy_workspaces: items });
        render();
      }, "button button-primary"));
      const list = document.createElement("ul");
      list.className = "easy-change-list";
      (state.uiSettings.easy_workspaces || []).forEach((item, index) => {
        const li = document.createElement("li");
        const label = document.createElement("strong");
        label.textContent = item.name;
        li.append(label, button("Uygula", async () => {
          if (workspaceBusy) return;
          workspaceBusy = true;
          try {
            await saveUiSettings({ easy_menu: item.menu, easy_ribbons: item.ribbons, easy_active_workspace: item.name });
            sessionZoom = clone(item.zoom);
            applyZoom();
            toast(`“${item.name}” çalışma düzeni uygulandı. Çizelge verileri korunuyor.`);
          } finally { workspaceBusy = false; }
        }), button("Sil", async () => {
          if (!window.confirm(`“${item.name}” çalışma düzeni silinsin mi? Çizelgeler silinmez.`)) return;
          await saveUiSettings({
            easy_workspaces: state.uiSettings.easy_workspaces.filter((_, position) => position !== index),
            easy_active_workspace: state.uiSettings.easy_active_workspace === item.name ? "" : state.uiSettings.easy_active_workspace || "",
          });
          render();
        }));
        list.append(li);
      });
      body.append(list);
    };
    render();
    showExtras(dialog);
  }

  let sessionZoom = null;
  let activeWorkspace = "";
  function applyZoom() {
    if (activeWorkspace !== (state.uiSettings.easy_active_workspace || "")) {
      activeWorkspace = state.uiSettings.easy_active_workspace || "";
      sessionZoom = clone((state.uiSettings.easy_workspaces || []).find((item) => item.name === activeWorkspace)?.zoom || null);
    }
    if (!sessionZoom) return;
    for (const kind of ["schedule", "attendance"]) {
      const table = document.querySelector(`#${kind}-table`);
      if (table) table.style.zoom = String(sessionZoom[kind] / 100);
    }
  }

  function openContext(node, x, y) {
    const target = cellTarget(node);
    if (!target || state.formatMode) return;
    document.getElementById("easy-context")?.remove();
    const menu = document.createElement("div");
    menu.id = "easy-context";
    menu.className = "easy-context";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", "Seçili hücre işlemleri");
    const add = (text, action) => {
      const item = button(text, async () => { menu.remove(); await action(); }, "");
      item.setAttribute("role", "menuitem");
      menu.append(item);
    };
    if (target.kind === "schedule") {
      add("Vardiya / izin düzenle", () => openCellQuickEntries(target.cell));
      add("Bu vardiya için tutanak / belge hazırla", () => {
        if (state.scheduleDirty || state.neighborDirty?.size) {
          throw new Error("Önce vardiya değişikliklerini kaydedin; tutanak kaydedilmiş vardiya bilgisine bağlanacak.");
        }
        return window.documentWorkflows.openSelection([
          { employee_id: target.employee_id, date: target.date, code: "" },
        ], { editable: true });
      });
      const quickSettings = document.getElementById("open-quick-entry-settings");
      if (quickSettings && !quickSettings.disabled && !quickSettings.hidden) {
        add("Hızlı giriş ayarları", () => quickSettings.click());
      }
      add("İzin kaydı ve aktarım", () => tool("Bağlantılı izin"));
      add("Kaydetmeden önce değişiklikler", () => openChanges("schedule"));
    } else {
      if (!selected.has(`${target.employee_id}:${target.date}`)) {
        selected.clear();
        selected.set(`${target.employee_id}:${target.date}`, target);
      }
      add("Kodu düzenle", () => node.focus());
      add("Kodu kopyala", () => { clipboard = target.code; toast("Puantaj kodu kopyalandı."); });
      if (clipboard !== null) add("Kodu seçili günlere yapıştır", () => {
        const cells = selectedCells();
        if (!window.confirm(`${cells.length} hücreye “${clipboard || "boş"}” kodu girilsin mi? Mevcut kodlar değişebilir.`)) return;
        for (const cell of cells) {
          const input = [...document.querySelectorAll('[data-action="attendance-code"]')].find((item) =>
            Number(item.dataset.person) === cell.employee_id && item.dataset.day === cell.date);
          if (!input) throw new Error("Seçili hücre artık görünmüyor. Filtreyi kaldırıp yeniden seçin.");
          input.value = clipboard;
          handleAttendanceEdit({ target: input });
        }
      });
      add("Tutanak / belge hazırla", () => window.documentWorkflows.openSelection(selectedCells(), { editable: true }));
      add("Hesap açıklamasını göster", () => window.documentWorkflows.openCalculation(target.employee_id));
      add("Kaydetmeden önce değişiklikler", () => openChanges("attendance"));
    }
    add("Biçimlendirme araçları", () => {
      const panel = document.querySelector(`#${target.kind}-view .format-panel`);
      if (!panel) throw new Error("Biçimlendirme alanı bulunamadı.");
      panel.open = true;
      const toggle = panel.querySelector("[data-format-mode]");
      if (toggle && !toggle.checked) {
        toggle.checked = true;
        toggle.dispatchEvent(new Event("change", { bubbles: true }));
      }
      panel.scrollIntoView({ block: "nearest" });
    });
    document.body.append(menu);
    const rect = node.getBoundingClientRect();
    menu.style.left = `${Math.max(4, Math.min(x ?? rect.left, innerWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${Math.max(4, Math.min(y ?? rect.bottom, innerHeight - menu.offsetHeight - 8))}px`;
    menu.querySelector("button")?.focus();
    repaintSelection();
  }

  function reportError(message) {
    let panel = document.getElementById("easy-problem");
    if (!panel) {
      panel = document.createElement("div");
      panel.id = "easy-problem";
      panel.className = "easy-problem";
      panel.setAttribute("role", "alert");
    }
    const dialog = document.activeElement?.closest("dialog[open]")
      || [...document.querySelectorAll("dialog[open]")].at(-1);
    (dialog || document.querySelector("main")).prepend(panel);
    panel.replaceChildren();
    const text = document.createElement("p");
    text.textContent = message;
    panel.append(text);
    const folded = message.toLocaleLowerCase("tr-TR");
    if (folded.includes("kilit")) panel.append(button("Kayıtlı ayları aç", () => switchView("archive")));
    else if (folded.includes("personel")) panel.append(button("Personel listesini aç", () => tool("Personel")));
    else panel.append(button("İlgili rehberi aç", openGuide));
    panel.append(button("Mesajı kapat", () => panel.remove()));
  }

  function setup() {
    const main = document.querySelector("main");
    const indicator = document.createElement("p");
    indicator.id = "easy-cell-focus";
    indicator.className = "easy-cell-focus";
    indicator.setAttribute("role", "status");
    indicator.hidden = true;
    main.prepend(indicator);
    const bar = document.createElement("div");
    bar.id = "easy-cell-actions";
    bar.className = "easy-cell-actions";
    bar.hidden = true;
    bar.innerHTML = '<span data-selection-count aria-live="polite"></span>';
    const toggle = button("Çoklu seçim", () => {
      selectionMode = !selectionMode;
      if (selectionMode && !selected.size) toast("Gün kutularını sürükleyin. Shift dikdörtgen, Ctrl ayrı gün seçer.");
      toggle.textContent = selectionMode ? "Seçimi bitir" : "Çoklu seçim";
      toggle.setAttribute("aria-pressed", String(selectionMode));
    });
    toggle.dataset.selectionMode = "";
    toggle.setAttribute("aria-pressed", "false");
    bar.append(toggle, button("Tutanak hazırla", () => window.documentWorkflows.openSelection(selectedCells(), { editable: true })),
      button("Seçimi temizle", () => { selected.clear(); anchor = null; repaintSelection(); }));
    document.getElementById("attendance-table")?.closest(".table-frame")?.after(bar);
    if (!bar.isConnected) document.getElementById("attendance-view").append(bar);
    document.addEventListener("pointerdown", trackCell, true);
    document.addEventListener("focusin", trackCell);
    document.addEventListener("pointermove", (event) => {
      if (!dragging) return;
      const target = attendanceTarget(event.target);
      if (target) selectRectangle(target, event.ctrlKey || event.metaKey);
    });
    document.addEventListener("pointerup", () => { dragging = false; });
    document.addEventListener("pointercancel", () => { dragging = false; });
    document.addEventListener("contextmenu", (event) => {
      if (!cellTarget(event.target) || state.formatMode) return;
      event.preventDefault();
      openContext(event.target, event.clientX, event.clientY);
    });
    document.addEventListener("click", (event) => {
      const calculation = event.target.closest("[data-explain-person]");
      if (calculation) {
        event.preventDefault();
        run(() => window.documentWorkflows.openCalculation(Number(calculation.dataset.explainPerson)));
      } else if (attendanceTarget(event.target) && (selectionMode || event.ctrlKey || event.metaKey || event.shiftKey)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      } else if (event.target.closest("#open-guide")) {
        event.preventDefault();
        event.stopImmediatePropagation();
        run(openGuide);
      } else if (!event.target.closest("#easy-context")) document.getElementById("easy-context")?.remove();
    }, true);
    document.addEventListener("change", (event) => {
      if (sessionZoom && event.target.matches("[data-format-zoom]")) {
        const kind = event.target.closest("#attendance-view") ? "attendance" : "schedule";
        sessionZoom[kind] = Number(event.target.value);
      }
    }, true);
    document.addEventListener("ui-settings-changed", applyZoom);
    document.addEventListener("keydown", (event) => {
      const menu = document.getElementById("easy-context");
      if (menu && ["Escape", "ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        if (event.key === "Escape") { menu.remove(); return; }
        const nodes = [...menu.querySelectorAll("button")];
        const index = nodes.indexOf(document.activeElement);
        nodes[event.key === "Home" ? 0 : event.key === "End" ? nodes.length - 1
          : (index + (event.key === "ArrowDown" ? 1 : -1) + nodes.length) % nodes.length].focus();
      }
      const target = attendanceTarget(event.target);
      if (target && event.shiftKey && ["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"].includes(event.key)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        const people = state.attendance.rows.map((row) => row.employee_id);
        const dates = state.attendance.dates;
        const row = people.indexOf(target.employee_id) + (event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0);
        const day = dates.indexOf(target.date) + (event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0);
        if (row < 0 || day < 0 || row >= people.length || day >= dates.length) return;
        const next = { employee_id: people[row], date: dates[day] };
        if (!anchor) anchor = target;
        selectRectangle(next);
        const input = [...document.querySelectorAll('[data-action="attendance-code"]')].find((node) =>
          Number(node.dataset.person) === next.employee_id && node.dataset.day === next.date);
        dragging = true;
        input?.focus();
        dragging = false;
      }
    }, true);
    const observer = new MutationObserver(() => {
      applyZoom();
      repaintSelection();
      const bar = document.getElementById("easy-cell-actions");
      if (bar) bar.hidden = state.view !== "attendance" || !selectedCells().length;
    });
    for (const kind of ["schedule", "attendance"]) {
      observer.observe(document.getElementById(`${kind}-table`), { childList: true });
    }
  }

  const originalPeriodCheck = openPeriodCheckAsync;
  openPeriodCheckAsync = async function explainedPeriodCheck(kind) {
    await originalPeriodCheck(kind);
    for (const group of document.querySelectorAll("#period-check-content .period-check-group")) {
      const heading = group.querySelector("h3")?.textContent.toLocaleLowerCase("tr-TR") || "";
      let advice = "Uyarıyı seçerek ilgili hücreye gidin. Personel ve tarihi doğrulayın; gerekliyse planı veya elle girilen puantajı düzeltin ve kontrolü yeniden çalıştırın.";
      if (/dinlenme|çakış|gece/.test(heading)) advice = "Önce önceki ve sonraki günün vardiya başlangıç/bitiş saatlerini birlikte inceleyin. Gece vardiyasının ertesi güne taşmasını ve kayıtlı dinlenme kurallarını kontrol edin; çalışma saatlerini doğrulamadan uyarıyı kapatmayın.";
      else if (/izin|tercih/.test(heading)) advice = "Personelin izin kaydını, onay durumunu ve geçerli tarihlerini kontrol edin. Bağlı izin dolu bir güne otomatik yazılmaz; önce çakışmayı inceleyin. Yıllık izin başlangıç bakiyesi ile kullanılan günleri Hesap açıklaması aracında karşılaştırın.";
      else if (/boş|kaydı olmayan|kadro|yetkinlik/.test(heading)) advice = "Önce personelin aktiflik ve işe giriş/çıkış tarihlerini doğrulayın. Boş günleri gerçek vardiya veya izin kaydına göre tamamlayın; kadro/ yetkinlik ihtiyacını uygun personelle karşılaştırın.";
      else if (/sınır|mesai|tatil/.test(heading)) advice = "Hesap açıklamasında gün gün çalışma ve manuel düzeltmeleri inceleyin. Kayıtlı hesaplama ayarları ile gerçek çalışma kayıtlarını karşılaştırın. Uyarı, tek başına ücret veya hukuki karar değildir.";
      const detail = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "Nasıl inceleyebilirim?";
      const text = document.createElement("p");
      text.textContent = advice;
      detail.append(summary, text);
      group.append(detail);
    }
  };

  const originalCommands = wsCommands;
  wsCommands = function easyCommands(query) {
    const fold = (text) => text.toLocaleLowerCase("tr-TR").replace(/ı/g, "i").normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    const text = fold(query);
    const aliases = [
      [/excel|cikti|yazdir/, "Yazdırma"], [/izinli|izin talep|izin kay|sabit izin/, "Bağlantılı izin"],
      [/rehber|nasil|yardim/, "Başlangıç rehberi"], [/tutanak|belge/, "Belge ve tutanak"],
      [/duzen|calisma alani/, "Çalışma düzenleri"], [/yedek/, "Yedeği incele"],
    ];
    const matched = aliases.filter(([pattern]) => pattern.test(text)).flatMap(([, prefix]) =>
      EXTRAS_MENU.map(([label, action], index) => ({ label, action, index })).filter((item) => item.label.startsWith(prefix)));
    return [...new Map([...originalCommands(query), ...matched].map((item) => [item.index, item])).values()].slice(0, 8);
  };
  const search = openSearch;
  openSearch = function easySearch() {
    search();
    const dialog = document.getElementById("search-dialog");
    const box = document.getElementById("ws-command-results");
    const input = document.getElementById("extras-search-input");
    if (!box || !input || input.dataset.easyPhrases) return;
    input.dataset.easyPhrases = "true";
    input.placeholder = "Ör. geçen ayı aç, izin kaydet, Excel çıktısı al";
    const update = () => {
      box.querySelectorAll("[data-easy-direct]").forEach((item) => item.remove());
      if (/ge[cç]en ay|[oö]nceki ay/i.test(input.value)) {
        const item = button("Önceki ayı aç · dönem değiştirir, veri yazmaz", async () => {
          if (!(await canLeaveCurrentView())) return;
          dialog.close();
          const kind = state.view === "attendance" ? "attendance" : "schedule";
          state[`${kind}Month`] = previousMonth(state[`${kind}Month`]);
          const picker = document.getElementById(`${kind}-month`);
          if (picker) picker.value = state[`${kind}Month`];
          await (kind === "attendance" ? loadAttendance() : loadSchedule());
        }, "ws-command");
        item.dataset.easyPrevious = "";
        item.dataset.easyDirect = "";
        box.prepend(item);
      } else if (/excel/i.test(input.value)) {
        const kind = state.view === "attendance" ? "attendance" : "schedule";
        const item = button(`${kind === "attendance" ? "Puantajı" : "Vardiyayı"} Excel olarak kaydet · dosya konumunu seçersiniz`, async () => {
          dialog.close();
          await downloadExcel(kind);
        }, "ws-command");
        item.dataset.easyDirect = "";
        box.prepend(item);
      }
    };
    input.addEventListener("input", update);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && box.querySelector("[data-easy-direct]")) {
        event.preventDefault();
        event.stopImmediatePropagation();
        box.querySelector("[data-easy-direct]").click();
      }
    }, true);
  };

  function openTaskFlows() {
    const flows = [
      { id: "new-month", title: "Yeni aya hazırlık", steps: [
        ["Ayı hazırlayın", "Doğru dönem ve kadroyu seçin; geçmiş ayı koruyun.", () => tool("Yeni ay sihirbazı")],
        ["Onaylı izinleri gözden geçirin", "Sabit ve tarihli izinlerin uygun boş günlere işlendiğini kontrol edin.", () => tool("Bağlantılı izinler")],
        ["Planı kontrol edin", "Eksik kadro, dinlenme ve çakışma bulgularını çözün.", () => tool("Eksik / hatalı gün kontrolü")],
        ["Yayın farkını onaylayın", "Kişi, bölüm, gün ve saat farklarını okuduktan sonra yayımlayın.", () => tool("Yayınlanan plan farkı")],
      ] },
      { id: "leave-document", title: "İzin, rapor ve belge", steps: [
        ["Personel talebini inceleyin", "Kabul edilen talep henüz çizelgeye uygulanmış sayılmaz.", () => tool("Personel talepleri")],
        ["Bağlantılı izni oluşturun", "Kişi, gerçek tarihler ve izin türünü seçip ayrı onay akışını tamamlayın.", () => tool("Bağlantılı izinler")],
        ["Puantaj günlerini seçin", "Yalnız ilgili kişinin gerçek günlerini seçin; ayrık günler aradaki günleri kapsamaz.", () => switchView("attendance")],
        ["Belgeyi takip edin", "Taslak, çıktı ve imzalı nüsha durumlarını birbirinden ayırın.", () => tool("Belge ve tutanak dosyası")],
      ] },
      { id: "pdks", title: "PDKS farklarını çözme", steps: [
        ["Cihaz kişilerini eşleştirin", "Her adı sicil/bordro koduyla doğrulayın. Ad benzerliği otomatik birleştirme değildir.", () => tool("PDKS dosyası")],
        ["Kaynakla planı karşılaştırın", "Tarih, giriş/çıkış ve eksik kayıt farklarını gerçek kaynağa göre inceleyin.", () => tool("PDKS ↔ plan mutabakatı")],
        ["Gerekli puantajı düzeltin", "PDKS aktarımı ücret veya puantajı kendiliğinden değiştirmez. Gerekli düzeltmeyi kaydedin.", () => switchView("attendance")],
        ["Son kontrolü yapın", "Düzeltilen kişi ve günleri kaynakla tekrar karşılaştırın.", () => tool("Eksik / hatalı gün kontrolü")],
      ] },
      { id: "emergency", title: "Acil devamsızlık", steps: [
        ["Kaydedilmemiş değişiklikleri inceleyin", "Vardiya, puantaj ve komşu ay taslaklarını güvenle kaydedin.", () => openChanges()],
        ["Devamsızlığı ve adayı inceleyin", "Kişi, gerçek gün ve gerekçeyi seçin. Uygun olmayan aday zorla atanamaz.", () => window.operationsTools.open("emergency")],
        ["Güncel planı kontrol edin", "Kaydedilen değişikliğin kadro ve dinlenme kurallarını karşıladığını doğrulayın.", () => window.operationsTools.open("quality")],
        ["İlgili plan farkını yayımlayın", "Yalnız incelenen güncel planı yayımlayın; yerel bildirim yasal tebliğ değildir.", () => tool("Yayınlanan plan farkı")],
      ] },
      { id: "closing", title: "Dönem sonu hazırlığı", steps: [
        ["Taslak farklarını kaydedin", "Kaydedilmemiş vardiya ve puantaj değişikliklerini inceleyin.", () => openChanges()],
        ["Dönemi kontrol edin", "Eksik günleri ve kaynak bulgularını çözün.", () => tool("Eksik / hatalı gün kontrolü")],
        ["Sorumlu onaylarını alın", "Hazırlayan, kontrol eden ve onaylayan bilgilerini gerçek işlemlere göre girin.", () => tool("Puantaj onay akışı")],
        ["Kaynak bağlı kapanışı tamamlayın", "Kapanış kontrol listesindeki güncel adımları gerçek sorumlu bilgileriyle tamamlayın.", () => window.operationsTools.open("closing")],
        ["Güncel çıktıyı alın", "Dönem, çıktı kodu ve kaynak güncelliğini denetleyip güvenli yedek alın.", () => tool("Ay sonu paketi")],
      ] },
    ];
    const dialog = extrasDialog("easy-task-flows", "Göreve göre yönlendirme",
      "Kişisel kontrol işaretleri seçili görev ve ay için saklanır. Sistem doğrulaması güncel kayıtlı kaynaklardan ayrıca alınır; kişisel işaret gerçek onay değildir.");
    const body = dialog.querySelector(".extras-body");
    let taskMonth = state.view === "attendance" ? state.attendanceMonth : state.scheduleMonth;
    let renderSequence = 0;
    const render = (flowId = state.uiSettings.easy_task?.flow || "new-month") => {
      const sequence = ++renderSequence;
      const flow = flows.find((item) => item.id === flowId);
      const progress = state.uiSettings.easy_task;
      const completed = progress?.flow === flowId && progress.month === taskMonth ? progress.checked : [];
      body.innerHTML = `<label>Yapmak istediğiniz iş<select id="easy-task-flow-choice">${flows.map((item) =>
        `<option value="${item.id}"${item.id === flowId ? " selected" : ""}>${escapeHtml(item.title)}</option>`).join("")}</select></label>
        <p>${escapeHtml(taskMonth)} · ${completed.length} / ${flow.steps.length} adım kişisel olarak kontrol edildi.</p>
        <p data-task-evidence-status role="status">Kayıtlı kaynaklar kontrol ediliyor…</p>`;
      flow.steps.forEach(([title, description, action], index) => {
        const section = document.createElement("section");
        section.dataset.taskStep = index;
        section.innerHTML = `<h3>${index + 1}. ${escapeHtml(title)}</h3><p>${escapeHtml(description)}</p>`;
        const actions = document.createElement("div");
        actions.className = "easy-inline-actions";
        actions.append(button("İlgili aracı aç", async () => {
          dialog.close();
          await saveUiSettings({ easy_task: { flow: flowId, month: taskMonth, checked: completed } });
          await action();
        }));
        actions.append(button(completed.includes(index) ? "Kontrol işaretini kaldır" : "Kendim kontrol ettim", async () => {
          await saveUiSettings({ easy_task: { flow: flowId, month: taskMonth, checked: completed.includes(index)
            ? completed.filter((value) => value !== index) : [...completed, index] } });
          render(flowId);
        }));
        section.append(actions);
        body.append(section);
      });
      body.querySelector("#easy-task-flow-choice").addEventListener("change", (event) => run(async () => {
        const selectedFlow = event.target.value;
        await saveUiSettings({ easy_task: { flow: selectedFlow, month: taskMonth, checked: [] } });
        render(selectedFlow);
      }));
      const evidence = {
        "new-month": { 0: ["plan", "Kayıtlı vardiya günleri var"], 2: ["quality", "Kayıtlı planda hata/uyarı yok"], 3: ["published", "Yayımlanan plan güncel"] },
        pdks: { 0: ["pdks", "Bu ayın PDKS kaynak kaydı var"], 3: ["quality", "Kayıtlı planda hata/uyarı yok"] },
        emergency: { 2: ["quality", "Kayıtlı planda hata/uyarı yok"], 3: ["published", "Yayımlanan plan güncel"] },
        closing: { 1: ["quality", "Kayıtlı planda hata/uyarı yok"], 2: ["approvals", "Puantaj onayları güncel"], 3: ["closing", "Kapanış kontrol adımları güncel"] },
      }[flowId] || {};
      request(`/api/schedule/workspace?month=${taskMonth}&evidence=1`).then((result) => {
        if (sequence !== renderSequence || !dialog.open) return;
        body.querySelector("[data-task-evidence-status]").textContent =
          `Doğrulama yalnız ${taskMonth} kayıtlı kaynaklarına aittir.${state.scheduleDirty || state.attendanceDirty ? " Kaydedilmemiş değişiklikler dahil değildir." : ""}`;
        for (const [index, [key, label]] of Object.entries(evidence)) {
          const text = document.createElement("p");
          text.className = "controls-hint";
          text.textContent = result.verification
            ? `Sistem doğrulaması: ${label} — ${result.verification[key] ? "evet" : "henüz doğrulanmadı"}.`
            : "Bu adımın sistem doğrulaması için yönetici yetkisi gerekir; kişisel kontrol ayrı tutulur.";
          body.querySelector(`[data-task-step="${index}"]`).append(text);
        }
      }).catch((error) => {
        if (sequence === renderSequence) body.querySelector("[data-task-evidence-status]").textContent = `Kaynak doğrulanamadı: ${error.message}`;
      });
    };
    render();
    showExtras(dialog);
  }

  function openExampleTables() {
    const dialog = extrasDialog("example-tables-dialog", "Vardiya ve puantaj örneği",
      "Bu tablolar yalnızca örnektir; personel, vardiya, puantaj veya veritabanı kayıtlarınız değiştirilmez.");
    dialog.querySelector(".extras-body").innerHTML = `
      <section class="example-table-section" aria-labelledby="example-schedule-title">
        <h3 id="example-schedule-title">Vardiya çizelgesi</h3>
        <div class="controls-table-wrap"><table class="archive-table extras-table">
          <thead><tr><th>Personel</th><th>Pazartesi</th><th>Salı</th><th>Çarşamba</th><th>Perşembe</th><th>Cuma</th></tr></thead>
          <tbody><tr><th scope="row">Örnek Personel 1</th><td>07:00–15:00</td><td>07:00–15:00</td><td>İzin</td><td>15:00–23:00</td><td>15:00–23:00</td></tr>
          <tr><th scope="row">Örnek Personel 2</th><td>15:00–23:00</td><td>İzin</td><td>07:00–15:00</td><td>07:00–15:00</td><td>07:00–15:00</td></tr></tbody>
        </table></div>
      </section>
      <section class="example-table-section" aria-labelledby="example-attendance-title">
        <h3 id="example-attendance-title">Puantaj tablosu</h3>
        <div class="controls-table-wrap"><table class="archive-table extras-table">
          <thead><tr><th>Personel</th><th>1</th><th>2</th><th>3</th><th>4</th><th>5</th><th>Açıklama</th></tr></thead>
          <tbody><tr><th scope="row">Örnek Personel 1</th><td>X</td><td>X</td><td>R</td><td>X</td><td>X</td><td>Örnek kayıt</td></tr>
          <tr><th scope="row">Örnek Personel 2</th><td>X</td><td>H</td><td>X</td><td>X</td><td>X</td><td>Örnek kayıt</td></tr></tbody>
        </table></div>
        <p class="controls-hint">Örnek kodlar: X = çalışma, H = hafta tatili, R = rapor. Gerçek puantaj ve ücret hesapları bu pencereden etkilenmez.</p>
      </section>`;
    showExtras(dialog);
  }

  EXTRAS_MENU.push(
    ["Çalışma düzenleri", openWorkspaces],
    ["Örnek tablo görünümü", openExampleTables],
    ["Kaydetmeden önce değişiklikler", () => openChanges()],
    ["Risksiz örnek çalışma", openTraining],
    ["Göreve göre yönlendirme", openTaskFlows],
  );
  const guideIndex = EXTRAS_MENU.findIndex(([label]) => label.startsWith("Başlangıç rehberi"));
  if (guideIndex >= 0) EXTRAS_MENU[guideIndex][1] = openGuide;
  async function trackViewOutput(output, kind) {
    const month = kind === "attendance" ? state.attendanceMonth : state.scheduleMonth;
    const draft = kind === "schedule" ? state.scheduleDirty
      : kind === "attendance" ? state.attendanceDirty : true;
    const sources = kind === "schedule" ? state.schedule?.source_revisions
      : kind === "attendance" ? state.attendance?.source_revisions : null;
    const result = await request("/api/view-output", {
      method: "POST",
      body: JSON.stringify({ kind, month, content: output.data.split(",")[1], draft: !!draft, sources: sources || null }),
    });
    return {
      ...output, data: `data:image/jpeg;base64,${result.content}`,
      rowHeights: [...output.rowHeights, result.footer_height],
    };
  }

  window.easyAssistant = {
    selectedCells, openChanges, openGuide, openTaskFlows, openContext, reportError,
    confirmChanges: (kind) => openChanges(kind, true),
    changes, lessons, selectRectangle, repaintSelection, trackViewOutput,
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup, { once: true });
  else setup();
})();
