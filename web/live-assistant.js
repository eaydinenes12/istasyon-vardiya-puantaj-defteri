"use strict";

(() => {
  const dialog = document.getElementById("live-assistant-dialog");
  const transcript = document.getElementById("live-assistant-transcript");
  const form = document.getElementById("live-assistant-form");
  const input = document.getElementById("live-assistant-input");
  const modelSelect = document.getElementById("live-assistant-model");
  const status = document.getElementById("live-assistant-status");
  const pending = document.getElementById("live-assistant-pending");
  const pendingSummary = document.getElementById("live-assistant-action-summary");
  const sendButton = document.getElementById("live-assistant-send");
  const micButton = document.getElementById("live-assistant-mic");
  const messages = [];
  let pendingAction = null;
  let pendingScheduleSource = null;
  let recognition = null;
  let voiceConsent = false;
  let busy = false;

  const sectionNames = {
    schedule: "Vardiya",
    attendance: "Puantaj",
    archive: "Arşiv",
    templates: "Tablo şablonları",
  };
  const attendanceLabels = {
    X: "Çalışma", H: "Hafta tatili", "G.Ç": "Geceden çıkış", S: "Yıllık izin",
    R: "Rapor", Ü: "Ücretsiz izin", G: "İşe gelmedi", "İST.": "İstifa",
    E: "Evlilik izni", K: "Karantina", Y: "Yarım gün",
  };
  const scheduleLabels = {
    work: "Çalışma", weekly_off: "Hafta tatili", overnight_exit: "Geceden çıkış",
    annual_leave: "Senelik izin", sick_leave: "Rapor", unpaid_leave: "Ücretsiz izin",
    marriage_leave: "Evlilik izni", quarantine: "Karantina", half_day: "Yarım gün",
    absent: "İşe gelmedi", resignation: "İstifa", custom: "Özel vardiya",
  };

  function setStatus(text, stateName = "") {
    status.textContent = text;
    status.dataset.state = stateName;
  }

  function dateLabel(value) {
    const parsed = new Date(`${value}T12:00:00`);
    return Number.isNaN(parsed.valueOf())
      ? value
      : parsed.toLocaleDateString("tr-TR", { day: "numeric", month: "long", year: "numeric" });
  }

  function addMessage(role, text, sources = []) {
    transcript.querySelector(".live-assistant-empty")?.remove();
    const wrapper = document.createElement("article");
    wrapper.className = "live-assistant-message";
    wrapper.dataset.role = role;
    const body = document.createElement("p");
    body.textContent = text;
    wrapper.append(body);
    if (role === "assistant") {
      const speak = document.createElement("button");
      speak.type = "button";
      speak.className = "live-assistant-speak";
      speak.textContent = "Sesli oku";
      speak.addEventListener("click", () => speakText(text));
      wrapper.append(speak);
      if (Array.isArray(sources)) {
        sources.forEach((source) => {
          if (!source || !["schedule", "attendance"].includes(source.kind)
              || !/^\d{4}-(0[1-9]|1[0-2])$/.test(source.month)
              || (source.date && (!/^\d{4}-\d{2}-\d{2}$/.test(source.date)
                || !source.date.startsWith(`${source.month}-`)))) return;
          const citation = document.createElement("div");
          citation.className = "live-assistant-source";
          const open = document.createElement("button");
          open.type = "button";
          open.className = "button button-outline";
          open.dataset.assistantSource = source.kind;
          open.textContent = source.kind === "schedule"
            ? `Plan gününü aç${source.date ? ` · ${dateLabel(source.date)}` : ""}${source.section ? ` · ${source.section}` : ""}`
            : `Puantaj hesabını aç · ${source.month}`;
          open.addEventListener("click", async () => {
            open.disabled = true;
            try {
              await openAssistantSource(source);
              setStatus(`Kaynak açıldı · ${source.month} · sürüm ${source.revision || "kayıt yok"}.`, "ready");
            } catch (error) {
              setStatus(`Kaynak açılamadı: ${error.message}`, "error");
            } finally {
              open.disabled = false;
            }
          });
          citation.append(open);
          const note = document.createElement("small");
          note.textContent = `${source.label || "Kayıtlı uygulama kaynağı"} · ${source.month} · rapor sürümü ${source.revision || "kayıt yok"}`;
          citation.append(note);
          wrapper.append(citation);
        });
      }
    }
    transcript.append(wrapper);
    transcript.scrollTop = transcript.scrollHeight;
  }

  async function openAssistantSource(source) {
    if (hasUnsavedChanges()) {
      throw new Error("Önce açık taslağı kaydedin veya uygulama içinden tamamlayın; taslağınız korunuyor.");
    }
    await loadTargetPeriod(source.kind, source.month);
    let target = null;
    if (source.kind === "schedule" && source.date) {
      const weekIndex = weekStarts(source.month).findIndex((weekStart) => {
        const days = Array.from({ length: 7 }, (_, offset) =>
          dateKey(new Date(weekStart.getFullYear(), weekStart.getMonth(), weekStart.getDate() + offset)));
        return days.includes(source.date);
      });
      if (weekIndex >= 0) await selectScheduleWeek(weekIndex);
      target = [...document.querySelectorAll("#schedule-table [data-shift-day]")]
        .find((cell) => cell.dataset.shiftDay === source.date
          && (!source.section || cell.dataset.shiftSection === source.section));
      target ||= document.querySelector("#schedule-week");
    } else if (source.kind === "attendance" && Number.isInteger(source.employee_id)) {
      target = [...document.querySelectorAll('#attendance-table input[data-action="attendance-code"]')]
        .find((cell) => Number(cell.dataset.person) === source.employee_id);
      target ||= document.querySelector("#attendance-table");
    } else {
      target = document.querySelector("#schedule-table");
    }
    if (!target) throw new Error("Kaynağın ilgili tablo alanı bulunamadı.");
    target.scrollIntoView({ block: "center", inline: "nearest" });
    target.classList.add("easy-guide-target");
    target.focus({ preventScroll: true });
    setTimeout(() => target.classList.remove("easy-guide-target"), 5000);
  }

  function speakText(text) {
    if (!("speechSynthesis" in window)) {
      setStatus("Bu cihazda sesli yanıt kullanılamıyor.", "error");
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "tr-TR";
    const voices = window.speechSynthesis.getVoices();
    utterance.voice = voices.find((voice) => voice.lang.toLocaleLowerCase("tr-TR").startsWith("tr")) || null;
    window.speechSynthesis.speak(utterance);
  }

  async function refreshModels() {
    const button = document.getElementById("refresh-live-assistant-models");
    button.disabled = true;
    modelSelect.disabled = true;
    setStatus("Yerel modeller aranıyor…");
    try {
      const result = await request("/api/assistant/models");
      const previous = modelSelect.value || localStorage.getItem("vp-live-assistant-model") || "";
      modelSelect.replaceChildren();
      for (const name of result.models) {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        modelSelect.append(option);
      }
      if (!result.models.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "Kurulu yerel model bulunamadı";
        modelSelect.append(option);
        setStatus("Ollama açık; model kurmak için `ollama pull qwen2.5:1.5b` komutunu çalıştırın.", "error");
      } else {
        modelSelect.value = result.models.includes(previous) ? previous : result.models[0];
        localStorage.setItem("vp-live-assistant-model", modelSelect.value);
        setStatus("Bağlantı yerel bilgisayardaki Ollama üzerinden.", "ready");
      }
    } catch (error) {
      modelSelect.replaceChildren(new Option("Ollama kullanılamıyor", ""));
      setStatus(error.message, "error");
    } finally {
      modelSelect.disabled = modelSelect.options.length < 2;
      button.disabled = false;
      updateSendState();
    }
  }

  function updateSendState() {
    sendButton.disabled = busy || !modelSelect.value || Boolean(pendingAction);
    input.disabled = busy || Boolean(pendingAction);
    micButton.disabled = busy || Boolean(pendingAction);
    document.getElementById("clear-live-assistant").disabled = busy;
    document.getElementById("refresh-live-assistant-models").disabled = busy;
  }

  function buildContext(text) {
    const month = state.view === "attendance" ? state.attendanceMonth : state.scheduleMonth;
    const normalize = (value) => value.normalize("NFC").toLocaleLowerCase("tr-TR");
    const words = ` ${normalize(text).replace(/[^\p{L}\p{N}\p{M}]+/gu, " ")} `;
    const people = (state.people || []).map((person) => person.name).filter((name) => {
      if (typeof name !== "string") return false;
      const key = normalize(name).replace(/[^\p{L}\p{N}\p{M}]+/gu, " ").trim();
      return key && words.includes(` ${key} `);
    }).slice(0, 60);
    return { view: state.view, month, people };
  }

  function previewContext() {
    const context = buildContext(input.value);
    document.getElementById("live-assistant-context").textContent =
      `Sayfa: ${sectionNames[context.view] || context.view} · Dönem: ${context.month} · `
      + `Mesajda adı geçen personel: ${context.people.join(", ") || "yok"}.`;
    return context;
  }

  function validateAction(action) {
    if (!action || typeof action !== "object" || Array.isArray(action)) return null;
    if (action.type === "navigate_view" && Object.keys(action).length === 2 &&
        Object.hasOwn(sectionNames, action.view)) return action;
    if (action.type === "create_backup" && Object.keys(action).length === 1) return action;
    if (action.type === "set_attendance_code" && Object.keys(action).length === 4 &&
        typeof action.employee_name === "string" && /^\d{4}-\d{2}-\d{2}$/.test(action.date) &&
        Object.hasOwn(attendanceLabels, action.code)) return action;
    if (action.type === "set_schedule_day" && typeof action.employee_name === "string" &&
        /^\d{4}-\d{2}-\d{2}$/.test(action.date) && Object.hasOwn(scheduleLabels, action.kind)) {
      const expectedKeys = action.kind === "work"
        ? ["type", "employee_name", "date", "kind", "start", "end"]
        : action.kind === "custom"
          ? ["type", "employee_name", "date", "kind", "custom_code"]
          : ["type", "employee_name", "date", "kind"];
      if (Object.keys(action).length !== expectedKeys.length ||
          expectedKeys.some((key) => !Object.hasOwn(action, key))) return null;
      if (action.kind === "work" && (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(action.start)
          || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(action.end))) return null;
      if (action.kind === "custom" && (typeof action.custom_code !== "string" ||
          !/^[A-ZÇĞİÖŞÜ0-9. -]{1,8}$/i.test(action.custom_code))) return null;
      return action;
    }
    return null;
  }

  function summarizeAction(action, previous = null) {
    if (action.type === "create_backup") return "Bu bilgisayarda şimdi yeni bir yedek oluşturulsun mu?";
    if (action.type === "set_attendance_code") {
      return `${action.employee_name} · ${dateLabel(action.date)} · ${attendanceLabels[action.code]} (${action.code}) olarak puantaj kaydı.`;
    }
    if (action.type === "set_schedule_day") {
      const hours = action.kind === "work" ? ` · ${action.start}–${action.end}` : "";
      const code = action.kind === "custom" ? ` · ${action.custom_code}` : "";
      const current = previous?.kind === "work"
        ? `Mevcut: ${previous.start || "başlangıç yok"}–${previous.end || "bitiş yok"}`
        : `Mevcut: ${previous?.kind ? scheduleLabels[previous.kind] || previous.kind : "boş gün"}`;
      return `${action.employee_name} · ${dateLabel(action.date)} · ${current} → Yeni: ${scheduleLabels[action.kind]}${hours}${code}.`;
    }
    return "";
  }

  async function showPendingAction(action) {
    const validated = validateAction(action);
    if (!validated) {
      addMessage("assistant", "İşlem önerisini güvenli biçimde doğrulayamadım; değişiklik yapılmadı.");
      return;
    }
    let previous = null;
    pendingScheduleSource = null;
    if (validated.type === "set_schedule_day") {
      try {
        const month = validated.date.slice(0, 7);
        const person = findPerson(validated.employee_name, state.people || []);
        const schedule = await request(`/api/schedule?month=${encodeURIComponent(month)}`);
        const rows = (schedule.entries || []).filter((row) => row.employee_id === person.id);
        const occupied = rows.filter((row) => row.days?.[validated.date]);
        const row = occupied.length === 1 ? occupied[0] : rows.length === 1 ? rows[0] : null;
        if (!row) {
          addMessage("assistant", rows.length
            ? "Bu kişinin birden fazla vardiya satırı var; değişiklik yapılmadan önce doğru satır seçilmelidir."
            : "Bu kişinin ilgili dönemde vardiya satırı bulunamadı; değişiklik yapılmadı.");
          return;
        }
        previous = row.days?.[validated.date] || null;
        pendingScheduleSource = {
          month, revision: schedule.revision || "", rowId: row.row_id,
          previous: previous ? structuredClone(previous) : null,
        };
      } catch (error) {
        addMessage("assistant", `Mevcut vardiya doğrulanamadı; değişiklik yapılmadı: ${error.message}`);
        return;
      }
    }
    pendingAction = validated;
    pendingSummary.textContent = summarizeAction(validated, previous);
    pending.hidden = false;
    updateSendState();
  }

  function hasUnsavedChanges() {
    return Boolean(state.scheduleDirty || state.attendanceDirty || state.neighborDirty?.size);
  }

  async function loadTargetPeriod(view, month) {
    if (hasUnsavedChanges()) {
      throw new Error("Önce açık vardiya/puantaj taslağını kaydedin veya uygulama içinden tamamlayın; taslağınız korunuyor.");
    }
    if (state.view !== view) await switchView(view);
    const key = view === "schedule" ? "scheduleMonth" : "attendanceMonth";
    state[key] = month;
    document.getElementById(`${view}-month`).value = month;
    if (view === "schedule") await loadSchedule();
    else await loadAttendance();
  }

  function findPerson(name, people) {
    const wanted = name.trim().replace(/\s+/g, " ").toLocaleLowerCase("tr-TR");
    const matches = people.filter((person) =>
      person.name.replace(/\s+/g, " ").toLocaleLowerCase("tr-TR") === wanted);
    if (matches.length !== 1) {
      const partial = people.filter((person) =>
        person.name.toLocaleLowerCase("tr-TR").includes(wanted)).slice(0, 5);
      const suggestions = partial.length ? ` Eşleşenler: ${partial.map((person) => person.name).join(", ")}.` : "";
      throw new Error(matches.length ? "Aynı adda birden fazla personel var; tam adı netleştirin." : `Personel bulunamadı.${suggestions}`);
    }
    return matches[0];
  }

  async function applyAttendanceCode(action) {
    const month = action.date.slice(0, 7);
    await loadTargetPeriod("attendance", month);
    if (state.attendanceDirty) throw new Error("Kaydedilmemiş puantaj taslağı var; önce kaydedin.");
    const person = findPerson(action.employee_name, state.attendance?.rows || []);
    const dateValues = state.attendance.overrides[action.date] ||= {};
    const id = String(person.employee_id);
    if (action.code === (person.automatic_codes?.[action.date] || "")) {
      delete dateValues[id];
    } else {
      dateValues[id] = action.code;
    }
    if (!Object.keys(dateValues).length) delete state.attendance.overrides[action.date];
    setAttendanceDirty();
    renderAttendance();
    await saveAttendance({ assistantConfirmed: true });
    if (state.attendanceDirty) throw new Error("Puantaj kaydedilemedi; değişiklik taslakta kaldı.");
  }

  async function applyScheduleDay(action, source) {
    const month = action.date.slice(0, 7);
    await loadTargetPeriod("schedule", month);
    if (state.scheduleDirty || state.neighborDirty.size) {
      throw new Error("Kaydedilmemiş vardiya taslağı var; önce kaydedin.");
    }
    if (!source || source.month !== month || state.schedule?.revision !== source.revision) {
      throw new Error("Önizlemeden sonra vardiya kaynağı değişti; güncel saatleri yeniden inceleyin.");
    }
    const sourceRow = state.schedule.entries.find((entry) => entry.row_id === source.rowId);
    if (!sourceRow || JSON.stringify(sourceRow.days?.[action.date] || null) !== JSON.stringify(source.previous)) {
      throw new Error("Önizlenen eski vardiya artık güncel değil; değişiklik yapılmadı.");
    }
    const person = findPerson(action.employee_name, state.people || []);
    const rows = state.schedule.entries.filter((row) => row.employee_id === person.id);
    const row = rows.find((item) => item.row_id === source.rowId);
    if (!row) {
      throw new Error(rows.length
        ? "Bu personelin birden fazla vardiya satırı var; istediğiniz satırı belirtin."
        : "Önce bu personeli vardiya tablosuna ekleyin.");
    }
    if (action.kind === "work") {
      updateDay(row.row_id, action.date, "work");
      updateDay(row.row_id, action.date, "work", action.start, "start");
      updateDay(row.row_id, action.date, "work", action.end, "end");
    } else if (action.kind === "custom") {
      updateDay(row.row_id, action.date, "custom", action.custom_code, "custom_code");
    } else {
      updateDay(row.row_id, action.date, action.kind);
    }
    await saveSchedule({ assistantConfirmed: true });
    if (state.scheduleDirty || state.neighborDirty.size) {
      throw new Error("Vardiya kaydedilemedi; değişiklik taslakta kaldı.");
    }
  }

  async function executeAction(action, source = null) {
    if (action.type === "navigate_view") {
      if (hasUnsavedChanges()) throw new Error("Kaydedilmemiş taslak var; önce kaydedin. Hiçbir taslak silinmedi.");
      await switchView(action.view);
      return `${sectionNames[action.view]} bölümü açıldı.`;
    }
    if (action.type === "create_backup") {
      await request("/api/backups", { method: "POST", body: JSON.stringify({}) });
      await loadBackups();
      return "Yedek oluşturuldu ve doğrulandı.";
    }
    if (action.type === "set_attendance_code") {
      await applyAttendanceCode(action);
      return "Puantaj değişikliği kaydedildi.";
    }
    if (action.type === "set_schedule_day") {
      await applyScheduleDay(action, source);
      return "Vardiya değişikliği kaydedildi.";
    }
    throw new Error("Bu işlem desteklenmiyor.");
  }

  async function sendMessage(text) {
    const content = text.trim();
    if (!content || busy || pendingAction) return;
    if (!modelSelect.value) {
      setStatus("Önce yerel bir yapay zekâ modeli kurup seçin.", "error");
      return;
    }
    addMessage("user", content);
    messages.push({ role: "user", content });
    while (messages.length > 12) messages.shift();
    const context = previewContext();
    input.value = "";
    previewContext();
    busy = true;
    updateSendState();
    setStatus("Yerel model yanıt hazırlıyor…");
    try {
      const result = await request("/api/assistant/chat", {
        method: "POST",
        body: JSON.stringify({ model: modelSelect.value, messages, context }),
      });
      const reply = typeof result.reply === "string" ? result.reply : "Yanıt alınamadı.";
      addMessage("assistant", reply, result.sources);
      messages.push({ role: "assistant", content: reply });
      if (result.action?.type === "navigate_view") {
        const action = validateAction(result.action);
        if (action) {
          try {
            addMessage("assistant", await executeAction(action));
          } catch (error) {
            addMessage("assistant", `Bölüm açılamadı: ${error.message}`);
          }
        } else {
          addMessage("assistant", "Bölüm önerisi geçersiz; hiçbir işlem yapılmadı.");
        }
      } else if (result.action) {
        await showPendingAction(result.action);
      }
      if (document.getElementById("live-assistant-speak").checked) speakText(reply);
      setStatus("Yanıt hazır.", "ready");
    } catch (error) {
      addMessage("assistant", `Asistana ulaşılamadı: ${error.message}`);
      setStatus(error.message, "error");
    } finally {
      busy = false;
      updateSendState();
      input.focus();
    }
  }

  document.getElementById("open-live-assistant").addEventListener("click", () => {
    previewContext();
    dialog.showModal();
    void refreshModels();
    input.focus();
  });
  document.getElementById("close-live-assistant").addEventListener("click", () => dialog.close());
  document.getElementById("refresh-live-assistant-models").addEventListener("click", refreshModels);
  document.getElementById("clear-live-assistant").addEventListener("click", () => {
    messages.length = 0;
    pendingAction = null;
    pendingScheduleSource = null;
    pending.hidden = true;
    transcript.replaceChildren();
    const empty = document.createElement("p");
    empty.className = "live-assistant-empty";
    empty.textContent = "Yeni sohbet başladı. Değişiklikler siz onaylamadan kaydedilmez.";
    transcript.append(empty);
    setStatus("Yeni sohbet hazır.", "ready");
    updateSendState();
    input.focus();
  });
  modelSelect.addEventListener("change", () => {
    localStorage.setItem("vp-live-assistant-model", modelSelect.value);
    updateSendState();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void sendMessage(input.value);
  });
  input.addEventListener("input", previewContext);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  document.getElementById("confirm-live-assistant-action").addEventListener("click", async (event) => {
    if (!pendingAction || busy) return;
    const confirmButton = event.currentTarget;
    const action = pendingAction;
    const source = pendingScheduleSource;
    pendingAction = null;
    pendingScheduleSource = null;
    pending.hidden = true;
    busy = true;
    confirmButton.disabled = true;
    updateSendState();
    try {
      addMessage("assistant", await executeAction(action, source));
      setStatus("İşlem tamamlandı.", "ready");
    } catch (error) {
      addMessage("assistant", `İşlem uygulanamadı: ${error.message}`);
      setStatus(error.message, "error");
    } finally {
      busy = false;
      confirmButton.disabled = false;
      updateSendState();
      input.focus();
    }
  });
  document.getElementById("cancel-live-assistant-action").addEventListener("click", () => {
    pendingAction = null;
    pendingScheduleSource = null;
    pending.hidden = true;
    addMessage("assistant", "İşlem iptal edildi; hiçbir değişiklik yapılmadı.");
    updateSendState();
    input.focus();
  });
  micButton.addEventListener("click", () => {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      setStatus("Bu tarayıcı ses tanımayı desteklemiyor. Metin yazarak devam edebilirsiniz.", "error");
      return;
    }
    if (!voiceConsent) {
      const consent = window.confirm(
        "Mikrofon girişi tarayıcının konuşma tanıma hizmetini kullanır. Sağlayıcıya göre ses çevrimiçi işlenebilir. Devam edilsin mi?",
      );
      if (!consent) return;
      voiceConsent = true;
    }
    try {
      recognition?.abort();
      recognition = new Recognition();
      recognition.lang = "tr-TR";
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;
      recognition.onstart = () => setStatus("Dinliyorum; konuşma metne çevrilince düzenleyip gönderebilirsiniz.", "ready");
      recognition.onresult = (event) => {
        input.value = event.results?.[0]?.[0]?.transcript || "";
        previewContext();
        input.focus();
      };
      recognition.onerror = (event) => setStatus(`Ses tanıma başarısız: ${event.error}. Metin yazarak devam edebilirsiniz.`, "error");
      recognition.onend = () => { recognition = null; };
      recognition.start();
    } catch (error) {
      setStatus(`Mikrofon başlatılamadı: ${error.message}`, "error");
    }
  });
  dialog.addEventListener("close", () => {
    recognition?.abort();
    recognition = null;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  });
})();
