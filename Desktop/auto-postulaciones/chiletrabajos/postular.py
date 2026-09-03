"""
ChileTrabajos — Postulaciones automáticas con Playwright.

Flujo:
  1. Obtiene sesión Playwright desde cookies guardadas en BigQuery
  2. Busca empleos en /encuentra-un-empleo por cargo y ubicación
  3. Por cada empleo: navega, click Postular, responde preguntas, envía
  4. Guarda cada postulación en BigQuery EMPLEOS
"""
import os
import sys
import re
import time
import json
import unicodedata

def _safe(s: str) -> str:
    """Convierte el string a algo imprimible en la consola actual (reemplaza chars inválidos)."""
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    return s.encode(enc, errors='replace').decode(enc)
import datetime

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bq
from emailer import extract_email, send_application
from portal_accounts import (
    get_chiletrabajos_pw_session,
    close_chiletrabajos_pw_session,
    _standard_answer,
    _llm_answer_questions,
    _extract_cv_text,
    _save_answers_to_cache,
    job_aplica_al_usuario,
)

BASE_URL  = "https://www.chiletrabajos.cl"
PORTAL_ID = "chiletrabajos"

_NUMERIC_KWS = {"PRETENSION", "SUELDO", "RENTA", "SALARIO", "ANOS DE EXP",
                "AÑOS DE EXP", "EXPERIENCIA", "CUANTOS ANOS"}

# Comunas de la Región Metropolitana → seleccionar "Santiago" (value 1022) en ChileTrabajos
_CHT_SANTIAGO_COMUNAS = {
    "santiago", "providencia", "nunoa", "nuñoa", "nuno", "las condes", "vitacura",
    "la florida", "maipu", "maipo", "puente alto", "pudahuel", "quilicura", "renca",
    "cerrillos", "estacion central", "estacion central", "san miguel", "la cisterna",
    "el bosque", "la granja", "la pintana", "lo espejo", "pedro aguirre cerda",
    "san ramon", "cerro navia", "lo prado", "quinta normal", "recoleta", "independencia",
    "conchalí", "conchali", "huechuraba", "lo barnechea", "peñalolen", "penalolen",
    "macul", "san joaquin", "la reina", "peñaflor", "penaflor", "buin", "calera de tango",
    "colina", "lampa", "talagante", "isla de maipo", "el monte", "melipilla",
    "padre hurtado", "paine", "pirque", "san bernardo", "til til",
}

# Mapa de ciudades fuera de RM a value del select de ChileTrabajos
_CHT_CIUDAD_VALUE = {
    "valparaiso":     "1014",
    "valparaíso":     "1014",
    "vina del mar":   "1014",
    "viña del mar":   "1014",
    "quilpue":        "1021",
    "quilpué":        "1021",
    "concepcion":     "1035",
    "concepción":     "1035",
    "talcahuano":     "1035",
    "temuco":         "1039",
    "rancagua":       "1028",
    "talca":          "1031",
    "antofagasta":    "1004",
    "iquique":        "1002",
    "la serena":      "1010",
    "coquimbo":       "1011",
    "puerto montt":   "1043",
    "osorno":         "1044",
    "arica":          "1000",
    "calama":         "1006",
    "chillan":        "1036",
    "chillán":        "1036",
    "los angeles":    "1037",
    "los ángeles":    "1037",
    "punta arenas":   "1051",
    "coyhaique":      "1047",
}

# Ciudades que indican trabajo fuera de la RM (o fuera de Chile), para filtrar por título
_OFFSITE_CITY_KEYWORDS = {
    "puerto montt", "puerto-montt", "valdivia", "temuco", "antofagasta",
    "arica", "iquique", "copiapo", "copiapó", "la serena", "coquimbo",
    "rancagua", "talca", "chillan", "chillán", "puerto varas", "osorno",
    "coyhaique", "punta arenas", "calama", "ovalle", "curico", "curicó",
    "linares", "los angeles", "angol", "curacavi", "curacaví",
    "eeuu", "ee.uu", "estados unidos", "exterior", "internacional", "remoto exterior",
}


