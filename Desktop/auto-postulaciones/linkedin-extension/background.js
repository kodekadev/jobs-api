/**
 * background.js — Service Worker de la extensión PostulAI.
 */

const SYNC_ALARM   = "postulai-sync";
const SYNC_MINUTES = 30;
const DAILY_ALARM  = "postulai-daily";

const PLAN_LIMITS = { FREE: 10, PRO: 25, PREMIUM: 50, TRIAL: 25 };

// ── alarmas ───────────────────────────────────────────────────────────────────

chrome.alarms.create(SYNC_ALARM,  { periodInMinutes: SYNC_MINUTES });
chrome.alarms.create(DAILY_ALARM, { periodInMinutes: 60 });

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === SYNC_ALARM)  syncProfile();
  if (alarm.name === DAILY_ALARM) maybeRunDailyAutopilot();
});

chrome.runtime.onStartup.addListener(() => {
  syncProfile();
  syncAppliedIds();
  maybeRunDailyAutopilot();
});

// ── sincronizar perfil desde la API ──────────────────────────────────────────

async function syncProfile() {
  const { apiUrl, apiToken, userId } = await getConfig();
  if (!apiUrl || !apiToken || !userId) return;

  try {
    // Ruta correcta: GET /api/profile/:id
    const r = await fetch(`${apiUrl}/api/profile/${userId}`, {
      headers: { Authorization: `Bearer ${apiToken}` },
    });
    if (!r.ok) return;
    const data = await r.json();
    // Normalizar: la API devuelve { usuario, perfil, postula_facil, ... }
    const pf = data.postula_facil || {};
    const profile = {
      id:                    data.usuario?.id       || userId,
      nombre:                data.usuario?.nombre   || '',
      email:                 data.usuario?.email    || '',
      celular:               data.usuario?.celular  || '',
      profesion:             data.perfil?.profesion || '',
      cv_url:                pf.cv_url || data.perfil?.cv_url || '',
      cargos:                pf.cargos      || [],
      ubicaciones:           pf.ubicaciones || [],
      experiencia:           pf.experiencia || '',
      resumen:               pf.resumen     || '',
      pretension_general:    pf.pretension_general || '',
      carrera:               pf.carrera     || '',
      nivel_educativo:       pf.nivel_educativo || '',
      institucion:           pf.institucion || '',
      actualmente_trabajando: pf.actualmente_trabajando ?? true,
      rut:                   pf.rut         || '',
      fecha_nacimiento:      pf.fecha_nacimiento || '',
    };
    await chrome.storage.local.set({ profile });
    console.log("[PostulAI] Perfil sincronizado:", profile.nombre);
  } catch (e) {
    console.warn("[PostulAI] Error sincronizando perfil:", e.message);
  }
}

// ── sincronizar IDs ya postulados (deduplicación) ─────────────────────────────

async function syncAppliedIds() {
  const { apiUrl, apiToken, userId } = await getConfig();
  if (!apiUrl || !apiToken || !userId) return;

  try {
    const r = await fetch(`${apiUrl}/api/applications/${userId}/applied`, {
      headers: { Authorization: `Bearer ${apiToken}` },
    });
    if (!r.ok) return;
    const ids = await r.json(); // string[]
    await chrome.storage.local.set({ appliedIds: ids });
    console.log("[PostulAI] IDs aplicados sincronizados:", ids.length);
  } catch (e) {
    console.warn("[PostulAI] Error sincronizando appliedIds:", e.message);
  }
}

// ── autopilot diario en segundo plano ────────────────────────────────────────
// Abre LinkedIn en una pestaña sin foco, postula con los cargos/ubicación del
// perfil y cierra la pestaña al terminar. Corre 1 vez al día.

function searchUrl(cargo, location) {
  const p = new URLSearchParams({ keywords: cargo, location, f_AL: "true" });
  return `https://www.linkedin.com/jobs/search/?${p.toString()}`;
}

function buildLocation(ubicaciones) {
  const u = (ubicaciones && ubicaciones[0]) || "";
  if (!u) return "Chile";
  return /chile/i.test(u) ? u : `${u}, Chile`;
}

