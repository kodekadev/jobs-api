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
import datetime

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bq
from portal_accounts import (
    get_chiletrabajos_pw_session,
    close_chiletrabajos_pw_session,
    _standard_answer,
    _llm_answer_questions,
    _extract_cv_text,
    _save_answers_to_cache,
)

BASE_URL  = "https://www.chiletrabajos.cl"
PORTAL_ID = "chiletrabajos"

_NUMERIC_KWS = {"PRETENSION", "SUELDO", "RENTA", "SALARIO", "ANOS DE EXP",
                "AÑOS DE EXP", "EXPERIENCIA", "CUANTOS ANOS"}


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
        cv_url  = user.get("cv_url") or ""
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
            print(f"    [preg/{source}] [{idx}] '{(item['label'] or item['type'])[:40]}' -> '{resp[:40]}'")
            el.evaluate(_SET_VALUE_JS, resp)
        except Exception:
            pass

    print(f"    [cht] Preguntas: {len(grupos)} radios, {len(pending)} inputs")


def _extraer_descripcion_cht(page) -> str:
    """Extrae el texto de descripción del empleo desde la página de detalle."""
    for sel in [
        "[class*='descripcion-oferta']", "[class*='descripcion-empleo']",
        "#descripcion", "[class*='descripcion']", ".detalle-oferta",
        "#detalle-oferta", ".job-description",
    ]:
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible():
                txt = el.inner_text().strip()
                if len(txt) > 50:
                    return txt[:5000]
        except Exception:
            continue
    return ""


def _postular_empleo_pw(page, job_url: str, user: dict, titulo: str) -> "dict | bool":
    """Navega al empleo y postula vía Playwright. Retorna dict con ok y descripcion, o False."""
    try:
        page.goto(job_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)

        if "chtlogin" in page.url:
            print(f"    [cht] Redirigido a login — sesión expirada")
            return False

        content = page.content().lower()
        if any(s in content for s in ["ya postulaste", "ya te postulaste", "postulado anteriormente"]):
            print(f"    [cht] Ya postulado")
            return False

        descripcion = _extraer_descripcion_cht(page)

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
                        print(f"    [cht] Click postular: '{txt}'")
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                break

        if not clicked:
            print(f"    [cht] Sin botón postular en {job_url[:60]}")
            return False

        page.wait_for_timeout(3000)
        if "chtlogin" in page.url:
            print(f"    [cht] Redirigido a login tras click")
            return False

        # Update description after the detail page fully loads (post-click may have more content)
        if not descripcion:
            descripcion = _extraer_descripcion_cht(page)

        # Formulario de preguntas
        form_loc = page.locator(
            "form[id*='postular'], form:has(input.enviar-postulacion)"
        )
        if form_loc.count() > 0:
            try:
                if form_loc.first.is_visible():
                    _responder_preguntas_cht_pw(page, user, job_title=titulo)
                    page.wait_for_timeout(1000)
            except Exception:
                pass

        # Submit
        submit_selectors = [
            "xpath=//input[contains(@class,'enviar-postulacion')]",
            "xpath=//input[@value='Enviar postulación']",
            "xpath=//input[@value='Enviar postulacion']",
            "xpath=//button[contains(translate(.,'ENVIAR','enviar'),'enviar') and not(@disabled)]",
            "xpath=//input[@type='submit' and not(@disabled)]",
            "xpath=//button[@type='submit' and not(@disabled)]",
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
                        el.click()
                        print(f"    [cht] Submit: '{enviado[:30]}'")
                        break
                except Exception:
                    continue
            if enviado:
                break

        if not enviado:
            print(f"    [cht] Sin botón submit")
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
        return {"ok": True, "descripcion": descripcion}

    except Exception as e:
        print(f"    [cht] Error postular: {e}")
        return False


def postular_empleos_cht(user_id: str, user: dict) -> int:
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
    try:
        print(f"[cht] Iniciando postulaciones para {user_id}")
        applied_ids = bq.get_applied_job_ids(user_id)

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

                # Seleccionar región
                try:
                    selects = page.locator("select").all()
                    for sel in selects:
                        opts = sel.locator("option").all()
                        for opt in opts:
                            opt_text = ""
                            try:
                                opt_text = opt.inner_text() or ""
                            except Exception:
                                pass
                            if ubicacion.lower() in opt_text.lower():
                                sel.select_option(label=opt_text.strip())
                                break
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

                print(f"[cht] Empleos encontrados: {len(jobs)}")

                for j, job in enumerate(jobs[:20]):
                    job_id = job["link"].split("/")[-1].split("?")[0]
                    if job_id in applied_ids:
                        print(f"[cht] {j+1}/{len(jobs)} Ya aplicado — skip")
                        continue

                    print(f"[cht] {j+1}/{len(jobs)} {job['titulo'][:50]}")
                    ok = _postular_empleo_pw(page, job["link"], user, job["titulo"])

                    if ok:
                        bq.save_jobs([{
                            "id_empleo":         job_id,
                            "id_usuario":        user_id,
                            "titulo_empleo":     job["titulo"],
                            "cargo":             cargo,
                            "Fecha_Postulacion": datetime.datetime.utcnow().isoformat(),
                            "empresa":           "",
                            "link":              job["link"],
                            "portal":            PORTAL_ID,
                        }])
                        applied_ids.add(job_id)
                        count += 1
                        print(f"[cht] Guardado ({count})")
                    else:
                        try:
                            page.go_back()
                            page.wait_for_timeout(2000)
                        except Exception:
                            pass

    except Exception as e:
        import traceback
        print(f"[cht] Error general: {e}")
        traceback.print_exc()
    finally:
        close_chiletrabajos_pw_session(user_id)

    print(f"[cht] Finalizado {user_id} — {count} postulaciones")
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