def _norm(texto: str) -> str:
    t = (texto or "").upper().strip().replace("?", "").replace("¿", "")
    nfkd = unicodedata.normalize("NFKD", t)
    return unicodedata.normalize("NFKC", nfkd.translate({0x0301: None, 0x0308: None}))


_GET_LABEL_JS = """(el) => {
    function isGeneric(t) {
        return /^(pregunta|question|respuesta|answer|campo|field)\\s*\\d*$/i.test(t.trim());
    }
    var al = el.getAttribute('aria-label');
    if (al && al.trim().length > 3 && !isGeneric(al)) return al.trim();
    if (el.id) {
        var lbl = document.querySelector('label[for="' + el.id + '"]');
        if (lbl && lbl.innerText && lbl.innerText.trim().length > 3) return lbl.innerText.trim();
    }
    var node = el.parentElement;
    for (var i = 0; i < 10; i++) {
        if (!node) break;
        for (var j = 0; j < node.childNodes.length; j++) {
            var c = node.childNodes[j];
            var tag = (c.tagName || '').toUpperCase();
            if (['P','LABEL','SPAN','H3','H4','H5','STRONG','B','LI'].indexOf(tag) >= 0) {
                var txt = (c.innerText || c.textContent || '').trim();
                if (txt.length > 5 && !c.contains(el) && !isGeneric(txt)) return txt;
            }
            if (c.nodeType === 3) {
                var t3 = c.textContent.trim();
                if (t3.length > 5 && !isGeneric(t3)) return t3;
            }
        }
        node = node.parentElement;
    }
    var ph = el.getAttribute('placeholder');
    if (ph && ph.trim().length > 10 && !isGeneric(ph)) return ph.trim();
    return '';
}"""

_SET_VALUE_JS = """(el, v) => {
    var tag = el.tagName;
    var proto = (tag === 'TEXTAREA')
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
    var s = Object.getOwnPropertyDescriptor(proto, 'value');
    if (s) s.set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    el.dispatchEvent(new FocusEvent('blur', {bubbles: true}));
}"""


