/**
 * form_filler.js — Lógica de llenado del wizard Easy Apply de LinkedIn.
 *
 * Respuestas:
 *   1. Estático para campos comunes (nombre, teléfono, salario, años exp.) — rápido, sin API
 *   2. Claude vía background.js para preguntas desconocidas del empleador
 */

const LIFormFiller = (() => {

  // ── utilidades DOM ──────────────────────────────────────────────────────────

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  function rand(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }

  async function humanType(el, text) {
    el.focus();
    el.value = "";
    el.dispatchEvent(new Event("input", { bubbles: true }));
    for (const ch of String(text)) {
      el.value += ch;
      el.dispatchEvent(new Event("input",  { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(rand(40, 120));
    }
  }

  function q(selector, root = document)    { return root.querySelector(selector); }
  function qAll(selector, root = document) { return [...root.querySelectorAll(selector)]; }

  function labelText(el) {
    const id = el.id;
    if (id) {
      const lbl = document.querySelector(`label[for="${id}"]`);
      if (lbl) return lbl.textContent.trim();
    }
    const wrap = el.closest(".artdeco-text-input--container, .fb-form-element, .jobs-easy-apply-form-element");
    if (wrap) {
      const lbl = wrap.querySelector("label, legend, .fb-form-element__label");
      if (lbl) return lbl.textContent.trim();
    }
    return el.placeholder || el.name || el.id || "";
  }

  // ── NORMALIZADOR estático (portado de NORMALIZADOR.py) ─────────────────────
  // Retorna { value, matched } — matched=false indica que no se reconoció

  function normalizarTexto(texto) {
    return texto
      .toUpperCase()
      .replace(/[?¿]/g, "")
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "");
  }

  function responderEstatico(pregunta, profile) {
    const p = normalizarTexto(pregunta);
    const e = String(profile.experiencia || "3").replace(/\s*años?/i, "").trim();
    const d = String(profile.pretension_general || "");
    const t = profile.carrera || profile.profesion || "";
    const cel = profile.celular || "";
    const cv  = profile.resumen || "";
    const nombre   = (profile.nombre || "").split(" ")[0] || "";
    const apellido = (profile.nombre || "").split(" ").slice(1).join(" ") || "";

    if (p.includes("ANOS DE EXPERIENCIA") || p.includes("AÑO") || p.includes("ANO") ||
        p.includes("HOW MANY") || p.includes("EXPERIENCIA") || p.includes("LIDERAZGO")) {
      const val = p.includes("LIDERAZGO") ? String(Math.max(0, parseInt(e) - 1)) : e;
      return { value: val, matched: true };
    }
    if (p.includes("CITY") || p.includes("CIUDAD") || p.includes("UBICACI"))
      return { value: "Santiago", matched: true };
    if (p.includes("LICENCIA") || p.includes("CONDUCIR"))
      return { value: "B", matched: true };
    if (p.includes("TELEFONO") || p.includes("CELULAR") || p.includes("NUMERO") ||
        p.includes("PHONE") || p.includes("INDICA NUMER"))
      return { value: cel, matched: true };
    if (p.includes("DISPONIBILIDAD") || p.includes("CUANTAS SEMANAS"))
      return { value: "2 semanas", matched: true };
    if (p.includes("FORMACION") || p.includes("TITULO") || p.includes("CARRERA"))
      return { value: t, matched: true };
    if (p.includes("CARTA") || p.includes("COVER LETTER") || p.includes("PRESENTACION"))
      return { value: cv, matched: true };
    if (p.includes("PRETENSION") || p.includes("SUELDO") || p.includes("RENTA") ||
        p.includes("SALARIO") || p.includes("BRUTO") || p.includes("LIQUID") ||
        p.includes("EXPECTATIVA") || p.includes("SALARIAL"))
      return { value: d, matched: true };
    if (p.includes("DISCAPACIDAD") || p.includes("REQUIERES AJUSTE"))
      return { value: "No", matched: true };
    if (p.includes("NIVEL"))   return { value: "Avanzado", matched: true };
    if (p.includes("FIRST NAME") || p.includes("PRIMER NOMBRE")) return { value: nombre, matched: true };
    if (p.includes("LAST NAME") || p.includes("APELLIDO"))        return { value: apellido, matched: true };

    return { value: "Sí", matched: false };
  }

  // ── Claude vía background (para preguntas no reconocidas) ──────────────────

  async function responderConClaude(pregunta, profile) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(
          { type: "ASK_AI", pregunta, perfil: profile },
          (res) => {
            if (chrome.runtime.lastError || !res?.respuesta) {
              resolve(null);
            } else {
              resolve(res.respuesta);
            }
          }
        );
      } catch (_) {
        resolve(null);
      }
    });
  }

  async function responder(pregunta, profile) {
    if (!pregunta) return null;
    const { value, matched } = responderEstatico(pregunta, profile);
    if (matched) return value;
    // Pregunta no reconocida → Claude
    const aiRespuesta = await responderConClaude(pregunta, profile);
    return aiRespuesta || value; // fallback al "Sí" si Claude falla
  }

  // ── combobox / typeahead (LinkedIn usa estos para País, Ciudad, etc.) ─────────
  // Estructura típica:
  //   <input role="combobox" aria-autocomplete="list" ...>
  //   <div role="listbox"> <div role="option">...</div> </div>
  //   o bien <ul role="listbox"> <li role="option">...</li> </ul>

  async function fillCombobox(input, valor) {
    if (!valor) return;
    input.focus();
    input.click();
    // Limpiar y escribir
    input.value = "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await sleep(300);
    for (const ch of String(valor)) {
      input.value += ch;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(rand(50, 130));
    }
    // Esperar a que aparezca el dropdown (listbox)
    for (let i = 0; i < 8; i++) {
      await sleep(400);
      const listbox =
        document.querySelector('[role="listbox"]') ||
        document.querySelector('ul[class*="typeahead"]') ||
        document.querySelector('div[class*="autocomplete-results"]');
      if (!listbox) continue;

      // Seleccionar la primera opción visible que coincida con el valor
      const opciones = [...listbox.querySelectorAll('[role="option"], li, div[data-value]')]
        .filter(el => el.offsetParent);

      if (!opciones.length) continue;

      // Buscar opción que contenga el valor (case-insensitive)
      const match = opciones.find(o =>
        o.textContent.trim().toLowerCase().includes(valor.toLowerCase())
      ) || opciones[0]; // si no hay match exacto, tomar la primera

      match.click();
      await sleep(400);
      return;
    }
    // Si no apareció listbox: simular Enter como fallback
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  }

  async function fillComboboxes(modal, profile) {
    for (const input of qAll('input[role="combobox"]', modal)) {
      if (!input.getBoundingClientRect().width) continue;
      if (input.value && input.value.trim()) continue; // ya tiene valor

      const label = labelText(input);
      if (!label) continue;

      const val = await responder(label, profile);
      if (val) await fillCombobox(input, val);
    }
  }

  // ── selección del resume ya cargado ─────────────────────────────────────────

  async function selectExistingResume(modal) {
    const resumeCards = qAll(
      ".jobs-document-upload-redesign-card__container, " +
      "[data-test-document-upload-list-item], " +
      "input[type='radio'][name*='resume'], " +
      ".document-upload-list-item",
      modal
    );
    if (resumeCards.length > 0) {
      const target = resumeCards[resumeCards.length - 1];
      if (target.tagName === "INPUT") target.click();
      else {
        const radio = target.querySelector("input[type='radio']");
        if (radio) radio.click();
        else target.click();
      }
      await sleep(500);
      return true;
    }
    return false;
  }

  // ── llenado de texto / textarea ──────────────────────────────────────────────

  async function fillInputs(modal, profile) {
    for (const input of qAll("input, textarea", modal)) {
      if (!input.getBoundingClientRect().width) continue;
      const type = input.type?.toLowerCase();
      if (type === "file" || type === "radio" || type === "checkbox" || type === "hidden") continue;
      // Los combobox se manejan en fillComboboxes()
      if (input.getAttribute("role") === "combobox") continue;
      if (input.value && input.value.trim()) continue;

      const label = labelText(input);
      if (!label) continue;

      const val = await responder(label, profile);
      if (val) await humanType(input, val);
    }
  }

  // ── selects ──────────────────────────────────────────────────────────────────

  async function fillSelects(modal, profile) {
    for (const select of qAll("select", modal)) {
      if (!select.getBoundingClientRect().width) continue;
      const label = labelText(select);
      await fillSelect(select, label, profile);
    }
  }

  async function fillSelect(select, label, profile) {
    const p = normalizarTexto(label);
    let chosen = null;

    if (p.includes("EXPERIENCIA") || p.includes("ANO") || p.includes("AÑO")) {
      const yrs = parseInt(profile.experiencia || "3");
      chosen = [...select.options].find(o => {
        const nums = (o.text.match(/\d+/g) || []).map(Number);
        return nums.length === 1 ? nums[0] === yrs
          : nums.length === 2 ? yrs >= nums[0] && yrs <= nums[1]
          : false;
      });
    }
    if (!chosen && (p.includes("NIVEL") || p.includes("EDUCATION") || p.includes("TITULO"))) {
      chosen = [...select.options].find(o => /universit|bachelor|licenci/i.test(o.text));
    }
    if (!chosen && (p.includes("RELOCAT") || p.includes("REUBIC") || p.includes("DISPONIB"))) {
      chosen = [...select.options].find(o => /yes|si|sí|open|dispon/i.test(o.text));
    }
    if (!chosen && (p.includes("COUNTRY") || p.includes("PAIS") || p.includes("PAÍS"))) {
      chosen = [...select.options].find(o => /chile/i.test(o.text));
    }
    // Para selects de sí/no simples: elegir "Yes/Sí"
    if (!chosen) {
      const opts = [...select.options];
      if (opts.length <= 3) {
        chosen = opts.find(o => /^(yes|si|sí|y)$/i.test(o.text.trim())) ||
                 opts.find(o => /yes|si|sí/i.test(o.text));
      }
    }

    if (chosen) {
      select.value = chosen.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(300);
    }
  }

  // ── radio buttons ────────────────────────────────────────────────────────────

  async function fillRadios(modal, profile) {
    for (const fs of qAll("fieldset", modal)) {
      const legend = (fs.querySelector("legend, span[class*='label']")?.textContent || "").trim();
      const radios = qAll("input[type='radio']", fs);
      if (!radios.length) continue;

      const res = await responder(legend, profile);
      let targetPattern = null;
      if (!res || res === "Sí" || res.toLowerCase() === "si" || res.toLowerCase() === "yes") {
        targetPattern = /yes|si|sí/i;
      } else if (res.toLowerCase() === "no") {
        targetPattern = /^no$/i;
      } else {
        // Respuesta específica → buscar en las opciones
        const target = radios.find(r => {
          const lbl = r.closest("label") || document.querySelector(`label[for="${r.id}"]`);
          return lbl?.textContent.trim().toLowerCase() === res.toLowerCase() ||
                 r.value.toLowerCase() === res.toLowerCase();
        });
        if (target && !target.checked) { target.click(); await sleep(300); }
        continue;
      }

      const target = radios.find(r =>
        targetPattern.test(r.value) ||
        targetPattern.test((r.closest("label") || document.querySelector(`label[for="${r.id}"]`))?.textContent.trim() || "")
      );
      if (target && !target.checked) { target.click(); await sleep(300); }
    }
  }

  // ── botón de avance ───────────────────────────────────────────────────────────

  // Espera hasta que el botón exista y esté habilitado (igual a WebDriverWait de Selenium)
  async function waitForClickable(selector, timeoutMs = 6000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const btn = document.querySelector(selector);
      if (btn && !btn.disabled && btn.getAttribute("aria-disabled") !== "true") return btn;
      await sleep(300);
    }
    return null;
  }

  async function clickNext() {
    const NEXT_LABELS = [
      'Ir al siguiente paso', 'Continue to next step', 'Continuar al siguiente paso',
      'Review your application', 'Revisar tu solicitud', 'Revisar',
    ];
    const SKIP_TEXTS = ["back","atrás","atras","cancel","cancelar","descartar"];

    // Chequea todos los selectores en paralelo cada 300ms (no 6s por cada uno)
    const start = Date.now();
    while (Date.now() - start < 6000) {
      for (const label of NEXT_LABELS) {
        const btn = document.querySelector(`button[aria-label='${label}']`);
        if (btn && !btn.disabled && btn.getAttribute("aria-disabled") !== "true") {
          btn.click(); await sleep(1500); return "next";
        }
      }
      // Fallback: cualquier botón visible con texto de avance
      for (const btn of qAll("button")) {
        if (btn.disabled || btn.getAttribute("aria-disabled") === "true") continue;
        if (!btn.getBoundingClientRect().width) continue;
        const txt = btn.textContent.trim().toLowerCase();
        if (SKIP_TEXTS.includes(txt)) continue;
        if (txt === "revisar" || txt === "review" || txt === "siguiente" ||
            txt === "next" || txt === "continuar" ||
            txt.startsWith("revisar") || txt.startsWith("siguiente") || txt.startsWith("continuar")) {
          btn.click(); await sleep(1500); return "next";
        }
      }
      await sleep(300);
    }
    return null;
  }

  async function clickSubmit() {
    const SUBMIT_SELECTORS = [
      "button[aria-label='Enviar solicitud']",
      "button[aria-label='Submit application']",
      "button[aria-label='Submit']",
      "button[aria-label='Enviar']",
    ];
    for (const sel of SUBMIT_SELECTORS) {
      const btn = await waitForClickable(sel, 4000);
      if (btn) { btn.click(); await sleep(2000); return true; }
    }
    // Fallback texto
    for (const btn of qAll("button.artdeco-button--primary")) {
      if (btn.disabled || btn.getAttribute("aria-disabled") === "true") continue;
      const txt = btn.textContent.trim().toLowerCase();
      if (txt.includes("enviar") || txt.includes("submit")) {
        btn.click(); await sleep(2000); return true;
      }
    }
    return false;
  }

  // ── pantalla de revisión / éxito ──────────────────────────────────────────────

  function isReviewScreen(modal) {
    // Solo es pantalla de revisión si hay un botón real de ENVIAR — no confundir
    // con el botón "Revisar" (aria-label='Revisar tu solicitud') que navega al paso anterior
    return !!q(
      "button[aria-label='Submit application'], button[aria-label='Enviar solicitud'], " +
      "button[aria-label='Submit'], button[aria-label='Enviar']",
      modal
    );
  }

  function isSuccessScreen() {
    const html = document.body.innerHTML.toLowerCase();
    return (
      html.includes("application submitted") ||
      html.includes("solicitud enviada") ||
      html.includes("your application was sent") ||
      html.includes("applied")
    );
  }

  // ── API pública ───────────────────────────────────────────────────────────────

  async function fill(profile) {
    // Esperar a que aparezca el modal (cualquier dialog de LinkedIn)
    let modal = null;
    for (let i = 0; i < 10; i++) {
      modal = document.querySelector(
        ".jobs-easy-apply-modal, [data-test-modal-id='easy-apply-modal'], " +
        "div[role='dialog'][aria-label*='apply'], div[role='dialog'][aria-label*='Apply'], " +
        "div[role='dialog'][aria-label*='olicitud'], div[role='dialog'][aria-label*='olicitar'], " +
        "div[role='dialog'][aria-label*='Aplicar']"
      ) || document.querySelector("div[role='dialog']");
      if (modal) break;
      await sleep(500);
    }
    if (!modal) { console.warn("[PostulAI] Modal no encontrado"); return false; }

    const MAX_STEPS = 12;

    for (let step = 0; step < MAX_STEPS; step++) {
      await sleep(800);
      if (isSuccessScreen()) return true;

      // Llenar todos los tipos de campo
      await selectExistingResume(modal);
      await fillComboboxes(modal, profile);
      await fillInputs(modal, profile);
      await fillSelects(modal, profile);
      await fillRadios(modal, profile);

      await sleep(500); // Dar tiempo a validaciones de LinkedIn

      // ¿Pantalla de revisión? → enviar
      if (isReviewScreen(modal)) {
        if (profile._autopilot) return await clickSubmit();
        return "review";
      }

      // ¿Botón de enviar directo?
      const submitBtn = document.querySelector(
        "button[aria-label='Enviar solicitud'], button[aria-label='Submit application'], button[aria-label='Enviar']"
      );
      if (submitBtn && !submitBtn.disabled && submitBtn.getAttribute("aria-disabled") !== "true") {
        if (profile._autopilot) { submitBtn.click(); await sleep(2000); return true; }
        return "review";
      }

      // Avanzar al siguiente paso
      const result = await clickNext();
      if (!result) {
        console.warn("[PostulAI] clickNext() no encontró botón habilitado en paso", step + 1);
        break;
      }
    }

    return false;
  }

  return { fill };

})();
