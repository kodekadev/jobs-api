/**
 * content.js — Script principal inyectado en linkedin.com/jobs/*
 * Replica el flujo de JOBS_LKD.py (Selenium).
 * NO hace nada al cargar la página: solo actúa ante mensajes del popup
 * (APPLY_NOW / RUN_AUTOPILOT).
 */

(() => {

  // ── utilidades ──────────────────────────────────────────────────────────────

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rand  = (a, b) => Math.floor(Math.random() * (b - a)) + a;

  function getJobId() {
    const m = location.search.match(/currentJobId=(\d+)/) ||
              location.pathname.match(/\/view\/(\d+)/);
    return m ? m[1] : null;
  }

  function getJobMeta(jobId) {
    // DOM clásico
    let title   = document.querySelector(".job-details-jobs-unified-top-card__job-title, .jobs-unified-top-card__job-title")?.textContent.trim() ?? "";
    let company = document.querySelector(".job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name")?.textContent.trim() ?? "";

    // SDUI 2026: las clases viejas no existen — sacar título/empresa de la tarjeta
    if ((!title || !company) && jobId) {
      const row = document.querySelector(rowSelector(jobId));
      if (row) {
        const lines = (row.innerText || "").split("\n").map((l) => l.trim()).filter(Boolean);
        title   = title   || lines[0] || "";
        company = company || lines[1] || "";
      }
    }
    // Última opción: heading del wizard "Solicitar empleo en X"
    if (!company) {
      const h = LIFormFiller.deepQAll("h1, h2, h3").map((x) => x.textContent.trim())
        .find((t) => /^solicitar empleo en /i.test(t) || /^apply to /i.test(t));
      if (h) company = h.replace(/^solicitar empleo en /i, "").replace(/^apply to /i, "");
    }
    return { title, company };
  }

  // ── indicador visual ────────────────────────────────────────────────────────

  function showBadge(text, color = "#0077b5", sticky = false) {
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
    badge.style.background = color;
    badge.textContent = text;
    badge.style.opacity = "1";
    clearTimeout(badge._t);
    if (!sticky) badge._t = setTimeout(() => { badge.style.opacity = "0"; }, 4000);
  }

  // ── contexto (config + perfil) ──────────────────────────────────────────────

  let ctx = null; // { profile, applied:Set }

  async function ensureContext() {
    const cfg = await chrome.storage.sync.get(["enabled", "autopilot", "userId"]);
    if (!cfg.enabled) { showBadge("PostulAI: extensión desactivada — actívala en el popup", "#e74c3c"); return null; }
    if (!cfg.userId)  { showBadge("PostulAI: inicia sesión desde el popup", "#e74c3c"); return null; }

    const { profile } = await chrome.storage.local.get(["profile"]);
    if (!profile) { showBadge("PostulAI: perfil no sincronizado — abre el popup", "#e74c3c"); return null; }

    if (!ctx) {
      const { appliedIds } = await chrome.storage.local.get(["appliedIds"]);
      ctx = { applied: new Set(appliedIds || []) };
    }
    ctx.profile = profile;
    ctx.profile._autopilot = !!cfg.autopilot;
    return ctx;
  }

  function isAlreadyApplied(jobId) {
    if (!jobId || !ctx) return false;
    if (ctx.applied.has(jobId)) return true;
    for (const entry of ctx.applied) {
      if (String(entry).includes(jobId)) return true;
    }
    return false;
  }

  function markApplied(jobId) {
    ctx.applied.add(jobId);
    ctx.applied.add(location.href);
    chrome.storage.local.set({ appliedIds: [...ctx.applied] });
  }

  // LinkedIn marca los empleos ya postulados con "Solicitud enviada" / "Applied":
  // detectarlo permite saltarlos aunque no estén en nuestro caché (ej: postulados a mano)
  const APPLIED_RE = /solicitud enviada|ya has solicitado|application submitted|applied on|applied \d+ (minute|hour|day|week|month)/;

  function cardSaysApplied(row) {
    return APPLIED_RE.test((row?.innerText || "").toLowerCase());
  }

  function panelSaysApplied() {
    const scope = document.querySelector(".jobs-details__main-content, .job-view-layout") || document.body;
    return APPLIED_RE.test((scope.innerText || "").slice(0, 2500).toLowerCase());
  }

  // ── detección del botón Easy Apply ──────────────────────────────────────────
  // Solo botones que digan explícitamente "Solicitud sencilla" / "Easy Apply".
  // "Solicitar ↗" (postulación externa) NO abre modal y debe ignorarse.

  function isVisible(el) {
    if (!el || el.disabled) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function findEasyApplyButton() {
    // LinkedIn alterna entre <button>, <a> y divs con role=button según versión del DOM.
    // Las tarjetas de la lista son DIVs role=button cuyo texto incluye "Solicitud sencilla":
    // se excluyen exigiendo aria-label explícito o texto CORTO (el botón real solo dice eso).
    const PHRASES = ["solicitud sencilla", "easy apply", "aplicar fácilmente"];
    for (const btn of LIFormFiller.deepQAll("button, a, [role='button']")) {
      if (!isVisible(btn)) continue;
      if (btn.closest("div[role='dialog']")) continue; // controles dentro de un modal no cuentan
      const aria = (btn.getAttribute("aria-label") || "").toLowerCase();
      const txt  = (btn.textContent || "").trim().toLowerCase();
      // Excluir postulación externa ("Solicitar en el sitio web de la empresa")
      if (aria.includes("sitio web") || aria.includes("company website")) continue;
      if (PHRASES.some((p) => aria.includes(p))) return btn;
      if (txt.length < 40 && PHRASES.some((p) => txt.includes(p))) return btn;
    }
    return null;
  }

  // Detectar postulación EXTERNA ("Solicitar ↗" / en el sitio de la empresa)
  function findExternalApply() {
    for (const el of LIFormFiller.deepQAll("button, a, [role='button']")) {
      if (!isVisible(el)) continue;
      if (el.closest("div[role='dialog']")) continue;
      const aria = (el.getAttribute("aria-label") || "").toLowerCase();
      const txt  = (el.textContent || "").trim().toLowerCase();
      if (aria.includes("sitio web") || aria.includes("company website")) return el;
      if ((txt === "solicitar" || txt === "apply") && !aria.includes("sencilla")) return el;
    }
    return null;
  }

  // El panel SDUI de LinkedIn renderiza el botón con retardo: esperar con polling
  async function waitForApplyControl(timeoutMs = 8000) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
      const btn = findEasyApplyButton();
      if (btn) return btn;
      if (findExternalApply()) return null; // es postulación externa, no seguir esperando
      if (panelSaysApplied()) return null;  // ya postulado según LinkedIn
      await sleep(400);
    }
    return null;
  }

  function dumpApplyCandidates() {
    const cands = [...document.querySelectorAll("button, a, [role='button']")]
      .filter(isVisible)
      .map((b) => `${b.tagName}: aria="${b.getAttribute("aria-label") || ""}" txt="${(b.textContent || "").trim().slice(0, 35)}"`)
      .filter((t) => /solicit|apply|guardar|save/i.test(t));
    console.log("[PostulAI] DEBUG controles visibles:", cands);
  }

  // ── cierre de modales ───────────────────────────────────────────────────────

  async function dismissAnyModal() {
    try {
      const dialog = LIFormFiller.deepQAll("div[role='dialog']").find(isVisible) || LIFormFiller.findContainer();
      if (!dialog) return;
      const CLOSE_SEL =
        "button[aria-label='Descartar'], button[aria-label='Dismiss'], " +
        "button[aria-label='Cerrar'], button[aria-label='Close']";
      const dismiss = LIFormFiller.deepQ(CLOSE_SEL, dialog) || LIFormFiller.deepQ(CLOSE_SEL);
      if (dismiss) {
        dismiss.click();
        await sleep(800);
        // LinkedIn pregunta "¿Descartar solicitud?" — confirmar
        const confirm = LIFormFiller.deepQAll("button").find((b) =>
          b.textContent.trim().toLowerCase() === "descartar" ||
          b.textContent.trim().toLowerCase() === "discard"
        );
        if (confirm) { confirm.click(); await sleep(500); }
      }
    } catch (_) {}
  }

  async function closeSuccessModal() {
    await sleep(800);
    // LinkedIn muestra "Hecho" (español) tras enviar; a veces solo una X
    const dialog = LIFormFiller.deepQAll("div[role='dialog']").find(isVisible);
    const root = dialog || document;
    const done = LIFormFiller.deepQAll("button", root).find((b) => {
      const t = b.textContent.trim().toLowerCase();
      return t === "hecho" || t === "done" || t === "listo";
    }) || LIFormFiller.deepQ(
      "button[aria-label='Cerrar'], button[aria-label='Close'], button[aria-label='Dismiss'], button[aria-label='Descartar']",
      root
    );
    if (done) { done.click(); await sleep(500); }
  }

  // ── postular al empleo actualmente visible en el panel derecho ──────────────
  // Retorna: "applied" | "review" | "skipped" | "failed"

  async function applyToCurrentJob(overrideJobId) {
    const jobId = overrideJobId || getJobId();
    if (!jobId) { console.log("[PostulAI] Sin jobId en la URL"); return "skipped"; }
    if (isAlreadyApplied(jobId)) { console.log(`[PostulAI] ${jobId} ya postulado, saltando`); return "skipped"; }

    // Si el wizard ya está abierto, el botón Easy Apply queda oculto detrás:
    // ir directo al llenado (SDUI ya no usa div[role='dialog'])
    let modal = LIFormFiller.findContainer();

    if (!modal) {
      const applyBtn = await waitForApplyControl();
      if (!applyBtn) {
        if (panelSaysApplied()) {
          console.log(`[PostulAI] ${jobId}: LinkedIn indica "Solicitud enviada", saltando`);
          markApplied(jobId);
        } else if (findExternalApply()) {
          console.log("[PostulAI] Postulación externa (Solicitar ↗), saltando");
        } else {
          console.log("[PostulAI] No hay botón Easy Apply visible tras 8s");
          dumpApplyCandidates();
        }
        return "skipped";
      }
      showBadge("PostulAI: abriendo Easy Apply...");
      applyBtn.click();

      for (let i = 0; i < 16 && !modal; i++) {
        await sleep(500);
        modal = LIFormFiller.findContainer();
      }
      if (!modal) {
        showBadge("PostulAI: el formulario no apareció", "#e74c3c");
        const iframes = [...document.querySelectorAll("iframe")].map((f) => (f.src || "(sin src)").slice(0, 90));
        console.warn(
          `[PostulAI] Formulario Easy Apply no encontrado tras 8s | url=${location.href.slice(0, 90)} | ` +
          `dialogs=${LIFormFiller.deepQAll("div[role='dialog']").length} forms=${LIFormFiller.deepQAll("form").length} ` +
          `iframes=${JSON.stringify(iframes)}`
        );
        return "failed";
      }
    } else {
      console.log("[PostulAI] Modal ya abierto — llenando directamente");
    }

    showBadge("PostulAI: llenando formulario...");
    const result = await LIFormFiller.fill(ctx.profile);

    if (result === true) {
      const { title, company } = getJobMeta(jobId);
      markApplied(jobId);

      chrome.runtime.sendMessage({
        type: "APPLICATION_SUBMITTED",
        payload: { jobId, title, company, url: `https://www.linkedin.com/jobs/view/${jobId}/`, cargo: ctx.profile.cargos?.[0] || "" },
      });

      showBadge(`PostulAI: postulado a ${company || "empleo"}`, "#27ae60");
      console.log("[PostulAI] Postulación enviada:", title, "—", company);
      await closeSuccessModal();
      return "applied";
    }

    if (result === "review") {
      showBadge("PostulAI: listo para enviar — revisa y confirma", "#f39c12");
      return "review";
    }

    showBadge("PostulAI: no se pudo completar la postulación", "#e74c3c");
    console.warn("[PostulAI] fill() retornó:", result);
    await dismissAnyModal();
    return "failed";
  }

  // ── modo AUTOPILOT ──────────────────────────────────────────────────────────

  // LinkedIn cambia el DOM seguido: soportar todas las variantes conocidas de tarjeta.
  // DOM 2026: div[role=button][componentkey="job-card-component-ref-<jobId>"]
  const CARD_SELECTOR =
    "li[data-occludable-job-id], div[data-job-id], li[data-job-id], [componentkey^='job-card-component-ref-']";

  function cardId(el) {
    return el.getAttribute("data-occludable-job-id") ||
           el.getAttribute("data-job-id") ||
           (el.getAttribute("componentkey") || "").replace("job-card-component-ref-", "") ||
           null;
  }

  function rowSelector(jobId) {
    return `li[data-occludable-job-id="${jobId}"], div[data-job-id="${jobId}"], ` +
           `li[data-job-id="${jobId}"], [componentkey="job-card-component-ref-${jobId}"]`;
  }

  function idFromHref(href) {
    const m = (href || "").match(/\/jobs\/view\/(\d+)/) || (href || "").match(/currentJobId=(\d+)/);
    return m ? m[1] : null;
  }

  function getJobList() {
    // Encontrar el contenedor scrolleable real subiendo desde una tarjeta
    const card = document.querySelector(CARD_SELECTOR) ||
                 document.querySelector("a[href*='/jobs/view/']");
    for (let el = card?.parentElement; el && el !== document.body; el = el.parentElement) {
      const style = getComputedStyle(el);
      if (el.scrollHeight > el.clientHeight + 50 && /auto|scroll/.test(style.overflowY)) return el;
    }
    return document.querySelector(
      "#main ul[class*='scaffold-layout__list'], .scaffold-layout__list-container, .scaffold-layout__list, #main ul"
    );
  }

  // LinkedIn usa DOM virtual: recolectar IDs MIENTRAS se scrollea,
  // porque las tarjetas fuera de vista se descargan del DOM
  async function collectJobIds() {
    const jobIdSet = new Set();
    const collect = () => {
      document.querySelectorAll(CARD_SELECTOR).forEach((el) => {
        const id = cardId(el);
        if (id && /^\d+$/.test(id)) jobIdSet.add(id);
      });
      // Fallback solo si no hay tarjetas: los links suelen apuntar al empleo seleccionado
      if (!jobIdSet.size) {
        document.querySelectorAll("a[href*='/jobs/view/'], a[href*='currentJobId=']").forEach((a) => {
          const id = idFromHref(a.getAttribute("href"));
          if (id) jobIdSet.add(id);
        });
      }
    };

    const jobList = getJobList();
    if (jobList) {
      jobList.scrollTop = 0;
      await sleep(600);
      collect();
      for (let i = 0; i < 30; i++) {
        const prev = jobList.scrollTop;
        jobList.scrollTop += 400;
        await sleep(350);
        collect();
        if (jobList.scrollTop === prev) break; // fin de la lista
      }
      jobList.scrollTop = 0;
      await sleep(800);
    } else {
      console.warn("[PostulAI] No se encontró contenedor scrolleable de la lista");
      collect();
    }
    return [...jobIdSet];
  }

  function queryRow(jobId) {
    // Retornar el elemento interactivo (la tarjeta role=button), no el <li> contenedor
    const row = document.querySelector(rowSelector(jobId));
    if (row) return row;
    // Fallback: link cuyo href contenga el id
    for (const a of document.querySelectorAll("a[href*='/jobs/view/'], a[href*='currentJobId=']")) {
      if (idFromHref(a.getAttribute("href")) === jobId) return a;
    }
    return null;
  }

  // Re-traer una tarjeta al DOM scrolleando la lista (DOM virtual)
  async function findRow(jobId) {
    let row = queryRow(jobId);
    if (row) return row;
    const list = getJobList();
    if (!list) return null;
    list.scrollTop = 0;
    await sleep(400);
    for (let s = 0; s < 30; s++) {
      row = queryRow(jobId);
      if (row) return row;
      const prev = list.scrollTop;
      list.scrollTop += 400;
      await sleep(300);
      if (list.scrollTop === prev) break;
    }
    return queryRow(jobId);
  }

  // Esperar a que el panel derecho cargue el empleo clickeado
  async function waitForJobPanel(jobId) {
    for (let i = 0; i < 12; i++) {
      if (getJobId() === jobId && findEasyApplyButton()) return true;
      if (getJobId() === jobId && i >= 6) return true; // cargó pero sin botón (externo)
      await sleep(400);
    }
    return getJobId() === jobId;
  }

  // ── paginación de resultados ────────────────────────────────────────────────

  function findNextPageButton() {
    const direct = document.querySelector(
      ".jobs-search-pagination__button--next, " +
      "button[aria-label='Ver siguiente página'], button[aria-label='Página siguiente'], " +
      "button[aria-label='Siguiente'], button[aria-label='Next']"
    );
    if (direct && isVisible(direct)) return direct;
    for (const btn of LIFormFiller.deepQAll("button, a")) {
      if (!isVisible(btn)) continue;
      const aria = (btn.getAttribute("aria-label") || "").toLowerCase();
      if ((aria.includes("siguiente") || aria.includes("next")) &&
          (aria.includes("página") || aria.includes("pagina") || aria.includes("page"))) return btn;
    }
    return null;
  }

  // Click en "siguiente página" y espera a que cambie la lista (navegación SPA)
  async function gotoNextPage() {
    const btn = findNextPageButton();
    if (!btn) return false;
    const before = cardId(document.querySelector(CARD_SELECTOR) || document.createElement("div"));
    btn.scrollIntoView({ block: "center" });
    await sleep(300);
    btn.click();
    for (let i = 0; i < 20; i++) {
      await sleep(600);
      const first = document.querySelector(CARD_SELECTOR);
      if (first && cardId(first) !== before) { await sleep(1200); return true; }
    }
    return false;
  }

  function hitDailyLimit() {
    const err = LIFormFiller.deepQ("div.artdeco-inline-feedback--error");
    if (!err) return false;
    const t = err.textContent.toLowerCase();
    return t.includes("límite") || t.includes("limit") || t.includes("alcanzado");
  }

  // Web Lock: exime a la pestaña del throttling intensivo de timers cuando
  // está oculta (modo 2do plano), sin él los sleeps correrían 1 vez por minuto
  async function runAutopilot(maxJobs) {
    let out;
    await navigator.locks.request("postulai-autopilot", async () => {
      out = await runAutopilotInner(maxJobs);
    });
    return out;
  }

  const MAX_PAGES = 15;
  const MAX_CONSECUTIVE_FAILS = 5;

  async function runAutopilotInner(maxJobs) {
    console.log("[PostulAI] Autopilot: recolectando empleos...");
    showBadge("PostulAI: buscando empleos...", "#0077b5", true);

    let applied = 0, failed = 0, skipped = 0, total = 0, pages = 0;
    let consecutiveFails = 0;
    let stopReason = null;
    const seen = new Set();

    for (let page = 1; page <= MAX_PAGES && !stopReason; page++) {
      const jobIds = (await collectJobIds()).filter((id) => !seen.has(id));
      jobIds.forEach((id) => seen.add(id));
      pages = page;
      total += jobIds.length;
      console.log(`[PostulAI] Página ${page}: ${jobIds.length} tarjetas nuevas`);

      if (!jobIds.length) {
        if (page === 1) showBadge("PostulAI: no se encontraron empleos en esta página", "#e74c3c");
        break;
      }

      for (let i = 0; i < jobIds.length; i++) {
        const jobId = jobIds[i];
        if (maxJobs > 0 && applied >= maxJobs) { stopReason = "limite-plan"; break; }

        console.log(`[PostulAI] Pág ${page} — empleo ${i + 1}/${jobIds.length} — ID: ${jobId}`);
        showBadge(`PostulAI: pág ${page}, empleo ${i + 1}/${jobIds.length} (${applied} postulados)`, "#0077b5", true);

        // Un error en un empleo NO detiene el ciclo
        try {
          if (isAlreadyApplied(jobId)) { skipped++; continue; }

          // Cerrar cualquier modal residual del empleo anterior (premium, descartar, etc.)
          await dismissAnyModal();
          const stray = document.querySelector(
            "button[aria-label='Cerrar'], button[aria-label='Close'], button[aria-label='Descartar']"
          );
          if (stray && !stray.closest("div[role='dialog']")) { try { stray.click(); await sleep(400); } catch (_) {} }

          // Re-query de la tarjeta desde el DOM actual (DOM virtual)
          const row = await findRow(jobId);
          if (!row) {
            console.log(`[PostulAI] Tarjeta ${jobId} no encontrada en el DOM, saltando`);
            skipped++;
            continue;
          }

          // LinkedIn ya la marca como "Solicitud enviada": saltar sin abrir el panel
          if (cardSaysApplied(row)) {
            console.log(`[PostulAI] ${jobId} ya postulado según LinkedIn, saltando`);
            markApplied(jobId);
            skipped++;
            continue;
          }

          row.scrollIntoView({ block: "center" });
          await sleep(300);
          const clickable = row.matches("a, [role='button']")
            ? row
            : (row.querySelector("[role='button'], a") || row);
          clickable.click();
          await waitForJobPanel(jobId);
          await sleep(600);

          if (hitDailyLimit()) {
            console.warn("[PostulAI] Límite diario de LinkedIn alcanzado");
            showBadge("PostulAI: límite diario de LinkedIn alcanzado", "#e74c3c");
            stopReason = "limite-linkedin";
            break;
          }

          const result = await applyToCurrentJob(jobId);
          if (result === "applied") { applied++; consecutiveFails = 0; }
          else if (result === "failed") { failed++; consecutiveFails++; }
          else { skipped++; consecutiveFails = 0; }

        } catch (err) {
          failed++;
          consecutiveFails++;
          console.warn(`[PostulAI] Error en empleo ${jobId}:`, err?.message || err);
          await dismissAnyModal();
        }

        // Demasiados fallos seguidos = algo cambió en LinkedIn; cortar para no insistir
        if (consecutiveFails >= MAX_CONSECUTIVE_FAILS) {
          console.warn(`[PostulAI] ${MAX_CONSECUTIVE_FAILS} fallos consecutivos — abortando ciclo`);
          showBadge("PostulAI: demasiados errores seguidos, ciclo detenido", "#e74c3c");
          stopReason = "fallos-consecutivos";
          break;
        }

        await sleep(rand(2000, 4500));
      }

      if (!stopReason && !(await gotoNextPage())) break;
    }

    const stats = {
      date: new Date().toISOString(), applied, failed, skipped, total, pages,
      stopReason: stopReason || "fin-resultados",
    };
    try { chrome.storage.local.set({ lastRunStats: stats }); } catch (_) {}
    console.log("[PostulAI] Autopilot completado —", JSON.stringify(stats));
    showBadge(`PostulAI: ciclo completado — ${applied} postulaciones`, applied > 0 ? "#27ae60" : "#f39c12");
    const out = { applied, total };
    if (stopReason === "fallos-consecutivos") out.error = "fallos-consecutivos";
    return out;
  }

  // ── mensajes desde el popup ─────────────────────────────────────────────────
  // El listener se registra SIEMPRE (aunque falte perfil) para que el popup
  // reciba respuesta y el usuario vea el error en pantalla.

  let autopilotRunning = false;

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === "PING") {
      sendResponse({ pong: true });
      return;
    }

    if (msg.type === "RUN_AUTOPILOT_BG") {
      // Modo automático: corre en pestaña sin foco y avisa al background al terminar
      if (autopilotRunning) {
        sendResponse({ ok: false, error: "already_running" });
        return;
      }
      (async () => {
        let result = { applied: 0, total: 0 };
        try {
          const c = await ensureContext();
          if (!c) { result.error = "context"; return; }
          c.profile._autopilot = true;
          autopilotRunning = true;
          result = await runAutopilot(msg.maxJobs);
        } catch (err) {
          result.error = err && err.message;
        } finally {
          autopilotRunning = false;
          try { chrome.runtime.sendMessage({ type: "AUTOPILOT_DONE", result }); } catch (_) {}
        }
      })();
      sendResponse({ ok: true, started: true });
      return;
    }

    if (msg.type === "APPLY_NOW") {
      (async () => {
        const c = await ensureContext();
        if (!c) return sendResponse({ ok: false, error: "context" });
        const result = await applyToCurrentJob();
        sendResponse({ ok: true, result });
      })();
      return true; // respuesta asíncrona
    }

    if (msg.type === "RUN_AUTOPILOT") {
      if (autopilotRunning) {
        console.log("[PostulAI] Autopilot ya está corriendo");
        sendResponse({ ok: false, error: "already_running" });
        return;
      }
      (async () => {
        const c = await ensureContext();
        if (!c) return;
        c.profile._autopilot = true; // RUN_AUTOPILOT implica envío automático
        autopilotRunning = true;
        try { await runAutopilot(msg.maxJobs); }
        finally { autopilotRunning = false; }
      })();
      sendResponse({ ok: true, started: true });
      return;
    }
  });

  console.log("[PostulAI] Content script listo — usa el popup para postular o iniciar el autopilot");

})();
