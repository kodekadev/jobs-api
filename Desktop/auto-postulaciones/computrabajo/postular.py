"""
Computrabajo.cl — Postulaciones automáticas con sesión autenticada.

Flujo:
  1. Carga cookies de BigQuery e inyecta en el contexto Playwright
  2. Por cada cargo/ciudad: navega al buscador de Computrabajo y recopila ofertas
  3. Por cada oferta relevante: navega, click "Postularme", confirma
  4. Guarda resultado en BigQuery EMPLEOS

Uso:
    from computrabajo.postular import postular_empleos_cpt
    ok = postular_empleos_cpt(user_id, user, max_n=10)
"""
import os
import sys
import re
import time
import random
import json
import unicodedata
import datetime

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bq

BASE_URL      = "https://cl.computrabajo.com"
CANDIDATO_URL = "https://candidato.cl.computrabajo.com"
ACCESO_URL    = f"{CANDIDATO_URL}/acceso/"
PORTAL_ID     = "computrabajo"

_BTN_POSTULAR = [
    "button:has-text('Postularme')",
    "button:has-text('Me interesa')",
    "a:has-text('Postularme')",
    "a:has-text('Me interesa')",
    "[data-test='btn-apply']",
    ".btn-apply",
]

_YA_POSTULADO = [
    "ya te has postulado", "ya postulaste", "postulación enviada",
    "te has postulado", "ya has postulado",
]

_CONFIRM_SIGNALS = [
    "postulación enviada", "te has postulado", "gracias", "éxito",
    "aplicación enviada", "postulación registrada", "ya postulaste",
    "ya te has postulado",
]


