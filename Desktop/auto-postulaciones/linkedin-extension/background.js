/**
 * background.js — Service Worker de la extensión PostulAI.
 */

const SYNC_ALARM   = "postulai-sync";
const SYNC_MINUTES = 30;

// ── alarma de sincronización ──────────────────────────────────────────────────

chrome.alarms.create(SYNC_ALARM, { periodInMinutes: SYNC_MINUTES });

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === SYNC_ALARM) syncProfile();
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
    const profile = {
      id:          data.usuario?.id       || userId,
      nombre:      data.usuario?.nombre   || '',
      email:       data.usuario?.email    || '',
      celular:     data.usuario?.celular  || '',
      profesion:   data.perfil?.profesion || '',
      cv_url:      data.postula_facil?.cv_url || data.perfil?.cv_url || '',
      cargos:      data.postula_facil?.cargos      || [],
      experiencia: data.postula_facil?.experiencia || '',
      resumen:     data.postula_facil?.resumen      || '',
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
});

// ── mensajes externos (desde la web de PostulAI via externally_connectable) ───

chrome.runtime.onMessageExternal.addListener((msg, sender, sendResponse) => {
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
      syncAppliedIds().then(() => sendResponse({ ok: true }));
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
  if (url && !appliedIds.includes(url)) appliedIds.push(url);
  await chrome.storage.local.set({ dailyCount: count + 1, dailyDate: today, appliedIds });

  // Enviar al backend
  if (apiUrl && apiToken && userId) {
    try {
      await fetch(`${apiUrl}/api/applications`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          user_id:           userId,
          titulo_empleo:     title,
          empresa:           company,
          link:              url,
          cargo:             cargo || '',
          portal:            "linkedin",
          fecha_postulacion: new Date().toISOString(),
        }),
      });
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

// ── arranque ──────────────────────────────────────────────────────────────────

syncProfile();
syncAppliedIds();