def _responder_preguntas_cht_pw(page, user: dict, job_title: str = "") -> None:
    """Responde formulario de preguntas de postulación en ChileTrabajos."""
    _EXCL = {"hidden", "radio", "checkbox", "submit", "button", "file", "image", "reset"}

    def _is_numeric(item: dict) -> bool:
        if item.get("type") == "number":
            return True
        if item.get("inputmode") in ("numeric", "decimal"):
            return True
        lbl = _norm((item.get("label") or "") + " " + (item.get("placeholder") or ""))
        return any(k in lbl for k in _NUMERIC_KWS)

    # ── Radios: preferir "Sí" ─────────────────────────────────────────────────
    radios = page.locator("input[type='radio']:visible").all()
    grupos: dict = {}
    for r in radios:
        try:
            name = r.get_attribute("name") or r.get_attribute("id") or str(id(r))
            grupos.setdefault(name, []).append(r)
        except Exception:
            pass

    for name, grupo in grupos.items():
        try:
            elegido = grupo[0]
            for r in grupo:
                try:
                    lbl = _norm(r.evaluate(_GET_LABEL_JS) or r.get_attribute("value") or "")
                    if any(k in lbl for k in ("SI", "YES", "SÍ", "TRUE")):
                        elegido = r
                        break
                except Exception:
                    pass
            elegido.evaluate(
                "(el) => { el.checked=true; el.dispatchEvent(new Event('change',{bubbles:true})); }"
            )
        except Exception:
            pass

    # ── Inputs y textareas vacíos ─────────────────────────────────────────────
    pending = []
    for inp in page.locator("input:visible").all():
        try:
            inp_type = (inp.get_attribute("type") or "text").lower()
            if (not inp.is_disabled() and inp_type not in _EXCL
                    and not (inp.input_value() or "").strip()):
                pending.append({
                    "el": inp, "kind": "input",
                    "label": inp.evaluate(_GET_LABEL_JS),
                    "type": inp_type,
                    "inputmode": (inp.get_attribute("inputmode") or "").lower(),
                    "placeholder": inp.get_attribute("placeholder") or "",
                })
        except Exception:
            pass

    for ta in page.locator("textarea:visible").all():
        try:
            if not (ta.input_value() or "").strip():
                pending.append({
                    "el": ta, "kind": "textarea",
                    "label": ta.evaluate(_GET_LABEL_JS),
                    "type": "textarea", "inputmode": "",
                    "placeholder": ta.get_attribute("placeholder") or "",
                })
        except Exception:
            pass

    # ── Respuestas del perfil + Claude ────────────────────────────────────────
    answers: dict = {}
    for idx, item in enumerate(pending):
        resp = _standard_answer(item, user)
        if resp is not None:
            answers[idx] = (resp, "perfil")

    sin_resp = [item for idx, item in enumerate(pending) if idx not in answers]
    if sin_resp:
        cv_url  = user.get("CV_URL") or user.get("cv_url") or ""
        cv_text = _extract_cv_text(cv_url) if cv_url else ""
        llm_raw = _llm_answer_questions(sin_resp, user, cv_text=cv_text, job_title=job_title)
        _save_answers_to_cache(sin_resp, llm_raw)
        sin_idx = [i for i in range(len(pending)) if i not in answers]
        for li, gi in enumerate(sin_idx):
            resp = llm_raw.get(str(li), "")
            if resp:
                answers[gi] = (resp, "Claude")

    def _fallback(it: dict) -> str:
        if _is_numeric(it):
            return re.sub(r"[^\d]", "", str(user.get("pretension_general") or "")) \
                   or str(user.get("experiencia") or "5")
        if it.get("type") == "textarea":
            rv = str(user.get("resumen") or "")
            return rv[:400] if rv else f"Profesional con {user.get('experiencia','5')} años de experiencia."
        return ""

    for idx, item in enumerate(pending):
        try:
            el   = item["el"]
            resp, source = answers.get(idx, (_fallback(item), "fallback"))
            if _is_numeric(item):
                resp = re.sub(r"[^\d]", "", resp) or resp
            print(f"    [preg/{source}] [{idx}] '{_safe((item['label'] or item['type'])[:40])}' -> '{_safe(resp[:40])}'")

            el.evaluate(_SET_VALUE_JS, resp)
        except Exception:
            pass

    print(f"    [cht] Preguntas: {len(grupos)} radios, {len(pending)} inputs")


_DESC_END_MARKERS = [
    "beneficios", "comparte por redes", "estadísticas del anuncio",
    "estadisticas del anuncio", "trabajos relacionados", "compartir enlace",
    "denunciar oferta", "ofertas relacionadas",
]


def _extraer_descripcion_cht(page) -> str:
    """Extrae el texto de descripción del empleo desde la página de detalle."""
    # 1) p.mb-0 más largo — estructura confirmada de ChileTrabajos
    try:
        best = ""
        for el in page.locator("p.mb-0").all():
            try:
                txt = (el.inner_text() or "").strip()
                if len(txt) > len(best):
                    best = txt
            except Exception:
                pass
        if len(best) > 50:
            return _recortar_descripcion(best)
    except Exception:
        pass

    # 2) Sección entre "Descripción oferta de trabajo" y marcadores de fin
    try:
        txt = page.evaluate("""() => {
            const body = document.body.innerText || '';
            const lower = body.toLowerCase();
            const startMarkers = ['descripción oferta de trabajo', 'descripcion oferta de trabajo'];
            let start = -1;
            for (const m of startMarkers) {
                const idx = lower.indexOf(m);
                if (idx !== -1) { start = idx + m.length; break; }
            }
            if (start === -1) return '';
            const endMarkers = [
                'beneficios', 'comparte por redes', 'estadísticas del anuncio',
                'estadisticas del anuncio', 'trabajos relacionados', 'compartir enlace'
            ];
            let end = body.length;
            for (const m of endMarkers) {
                const idx = lower.indexOf(m, start);
                if (idx !== -1 && idx < end) end = idx;
            }
            return body.slice(start, end).trim();
        }""") or ""
        if len(txt) > 50:
            return txt[:5000]
    except Exception:
        pass

    # 3) Selectores CSS genéricos
    for sel in [
        "[class*='descripcion-oferta']", "[class*='descripcion-empleo']",
        "#descripcion", "[class*='descripcion']", ".detalle-oferta",
        "#detalle-oferta", ".job-description",
    ]:
        try:
            el = page.locator(sel).first
            if el.count():
                txt = (el.inner_text() or "").strip()
                if len(txt) > 50:
                    return _recortar_descripcion(txt)
        except Exception:
            continue

    return ""


