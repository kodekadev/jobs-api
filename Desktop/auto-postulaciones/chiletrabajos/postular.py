"""
ChileTrabajos — Postulaciones automáticas.

Flujo por usuario:
  1. Login con cookies (BigQuery) o formulario
  2. Por cada cargo + ubicación: busca empleos en /encuentra-un-empleo
  3. Por cada empleo: click Postular → responder preguntas → enviar
  4. Guarda cada empleo en BigQuery EMPLEOS

Uso:
    from chiletrabajos.postular import postular_empleos_cht
    count = postular_empleos_cht(user_id="jobs2", user=perfil_dict)
"""
import os
import sys
import re
import time
import json

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bq
from portal_accounts import _standard_answer, _llm_answer_questions, _extract_cv_text, _save_answers_to_cache
from chiletrabajos.login import make_driver, _do_login

BASE_URL  = "https://www.chiletrabajos.cl"
PORTAL_ID = "chiletrabajos"


def _norm(texto: str) -> str:
    import unicodedata
    t = (texto or "").upper().strip().replace("?","").replace("¿","")
    nfkd = unicodedata.normalize("NFKD", t)
    return unicodedata.normalize("NFKC", nfkd.translate({0x0301: None, 0x0308: None}))


def _get_label(driver: webdriver.Chrome, el) -> str:
    """Extrae el texto de la pregunta más cercana al elemento."""
    try:
        return driver.execute_script("""
            var el = arguments[0];
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
        """, el)
    except Exception:
        return ""


def _responder_preguntas_cht(driver: webdriver.Chrome, user: dict, job_title: str = ""):
    """Responde el formulario de preguntas del modal de postulación."""
    _EXCL = {"hidden", "radio", "checkbox", "submit", "button", "file", "image", "reset"}
    _NUMERIC_KWS = {"PRETENSION", "SUELDO", "RENTA", "SALARIO", "ANOS DE EXP", "AÑOS DE EXP",
                    "EXPERIENCIA", "CUANTOS ANOS"}

    def _is_numeric(item: dict) -> bool:
        if item.get("type") == "number":
            return True
        if item.get("inputmode") in ("numeric", "decimal"):
            return True
        lbl = _norm((item.get("label") or "") + " " + (item.get("placeholder") or ""))
        return any(k in lbl for k in _NUMERIC_KWS)

    # --- Radios: siempre seleccionar "Sí" ---
    radios = [r for r in driver.find_elements(By.CSS_SELECTOR, "input[type='radio']")
              if r.is_displayed()]
    grupos: dict = {}
    for r in radios:
        name = r.get_attribute("name") or r.get_attribute("id") or str(id(r))
        grupos.setdefault(name, []).append(r)

    for name, grupo in grupos.items():
        try:
            elegido = grupo[0]
            for r in grupo:
                lbl = _norm(_get_label(driver, r) or r.get_attribute("value") or "")
                if any(k in lbl for k in ("SI", "YES", "SÍ", "TRUE")):
                    elegido = r
                    break
            driver.execute_script(
                "arguments[0].checked=true;"
                "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
                elegido
            )
        except Exception:
            pass

    # --- Inputs y textareas ---
    pending = []
    for inp in driver.find_elements(By.TAG_NAME, "input"):
        try:
            inp_type = (inp.get_attribute("type") or "text").lower()
            if (inp.is_displayed() and not inp.get_attribute("disabled")
                    and inp_type not in _EXCL
                    and not (inp.get_attribute("value") or "").strip()):
                pending.append({
                    "el": inp, "kind": "input",
                    "label": _get_label(driver, inp),
                    "type": inp_type,
                    "inputmode": (inp.get_attribute("inputmode") or "").lower(),
                    "placeholder": inp.get_attribute("placeholder") or "",
                })
        except Exception:
            pass

    for ta in driver.find_elements(By.TAG_NAME, "textarea"):
        try:
            if ta.is_displayed() and not (ta.get_attribute("value") or "").strip():
                pending.append({
                    "el": ta, "kind": "textarea",
                    "label": _get_label(driver, ta),
                    "type": "textarea", "inputmode": "",
                    "placeholder": ta.get_attribute("placeholder") or "",
                })
        except Exception:
            pass

    # Paso 1: respuesta desde perfil
    answers: dict = {}
    for idx, item in enumerate(pending):
        resp = _standard_answer(item, user)
        if resp is not None:
            answers[idx] = (resp, "perfil")

    # Paso 2: Claude para las sin respuesta
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

    # Paso 3: escribir respuestas
    def _fallback(it: dict) -> str:
        if _is_numeric(it):
            return re.sub(r"[^\d]", "", str(user.get("pretension_general") or "")) or str(user.get("experiencia") or "5")
        if it.get("type") == "textarea":
            rv = str(user.get("resumen") or "")
            return rv[:400] if rv else f"Profesional con {user.get('experiencia','5')} años de experiencia."
        return ""

    for idx, item in enumerate(pending):
        try:
            el   = item["el"]
            kind = item["kind"]
            resp, source = answers.get(idx, (_fallback(item), "fallback"))
            if _is_numeric(item):
                resp = re.sub(r"[^\d]", "", resp) or resp

            print(f"    [preg/{source}] [{idx}] '{(item['label'] or item['type'])[:40]}' -> '{resp[:40]}'")

            proto = "window.HTMLTextAreaElement.prototype" if kind == "textarea" else "window.HTMLInputElement.prototype"
            driver.execute_script(f"""
                var el=arguments[0], v=arguments[1];
                var s=Object.getOwnPropertyDescriptor({proto},'value');
                if(s) s.set.call(el,v);
                el.dispatchEvent(new Event('input',{{bubbles:true}}));
                el.dispatchEvent(new Event('change',{{bubbles:true}}));
                el.dispatchEvent(new FocusEvent('blur',{{bubbles:true}}));
            """, el, resp)
        except Exception:
            pass

    print(f"    [cht] Preguntas respondidas: {len(grupos)} radios, {len(pending)} inputs")