function notify(title, message) {
  chrome.notifications.create({ type: "basic", iconUrl: "icons/icon48.png", title, message });
}

async function getPlanLimit(cfg) {
  try {
    const r = await fetch(`${cfg.apiUrl}/api/plan/${cfg.userId}`, {
      headers: { Authorization: `Bearer ${cfg.apiToken}` },
    });
    if (r.status === 401) {
      notify("PostulAI", "Tu sesión expiró — vuelve a iniciar sesión en PostulAI para seguir postulando");
      return null;
    }
    if (!r.ok) return PLAN_LIMITS.FREE;
    const d = await r.json();
    return PLAN_LIMITS[(d.plan || "FREE").toUpperCase()] ?? PLAN_LIMITS.FREE;
  } catch (_) {
    return PLAN_LIMITS.FREE;
  }
}

// Espera a que el content script cargue; "auth" si LinkedIn pide login
async function waitForContentScript(tabId, timeoutMs = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const res = await chrome.tabs.sendMessage(tabId, { type: "PING" });
      if (res && res.pong) return true;
    } catch (_) {}
    let tab;
    try { tab = await chrome.tabs.get(tabId); } catch (_) { return false; }
    if (tab.url && !/linkedin\.com\/jobs/.test(tab.url)) return "auth";
    await new Promise(r => setTimeout(r, 1500));
  }
  return false;
}

async function getDailyCount() {
  const today  = new Date().toISOString().slice(0, 10);
  const stored = await chrome.storage.local.get(["dailyCount", "dailyDate"]);
  return stored.dailyDate === today ? (stored.dailyCount || 0) : 0;
}

async function maybeRunDailyAutopilot(force = false) {
  try {
    await runDailyAutopilot(force);
  } catch (e) {
    console.warn("[PostulAI] Auto: error", e);
    await chrome.storage.local.set({ autoLastError: String((e && e.message) || e) });
  }
}

const mark = s => chrome.storage.local.set({ autoStage: s });

async function runDailyAutopilot(force) {
  await mark("config");
  const cfg = await getConfig();
  if (!cfg.apiUrl || !cfg.apiToken || !cfg.userId) return mark("sin-config");

  const prefs = await new Promise(r => chrome.storage.sync.get(["enabled", "autopilot"], r));
  if (prefs.enabled === false || prefs.autopilot === false) return mark("desactivado");

  const today = new Date().toISOString().slice(0, 10);
  const st = await chrome.storage.local.get(["lastAutoRun", "autoRun", "profile"]);
  if (!force && st.lastAutoRun === today) return mark("ya-corrio-hoy");

  // ¿Hay una corrida colgada? Si su pestaña sigue viva, no duplicar
  if (st.autoRun) {
    const alive = await chrome.tabs.get(st.autoRun.tabId).catch(() => null);
    if (alive) return mark("corrida-activa");
    await chrome.storage.local.remove("autoRun");
  }

  let profile = st.profile;
  if (!profile || !profile.cargos || !profile.cargos.length) {
    await syncProfile();
    profile = (await chrome.storage.local.get("profile")).profile;
  }
  if (!profile || !profile.cargos || !profile.cargos.length) {
    console.log("[PostulAI] Auto: sin cargos en el perfil, no se ejecuta");
    return mark("sin-cargos");
  }

  await mark("plan");
  const limit = await getPlanLimit(cfg);
  if (limit === null) return mark("sesion-expirada");

  const remaining = limit - (await getDailyCount());
  if (remaining <= 0) {
    await chrome.storage.local.set({ lastAutoRun: today });
    return mark("limite-agotado");
  }
  await mark("abriendo-tab");

  const location = buildLocation(profile.ubicaciones);
  const cargos   = profile.cargos;
  console.log(`[PostulAI] Auto: iniciando — cargos: ${cargos.join(", ")} | ${location} | tope: ${remaining}`);

  // Ventana aparte: Chrome congela el rendering de pestañas ocultas y de
  // ventanas 100% tapadas (occlusion) — el wizard nunca se monta. La ventana
  // debe quedar visible al frente; el usuario puede usar otras apps al lado,
  // pero no minimizarla ni taparla por completo.
  const win = await chrome.windows.create({
    url:     searchUrl(cargos[0], location),
    focused: true,
    width:   1300,
    height:  850,
    left:    60,
    top:     40,
  });
  notify("PostulAI", "Postulando por ti en la ventana nueva — puedes seguir usando el PC, pero no la minimices");
  const tab = win.tabs && win.tabs[0];
  await chrome.storage.local.set({
    autoRun: { tabId: tab.id, winId: win.id, cargos, location, cargoIndex: 0, limit, date: today },
  });

  const ok = await waitForContentScript(tab.id);
  if (ok === "auth") {
    notify("PostulAI", "Inicia sesión en LinkedIn una vez para que postulemos por ti automáticamente");
    await chrome.storage.local.set({ lastAutoRun: today });
    await chrome.storage.local.remove("autoRun");
    chrome.windows.remove(win.id).catch(() => {});
    return;
  }
  if (!ok) {
    await chrome.storage.local.remove("autoRun");
    chrome.windows.remove(win.id).catch(() => {});
    return;
  }

  chrome.tabs.sendMessage(tab.id, { type: "RUN_AUTOPILOT_BG", maxJobs: remaining }).catch(() => {});
}