def _slugify(text: str) -> str:
    """Convierte texto a slug URL-safe sin acentos."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")


# Mapping ciudad/región → slug de región en Computrabajo Chile
# El slug se usa en la URL: /trabajo-de-{cargo}-en-{slug}
_CPT_REGION_SLUGS: dict[str, str] = {
    # Región Metropolitana
    "santiago": "rmetropolitana", "metropolitana": "rmetropolitana",
    "rm": "rmetropolitana", "r.metropolitana": "rmetropolitana",
    "providencia": "rmetropolitana", "las condes": "rmetropolitana",
    "maipu": "rmetropolitana", "puente alto": "rmetropolitana",
    "nunoa": "rmetropolitana", "la florida": "rmetropolitana",
    "pudahuel": "rmetropolitana", "quilicura": "rmetropolitana",
    "vitacura": "rmetropolitana", "lo barnechea": "rmetropolitana",
    "recoleta": "rmetropolitana", "independencia": "rmetropolitana",
    "san bernardo": "rmetropolitana", "el bosque": "rmetropolitana",
    # Valparaíso
    "valparaiso": "valparaiso", "vina del mar": "valparaiso",
    "vina": "valparaiso", "concon": "valparaiso", "quilpue": "valparaiso",
    "villa alemana": "valparaiso", "quillota": "valparaiso",
    "san antonio": "valparaiso", "los andes": "valparaiso",
    # Biobío
    "biobio": "biobio", "bio bio": "biobio", "concepcion": "biobio",
    "talcahuano": "biobio", "coronel": "biobio", "chiguayante": "biobio",
    "los angeles": "biobio", "san pedro de la paz": "biobio",
    # La Araucanía
    "araucania": "araucania", "temuco": "araucania",
    "padre las casas": "araucania", "angol": "araucania",
    # Los Lagos
    "los lagos": "los-lagos", "puerto montt": "los-lagos",
    "osorno": "los-lagos", "puerto varas": "los-lagos",
    # Los Ríos
    "los rios": "los-rios", "valdivia": "los-rios", "la union": "los-rios",
    # Maule
    "maule": "maule", "talca": "maule", "curico": "maule",
    "linares": "maule", "constitucion": "maule",
    # O'Higgins
    "ohiggins": "ohiggins", "o'higgins": "ohiggins",
    "rancagua": "ohiggins", "san fernando": "ohiggins",
    # Coquimbo
    "coquimbo": "coquimbo", "la serena": "coquimbo",
    "ovalle": "coquimbo", "illapel": "coquimbo",
    # Antofagasta
    "antofagasta": "antofagasta", "calama": "antofagasta",
    "tocopilla": "antofagasta", "mejillones": "antofagasta",
    # Atacama
    "atacama": "atacama", "copiapo": "atacama",
    "vallenar": "atacama", "tierra amarilla": "atacama",
    # Tarapacá
    "tarapaca": "tarapaca", "iquique": "tarapaca",
    "alto hospicio": "tarapaca",
    # Arica y Parinacota
    "arica": "arica-y-parinacota", "parinacota": "arica-y-parinacota",
    # Ñuble
    "nuble": "nuble", "chilian": "nuble", "chillan": "nuble",
    "san carlos": "nuble",
    # Aysén
    "aysen": "aysen", "coyhaique": "aysen",
    # Magallanes
    "magallanes": "magallanes", "punta arenas": "magallanes",
}


def _ubicacion_to_cpt_slug(ubicacion: str) -> str:
    """
    Convierte el nombre de ciudad/región del usuario al slug de región
    que usa Computrabajo en sus URLs de búsqueda.
    Ej: "Santiago" → "rmetropolitana", "Temuco" → "araucania"
    """
    # Normalizar: quitar acentos, minúsculas, recortar
    nfkd = unicodedata.normalize("NFKD", ubicacion.lower().strip())
    norm = "".join(c for c in nfkd if not unicodedata.combining(c)).strip()
    norm = norm.split(",")[0].strip()

    # Match exacto
    if norm in _CPT_REGION_SLUGS:
        return _CPT_REGION_SLUGS[norm]

    # Match parcial (contiene la ciudad o viceversa)
    for key, slug in _CPT_REGION_SLUGS.items():
        if key in norm or norm in key:
            return slug

    # Fallback: slugify directo (ej: "Antofagasta" → "antofagasta")
    return _slugify(norm)


def _esta_logueado(page) -> bool:
    cur = page.url.lower()
    if "login" in cur or "iniciar" in cur or "acceso" in cur:
        return False
    content = page.content().lower()
    return any(s in content for s in ["cerrar sesión", "mi área", "mis postulaciones", "notificaciones"])


_PW_SELS = "input[name='Password'], input[name='Input.Password'], input[type='password']"
_EM_SELS = "input[name='Email'], input[name='Input.Email'], input[name='username'], input[type='email']"

_CPT_PASSWORD_DEFAULT = "Jobs2025"


def _cpt_submit_password(page) -> bool:
    """Click el botón de submit de contraseña. Retorna True si se clickeó."""
    for sel in ["a[btn-submit]", "a#btnSubmitPass", "a.b_primary",
                "button:has-text('Iniciar sesión'):visible",
                "button:has-text('Acceder'):visible",
                "input[type='submit']:visible",
                "button[type='submit']:visible"]:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible(timeout=1000):
                btn.click()
                return True
        except Exception:
            pass
    return False


def _recuperar_password_cpt(page, email_usuario: str, user_id: str) -> str | None:
    """
    Flujo de recuperación de contraseña en Computrabajo.
    Hace click en '¿No recuerdas tu contraseña?', espera el email de reset,
    navega al link, pone nueva contraseña y la guarda en BQ.
    Retorna la nueva contraseña si tuvo éxito, None si falló.
    """
    print(f"  [cpt-recover] Iniciando recuperación de contraseña para {email_usuario}")

    # Click en "¿No recuerdas tu contraseña?"
    try:
        link = page.locator(
            "a#showRememberPasswordButton, a.open-recover-container, "
            "a:has-text('No recuerdas'), a:has-text('Olvidé')"
        ).first
        link.wait_for(state="visible", timeout=5000)
        link.click()
        page.wait_for_timeout(2000)
    except Exception as e:
        print(f"  [cpt-recover] No se encontró link de recuperación: {e}")
        return None

    # El formulario de recuperación puede pedir el email (ya debería estar pre-llenado)
    try:
        em_recover = page.locator(
            "input[name='RememberPasswordEmail'], input[id='rememberPasswordEmail'], "
            "input.recover-email, input[type='email']:visible"
        ).first
        if em_recover.count() and em_recover.is_visible(timeout=2000):
            val = em_recover.input_value()
            if not val:
                em_recover.fill(email_usuario)
            page.wait_for_timeout(300)
    except Exception:
        pass

    # Submit del formulario de recuperación
    for sel in ["a#sbRemember", "a:has-text('Restablecer contraseña')",
                "a[btn-submit]:visible", "button:has-text('Enviar'):visible",
                "a:has-text('Enviar'):visible"]:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible(timeout=1500):
                btn.click()
                print(f"  [cpt-recover] Solicitud enviada ({sel})")
                break
        except Exception:
            pass

    # Esperar confirmación visual (div.js_pswd-recovered pierde la clase "hide")
    try:
        page.wait_for_selector("div.js_pswd-recovered:not(.hide)", timeout=8000)
        print("  [cpt-recover] Confirmación visual: email enviado")
    except Exception:
        pass

    page.wait_for_timeout(1000)

    # Esperar email de reset vía IMAP
    print(f"  [cpt-recover] Esperando email de reset para {email_usuario}...")
    from email_verifier import esperar_verificacion
    import time as _t
    _t.sleep(10)
    link_reset = esperar_verificacion(
        email_usuario, timeout=180,
        freshness_minutes=10, sender_filter="computrabajo"
    )
    if not link_reset:
        print(f"  [cpt-recover] No llegó email de reset — abortando")
        return None

    print(f"  [cpt-recover] Link de reset recibido: {link_reset[:80]}")

    # Navegar al link de reset
    try:
        page.goto(link_reset, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
    except Exception as e:
        print(f"  [cpt-recover] Error navegando al link: {e}")
        return None

    nueva = _CPT_PASSWORD_DEFAULT

    # Llenar nueva contraseña (puede haber uno o dos campos)
    for sel in ["input[name='NewPassword'], input[name='Password'], input[type='password']"]:
        try:
            campos = page.locator(sel).all()
            for campo in campos:
                if campo.is_visible(timeout=1000):
                    campo.fill(nueva)
                    page.wait_for_timeout(200)
        except Exception:
            pass

    page.wait_for_timeout(400)

    # Submit del formulario de reset
    for sel in ["a[btn-submit]", "a.b_primary", "button:has-text('Guardar'):visible",
                "button:has-text('Cambiar'):visible", "button[type='submit']:visible",
                "input[type='submit']:visible"]:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible(timeout=1500):
                btn.click()
                print(f"  [cpt-recover] Nueva contraseña enviada")
                break
        except Exception:
            pass

    _t.sleep(5)

    # Guardar nueva contraseña en BQ
    try:
        cuenta = bq.get_portal_account(user_id, PORTAL_ID) or {}
        bq.save_portal_account(user_id, PORTAL_ID, cuenta.get("email", email_usuario), nueva)
        bq.update_portal_account(user_id, PORTAL_ID, password_invalida=False)
        print(f"  [cpt-recover] Contraseña actualizada en BQ para {user_id}")
    except Exception as e:
        print(f"  [cpt-recover] Error guardando en BQ: {e}")

    return nueva


def _login_pw(page, email: str, password: str, user_id: str = "") -> bool:
    """Login en Computrabajo: email → Continuar → password → submit."""
    page.goto(ACCESO_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    # 1. Llenar email
    try:
        em = page.locator(_EM_SELS).first
        em.wait_for(state="visible", timeout=5000)
        em.fill(email)
        page.wait_for_timeout(400)
    except Exception as e:
        print(f"  [cpt-post] Error ingresando email: {e}")
        return False

    # 2. Click "Continuar" para avanzar al paso de contraseña
    for sel in ["button:has-text('Continuar'):visible", "button:has-text('Siguiente'):visible"]:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible(timeout=2000):
                btn.click()
                break
        except Exception:
            pass

    page.wait_for_timeout(2500)

    if "step1" in page.url.lower():
        print("  [cpt-post] Email no existe en Computrabajo")
        return False

    # 3. Llenar contraseña
    try:
        pwd_loc = page.locator(_PW_SELS).first
        pwd_loc.wait_for(state="visible", timeout=5000)
        pwd_loc.fill(password)
        page.wait_for_timeout(400)
    except Exception as e:
        print(f"  [cpt-post] Error campo password: {e}")
        return False

    # 4. Submit
    if not _cpt_submit_password(page):
        print("  [cpt-post] No se encontró botón de submit")
        return False

    import time as _t
    _t.sleep(8)

    # 5. Verificar si login falló por contraseña incorrecta → recuperar
    content = page.content().lower()
    if any(s in content for s in ["contraseña incorrecta", "password incorrect",
                                   "credenciales incorrectas", "incorrect password"]):
        print(f"  [cpt-post] Contraseña incorrecta — iniciando recuperación...")
        nueva = _recuperar_password_cpt(page, email, user_id)
        if not nueva:
            return False
        # Reintentar login con nueva contraseña
        return _login_pw(page, email, nueva, user_id)

    ok = _esta_logueado(page)
    print(f"  [cpt-post] Login {'OK' if ok else 'FALLÓ'}: {page.url[:80]}")
    return ok


def _buscar_empleos_cpt(page, cargo: str, ciudad: str, n: int) -> list[dict]:
    """
    Navega al buscador de Computrabajo filtrando por cargo y ciudad.
    Retorna lista de dicts con {id, titulo, empresa, link}.
    """
    slug_cargo  = _slugify(cargo)
    slug_ciudad = _slugify(ciudad)

    if slug_ciudad:
        search_url = f"{BASE_URL}/trabajo-de-{slug_cargo}-en-{slug_ciudad}"
    else:
        search_url = f"{BASE_URL}/trabajo-de-{slug_cargo}"

    print(f"  [cpt-post] Buscando: '{cargo}' en '{ciudad}' → {search_url}")

    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2500)
    except Exception as e:
        print(f"  [cpt-post] Error navegando buscador: {e}")
        return []

    jobs: list[dict] = []
    seen: set = set()

    # Computrabajo: cards de trabajo en article[data-id]
    articles = page.locator("article[data-id]").all()
    for art in articles:
        if len(jobs) >= n:
            break
        try:
            # Link + título: a.js-o-link tiene el href y texto del cargo
            link_el = art.locator("a.js-o-link").first
            if not link_el.count():
                link_el = art.locator("h2 a, h3 a").first
            if not link_el.count():
                continue

            href  = link_el.get_attribute("href") or ""
            title = (link_el.inner_text() or "").strip()
            if not href or not title or len(title) < 4:
                continue

            if href.startswith("/"):
                href = f"{BASE_URL}{href}"
            if href in seen:
                continue
            seen.add(href)

            # Empresa — <a offer-grid-article-company-url="">
            empresa = ""
            try:
                emp_el = art.locator("[offer-grid-article-company-url]").first
                if emp_el.count():
                    empresa = (emp_el.inner_text() or "").strip()
            except Exception:
                pass

            jobs.append({
                "id":          href,
                "titulo":      title,
                "empresa":     empresa,
                "link":        href,
                "descripcion": "",
                "cargo":       cargo,
            })
        except Exception:
            continue

    # Fallback: links directos /oferta-de-trabajo/
    if not jobs:
        for a in page.locator("a[href*='/oferta-de-trabajo/']").all():
            if len(jobs) >= n:
                break
            try:
                href  = a.get_attribute("href") or ""
                title = (a.inner_text() or "").strip()
                if href and title and len(title) > 4 and href not in seen:
                    if href.startswith("/"):
                        href = f"{BASE_URL}{href}"
                    jobs.append({"id": href, "titulo": title, "empresa": "",
                                 "link": href, "descripcion": "", "cargo": cargo})
                    seen.add(href)
            except Exception:
                continue

    print(f"  [cpt-post] {len(jobs)} ofertas encontradas para '{cargo}' en '{ciudad}'")
    return jobs


def _extraer_descripcion_cpt(page) -> str:
    """Extrae el texto de la descripción del empleo de la página actual de Computrabajo."""
    raw = page.evaluate("""