# ── postular a un empleo ──────────────────────────────────────────────────────

def _click_fresh(driver: webdriver.Chrome, xpaths: list, label: str = "") -> str:
    """Re-busca y clickea el primer elemento visible. Evita StaleElementReferenceException."""
    for xp in xpaths:
        try:
            els = driver.find_elements(By.XPATH, xp)
            for el in els:
                try:
                    if el.is_displayed():
                        txt = ""
                        try:
                            txt = el.text.strip()[:40] or el.get_attribute("value") or ""
                        except Exception:
                            pass
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        driver.execute_script("arguments[0].click();", el)
                        print(f"    [cht] {label}: '{txt}'")
                        return txt
                except Exception:
                    continue
        except Exception:
            continue
    return ""


def _postular_empleo(driver: webdriver.Chrome, job_url: str, user: dict, titulo: str) -> bool:
    """Navega al empleo y postula. Retorna True si fue exitoso."""
    wait = WebDriverWait(driver, 10)
    try:
        driver.get(job_url)
        time.sleep(4)

        # Verificar si ya postulé o redirigió a login
        cur = driver.current_url
        if "chtlogin" in cur or "/login" in cur:
            print(f"    [cht] Redirigido a login — re-logueando")
            return False

        content = driver.page_source.lower()
        if any(s in content for s in ["ya postulaste", "ya te postulaste", "postulado anteriormente"]):
            print(f"    [cht] Ya postulado anteriormente")
            return False

        # Click en "Postular" — re-busca fresh para evitar stale
        txt = _click_fresh(driver, [
            "//a[contains(translate(.,'POSTULAR','postular'),'postular') and not(contains(.,'de nuevo'))]",
            "//*[@id='detalle-oferta']//a[contains(@href,'postular')]",
            "//*[@id='detalle-oferta']//a[2]",
            "//a[contains(@href,'postular')]",
        ], label="Click postular")

        if not txt:
            print(f"    [cht] Sin botón postular en {job_url[:60]}")
            return False

        time.sleep(3)
        if "chtlogin" in driver.current_url:
            print(f"    [cht] Redirigido a login tras click — sesión expirada")
            return False

        # Formulario de preguntas (re-buscar fresh)
        forms = driver.find_elements(By.XPATH,
            "//form[contains(@id,'postular')] | //form[.//input[@class[contains(.,'enviar-postulacion')]]]"
        )
        if any(f.is_displayed() for f in forms):
            _responder_preguntas_cht(driver, user, job_title=titulo)
            time.sleep(1)

        # Submit — re-busca fresh
        enviado = _click_fresh(driver, [
            "//input[contains(@class,'enviar-postulacion')]",
            "//input[@value='Enviar postulación']",
            "//input[@value='Enviar postulacion']",
            "//button[contains(translate(.,'ENVIAR','enviar'),'enviar') and not(@disabled)]",
            "//input[@type='submit' and not(@disabled)]",
            "//button[@type='submit' and not(@disabled)]",
        ], label="Submit")

        if not enviado:
            print(f"    [cht] Sin botón submit")
            return False

        time.sleep(4)

        content = driver.page_source.lower()
        confirmed = any(s in content for s in [
            "postulación enviada", "postulacion enviada", "gracias por postular",
            "aplicación enviada", "te has postulado", "exitoso", "hemos recibido",
        ])
        # Error explícito del sitio
        error = any(s in content for s in [
            "ha ocurrido un error", "error al postular", "no pudimos", "inténtalo de nuevo",
        ])
        ok = confirmed or (not error)  # si no hay error visible, asumir éxito
        print(f"    [cht] {'OK Postulado' if confirmed else ('Error en envio' if error else 'Enviado sin confirmar')}")
        return ok

    except Exception as e:
        print(f"    [cht] Error postular: {e}")
        return False


