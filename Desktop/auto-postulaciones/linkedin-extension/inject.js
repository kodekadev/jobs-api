// inject.js — se inyecta en páginas de AplicAI
// Dispara un evento con el ID real de la extensión para que el dashboard
// pueda comunicarse sin necesitar NEXT_PUBLIC_EXTENSION_ID hardcodeado
window.dispatchEvent(new CustomEvent("aplicai:ready", {
  detail: { extensionId: chrome.runtime.id }
}));
