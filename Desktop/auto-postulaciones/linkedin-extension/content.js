/**
 * content.js — Script principal inyectado en linkedin.com/jobs/*
 *
 * Modos de operación:
 *   - MANUAL   : el usuario hace click en Easy Apply y la extensión llena el formulario
 *   - AUTOPILOT: la extensión busca empleos en la página y aplica automáticamente
 *
 * Flujo:
 *   1. Carga el perfil del usuario desde chrome.storage (lo pone background.js)
 *   2. Observa el DOM para detectar el botón Easy Apply
 *   3. Cuando aparece el modal, llama a LIFormFiller.fill(profile)
 *   4. Reporta la postulación al background para guardar en PostulAI
 */

(async () => {

  // ── config ──────────────────────────────────────────────────────────────────

  const cfg = await new Promise(r =>
    chrome.storage.sync.get(["enabled", "autopilot", "userId"], r)
  );

  if (!cfg.enabled || !cfg.userId) return; // extensión desactivada o sin cuenta

  const profile = await new Promise(r =>
    chrome.storage.local.get(["profile"], d => r(d.profile))
  );

  if (!profile) {
    console.warn("[PostulAI] Perfil no cargado. Abre el popup para sincronizar.");
    return;
  }

  profile._autopilot = !!cfg.autopilot;

  // IDs ya aplicados (evitar duplicados). Se cargan desde backend (URLs) + sesión actual (jobIds)
  const _storedIds = await new Promise(r => chrome.storage.local.get(["appliedIds"], d =>
    r(d.appliedIds || [])
  ));
  const appliedThisSession = new Set(_storedIds); // contiene URLs del backend

  // ── utilidades ──────────────────────────────────────────────────────────────

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  function getJobId() {
    const m = location.search.match(/currentJobId=(\d+)/) ||
              location.pathname.match(/\/view\/(\d+)/);
    return m ? m[1] : null;
  }

  function getJobMeta() {
    const title   = document.querySelector(".job-details-jobs-unified-top-card__job-title, .jobs-unified-top-card__job-title")?.textContent.trim() ?? "";
    const company = document.querySelector(".job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name")?.textContent.trim() ?? "";
    return { title, company };
  }

  // ── indicador visual ────────────────────────────────────────────────────────

  function showBadge(text, color = "#0077b5") {
    let badge = document.getElementById("postulai-badge");
    if (!badge) {
      badge = document.createElement("div");
      badge.id = "postulai-badge";
      Object.assign(badge.style, {
        position: "fixed", bottom: "24px", right: "24px",
        background: color, color: "#fff",
        padding: "10px 18px", borderRadius: "8px",
        fontFamily: "sans-serif", fontSize: "14px",
        fontWeight: "600", zIndex: "99999",
        boxShadow: "0 4px 12px rgba(0,0,0,0.25)",
        transition: "opacity .4s",
      });
      document.body.appendChild(badge);
    }
    badge.textContent = text;
    badge.style.opacity = "1";
    clearTimeout(badge._t);
    badge._t = setTimeout(() => { badge.style.opacity = "0"; }, 4000);
  }

  // ── lógica de postulación ────────────────────────────────────────────────────

  function isAlreadyApplied() {
    const jobId   = getJobId();
    const currUrl = location.href;
    // Chequear por jobId (sesión actual) o por URL exacta/parcial (backend)
    if (jobId && appliedThisSession.has(jobId)) return true;
    for (const entry of appliedThisSession) {
      if (entry.includes(jobId) || currUrl.includes(entry.replace(/^https?:\/\/[^/]+/, ''))) {
        return true;
      }
    }
    return false;
  }

  async function applyToCurrentJob() {
    const jobId = getJobId();
    if (!jobId || isAlreadyApplied()) return;

    // Buscar botón Easy Apply
    const applyBtn = document.querySelector(
      "button.jobs-apply-button, " +
      "button[aria-label*='Easy Apply'], button[aria-label*='Aplicar fácilmente'], " +
      ".jobs-apply-button--top-card button"
    );

    if (!applyBtn || applyBtn.disabled) return;

    // Verificar que sea Easy Apply (no redirija a sitio externo)
    const btnText = applyBtn.textContent.toLowerCase();
    const isEasyApply = btnText.includes("easy apply") ||
                        btnText.includes("aplicar fácilmente") ||
                        applyBtn.closest("[data-is-easy-apply='true']");
    if (!isEasyApply) return;

    showBadge("PostulAI: Abriendo Easy Apply...");
    applyBtn.click();
    await sleep(2000);

    // Esperar a que aparezca el modal
    let modal = null;
    for (let i = 0; i < 10; i++) {
      modal = document.querySelector(
        ".jobs-easy-apply-modal, [data-test-modal-id='easy-apply-modal'], " +
        "div[role='dialog'][aria-label*='pply']"
      );
      if (modal) break;
      await sleep(500);
    }

    if (!modal) {
      showBadge("PostulAI: Modal no apareció", "#e74c3c");
      return;
    }

    showBadge("PostulAI: Llenando formulario...");

    const result = await LIFormFiller.fill(profile);

    if (result === true) {
      const { title, company } = getJobMeta();
      // Guardar tanto el jobId como la URL para deduplicación robusta
      appliedThisSession.add(jobId);
      appliedThisSession.add(location.href);
      chrome.storage.local.set({ appliedIds: [...appliedThisSession] });

      // Inferir cargo del perfil (primer cargo configurado)
      const cargo = profile.cargos?.[0] || '';

      // Reportar al background (que lo manda al backend)
      chrome.runtime.sendMessage({
        type: "APPLICATION_SUBMITTED",
        payload: { jobId, title, company, url: location.href, cargo },
      });

      showBadge(`PostulAI: Postulado a ${company || "empleo"}`, "#27ae60");

    } else if (result === "review") {
      showBadge("PostulAI: Listo para enviar — revisa y confirma", "#f39c12");
    } else {
      showBadge("PostulAI: No se pudo completar la postulacion", "#e74c3c");
    }
  }

  // ── modo AUTOPILOT ───────────────────────────────────────────────────────────
  // Recorre las tarjetas de empleos en la lista y aplica a cada una

  async function runAutopilot() {
    const jobCards = document.querySelectorAll(
      ".scaffold-layout__list-container .job-card-container, " +
      ".jobs-search-results-list li.ember-view, " +
      ".jobs-search__results-list li"
    );

    for (const card of jobCards) {
      // Solo tarjetas con indicador Easy Apply
      const hasEasyApply = card.textContent.includes("Easy Apply") ||
                           card.textContent.includes("Aplicar fácilmente");
      if (!hasEasyApply) continue;

      // Click en la tarjeta para cargar el detalle en el panel derecho
      card.querySelector("a, .job-card-list__title")?.click();
      await sleep(2500);

      await applyToCurrentJob();
      await sleep(rand(3000, 6000)); // pausa humana entre postulaciones
    }
  }

  function rand(a, b) { return Math.floor(Math.random() * (b - a)) + a; }

  // ── observer: detecta cambios de URL (SPA navigation) ───────────────────────

  let lastUrl = location.href;
  let debounce = null;

  const observer = new MutationObserver(() => {
    if (location.href === lastUrl) return;
    lastUrl = location.href;
    clearTimeout(debounce);
    debounce = setTimeout(async () => {
      if (!cfg.autopilot) return;
      await sleep(2000);
      await applyToCurrentJob();
    }, 1000);
  });

  observer.observe(document.body, { childList: true, subtree: true });

  // ── inicio ───────────────────────────────────────────────────────────────────

  // Escuchar mensajes del popup (ej: "aplica ahora" button)
  chrome.runtime.onMessage.addListener(async (msg) => {
    if (msg.type === "APPLY_NOW") {
      await applyToCurrentJob();
    }
    if (msg.type === "RUN_AUTOPILOT") {
      await runAutopilot();
    }
  });

  // En modo autopilot, correr al cargar la página
  if (cfg.autopilot) {
    await sleep(3000);
    await runAutopilot();
  }

})();
