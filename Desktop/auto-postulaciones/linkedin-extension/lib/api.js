/**
 * api.js — Cliente HTTP al backend de AplicAI.
 *
 * Todos los métodos leen la API_URL y el token de chrome.storage.
 * Si no hay token configurado, retornan null sin lanzar error.
 */

const API = {

  async _cfg() {
    return new Promise(resolve => {
      chrome.storage.sync.get(["apiUrl", "apiToken", "userId"], resolve);
    });
  },

  async _get(path) {
    const { apiUrl, apiToken } = await this._cfg();
    if (!apiUrl || !apiToken) return null;
    try {
      const r = await fetch(`${apiUrl}${path}`, {
        headers: { Authorization: `Bearer ${apiToken}` },
      });
      if (!r.ok) return null;
      return r.json();
    } catch {
      return null;
    }
  },

  async _post(path, body) {
    const { apiUrl, apiToken } = await this._cfg();
    if (!apiUrl || !apiToken) return null;
    try {
      const r = await fetch(`${apiUrl}${path}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      if (!r.ok) return null;
      return r.json();
    } catch {
      return null;
    }
  },

  /** Trae el perfil del usuario desde AplicAI */
  async getProfile() {
    const { userId } = await this._cfg();
    if (!userId) return null;
    return this._get(`/api/users/${userId}/profile`);
  },

  /** Registra una postulación realizada */
  async saveApplication({ jobId, title, company, url, portal = "linkedin" }) {
    const { userId } = await this._cfg();
    if (!userId) return null;
    return this._post("/api/applications", {
      user_id: userId,
      id_empleo: jobId,
      titulo_empleo: title,
      empresa: company,
      link: url,
      portal,
      fecha_postulacion: new Date().toISOString(),
    });
  },

  /** Trae los IDs de empleos ya aplicados (para deduplicar) */
  async getAppliedIds() {
    const { userId } = await this._cfg();
    if (!userId) return [];
    const data = await this._get(`/api/users/${userId}/applied-ids`);
    return data?.ids ?? [];
  },

  /** Verifica si el token es válido */
  async ping() {
    return this._get("/api/ping");
  },
};