() => {
    // Estructura real de Computrabajo: <div div-link="oferta"><p class="mbB">descripción</p>
    const ofertaDiv = document.querySelector('[div-link="oferta"]');
    if (ofertaDiv) {
        const p = ofertaDiv.querySelector('p.mbB');
        if (p && p.innerText.trim().length > 50) return p.innerText.trim().slice(0, 4000);
        // Fallback: todos los <p> del div de oferta
        const parrs = Array.from(ofertaDiv.querySelectorAll('p')).map(e => e.innerText.trim()).filter(t => t.length > 50);
        if (parrs.length) return parrs.join('\\n').slice(0, 4000);
    }
    // Fallbacks adicionales
    const selectors = [
        '[itemprop="description"]',
        '.jobDescription',
        '#jobDescription',
        '.offer-desc',
        '#descripcion',
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el && el.innerText.trim().length > 50) return el.innerText.trim().slice(0, 4000);
    }
    return '';
}
""")
    return (raw or "").strip()


def _responder_killer_questions(page, user: dict, job_title: str, cv_text: str) -> bool:
    """
    Detecta y responde las Killer Questions de Computrabajo.
    Maneja textareas (OpenQuestion), radios y selects.
    Retorna True si encontró y respondió preguntas (o si no había ninguna).
    """
    if not page.locator(".form_kq, #btnKiller").count():
        return True

    print(f"    [cpt] killer questions detectadas")

    # Extraer TODAS las preguntas: textarea, radio y select
    all_questions = page.evaluate("""
