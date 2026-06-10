/**
 * form_filler.js — Lógica de llenado del wizard Easy Apply de LinkedIn.
 * Lógica basada directamente en JOBS_LKD.py (Selenium RPA).
 */

const LIFormFiller = (() => {

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  function rand(a, b) { return Math.floor(Math.random() * (b - a + 1)) + a; }
  function qAll(sel, root = document) { return [...(root || document).querySelectorAll(sel)]; }

  // ── label del campo ──────────────────────────────────────────────────────────

  function labelText(el) {
    const id = el.id;
    if (id) {
      const lbl = document.querySelector(`label[for="${id}"]`);
      if (lbl) return lbl.textContent.trim();
    }
    const wrap = el.closest(".fb-form-element, .jobs-easy-apply-form-element, .artdeco-text-input--container");
    if (wrap) {
      const lbl = wrap.querySelector("label, legend, .fb-form-element__label");
      if (lbl) return lbl.textContent.trim();
    }
    return el.placeholder || el.name || el.id || "";
  }

  // ── normalizar texto (igual a Python) ───────────────────────────────────────

  function normalizar(texto) {
    return texto
      .toUpperCase()
      .replace(/[?¿]/g, "")
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "");
  }

  // ── respuesta estática (portado de NORMALIZADOR.py) ─────────────────────────

  function responderEstatico(pregunta, profile) {
    const p = normalizar(pregunta);
    const exp  = String(profile.experiencia || "3").replace(/\s*años?/i, "").trim();
    const sueldo = String(profile.pretension_general || "1500000").replace(/[^\d]/g, "") || "1500000";
    const cel    = profile.celular || "";
    const ciudad = "Santiago";
    const carrera = profile.carrera || profile.profesion || "";
    const resumen = profile.resumen || "";
    const nombre  = (profile.nombre || "").split(" ")[0] || "";
    const apellido = (profile.nombre || "").split(" ").slice(1).join(" ") || "";

    if (p.includes("ANO") || p.includes("AÑO") || p.includes("EXPERIENCIA") ||
        p.includes("HOW MANY") || p.includes("YEARS"))
      return { value: p.includes("LIDERAZGO") ? String(Math.max(0, parseInt(exp) - 1)) : exp, matched: true };

    if (p.includes("PRETENSION") || p.includes("SUELDO") || p.includes("RENTA") ||
        p.includes("SALARIO") || p.includes("BRUTO") || p.includes("LIQUID") ||
        p.includes("EXPECTATIVA") || p.includes("SALARIAL") || p.includes("REMUNERACION"))
      return { value: sueldo, matched: true };

    if (p.includes("TELEFONO") || p.includes("CELULAR") || p.includes("PHONE") ||
        p.includes("MOVIL") || p.includes("NUMERO") || p.includes("INDICA NUMER"))
      return { value: cel, matched: true };

    if (p.includes("CITY") || p.includes("CIUDAD") || p.includes("UBICACI"))
      return { value: ciudad, matched: true };

    if (p.includes("LICENCIA") || p.includes("CONDUCIR"))
      return { value: "B", matched: true };

    if (p.includes("DISPONIBILIDAD") || p.includes("CUANTAS SEMANAS"))
      return { value: "2 semanas", matched: true };

    if (p.includes("FORMACION") || p.includes("TITULO") || p.includes("CARRERA") ||
        p.includes("ESTUDIOS"))
      return { value: carrera, matched: true };

    if (p.includes("CARTA") || p.includes("COVER") || p.includes("PRESENTACION") ||
        p.includes("RESUMEN") || p.includes("ACERCA"))
      return { value: resumen, matched: true };

    if (p.includes("DISCAPACIDAD") || p.includes("REQUIERES AJUSTE"))
      return { value: "No", matched: true };

    if (p.includes("NIVEL") || p.includes("NIVEL DE"))
      return { value: "Avanzado", matched: true };

    if (p.includes("FIRST NAME") || p.includes("PRIMER NOMBRE") || p.includes("NOMBRE"))
      return { value: nombre, matched: true };

    if (p.includes("LAST NAME") || p.includes("APELLIDO"))
      return { value: apellido, matched: true };

    if (p.includes("PAIS") || p.includes("COUNTRY") || p.includes("PAÍS"))
      return { value: "Chile", matched: true };

    return { value: "Si", matched: false };
  }

  // ── Claude para preguntas desconocidas ───────────────────────────────────────

  async function responderConClaude(pregunta, profile) {
    return new Promise(resolve => {
      try {
        chrome.runtime.sendMessage({ type: "ASK_AI", pregunta, perfil: profile }, res => {
          if (chrome.runtime.lastError || !res?.respuesta) resolve(null);
          else resolve(res.respuesta);
        });
      } catch { resolve(null); }
    });
  }

  async function responder(pregunta, profile) {
    if (!pregunta) return null;
    const { value, matched } = responderEstatico(pregunta, profile);
    if (matched) return value;
    const ai = await responderConClaude(pregunta, profile);
    return ai || value;
  }

  // ── typing humano ────────────────────────────────────────────────────────────

  async function humanType(el, text) {
    el.focus();
    el.value = "";
    el.dispatchEvent(new Event("input", { bubbles: true }));
    for (const ch of String(text)) {
      el.value += ch;
      el.dispatchEvent(new Event("input",  { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(rand(30, 80));
    }
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  }

  // ── combobox / typeahead ─────────────────────────────────────────────────────

  async function fillCombobox(input, valor) {
    if (!valor) return;
    input.focus();
    input.click();
    input.value = "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await sleep(200);
    for (const ch of String(valor)) {
      input.value += ch;
      input.dispatchEvent(new Event("input",  { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(rand(40, 100));
    }
    for (let i = 0; i < 8; i++) {
      await sleep(400);
      const listbox = document.querySelector('[role="listbox"], ul[class*="typeahead"], div[class*="autocomplete"]');
      if (!listbox) continue;
      const opciones = [...listbox.querySelectorAll('[role="option"], li')].filter(el => el.getBoundingClientRect().width);
      if (!opciones.length) continue;
      const match = opciones.find(o => o.textContent.trim().toLowerCase().includes(valor.toLowerCase())) || opciones[0];
      match.click();
      await sleep(300);
      return;
    }
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  }

  // ── seleccionar CV existente ─────────────────────────────────────────────────

  async function selectExistingResume(modal) {
    const cards = qAll(
      ".jobs-document-upload-redesign-card__container, [data-test-document-upload-list-item], " +
      "input[type='radio'][name*='resume'], .document-upload-list-item",
      modal
    );
    if (!cards.length) return;
    const target = cards[cards.length - 1];
    if (target.tagName === "INPUT") target.click();
    else { const r = target.querySelector("input[type='radio']"); if (r) r.click(); else target.click(); }
    await sleep(400);
  }

  // ── llenado de secciones (igual a Python: detecta por outerHTML) ─────────────
  // Python itera div[*] del formulario y chequea el outerHTML para saber el tipo

  async function fillSection(section, profile) {
    const html = section.outerHTML || "";

    // Fieldset → radio buttons: click primer label (Python: xp2 label click)
    if (html.includes("<fieldset")) {
      const labels = qAll("input[type='radio']", section);
      if (labels.length && !labels.some(r => r.checked)) {
        labels[0].click();
        await sleep(300);
      }
      return;
    }

    // Select (Python: select_by_value "Yes" → "Professional")
    if (html.includes("<select")) {
      const select = section.querySelector("select");
      if (!select || !select.getBoundingClientRect().width) return;
      const label = labelText(select);
      const p = normalizar(label);
      let chosen = null;

      if (p.includes("EXPERIENCIA") || p.includes("ANO") || p.includes("AÑO")) {
        const yrs = parseInt(profile.experiencia || "3");
        chosen = [...select.options].find(o => {
          const nums = (o.text.match(/\d+/g) || []).map(Number);
          return nums.length === 1 ? nums[0] === yrs : nums.length === 2 ? yrs >= nums[0] && yrs <= nums[1] : false;
        });
      }
      if (!chosen && (p.includes("NIVEL") || p.includes("EDUCATION") || p.includes("TITULO")))
        chosen = [...select.options].find(o => /universit|bachelor|licenci/i.test(o.text));
      if (!chosen && (p.includes("COUNTRY") || p.includes("PAIS")))
        chosen = [...select.options].find(o => /chile/i.test(o.text));
      if (!chosen && (p.includes("RELOCAT") || p.includes("DISPONIB")))
        chosen = [...select.options].find(o => /yes|si|sí|open|dispon/i.test(o.text));
      // Python fallback: select_by_value "Yes"
      if (!chosen) chosen = [...select.options].find(o => /^(yes|si|sí)$/i.test(o.text.trim()));
      // Python fallback 2: "Professional"
      if (!chosen) chosen = [...select.options].find(o => /professional/i.test(o.text));
      if (!chosen && select.options.length > 1) chosen = select.options[1]; // primera opción no-vacía

      if (chosen) {
        select.value = chosen.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        await sleep(300);
      }
      return;
    }

    // Combobox typeahead
    const combobox = section.querySelector("input[role='combobox']");
    if (combobox && combobox.getBoundingClientRect().width && !combobox.value.trim()) {
      const label = labelText(combobox);
      if (label) {
        const val = await responder(label, profile);
        if (val) await fillCombobox(combobox, val);
      }
      return;
    }

    // Input / Textarea (Python: send_keys respuesta)
    if (html.includes("<input") || html.includes("<textarea")) {
      for (const el of qAll("input:not([type='hidden']):not([type='file']):not([type='radio']):not([type='checkbox']), textarea", section)) {
        if (!el.getBoundingClientRect().width) continue;
        if (el.getAttribute("role") === "combobox") continue;
        if (el.value && el.value.trim()) continue;
        const label = labelText(el);
        if (!label) continue;
        let val = await responder(label, profile);
        if (!val) continue;
        if (el.type === "number") val = val.replace(/[^\d]/g, "") || val;
        await humanType(el, val);
      }
    }
  }

  // ── llenar todos los campos del paso actual ──────────────────────────────────

  async function fillCurrentStep(modal, profile) {
    await selectExistingResume(modal);

    // Obtener secciones del formulario (Python: /form/div/div/div[*])
    const form = modal.querySelector("form");
    if (form) {
      const sections = qAll(":scope > div > div > div", form);
      if (sections.length) {
        for (const section of sections) {
          await fillSection(section, profile);
        }
        return;
      }
    }

    // Fallback: llenar campo por campo directamente
    for (const combobox of qAll("input[role='combobox']", modal)) {
      if (!combobox.getBoundingClientRect().width || combobox.value.trim()) continue;
      const label = labelText(combobox);
      if (label) { const val = await responder(label, profile); if (val) await fillCombobox(combobox, val); }
    }
    for (const input of qAll("input:not([type='hidden']):not([type='file']):not([type='radio']):not([type='checkbox']), textarea", modal)) {
      if (!input.getBoundingClientRect().width) continue;
      if (input.getAttribute("role") === "combobox") continue;
      if (input.value && input.value.trim()) continue;
      const label = labelText(input);
      if (!label) continue;
      let val = await responder(label, profile);
      if (!val) continue;
      if (input.type === "number") val = val.replace(/[^\d]/g, "") || val;
      await humanType(input, val);
    }
    for (const select of qAll("select", modal)) {
      if (!select.getBoundingClientRect().width) continue;
      await fillSection(select.closest("div") || select.parentElement, profile);
    }
    for (const fieldset of qAll("fieldset", modal)) {
      await fillSection(fieldset.closest("div") || fieldset.parentElement, profile);
    }
  }

  // ── buscar botón habilitado por aria-label ───────────────────────────────────

  function findBtn(...ariaLabels) {
    for (const label of ariaLabels.flat()) {
      const btn = document.querySelector(`button[aria-label='${label}']`);
      if (btn && !btn.disabled && btn.getAttribute("aria-disabled") !== "true") return btn;
    }
    return null;
  }

  // ── buscar botón habilitado por texto visible ────────────────────────────────

  function findBtnByText(...texts) {
    for (const btn of document.querySelectorAll("button")) {
      if (btn.disabled || btn.getAttribute("aria-disabled") === "true") continue;
      if (!btn.getBoundingClientRect().width) continue;
      const txt = btn.textContent.trim().toLowerCase();
      if (texts.flat().some(t => txt === t || txt.startsWith(t))) return btn;
    }
    return null;
  }

  // ── esperar modal ────────────────────────────────────────────────────────────

  async function waitForModal() {
    for (let i = 0; i < 10; i++) {
      const m = document.querySelector("div[role='dialog']");
      if (m) return m;
      await sleep(500);
    }
    return null;
  }

  // ── API pública: fill() ──────────────────────────────────────────────────────
  // Replica el while (vla=='Siguiente')|(vla=='Revisar') del script Python

  async function fill(profile) {
    const modal = await waitForModal();
    if (!modal) { console.warn("[PostulAI] Modal no encontrado"); return false; }

    const SUBMIT_LABELS = ["Enviar solicitud", "Submit application", "Submit", "Enviar"];
    const NEXT_LABELS   = ["Ir al siguiente paso", "Continue to next step", "Continuar al siguiente paso"];

    const MAX_STEPS = 15;

    for (let step = 0; step < MAX_STEPS; step++) {
      await sleep(800);

      // ¿Botón de enviar? → fin del formulario (Python: click "Enviar" tras el while)
      const submitBtn = findBtn(SUBMIT_LABELS);
      if (submitBtn) {
        console.log("[PostulAI] Pantalla de envío — enviando...");
        if (profile._autopilot) { submitBtn.click(); await sleep(2000); return true; }
        return "review";
      }

      // Llenar campos del paso actual (Python: for j, x in enumerate(n))
      await fillCurrentStep(modal, profile);
      await sleep(600);

      // Intentar avanzar: "Ir al siguiente paso" primero (Python: aria-label)
      const nextBtn = findBtn(NEXT_LABELS);
      if (nextBtn) {
        console.log(`[PostulAI] Paso ${step + 1} — avanzando...`);
        nextBtn.click();
        await sleep(1500);
        continue;
      }

      // Fallback: botón "Revisar" (Python: //button[contains(., 'Revisar')])
      const revisarBtn = findBtnByText("revisar", "review");
      if (revisarBtn) {
        console.log(`[PostulAI] Paso ${step + 1} — revisando...`);
        revisarBtn.click();
        await sleep(1500);
        continue;
      }

      console.warn(`[PostulAI] Paso ${step + 1}: sin botón de avance`);
      return false;
    }

    return false;
  }

  return { fill };

})();
