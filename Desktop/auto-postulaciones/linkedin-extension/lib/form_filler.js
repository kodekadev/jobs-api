/**
 * form_filler.js — Lógica de llenado del wizard Easy Apply de LinkedIn.
 *
 * Se inyecta antes que content.js.
 * Expone el objeto global `LIFormFiller` con un método fill(profile).
 */

const LIFormFiller = (() => {

  // ── utilidades DOM ──────────────────────────────────────────────────────────

  function sleep(ms) {
    return new Promise(r => setTimeout(r, ms));
  }

  function rand(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
  }

  async function humanType(el, text) {
    el.focus();
    el.value = "";
    el.dispatchEvent(new Event("input", { bubbles: true }));
    for (const ch of text) {
      el.value += ch;
      el.dispatchEvent(new Event("input",  { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(rand(40, 120));
    }
  }

  function q(selector, root = document) {
    return root.querySelector(selector);
  }

  function qAll(selector, root = document) {
    return [...root.querySelectorAll(selector)];
  }

  function labelText(el) {
    const id = el.id;
    if (id) {
      const lbl = document.querySelector(`label[for="${id}"]`);
      if (lbl) return lbl.textContent.trim().toLowerCase();
    }
    const wrap = el.closest(".artdeco-text-input--container, .fb-form-element, .jobs-easy-apply-form-element");
    if (wrap) {
      const lbl = wrap.querySelector("label, legend, .fb-form-element__label");
      if (lbl) return lbl.textContent.trim().toLowerCase();
    }
    return (el.placeholder || el.name || el.id || "").toLowerCase();
  }

  // ── selección del resume ya cargado ─────────────────────────────────────────

  async function selectExistingResume(modal) {
    // LinkedIn muestra el último CV cargado como un radio/card — lo seleccionamos
    const resumeCards = qAll(
      ".jobs-document-upload-redesign-card__container, " +
      "[data-test-document-upload-list-item], " +
      "input[type='radio'][name*='resume'], " +
      ".document-upload-list-item",
      modal
    );
    if (resumeCards.length > 0) {
      const target = resumeCards[resumeCards.length - 1]; // el más reciente
      if (target.tagName === "INPUT") {
        target.click();
      } else {
        const radio = target.querySelector("input[type='radio']");
        if (radio) radio.click();
        else target.click();
      }
      await sleep(500);
      return true;
    }
    return false;
  }

  // ── llenado de campos de texto / select / radio ──────────────────────────────

  async function fillInputs(modal, profile) {
    const {
      nombre = "",
      email = "",
      celular = "",
      experiencia = "3",
      pretension_general = "",
      ciudad = "Santiago",
    } = profile;

    const firstName = nombre.split(" ")[0] || nombre;
    const lastName  = nombre.split(" ").slice(1).join(" ") || "";

    for (const input of qAll("input, textarea, select", modal)) {
      if (!input.offsetParent) continue; // oculto
      const label = labelText(input);
      const type  = input.type?.toLowerCase();

      if (type === "file") continue; // manejado aparte

      // ── texto libre ──────────────────────────────────────────────────────────
      if (type === "text" || type === "search" || input.tagName === "TEXTAREA") {
        if      (/first.?name|nombre/i.test(label) && firstName) await humanType(input, firstName);
        else if (/last.?name|apellido/i.test(label) && lastName)  await humanType(input, lastName);
        else if (/city|ciudad|ubicaci/i.test(label) && ciudad)    await humanType(input, ciudad);
        else if (/years?.of.exp|a.{1,3}os.de.exp|experience/i.test(label)) {
          await humanType(input, experiencia.toString());
        } else if (/salary|salario|remuner|pretensi/i.test(label) && pretension_general) {
          await humanType(input, pretension_general.toString());
        }
      }

      // ── email ────────────────────────────────────────────────────────────────
      if (type === "email" && email && !input.value) {
        await humanType(input, email);
      }

      // ── teléfono ─────────────────────────────────────────────────────────────
      if (type === "tel" && celular && !input.value) {
        await humanType(input, celular);
      }

      // ── select / dropdown ────────────────────────────────────────────────────
      if (input.tagName === "SELECT") {
        await fillSelect(input, label, profile);
      }
    }
  }

  async function fillSelect(select, label, profile) {
    const opts = [...select.options].map(o => o.text.toLowerCase());

    let chosen = null;

    if (/experience|experiencia/i.test(label)) {
      const yrs = parseInt(profile.experiencia || "3");
      // Buscar la opción que mejor coincide con los años
      chosen = [...select.options].find(o => {
        const t = o.text;
        const nums = t.match(/\d+/g)?.map(Number) || [];
        return nums.length === 1 ? nums[0] === yrs
          : nums.length === 2 ? yrs >= nums[0] && yrs <= nums[1]
          : false;
      });
    }

    if (/nivel.edu|education|degree|titulo/i.test(label)) {
      chosen = [...select.options].find(o =>
        /universit|bachelor|licenci/i.test(o.text)
      );
    }

    if (/relocat|reubic|disponib/i.test(label)) {
      chosen = [...select.options].find(o =>
        /yes|si|sí|open|dispon/i.test(o.text)
      );
    }

    if (/country|pa.?s/i.test(label)) {
      chosen = [...select.options].find(o =>
        /chile/i.test(o.text)
      );
    }

    if (chosen) {
      select.value = chosen.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(300);
    }
  }

  // ── preguntas YES/NO (radio buttons) ─────────────────────────────────────────

  async function fillRadios(modal, profile) {
    const fieldsets = qAll("fieldset", modal);
    for (const fs of fieldsets) {
      const legend = (fs.querySelector("legend, span[class*='label']")?.textContent || "").toLowerCase();
      const radios = qAll("input[type='radio']", fs);
      if (!radios.length) continue;

      let targetValue = null;

      if (/authorized|autorizado|work.?permit|permiso/i.test(legend)) {
        targetValue = "yes";
      } else if (/relocat|reubic/i.test(legend)) {
        targetValue = "yes";
      } else if (/sponsor/i.test(legend)) {
        targetValue = "no";
      } else if (/currently.?employ|actualmente.?trabaj/i.test(legend)) {
        targetValue = profile.actualmente_trabajando ? "yes" : "no";
      }

      if (!targetValue) continue;

      const target = radios.find(r =>
        r.value.toLowerCase().startsWith(targetValue) ||
        (r.closest("label") || document.querySelector(`label[for="${r.id}"]`))
          ?.textContent.trim().toLowerCase().startsWith(targetValue)
      );
      if (target && !target.checked) {
        target.click();
        await sleep(300);
      }
    }
  }

  // ── botón de avance ───────────────────────────────────────────────────────────

  async function clickNext(modal) {
    const BTN_SELECTORS = [
      "button[aria-label='Continue to next step']",
      "button[aria-label='Continuar al siguiente paso']",
      "button[aria-label='Review your application']",
      "button[aria-label='Revisar tu solicitud']",
    ];
    for (const sel of BTN_SELECTORS) {
      const btn = q(sel, modal);
      if (btn && !btn.disabled) {
        btn.click();
        await sleep(1500);
        return true;
      }
    }
    // Fallback: cualquier botón primario que no sea "Back"
    const btns = qAll("button.artdeco-button--primary", modal);
    for (const btn of btns) {
      const txt = btn.textContent.trim().toLowerCase();
      if (!["back", "atrás", "atras", "cancel", "cancelar"].includes(txt) && !btn.disabled) {
        btn.click();
        await sleep(1500);
        return true;
      }
    }
    return false;
  }

  // ── submit final ──────────────────────────────────────────────────────────────

  async function submit(modal) {
    const SUBMIT_SELECTORS = [
      "button[aria-label='Submit application']",
      "button[aria-label='Enviar solicitud']",
      "button[aria-label='Submit']",
    ];
    for (const sel of SUBMIT_SELECTORS) {
      const btn = q(sel, modal);
      if (btn && !btn.disabled) {
        btn.click();
        await sleep(2000);
        return true;
      }
    }
    return false;
  }

  // ── comprobación de pantalla final ────────────────────────────────────────────

  function isReviewScreen(modal) {
    const html = (modal?.innerHTML || "").toLowerCase();
    return (
      html.includes("review your application") ||
      html.includes("revisar tu solicitud") ||
      html.includes("revisa tu solicitud") ||
      !!q("button[aria-label='Submit application'], button[aria-label='Enviar solicitud']", modal)
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
    const modal = q(
      ".jobs-easy-apply-modal, [data-test-modal-id='easy-apply-modal'], " +
      "div[role='dialog'][aria-label*='apply'], div[role='dialog'][aria-label*='Apply']"
    );
    if (!modal) {
      console.warn("[PostulAI] Modal Easy Apply no encontrado");
      return false;
    }

    const MAX_STEPS = 8;

    for (let step = 0; step < MAX_STEPS; step++) {
      await sleep(800);

      if (isSuccessScreen()) return true;

      // Seleccionar CV existente si hay sección de resume
      await selectExistingResume(modal);

      // Llenar campos
      await fillInputs(modal, profile);
      await fillRadios(modal, profile);

      if (isReviewScreen(modal)) {
        // En la pantalla de revisión no avanzamos — dejamos al usuario confirmar
        // o hacemos submit automático si está en modo autopilot
        if (profile._autopilot) {
          return await submit(modal);
        }
        return "review"; // content.js lo maneja
      }

      const advanced = await clickNext(modal);
      if (!advanced) break;
    }

    return false;
  }

  return { fill };

})();