() => {
    const result = [];
    const form = document.querySelector('.form_kq') || document.querySelector('form');
    if (!form) return result;

    // ── Textareas (OpenQuestion) ──────────────────────────────────────────────
    const titleInputs = form.querySelectorAll('input[name*="KillerQuestions"][name$="].Title"]');
    for (const inp of titleInputs) {
        const m = inp.name.match(/\\[(\\d+)\\]/);
        if (!m) continue;
        const idx = m[1];
        const taName = 'KillerQuestions[' + idx + '].OpenQuestion';
        const ta = form.querySelector('textarea[name="' + taName + '"]');
        if (!ta) continue;
        result.push({ kind: 'textarea', index: parseInt(idx), id: ta.id, name: taName, label: inp.value.trim(), options: [] });
    }
    // Fallback textarea: leer por .field_textarea
    if (result.length === 0) {
        form.querySelectorAll('.field_textarea').forEach((div, i) => {
            const lbl = div.querySelector('label');
            const ta  = div.querySelector('textarea');
            if (lbl && ta) result.push({ kind: 'textarea', index: i, id: ta.id, name: ta.name, label: lbl.innerText.trim(), options: [] });
        });
    }

    // ── Radios ────────────────────────────────────────────────────────────────
    const seenRadioNames = new Set();
    form.querySelectorAll('input[type="radio"]').forEach(radio => {
        if (seenRadioNames.has(radio.name)) return;
        seenRadioNames.add(radio.name);

        // Obtener todas las opciones del grupo
        const opts = [];
        form.querySelectorAll('input[type="radio"][name="' + radio.name + '"]').forEach(r => {
            const lbl = form.querySelector('label[for="' + r.id + '"]') || r.closest('label');
            const optLabel = lbl ? lbl.innerText.trim() : r.value;
            opts.push({ value: r.value, label: optLabel, id: r.id });
        });

        // Texto de la pregunta: buscar hidden Title, luego p/label cercano
        let questionLabel = '';
        const m = radio.name.match(/KillerQuestions\\[(\\d+)\\]/);
        if (m) {
            const titleInp = form.querySelector('input[name="KillerQuestions[' + m[1] + '].Title"]');
            if (titleInp) questionLabel = titleInp.value.trim();
        }
        if (!questionLabel) {
            const container = radio.closest('li, .kq-question, .field_radio, .form-group, div');
            if (container) {
                const pOrLabel = container.querySelector('p, h4, h3, strong, .question-text');
                if (pOrLabel) questionLabel = pOrLabel.innerText.trim();
            }
        }

        const idx = m ? parseInt(m[1]) : result.length + 100;
        result.push({ kind: 'radio', index: idx, id: radio.id, name: radio.name, label: questionLabel, options: opts });
    });

    // ── Selects ───────────────────────────────────────────────────────────────
    form.querySelectorAll('select[name*="KillerQuestions"]').forEach(sel => {
        let questionLabel = '';
        const m = sel.name.match(/KillerQuestions\\[(\\d+)\\]/);
        if (m) {
            const titleInp = form.querySelector('input[name="KillerQuestions[' + m[1] + '].Title"]');
            if (titleInp) questionLabel = titleInp.value.trim();
        }
        if (!questionLabel) {
            const container = sel.closest('li, .kq-question, .field_select, .form-group, div');
            if (container) {
                const lbl = container.querySelector('label, p, h4, strong');
                if (lbl) questionLabel = lbl.innerText.trim();
            }
        }
        const opts = Array.from(sel.options)
            .filter(o => o.value !== '')
            .map(o => ({ value: o.value, label: o.text.trim(), id: '' }));
        const idx = m ? parseInt(m[1]) : result.length + 200;
        result.push({ kind: 'select', index: idx, id: sel.id, name: sel.name, label: questionLabel, options: opts });
    });

    return result;
}
""")

    if not all_questions:
        print(f"    [cpt] no se pudieron extraer preguntas — enviando sin responder")
        return False

    print(f"    [cpt] {len(all_questions)} pregunta(s): {[q['kind'] + ':' + q['label'][:30] for q in all_questions]}")

    from portal_accounts import _llm_answer_questions, _standard_answer

    # Resolver cada pregunta
    answers: dict[int, str] = {}
    unresolved_idx: list[int] = []
    llm_items: list[dict] = []

    for i, q in enumerate(all_questions):
        item = {"label": q["label"], "type": q["kind"], "options": [o["label"] for o in q["options"]], "placeholder": ""}
        std = _standard_answer(item, user)
        if std:
            answers[i] = std
        else:
            unresolved_idx.append(i)
            llm_items.append({"label": q["label"], "type": q["kind"],
                               "options": [{"text": o["label"], "value": o["value"]} for o in q["options"]],
                               "placeholder": ""})

    if unresolved_idx:
        llm_ans = _llm_answer_questions(llm_items, user, cv_text=cv_text, job_title=job_title)
        for llm_i, orig_i in enumerate(unresolved_idx):
            ans = llm_ans.get(str(llm_i), "")
            if ans:
                answers[orig_i] = ans

    # Aplicar respuestas al DOM
    for i, q in enumerate(all_questions):
        ans_text = answers.get(i, "")
        if not ans_text:
            # Para Si/No sin respuesta → No (respuesta conservadora)
            if q["kind"] in ("radio", "select") and len(q["options"]) == 2:
                opts_lower = [o["label"].lower() for o in q["options"]]
                if "no" in opts_lower:
                    ans_text = q["options"][opts_lower.index("no")]["label"]
                    print(f"    [cpt] default 'No' para: '{q['label'][:50]}'")

        if not ans_text:
            print(f"    [cpt] sin respuesta para: '{q['label'][:50]}'")
            continue

        if q["kind"] == "textarea":
            ans_safe = ans_text[:490].replace("\\", "\\\\").replace("`", "'")
            page.evaluate(f"""