def _recortar_descripcion(txt: str) -> str:
    """Corta la descripción en el primer marcador de sección no relevante."""
    lower = txt.lower()
    cut = len(txt)
    for marker in _DESC_END_MARKERS:
        idx = lower.find(marker)
        if idx != -1 and idx < cut:
            cut = idx
    return txt[:cut].strip()[:5000]


def _extraer_salario_cht(page) -> "int | None":
    """Extrae el salario de la página de detalle. Retorna None si no aparece."""
    try:
        raw = page.evaluate("""() => {
            const rows = document.querySelectorAll('tr, .info-row, dl dt, dl dd');
            let isNext = false;
            for (const el of rows) {
                const t = (el.innerText || '').trim();
                if (isNext) {
                    const n = t.replace(/[.\\s]/g, '').replace(/[^\\d]/g, '');
                    return n.length >= 5 ? n : null;
                }
                if (/^salario$/i.test(t) || /^sueldo$/i.test(t)) isNext = true;
            }
            // Intentar buscar en celdas de tabla: par clave/valor
            for (const td of document.querySelectorAll('td')) {
                if (/salario|sueldo/i.test(td.innerText || '')) {
                    const next = td.nextElementSibling;
                    if (next) {
                        const n = (next.innerText || '').replace(/[.\\s]/g, '').replace(/[^\\d]/g, '');
                        if (n.length >= 5) return n;
                    }
                }
            }
            return null;
        }""")
        return int(raw) if raw else None
    except Exception:
        return None