# ── función principal ─────────────────────────────────────────────────────────

def postular_empleos_cht(user_id: str, user: dict) -> int:
    """
    Busca y postula empleos en ChileTrabajos para el usuario.

    Args:
        user_id : ID del usuario en BigQuery
        user    : dict con datos de PostulaFacil (cargos, ubicaciones, pretension, cv_url, etc.)

    Returns:
        Número de postulaciones guardadas.
    """
    cuenta = bq.get_portal_account(user_id, PORTAL_ID)
    if not cuenta:
        print(f"[cht] Sin cuenta para {user_id} — ejecuta crear_cuenta primero")
        return 0

    email    = cuenta["email"]
    password = cuenta["password"]

    # Parsear cargos y ubicaciones (BigQuery devuelve en mayúsculas)
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

    driver = None
    count  = 0

    try:
        driver = make_driver()

        # Login con email/password (las cookies solo dan acceso de lectura,
        # no son suficientes para postular)
        logged = _do_login(driver, email, password)
        if not logged:
            print(f"[cht] Login fallido para {user_id}")
            driver.quit()
            return 0

        bq.save_portal_cookies(user_id, PORTAL_ID, driver.get_cookies())
        print(f"[cht] Iniciando postulaciones para {user_id}")

        applied_ids = bq.get_applied_job_ids(user_id)

        for cargo in cargos:
            for ubicacion in ubicaciones:
                print(f"[cht] Buscando: '{cargo}' en '{ubicacion}'")

                # Buscar empleos
                driver.get(f"{BASE_URL}/encuentra-un-empleo")
                time.sleep(3)
                try:
                    inp_trabajo = WebDriverWait(driver, 8).until(
                        EC.presence_of_element_located((By.ID, "trabajo"))
                    )
                    inp_trabajo.clear()
                    inp_trabajo.send_keys(cargo)
                except Exception:
                    print(f"[cht] ! Sin campo de búsqueda")
                    continue

                # Seleccionar región
                try:
                    sel = driver.find_element(
                        By.XPATH,
                        "//select[.//option[contains(translate(.,'SANTIAGO','santiago'),'santiago')]]"
                    )
                    from selenium.webdriver.support.ui import Select as _Sel
                    opts = _Sel(sel).options
                    for opt in opts:
                        if ubicacion.lower() in opt.text.lower():
                            opt.click()
                            break
                except Exception:
                    pass

                # Submit búsqueda
                try:
                    driver.find_element(By.XPATH, "//button[@type='submit'] | //input[@type='submit']").click()
                    time.sleep(4)
                except Exception:
                    pass

                # Recopilar links — solo URLs de empleo reales (con /trabajo/ o id numérico)
                jobs = []
                seen = set()
                for a in driver.find_elements(By.CSS_SELECTOR,
                    "h2 a, h3 a, .job-item h2 a, .job-item h3 a, "
                    "a.font-weight-bold, a[href*='/trabajo/'], a[href*='/empleo/']"
                ):
                    try:
                        href  = a.get_attribute("href") or ""
                        title = a.text.strip()
                        if (href and title and href not in seen
                                and any(k in href for k in ["/trabajo/", "/empleo/"])
                                and not any(k in href for k in ["/ciudad/", "/empresa/", "/encuentra-", "/categoria/"])
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
                    ok = _postular_empleo(driver, job["link"], user, job["titulo"])

                    if ok:
                        bq.save_jobs([{
                            "id_empleo":         job_id,
                            "id_usuario":        user_id,
                            "titulo_empleo":     job["titulo"],
                            "cargo":             cargo,
                            "Fecha_Postulacion": __import__("datetime").datetime.utcnow().isoformat(),
                            "empresa":           "",
                            "link":              job["link"],
                            "portal":            PORTAL_ID,
                        }])
                        applied_ids.add(job_id)
                        count += 1
                        print(f"[cht] Guardado ({count})")
                    else:
                        # Volver a búsqueda si el driver quedó en otro lugar
                        try:
                            driver.back()
                            time.sleep(2)
                            driver.back()
                            time.sleep(1)
                        except Exception:
                            pass

    except Exception as e:
        import traceback
        print(f"[cht] Error general: {e}")
        traceback.print_exc()
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    print(f"[cht] Finalizado {user_id} — {count} postulaciones")
    return count


if __name__ == "__main__":
    import os, sys
    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, _ROOT)
    os.environ.setdefault(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\Users\bastian\Desktop\Script_Python\jobs-425301-ba25295bbbd0.json",
    )
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
    import bq
    users = bq.get_active_users()
    for u in users:
        postular_empleos_cht(u["ID_USUARIO"], u)