() => {{
    const ta = document.getElementById('{q["id"]}') || document.querySelector('[name="{q["name"]}"]');
    if (ta) {{
        ta.value = `{ans_safe}`;
        ta.dispatchEvent(new Event('input', {{bubbles:true}}));
        ta.dispatchEvent(new Event('change', {{bubbles:true}}));
    }}
}}
""")

        elif q["kind"] == "radio":
            # Buscar la opción cuyo label o value coincida con la respuesta
            # Nota: los radios de CPT no tienen id, se ubican por name+value
            ans_norm = ans_text.strip().lower()
            matched_val = None
            for opt in q["options"]:
                if opt["label"].lower() == ans_norm or opt["value"].lower() == ans_norm:
                    matched_val = opt["value"]
                    break
            if matched_val is None and q["options"]:
                # Coincidencia parcial sobre el label
                for opt in q["options"]:
                    if ans_norm in opt["label"].lower() or opt["label"].lower() in ans_norm:
                        matched_val = opt["value"]
                        break
            if matched_val is None and q["options"]:
                matched_val = q["options"][0]["value"]  # fallback: primera opción
            if matched_val is not None:
                rname = q["name"].replace('"', '\\"')
                rval  = str(matched_val).replace('"', '\\"')
                page.evaluate(f"""