def _postular_empleo_pw(page, job_url: str, user: dict, titulo: str) -> "dict | bool":
    """Navega al empleo y postula vía Playwright. Retorna dict con ok y descripcion, o False."""
    try:
        page.goto(job_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)

        if "chtlogin" in page.url:
            print(f"    [cht] Redirigido a login — sesión expirada")
            return False

        content = page.content().lower()
        if any(s in content for s in [
            "ya postulaste", "ya te postulaste", "postulado anteriormente",
            "usted ya ha postulado", "ya ha postulado", "postular de nuevo",
            "ya aplicaste", "already applied",
        ]):
            print(f"    [cht] Ya postulado — skip")
            return {"ok": True, "ya_postulado": True, "descripcion": ""}

        # Extraer empresa desde campo "Buscado" y verificar empresas excluidas
        empresa = ""
        try:
            empresa = page.evaluate("""() => {
                const rows = document.querySelectorAll('tr, .info-row');
                for (const row of rows) {
                    const cells = row.querySelectorAll('td, th, dt, dd');
                    for (let i = 0; i < cells.length - 1; i++) {
                        if (/buscado/i.test(cells[i].innerText || '')) {
                            return (cells[i+1].innerText || '').trim();
                        }
                    }
                }
                return '';
            }""") or ""
        except Exception:
            pass
        if empresa:
            aplica, motivo = job_aplica_al_usuario(titulo, empresa, user)
            if not aplica:
                print(f"    [cht] SALTADO ({motivo}): empresa '{empresa}'")
                return False

        descripcion = _extraer_descripcion_cht(page)

        # Filtro salario: saltar si la oferta paga menos de la pretensión del usuario
        salario = _extraer_salario_cht(page)
        pretension = None
        try:
            raw_p = str(user.get("PRETENSION_GENERAL") or user.get("pretension_general") or "")
            cleaned = re.sub(r"[^\d]", "", raw_p)
            pretension = int(cleaned) if cleaned else None
        except Exception:
            pass
        if salario and pretension and salario < pretension:
            print(f"    [cht] Salario {salario:,} < pretensión {pretension:,} — skip")
            return {"skip": "salario"}

        # Click "Postular"
        postular_selectors = [
            "xpath=//a[contains(translate(.,'POSTULAR','postular'),'postular') and not(contains(.,'de nuevo'))]",
            "xpath=//*[@id='detalle-oferta']//a[contains(@href,'postular')]",
            "xpath=//a[contains(@href,'postular')]",
        ]
        clicked = False
        for sel in postular_selectors:
            for el in page.locator(sel).all():
                try:
                    if el.is_visible():
                        txt = ""
                        try:
                            txt = el.inner_text()[:40]
                        except Exception:
                            pass
                        el.click()
                        print(f"    [cht] Click postular: '{_safe(txt)}'")
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                break

        if not clicked:
            print(f"    [cht] Sin botón postular en {job_url[:60]}")
            return False

        try:
            page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(2000)

        if "chtlogin" in page.url:
            print(f"    [cht] Redirigido a login tras click")
            return False

        # Comprobar si el click de "Postular" ya fue suficiente (aplicación directa)
        content_after = page.content().lower()
        if any(s in content_after for s in [
            "postulación enviada", "postulacion enviada", "gracias por postular",
            "aplicación enviada", "te has postulado", "ya te has postulado",
            "exitosamente", "hemos recibido tu postulación",
        ]):
            print(f"    [cht] Postulado directamente (sin form adicional)")
            if not descripcion:
                descripcion = _extraer_descripcion_cht(page)
            return {"ok": True, "descripcion": descripcion, "empresa": empresa}

        # Update description
        if not descripcion:
            descripcion = _extraer_descripcion_cht(page)

        # Formulario de preguntas — selector ampliado
        form_loc = page.locator(
            "form[id*='postular'], form:has(input.enviar-postulacion), form:has([type='submit']), form:has(button[type='submit'])"
        )
        if form_loc.count() > 0:
            try:
                if form_loc.first.is_visible():
                    _responder_preguntas_cht_pw(page, user, job_title=titulo)
                    page.wait_for_timeout(1500)
            except Exception:
                pass

        # Submit — selectors ampliados + forzar click si disabled
        submit_selectors = [
            "xpath=//input[contains(@class,'enviar-postulacion')]",
            "xpath=//input[@value='Enviar postulación']",
            "xpath=//input[@value='Enviar postulacion']",
            "xpath=//input[@value='Postular']",
            "xpath=//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'enviar')]",
            "xpath=//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'postular')]",
            "xpath=//input[@type='submit']",
            "xpath=//button[@type='submit']",
            "xpath=//a[contains(@class,'postular') or contains(@class,'enviar')]",
        ]
        enviado = ""
        for sel in submit_selectors:
            for el in page.locator(sel).all():
                try:
                    if el.is_visible():
                        try:
                            enviado = el.get_attribute("value") or el.inner_text() or "submit"
                        except Exception:
                            enviado = "submit"
                        # Intentar click normal; si falla, forzar con JS
                        try:
                            el.click(timeout=3000)
                        except Exception:
                            try:
                                el.evaluate("el => el.click()")
                            except Exception:
                                pass
                        print(f"    [cht] Submit: '{_safe(enviado[:30])}'")

                        break
                except Exception:
                    continue
            if enviado:
                break

        if not enviado:
            print(f"    [cht] Sin botón submit — URL: {page.url[:80]}")
            return False

        page.wait_for_timeout(4000)
        content = page.content().lower()
        confirmed = any(s in content for s in [
            "postulación enviada", "postulacion enviada", "gracias por postular",
            "aplicación enviada", "te has postulado", "exitoso", "hemos recibido",
        ])
        error = any(s in content for s in [
            "ha ocurrido un error", "error al postular", "no pudimos", "inténtalo de nuevo",
        ])
        ok = confirmed or (not error)
        print(f"    [cht] {'OK Postulado' if confirmed else ('Error en envio' if error else 'Enviado sin confirmar')}")
        if not ok:
            return False
        return {"ok": True, "descripcion": descripcion, "empresa": empresa}

    except Exception as e:
        print(f"    [cht] Error postular: {e}")
        return False


