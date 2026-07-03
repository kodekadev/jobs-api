/**
 * form_filler.js — Llenado del wizard Easy Apply de LinkedIn.
 * Lógica portada de JOBS_LKD.py (Selenium):
 *  - mismas respuestas estáticas (NORMALIZADOR.preg)
 *  - mismo orden de estrategias en selects: Yes → Professional → primera opción
 *  - mismo loop de pasos: Siguiente → Revisar → Enviar solicitud
 */

const LIFormFiller = (() => {

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rand  = (a, b) => Math.floor(Math.random() * (b - a + 1)) + a;

  // LinkedIn SDUI (2026) renderiza el wizard Easy Apply dentro de shadow DOM:
  // todos los queries deben atravesar shadow roots recursivamente
  const qAll = (sel, root = document) => {
    const r = root || document;
    const out = [...r.querySelectorAll(sel)];
    const walk = (node) => {
      for (const el of node.querySelectorAll("*")) {
        if (el.shadowRoot) {
          out.push(...el.shadowRoot.querySelectorAll(sel));
          walk(el.shadowRoot);
        }
      }
    };
    walk(r);
    return out;
  };
  const q = (sel, root) => qAll(sel, root)[0] || null;

  // parentElement que cruza el límite del shadow root hacia su host
  const parentOf = (el) => {
    if (el.parentElement) return el.parentElement;
    const rn = el.getRootNode();
    return rn instanceof ShadowRoot ? rn.host : null;
  };

  function visible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  // ── label del campo ─────────────────────────────────────────────────────────

  function labelText(el) {
    if (el.id) {
      const lbl = el.getRootNode().querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lbl) return lbl.textContent.trim();
    }
    const wrap = el.closest(".fb-form-element, .jobs-easy-apply-form-element, .artdeco-text-input--container, [data-test-form-element]");
    if (wrap) {
      const lbl = wrap.querySelector("label, legend, .fb-form-element__label");
      if (lbl) return lbl.textContent.trim();
    }
    return el.getAttribute("aria-label") || el.placeholder || el.name || el.id || "";
  }

  // ── normalizar texto (igual a Python: upper + sin ?¿ + sin tildes) ──────────

  function normalizar(texto) {
    return String(texto)
      .toUpperCase()
      .replace(/[?¿]/g, "")
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "");
  }

  // ── respuestas estáticas (portado de NORMALIZADOR.py) ───────────────────────

  function responderEstatico(pregunta, profile) {
    const p = normalizar(pregunta);
    const exp     = String(profile.experiencia || "3").replace(/\s*años?/i, "").trim();
    const sueldo  = String(profile.pretension_general || "1500000").replace(/[^\d]/g, "") || "1500000";
    const cel     = profile.celular || "";
    const ciudad  = "Santiago";
    const carrera = profile.carrera || profile.profesion || "";
    const resumen = profile.resumen || "";
    const nombre   = (profile.nombre || "").split(" ")[0] || "";
    const apellido = (profile.nombre || "").split(" ").slice(1).join(" ") || "";

    if (p.includes("ANO") || p.includes("EXPERIENCIA") || p.includes("HOW MANY") || p.includes("YEARS"))
      return { value: p.includes("LIDERAZGO") ? String(Math.max(0, parseInt(exp) - 1)) : exp, matched: true };

    if (p.includes("PRETENSION") || p.includes("SUELDO") || p.includes("RENTA") ||
        p.includes("SALARIO") || p.includes("SALARY") || p.includes("BRUTO") || p.includes("LIQUID") ||
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

    if (p.includes("FORMACION") || p.includes("TITULO") || p.includes("CARRERA") || p.includes("ESTUDIOS"))
      return { value: carrera, matched: true };

    if (p.includes("CARTA") || p.includes("COVER") || p.includes("PRESENTACION") ||
        p.includes("RESUMEN") || p.includes("ACERCA"))
      return { value: resumen, matched: true };

    if (p.includes("DISCAPACIDAD") || p.includes("REQUIERES AJUSTE"))
      return { value: "No", matched: true };

    if (p.includes("NIVEL"))
      return { value: "Avanzado", matched: true };

    if (p.includes("FIRST NAME") || p.includes("PRIMER NOMBRE"))
      return { value: nombre, matched: true };

    if (p.includes("LAST NAME") || p.includes("APELLIDO"))
      return { value: apellido, matched: true };

    if (p.includes("NOMBRE"))
      return { value: nombre, matched: true };

    if (p.includes("PAIS") || p.includes("COUNTRY"))
      return { value: "Chile", matched: true };

    return { value: "Si", matched: false };
  }

  // ── Claude (background) para preguntas desconocidas ─────────────────────────

  function responderConClaude(pregunta, profile) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type: "ASK_AI", pregunta, perfil: profile }, (res) => {
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

  // ── typing humano ───────────────────────────────────────────────────────────

  async function humanType(el, text) {
    el.focus();
    el.value = "";
    el.dispatchEvent(new Event("input", { bubbles: true }));
    for (const ch of String(text)) {
      el.value += ch;
      el.dispatchEvent(new Event("input",  { bubbles: true }));
      await sleep(rand(25, 70));
    }
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur",   { bubbles: true }));
  }

  // ── combobox / typeahead ────────────────────────────────────────────────────

  async function fillCombobox(input, valor) {
    if (!valor) return;
    input.focus();
    input.click();
    input.value = "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await sleep(200);
    for (const ch of String(valor)) {
      input.value += ch;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await sleep(rand(40, 90));
    }
    for (let i = 0; i < 8; i++) {
      await sleep(400);
      const listbox = q('[role="listbox"], ul[class*="typeahead"], div[class*="autocomplete"]');
      if (!listbox) continue;
      const opciones = qAll('[role="option"], li', listbox).filter(visible);
      if (!opciones.length) continue;
      const match = opciones.find((o) => o.textContent.trim().toLowerCase().includes(String(valor).toLowerCase())) || opciones[0];
      match.click();
      await sleep(300);
      return;
    }
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  }

  // ── seleccionar CV ya subido ────────────────────────────────────────────────

  async function selectExistingResume(modal) {
    const cards = qAll(
      ".jobs-document-upload-redesign-card__container, [data-test-document-upload-list-item], " +
      "input[type='radio'][name*='resume'], .document-upload-list-item",
      modal
    ).filter(visible);
    if (!cards.length) return;
    const alreadySelected = cards.some((c) =>
      (c.tagName === "INPUT" && c.checked) || c.querySelector("input[type='radio']:checked")
    );
    if (alreadySelected) return;
    const target = cards[cards.length - 1];
    if (target.tagName === "INPUT") target.click();
    else {
      const r = target.querySelector("input[type='radio']");
      if (r) r.click(); else target.click();
    }
    await sleep(400);
  }

  // ── llenado de una sección ──────────────────────────────────────────────────
  // IMPORTANTE: procesa TODOS los tipos de campo SIN return anticipado.
  // Una misma sección puede tener select + input (ej: código país + teléfono).

  async function fillSection(section, profile) {

    // 1) Fieldsets → radios (Python: click primer label del fieldset)
    for (const fs of qAll("fieldset", section)) {
      const radios = qAll("input[type='radio']", fs);
      if (!radios.length || radios.some((r) => r.checked)) continue;
      const legend = normalizar(fs.querySelector("legend")?.textContent || "");
      // Preferir "Sí"/"Yes" si existe; si no, el primero
      const labels = radios.map((r) => ({
        radio: r,
        text: normalizar(r.getRootNode().querySelector(`label[for="${CSS.escape(r.id)}"]`)?.textContent || r.value || ""),
      }));
      let pick = labels.find((l) => /^(SI|YES)\b/.test(l.text.trim()));
      if (legend.includes("DISCAPACIDAD") || legend.includes("DISABILITY"))
        pick = labels.find((l) => /^NO\b/.test(l.text.trim())) || pick;
      (pick || labels[0]).radio.click();
      await sleep(300);
    }

    // 2) Selects — orden de estrategias fiel a Python: Yes → Professional → primera opción,
    //    con heurísticas previas por label (experiencia, país, nivel)
    for (const select of qAll("select", section)) {
      if (!visible(select)) continue;
      if (select.selectedIndex > 0 && !/select|selecciona/i.test(select.options[select.selectedIndex]?.text || "")) continue;
      const p = normalizar(labelText(select));
      const opts = [...select.options];
      let chosen = null;

      if (p.includes("EXPERIENCIA") || p.includes("ANO") || p.includes("YEARS")) {
        const yrs = parseInt(profile.experiencia || "3") || 3;
        chosen = opts.find((o) => {
          const nums = (o.text.match(/\d+/g) || []).map(Number);
          return nums.length === 1 ? nums[0] === yrs : nums.length === 2 ? yrs >= nums[0] && yrs <= nums[1] : false;
        });
      }
      if (!chosen && (p.includes("NIVEL") || p.includes("EDUCATION") || p.includes("TITULO")))
        chosen = opts.find((o) => /universit|bachelor|licenci/i.test(o.text));
      if (!chosen && (p.includes("COUNTRY") || p.includes("PAIS") || p.includes("PREFIJO") || p.includes("CODE")))
        chosen = opts.find((o) => /chile|\+56/i.test(o.text));
      if (!chosen && (p.includes("RELOCAT") || p.includes("DISPONIB")))
        chosen = opts.find((o) => /yes|si|sí|open|dispon/i.test(o.text));
      // Estrategia Python: Yes → Professional → primera opción real
      if (!chosen) chosen = opts.find((o) => /^(yes|si|sí)$/i.test(o.text.trim()) || o.value === "Yes");
      if (!chosen) chosen = opts.find((o) => /professional/i.test(o.text) || o.value === "Professional");
      if (!chosen && opts.length > 1) chosen = opts[1];

      if (chosen) {
        select.value = chosen.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        await sleep(300);
      }
    }

    // 3) Comboboxes / typeahead
    for (const combobox of qAll("input[role='combobox']", section)) {
      if (!visible(combobox) || combobox.value.trim()) continue;
      const label = labelText(combobox);
      if (!label) continue;
      const val = await responder(label, profile);
      if (val) await fillCombobox(combobox, val);
    }

    // 4) Inputs y textareas
    for (const el of qAll("input:not([type='hidden']):not([type='file']):not([type='radio']):not([type='checkbox']), textarea", section)) {
      if (!visible(el)) continue;
      if (el.getAttribute("role") === "combobox") continue;
      if (el.value && el.value.trim()) continue; // respetar valores prellenados por LinkedIn
      const label = labelText(el);
      if (!label) continue;
      let val = await responder(label, profile);
      if (val == null || val === "") continue;
      val = String(val);
      if (el.type === "number") val = val.replace(/[^\d]/g, "") || "0";
      console.log(`[AplicAI] Campo "${label.slice(0, 60)}" → "${String(val).slice(0, 40)}"`);
      await humanType(el, val);
    }
  }

  // ── llenar todos los campos del paso actual ─────────────────────────────────

  async function fillCurrentStep(modal, profile) {
    await selectExistingResume(modal);

    // Python recorre form/div/div/div[*]; si no hay secciones, usar el modal completo
    const form = modal.tagName === "FORM" ? modal : q("form", modal);
    let sections = form ? qAll(":scope > div > div > div", form).filter(visible) : [];
    if (!sections.length) sections = [form || modal];

    for (const section of sections) {
      try { await fillSection(section, profile); }
      catch (err) { console.warn("[AplicAI] Error llenando sección:", err?.message || err); }
    }
  }

  // ── botones del wizard (scoped al modal) ────────────────────────────────────

  function enabled(btn) {
    return btn && !btn.disabled && btn.getAttribute("aria-disabled") !== "true" && visible(btn);
  }

  function findWizardButton(modal, ariaParts, textParts) {
    for (const btn of qAll("button, a, [role='button']", modal)) {
      if (!enabled(btn)) continue;
      const aria = (btn.getAttribute("aria-label") || "").toLowerCase();
      const txt  = btn.textContent.trim().toLowerCase();
      if (ariaParts.some((a) => aria.includes(a))) return btn;
      if (textParts.some((t) => txt === t || txt.startsWith(t))) return btn;
    }
    return null;
  }

  const findSubmit  = (m) => findWizardButton(m,
    ["enviar solicitud", "submit application"],
    ["enviar solicitud", "submit application"]);
  const findNext    = (m) => findWizardButton(m,
    ["ir al siguiente paso", "continue to next step", "continuar al siguiente paso"],
    ["siguiente", "next"]);
  const findRevisar = (m) => findWizardButton(m,
    ["revisar tu solicitud", "review your application", "revisar"],
    ["revisar", "review"]);

  // El wizard nuevo (SDUI) ya no usa div[role='dialog']: ubicarlo por sus botones
  function wizardButtonAnywhere() {
    for (const b of qAll("button, [role='button'], a")) {
      if (!visible(b)) continue;
      const aria = (b.getAttribute("aria-label") || "").toLowerCase();
      const txt  = (b.textContent || "").trim().toLowerCase();
      // Solo frases explícitas del wizard: el "Siguiente" de la paginación de
      // resultados también es un botón visible y NO debe hacer match aquí
      if (/ir al siguiente paso|enviar solicitud|continue to next step|submit application|revisar tu solicitud|review your application/.test(aria + " " + txt)) return b;
      // Texto corto solo si está dentro de un <form> (la paginación nunca lo está)
      if (/^(siguiente|next|revisar|review|enviar solicitud|submit application)$/.test(txt) && b.closest("form")) return b;
    }
    return null;
  }

  function getModal() {
    const dlg = qAll("div[role='dialog']").find(visible);
    if (dlg) return dlg;
    const btn = wizardButtonAnywhere();
    if (!btn) return null;
    const form = btn.closest("form");
    if (form) return form;
    // Subir (cruzando shadow roots) hasta un contenedor con el botón Y campos
    for (let el = parentOf(btn); el && el !== document.body; el = parentOf(el)) {
      if (q("input, select, textarea, fieldset", el)) return el;
    }
    return null;
  }

  function stepHeading(modal) {
    // El título del modal ("Solicitar empleo en X") es constante en todos los
    // pasos: concatenar TODOS los headings para que el heading cambie por paso
    // y la detección de atasco no aborte falsamente
    return qAll("h3, h2", modal).filter(visible).map((h) => h.textContent.trim()).join(" | ");
  }

  function hasFormErrors(modal) {
    return qAll(".artdeco-inline-feedback--error", modal).some(visible);
  }

  // ── API pública: fill() ─────────────────────────────────────────────────────
  // Replica el while (vla=='Siguiente')|(vla=='Revisar') de JOBS_LKD.py.
  // Retorna: true (enviada) | "review" (lista, falta confirmar) | false (falló)

  async function fill(profile) {
    let modal = getModal();
    for (let i = 0; i < 10 && !modal; i++) { await sleep(500); modal = getModal(); }
    if (!modal) { console.warn("[AplicAI] Modal no encontrado"); return false; }

    const MAX_STEPS = 15;
    let lastHeading = null;
    let stuckCount = 0;

    for (let step = 0; step < MAX_STEPS; step++) {
      await sleep(800);
      modal = getModal();
      if (!modal) { console.warn("[AplicAI] El modal se cerró inesperadamente"); return false; }

      const heading = stepHeading(modal);
      console.log(`[AplicAI] Paso ${step + 1} — "${heading}"`);

      // Detectar bucle: mismo paso 3 veces sin avanzar → algo no se pudo llenar
      if (heading && heading === lastHeading) {
        stuckCount++;
        if (stuckCount >= 3) {
          console.warn(`[AplicAI] Atascado en "${heading}" tras 3 intentos — abortando`);
          return false;
        }
      } else {
        stuckCount = 0;
      }
      lastHeading = heading;

      // 1) Llenar los campos del paso actual (Python: for j, x in enumerate(n))
      await fillCurrentStep(modal, profile);
      await sleep(600);

      // 2) ¿Botón "Enviar solicitud"? → fin del wizard
      const submitBtn = findSubmit(modal);
      if (submitBtn) {
        if (!profile._autopilot) {
          console.log("[AplicAI] Pantalla de envío — modo manual, dejando para revisión");
          return "review";
        }
        console.log("[AplicAI] Pantalla de envío — enviando solicitud...");
        submitBtn.click();
        await sleep(2500);
        // Verificar que no haya errores de validación que impidieron el envío
        const after = getModal();
        if (after && findSubmit(after) && hasFormErrors(after)) {
          console.warn("[AplicAI] El envío fue rechazado por validación");
          return false;
        }
        return true;
      }

      // 3) "Ir al siguiente paso" (Python: aria-label='Ir al siguiente paso')
      const nextBtn = findNext(modal);
      if (nextBtn) {
        console.log(`[AplicAI] Paso ${step + 1} — avanzando...`);
        nextBtn.click();
        await sleep(1500);
        continue;
      }

      // 4) "Revisar" (Python: //button[contains(., 'Revisar')])
      const revisarBtn = findRevisar(modal);
      if (revisarBtn) {
        console.log(`[AplicAI] Paso ${step + 1} — pasando a revisión...`);
        revisarBtn.click();
        await sleep(1500);
        continue;
      }

      console.warn(`[AplicAI] Paso ${step + 1}: sin botón de avance (Siguiente/Revisar/Enviar)`);
      return false;
    }

    console.warn(`[AplicAI] Más de ${MAX_STEPS} pasos — abortando`);
    return false;
  }

  return { fill, findContainer: getModal, deepQ: q, deepQAll: qAll };

})();
