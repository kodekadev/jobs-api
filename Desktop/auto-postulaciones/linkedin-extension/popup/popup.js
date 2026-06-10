const $ = id => document.getElementById(id);

async function init() {
  // Cargar config
  const cfg = await new Promise(r =>
    chrome.storage.sync.get(["enabled", "autopilot"], r)
  );
  $("toggleEnabled").checked   = !!cfg.enabled;
  $("toggleAutopilot").checked = !!cfg.autopilot;

  // Stats del día
  chrome.runtime.sendMessage({ type: "GET_STATS" }, stats => {
    $("dailyCount").textContent = stats?.dailyCount ?? 0;
    if (stats?.name) $("userName").textContent = stats.name.split(" ")[0];
  });

  // Estado de conexión
  const profile = await new Promise(r =>
    chrome.storage.local.get(["profile"], d => r(d.profile))
  );
  const dot  = $("statusDot");
  const text = $("statusText");
  if (profile) {
    dot.className  = "status-dot connected";
    text.textContent = "Conectado a PostulAI";
  } else {
    dot.className  = "status-dot error";
    text.textContent = "Sin perfil — configura primero";
  }
}

// Guardar toggles
$("toggleEnabled").addEventListener("change", e => {
  chrome.storage.sync.set({ enabled: e.target.checked });
});
$("toggleAutopilot").addEventListener("change", e => {
  chrome.storage.sync.set({ autopilot: e.target.checked });
});

// Aplicar al empleo actual
$("btnApplyNow").addEventListener("click", async () => {
  $("btnApplyNow").textContent = "Aplicando...";
  $("btnApplyNow").disabled = true;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  chrome.tabs.sendMessage(tab.id, { type: "APPLY_NOW" });
  setTimeout(() => {
    $("btnApplyNow").textContent = "Aplicar al empleo actual";
    $("btnApplyNow").disabled = false;
  }, 3000);
});

// Sincronizar perfil
$("btnSync").addEventListener("click", () => {
  $("btnSync").textContent = "Sincronizando...";
  chrome.runtime.sendMessage({ type: "SYNC_NOW" }, () => {
    $("btnSync").textContent = "Sincronizar perfil";
    init();
  });
});

// Abrir opciones
$("linkOptions").addEventListener("click", e => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});

init();