def postular_empleos_cht(user_id: str, user: dict, max_count: int = 999) -> int:
    """
    Busca y postula empleos en ChileTrabajos para el usuario.

    Args:
        user_id : ID del usuario en BigQuery
        user    : dict con datos del perfil (cargos, ubicaciones, pretension, cv_url, etc.)

    Returns:
        Número de postulaciones guardadas.
    """
    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if not cuenta:
        print(f"[cht] Sin cuenta para {user_id} — ejecuta crear_cuenta primero")
        return 0

    email    = cuenta["email"]
    password = cuenta["password"]

    cargos = user.get("CARGOS") or user.get("cargos") or []
    if isinstance(cargos, str):
        try:
            cargos = json.loads(cargos)
        except Exception:
            cargos = [cargos]

    ubicaciones = user.get("UBICACIONES") or user.get("ubicaciones") or ["Santiago"]
    if isinstance(ubicaciones, str):
        try:
            ubicaciones = json.loads(ubicaciones)
        except Exception:
            ubicaciones = [ubicaciones]

    page = get_chiletrabajos_pw_session(user_id, email, password)
    if not page:
        print(f"[cht] Sin sesión Playwright para {user_id} — cookies expiradas o faltantes")
        return 0

    count = 0
    modo_revision = False
    pending_jobs: list = []
    stats = {"encontrados": 0, "ya_aplicado": 0, "ciudad_offsite": 0,
             "no_aplica": 0, "postulados": 0, "pendientes": 0, "ya_postulado_portal": 0,
             "salario": 0, "error": 0}
    try:
        _ident = user.get("EMAIL") or user.get("NOMBRE") or user_id
        print(f"[cht] Iniciando postulaciones para {_ident}")
        applied_ids = bq.get_applied_job_ids(user_id)
        modo_revision = bq.get_modo_revision(user_id)
        print(f"[cht] Modo: {'REVISIÓN (guardará para aprobar)' if modo_revision else 'AUTOPILOT (postula directo)'}")

        for cargo in cargos:
            for ubicacion in ubicaciones:
                print(f"[cht] Buscando: '{cargo}' en '{ubicacion}'")

                page.goto(f"{BASE_URL}/encuentra-un-empleo", wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                # Campo de búsqueda
                try:
                    page.wait_for_selector("#trabajo", timeout=8000)
                    page.fill("#trabajo", cargo)
                except Exception:
                    print(f"[cht] ! Sin campo de búsqueda")
                    continue

                # Seleccionar ciudad en el select de ChileTrabajos
                _ub_key = unicodedata.normalize("NFKD", ubicacion.lower())
                _ub_key = "".join(ch for ch in _ub_key if not unicodedata.combining(ch))
                if _ub_key in _CHT_SANTIAGO_COMUNAS:
                    _cht_value = "1022"  # Santiago
                else:
                    _cht_value = _CHT_CIUDAD_VALUE.get(_ub_key, "1022")
                try:
                    sel = page.locator("select[name='13']").first
                    if sel.count() == 0:
                        sel = page.locator("select").first
                    sel.select_option(value=_cht_value)
                    _sel_label = sel.locator(f"option[value='{_cht_value}']").first.inner_text()
                    print(f"[cht] Ciudad seleccionada: '{_sel_label.strip()}' ({_cht_value})")
                except Exception:
                    pass

                # Submit búsqueda
                try:
                    page.locator(
                        "button[type='submit'], input[type='submit']"
                    ).first.click()
                    page.wait_for_timeout(4000)
                except Exception:
                    pass

                # Recopilar links de empleos
                jobs: list = []
                seen: set = set()
                for sel_css in [
                    "h2 a", "h3 a", ".job-item h2 a", ".job-item h3 a",
                    "a.font-weight-bold", "a[href*='/trabajo/']", "a[href*='/empleo/']",
                ]:
                    for a in page.locator(sel_css).all():
                        try:
                            href  = a.get_attribute("href") or ""
                            title = a.inner_text().strip()
                            if (href and title and href not in seen
                                    and any(k in href for k in ["/trabajo/", "/empleo/"])
                                    and not any(k in href for k in [
                                        "/ciudad/", "/empresa/", "/encuentra-", "/categoria/"
                                    ])
                                    and len(title) > 5):
                                jobs.append({"titulo": title, "link": href})
                                seen.add(href)
                        except Exception:
                            continue

                n_encontrados = len(jobs)
                print(f"[cht] Empleos encontrados: {n_encontrados}")
                stats["encontrados"] += n_encontrados
                lc = {"encontrados": n_encontrados, "ya_aplicado": 0, "ciudad_offsite": 0,
                      "no_aplica": 0, "salario": 0, "pendientes": 0, "postulados": 0,
                      "ya_portal": 0, "error": 0}

                # Ciudades válidas para este usuario (normalizadas sin acentos)
                _user_cities_norm = set()
                for _ub in ubicaciones:
                    _n = unicodedata.normalize("NFKD", _ub.lower())
                    _n = "".join(ch for ch in _n if not unicodedata.combining(ch))
                    _user_cities_norm.add(_n)

                for j, job in enumerate(jobs[:20]):
                    job_id = job["link"].split("/")[-1].split("?")[0]
                    if job_id in applied_ids:
                        print(f"[cht] {j+1}/{len(jobs)} Ya aplicado — skip")
                        stats["ya_aplicado"] += 1; lc["ya_aplicado"] += 1
                        continue

                    titulo = job["titulo"]
                    titulo_norm = unicodedata.normalize("NFKD", titulo.lower())
                    titulo_norm = "".join(ch for ch in titulo_norm if not unicodedata.combining(ch))

                    # Filtro: ciudad fuera del target mencionada en el título
                    ciudad_offsita = next(
                        (c for c in _OFFSITE_CITY_KEYWORDS if c in titulo_norm), None
                    )
                    if ciudad_offsita and not any(c in titulo_norm for c in _user_cities_norm):
                        print(f"[cht] {j+1}/{len(jobs)} SALTADO (ciudad '{ciudad_offsita}'): '{_safe(titulo[:40])}'")
                        stats["ciudad_offsite"] += 1; lc["ciudad_offsite"] += 1
                        continue

                    aplica, motivo = job_aplica_al_usuario(titulo, "", user)
                    if not aplica:
                        print(f"[cht] {j+1}/{len(jobs)} SALTADO ({motivo}): '{_safe(titulo[:40])}'")
                        stats["no_aplica"] += 1; lc["no_aplica"] += 1
                        continue

                    if modo_revision:
                        pending_jobs.append({"titulo": titulo, "link": job["link"], "empresa": ""})
                        print(f"[cht] {j+1}/{len(jobs)} PENDIENTE revisión: '{_safe(titulo[:40])}'")
                        stats["pendientes"] += 1; lc["pendientes"] += 1
                        continue

                    print(f"[cht] {j+1}/{len(jobs)} {_safe(titulo[:50])}")

                    # Postular con reconexión en TargetClosedError
                    ok = False
                    try:
                        ok = _postular_empleo_pw(page, job["link"], user, titulo)
                    except Exception as e:
                        if "TargetClosedError" in type(e).__name__ or "closed" in str(e).lower():
                            print(f"[cht] Browser cerrado — reconectando...")
                            try:
                                close_chiletrabajos_pw_session(user_id)
                            except Exception:
                                pass
                            page = get_chiletrabajos_pw_session(user_id, email, password)
                            if page:
                                print(f"[cht] Reconectado — reintentando empleo...")
                                try:
                                    ok = _postular_empleo_pw(page, job["link"], user, titulo)
                                except Exception:
                                    pass
                        else:
                            print(f"[cht] Error: {e}")

                    if ok and isinstance(ok, dict) and ok.get("ya_postulado"):
                        applied_ids.add(job_id)
                        stats["ya_postulado_portal"] += 1; lc["ya_portal"] += 1
                        print(f"[cht] {j+1}/{len(jobs)} Ya postulado — skip")
                    elif ok and isinstance(ok, dict) and ok.get("skip") == "salario":
                        stats["salario"] += 1; lc["salario"] += 1
                    elif ok:
                        descripcion  = (isinstance(ok, dict) and ok.get("descripcion")) or ""
                        empresa_cht  = (isinstance(ok, dict) and ok.get("empresa")) or ""

                        # Enviar email al reclutador si hay email en la descripción
                        email_rec = extract_email(descripcion)
                        if email_rec:
                            if bq.ya_envio_email(user_id, email_rec):
                                print(f"[cht] Email a {email_rec} ya enviado antes — skip")
                            else:
                                enviado_ok = send_application(
                                    user,
                                    {"titulo": job["titulo"], "empresa": empresa_cht},
                                    email_rec,
                                )
                                if enviado_ok:
                                    print(f"[cht] Email enviado a {email_rec} ok")
                                    descripcion += f"\n\n[email_directo: {email_rec}]"

                        bq.save_jobs([{
                            "id_empleo":         job_id,
                            "id_usuario":        user_id,
                            "titulo_empleo":     job["titulo"],
                            "cargo":             cargo,
                            "Fecha_Postulacion": datetime.datetime.utcnow().isoformat(),
                            "empresa":           empresa_cht,
                            "descripcion":       descripcion,
                            "link":              job["link"],
                            "portal":            PORTAL_ID,
                        }])
                        applied_ids.add(job_id)
                        count += 1
                        stats["postulados"] += 1; lc["postulados"] += 1
                        print(f"[cht] Guardado ({count})")
                        if count >= max_count:
                            print(f"[cht] Limite {max_count} alcanzado — deteniendo ChileTrabajos")
                            raise StopIteration
                    else:
                        stats["error"] += 1; lc["error"] += 1
                        try:
                            page.go_back()
                            page.wait_for_timeout(2000)
                        except Exception:
                            pass

                # Resumen por búsqueda
                partes = [f"encontrados={lc['encontrados']}"]
                if lc["ya_aplicado"]:   partes.append(f"ya_aplicado={lc['ya_aplicado']}")
                if lc["ya_portal"]:     partes.append(f"ya_portal={lc['ya_portal']}")
                if lc["ciudad_offsite"]: partes.append(f"ciudad={lc['ciudad_offsite']}")
                if lc["no_aplica"]:     partes.append(f"no_aplica={lc['no_aplica']}")
                if lc["salario"]:       partes.append(f"salario_bajo={lc['salario']}")
                if lc["pendientes"]:    partes.append(f"pendientes={lc['pendientes']}")
                if lc["postulados"]:    partes.append(f"postulados={lc['postulados']}")
                if lc["error"]:         partes.append(f"error={lc['error']}")
                print(f"[cht] >> '{cargo}' en '{ubicacion}': {' | '.join(partes)}")

    except StopIteration:
        pass  # límite alcanzado — salida limpia
    except Exception as e:
        import traceback
        print(f"[cht] Error general: {e}")
        traceback.print_exc()
    finally:
        close_chiletrabajos_pw_session(user_id)

    if modo_revision and pending_jobs:
        seen_links: set = set()
        pending_unique = []
        for pj in pending_jobs:
            if pj["link"] not in seen_links:
                seen_links.add(pj["link"])
                pending_unique.append(pj)
        saved = bq.save_pending_jobs(user_id, PORTAL_ID, pending_unique)
        print(f"[cht] {saved} empleos guardados para revision (de {len(pending_jobs)} encontrados, {len(pending_jobs)-len(pending_unique)} duplicados):")
        for pj in pending_unique:
            print(f"  -> {_safe(pj['titulo'][:70])}")

    _ident2 = user.get("EMAIL") or user.get("NOMBRE") or user_id
    print(
        f"[cht] RESUMEN {_ident2} | "
        f"encontrados={stats['encontrados']} | "
        f"ya_aplicado={stats['ya_aplicado']} | "
        f"ya_portal={stats['ya_postulado_portal']} | "
        f"ciudad={stats['ciudad_offsite']} | "
        f"no_aplica={stats['no_aplica']} | "
        f"salario_bajo={stats['salario']} | "
        f"pendientes={stats['pendientes']} | "
        f"postulados={stats['postulados']} | "
        f"error={stats['error']}"
    )

    return count


if __name__ == "__main__":
    os.environ.setdefault(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\Users\bastian\Desktop\Script_Python\jobs-425301-ba25295bbbd0.json",
    )
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
    users = bq.get_active_users()
    for u in users:
        postular_empleos_cht(u["ID_USUARIO"], u)