() => {{
    const r = document.querySelector('input[type="radio"][name="{rname}"][value="{rval}"]');
    if (r) {{ r.checked = true; r.click(); r.dispatchEvent(new Event('change', {{bubbles:true}})); }}
}}
""")

        elif q["kind"] == "select":
            ans_norm = ans_text.strip().lower()
            matched_val = None
            for opt in q["options"]:
                if opt["label"].lower() == ans_norm or opt["value"].lower() == ans_norm:
                    matched_val = opt["value"]
                    break
            if not matched_val:
                for opt in q["options"]:
                    if ans_norm in opt["label"].lower():
                        matched_val = opt["value"]
                        break
            if matched_val:
                page.evaluate(f"""
() => {{
    const sel = document.getElementById('{q["id"]}') || document.querySelector('[name="{q["name"]}"]');
    if (sel) {{
        sel.value = '{matched_val}';
        sel.dispatchEvent(new Event('change', {{bubbles:true}}));
    }}
}}
""")

        print(f"    [cpt] {q['kind']} '{q['label'][:45]}' → '{ans_text[:40]}'")

    page.wait_for_timeout(500)

    # Submit
    submitted = page.evaluate("""
() => {
    const btn = document.getElementById('btnKiller');
    if (btn) { btn.click(); return true; }
    return false;
}
""")
    page.wait_for_timeout(3000)
    return submitted


def _postular_uno(page, empleo: dict, user: dict, cv_text: str = "", skip_llm: bool = False) -> tuple[bool, str]:
    """
    Navega a la oferta, evalúa con LLM si aplica (salvo skip_llm=True), luego postula.
    Retorna (ok: bool, motivo: str).
    """
    url    = empleo.get("link", "")
    titulo = empleo.get("titulo", url)[:60]

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(random.uniform(1.5, 2.5))
    except Exception as e:
        print(f"    [cpt] timeout: {url[:60]}: {e}")
        return False, "timeout"

    # Verificar si ya postulado
    content = page.content().lower()
    if any(s in content for s in _YA_POSTULADO):
        print(f"    [cpt] ya postulado: {titulo}")
        return False, "ya_postulado"

    # Extraer descripción siempre (estamos en la página de detalle)
    descripcion = _extraer_descripcion_cpt(page)
    empleo["descripcion"] = descripcion  # actualizar in-place para que bq.save_jobs la use

    # Evaluar con LLM (omitir si el gate de título ya aprobó)
    if not skip_llm:
        from portal_accounts import _llm_job_aplica
        llm_ok, llm_razon = _llm_job_aplica(
            descripcion, titulo, empleo.get("empresa", ""), user,
            link=url, portal=PORTAL_ID,
        )
        if not llm_ok:
            return False, f"llm_descarte: {llm_razon}"

    # Click botón postular
    clicked = False
    for sel in _BTN_POSTULAR:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible(timeout=2000):
                btn.click()
                clicked = True
                print(f"    [cpt] click '{(btn.inner_text() or sel)[:30]}': {titulo}")
                break
        except Exception:
            pass

    if not clicked:
        clicked = page.evaluate("""
