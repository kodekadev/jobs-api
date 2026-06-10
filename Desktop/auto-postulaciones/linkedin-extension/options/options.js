const $ = id => document.getElementById(id);

// Cargar config guardada
chrome.storage.sync.get(["userId", "apiUrl", "apiToken"], cfg => {
  if (cfg.userId)   $("userId").value   = cfg.userId;
  if (cfg.apiUrl)   $("apiUrl").value   = cfg.apiUrl;
  if (cfg.apiToken) $("apiToken").value = cfg.apiToken;
});

function showMsg(text, type) {
  const el = $("msg");
  el.textContent = text;
  el.className = `msg ${type}`;
  el.style.display = "block";
  setTimeout(() => { el.style.display = "none"; }, 4000);
}

$("btnSave").addEventListener("click", async () => {
  const userId   = $("userId").value.trim();
  const apiUrl   = $("apiUrl").value.trim().replace(/\/$/, "");
  const apiToken = $("apiToken").value.trim();

  if (!userId || !apiUrl || !apiToken) {
    showMsg("Completa todos los campos.", "error");
    return;
  }

  // Verificar conexión
  $("btnSave").textContent = "Verificando...";
  try {
    // Ruta correcta: GET /api/profile/:id
    const r = await fetch(`${apiUrl}/api/profile/${userId}`, {
      headers: { Authorization: `Bearer ${apiToken}` },
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();

    // Normalizar respuesta: { usuario, perfil, postula_facil, ... }
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

    // Guardar config y perfil
    await chrome.storage.sync.set({ userId, apiUrl, apiToken, enabled: true });
    await chrome.storage.local.set({ profile });

    const name = profile.nombre || userId;
    showMsg(`Conectado como ${name}. Perfil sincronizado.`, "ok");

  } catch (e) {
    showMsg(`Error al conectar: ${e.message}`, "error");
  } finally {
    $("btnSave").textContent = "Guardar y conectar";
  }
});