// El content script avisa cuando termina un ciclo → siguiente cargo o cierre
async function handleAutopilotDone(tabId, result) {
  const { autoRun } = await chrome.storage.local.get("autoRun");
  if (!autoRun || autoRun.tabId !== tabId) return; // corrida manual desde popup

  const count     = await getDailyCount();
  const remaining = autoRun.limit - count;
  const nextIdx   = autoRun.cargoIndex + 1;

  if (remaining > 0 && nextIdx < autoRun.cargos.length && !(result && result.error)) {
    autoRun.cargoIndex = nextIdx;
    await chrome.storage.local.set({ autoRun });
    await chrome.tabs.update(tabId, { url: searchUrl(autoRun.cargos[nextIdx], autoRun.location) });
    const ok = await waitForContentScript(tabId);
    if (ok === true) {
      chrome.tabs.sendMessage(tabId, { type: "RUN_AUTOPILOT_BG", maxJobs: remaining }).catch(() => {});
      return;
    }
  }

  await chrome.storage.local.set({ lastAutoRun: autoRun.date });
  await chrome.storage.local.remove("autoRun");
  if (autoRun.winId) chrome.windows.remove(autoRun.winId).catch(() => {});
  else chrome.tabs.remove(tabId).catch(() => {});
  notify("PostulAI — Postulación automática", `Hoy postulamos a ${count} empleo(s) por ti`);
  console.log(`[PostulAI] Auto: ciclo diario completado — ${count} postulaciones`);
}

// ── mensajes internos (desde content.js y popup) ─────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "APPLICATION_SUBMITTED") {
    handleApplication(msg.payload).then(() => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === "SYNC_NOW") {
    Promise.all([syncProfile(), syncAppliedIds()]).then(() => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === "GET_STATS") {
    getStats().then(stats => sendResponse(stats));
    return true;
  }
  if (msg.type === "RUN_DAILY_NOW") {
    maybeRunDailyAutopilot(true).then(() => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === "AUTOPILOT_DONE") {
    handleAutopilotDone(sender.tab && sender.tab.id, msg.result).then(() => sendResponse({ ok: true }));
    return true;
  }
  if (msg.type === "ASK_AI") {
    responderPreguntaConClaude(msg.pregunta, msg.perfil)
      .then(respuesta => sendResponse({ respuesta }))
      .catch(() => sendResponse({ respuesta: null }));
    return true;
  }
});

// ── mensajes externos (desde la web de PostulAI via externally_connectable) ───