() => {
    for (const sel of ['button', 'a']) {
        for (const el of document.querySelectorAll(sel)) {
            const t = (el.innerText || '').toLowerCase();
            if (t.includes('postularme') || t.includes('me interesa')) {
                el.click(); return true;
            }
        }
    }
    return false;
}
""")
        if clicked:
            print(f"    [cpt] click JS: {titulo}")

    if not clicked:
        print(f"    [cpt] sin botón postular: {titulo}")
        return False, "sin_boton"

    page.wait_for_timeout(3000)

    # Detectar "ya aplicaste" ANTES de contar como nuevo
    _ya_content = page.content().lower()
    _YA_SIGNALS = ["ya aplicaste a esta oferta", "ya te postulaste", "ya aplicaste"]
    if any(s in _ya_content for s in _YA_SIGNALS):
        return False, "ya_postulado_previamente"

    # Responder killer questions si aparecen
    _responder_killer_questions(page, user, job_title=titulo, cv_text=cv_text)
    page.wait_for_timeout(2000)

    # Verificar éxito
    content = page.content().lower()
    ok = any(s in content for s in _CONFIRM_SIGNALS)
    if not ok:
        # Solo confiar en URL-fallback si tampoco hay señal de "ya postulado"
        if any(s in content for s in _YA_SIGNALS):
            return False, "ya_postulado_previamente"
        ok = "login" not in page.url.lower() and "acceso" not in page.url.lower()
    return ok, "" if ok else "sin_confirmacion"


def postular_empleos_cpt(user_id: str, user: dict, max_n: int = 10) -> int:
    """
    Busca en el buscador de Computrabajo por cargo + ciudad y postula.
    Retorna el número de postulaciones exitosas.
    """
    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if not cuenta:
        print(f"  [cpt-post] Sin cuenta para {user_id}")
        return 0

    email    = cuenta.get("email", "")
    password = cuenta.get("password", "")

    cargos = user.get("CARGOS") or user.get("cargos") or []
    if isinstance(cargos, str):
        try: cargos = json.loads(cargos)
        except Exception: cargos = [cargos]

    ubicaciones = user.get("UBICACIONES") or user.get("ubicaciones") or ["Santiago"]
    if isinstance(ubicaciones, str):
        try: ubicaciones = json.loads(ubicaciones)
        except Exception: ubicaciones = [ubicaciones]
    # Convertir ciudad/región al slug de región de Computrabajo
    # "Santiago" → "rmetropolitana", "Temuco" → "araucania", etc.
    _ubicacion_raw = (ubicaciones[0] if ubicaciones else "Santiago").strip()
    ciudad = _ubicacion_to_cpt_slug(_ubicacion_raw)
    print(f"  [cpt-post] Ubicación: '{_ubicacion_raw}' → slug '{ciudad}'")

    from google.cloud import bigquery as _bq
    _ya_cfg = _bq.QueryJobConfig(query_parameters=[
        _bq.ScalarQueryParameter("uid", "STRING", user_id),
    ])
    ya_postulados = set(
        dict(r).get("link", "") or ""
        for r in bq._query(
            f"""SELECT link FROM `{bq.PROJECT}.{bq.DATASET}.EMPLEOS`
                WHERE id_usuario = @uid AND portal = 'computrabajo'
                AND DATE(Fecha_Postulacion, 'America/Santiago')
                    = CURRENT_DATE('America/Santiago')""",
            _ya_cfg,
        ).result()
    )

    try:
        from playwright.sync_api import sync_playwright
        from portal_accounts import (
            _make_pw_context, _new_stealth_page, job_aplica_al_usuario,
            _llm_answer_questions, _standard_answer, _extract_cv_text,
            _llm_job_aplica, _titulo_gate,
        )
    except ImportError:
        print("  [cpt-post] Playwright no disponible")
        return 0

    _ident = user.get("EMAIL") or user.get("NOMBRE") or user_id
    print(f"  [cpt-post] Iniciando para {_ident}")
    modo_revision = bq.get_modo_revision(user_id)
    print(f"  [cpt-post] Modo: {'REVISIÓN (guardará para aprobar)' if modo_revision else 'AUTOPILOT (postula directo)'}")

    ok_count = 0

    try:
        _, browser, ctx, _ = _make_pw_context()
        page = _new_stealth_page(ctx)
        try:

            # Inyectar cookies
            cookies = bq.get_portal_cookies(user_id, PORTAL_ID)
            if cookies:
                ctx.add_cookies([c for c in cookies if isinstance(c, dict) and "name" in c and "value" in c])
                print(f"  [cpt-post] {len(cookies)} cookies inyectadas")

            # Verificar sesión
            page.goto(f"{CANDIDATO_URL}/candidate/home", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            if not _esta_logueado(page):
                if not _login_pw(page, email, password, user_id=user_id):
                    return 0

            cv_text = ""
            try:
                from portal_accounts import _extract_cv_text
                _cv_url = user.get("CV_URL") or user.get("cv_url") or ""
                cv_text = _extract_cv_text(_cv_url) or ""
            except Exception:
                pass

            print(f"  [cpt-post] Sesión activa | ciudad: {ciudad} | max: {max_n}")

            pending_jobs: list[dict] = []

            for cargo in (cargos if cargos else [""]):
                if not modo_revision and ok_count >= max_n:
                    break

                empleos = _buscar_empleos_cpt(page, cargo, ciudad, n=max_n * 3)

                for emp in empleos:
                    if not modo_revision and ok_count >= max_n:
                        break

                    url = emp.get("link", "")
                    if not url or url in ya_postulados:
                        continue

                    # 1. Filtro básico: empresa excluida, práctica vs empleo, part-time
                    aplica, motivo_basico = job_aplica_al_usuario(
                        emp.get("titulo", ""), emp.get("empresa", ""), user,
                        check_score=False,
                    )
                    if not aplica:
                        print(f"    ⊘ [cpt] filtro básico: {emp.get('titulo','')[:40]} | {motivo_basico}")
                        continue

                    # 2. Gate de título: evita navegar a empleos obviamente irrelevantes
                    gate = _titulo_gate(emp.get("titulo", ""), user)
                    if gate == "rechazar":
                        print(f"    ⊘ [cpt] título sin match: {emp.get('titulo','')[:50]}")
                        continue

                    # Modo revisión: guardar para que el usuario apruebe
                    if modo_revision:
                        pending_jobs.append(emp)
                        ya_postulados.add(url)
                        print(f"    📋 [cpt] pendiente revisión: {emp.get('titulo','')[:50]}")
                        continue

                    ok, motivo = _postular_uno(page, emp, user, cv_text=cv_text, skip_llm=(gate == "aprobar"))

                    if ok:
                        bq.save_jobs([{
                            "id_empleo":         url,
                            "id_usuario":        user_id,
                            "titulo_empleo":     emp.get("titulo", ""),
                            "cargo":             emp.get("cargo", cargo),
                            "Fecha_Postulacion": datetime.datetime.utcnow().isoformat(),
                            "empresa":           emp.get("empresa", ""),
                            "descripcion":       emp.get("descripcion", ""),
                            "link":              url,
                            "portal":            PORTAL_ID,
                        }])
                        ok_count += 1
                        ya_postulados.add(url)
                        print(f"    ✓ [cpt] postulado: {emp.get('titulo','')[:50]}")
                    elif motivo == "ya_postulado_previamente":
                        ya_postulados.add(url)  # evitar re-check en esta sesión
                        print(f"    ~ [cpt] ya postulado antes: {emp.get('titulo','')[:50]}")
                    elif motivo.startswith("llm_descarte"):
                        pass  # ya logueado dentro de _postular_uno
                    else:
                        print(f"    ✗ [cpt] falló ({motivo}): {emp.get('titulo','')[:50]}")

                    time.sleep(random.uniform(3, 5))

            # Modo revisión: guardar pendientes al final de la sesión
            if modo_revision and pending_jobs:
                saved = bq.save_pending_jobs(user_id, PORTAL_ID, pending_jobs)
                print(f"  [cpt-post] {saved} empleos guardados para revisión:")
                for pj in pending_jobs:
                    print(f"    -> {pj['titulo'][:70]}")

            # Actualizar cookies
            bq.save_portal_cookies(user_id, PORTAL_ID, ctx.cookies())
        finally:
            try:
                browser.close()
            except Exception:
                pass

    except Exception as e:
        import traceback
        print(f"  [cpt-post] Error: {e}")
        traceback.print_exc()

    print(f"  [cpt-post] {ok_count} postulaciones OK para {user_id}")
    return ok_count
