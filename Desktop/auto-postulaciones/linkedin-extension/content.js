/**
 * content.js — Script principal inyectado en linkedin.com/jobs/*
 */

(async () => {

  const cfg = await new Promise(r =>
    chrome.storage.sync.get(["enabled", "autopilot", "userId"], r)
  );

  console.log("[PostulAI] Content script iniciado", { enabled: cfg.enabled, userId: cfg.userId, autopilot: cfg.autopilot });

  if (!cfg.enabled || !cfg.userId) {
    console.warn("[PostulAI] Detenido: extensión desactivada o sin userId");
    return;
  }

  const profile = await new Promise(r =>
    chrome.storage.local.get(["profile"], d => r(d.profile))
  );

  if (!profile) {
    console.warn("[PostulAI] Perfil no cargado. Abre el popup para sincronizar.");
    return;
  }

  console.log("[PostulAI] Perfil cargado:", profile.nombre);
  profile._autopilot = !!cfg.autopilot;

  const _storedIds = await new Promise(r => chrome.storage.local.get(["appliedIds"], d =>
    r(d.appliedIds || [])
  ));
  const appliedThisSession = new Set(_storedIds);

  // ── utilidades ───────────────────────────────────────────────────────────────

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  function rand(a, b) { return Math.floor(Math.random() * (b - a)) + a; }

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

  // ── detectar botón Easy Apply visible en el panel derecho ───────────────────

  function isVisible(btn) {
    if (!btn || btn.disabled) return false;
    const r = btn.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function findEasyApplyButton() {
    // Solo retorna botones que explícitamente digan "Solicitud sencilla" o "Easy Apply"
    // Ignora "Solicitar ↗" (externo) aunque tenga la misma clase
    for (const btn of document.querySelectorAll("button")) {
      if (!isVisible(btn)) continue;
      const aria = (btn.getAttribute("aria-label") || "").toLowerCase();
      const txt  = btn.textContent.trim().toLowerCase();
      if (aria.includes("solicitud sencilla") || aria.includes("easy apply") ||
          aria.includes("aplicar fácilmente") ||
          txt.includes("solicitud sencilla") || txt.includes("easy apply") ||
          txt.includes("aplicar fácilmente")) {
        return btn;
      }
    }
    return null;
  }

  // ── lógica de postulación ────────────────────────────────────────────────────

  function isAlreadyApplied(jobId) {
    if (!jobId) return false;
    if (appliedThisSession.has(jobId)) return true;
    for (const entry of appliedThisSession) {
      if (String(entry).includes(jobId)) return true;
    }
    return false;
  }

  async function applyToCurrentJob(overrideJobId) {
    const jobId = overrideJobId || getJobId();
    if (!jobId || isAlreadyApplied(jobId)) return;

    const applyBtn = findEasyApplyButton();
    if (!applyBtn) {
      console.log("[PostulAI] No hay botón Easy Apply visible");
      return;
    }

    showBadge("PostulAI: Abriendo Easy Apply...");
    applyBtn.click();
    await sleep(2000);

    // Esperar a que aparezca el modal
    let modal = null;
    for (let i = 0; i < 10; i++) {
      modal = document.querySelector(
        ".jobs-easy-apply-modal, [data-test-modal-id='easy-apply-modal'], " +
        "div[role='dialog'][aria-label*='pply'], div[role='dialog'][aria-label*='olicitud'], " +
        "div[role='dialog'][aria-label*='Aplicar']"
      );
      if (modal) break;
      await sleep(500);
    }

    if (!modal) {
      showBadge("PostulAI: Modal no apareció", "#e74c3c");
      console.warn("[PostulAI] Modal no encontrado tras 5s");
      return;
    }

    showBadge("PostulAI: Llenando formulario...");
    console.log("[PostulAI] Modal encontrado, llenando...");

    const result = await LIFormFiller.fill(profile);

    if (result === true) {
      const { title, company } = getJobMeta();
      appliedThisSession.add(jobId);
      appliedThisSession.add(location.href);
      chrome.storage.local.set({ appliedIds: [...appliedThisSession] });

      const cargo = profile.cargos?.[0] || '';
      chrome.runtime.sendMessage({
        type: "APPLICATION_SUBMITTED",
        payload: { jobId, title, company, url: location.href, cargo },
      });

      showBadge(`PostulAI: Postulado a ${company || "empleo"}`, "#27ae60");
      console.log("[PostulAI] Postulación enviada:", title, company);

    } else if (result === "review") {
      showBadge("PostulAI: Listo para enviar — revisa y confirma", "#f39c12");
    } else {
      showBadge("PostulAI: No se pudo completar la postulacion", "#e74c3c");
      console.warn("[PostulAI] fill() retornó:", result);
      // Cerrar modal Easy Apply
      const dismiss = document.querySelector(
        "button[aria-label='Descartar'], button[aria-label='Dismiss'], button[aria-label='Close']"
      );
      if (dismiss) {
        dismiss.click();
        await sleep(800);
        // LinkedIn muestra "¿Guardar solicitud?" — confirmar descarte
        const confirmDiscard = [...document.querySelectorAll("button")].find(b =>
          b.textContent.trim() === "Descartar"
        );
        if (confirmDiscard) { confirmDiscard.click(); await sleep(500); }
      }
    }
  }

  // ── modo AUTOPILOT ───────────────────────────────────────────────────────────

  async function runAutopilot() {
    console.log("[PostulAI] Autopilot: iniciando búsqueda de empleos...");

    // Hacer scroll de la lista para forzar lazy loading (igual que el script RPA)
    const jobList = document.querySelector(
      "#main ul[class*='scaffold-layout__list'], #main ul, .scaffold-layout__list-container"
    );
    if (jobList) {
      for (let i = 0; i < 5; i++) {
        jobList.scrollTop += 600;
        await sleep(500);
      }
      jobList.scrollTop = 0;
      await sleep(1000);
    }

    // Selector estable igual al script RPA en Python
    const rows = Array.from(document.querySelectorAll("li[data-occludable-job-id]"));
    console.log("[PostulAI] Tarjetas encontradas:", rows.length);

    if (!rows.length) {
      // Fallback: links directos
      const links = Array.from(document.querySelectorAll('a[href*="/jobs/view/"], a[href*="currentJobId="]'))
        .filter((a, i, arr) => arr.findIndex(b => b.href === a.href) === i);
      console.log("[PostulAI] Fallback — links encontrados:", links.length);
      for (const link of links) {
        link.click();
        await sleep(2500);
        await applyToCurrentJob();
        await sleep(rand(3000, 6000));
      }
      return;
    }

    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      const jobId = row.getAttribute("data-occludable-job-id");
      console.log(`[PostulAI] Empleo ${i+1}/${rows.length} — ID: ${jobId}`);

      if (isAlreadyApplied(jobId)) {
        console.log("[PostulAI] Ya aplicado, saltando");
        continue;
      }

      // Cerrar modal premium si quedó abierto del empleo anterior
      try {
        const closeBtn = document.querySelector(
          "button[aria-label='Cerrar'], button[aria-label='Close'], button[aria-label='Descartar']"
        );
        if (closeBtn) { closeBtn.click(); await sleep(500); }
      } catch(_) {}

      row.click();
      await sleep(2000); // Esperar a que cargue el panel derecho

      // Verificar que haya límite diario
      const errorDiv = document.querySelector("div.artdeco-inline-feedback--error");
      if (errorDiv && errorDiv.textContent.includes("límite")) {
        console.warn("[PostulAI] Límite diario alcanzado");
        showBadge("PostulAI: Límite diario de LinkedIn alcanzado", "#e74c3c");
        break;
      }

      // Verificar que tenga botón de Solicitud sencilla
      const applyBtn = findEasyApplyButton();
      if (!applyBtn) {
        console.log("[PostulAI] Sin botón Easy Apply, saltando");
        continue;
      }

      await applyToCurrentJob(jobId);
      await sleep(rand(3000, 6000));
    }

    console.log("[PostulAI] Autopilot: ciclo completado");
    showBadge("PostulAI: Ciclo completado", "#27ae60");
  }

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

  // ── mensajes desde el popup ──────────────────────────────────────────────────

  chrome.runtime.onMessage.addListener(async (msg) => {
    if (msg.type === "APPLY_NOW")    await applyToCurrentJob();
    if (msg.type === "RUN_AUTOPILOT") await runAutopilot();
  });

  // ── inicio ───────────────────────────────────────────────────────────────────

  if (cfg.autopilot) {
    console.log("[PostulAI] Autopilot activo, esperando 3s para iniciar...");
    await sleep(3000);
    await runAutopilot();
  } else {
    console.log("[PostulAI] Modo manual activo (autopilot desactivado)");
  }

})();