chrome.runtime.onMessageExternal.addListener((msg, sender, sendResponse) => {
  if (msg.type === "GET_STATS") {
    // Permite a la web detectar la extensión y mostrar stats en el dashboard
    getStats().then(stats => sendResponse({ ok: true, ...stats }));
    return true;
  }

  if (msg.type === "POSTULAI_LOGIN") {
    // El frontend envía el perfil y token al hacer login
    const { profile, apiUrl, apiToken, userId } = msg.payload || {};
    const saves = {};
    if (profile)  saves.profile  = profile;
    if (apiUrl)   saves.apiUrl   = apiUrl;
    if (apiToken) saves.apiToken = apiToken;
    if (userId)   saves.userId   = userId;

    chrome.storage.local.set(saves, () => {
      if (apiUrl)   chrome.storage.sync.set({ apiUrl });
      if (apiToken) chrome.storage.sync.set({ apiToken });
      if (userId)   chrome.storage.sync.set({ userId });
      // Modo automático activado por defecto al primer login
      chrome.storage.sync.get(["enabled", "autopilot"], prefs => {
        const defaults = {};
        if (prefs.enabled === undefined)   defaults.enabled   = true;
        if (prefs.autopilot === undefined) defaults.autopilot = true;
        chrome.storage.sync.set(defaults, () => {
          syncAppliedIds().then(() => {
            sendResponse({ ok: true });
            maybeRunDailyAutopilot();
          });
        });
      });
    });
    return true;
  }

  if (msg.type === "POSTULAI_LOGOUT") {
    chrome.storage.local.clear(() => sendResponse({ ok: true }));
    chrome.storage.sync.remove(["apiUrl", "apiToken", "userId"]);
    return true;
  }
});

// ── guardar postulación ───────────────────────────────────────────────────────

async function handleApplication({ jobId, title, company, url, cargo }) {
  const { apiUrl, apiToken, userId } = await getConfig();

  // Contador diario local
  const today  = new Date().toISOString().slice(0, 10);
  const stored = await chrome.storage.local.get(["dailyCount", "dailyDate", "appliedIds"]);
  const count  = stored.dailyDate === today ? (stored.dailyCount || 0) : 0;

  // Agregar al cache local de IDs aplicados
  const appliedIds = stored.appliedIds || [];
  if (jobId && !appliedIds.includes(jobId)) appliedIds.push(jobId);
  await chrome.storage.local.set({ dailyCount: count + 1, dailyDate: today, appliedIds });

  // Enviar al backend (DWH.EMPLEOS, mismo formato que el scraper)
  if (apiUrl && apiToken && userId) {
    try {
      const r = await fetch(`${apiUrl}/api/applications`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          user_id:       userId,
          id_empleo:     jobId || "",
          titulo_empleo: title,
          empresa:       company,
          link:          url,
          cargo:         cargo || "",
          portal:        "linkedin",
        }),
      });
      if (!r.ok) console.warn("[PostulAI] Backend rechazó la postulación:", r.status);
    } catch (e) {
      console.warn("[PostulAI] Error guardando postulación:", e.message);
    }
  }

  // Notificación
  chrome.notifications.create({
    type:    "basic",
    iconUrl: "icons/icon48.png",
    title:   "PostulAI — Postulación enviada",
    message: `${title}${company ? " en " + company : ""}`,
  });
}

// ── estadísticas ──────────────────────────────────────────────────────────────

async function getStats() {
  const today  = new Date().toISOString().slice(0, 10);
  const stored = await chrome.storage.local.get(["dailyCount", "dailyDate", "profile"]);
  const count  = stored.dailyDate === today ? (stored.dailyCount || 0) : 0;
  const name   = stored.profile?.nombre || stored.profile?.NOMBRE || "";
  return { dailyCount: count, name };
}

// ── helpers ───────────────────────────────────────────────────────────────────

function getConfig() {
  return new Promise(r =>
    chrome.storage.sync.get(["apiUrl", "apiToken", "userId"], r)
  );
}

async function responderPreguntaConClaude(pregunta, perfil) {
  const { apiUrl, apiToken } = await getConfig();
  if (!apiUrl || !apiToken) return null;
  try {
    const r = await fetch(`${apiUrl}/api/ai/responder-pregunta`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${apiToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ pregunta, perfil }),
    });
    if (!r.ok) return null;
    const data = await r.json();
    return data.respuesta || null;
  } catch (_) {
    return null;
  }
}

// ── arranque ──────────────────────────────────────────────────────────────────

syncProfile();
syncAppliedIds();
